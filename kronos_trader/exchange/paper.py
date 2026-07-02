"""Paper trading simulator.

Market data comes from a real exchange (public endpoints, no keys needed) or
from an injected data feed (backtest / tests). Orders are simulated locally
with taker fee and slippage.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import pandas as pd

from ..config import ExchangeConfig
from .base import ExchangeError, ExchangeInterface, Fill

logger = logging.getLogger("kronos.paper")


class PaperExchange(ExchangeInterface):
    def __init__(self, cfg: ExchangeConfig, start_balance: float,
                 data_source: Optional[ExchangeInterface] = None,
                 price_fn: Optional[Callable[[str], float]] = None):
        """`data_source` supplies market data (a LiveExchange without keys);
        `price_fn` overrides price lookup for backtests/tests."""
        self.cfg = cfg
        self._lock = threading.RLock()
        self.balance = float(start_balance)
        self.holdings: dict[str, float] = {}   # base asset -> amount (can be negative)
        self._data = data_source
        self._price_fn = price_fn

    # ---- market data -----------------------------------------------------

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        if self._data is None:
            raise ExchangeError("paper exchange has no market data source")
        return self._data.fetch_ohlcv(symbol, timeframe, limit)

    def fetch_price(self, symbol: str) -> float:
        if self._price_fn is not None:
            return self._price_fn(symbol)
        if self._data is None:
            raise ExchangeError("paper exchange has no market data source")
        return self._data.fetch_price(symbol)

    # ---- account -----------------------------------------------------------

    def fetch_balance_usd(self) -> float:
        with self._lock:
            return self.balance

    def equity(self, symbol: str) -> float:
        """Balance plus mark-to-market value of holdings."""
        with self._lock:
            value = self.balance
            for base, amount in self.holdings.items():
                if amount:
                    value += amount * self.fetch_price(f"{base}/USDT"
                                                       if "/" not in symbol else symbol)
            return value

    def market_order(self, symbol: str, side: str, size: float) -> Fill:
        if size <= 0:
            raise ExchangeError("order size must be positive")
        price = self.fetch_price(symbol)
        # slippage always works against the order
        price *= (1 + self.cfg.slippage) if side == "buy" else (1 - self.cfg.slippage)
        notional = size * price
        fee = notional * self.cfg.taker_fee
        base = symbol.split("/")[0]
        with self._lock:
            if side == "buy":
                if notional + fee > self.balance + 1e-9:
                    raise ExchangeError(
                        f"insufficient paper balance: need {notional + fee:.2f}, "
                        f"have {self.balance:.2f}")
                self.balance -= notional + fee
                self.holdings[base] = self.holdings.get(base, 0.0) + size
            else:
                self.balance += notional - fee
                self.holdings[base] = self.holdings.get(base, 0.0) - size
        logger.info("PAPER fill: %s %s %.8f @ %.6f (fee %.6f)",
                    side, symbol, size, price, fee)
        return Fill(symbol=symbol, side=side, size=size, price=price, fee=fee)

    def ping(self) -> bool:
        if self._data is not None:
            return self._data.ping()
        return True
