"""Thread-safe runtime state shared between the engine, monitor and Telegram bot."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class TradeRecord:
    timestamp: datetime
    symbol: str
    side: str            # LONG / SHORT
    action: str          # OPEN / CLOSE
    price: float
    size: float
    forecast: float
    confidence: float
    pnl: Optional[float] = None
    reason: str = ""     # signal / stop_loss / take_profit / manual


class RuntimeState:
    """Aggregated live state: mode, trades, stats, cycle timings."""

    def __init__(self, mode: str = "paper"):
        self._lock = threading.RLock()
        self.mode = mode
        self.trading_enabled = False
        self.started_at = time.time()
        self.trades: list[TradeRecord] = []
        self.last_cycle_time: float = 0.0
        self.max_cycle_time: float = 0.0
        self.cycles_completed: int = 0
        self.last_error: str = ""
        self.connection_ok: bool = True

    def record_trade(self, trade: TradeRecord) -> None:
        with self._lock:
            self.trades.append(trade)
            # keep memory bounded for 24/7 operation
            if len(self.trades) > 10_000:
                del self.trades[:5_000]

    def record_cycle(self, seconds: float) -> None:
        with self._lock:
            self.last_cycle_time = seconds
            self.max_cycle_time = max(self.max_cycle_time, seconds)
            self.cycles_completed += 1

    def last_trade(self) -> Optional[TradeRecord]:
        with self._lock:
            return self.trades[-1] if self.trades else None

    def closed_trades(self, since: Optional[datetime] = None) -> list[TradeRecord]:
        with self._lock:
            out = [t for t in self.trades if t.action == "CLOSE" and t.pnl is not None]
        if since is not None:
            out = [t for t in out if t.timestamp >= since]
        return out

    def stats(self, since: Optional[datetime] = None) -> dict:
        closed = self.closed_trades(since)
        wins = [t for t in closed if (t.pnl or 0) > 0]
        losses = [t for t in closed if (t.pnl or 0) <= 0]
        total_pnl = sum(t.pnl or 0 for t in closed)
        return {
            "trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "winrate": len(wins) / len(closed) if closed else 0.0,
            "total_pnl": total_pnl,
            "avg_pnl": total_pnl / len(closed) if closed else 0.0,
            "best": max((t.pnl or 0 for t in closed), default=0.0),
            "worst": min((t.pnl or 0 for t in closed), default=0.0),
        }

    def today_stats(self) -> dict:
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return self.stats(since=midnight)

    def uptime_str(self) -> str:
        seconds = int(time.time() - self.started_at)
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        return f"{days}d {hours:02d}h {minutes:02d}m"
