"""Kronos trading system entry point.

    python main.py                 # mode from config (paper by default)
    python main.py --mode live     # real trading (requires API keys in .env)
    python main.py --mode paper    # paper trading
    python main.py --autostart     # enable trading immediately (else /startbot)
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading

from kronos_trader.config import load_config
from kronos_trader.exchange.live import LiveExchange
from kronos_trader.exchange.paper import PaperExchange
from kronos_trader.logger import get_logger, setup_logging
from kronos_trader.model.predictor import create_forecaster
from kronos_trader.monitoring.monitor import SystemMonitor
from kronos_trader.risk.manager import RiskManager
from kronos_trader.state import RuntimeState
from kronos_trader.telegram.bot import TelegramBot
from kronos_trader.telegram.notifier import Notifier
from kronos_trader.trading.engine import TradingEngine


def build_exchange(cfg):
    live = LiveExchange(cfg.exchange)
    if cfg.mode == "live":
        return live
    # paper mode: real market data via public endpoints, simulated fills
    return PaperExchange(cfg.exchange, cfg.trading.paper_start_balance,
                         data_source=live)


def main() -> int:
    parser = argparse.ArgumentParser(description="Kronos-mini trading system")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--mode", choices=["paper", "live"], default=None,
                        help="override mode from config")
    parser.add_argument("--autostart", action="store_true",
                        help="enable trading immediately instead of waiting for /startbot")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.mode:
        cfg.mode = args.mode
    setup_logging(cfg.logging)
    logger = get_logger()
    logger.info("=== Kronos trading system starting (mode=%s) ===", cfg.mode)

    state = RuntimeState(mode=cfg.mode)
    notifier = Notifier(cfg.telegram)
    exchange = build_exchange(cfg)
    forecaster = create_forecaster(cfg.model)
    risk = RiskManager(cfg.risk)
    engine = TradingEngine(cfg, exchange, forecaster, risk, state, notifier)
    monitor = SystemMonitor(cfg.monitoring, cfg.trading, exchange, forecaster,
                            state, notifier)
    bot = TelegramBot(cfg.telegram, engine)

    engine.start()
    monitor.start()
    bot.start()
    if args.autostart:
        engine.start_trading()
    notifier.send(
        f"🚀 Система запущена (режим: <b>{cfg.mode}</b>, {cfg.exchange.symbol}).\n"
        f"Торговля: {'включена' if args.autostart else 'ожидает /startbot'}")

    stop_event = threading.Event()

    def handle_signal(signum, _frame):
        logger.info("Received signal %s, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    stop_event.wait()

    bot.stop()
    monitor.stop()
    engine.shutdown()
    notifier.send("🛑 Система остановлена.")
    logger.info("=== Shutdown complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
