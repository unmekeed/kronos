"""Run a backtest over a CSV produced by download_data.py.

    python scripts/run_backtest.py --data data/BTCUSDT_5m.csv
    python scripts/run_backtest.py --data data/BTCUSDT_5m.csv --mock   # no GPU
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from kronos_trader.backtest.engine import BacktestEngine
from kronos_trader.config import load_config
from kronos_trader.logger import setup_logging
from kronos_trader.model.predictor import create_forecaster


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--data", required=True, help="CSV from download_data.py")
    parser.add_argument("--balance", type=float, default=None)
    parser.add_argument("--step", type=int, default=1,
                        help="candles between decisions (speeds up model runs)")
    parser.add_argument("--mock", action="store_true",
                        help="use the mock forecaster instead of Kronos")
    parser.add_argument("--report", default="reports/backtest_trades.csv")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.mock:
        cfg.model.use_mock = True
    setup_logging(cfg.logging)

    df = pd.read_csv(args.data, parse_dates=["timestamps"])
    forecaster = create_forecaster(cfg.model)
    forecaster.load()

    engine = BacktestEngine(cfg, forecaster, start_balance=args.balance)
    result = engine.run(df, step=args.step)

    print()
    print(result.summary())
    if result.trades:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([t.__dict__ for t in result.trades]).to_csv(report, index=False)
        print(f"Trade list saved to {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
