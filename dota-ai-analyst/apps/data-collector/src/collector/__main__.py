"""Точка входа Data Collector: периодический цикл сбора.

Запуск:  python -m collector [--source fixture|opendota] [--interval 300]
"""
from __future__ import annotations

import argparse
import logging
import os
import time

from .runner import Collector, CollectorConfig
from .sources.fixture import FixtureSource
from .sources.opendota import OpenDotaSource


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='{"time":"%(asctime)s","level":"%(levelname)s",'
               '"service":"data-collector","msg":"%(message)s"}')

    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=os.getenv("COLLECTOR_SOURCE", "fixture"),
                        choices=["fixture", "opendota"])
    parser.add_argument("--interval", type=int,
                        default=int(os.getenv("COLLECTOR_INTERVAL_SECONDS", "300")))
    parser.add_argument("--once", action="store_true",
                        help="один проход и выход (для тестов/CI)")
    args = parser.parse_args()

    source = FixtureSource() if args.source == "fixture" else OpenDotaSource()
    cfg = CollectorConfig(
        postgres_dsn=os.getenv(
            "POSTGRES_DSN",
            "postgresql://dota:dota_dev_password@localhost:5432/dota_analyst"),
        kafka_brokers=os.getenv("KAFKA_BROKERS", "localhost:9092"),
        s3_endpoint=os.getenv("S3_ENDPOINT", "localhost:9500"),
        s3_access_key=os.getenv("S3_ACCESS_KEY", "dota"),
        s3_secret_key=os.getenv("S3_SECRET_KEY", "dota_dev_password"),
        s3_bucket=os.getenv("S3_BUCKET", "replays"),
    )

    collector = Collector(cfg, source)
    try:
        while True:
            n = collector.collect_once()
            logging.getLogger("collector").info("cycle done, processed=%s", n)
            if args.once:
                break
            time.sleep(args.interval)
    finally:
        collector.close()


if __name__ == "__main__":
    main()
