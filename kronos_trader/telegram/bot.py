"""Telegram command bot (long polling, no external bot framework).

Commands per spec:
    /status    — balance, positions, profit, mode
    /trade     — last trade
    /stats     — trading statistics
    /positions — open positions
    /profit    — profitability
    /startbot  — start trading
    /stopbot   — stop trading
    /restart   — restart the trading engine
    /risk      — current risk limits
"""

from __future__ import annotations

import logging
import threading

import requests

from ..config import TelegramConfig
from .notifier import API

logger = logging.getLogger("kronos.telegram.bot")


class TelegramBot:
    def __init__(self, cfg: TelegramConfig, engine):
        """`engine` is the TradingEngine facade (status/start/stop/restart accessors)."""
        self.cfg = cfg
        self.engine = engine
        self.enabled = bool(cfg.enabled and cfg.bot_token and cfg.chat_id)
        self._offset = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- lifecycle -----------------------------------------------------

    def start(self) -> None:
        if not self.enabled:
            logger.info("Telegram bot disabled (no token/chat_id)")
            return
        self._thread = threading.Thread(target=self._poll_loop,
                                        name="telegram-bot", daemon=True)
        self._thread.start()
        logger.info("Telegram bot started")

    def stop(self) -> None:
        self._stop.set()

    # ---- polling ---------------------------------------------------------

    def _api(self, method: str, **params):
        resp = requests.post(API.format(token=self.cfg.bot_token, method=method),
                             json=params, timeout=self.cfg.poll_timeout_sec + 10)
        resp.raise_for_status()
        return resp.json()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._api("getUpdates", offset=self._offset,
                                 timeout=self.cfg.poll_timeout_sec,
                                 allowed_updates=["message"])
                for update in data.get("result", []):
                    self._offset = update["update_id"] + 1
                    self._handle_update(update)
            except Exception as exc:
                logger.warning("Telegram poll error: %s", exc)
                self._stop.wait(5)

    def _handle_update(self, update: dict) -> None:
        message = update.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        text = (message.get("text") or "").strip()
        if chat_id != str(self.cfg.chat_id):
            logger.warning("Ignoring command from unauthorized chat %s", chat_id)
            return
        if not text.startswith("/"):
            return
        command = text.split()[0].split("@")[0].lower()
        handler = getattr(self, f"_cmd_{command[1:]}", None)
        try:
            reply = handler() if handler else (
                "Неизвестная команда. Доступно: /status /trade /stats /positions "
                "/profit /startbot /stopbot /restart /risk")
        except Exception as exc:
            logger.exception("Command %s failed", command)
            reply = f"Ошибка выполнения команды: {exc}"
        self._api("sendMessage", chat_id=self.cfg.chat_id, text=reply,
                  parse_mode="HTML")

    # ---- commands ---------------------------------------------------------

    def _cmd_status(self) -> str:
        s = self.engine.status()
        positions = "\n".join(
            f"  {p['symbol']} {p['side']} {p['size']:.8f} @ {p['entry']:.6f} "
            f"(PnL {p['unrealized_pnl']:+.4f})"
            for p in s["positions"]) or "  нет"
        return (
            f"📟 <b>Статус</b>\n"
            f"Режим: {s['mode']} | Торговля: {'▶️ включена' if s['trading_enabled'] else '⏸ остановлена'}\n"
            f"Баланс: {s['balance']:.2f} USD\n"
            f"Прибыль (всего): {s['total_pnl']:+.4f} USD\n"
            f"Прибыль (сегодня): {s['daily_pnl']:+.4f} USD\n"
            f"Позиции:\n{positions}\n"
            f"Аптайм: {s['uptime']} | Цикл: {s['last_cycle_time']:.2f}s")

    def _cmd_trade(self) -> str:
        t = self.engine.state.last_trade()
        if t is None:
            return "Сделок ещё не было."
        pnl = f"{t.pnl:+.4f} USD" if t.pnl is not None else "—"
        return (f"🧾 <b>Последняя сделка</b>\n"
                f"{t.timestamp:%Y-%m-%d %H:%M:%S} UTC\n"
                f"{t.symbol} {t.side} {t.action}\n"
                f"Цена: {t.price:.6f} | Объём: {t.size:.8f}\n"
                f"Прогноз: {t.forecast * 100:+.3f}% | Уверенность: {t.confidence:.2f}\n"
                f"PnL: {pnl} | Причина: {t.reason or '—'}")

    def _cmd_stats(self) -> str:
        st = self.engine.state.stats()
        return (f"📊 <b>Статистика</b>\n"
                f"Закрытых сделок: {st['trades']}\n"
                f"Прибыльных: {st['wins']} | Убыточных: {st['losses']}\n"
                f"Winrate: {st['winrate']:.1%}\n"
                f"Суммарный PnL: {st['total_pnl']:+.4f} USD\n"
                f"Средний PnL: {st['avg_pnl']:+.4f} USD\n"
                f"Лучшая: {st['best']:+.4f} | Худшая: {st['worst']:+.4f}")

    def _cmd_positions(self) -> str:
        s = self.engine.status()
        if not s["positions"]:
            return "Открытых позиций нет."
        lines = [
            f"{p['symbol']} {p['side']}\n"
            f"  Объём: {p['size']:.8f} | Вход: {p['entry']:.6f}\n"
            f"  Стоп: {p['stop']:.6f} | Тейк: {p['take']:.6f}\n"
            f"  PnL: {p['unrealized_pnl']:+.4f} USD"
            for p in s["positions"]]
        return "📌 <b>Позиции</b>\n" + "\n".join(lines)

    def _cmd_profit(self) -> str:
        s = self.engine.status()
        start = self.engine.start_balance
        total_ret = (s["equity"] - start) / start if start else 0.0
        return (f"💰 <b>Доходность</b>\n"
                f"Начальный баланс: {start:.2f} USD\n"
                f"Текущий эквити: {s['equity']:.2f} USD\n"
                f"PnL всего: {s['total_pnl']:+.4f} USD\n"
                f"PnL сегодня: {s['daily_pnl']:+.4f} USD\n"
                f"Доходность: {total_ret:+.2%}")

    def _cmd_startbot(self) -> str:
        self.engine.start_trading()
        return "▶️ Торговля запущена."

    def _cmd_stopbot(self) -> str:
        self.engine.stop_trading()
        return "⏸ Торговля остановлена. Открытые позиции сохранены."

    def _cmd_restart(self) -> str:
        self.engine.restart()
        return "🔄 Движок перезапущен."

    def _cmd_risk(self) -> str:
        r = self.engine.risk.summary()
        kill = f"\n🚨 KILL SWITCH: {r['kill_reason']}" if r["kill_switch"] else ""
        return (f"🛡 <b>Лимиты риска</b>\n"
                f"Риск на сделку: {r['risk_per_trade']:.1%}\n"
                f"Макс. позиция: {r['max_position_pct']:.0%} баланса\n"
                f"Макс. открытых позиций: {r['max_open_positions']}\n"
                f"Стоп-лосс: {r['stop_loss_pct']:.1%} | Тейк-профит: {r['take_profit_pct']:.1%}\n"
                f"Дневной лимит убытка: {r['max_daily_loss_pct']:.0%}\n"
                f"Макс. просадка: {r['max_drawdown_pct']:.0%}\n"
                f"PnL сегодня: {r['daily_pnl']:+.4f} USD{kill}")
