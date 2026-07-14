"""Обучение бейзлайна Win Probability (Гл. 6.2.2).

Стек по спецификации: LightGBM (binary) + калибратор поверх сырого выхода
(изотоническая регрессия на отложенных матчах). Метрика приёмки — Brier;
целевой порог из спецификации: ≤ 0.18 на реальных данных.

Запуск:
    python -m training.train_winprob [--synthetic N] [--out models/win_probability.pkl]

Реальные матчи читаются из MatchTimelineFeatures; пока их мало, добавка
--synthetic N дополняет датасет синтетикой (факт фиксируется в метаданных
артефакта — модель со синтетикой не должна попадать в прод).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, log_loss

from .dataset import (FEATURES, Dataset, dataset_hash, load_from_clickhouse,
                      merge, synth_matches)

logger = logging.getLogger("train_winprob")

MODEL_VERSION = "0.1.0"

LGB_PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "num_leaves": 31,
    "learning_rate": 0.05,
    "feature_fraction": 0.9,
    "verbose": -1,
    "seed": 42,
}


def train(ds: Dataset, num_rounds: int = 300) -> dict:
    """Обучить модель + калибратор; вернуть артефакт со всеми метаданными."""
    (X_tr, y_tr), (X_va, y_va) = ds.split_by_match()
    booster = lgb.train(
        LGB_PARAMS,
        lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURES),
        num_boost_round=num_rounds,
        valid_sets=[lgb.Dataset(X_va, label=y_va)],
        callbacks=[lgb.early_stopping(30, verbose=False)],
    )
    raw_va = booster.predict(X_va)

    # Калибровка (Гл. 6.2.2): изотоническая регрессия на отложенных матчах.
    calibrator = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    calibrator.fit(raw_va, y_va)
    cal_va = calibrator.predict(raw_va)

    metrics = {
        "brier_raw": round(float(brier_score_loss(y_va, raw_va)), 4),
        "brier_calibrated": round(float(brier_score_loss(y_va, cal_va)), 4),
        "logloss_calibrated": round(float(log_loss(y_va, np.clip(cal_va, 1e-6, 1 - 1e-6))), 4),
        "valid_rows": int(len(y_va)),
        "best_iteration": int(booster.best_iteration or num_rounds),
    }
    return {
        "model_version": MODEL_VERSION,
        "algo": "lightgbm+isotonic",
        "features": FEATURES,
        "booster": booster.model_to_string(),
        "calibrator": calibrator,
        "metrics": metrics,
        "dataset": {
            "matches": ds.n_matches,
            "synthetic_matches": ds.n_synthetic,
            "rows": int(len(ds.y)),
            "hash": dataset_hash(ds),
        },
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", type=int, default=0,
                    help="добавить N синтетических матчей (smoke-режим)")
    ap.add_argument("--min-matches", type=int, default=20)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2]
                                         / "models" / "win_probability.pkl"))
    args = ap.parse_args()

    real = load_from_clickhouse(
        os.getenv("CLICKHOUSE_URL", "http://localhost:8123"),
        os.getenv("CLICKHOUSE_DB", "dota_analyst"),
        os.getenv("CLICKHOUSE_USER", "dota"),
        os.getenv("CLICKHOUSE_PASSWORD", "dota_dev_password"))
    logger.info("real matches: %d (%d rows)", real.n_matches, len(real.y))

    ds = real
    if args.synthetic > 0:
        ds = merge(real, synth_matches(args.synthetic))
        logger.info("added %d synthetic matches", args.synthetic)

    if ds.n_matches < args.min_matches:
        logger.error("недостаточно матчей: %d < %d (добавьте --synthetic N "
                     "для smoke-прогона)", ds.n_matches, args.min_matches)
        return 1

    artifact = train(ds)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, out)
    logger.info("metrics: %s", json.dumps(artifact["metrics"]))
    logger.info("artifact saved: %s (%.1f KiB)", out, out.stat().st_size / 1024)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
