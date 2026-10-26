# -*- coding: utf-8 -*-
"""图片下载器（唯一的非浏览器网络出口）。

media.karousell.com 没有 Cloudflare 防护，直连即可，线程池并发下载。
**页面类请求不在这里**：那些必须由浏览器自己发，否则 cookie/UA/TLS 指纹
对不上，会被 CF 判成另一个客户端（这是旧方案的根本错误）。
"""
import random
import threading
import time
from typing import Callable, Optional

from curl_cffi import requests as creq

# 只影响图片 CDN（不校验指纹），任何常见桌面 UA 均可
DEFAULT_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")

BASE_REFERER = "https://www.carousell.com.hk/"


class StopRequested(RuntimeError):
    """用户请求停止：立即中止一切下载动作。"""


class ImageFetcher:
    """线程安全的图片下载会话。"""

    def __init__(self, log: Callable[[str], None] = print,
                 min_delay: float = 0.0, max_delay: float = 0.0,
                 stop_fn: Optional[Callable[[], bool]] = None):
        self.log = log
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.stop_fn = stop_fn or (lambda: False)
        self._lock = threading.Lock()
        self._last = 0.0
        self._closed = False
        self._session = creq.Session()
        self._session.headers.update({"User-Agent": DEFAULT_UA, "Referer": BASE_REFERER})

    def _check_stop(self):
        if self._closed or self.stop_fn():
            raise StopRequested("用户停止")

    def _sleep(self, seconds: float):
        end = time.time() + max(0.0, seconds)
        while True:
            self._check_stop()
            remain = end - time.time()
            if remain <= 0:
                return
            time.sleep(min(0.2, remain))

    def _throttle(self):
        if self.max_delay <= 0:
            return
        wait = self.min_delay + random.random() * (self.max_delay - self.min_delay)
        with self._lock:
            need = wait - (time.time() - self._last)
            self._last = time.time()
        if need > 0:
            self._sleep(need)

    def download(self, url: str, dest: str, timeout: float = 60.0) -> int:
        """下载单个链接到 dest，返回字节数（失败返回 0）。停止请求立即可断。"""
        for attempt in range(3):
            self._check_stop()
            self._throttle()
            try:
                resp = self._session.get(url, timeout=timeout)
                if resp.status_code == 200 and resp.content:
                    with open(dest, "wb") as f:
                        f.write(resp.content)
                    return len(resp.content)
            except StopRequested:
                raise
            except Exception:
                pass
            self._sleep(1.0 * (attempt + 1))
        return 0

    def close(self):
        self._closed = True
        try:
            self._session.close()
        except Exception:
            pass
