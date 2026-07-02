"""Risk management: position sizing, stop/take levels, daily loss and drawdown limits."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from ..config import RiskConfig
from ..strategy.signal import Action, Signal

logger = logging.getLogger("kronos.risk")


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = ""
    size: float = 0.0            # base asset amount
    notional: float = 0.0        # quote (USD) amount
    stop_price: float = 0.0
    take_price: float = 0.0


class RiskManager:
    """Stateful gatekeeper for every order the engine wants to place."""

    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self._lock = threading.RLock()
        self._daily_pnl = 0.0
        self._daily_date: date = datetime.now(timezone.utc).date()
        self._peak_equity: Optional[float] = None
        self._last_loss_ts: float = 0.0
        self.kill_switch = False
        self.kill_reason = ""

    # ---- state updates -------------------------------------------------

    def _roll_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._daily_date:
            self._daily_date = today
            self._daily_pnl = 0.0

    def register_trade_result(self, pnl: float) -> None:
        with self._lock:
            self._roll_day()
            self._daily_pnl += pnl
            if pnl < 0:
                self._last_loss_ts = time.time()

    def update_equity(self, equity: float) -> None:
        """Track peak equity; trip the kill switch on max drawdown."""
        with self._lock:
            if self._peak_equity is None or equity > self._peak_equity:
                self._peak_equity = equity
            drawdown = 1 - equity / self._peak_equity if self._peak_equity else 0.0
            if drawdown >= self.cfg.max_drawdown_pct and not self.kill_switch:
                self.kill_switch = True
                self.kill_reason = f"max drawdown {drawdown:.1%} >= {self.cfg.max_drawdown_pct:.1%}"
                logger.error("KILL SWITCH: %s", self.kill_reason)

    def reset_kill_switch(self) -> None:
        with self._lock:
            self.kill_switch = False
            self.kill_reason = ""
            self._peak_equity = None

    # ---- checks ---------------------------------------------------------

    def daily_pnl(self) -> float:
        with self._lock:
            self._roll_day()
            return self._daily_pnl

    def daily_loss_exceeded(self, balance: float) -> bool:
        # limit is relative to current balance; conservative enough for small accounts
        return self.daily_pnl() <= -abs(balance * self.cfg.max_daily_loss_pct)

    def evaluate(self, signal: Signal, balance: float, open_positions: int) -> RiskDecision:
        """Decide whether the signal may become an order, and size it."""
        with self._lock:
            self._roll_day()
            if self.kill_switch:
                return RiskDecision(False, f"kill switch: {self.kill_reason}")
            if not signal.actionable:
                return RiskDecision(False, "signal is HOLD")
            if signal.action is Action.SHORT and not self.cfg.allow_short:
                return RiskDecision(False, "shorts disabled (spot mode)")
            if open_positions >= self.cfg.max_open_positions:
                return RiskDecision(False, "max open positions reached")
            if self.daily_loss_exceeded(balance):
                return RiskDecision(False,
                                    f"daily loss limit reached ({self._daily_pnl:+.2f} USD)")
            cooldown_left = self.cfg.cooldown_after_loss_sec - (time.time() - self._last_loss_ts)
            if self._last_loss_ts and cooldown_left > 0:
                return RiskDecision(False, f"cooldown after loss: {cooldown_left:.0f}s left")

            price = signal.price
            # risk_per_trade of balance is lost if the stop is hit
            notional = balance * self.cfg.risk_per_trade / self.cfg.stop_loss_pct
            notional = min(notional, balance * self.cfg.max_position_pct)
            if notional < self.cfg.min_order_usd:
                return RiskDecision(
                    False, f"order {notional:.2f} USD below exchange minimum "
                           f"{self.cfg.min_order_usd:.2f} USD")

            if signal.action is Action.LONG:
                stop = price * (1 - self.cfg.stop_loss_pct)
                take = price * (1 + self.cfg.take_profit_pct)
            else:
                stop = price * (1 + self.cfg.stop_loss_pct)
                take = price * (1 - self.cfg.take_profit_pct)

            return RiskDecision(
                allowed=True, reason="ok",
                size=notional / price, notional=notional,
                stop_price=stop, take_price=take,
            )

    def summary(self) -> dict:
        with self._lock:
            self._roll_day()
            return {
                "risk_per_trade": self.cfg.risk_per_trade,
                "max_position_pct": self.cfg.max_position_pct,
                "max_open_positions": self.cfg.max_open_positions,
                "stop_loss_pct": self.cfg.stop_loss_pct,
                "take_profit_pct": self.cfg.take_profit_pct,
                "max_daily_loss_pct": self.cfg.max_daily_loss_pct,
                "max_drawdown_pct": self.cfg.max_drawdown_pct,
                "daily_pnl": self._daily_pnl,
                "kill_switch": self.kill_switch,
                "kill_reason": self.kill_reason,
            }
