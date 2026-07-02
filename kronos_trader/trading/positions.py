"""Open position tracking and PnL calculation."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Position:
    symbol: str
    side: str                 # LONG / SHORT
    size: float               # base asset amount
    entry_price: float
    stop_price: float
    take_price: float
    opened_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))
    entry_fee: float = 0.0    # quote currency

    def unrealized_pnl(self, price: float) -> float:
        direction = 1.0 if self.side == "LONG" else -1.0
        return (price - self.entry_price) * self.size * direction - self.entry_fee

    def notional(self, price: float) -> float:
        return self.size * price

    def exit_hit(self, high: float, low: float) -> Optional[str]:
        """Check whether the candle range touched the stop or take level.

        Returns 'stop_loss' / 'take_profit' / None. Stop wins ties (conservative).
        """
        if self.side == "LONG":
            if low <= self.stop_price:
                return "stop_loss"
            if high >= self.take_price:
                return "take_profit"
        else:
            if high >= self.stop_price:
                return "stop_loss"
            if low <= self.take_price:
                return "take_profit"
        return None


class PositionBook:
    """Thread-safe container for open positions (one per symbol)."""

    def __init__(self):
        self._lock = threading.RLock()
        self._positions: dict[str, Position] = {}

    def open(self, position: Position) -> None:
        with self._lock:
            if position.symbol in self._positions:
                raise ValueError(f"position already open for {position.symbol}")
            self._positions[position.symbol] = position

    def close(self, symbol: str) -> Optional[Position]:
        with self._lock:
            return self._positions.pop(symbol, None)

    def get(self, symbol: str) -> Optional[Position]:
        with self._lock:
            return self._positions.get(symbol)

    def all(self) -> list[Position]:
        with self._lock:
            return list(self._positions.values())

    def count(self) -> int:
        with self._lock:
            return len(self._positions)
