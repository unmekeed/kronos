"""Telegram notifications for every event required by the spec:

open/close of a trade, stop-loss hit, take-profit hit, API errors,
exchange disconnect, risk-limit breach, daily report.
"""

from __future__ import annotations

import logging
import threading
import time

import requests

from ..config import TelegramConfig

logger = logging.getLogger("kronos.telegram")

API = "https://api.telegram.org/bot{token}/{method}"


class Notifier:
    def __init__(self, cfg: TelegramConfig):
        self.cfg = cfg
        self.enabled = bool(cfg.enabled and cfg.bot_token and cfg.chat_id)
        self._lock = threading.Lock()
        self._last_sent: dict[str, float] = {}  # anti-spam per event key
        if cfg.enabled and not self.enabled:
            logger.warning("Telegram notifications disabled: token/chat_id missing")

    def send(self, text: str, dedup_key: str | None = None,
             dedup_interval: float = 300.0) -> None:
        """Send a message; identical `dedup_key` events are throttled."""
        if not self.enabled:
            logger.info("TG (disabled): %s", text.replace("\n", " | "))
            return
        if dedup_key is not None:
            with self._lock:
                last = self._last_sent.get(dedup_key, 0.0)
                if time.time() - last < dedup_interval:
                    return
                self._last_sent[dedup_key] = time.time()
        try:
            resp = requests.post(
                API.format(token=self.cfg.bot_token, method="sendMessage"),
                json={"chat_id": self.cfg.chat_id, "text": text,
                      "parse_mode": "HTML"},
                timeout=10,
            )
            if not resp.ok:
                logger.warning("Telegram send failed: %s %s",
                               resp.status_code, resp.text[:200])
        except requests.RequestException as exc:
            logger.warning("Telegram send failed: %s", exc)

    # ---- spec events ------------------------------------------------------

    def trade_opened(self, symbol: str, side: str, size: float, price: float,
                     stop: float, take: float, confidence: float) -> None:
        self.send(
            f"🟢 <b>Сделка открыта</b>\n"
            f"{symbol} {side}\n"
            f"Цена: {price:.6f}\nОбъём: {size:.8f}\n"
            f"Стоп: {stop:.6f} | Тейк: {take:.6f}\n"
            f"Уверенность: {confidence:.2f}")

    def trade_closed(self, symbol: str, side: str, price: float,
                     pnl: float, reason: str) -> None:
        icons = {"stop_loss": "🛑 <b>Сработал стоп</b>",
                 "take_profit": "🎯 <b>Сработал тейк</b>"}
        header = icons.get(reason, "🔵 <b>Сделка закрыта</b>")
        emoji = "✅" if pnl >= 0 else "❌"
        self.send(f"{header}\n{symbol} {side}\nЦена: {price:.6f}\n"
                  f"{emoji} PnL: {pnl:+.4f} USD\nПричина: {reason}")

    def api_error(self, message: str) -> None:
        self.send(f"⚠️ <b>Ошибка API</b>\n{message}",
                  dedup_key=f"api_error:{message[:60]}")

    def exchange_disconnected(self) -> None:
        self.send("🔌 <b>Биржа недоступна</b>\nСоединение потеряно, идёт переподключение…",
                  dedup_key="exchange_disconnected")

    def exchange_reconnected(self) -> None:
        self.send("🔌 <b>Соединение с биржей восстановлено</b>",
                  dedup_key="exchange_reconnected", dedup_interval=60.0)

    def risk_breach(self, message: str) -> None:
        self.send(f"🚨 <b>Превышение риска</b>\n{message}",
                  dedup_key=f"risk:{message[:60]}")

    def resource_alert(self, message: str) -> None:
        self.send(f"📈 <b>Мониторинг</b>\n{message}",
                  dedup_key=f"monitor:{message[:60]}")

    def daily_report(self, text: str) -> None:
        self.send(f"📊 <b>Ежедневный отчёт</b>\n{text}")
