"""End-to-end paper-trading cycle test with the mock forecaster: the engine
opens a position on a strong forecast and closes it when the stop is hit."""

import time

import numpy as np
import pandas as pd
import pytest

from kronos_trader.config import Config
from kronos_trader.exchange.paper import PaperExchange
from kronos_trader.model.predictor import Forecast
from kronos_trader.risk.manager import RiskManager
from kronos_trader.state import RuntimeState
from kronos_trader.trading.engine import TradingEngine


class DummyNotifier:
    def __init__(self):
        self.events = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.events.append((name, args))
        return record


class ScriptedForecaster:
    """Returns a fixed forecast; lets the test force LONG then HOLD."""

    def __init__(self):
        self.next_return = 0.02
        self.next_confidence = 0.9

    def load(self):
        pass

    def gpu_mem_info(self):
        return None

    def predict(self, df):
        price = float(df["close"].iloc[-1])
        return Forecast(last_price=price,
                        predicted_price=price * (1 + self.next_return),
                        expected_return=self.next_return,
                        confidence=self.next_confidence, horizon=6)


class ScriptedPaper(PaperExchange):
    """Paper exchange fed by a mutable candle script instead of a live feed."""

    def __init__(self, cfg, balance):
        super().__init__(cfg, balance, price_fn=lambda _s: self.current_price)
        self.current_price = 100.0
        self.candle = {"high": 100.5, "low": 99.5}

    def fetch_ohlcv(self, symbol, timeframe, limit):
        n = limit
        ts = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
        close = np.full(n, self.current_price)
        high = close.copy()
        low = close.copy()
        high[-1] = self.candle["high"]
        low[-1] = self.candle["low"]
        return pd.DataFrame({"timestamps": ts, "open": close, "high": high,
                             "low": low, "close": close,
                             "volume": np.ones(n)})

    def ping(self):
        return True


@pytest.fixture
def setup():
    cfg = Config()
    cfg.model.use_mock = True
    cfg.model.max_context = 50
    cfg.risk.min_order_usd = 1.0
    cfg.risk.cooldown_after_loss_sec = 0
    cfg.telegram.enabled = False
    exchange = ScriptedPaper(cfg.exchange, 1000.0)
    forecaster = ScriptedForecaster()
    state = RuntimeState()
    notifier = DummyNotifier()
    engine = TradingEngine(cfg, exchange, forecaster,
                           RiskManager(cfg.risk), state, notifier)
    engine.start_balance = 1000.0
    state.trading_enabled = True
    return engine, exchange, forecaster, state, notifier


def test_open_then_stop_loss(setup):
    engine, exchange, forecaster, state, notifier = setup

    engine._cycle()  # strong LONG forecast -> opens a position
    assert engine.positions.count() == 1
    pos = engine.positions.get(engine.cfg.exchange.symbol)
    assert pos.side == "LONG"
    assert state.last_trade().action == "OPEN"
    assert any(e[0] == "trade_opened" for e in notifier.events)

    # price crashes through the stop on the next candle
    forecaster.next_return = 0.0
    forecaster.next_confidence = 0.0
    exchange.current_price = pos.stop_price * 0.999
    exchange.candle = {"high": pos.stop_price, "low": pos.stop_price * 0.99}
    engine._cycle()

    assert engine.positions.count() == 0
    last = state.last_trade()
    assert last.action == "CLOSE"
    assert last.reason == "stop_loss"
    assert last.pnl < 0
    assert any(e[0] == "trade_closed" for e in notifier.events)


def test_cycle_time_fast(setup):
    engine = setup[0]
    start = time.monotonic()
    engine._cycle()
    elapsed = time.monotonic() - start
    # performance requirement: full cycle < 15s (mock is instant)
    assert elapsed < 15.0


def test_status_snapshot(setup):
    engine, exchange, forecaster, state, notifier = setup
    engine._cycle()
    s = engine.status()
    assert s["mode"] == "paper"
    assert s["trading_enabled"] is True
    assert len(s["positions"]) == 1
    assert s["balance"] < 1000.0  # cash went into the position
    assert s["equity"] == pytest.approx(1000.0, rel=0.01)  # minus fees only
