"""Common exchange interface shared by the live (ccxt) client and the paper simulator."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


class ExchangeError(Exception):
    """Raised when the exchange is unreachable or rejects a request."""


@dataclass
class Fill:
    symbol: str
    side: str        # buy / sell
    size: float      # base amount actually filled
    price: float     # average fill price
    fee: float       # quote currency


class ExchangeInterface(ABC):
    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """Return DataFrame: timestamps(datetime64), open, high, low, close, volume."""

    @abstractmethod
    def fetch_price(self, symbol: str) -> float:
        """Last traded price."""

    @abstractmethod
    def fetch_balance_usd(self) -> float:
        """Free quote-currency balance (USDT treated as USD)."""

    @abstractmethod
    def market_order(self, symbol: str, side: str, size: float) -> Fill:
        """Execute a market order; side is 'buy' or 'sell'."""

    @abstractmethod
    def ping(self) -> bool:
        """Cheap connectivity check."""


def ohlcv_to_df(raw: list[list[float]]) -> pd.DataFrame:
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["timestamps"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df[["timestamps", "open", "high", "low", "close", "volume"]]
