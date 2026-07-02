"""Download historical OHLCV to CSV for backtesting.

    python scripts/download_data.py --symbol BTC/USDT --timeframe 5m \
        --days 30 --out data/BTCUSDT_5m.csv
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from kronos_trader.config import load_config
from kronos_trader.exchange.live import LiveExchange
from kronos_trader.utils import timeframe_to_seconds


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--timeframe", default=None)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    symbol = args.symbol or cfg.exchange.symbol
    timeframe = args.timeframe or cfg.exchange.timeframe
    out = args.out or f"data/{symbol.replace('/', '')}_{timeframe}.csv"

    exchange = LiveExchange(cfg.exchange)
    tf_sec = timeframe_to_seconds(timeframe)
    since_ms = int((time.time() - args.days * 86400) * 1000)
    frames = []
    while True:
        raw = exchange._call("fetch_ohlcv", symbol, timeframe=timeframe,
                             since=since_ms, limit=1000)
        if not raw:
            break
        frames.append(pd.DataFrame(
            raw, columns=["ts", "open", "high", "low", "close", "volume"]))
        last_ts = raw[-1][0]
        if len(raw) < 1000:
            break
        since_ms = last_ts + tf_sec * 1000
        print(f"  fetched up to {pd.to_datetime(last_ts, unit='ms', utc=True)}")

    if not frames:
        print("No data fetched", file=sys.stderr)
        return 1
    df = pd.concat(frames).drop_duplicates("ts").sort_values("ts")
    df["timestamps"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    df[["timestamps", "open", "high", "low", "close", "volume"]].to_csv(out, index=False)
    print(f"Saved {len(df)} candles to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
