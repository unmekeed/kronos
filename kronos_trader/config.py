"""Configuration loading: YAML for behaviour, .env for secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional in tests
    def load_dotenv(*_a, **_kw):
        return False


@dataclass
class ExchangeConfig:
    id: str = "binance"
    market_type: str = "spot"
    symbol: str = "BTC/USDT"
    timeframe: str = "5m"
    taker_fee: float = 0.001
    slippage: float = 0.0005
    api_key: str = ""
    api_secret: str = ""


@dataclass
class ModelConfig:
    model_name: str = "NeoQuasar/Kronos-mini"
    tokenizer_name: str = "NeoQuasar/Kronos-Tokenizer-2k"
    device: str = "cuda:0"
    max_context: int = 512
    pred_len: int = 12
    n_samples: int = 3
    temperature: float = 1.0
    top_p: float = 0.9
    use_mock: bool = False


@dataclass
class StrategyConfig:
    min_expected_return: float = 0.0015
    min_confidence: float = 0.55
    close_on_opposite: bool = True


@dataclass
class RiskConfig:
    risk_per_trade: float = 0.01
    max_position_pct: float = 0.25
    max_open_positions: int = 1
    stop_loss_pct: float = 0.01
    take_profit_pct: float = 0.02
    max_daily_loss_pct: float = 0.05
    max_drawdown_pct: float = 0.15
    cooldown_after_loss_sec: int = 300
    min_order_usd: float = 10.0
    allow_short: bool = False


@dataclass
class TradingConfig:
    cycle_interval_sec: int = 60
    max_cycle_time_sec: float = 15.0
    paper_start_balance: float = 50.0


@dataclass
class TelegramConfig:
    enabled: bool = True
    poll_timeout_sec: int = 25
    daily_report_hour_utc: int = 20
    bot_token: str = ""
    chat_id: str = ""


@dataclass
class MonitoringConfig:
    interval_sec: int = 30
    max_ram_pct: float = 90.0
    max_gpu_mem_pct: float = 90.0
    connection_check_interval_sec: int = 60


@dataclass
class LoggingConfig:
    dir: str = "logs"
    level: str = "INFO"
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5


@dataclass
class Config:
    mode: str = "paper"
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _apply(section: Any, data: dict) -> None:
    for key, value in (data or {}).items():
        if hasattr(section, key):
            setattr(section, key, value)


def load_config(path: str | Path = "config/config.yaml") -> Config:
    """Load YAML config and merge secrets from environment / .env."""
    load_dotenv()
    cfg = Config()
    path = Path(path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        cfg.mode = raw.get("mode", cfg.mode)
        for name in ("exchange", "model", "strategy", "risk", "trading",
                     "telegram", "monitoring", "logging"):
            _apply(getattr(cfg, name), raw.get(name, {}))

    cfg.exchange.api_key = os.getenv("EXCHANGE_API_KEY", cfg.exchange.api_key)
    cfg.exchange.api_secret = os.getenv("EXCHANGE_API_SECRET", cfg.exchange.api_secret)
    cfg.telegram.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", cfg.telegram.bot_token)
    cfg.telegram.chat_id = os.getenv("TELEGRAM_CHAT_ID", cfg.telegram.chat_id)

    if cfg.mode not in ("paper", "live"):
        raise ValueError(f"mode must be 'paper' or 'live', got {cfg.mode!r}")
    if cfg.mode == "live" and not (cfg.exchange.api_key and cfg.exchange.api_secret):
        raise ValueError("live mode requires EXCHANGE_API_KEY / EXCHANGE_API_SECRET in .env")
    return cfg
