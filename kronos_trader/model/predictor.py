"""Kronos-mini forecaster.

Wraps KronosPredictor from https://github.com/shiyu-coder/Kronos (the repo must
be on PYTHONPATH — it is not published to PyPI). Torch and the Kronos code are
imported lazily so that backtests, tests and paper trading with the mock
forecaster work on machines without a GPU.

Confidence is estimated from `n_samples` independent sampled forecasts:
    confidence = sign_agreement * tanh(|mean_return| / recent_volatility)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import ModelConfig

logger = logging.getLogger("kronos.model")


@dataclass
class Forecast:
    """Model output for one cycle."""
    last_price: float
    predicted_price: float          # mean predicted close at horizon
    expected_return: float          # (predicted - last) / last
    confidence: float               # 0..1
    horizon: int                    # candles ahead
    sample_returns: list[float] = field(default_factory=list)


def _recent_volatility(closes: np.ndarray, window: int = 48) -> float:
    """Std of returns over the recent window, floored to avoid div-by-zero."""
    rets = np.diff(np.log(closes[-window - 1:]))
    vol = float(np.std(rets)) if len(rets) > 1 else 0.0
    return max(vol, 1e-5)


def _aggregate(sample_returns: list[float], volatility: float) -> tuple[float, float]:
    """Mean expected return + confidence from sample agreement and signal/noise."""
    mean_ret = float(np.mean(sample_returns))
    if mean_ret == 0.0:
        return 0.0, 0.0
    signs = np.sign(sample_returns)
    agreement = float(np.mean(signs == np.sign(mean_ret)))
    signal_to_noise = math.tanh(abs(mean_ret) / volatility)
    return mean_ret, agreement * signal_to_noise


class KronosForecaster:
    """Real Kronos-mini inference with OOM protection."""

    def __init__(self, cfg: ModelConfig):
        self.cfg = cfg
        self._predictor = None

    def load(self) -> None:
        """Load tokenizer + model onto the configured device."""
        if self._predictor is not None:
            return
        try:
            from model import Kronos, KronosPredictor, KronosTokenizer  # Kronos repo
        except ImportError as exc:
            raise RuntimeError(
                "Kronos model code not found. Clone https://github.com/shiyu-coder/Kronos "
                "and add it to PYTHONPATH, or set model.use_mock: true in config."
            ) from exc
        logger.info("Loading %s / %s on %s", self.cfg.model_name,
                    self.cfg.tokenizer_name, self.cfg.device)
        tokenizer = KronosTokenizer.from_pretrained(self.cfg.tokenizer_name)
        model = Kronos.from_pretrained(self.cfg.model_name)
        self._predictor = KronosPredictor(
            model, tokenizer, device=self.cfg.device, max_context=self.cfg.max_context
        )
        logger.info("Kronos-mini loaded")

    def gpu_mem_info(self) -> tuple[float, float] | None:
        """(used_bytes, total_bytes) for the model device, None on CPU."""
        try:
            import torch
            if not self.cfg.device.startswith("cuda") or not torch.cuda.is_available():
                return None
            idx = torch.device(self.cfg.device).index or 0
            free, total = torch.cuda.mem_get_info(idx)
            return float(total - free), float(total)
        except Exception:
            return None

    def _predict_once(self, df: pd.DataFrame) -> float:
        """One sampled forecast; returns expected return at the horizon."""
        window = df.tail(self.cfg.max_context)
        x_df = window[["open", "high", "low", "close", "volume"]]
        x_ts = pd.Series(window["timestamps"].values)
        step = window["timestamps"].iloc[-1] - window["timestamps"].iloc[-2]
        y_ts = pd.Series([window["timestamps"].iloc[-1] + step * (i + 1)
                          for i in range(self.cfg.pred_len)])
        pred_df = self._predictor.predict(
            df=x_df, x_timestamp=x_ts, y_timestamp=y_ts,
            pred_len=self.cfg.pred_len, T=self.cfg.temperature,
            top_p=self.cfg.top_p, sample_count=1, verbose=False,
        )
        last_close = float(window["close"].iloc[-1])
        pred_close = float(pred_df["close"].iloc[-1])
        return (pred_close - last_close) / last_close

    def predict(self, df: pd.DataFrame) -> Forecast:
        """df: columns timestamps(datetime), open, high, low, close, volume."""
        self.load()
        closes = df["close"].to_numpy(dtype=float)
        last_price = float(closes[-1])
        vol = _recent_volatility(closes)

        samples: list[float] = []
        n = max(1, self.cfg.n_samples)
        for i in range(n):
            try:
                samples.append(self._predict_once(df))
            except Exception as exc:
                if self._is_oom(exc):
                    logger.warning("CUDA OOM on sample %d — clearing cache, degrading", i)
                    self._clear_cuda()
                    if samples:
                        break  # use what we have
                    raise
                raise

        mean_ret, confidence = _aggregate(samples, vol)
        return Forecast(
            last_price=last_price,
            predicted_price=last_price * (1 + mean_ret),
            expected_return=mean_ret,
            confidence=confidence,
            horizon=self.cfg.pred_len,
            sample_returns=samples,
        )

    @staticmethod
    def _is_oom(exc: Exception) -> bool:
        return "out of memory" in str(exc).lower()

    @staticmethod
    def _clear_cuda() -> None:
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass


class MockForecaster:
    """Momentum-based pseudo-forecast: no GPU, deterministic with a seed.

    Used for tests, backtest dry-runs and paper trading without the model.
    """

    def __init__(self, cfg: ModelConfig, seed: int = 42):
        self.cfg = cfg
        self._rng = np.random.default_rng(seed)

    def load(self) -> None:
        pass

    def gpu_mem_info(self):
        return None

    def predict(self, df: pd.DataFrame) -> Forecast:
        closes = df["close"].to_numpy(dtype=float)
        last_price = float(closes[-1])
        vol = _recent_volatility(closes)
        # momentum over ~pred_len recent candles + noise per sample
        lookback = min(len(closes) - 1, self.cfg.pred_len)
        momentum = (closes[-1] - closes[-1 - lookback]) / closes[-1 - lookback]
        samples = [float(momentum * 0.5 + self._rng.normal(0, vol))
                   for _ in range(max(1, self.cfg.n_samples))]
        mean_ret, confidence = _aggregate(samples, vol)
        return Forecast(
            last_price=last_price,
            predicted_price=last_price * (1 + mean_ret),
            expected_return=mean_ret,
            confidence=confidence,
            horizon=self.cfg.pred_len,
            sample_returns=samples,
        )


def create_forecaster(cfg: ModelConfig):
    return MockForecaster(cfg) if cfg.use_mock else KronosForecaster(cfg)
