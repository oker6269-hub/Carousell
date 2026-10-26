# -*- coding: utf-8 -*-
"""浏览器辅助层（最终版）

职责（一次会话完成，用完即关）：
1) 过 CF 挑战（复用持久 profile，通常 5-20s）
2) 枚举卖家全部商品（点击「浏览更多」直到耗尽），返回 [{id, path, title?}]
3) 导出 cf_clearance 等 cookie 供 curl_cffi 纯协议采集

支持输入链接两种形态：
- /u/{seller}/        卖家主页
- /p/{slug}-{id}/     单商品 → 自动解析卖家 → 转入该卖家主页全量枚举
"""
import atexit
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from .cf_session import _CDP, _pages_on, _wait_port, find_browser, _is_challenged
CLICK_JS = """(() => {
    const els = Array.from(document.querySelectorAll('button, [role=button]'));
    const more = els.find(e => /浏览更多|瀏覽更多|load more|view more|查看更多/i.test(e.textContent || ''));
    if (more) { more.scrollIntoView({block: 'center'}); more.click(); return true; }
    return false;
})()"""

COUNT_JS = """document.querySelectorAll('a[href*="/p/"]').length"""

EXTRACT_JS = """(() => {
    const map = new Map();
    document.querySelectorAll('a[href*="/p/"]').forEach(a => {
        const href = a.getAttribute('href') || '';
        const path = href.split('?')[0].split('#')[0];
        const m = path.match(/-(\\d{6,})\\/?$/);
        if (m && !map.has(m[1])) map.set(m[1], path);
    });
    return JSON.stringify(Array.from(map, ([id, path]) => ({id, path})));
})()"""

SELLER_JS = """(() => {
    const counts = {};
    document.querySelectorAll('a[href^="/u/"]').forEach(a => {
        const m = (a.getAttribute('href') || '').match(/^\\/u\\/([A-Za-z0-9_.]+)\\/?$/);
        if (m && !/^(categories|login|signup)$/i.test(m[1])) counts[m[1]] = (counts[m[1]] || 0) + 1;
    });
    const best = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
    return best ? best[0] : '';
})()"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class BrowserSession:
    """短生命周期浏览器会话。"""

    def __init__(self, profile_dir: str, log: Callable[[str], None] = print,
                 proxy: str = "", use_temp_profile: bool = False,
                 browser_path: Optional[str] = None,
                 stop_fn: Optional[Callable[[], bool]] = None):
        self.profile_dir = profile_dir
        self.log = log
        self.proxy = proxy
        self.use_temp_profile = use_temp_profile
        self.stop_fn = stop_fn
        found = find_browser() or (None, "")
        self.browser_path = browser_path or found[0]
        self.browser_kind = found[1] or ("cloak" if browser_path else "chrome")
        self._proc: Optional[subprocess.Popen] = None
        self._port = 0
        self._temp_profile: Optional[str] = None
        self.cookies: dict[str, str] = {}
        self.user_agent = ""

    # ---------- lifecycle ----------

    def _profile(self) -> str:
        if self.use_temp_profile:
            self._temp_profile = tempfile.mkdtemp(prefix="carou-chrome-")
            return self._temp_profile
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        return self.profile_dir

    def _clear_profile_locks(self):
        """清理残留锁（上次异常退出遗留）。"""
        if self.use_temp_profile:
            return
        p = Path(self.profile_dir)
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
            try:
                f = p / name
                if f.exists() or f.is_symlink():
                    f.unlink()
            except Exception:
                pass

    def _launch(self, url: str) -> bool:
        if not self.browser_path:
            self.log("[浏览器] 未找到 Chrome/Edge/CloakBrowser，请安装 Google Chrome")
            return False
        self.close()
        self._kill_stale()
        self._port = _free_port()
        profile = self._profile()
        cmd = [
            self.browser_path,
            f"--remote-debugging-port={self._port}",
            f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check",
            "--window-size=1000,780",
            "--disable-session-crashed-bubble", "--hide-crash-restore-bubble",
            "--disable-background-networking",
            "--remote-allow-origins=*",   # Chromium 111+ CDP WebSocket origin 校验
        ]
        # macOS/Linux：绕过系统 Keychain（否则 Chrome 每次启动都弹密码授权框）
        if os.name == "posix":
            cmd += ["--use-mock-keychain", "--password-store=basic"]
        # CloakBrowser：固定指纹种子 → 同一 profile 始终同一指纹身份（更像真人回访）
        if self.browser_kind == "cloak":
            seed = self._fingerprint_seed()
            cmd.append(f"--fingerprint={seed}")
            self.log(f"[浏览器] CloakBrowser 内核（指纹种子 {seed[:8]}…）")
        else:
            self.log(f"[浏览器] {'Chrome' if self.browser_kind == 'chrome' else 'Edge'} 内核"
                     + ("（建议安装 CloakBrowser 提升反检测能力）" if self.browser_kind != "cloak" else ""))
        if self.proxy:
            cmd.append(f"--proxy-server={self.proxy}")
        cmd.append(url)
        # POSIX：独立会话，便于整组回收（Chrome 会派生多个子进程）
        kwargs = {"start_new_session": True} if os.name == "posix" else {}
        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, **kwargs)
        atexit.register(self.close)
        if _wait_port(self._port, timeout=30):
            return True
        self.log("[浏览器] 首次启动失败，清理锁后重试")
        self.close()
        self._clear_profile_locks()
        return False

    def _fingerprint_seed(self) -> str:
        """稳定指纹种子：存 profile 目录（.cloakbrowser-seed），跨次运行同一身份。"""
        import hashlib

        seed_file = Path(self.profile_dir) / ".cloakbrowser-seed"
        if seed_file.exists():
            try:
                return seed_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        seed = hashlib.sha256(str(self.profile_dir).encode()).hexdigest()[:16]
        try:
            seed_file.parent.mkdir(parents=True, exist_ok=True)
            seed_file.write_text(seed, encoding="utf-8")
        except Exception:
            pass
        return seed

    def _kill_stale(self):
        """清理上次异常退出残留的、使用我们 profile 的 Chrome 进程。"""
        if os.name != "posix":
            return
        try:
            subprocess.run(["pkill", "-f", self.profile_dir],
                           capture_output=True, timeout=10)
        except Exception:
            pass

    def close(self):
        """彻底回收：杀掉 Chrome 整个进程组（含渲染器/GPU 子进程）。"""
        proc = self._proc
        self._proc = None
        if proc:
            pid = proc.pid
            try:
                if os.name == "posix":
                    # 先整组 SIGTERM，再 SIGKILL 兜底
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGTERM)
                    except Exception:
                        proc.terminate()
                    try:
                        proc.wait(timeout=4)
                    except Exception:
                        try:
                            os.killpg(os.getpgid(pid), signal.SIGKILL)
                        except Exception:
                            proc.kill()
                else:
                    # Windows：taskkill /T 连带子进程
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                   capture_output=True, timeout=15)
                    try:
                        proc.wait(timeout=4)
                    except Exception:
                        proc.kill()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._kill_stale()
        if self._temp_profile:
            time.sleep(0.3)
            shutil.rmtree(self._temp_profile, ignore_errors=True)
            self._temp_profile = None

    # ---------- page ----------

    def _page(self) -> Optional[dict]:
        try:
            for p in _pages_on(self._port):
                if "carousell" in p.get("url", ""):
                    return p
            pages = _pages_on(self._port)
            return pages[0] if pages else None
        except Exception:
            return None

    def _wait_ready(self, timeout: float = 120.0) -> bool:
        """等待页面真正就绪：title 非挑战 + DOM 可查（商品链接或已水合的正文）。
        停止请求时立即返回 False。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._stop_requested():
                return False
            time.sleep(2)
            page = None
            try:
                for p in _pages_on(self._port):
                    if "carousell" in p.get("url", ""):
                        page = p
                        break
            except Exception:
                continue
            if not page:
                continue
            title = page.get("title", "")
            if _is_challenged(title) or "__cf_chl" in page.get("url", ""):
                continue
            try:
                cdp = _CDP(page["webSocketDebuggerUrl"])
                try:
                    r = cdp.call("Runtime.evaluate", {
                        "expression": """JSON.stringify({
                            ready: document.readyState,
                            links: document.querySelectorAll('a[href*="/p/"]').length,
                            title: document.title,
                            body: (document.body ? document.body.innerText.length : 0)
                        })""",
                        "returnByValue": True}, timeout=10)
                    st = json.loads(r.get("result", {}).get("value") or "{}")
                    if st.get("ready") == "complete" and not _is_challenged(st.get("title", "")) \
                            and (st.get("links", 0) > 0 or st.get("body", 0) > 200):
                        return True
                finally:
                    cdp.close()
            except Exception:
                pass
        return False

    def open_seller(self, url: str, challenge_timeout: float = 120.0) -> bool:
        """启动并等待就绪。

        只在**进程已死**时重新拉起浏览器；进程存活则复用同一实例重试导航，
        避免重复拉起 Chrome（macOS 上反复弹窗的问题）。
        停止请求时立即放弃（不再重试拉起）。
        """
        if not self._launch(url):
            self.log("[浏览器] 启动失败")
            return False
        for attempt in (1, 2, 3):
            if self._stop_requested():
                self.log("[浏览器] 用户停止，放弃等待")
                self.close()
                return False
            if self._wait_ready(challenge_timeout if attempt == 1 else 45):
                return True
            if self._stop_requested():
                self.log("[浏览器] 用户停止，放弃重试")
                self.close()
                return False
            alive = self._proc is not None and self._proc.poll() is None
            if not alive:
                self.log(f"[浏览器] 进程已退出，重新拉起（第 {attempt} 次）")
                if not self._launch(url):
                    return False
                continue
            self.log(f"[浏览器] 尚未就绪，复用现有窗口重试导航（第 {attempt} 次）")
            self._navigate(url)
        self.log("[浏览器] CF 未通过（可在弹出的窗口手动完成验证后重跑）")
        return False

    def _navigate(self, url: str) -> bool:
        page = self._page()
        if not page:
            return False
        cdp = _CDP(page["webSocketDebuggerUrl"])
        try:
            cdp.call("Page.enable", timeout=10)
            cdp.call("Page.navigate", {"url": url}, timeout=20)
            return True
        except Exception:
            return False
        finally:
            cdp.close()

    # ---------- public ----------

    def current_seller(self) -> str:
        page = self._page()
        if not page:
            return ""
        cdp = _CDP(page["webSocketDebuggerUrl"])
        try:
            cdp.call("Runtime.enable", timeout=15)
            r = cdp.call("Runtime.evaluate", {"expression": SELLER_JS, "returnByValue": True}, timeout=15)
            return r.get("result", {}).get("value") or ""
        except Exception:
            return ""
        finally:
            cdp.close()

    def enumerate_items(self, max_items: int = 5000, idle_rounds: int = 4,
                        click_wait: float = 1.2) -> list[dict]:
        """展开「浏览更多」，返回 [{id, path}]。停止请求时立即中断返回已展开部分。"""
        page = self._page()
        if not page:
            return []
        cdp = _CDP(page["webSocketDebuggerUrl"])
        items: list[dict] = []
        try:
            cdp.call("Runtime.enable", timeout=15)
            self._wait_hydrate(cdp)
            last = self._count(cdp)
            self.log(f"[枚举] 初始 {last} 个商品，展开中…")
            idle = 0
            for _ in range(500):
                if last >= max_items:
                    break
                if self._stop_requested():
                    self.log(f"[枚举] 用户停止（已展开 {last} 个）")
                    break
                clicked = self._click(cdp)
                time.sleep(click_wait)
                cur = self._count(cdp)
                if cur > last:
                    self.log(f"[枚举] 已加载 {cur} 个")
                    last = cur
                    idle = 0
                else:
                    idle += 1
                    if idle >= idle_rounds:
                        if not self._has_more(cdp):
                            break
                        if not clicked:
                            break
                        time.sleep(1.5)
                    else:
                        time.sleep(1.0)
            raw = cdp.call("Runtime.evaluate", {"expression": EXTRACT_JS, "returnByValue": True}, timeout=30)
            items = json.loads(raw.get("result", {}).get("value") or "[]")
            if len(items) > max_items:
                items = items[:max_items]
            self.log(f"[枚举] 完成：{len(items)} 个商品")
        except Exception as e:
            self.log(f"[枚举] 异常: {e}")
        finally:
            cdp.close()
        return items

    def _stop_requested(self) -> bool:
        return bool(self.stop_fn and self.stop_fn())

    def _wait_hydrate(self, cdp: "_CDP", timeout: float = 20.0) -> bool:
        """等待页面水合完成（React 挂载、商品卡渲染、『浏览更多』按钮出现）。
        停止请求时立即返回。"""
        t0 = time.time()
        ready = False
        while time.time() - t0 < timeout:
            if self._stop_requested():
                return ready
            try:
                r = cdp.call("Runtime.evaluate", {"expression": """(() => {
                    const links = document.querySelectorAll('a[href*="/p/"]').length;
                    const hasBtn = [...document.querySelectorAll('button,[role=button]')]
                        .some(e => /浏览更多|瀏覽更多|load more|view more|查看更多/i.test(e.textContent || ''));
                    return JSON.stringify({links, hasBtn, ready: document.readyState});
                })()""", "returnByValue": True}, timeout=15)
                st = json.loads(r.get("result", {}).get("value") or "{}")
                if st.get("ready") == "complete" and st.get("links", 0) > 0:
                    ready = True
                    if st.get("hasBtn"):
                        return True
                    # 无按钮：可能卖家 ≤20 个，也可能按钮在懒加载 —— 再等几秒
                    if time.time() - t0 > 8:
                        return True
            except Exception:
                pass
            time.sleep(1)
        return ready

    def _has_more(self, cdp: "_CDP") -> bool:
        try:
            r = cdp.call("Runtime.evaluate", {"expression": """(() => {
                return [...document.querySelectorAll('button,[role=button]')]
                    .some(e => /浏览更多|瀏覽更多|load more|view more|查看更多/i.test(e.textContent || ''));
            })()""", "returnByValue": True}, timeout=15)
            return bool(r.get("result", {}).get("value"))
        except Exception:
            return False

    def export_cookies(self, base_url: str = "https://www.carousell.com.hk/") -> bool:
        page = self._page()
        if not page:
            return False
        cdp = _CDP(page["webSocketDebuggerUrl"])
        try:
            res = cdp.call("Network.getCookies", {"urls": [base_url]}, timeout=15)
            self.cookies = {c["name"]: c["value"] for c in res.get("cookies", [])}
            ua = cdp.call("Runtime.evaluate", {"expression": "navigator.userAgent", "returnByValue": True}, timeout=10)
            self.user_agent = ua.get("result", {}).get("value", "") or ""
            self.log(f"[浏览器] 导出 {len(self.cookies)} 个 cookie（cf_clearance: "
                     f"{'有' if 'cf_clearance' in self.cookies else '无'}）")
            return "cf_clearance" in self.cookies
        except Exception as e:
            self.log(f"[浏览器] cookie 导出失败: {e}")
            return False
        finally:
            cdp.close()

    def collect_sellers(self, urls: list[str], max_items: int = 5000,
                        log_progress: Optional[Callable[[int, int, str], None]] = None) -> dict:
        """对一组输入链接（主页或商品）枚举全部商品。

        返回 {"sellers": {seller: [items]}, "cookie_ok": bool}
        """
        from .parser import parse_link_kind, parse_seller_username, parse_listing_id_from_url

        result = {"sellers": {}, "cookie_ok": False}
        try:
            if not self.browser_path:
                return result
            first = True
            for i, url in enumerate(urls, 1):
                if self._stop_requested():
                    self.log("[浏览器] 用户停止，结束枚举")
                    break
                kind = parse_link_kind(url)
                if kind == "unknown":
                    continue
                if first:
                    if not self.open_seller(url):
                        return result
                    first = False
                else:
                    if not self._navigate(url):
                        continue
                    if not self._wait_ready(60):
                        self.log(f"[浏览器] {url[:50]} 未就绪，跳过")
                        continue

                seller = ""
                if kind == "seller":
                    seller = parse_seller_username(url) or ""
                else:
                    seller = self.current_seller()
                    self.log(f"[解析] 单商品 → 卖家 {seller or '(未识别)'}")
                    if seller:
                        if not self._navigate(f"https://www.carousell.com.hk/u/{seller}/"):
                            continue
                        if not self._wait_ready(60):
                            continue

                if not seller:
                    continue
                if seller in result["sellers"]:
                    self.log(f"[枚举] {seller} 已枚举，跳过")
                    continue
                if log_progress:
                    log_progress(i, len(urls), seller)
                self.log(f"[枚举] 卖家 {seller} 全量展开…")
                result["sellers"][seller] = self.enumerate_items(max_items=max_items)

            result["cookie_ok"] = self.export_cookies()
            return result
        finally:
            self.close()

    # ---------- low-level ----------

    def _click(self, cdp: "_CDP") -> bool:
        try:
            r = cdp.call("Runtime.evaluate", {"expression": CLICK_JS, "returnByValue": True}, timeout=20)
            return bool(r.get("result", {}).get("value"))
        except Exception:
            return False

    def _count(self, cdp: "_CDP") -> int:
        try:
            r = cdp.call("Runtime.evaluate", {"expression": COUNT_JS, "returnByValue": True}, timeout=20)
            return int(r.get("result", {}).get("value") or 0)
        except Exception:
            return 0
