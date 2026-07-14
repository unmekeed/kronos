# ML Service

Модели машинного обучения платформы (Гл. 6.2, Гл. 13.3.3). Спринт 10 —
бейзлайн **Win Probability** (Гл. 6.2.2): LightGBM (binary) + изотоническая
калибровка поверх сырого выхода.

## Структура

```
src/
├── training/
│   ├── dataset.py          # датасет из MatchTimelineFeatures + синтетика
│   └── train_winprob.py    # обучение + калибровка + артефакт
└── predictors/
    └── win_probability.py  # загрузка артефакта, WP-кривая матча (CLI)
models/                     # артефакты (в git не попадают — реестр/S3)
tests/                      # юнит-тесты конвейера на синтетике
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

Smoke-результат (1 реальный + 200 синтетических матчей): Brier
calibrated ≈ 0.13 на отложенных матчах (цель спецификации ≤ 0.18);
кривая реального матча 8892914077 отражает исход — ровная игра ~0.40–0.44
до 50-й минуты и обвал WP Radiant к 0 при развале в лейте (Dire победили).

## Артефакт

`models/win_probability.pkl` (joblib): booster (model_to_string),
калибратор, список фич, метрики, отпечаток датасета, версия. Валидация
совместимости фич выполняется при загрузке предиктора.

## Дальше

- Массовый датасет: Data Collector → сотни матчей → переобучение без
  `--synthetic`, контроль Brier ≤ 0.18 на реальных данных.
- gRPC-сервер `app.py` + фичи из Feature Store (онлайн-инференс).
- MLflow Registry вместо локального .pkl (Гл. 10.6).
