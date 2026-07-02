"""System monitoring per spec: GPU memory, RAM, exchange connection, cycle time.

Runs in a background thread; alerts go to system.log and Telegram (deduplicated).
"""

from __future__ import annotations

import logging
import threading
import time

from ..config import MonitoringConfig, TradingConfig

logger = logging.getLogger("kronos.monitor")


class SystemMonitor:
    def __init__(self, cfg: MonitoringConfig, trading_cfg: TradingConfig,
                 exchange, forecaster, state, notifier):
        self.cfg = cfg
        self.trading_cfg = trading_cfg
        self.exchange = exchange
        self.forecaster = forecaster
        self.state = state
        self.notifier = notifier
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_conn_check = 0.0
        self._was_disconnected = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="monitor", daemon=True)
        self._thread.start()
        logger.info("System monitor started (interval %ds)", self.cfg.interval_sec)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._check_ram()
                self._check_gpu()
                self._check_connection()
                self._check_cycle_time()
            except Exception as exc:
                logger.warning("Monitor check failed: %s", exc)
            self._stop.wait(self.cfg.interval_sec)

    # ---- checks ---------------------------------------------------------

    def _check_ram(self) -> None:
        import psutil
        mem = psutil.virtual_memory()
        if mem.percent >= self.cfg.max_ram_pct:
            msg = f"RAM {mem.percent:.0f}% (лимит {self.cfg.max_ram_pct:.0f}%)"
            logger.warning(msg)
            self.notifier.resource_alert(msg)

    def _check_gpu(self) -> None:
        info = self.forecaster.gpu_mem_info()
        if info is None:
            return
        used, total = info
        pct = used / total * 100
        if pct >= self.cfg.max_gpu_mem_pct:
            msg = (f"GPU память {pct:.0f}% ({used / 2**30:.2f}/{total / 2**30:.2f} GiB, "
                   f"лимит {self.cfg.max_gpu_mem_pct:.0f}%)")
            logger.warning(msg)
            self.notifier.resource_alert(msg)

    def _check_connection(self) -> None:
        now = time.time()
        if now - self._last_conn_check < self.cfg.connection_check_interval_sec:
            return
        self._last_conn_check = now
        ok = self.exchange.ping()
        self.state.connection_ok = ok
        if not ok:
            logger.error("Exchange connection lost")
            self._was_disconnected = True
            self.notifier.exchange_disconnected()
        elif self._was_disconnected:
            logger.info("Exchange connection restored")
            self._was_disconnected = False
            self.notifier.exchange_reconnected()

    def _check_cycle_time(self) -> None:
        limit = self.trading_cfg.max_cycle_time_sec
        if self.state.last_cycle_time > limit:
            msg = (f"Цикл {self.state.last_cycle_time:.1f}s превысил лимит {limit:.0f}s")
            logger.warning(msg)
            self.notifier.resource_alert(msg)
