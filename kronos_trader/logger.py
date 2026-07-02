"""Logging per spec: trade.log, error.log, system.log with rotation.

Trade log line format (pipe separated):
    Date | Instrument | Price | Forecast | Confidence | Action | Position size | PnL | Status
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import LoggingConfig

_FMT = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
_TRADE_FMT = logging.Formatter("%(message)s")

_initialised = False


def setup_logging(cfg: LoggingConfig) -> None:
    """Create the three rotating log files and a console handler."""
    global _initialised
    if _initialised:
        return
    log_dir = Path(cfg.dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, cfg.level.upper(), logging.INFO)

    def handler(filename: str, fmt: logging.Formatter) -> RotatingFileHandler:
        h = RotatingFileHandler(log_dir / filename, maxBytes=cfg.max_bytes,
                                backupCount=cfg.backup_count, encoding="utf-8")
        h.setFormatter(fmt)
        return h

    root = logging.getLogger("kronos")
    root.setLevel(level)
    root.addHandler(handler("system.log", _FMT))
    console = logging.StreamHandler()
    console.setFormatter(_FMT)
    root.addHandler(console)

    errors = logging.getLogger("kronos.errors")
    err_handler = handler("error.log", _FMT)
    err_handler.setLevel(logging.WARNING)
    errors.addHandler(err_handler)

    trade = logging.getLogger("kronos.trades")
    trade.setLevel(logging.INFO)
    trade.addHandler(handler("trade.log", _TRADE_FMT))
    trade.propagate = False

    _initialised = True


def get_logger(name: str = "kronos") -> logging.Logger:
    return logging.getLogger(name)


def log_error(message: str, exc: Exception | None = None) -> None:
    logger = logging.getLogger("kronos.errors")
    if exc is not None:
        logger.error("%s: %s", message, exc, exc_info=exc)
    else:
        logger.error(message)


def log_trade(symbol: str, price: float, forecast: float, confidence: float,
              action: str, size: float, pnl: float | None, status: str) -> None:
    """Write one trade log line in the spec format."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    pnl_str = f"{pnl:+.4f}" if pnl is not None else "-"
    logging.getLogger("kronos.trades").info(
        "%s | %s | %.6f | %+.4f%% | %.2f | %s | %.8f | %s | %s",
        now, symbol, price, forecast * 100, confidence, action, size, pnl_str, status,
    )
