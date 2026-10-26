# -*- coding: utf-8 -*-
"""HTTP 客户端：curl_cffi 会话封装（cookie 注入、挑战检测、自动重过、限速重试）。"""
import random
import threading
import time
from typing import Callable, Optional

from curl_cffi import requests as creq

from .cf_session import CFSession, CHALLENGE_MARKS

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"


class ChallengeHit(Exception):
    """响应命中 CF 挑战页。"""


class StopRequested(RuntimeError):
    """用户请求停止：立即中止一切采集动作。"""


class HttpClient:
    """线程安全的采集会话：多线程共享 curl_cffi Session。"""

    def __init__(self, cf: CFSession, log: Callable[[str], None] = print,
                 min_delay: float = 0.6, max_delay: float = 1.6, max_retries: int = 3,
                 refresh_fn: Optional[Callable[[], bool]] = None,
                 stop_fn: Optional[Callable[[], bool]] = None):
        self.cf = cf
        self.log = log
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.refresh_fn = refresh_fn      # 会话续期回调（最多调用一次，由引擎控制）
        self.stop_fn = stop_fn or (lambda: False)   # 停止检测：任何等待/重试中立即可断
        self._lock = threading.Lock()
        self._last_req = 0.0
        self._closed = False
        self._session = self._build()

    def _check_stop(self):
        if self._closed or self.stop_fn():
            raise StopRequested("用户停止")

    def _interruptible_sleep(self, seconds: float):
        """可中断睡眠：每 0.2s 检查一次停止标志。"""
        end = time.time() + max(0.0, seconds)
        while True:
            self._check_stop()
            remain = end - time.time()
            if remain <= 0:
                return
            time.sleep(min(0.2, remain))

    def _build(self) -> creq.Session:
        # chrome150：curl_cffi 0.16 支持的最新 Chrome TLS 指纹；失败回退 chrome
        s = None
        for imp in ("chrome150", "chrome"):
            try:
                s = creq.Session(impersonate=imp)
                break
            except Exception:
                continue
        if s is None:
            s = creq.Session()
        s.headers.update({
            "User-Agent": self.cf.user_agent or DEFAULT_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8,zh-CN;q=0.7",
        })
        for k, v in (self.cf.cookies or {}).items():
            s.cookies.set(k, v, domain=".carousell.com.hk")
        return s

    # ---------- internals ----------

    def _throttle(self):
        wait = self.min_delay + random.random() * (self.max_delay - self.min_delay)
        with self._lock:
            elapsed = time.time() - self._last_req
            need = wait - elapsed
            self._last_req = time.time()
        if need > 0:
            self._interruptible_sleep(need)

    @staticmethod
    def _looks_challenged(resp) -> bool:
        if resp.status_code in (403, 429, 503):
            ct = resp.headers.get("content-type") or ""
            if "text/html" in ct:
                text = resp.text[:4000].lower()
                return any(m in text for m in (c.lower() for c in CHALLENGE_MARKS)) or "cf-mitigated" in (
                    resp.headers.get("cf-mitigated") or ""
                )
            if resp.headers.get("cf-mitigated") == "challenge":
                return True
        return False

    def _refresh_session(self):
        """CF 失效自愈：由引擎提供的回调续期（最多一次，避免重复拉起浏览器）。"""
        if not self.refresh_fn:
            self.log("[HTTP] cf_clearance 失效且无续期能力，中止")
            return False
        self.log("[HTTP] cf_clearance 失效，尝试续期…")
        try:
            if not self.refresh_fn():
                return False
            with self._lock:
                self._session = self._build()
            return True
        except Exception as e:
            self.log(f"[HTTP] 续期失败: {e}")
            return False

    def get(self, url: str, timeout: float = 30.0, referer: Optional[str] = None) -> "creq.Response":
        """带挑战自愈的 GET。停止请求立即可断（不等当前超时）。"""
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries + 2):
            self._check_stop()
            self._throttle()
            try:
                headers = {"Referer": referer} if referer else {}
                resp = self._session.get(url, timeout=timeout, headers=headers)
                if self._looks_challenged(resp):
                    raise ChallengeHit(resp.status_code)
                return resp
            except ChallengeHit:
                self._check_stop()
                if not self._refresh_session():
                    raise RuntimeError("CF 重过失败，采集中止")
                continue
            except Exception as e:  # 网络抖动重试
                last_err = e
                self._interruptible_sleep(1.5 * (attempt + 1))
                continue
        raise RuntimeError(f"请求失败（已重试）: {url[:80]} — {last_err}")

    def get_json(self, url: str, **kw):
        resp = self.get(url, **kw)
        return resp.json()

    def download(self, url: str, dest: str, timeout: float = 60.0) -> int:
        """图片下载（media CDN 无 CF，直连即可）。停止请求立即可断。"""
        for attempt in range(3):
            self._check_stop()
            try:
                resp = self._session.get(url, timeout=timeout, headers={"Referer": "https://www.carousell.com.hk/"})
                if resp.status_code == 200 and resp.content:
                    with open(dest, "wb") as f:
                        f.write(resp.content)
                    return len(resp.content)
            except StopRequested:
                raise
            except Exception:
                pass
            self._interruptible_sleep(1.0 * (attempt + 1))
        return 0

    def close(self):
        self._closed = True
        try:
            self._session.close()
        except Exception:
            pass
