"""Small shared helpers: retry with backoff, timeframe parsing."""

from __future__ import annotations

import functools
import logging
import time
from typing import Callable, Type

logger = logging.getLogger("kronos.utils")

_TF_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def timeframe_to_seconds(timeframe: str) -> int:
    """'5m' -> 300, '1h' -> 3600."""
    unit = timeframe[-1]
    if unit not in _TF_SECONDS:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    return int(timeframe[:-1]) * _TF_SECONDS[unit]


def retry(attempts: int = 4, base_delay: float = 2.0,
          exceptions: tuple[Type[BaseException], ...] = (Exception,),
          on_fail: Callable[[Exception], None] | None = None):
    """Retry decorator with exponential backoff (2s, 4s, 8s, ...)."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            delay = base_delay
            last: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:  # noqa: PERF203
                    last = exc
                    if attempt == attempts:
                        break
                    logger.warning("%s failed (attempt %d/%d): %s — retry in %.0fs",
                                   fn.__name__, attempt, attempts, exc, delay)
                    time.sleep(delay)
                    delay *= 2
            if on_fail is not None and last is not None:
                on_fail(last)
            raise last  # type: ignore[misc]
        return wrapper
    return decorator
