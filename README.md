# Kronos Trading System

Автоматическая торговая система на базе модели прогнозирования свечей
[Kronos-mini](https://github.com/shiyu-coder/Kronos): прогноз → сигнал →
риск-менеджмент → исполнение (paper или live) → уведомления в Telegram.

## Архитектура

```
main.py                     — точка входа (paper / live)
kronos_trader/
├── config.py               — конфигурация (YAML + .env)
├── logger.py               — trade.log / error.log / system.log
├── state.py                — общее состояние (сделки, статистика, тайминги)
├── model/predictor.py      — Kronos-mini (+ mock-прогнозист без GPU)
├── strategy/signal.py      — прогноз → LONG / SHORT / HOLD
├── risk/manager.py         — размер позиции, стоп/тейк, лимиты, kill switch
├── exchange/
│   ├── live.py             — ccxt-клиент (retry + автопереподключение)
│   └── paper.py            — paper trading (реальные данные, виртуальные сделки)
├── trading/
│   ├── engine.py           — главный цикл (< 15 секунд на цикл)
│   └── positions.py        — учёт позиций и PnL
├── backtest/engine.py      — бэктест с комиссиями, проскальзыванием и внутрибарными стопами
├── telegram/
│   ├── bot.py              — команды (/status, /startbot, …)
│   └── notifier.py         — уведомления о событиях
└── monitoring/monitor.py   — GPU / RAM / соединение / время цикла
scripts/
├── download_data.py        — выгрузка истории OHLCV в CSV
└── run_backtest.py         — запуск бэктеста
tests/                      — 27 юнит- и интеграционных тестов
```

## Установка

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Kronos-mini (для реального прогноза)

Код модели не публикуется в PyPI — клонируйте репозиторий и добавьте его в
`PYTHONPATH`, затем установите torch:

```bash
git clone https://github.com/shiyu-coder/Kronos.git ../Kronos
pip install torch huggingface_hub
export PYTHONPATH="$PYTHONPATH:$(realpath ../Kronos)"
```

Веса (`NeoQuasar/Kronos-mini` + `NeoQuasar/Kronos-Tokenizer-2k`) скачиваются
с HuggingFace автоматически при первом запуске. Без GPU можно работать с
`model.use_mock: true` (моментум-прогнозист) — этого достаточно для проверки
всей инфраструктуры и paper trading.

### Секреты

```bash
cp .env.example .env
# заполните EXCHANGE_API_KEY / EXCHANGE_API_SECRET / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
```

Для paper-режима ключи биржи не нужны (данные берутся с публичных эндпоинтов).

## Запуск

```bash
python main.py                    # режим из config/config.yaml (paper)
python main.py --autostart        # сразу включить торговлю
python main.py --mode live        # реальная торговля (этап 8, депозит 10–50$)
```

После старта торговля ожидает команду `/startbot` в Telegram (если не указан
`--autostart`).

## Бэктест

```bash
python scripts/download_data.py --symbol BTC/USDT --timeframe 5m --days 30
python scripts/run_backtest.py --data data/BTCUSDT_5m.csv --mock        # без GPU
python scripts/run_backtest.py --data data/BTCUSDT_5m.csv --step 3      # с Kronos
```

Результат: суммарная доходность, winrate, максимальная просадка, Sharpe,
CSV со списком сделок в `reports/`.

## Telegram-бот

| Команда | Действие |
|---|---|
| `/status` | баланс, позиции, прибыль, режим работы |
| `/trade` | последняя сделка |
| `/stats` | статистика торговли |
| `/positions` | текущие позиции |
| `/profit` | доходность |
| `/startbot` | запуск торговли |
| `/stopbot` | остановка торговли |
| `/restart` | перезапуск движка (сброс kill switch) |
| `/risk` | текущие лимиты риска |

Команды принимаются только из чата `TELEGRAM_CHAT_ID`.

**Автоматические уведомления:** открытие/закрытие сделки, срабатывание
стопа/тейка, ошибки API, потеря/восстановление соединения с биржей,
превышение риск-лимитов, ежедневный отчёт (час задаётся в
`telegram.daily_report_hour_utc`). Повторяющиеся алерты дедуплицируются.

## Риск-менеджмент

- размер позиции: `баланс × risk_per_trade / stop_loss_pct`, с ограничением
  `max_position_pct` от баланса и минимальным ордером биржи;
- стоп-лосс и тейк-профит на каждую позицию (проверяются по high/low свечи);
- дневной лимит убытка (`max_daily_loss_pct`) — блокирует новые сделки до конца дня;
- kill switch по максимальной просадке (`max_drawdown_pct`) — останавливает
  торговлю, сбрасывается командой `/restart`;
- пауза после убыточной сделки (`cooldown_after_loss_sec`);
- шорты по умолчанию выключены (spot); для фьючерсов — `risk.allow_short: true`.

## Логирование

Файлы в `logs/` (ротация 10 МБ × 5):

- `trade.log` — `Дата | Инструмент | Цена | Прогноз | Уверенность | Действие | Размер позиции | PnL | Статус`
- `error.log` — ошибки и предупреждения
- `system.log` — системные события

## Мониторинг и производительность

Фоновый монитор проверяет: память GPU (`torch.cuda.mem_get_info`), RAM
(`psutil`), соединение с биржей (ping + уведомление о разрыве/восстановлении),
время полного цикла.

Требования и как они выполняются:

- **цикл < 15 секунд** — тайминг каждого цикла, предупреждение при превышении
  (`trading.max_cycle_time_sec`);
- **нет CUDA OOM** — ограниченный контекст (`model.max_context`),
  `empty_cache()` + деградация числа сэмплов при OOM;
- **retry при ошибках** — экспоненциальный backoff (2s/4s/8s) на всех вызовах биржи;
- **восстановление соединения** — автоматический reconnect ccxt-клиента;
- **24/7** — движок, бот и монитор в отдельных потоках; ошибки цикла не
  останавливают процесс; память под историю сделок ограничена.

Для непрерывной работы (7+ суток) — systemd:

```ini
[Unit]
Description=Kronos trading system
After=network-online.target

[Service]
WorkingDirectory=/opt/kronos
ExecStart=/opt/kronos/.venv/bin/python main.py --autostart
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Этапы внедрения

1. Окружение: `pip install -r requirements.txt`, `.env`, тесты `pytest tests/`.
2. Kronos-mini: клонировать репозиторий модели, проверить прогноз на GPU.
3. Биржа: paper-режим уже ходит на публичное API; для live добавить ключи.
4. Бэктест: `scripts/download_data.py` + `scripts/run_backtest.py`.
5. Риск-менеджер: настроить лимиты в `config.yaml` под депозит.
6. Telegram: создать бота у @BotFather, вписать токен и chat id.
7. Paper trading: `python main.py --autostart` (mode: paper), минимум неделя.
8. Live: `mode: live`, депозит 10–50$, `risk.min_order_usd` по правилам биржи.

## Тесты

```bash
.venv/bin/python -m pytest tests/ -q
```

Покрывают: риск-менеджер (сайзинг, лимиты, kill switch), paper-биржу
(комиссии, проскальзывание, балансы), сигналы, учёт позиций и стопов, бэктест
(детерминизм, сходимость учёта) и полный цикл движка (открытие → стоп-лосс).

⚠️ Торговля криптовалютой сопряжена с риском потери средств. Начинайте с
paper trading и минимального депозита.
