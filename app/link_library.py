# -*- coding: utf-8 -*-
"""链接库：持久化所有运行过的卖家主页链接（全局共享，跨项目）。

存储 ~/.carousell-collector/link_library.json：
{
  "卖家名": {
    "url": "https://www.carousell.com.hk/u/xxx/",
    "first_seen": "2026-10-26T17:50:00",
    "last_run": "2026-10-26T18:02:11",
    "run_count": 5,
    "last_stats": {"new": 3, "skipped": 0, "failed": 0, "images": 18},
    "total_items": 45,
    "enabled": true
  }
}
"""
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional


class LinkLibrary:
    """所有被采集过的卖家链接与运行统计。"""

    def __init__(self, path: Optional[str] = None):
        if path is None:
            path = str(Path.home() / ".carousell-collector" / "link_library.json")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data: dict = self._load()

    # ---------- persistence ----------

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    # ---------- api ----------

    def record(self, seller: str, url: str, delta: Optional[dict] = None,
               total_items: Optional[int] = None):
        """记录一次运行（更新统计与时间）。线程安全。"""
        if not seller:
            return
        with self._lock:
            e = self._data.get(seller, {})
            now = datetime.now().isoformat(timespec="seconds")
            e.update({
                "url": url,
                "last_run": now,
                "run_count": int(e.get("run_count", 0)) + 1,
                "last_stats": delta or {},
                "enabled": e.get("enabled", True),
            })
            if not e.get("first_seen"):
                e["first_seen"] = now
            if total_items is not None:
                e["total_items"] = total_items
            self._data[seller] = e
            self._save()

    def entries(self) -> dict:
        """返回 {卖家: entry} 拷贝（按最近运行倒序）。"""
        items = sorted(self._data.items(),
                       key=lambda kv: kv[1].get("last_run", ""), reverse=True)
        return dict(items)

    def get(self, seller: str) -> dict:
        return self._data.get(seller, {})

    def set_enabled(self, seller: str, enabled: bool):
        with self._lock:
            if seller in self._data:
                self._data[seller]["enabled"] = bool(enabled)
                self._save()

    def remove(self, seller: str):
        with self._lock:
            self._data.pop(seller, None)
            self._save()

    def enabled_urls(self) -> list[str]:
        return [e["url"] for e in self._data.values() if e.get("enabled") and e.get("url")]

    def count(self) -> int:
        return len(self._data)
