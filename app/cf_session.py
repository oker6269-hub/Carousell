# -*- coding: utf-8 -*-
"""CF 会话：Chrome 干净 CDP 一次性突破 Cloudflare，提取 cf_clearance 等 cookie。

设计要点（实测验证）：
- headed Chrome（不 headless），直连（默认无代理，可选 --proxy-server）
- 全程不发 Runtime.enable（Playwright 的检测向量），只用 /json/list 轮询标题
- profile 目录复用 → 挑战通过后 cookie 持久，二次启动秒过
- CDP 仅 Network.getCookies 一个调用
"""
import json
import queue
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

try:
    import websocket
except ImportError:  # pragma: no cover
    websocket = None

CHALLENGE_MARKS = ("just a moment", "請稍候", "请稍候", "moments", "checking your browser", "安全验证", "安全驗證")


def find_browser() -> Optional[str]:
    """定位可用的 Chromium 浏览器（Chrome 优先，其次 Edge）。"""
    import platform

    candidates = []
    if platform.system() == "Windows":
        candidates = [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe",
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        ]
    elif platform.system() == "Darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ]
    else:
        for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
            p = shutil.which(name)
            if p:
                candidates.append(Path(p))
    for c in candidates:
        if c.exists():
            return str(c)
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


class CFSession:
    """管理 Chrome 进程与 CF cookie 获取/续期。"""

    def __init__(self, profile_dir: str, log: Callable[[str], None] = print, proxy: str = ""):
        self.profile_dir = str(Path(profile_dir).resolve())
        self.log = log
        self.proxy = proxy
        self.browser_path = find_browser()
        self._proc: Optional[subprocess.Popen] = None
        self._port = 0
        self.cookies: dict[str, str] = {}
        self.user_agent = ""

    # ---------- lifecycle ----------

    def _launch(self, target_url: str) -> bool:
        if not self.browser_path:
            self.log("[CF] 未找到 Chrome/Edge 浏览器，请安装 Google Chrome")
            return False
        if websocket is None:
            self.log("[CF] 缺少 websocket-client 依赖")
            return False
        self._port = 19300 + (int(time.time()) % 200)
        cmd = [
            self.browser_path,
            f"--remote-debugging-port={self._port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1000,760",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
        ]
        if self.proxy:
            cmd.append(f"--proxy-server={self.proxy}")
        cmd.append(target_url)
        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not _wait_port(self._port, timeout=25):
            self.log("[CF] 浏览器调试端口未就绪")
            return False
        return True

    def stop(self):
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    # ---------- cookie acquisition ----------

    def obtain(self, target_url: str, wait_seconds: float = 120.0) -> bool:
        """启动浏览器访问目标 URL，等待 CF 挑战通过，提取 cookie。"""
        self.stop()
        t_start = time.time()
        if not self._launch(target_url):
            return False
        self.log(f"[CF] 浏览器已启动，等待挑战通过（首次约 15-40s，复用 profile 通常 <5s）…")
        page = None
        passed = False
        while time.time() - t_start < wait_seconds:
            time.sleep(2)
            try:
                pages = _pages_on(self._port)
                for p in pages:
                    url = p.get("url", "")
                    title = p.get("title", "")
                    if "carousell" in url:
                        page = p
                        if not _is_challenged(title) and "__cf_chl" not in url:
                            passed = True
                            break
                if passed:
                    break
            except Exception:
                pass
        if not passed:
            self.log("[CF] 挑战未在时限内通过（可能需要人工点击验证），请查看浏览器窗口")
            # 额外宽限一轮：允许人工干预
            extra = time.time() + 60
            while time.time() < extra:
                time.sleep(3)
                try:
                    for p in _pages_on(self._port):
                        if "carousell" in p.get("url", "") and not _is_challenged(p.get("title", "")) and "__cf_chl" not in p.get("url", ""):
                            page = p
                            passed = True
                            break
                except Exception:
                    pass
                if passed:
                    break
        if not passed:
            self.stop()
            return False
        self.log(f"[CF] 挑战已通过（{time.time()-t_start:.0f}s），提取 cookie…")
        try:
            cdp = _CDP(page["webSocketDebuggerUrl"])
            try:
                res = cdp.call("Network.getCookies", {"urls": [target_url.split("/u/")[0] + "/"]}, timeout=15)
                cl = res.get("cookies", [])
                self.cookies = {c["name"]: c["value"] for c in cl}
            finally:
                cdp.close()
        except Exception as e:
            self.log(f"[CF] cookie 提取失败: {e}")
            self.stop()
            return False
        self.user_agent = self._detect_ua()
        self.log(f"[CF] 获得 {len(self.cookies)} 个 cookie（cf_clearance: {'有' if 'cf_clearance' in self.cookies else '无'}）")
        self.stop()
        return "cf_clearance" in self.cookies

    def _detect_ua(self) -> str:
        """从页面探测 UA（保底用 Chrome 156 桌面 UA）。"""
        try:
            pages = _pages_on(self._port)
            for p in pages:
                if "carousell" in p.get("url", ""):
                    cdp = _CDP(p["webSocketDebuggerUrl"])
                    try:
                        res = cdp.call("Runtime.evaluate", {"expression": "navigator.userAgent", "returnByValue": True}, timeout=10)
                        ua = res.get("result", {}).get("value", "")
                        if ua:
                            return ua
                    finally:
                        cdp.close()
        except Exception:
            pass
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"

    # ---------- persistence ----------

    def save(self, path: str):
        Path(path).write_text(json.dumps({"cookies": self.cookies, "ua": self.user_agent}, ensure_ascii=False), encoding="utf-8")

    def load(self, path: str) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("cookies", {}).get("cf_clearance"):
                self.cookies = data["cookies"]
                self.user_agent = data.get("ua") or self.user_agent
                return True
        except Exception:
            pass
        return False
