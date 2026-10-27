# -*- coding: utf-8 -*-
"""代理池：动态 IP 轮换。

配置位置：~/.carousell-collector/proxies.json

    {
      "enabled": true,
      "strategy": "round_robin",     // round_robin | random | least_used
      "requests_per_ip": 200,        // 单个 IP 发够多少条详情请求后轮换
      "cooldown_minutes": 10,        // 被站点拒绝后该 IP 冷却多久
      "proxies": [
        {"label": "HK-01", "url": "socks5://user:pass@1.2.3.4:1080",
         "enabled": true, "used": 0, "fails": 0, "last_used": "", "cooldown_until": 0}
      ]
    }

为什么是「按段轮换」而不是「每个请求轮换」：
cf_clearance 与出口 IP 绑定，换 IP 后必须重新过一次 CF；每请求轮换等于每条都卡在验证上。
"""
import json
import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from typing import Callable, Optional

STRATEGIES = {
    "round_robin": "轮询",
    "random": "随机",
    "least_used": "最少使用",
}
DEFAULT_REQUESTS_PER_IP = 200
DEFAULT_COOLDOWN_MIN = 10


def mask_proxy(url: str) -> str:
    """隐去密码，便于在日志/界面上展示。"""
    return re.sub(r"(://[^:/@]+:)[^@]+@", r"\1***@", url or "")


def parse_proxy(raw: str) -> str:
    """规范化用户输入。

    支持这些写法（一行一条）：
        host:port
        host:port:user:pass          ← 很多代理商的导出格式
        user:pass@host:port
        socks5://user:pass@host:port
    """
    raw = (raw or "").strip()
    if not raw or raw.startswith("#"):
        return ""
    if "://" in raw:
        return raw
    parts = raw.split(":")
    if len(parts) == 4 and parts[1].isdigit():
        host, port, user, pwd = (x.strip() for x in parts)
        return f"http://{quote(user, safe='')}:{quote(pwd, safe='')}@{host}:{port}"
    if len(parts) >= 2 and parts[1].isdigit():
        return "http://" + raw
    return "http://" + raw


class ProxyPool:
    """代理池状态与轮换策略。线程安全（GUI 与后台采集线程共用）。"""

    def __init__(self, path: Optional[str] = None, log: Callable[[str], None] = print):
        self.path = Path(path) if path else Path.home() / ".carousell-collector" / "proxies.json"
        self.log = log
        self._lock = threading.RLock()
        self._data = {
            "enabled": False,
            "strategy": "round_robin",
            "requests_per_ip": DEFAULT_REQUESTS_PER_IP,
            "cooldown_minutes": DEFAULT_COOLDOWN_MIN,
            "proxies": [],
        }
        self._current: Optional[int] = None
        self._current_used = 0
        self._load()

    # ---------- 读写 ----------

    def _load(self):
        try:
            if not self.path.exists():
                return
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            self.log(f"[IP] 配置读取失败，按空池处理: {e}")
            return
        if not isinstance(data, dict):
            return
        with self._lock:
            self._data["enabled"] = bool(data.get("enabled", False))
            if data.get("strategy") in STRATEGIES:
                self._data["strategy"] = data["strategy"]
            try:
                self._data["requests_per_ip"] = max(1, int(data.get("requests_per_ip", DEFAULT_REQUESTS_PER_IP)))
            except (TypeError, ValueError):
                pass
            try:
                self._data["cooldown_minutes"] = max(0, int(data.get("cooldown_minutes", DEFAULT_COOLDOWN_MIN)))
            except (TypeError, ValueError):
                pass
            items = data.get("proxies")
            if isinstance(items, list):
                cleaned = []
                for it in items:
                    if isinstance(it, str):
                        it = {"url": it}
                    if not isinstance(it, dict) or not str(it.get("url", "")).strip():
                        continue
                    cleaned.append({
                        "label": str(it.get("label", ""))[:40],
                        "url": str(it["url"]).strip(),
                        "enabled": bool(it.get("enabled", True)),
                        "used": int(it.get("used", 0) or 0),
                        "fails": int(it.get("fails", 0) or 0),
                        "last_used": str(it.get("last_used", "") or ""),
                        "cooldown_until": float(it.get("cooldown_until", 0) or 0),
                    })
                self._data["proxies"] = cleaned

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:
            self.log(f"[IP] 配置保存失败: {e}")

    # ---------- 查询 ----------

    @property
    def enabled(self) -> bool:
        """用户是否勾了「启用」（不代表一定有可用出口）。"""
        return bool(self._data.get("enabled"))

    @property
    def usable(self) -> bool:
        """启用且至少有一个不在冷却期的出口——采集时才用它判断。"""
        return self.enabled and self.available_count() > 0

    def set_enabled(self, on: bool):
        with self._lock:
            self._data["enabled"] = bool(on)
            self._save()

    @property
    def strategy(self) -> str:
        return self._data.get("strategy", "round_robin")

    def set_strategy(self, strategy: str):
        with self._lock:
            if strategy in STRATEGIES:
                self._data["strategy"] = strategy
                self._save()

    @property
    def requests_per_ip(self) -> int:
        return int(self._data.get("requests_per_ip", DEFAULT_REQUESTS_PER_IP))

    def set_requests_per_ip(self, n: int):
        with self._lock:
            self._data["requests_per_ip"] = max(1, int(n))
            self._save()

    def proxies(self) -> list:
        with self._lock:
            return [dict(p) for p in self._data.get("proxies", [])]

    def available_count(self) -> int:
        now = time.time()
        return sum(1 for p in self._data.get("proxies", [])
                   if p.get("enabled") and p.get("cooldown_until", 0) <= now)

    def current(self) -> Optional[dict]:
        with self._lock:
            if self._current is None or not (0 <= self._current < len(self._data["proxies"])):
                return None
            return dict(self._data["proxies"][self._current])

    def current_used(self) -> int:
        return self._current_used

    # ---------- 增删改 ----------

    def add(self, raw_urls: list, label: str = "") -> int:
        """批量添加；返回实际新增条数（重复 URL 跳过）。"""
        added = 0
        with self._lock:
            existing = {p["url"] for p in self._data["proxies"]}
            for raw in raw_urls:
                url = parse_proxy(raw)
                if not url or url in existing:
                    continue
                self._data["proxies"].append({
                    "label": (label or f"IP{len(self._data['proxies']) + 1}")[:40],
                    "url": url, "enabled": True, "used": 0, "fails": 0,
                    "last_used": "", "cooldown_until": 0,
                })
                existing.add(url)
                added += 1
            if added:
                self._save()
        return added

    def remove(self, index: int):
        with self._lock:
            items = self._data.get("proxies", [])
            if 0 <= index < len(items):
                items.pop(index)
                self._current = None
                self._current_used = 0
                self._save()

    def toggle(self, index: int):
        with self._lock:
            items = self._data.get("proxies", [])
            if 0 <= index < len(items):
                items[index]["enabled"] = not items[index].get("enabled", True)
                self._save()

    def clear_stats(self):
        with self._lock:
            for p in self._data.get("proxies", []):
                p["used"] = 0
                p["fails"] = 0
                p["cooldown_until"] = 0
            self._current_used = 0
            self._save()

    def set_proxy_status(self, index: int, ok: bool, note: str = ""):
        """连通性测试结果回写（只影响展示，不参与轮换）。"""
        with self._lock:
            items = self._data.get("proxies", [])
            if 0 <= index < len(items):
                items[index]["last_test"] = f"{'OK' if ok else 'FAIL'} {note}".strip()
                self._save()

    # ---------- 轮换 ----------

    def should_rotate(self) -> bool:
        if self._current is None:
            return True
        return self._current_used >= self.requests_per_ip

    def next(self) -> Optional[dict]:
        """按策略挑下一个可用代理（跳过冷却中的），并把它设为当前。"""
        with self._lock:
            items = self._data.get("proxies", [])
            now = time.time()
            cands = [i for i, p in enumerate(items)
                     if p.get("enabled") and p.get("cooldown_until", 0) <= now]
            if not cands:
                return None
            strategy = self.strategy
            if strategy == "random":
                idx = random.choice([i for i in cands if i != self._current] or cands)
            elif strategy == "least_used":
                pool = [i for i in cands if i != self._current] or cands
                idx = min(pool, key=lambda i: items[i].get("used", 0))
            else:  # round_robin
                after = [i for i in cands if self._current is None or i > self._current]
                idx = after[0] if after else cands[0]
            self._current = idx
            self._current_used = 0
            items[idx]["last_used"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            self._save()
            return dict(items[idx])

    def mark_requests(self, n: int = 1):
        with self._lock:
            self._current_used += max(0, n)
            if self._current is not None and 0 <= self._current < len(self._data["proxies"]):
                self._data["proxies"][self._current]["used"] += max(0, n)
                self._save()

    def mark_blocked(self, reason: str = ""):
        """当前 IP 被站点拒绝：记一次失败并进入冷却。"""
        with self._lock:
            if self._current is None:
                return
            p = self._data["proxies"][self._current]
            p["fails"] = int(p.get("fails", 0)) + 1
            mins = int(self._data.get("cooldown_minutes", DEFAULT_COOLDOWN_MIN))
            if mins > 0:
                p["cooldown_until"] = time.time() + mins * 60
            self._save()
            self.log(f"[IP] {mask_proxy(p['url'])} 被拒绝（{reason or '限流'}），"
                     f"冷却 {mins} 分钟，失败累计 {p['fails']} 次")

    def request_rotation(self):
        """界面上的「立即轮换」：下一次检查时强制换出口。"""
        with self._lock:
            self._current_used = self.requests_per_ip

    def reset_cursor(self):
        with self._lock:
            self._current = None
            self._current_used = 0
