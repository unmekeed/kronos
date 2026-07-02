"""Turn a Kronos forecast into a trading signal."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..config import StrategyConfig
from ..model.predictor import Forecast


class Action(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    HOLD = "HOLD"


@dataclass
class Signal:
    action: Action
    price: float
    expected_return: float
    confidence: float

    @property
    def actionable(self) -> bool:
        return self.action is not Action.HOLD


def make_signal(forecast: Forecast, cfg: StrategyConfig) -> Signal:
    """LONG/SHORT only when both the predicted move and confidence clear thresholds."""
    action = Action.HOLD
    if (abs(forecast.expected_return) >= cfg.min_expected_return
            and forecast.confidence >= cfg.min_confidence):
        action = Action.LONG if forecast.expected_return > 0 else Action.SHORT
    return Signal(
        action=action,
        price=forecast.last_price,
        expected_return=forecast.expected_return,
        confidence=forecast.confidence,
    )
