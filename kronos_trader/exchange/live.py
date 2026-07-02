"""Live exchange client on top of ccxt with retry and automatic reconnection."""

from __future__ import annotations

import logging

import pandas as pd

from ..config import ExchangeConfig
from ..utils import retry
from .base import ExchangeError, ExchangeInterface, Fill, ohlcv_to_df

logger = logging.getLogger("kronos.exchange")


class LiveExchange(ExchangeInterface):
    def __init__(self, cfg: ExchangeConfig):
        self.cfg = cfg
        self._client = None

    # ---- connection ------------------------------------------------------

    def _connect(self):
        import ccxt
        cls = getattr(ccxt, self.cfg.id, None)
        if cls is None:
            raise ExchangeError(f"unknown ccxt exchange id: {self.cfg.id}")
        client = cls({
            "apiKey": self.cfg.api_key,
            "secret": self.cfg.api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": self.cfg.market_type},
        })
        client.load_markets()
        logger.info("Connected to %s (%s)", self.cfg.id, self.cfg.market_type)
        return client

    @property
    def client(self):
        if self._client is None:
            self._client = self._connect()
        return self._client

    def reconnect(self) -> None:
        logger.warning("Reconnecting to %s", self.cfg.id)
        self._client = None
        _ = self.client

    def _call(self, fn_name: str, *args, **kwargs):
        """Run a ccxt call; on network failure reconnect once and re-raise."""
        import ccxt
        try:
            return getattr(self.client, fn_name)(*args, **kwargs)
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as exc:
            self._client = None  # force reconnect on next use
            raise ExchangeError(f"{fn_name}: {exc}") from exc
        except ccxt.BaseError as exc:
            raise ExchangeError(f"{fn_name}: {exc}") from exc

    # ---- interface ---------------------------------------------------------

    @retry(attempts=4, base_delay=2.0, exceptions=(ExchangeError,))
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        raw = self._call("fetch_ohlcv", symbol, timeframe=timeframe, limit=limit)
        if not raw:
            raise ExchangeError(f"empty OHLCV for {symbol}")
        return ohlcv_to_df(raw)

    @retry(attempts=4, base_delay=2.0, exceptions=(ExchangeError,))
    def fetch_price(self, symbol: str) -> float:
        ticker = self._call("fetch_ticker", symbol)
        return float(ticker["last"])

    @retry(attempts=4, base_delay=2.0, exceptions=(ExchangeError,))
    def fetch_balance_usd(self) -> float:
        balance = self._call("fetch_balance")
        free = balance.get("free", {}) or {}
        return float(free.get("USDT", 0.0) or 0.0)

    @retry(attempts=2, base_delay=2.0, exceptions=(ExchangeError,))
    def market_order(self, symbol: str, side: str, size: float) -> Fill:
        size = float(self.client.amount_to_precision(symbol, size))
        order = self._call("create_order", symbol, "market", side, size)
        # refresh to get fill details
        fetched = self._call("fetch_order", order["id"], symbol)
        price = float(fetched.get("average") or fetched.get("price")
                      or self.fetch_price(symbol))
        filled = float(fetched.get("filled") or size)
        fee = 0.0
        for f in fetched.get("fees") or []:
            if f.get("currency") in ("USDT", "USD"):
                fee += float(f.get("cost") or 0.0)
        if fee == 0.0:
            fee = filled * price * self.cfg.taker_fee
        logger.info("Order filled: %s %s %.8f @ %.6f (fee %.6f)",
                    side, symbol, filled, price, fee)
        return Fill(symbol=symbol, side=side, size=filled, price=price, fee=fee)

    def ping(self) -> bool:
        try:
            self._call("fetch_time")
            return True
        except Exception:
            return False
