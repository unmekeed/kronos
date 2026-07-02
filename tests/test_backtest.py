import numpy as np
import pandas as pd

from kronos_trader.backtest.engine import BacktestEngine
from kronos_trader.config import Config
from kronos_trader.model.predictor import MockForecaster


def synthetic_ohlcv(n=600, seed=7, drift=0.0005):
    """Trending random walk so the momentum mock finds trades."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.004, n)
    close = 100.0 * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.002, n)))
    open_ = np.concatenate([[100.0], close[:-1]])
    ts = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({"timestamps": ts, "open": open_, "high": high,
                         "low": low, "close": close, "volume": rng.uniform(1, 10, n)})


def make_config():
    cfg = Config()
    cfg.model.use_mock = True
    cfg.model.max_context = 100
    cfg.model.pred_len = 6
    cfg.strategy.min_expected_return = 0.0005
    cfg.strategy.min_confidence = 0.2
    cfg.risk.min_order_usd = 1.0
    cfg.trading.paper_start_balance = 1000.0
    return cfg


def test_backtest_runs_and_produces_trades():
    cfg = make_config()
    engine = BacktestEngine(cfg, MockForecaster(cfg.model), start_balance=1000.0)
    result = engine.run(synthetic_ohlcv())
    assert result.start_balance == 1000.0
    assert len(result.equity_curve) > 0
    assert len(result.trades) > 0
    # accounting sanity: end balance == start + sum of trade PnL
    assert abs(result.end_balance - (1000.0 + sum(t.pnl for t in result.trades))) < 1e-6
    assert 0.0 <= result.winrate <= 1.0
    assert 0.0 <= result.max_drawdown <= 1.0


def test_backtest_deterministic_with_seed():
    cfg = make_config()
    df = synthetic_ohlcv()
    r1 = BacktestEngine(cfg, MockForecaster(cfg.model, seed=1), 1000.0).run(df)
    r2 = BacktestEngine(cfg, MockForecaster(cfg.model, seed=1), 1000.0).run(df)
    assert r1.end_balance == r2.end_balance
    assert len(r1.trades) == len(r2.trades)


def test_backtest_needs_enough_data():
    cfg = make_config()
    engine = BacktestEngine(cfg, MockForecaster(cfg.model), 1000.0)
    short_df = synthetic_ohlcv(n=50)
    try:
        engine.run(short_df)
        raised = False
    except ValueError:
        raised = True
    assert raised
