# -*- coding: utf-8 -*-
"""调度器：定时自动增量更新（时间点/间隔触发，拉取新品）。"""
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Optional


class UpdateScheduler:
    """按时间点或间隔自动重跑采集（增量：state 去重，仅新 SKU 会被下载）。

    modes:
      - interval: 每 N 分钟跑一次
      - daily:    每天固定 HH:MM 跑
    """

    def __init__(self, run_fn: Callable[[], None], log: Callable[[str], None] = print,
                 status_fn: Optional[Callable[[], str]] = None):
        self.run_fn = run_fn
        self.log = log
        self.status_fn = status_fn or (lambda: "idle")
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._mode = "interval"
        self._interval_min = 60
        self._daily_hhmm = "09:00"
        self.last_run: Optional[datetime] = None
        self.next_run: Optional[datetime] = None
        self.run_count = 0

    # ---------- config ----------

    def configure(self, mode: str, interval_min: int = 60, daily_hhmm: str = "09:00"):
        self._mode = mode
        self._interval_min = max(1, int(interval_min))
        self._daily_hhmm = daily_hhmm
        self.next_run = self._compute_next()

    def _compute_next(self) -> datetime:
        now = datetime.now()
        if self._mode == "daily":
            hh, mm = 9, 0
            try:
                parts = self._daily_hhmm.split(":")
                hh, mm = int(parts[0]), int(parts[1])
            except Exception:
                pass
            candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate
        return now + timedelta(minutes=self._interval_min)

    # ---------- lifecycle ----------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.next_run = self._compute_next()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.log(f"[调度] 已启动: {'每 ' + str(self._interval_min) + ' 分钟' if self._mode == 'interval' else '每天 ' + self._daily_hhmm}"
                 f" | 下次: {self.next_run:%H:%M:%S}")

    def stop(self):
        self._stop.set()
        self.log("[调度] 已停止")

    def trigger_now(self):
        """立即触发一次（不等下一轮）。"""
        self.next_run = datetime.now()

    def _loop(self):
        while not self._stop.is_set():
            now = datetime.now()
            if self.next_run and now >= self.next_run:
                self.log(f"[调度] 触发自动更新（第 {self.run_count + 1} 次）")
                try:
                    self.run_fn()
                    self.run_count += 1
                    self.last_run = datetime.now()
                except Exception as e:
                    self.log(f"[调度] 运行异常: {e}")
                self.next_run = self._compute_next()
                self.log(f"[调度] 下次更新: {self.next_run:%m-%d %H:%M:%S}")
            self._stop.wait(20)
