"""Event-driven backtest over historical OHLCV using the same signal/risk logic
as live trading. Fees, slippage and intrabar stop/take execution are modelled.
"""

from __future__ import annotations

import dataclasses
import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import Config
from ..risk.manager import RiskManager
from ..strategy.signal import Action, make_signal
from ..trading.positions import Position

logger = logging.getLogger("kronos.backtest")


@dataclass
class BacktestTrade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: str
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    reason: str


@dataclass
class BacktestResult:
    start_balance: float
    end_balance: float
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

    @property
    def total_return(self) -> float:
        return (self.end_balance - self.start_balance) / self.start_balance

    @property
    def winrate(self) -> float:
        if not self.trades:
            return 0.0
        return sum(1 for t in self.trades if t.pnl > 0) / len(self.trades)

    @property
    def max_drawdown(self) -> float:
        peak, mdd = -math.inf, 0.0
        for eq in self.equity_curve:
            peak = max(peak, eq)
            mdd = max(mdd, 1 - eq / peak)
        return mdd

    @property
    def sharpe(self) -> float:
        if len(self.equity_curve) < 3:
            return 0.0
        rets = np.diff(self.equity_curve) / np.array(self.equity_curve[:-1])
        std = rets.std()
        if std == 0:
            return 0.0
        # annualised assuming one equity point per candle, 5m candles by default
        return float(rets.mean() / std * math.sqrt(365 * 24 * 12))

    def summary(self) -> str:
        return (
            f"Backtest: {len(self.trades)} trades | "
            f"return {self.total_return:+.2%} | winrate {self.winrate:.1%} | "
            f"max DD {self.max_drawdown:.2%} | sharpe {self.sharpe:.2f} | "
            f"balance {self.start_balance:.2f} -> {self.end_balance:.2f} USD"
        )


class BacktestEngine:
    """Replays history candle by candle with a sliding context window."""

    def __init__(self, cfg: Config, forecaster, start_balance: float | None = None):
        self.cfg = cfg
        self.forecaster = forecaster
        self.start_balance = start_balance or cfg.trading.paper_start_balance

    def run(self, df: pd.DataFrame, step: int = 1) -> BacktestResult:
        """df: timestamps, open, high, low, close, volume. `step` — candles
        between decisions (1 = decide on every candle)."""
        cfg = self.cfg
        window = cfg.model.max_context
        if len(df) <= window + 1:
            raise ValueError(f"need more than {window + 1} candles, got {len(df)}")

        # the loss cooldown is wall-clock based and meaningless when replaying
        # history at full speed — disable it for the backtest run
        risk = RiskManager(dataclasses.replace(cfg.risk, cooldown_after_loss_sec=0))
        balance = self.start_balance
        position: Position | None = None
        result = BacktestResult(start_balance=balance, end_balance=balance)
        fee = cfg.exchange.taker_fee
        slip = cfg.exchange.slippage

        def fill_price(price: float, side: str) -> float:
            return price * (1 + slip) if side == "buy" else price * (1 - slip)

        def close(pos: Position, raw_price: float, ts, reason: str) -> float:
            nonlocal balance
            side = "sell" if pos.side == "LONG" else "buy"
            price = fill_price(raw_price, side)
            direction = 1.0 if pos.side == "LONG" else -1.0
            exit_fee = pos.size * price * fee
            pnl = (price - pos.entry_price) * pos.size * direction - pos.entry_fee - exit_fee
            balance += pnl
            risk.register_trade_result(pnl)
            result.trades.append(BacktestTrade(
                entry_time=pos.opened_at, exit_time=ts, side=pos.side,
                entry_price=pos.entry_price, exit_price=price,
                size=pos.size, pnl=pnl, reason=reason))
            return pnl

        for i in range(window, len(df), step):
            candle = df.iloc[i]
            ts = candle["timestamps"]
            price = float(candle["close"])

            # intrabar stop/take on the current candle
            if position is not None:
                hit = position.exit_hit(float(candle["high"]), float(candle["low"]))
                if hit is not None:
                    raw = (position.stop_price if hit == "stop_loss"
                           else position.take_price)
                    close(position, raw, ts, hit)
                    position = None

            equity = balance
            if position is not None:
                equity += position.unrealized_pnl(price)
            result.equity_curve.append(equity)
            risk.update_equity(equity)

            context = df.iloc[i - window:i + 1]
            forecast = self.forecaster.predict(context)
            signal = make_signal(forecast, cfg.strategy)

            if (position is not None and signal.actionable
                    and cfg.strategy.close_on_opposite
                    and position.side != signal.action.value):
                close(position, price, ts, "signal")
                position = None

            if position is None and signal.actionable:
                decision = risk.evaluate(signal, balance, 0)
                if decision.allowed:
                    side = "buy" if signal.action is Action.LONG else "sell"
                    entry = fill_price(price, side)
                    entry_fee = decision.size * entry * fee
                    position = Position(
                        symbol=cfg.exchange.symbol, side=signal.action.value,
                        size=decision.size, entry_price=entry,
                        stop_price=decision.stop_price,
                        take_price=decision.take_price,
                        opened_at=ts, entry_fee=entry_fee)

        # liquidate at the end
        if position is not None:
            last = df.iloc[-1]
            close(position, float(last["close"]), last["timestamps"], "end_of_data")
        result.end_balance = balance
        logger.info(result.summary())
        return result
