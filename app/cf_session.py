# -*- coding: utf-8 -*-
"""CDP 工具层：内核定位、调试端口探测、极简 CDP 客户端。

只提供「一个客户端」所需的最底层能力，不再负责导出/复用 cookie：
- 定位安装好的内核（CloakBrowser > Chrome > Edge）
- 探测调试端口、轮询页面、判定挑战页
- 极简 CDP 调用（消息泵 + 按 id 关联响应）
"""
import json
import queue
import shutil
import socket
import time
import urllib.request
from pathlib import Path
from typing import Optional

try:
    import websocket
except ImportError:  # pragma: no cover
    websocket = None

CHALLENGE_MARKS = ("just a moment", "請稍候", "请稍候", "moments", "checking your browser", "安全验证", "安全驗證")


def find_cloakbrowser() -> Optional[str]:
    """定位 CloakBrowser 定制 Chromium（源码级指纹伪装，过 CF 更稳）。

    安装位置：~/.cloakbrowser/chromium-<ver>/ 下的可执行文件。
    """
    root = Path.home() / ".cloakbrowser"
    if not root.is_dir():
        return None
    names = ("chrome", "chrome.exe", "Chromium", "Chromium.app/Contents/MacOS/Chromium")
    # 版本号倒序：优先最新
    dirs = sorted([d for d in root.iterdir() if d.is_dir() and d.name.startswith("chromium-")],
                  reverse=True)
    for d in dirs:
        for n in names:
            exe = d / n
            if exe.exists():
                return str(exe)
        # macOS bundle 形态
        for app in d.glob("*.app"):
            exe = app / "Contents" / "MacOS" / (app.stem or "Chromium")
            if exe.exists():
                return str(exe)
    return None


def find_browser(prefer_cloak: bool = True) -> Optional[tuple[str, str]]:
    """定位可用浏览器。返回 (路径, 类型)。

    类型：cloak（CloakBrowser 定制内核，带指纹伪装） / chrome / edge
    优先级：CloakBrowser > Chrome > Edge
    """
    import platform

    if prefer_cloak:
        cb = find_cloakbrowser()
        if cb:
            return cb, "cloak"

    candidates = []
    if platform.system() == "Windows":
        candidates = [
            (Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"), "chrome"),
            (Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"), "chrome"),
            (Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe", "chrome"),
            (Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"), "edge"),
            (Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"), "edge"),
        ]
    elif platform.system() == "Darwin":
        candidates = [
            (Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"), "chrome"),
            (Path("/Applications/Chromium.app/Contents/MacOS/Chromium"), "chrome"),
            (Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"), "edge"),
        ]
    else:
        for name, kind in (("google-chrome", "chrome"), ("chromium", "chrome"),
                           ("chromium-browser", "chrome"), ("microsoft-edge", "edge")):
            p = shutil.which(name)
            if p:
                candidates.append((Path(p), kind))
    for c, kind in candidates:
        if c.exists():
            return str(c), kind
    return None


def _wait_port(port: int, timeout: float = 20.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return True
        except OSError:
            time.sleep(0.3)
    return False


class _CDP:
    """极简 CDP 客户端：统一消息泵，按 id 关联响应。"""

    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.events: "queue.Queue[dict]" = queue.Queue()
        self._id = 0
        self._pending: dict[int, dict] = {}

    def _recv_once(self, timeout: float = 1.0):
        self.ws.settimeout(timeout)
        try:
            msg = json.loads(self.ws.recv())
        except websocket.WebSocketTimeoutException:
            return None
        except Exception:
            return None
        if "id" in msg:
            self._pending[msg["id"]] = msg
        else:
            self.events.put(msg)
        return msg

    def call(self, method: str, params: Optional[dict] = None, timeout: float = 30.0) -> dict:
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        t0 = time.time()
        while time.time() - t0 < timeout:
            if mid in self._pending:
                msg = self._pending.pop(mid)
                if "error" in msg:
                    raise RuntimeError(f"CDP {method}: {msg['error']}")
                return msg.get("result", {})
            self._recv_once(0.5)
        raise TimeoutError(method)

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def _pages_on(port: int) -> list[dict]:
    info = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5).read())
    return [t for t in info if t.get("type") == "page"]


def _is_challenged(title: str) -> bool:
    t = (title or "").lower()
    return any(m in t for m in CHALLENGE_MARKS) or title == ""
