# ML Service

Модели машинного обучения платформы (Гл. 6.2, Гл. 13.3.3). Спринт 10 —
бейзлайн **Win Probability** (Гл. 6.2.2): LightGBM (binary) + изотоническая
калибровка поверх сырого выхода.

## Структура

```
src/
├── app.py                  # gRPC-сервер MLService (Predict/PredictStream)
├── gen/                    # стабы из proto/services.proto (make proto-gen)
├── training/
│   ├── dataset.py          # датасет из MatchTimelineFeatures + синтетика
│   └── train_winprob.py    # обучение + калибровка + артефакт
└── predictors/
    └── win_probability.py  # загрузка артефакта, WP-кривая матча (CLI)
models/                     # артефакты (в git не попадают — реестр/S3)
tests/                      # юнит-тесты конвейера + in-process gRPC-тесты
```

## gRPC-инференс (Гл. 3.7)

Контракт — `proto/services.proto` (источник истины, Гл. 13). Реализовано:

- `MLService.Predict(PredictRequest) → PredictResponse` — WP Radiant по
  `FeatureVector.values` (ключи `training.dataset.FEATURES`; отсутствие
  ключа — `INVALID_ARGUMENT`, неизвестная модель — `NOT_FOUND`);
- `MLService.PredictStream(stream FeatureFrame) → stream WinProbability`
  — потоковая WP-кривая (Гл. 6.2.2: пересчёт каждые N секунд).

```bash
make proto-gen          # перегенерировать стабы после правки proto
make ml-serve           # gRPC на :50051 (GRPC_PORT, MODEL_PATH)
```

## Датасет

Строка — снапшот матча в минуту t из `MatchTimelineFeatures`
(`game_time`, `networth_diff`, `xp_diff`, `kills_diff`, `kills_total`),
target — `radiant_win`. Сплит train/valid — **по матчам** (group split):
снапшоты одного матча скоррелированы, разрез по строкам дал бы утечку.

Пока реальных матчей мало, `--synthetic N` дополняет датасет симуляцией
(преимущество по золоту → sigmoid-вероятность победы). Доля синтетики
фиксируется в метаданных артефакта; такая модель — только для smoke.

## Запуск

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m training.train_winprob --synthetic 200
PYTHONPATH=src python -m predictors.win_probability 8892914077   # WP-кривая
pytest tests/
```

Текущие результаты (21 реальный матч, собранный конвейером OpenDota):

| Датасет | Brier calibrated | Комментарий |
|---|---|---|
| 21 реальный | **0.076** | валидация мала (~4 матча) — оценка шумная; изотоника вырождается (WP насыщается до 0/1) |
| 21 реальный + 100 синтетических | 0.097 | сервится по умолчанию: сглаживает калибровку, но переносит артефакт синтетики — переоценку ранних лидов |

Цель спецификации — Brier ≤ 0.18 на репрезентативной реальной выборке;
оба числа выше порога формально, но честная оценка требует сотен матчей.
Известное смещение: у синтетики дрейф золота постоянен внутри матча,
поэтому ранний лид почти детерминирует исход — модель переуверенна на
первых минутах. Лечится объёмом реальных данных, не кодом.

## Артефакт

`models/win_probability.pkl` (joblib): booster (model_to_string),
калибратор, список фич, метрики, отпечаток датасета, версия. Валидация
совместимости фич выполняется при загрузке предиктора.

## Дальше

- Массовый датасет: Data Collector → сотни матчей → переобучение без
  `--synthetic`, контроль Brier ≤ 0.18 на реальных данных.
- gRPC-сервер `app.py` + фичи из Feature Store (онлайн-инференс).
- MLflow Registry вместо локального .pkl (Гл. 10.6).
