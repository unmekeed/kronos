"""Main trading engine: fetch data → forecast → signal → risk → execute.

The full cycle is timed and must stay under trading.max_cycle_time_sec (15s).
The engine runs 24/7 in a background thread; API errors are retried at the
exchange layer and reported to Telegram here.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from ..config import Config
from ..exchange.base import ExchangeError, ExchangeInterface
from ..logger import log_error, log_trade
from ..risk.manager import RiskManager
from ..state import RuntimeState, TradeRecord
from ..strategy.signal import Action, Signal, make_signal
from .positions import Position, PositionBook

logger = logging.getLogger("kronos.engine")


class TradingEngine:
    def __init__(self, cfg: Config, exchange: ExchangeInterface,
                 forecaster, risk: RiskManager, state: RuntimeState, notifier):
        self.cfg = cfg
        self.exchange = exchange
        self.forecaster = forecaster
        self.risk = risk
        self.state = state
        self.notifier = notifier
        self.positions = PositionBook()
        self.start_balance = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_report_date = None

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> None:
        """Load the model, snapshot the starting balance, launch the loop."""
        self.forecaster.load()
        self.start_balance = self.exchange.fetch_balance_usd()
        self.risk.update_equity(self.start_balance)
        logger.info("Engine starting: mode=%s symbol=%s balance=%.2f USD",
                    self.cfg.mode, self.cfg.exchange.symbol, self.start_balance)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="engine", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)

    def start_trading(self) -> None:
        self.state.trading_enabled = True
        logger.info("Trading ENABLED")

    def stop_trading(self) -> None:
        self.state.trading_enabled = False
        logger.info("Trading DISABLED")

    def restart(self) -> None:
        """Soft restart: reset kill switch and re-enable trading; positions survive."""
        logger.info("Engine restart requested")
        self.risk.reset_kill_switch()
        self.risk.update_equity(self._equity_safe())
        self.state.trading_enabled = True

    # ---- main loop ---------------------------------------------------------

    def _run_loop(self) -> None:
        interval = self.cfg.trading.cycle_interval_sec
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._cycle()
            except ExchangeError as exc:
                self.state.last_error = str(exc)
                log_error("Exchange error in cycle", exc)
                self.notifier.api_error(str(exc))
            except Exception as exc:
                self.state.last_error = str(exc)
                log_error("Unexpected error in cycle", exc)
                self.notifier.api_error(f"Внутренняя ошибка: {exc}")
            elapsed = time.monotonic() - started
            self.state.record_cycle(elapsed)
            if elapsed > self.cfg.trading.max_cycle_time_sec:
                logger.warning("Cycle took %.2fs (limit %.0fs)",
                               elapsed, self.cfg.trading.max_cycle_time_sec)
            self._maybe_daily_report()
            self._stop.wait(max(1.0, interval - elapsed))

    def _cycle(self) -> None:
        symbol = self.cfg.exchange.symbol
        df = self.exchange.fetch_ohlcv(symbol, self.cfg.exchange.timeframe,
                                       limit=self.cfg.model.max_context + 2)
        last = df.iloc[-1]
        price = float(last["close"])

        # 1) manage the open position first (stops/takes fire even when paused)
        self._manage_position(symbol, price, float(last["high"]), float(last["low"]))

        # 2) equity & drawdown tracking
        equity = self._equity(price)
        self.risk.update_equity(equity)
        if self.risk.kill_switch and self.state.trading_enabled:
            self.state.trading_enabled = False
            self.notifier.risk_breach(f"Kill switch: {self.risk.kill_reason}. "
                                      f"Торговля остановлена.")
            return

        if not self.state.trading_enabled:
            return

        # 3) forecast + signal
        forecast = self.forecaster.predict(df)
        signal = make_signal(forecast, self.cfg.strategy)
        logger.info("Forecast %s: ret=%+.4f%% conf=%.2f -> %s",
                    symbol, forecast.expected_return * 100,
                    forecast.confidence, signal.action.value)

        # 4) close on opposite signal
        pos = self.positions.get(symbol)
        if (pos is not None and signal.actionable
                and self.cfg.strategy.close_on_opposite
                and pos.side != signal.action.value):
            self._close_position(pos, price, "signal", forecast.expected_return,
                                 forecast.confidence)
            pos = None

        # 5) open a new position if risk allows
        if pos is None and signal.actionable:
            balance = self.exchange.fetch_balance_usd()
            decision = self.risk.evaluate(signal, balance, self.positions.count())
            if decision.allowed:
                self._open_position(signal, decision)
            else:
                logger.info("Trade rejected by risk manager: %s", decision.reason)
                if "daily loss" in decision.reason or "kill switch" in decision.reason:
                    self.notifier.risk_breach(decision.reason)
                log_trade(symbol, price, forecast.expected_return,
                          forecast.confidence, signal.action.value, 0.0, None,
                          f"REJECTED: {decision.reason}")

    # ---- position handling ---------------------------------------------------

    def _manage_position(self, symbol: str, price: float,
                         high: float, low: float) -> None:
        pos = self.positions.get(symbol)
        if pos is None:
            return
        exit_reason = pos.exit_hit(high, low)
        if exit_reason is not None:
            exit_price = pos.stop_price if exit_reason == "stop_loss" else pos.take_price
            self._close_position(pos, exit_price, exit_reason, 0.0, 0.0)

    def _open_position(self, signal: Signal, decision) -> None:
        symbol = self.cfg.exchange.symbol
        side = "buy" if signal.action is Action.LONG else "sell"
        fill = self.exchange.market_order(symbol, side, decision.size)
        position = Position(
            symbol=symbol, side=signal.action.value, size=fill.size,
            entry_price=fill.price, stop_price=decision.stop_price,
            take_price=decision.take_price, entry_fee=fill.fee,
        )
        self.positions.open(position)
        self.state.record_trade(TradeRecord(
            timestamp=datetime.now(timezone.utc), symbol=symbol,
            side=position.side, action="OPEN", price=fill.price, size=fill.size,
            forecast=signal.expected_return, confidence=signal.confidence,
            reason="signal"))
        log_trade(symbol, fill.price, signal.expected_return, signal.confidence,
                  f"OPEN {position.side}", fill.size, None, "FILLED")
        self.notifier.trade_opened(symbol, position.side, fill.size, fill.price,
                                   decision.stop_price, decision.take_price,
                                   signal.confidence)

    def _close_position(self, pos: Position, ref_price: float, reason: str,
                        forecast: float, confidence: float) -> None:
        side = "sell" if pos.side == "LONG" else "buy"
        fill = self.exchange.market_order(pos.symbol, side, pos.size)
        direction = 1.0 if pos.side == "LONG" else -1.0
        pnl = ((fill.price - pos.entry_price) * pos.size * direction
               - pos.entry_fee - fill.fee)
        self.positions.close(pos.symbol)
        self.risk.register_trade_result(pnl)
        self.state.record_trade(TradeRecord(
            timestamp=datetime.now(timezone.utc), symbol=pos.symbol,
            side=pos.side, action="CLOSE", price=fill.price, size=pos.size,
            forecast=forecast, confidence=confidence, pnl=pnl, reason=reason))
        log_trade(pos.symbol, fill.price, forecast, confidence,
                  f"CLOSE {pos.side}", pos.size, pnl, f"FILLED ({reason})")
        self.notifier.trade_closed(pos.symbol, pos.side, fill.price, pnl, reason)
        balance = self.exchange.fetch_balance_usd()
        if self.risk.daily_loss_exceeded(balance):
            self.notifier.risk_breach(
                f"Достигнут дневной лимит убытка "
                f"({self.risk.daily_pnl():+.2f} USD). Новые сделки заблокированы до конца дня.")

    # ---- status / reporting -----------------------------------------------

    def _equity(self, price: float) -> float:
        """USD balance plus mark-to-market value of open positions.

        A LONG holds base asset paid out of the balance, so it is valued at
        full notional; a SHORT keeps the quote proceeds in the balance, so
        only its unrealized PnL is added.
        """
        balance = self.exchange.fetch_balance_usd()
        for p in self.positions.all():
            if p.side == "LONG":
                balance += p.notional(price)
            else:
                balance += p.unrealized_pnl(price)
        return balance

    def _equity_safe(self) -> float:
        try:
            return self._equity(self.exchange.fetch_price(self.cfg.exchange.symbol))
        except Exception:
            return self.exchange.fetch_balance_usd()

    def status(self) -> dict:
        symbol = self.cfg.exchange.symbol
        try:
            price = self.exchange.fetch_price(symbol)
        except Exception:
            price = 0.0
        positions = [{
            "symbol": p.symbol, "side": p.side, "size": p.size,
            "entry": p.entry_price, "stop": p.stop_price, "take": p.take_price,
            "unrealized_pnl": p.unrealized_pnl(price) if price else 0.0,
        } for p in self.positions.all()]
        stats = self.state.stats()
        return {
            "mode": self.cfg.mode,
            "trading_enabled": self.state.trading_enabled,
            "balance": self.exchange.fetch_balance_usd(),
            "equity": self._equity(price) if price else self.exchange.fetch_balance_usd(),
            "total_pnl": stats["total_pnl"],
            "daily_pnl": self.risk.daily_pnl(),
            "positions": positions,
            "uptime": self.state.uptime_str(),
            "last_cycle_time": self.state.last_cycle_time,
        }

    def _maybe_daily_report(self) -> None:
        now = datetime.now(timezone.utc)
        if now.hour != self.cfg.telegram.daily_report_hour_utc:
            return
        if self._last_report_date == now.date():
            return
        self._last_report_date = now.date()
        st = self.state.today_stats()
        s = self.status()
        self.notifier.daily_report(
            f"Дата: {now:%Y-%m-%d}\n"
            f"Режим: {s['mode']}\n"
            f"Баланс: {s['balance']:.2f} USD | Эквити: {s['equity']:.2f} USD\n"
            f"Сделок за день: {st['trades']} (W {st['wins']} / L {st['losses']}, "
            f"winrate {st['winrate']:.0%})\n"
            f"PnL за день: {st['total_pnl']:+.4f} USD\n"
            f"Циклов: {self.state.cycles_completed}, "
            f"макс. цикл: {self.state.max_cycle_time:.2f}s\n"
            f"Аптайм: {self.state.uptime_str()}")
