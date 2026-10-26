# -*- coding: utf-8 -*-
"""浏览器辅助层：唯一可信客户端。

职责（一次启动，全程复用）：
1) 过 CF 挑战（复用持久 profile，通常 5-20s）
2) 枚举卖家全部商品（点击「浏览更多」直到耗尽），返回 [{id, path}]
3) 在页面上下文里并发 fetch 详情页，抽出结构化 listing（同源请求，天然同身份）

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


# 详情页抓取：同源 fetch + 从内联状态 JSON 里取 listingsMap（字段比 ld+json 全：
# 类目、卖家、完整图片数组都在里面）。返回精简结构，避免把整页 HTML 搬回 Python。
_FETCH_LISTINGS_JS = r"""
(async () => {
  const urls = __URLS__;
  const grabState = (html) => {
    const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
    for (const s of scripts) {
      const t = s.trim();
      if (t.length < 2000 || !t.includes('"listingsMap"')) continue;
      try { return JSON.parse(t); } catch (e) {}
    }
    return null;
  };
  const out = [];
  await Promise.all(urls.map(async (u) => {
    try {
      const resp = await fetch(u, {credentials: 'include'});
      if (resp.status !== 200) { out.push({path: u, status: resp.status, d: null}); return; }
      const st = grabState(await resp.text());
      const map = st && st.Listing && st.Listing.listingsMap;
      if (!map) { out.push({path: u, status: resp.status, d: null}); return; }
      const L = map[Object.keys(map)[0]] || {};
      const attrs = L.listing_attributes || {};
      out.push({path: u, status: resp.status, d: {
        id: String(L.id || ''),
        title: L.title || '',
        desc: L.flattened_description || L.description || '',
        price: L.price || '',
        currency: L.currency_code || 'HKD',
        category: attrs.category || '',
        seller: (L.seller && L.seller.username) || '',
        images: (L.photos || []).map(p => p && p.image_url).filter(Boolean)
      }});
    } catch (e) {
      out.push({path: u, status: 0, d: null});
    }
  }));
  return JSON.stringify(out);
})()
"""


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
        self._errlog = None
        self._recovered = False
        self._port = 0
        self._temp_profile: Optional[str] = None

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
        # 不再丢弃内核输出：崩溃时唯一线索就在这里（内核 FATAL 只写 stderr）
        self._errlog = tempfile.TemporaryFile()
        try:
            self._proc = subprocess.Popen(cmd, stdout=self._errlog,
                                          stderr=subprocess.STDOUT, **kwargs)
        except OSError as e:   # 二进制缺失 / 架构不符 / 权限不足
            self.log(f"[浏览器] 无法执行内核: {e}")
            self.close()
            return False
        atexit.register(self.close)
        if self._wait_browser(timeout=30):
            return True
        rc = self._proc.poll() if self._proc is not None else None
        tail = self._read_errlog()
        self.log(f"[浏览器] 启动失败（退出码 {rc}）{tail}")
        self.close()
        self._clear_profile_locks()
        # 进程异常退出（非 0）→ profile 多半已损坏：隔离旧 profile 后用全新的再试一次
        # 实测：Default/Sync Data 损坏会让内核启动 1 秒内 SIGTRAP，且不写任何日志
        if rc not in (None, 0) and not self.use_temp_profile and not self._recovered:
            if self._quarantine_profile():
                self._recovered = True
                self.log("[浏览器] profile 已隔离，换全新 profile 重试")
                return self._launch(url)
        return False

    def _wait_browser(self, timeout: float) -> bool:
        """等调试端口就绪；内核提前退出就立刻返回 False（不再空等 30 秒）。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._proc is not None and self._proc.poll() is not None:
                return False
            if _wait_port(self._port, timeout=0.5):
                return True
        return False

    def _read_errlog(self) -> str:
        f = self._errlog
        if f is None:
            return ""
        try:
            f.seek(0)
            lines = [l for l in f.read().decode("utf-8", "replace").splitlines() if l.strip()]
        except Exception:
            return ""
        return ("：" + lines[-1][-180:]) if lines else ""

    def _quarantine_profile(self) -> bool:
        """把起不来的 profile 改名隔离（保留现场便于排查），下次 _profile() 会新建。"""
        p = Path(self.profile_dir)
        if not p.is_dir():
            return False
        target = p.with_name(f"{p.name}.broken-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            p.rename(target)
            self.log(f"[浏览器] 旧 profile 已移到 {target.name}")
            return True
        except OSError as e:
            self.log(f"[浏览器] 隔离 profile 失败: {e}")
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
        if self._errlog is not None:
            try:
                self._errlog.close()
            except Exception:
                pass
            self._errlog = None
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

    def fetch_listings(self, urls: list[str], timeout: float = 150.0) -> dict[str, dict]:
        """在页面上下文里并发 fetch 详情页，返回 {url: 精简 listing 字段}。

        单一客户端原则：请求由浏览器自己发出，cookie/UA/TLS 天然一致，
        不再把 cf_clearance 导出给另一个客户端复用（那是设计上的错）。
        并发由 JS 的 Promise.all 完成，一批几条由调用方决定。
        """
        page = self._page()
        if not page:
            return {}
        cdp = _CDP(page["webSocketDebuggerUrl"])
        try:
            js = _FETCH_LISTINGS_JS.replace("__URLS__", json.dumps(urls, ensure_ascii=False))
            res = cdp.call("Runtime.evaluate",
                           {"expression": js, "awaitPromise": True, "returnByValue": True},
                           timeout=timeout)
            rows = json.loads(res.get("result", {}).get("value") or "[]")
        except Exception as e:
            self.log(f"[浏览器] 详情抓取失败: {e}")
            return {}
        finally:
            cdp.close()
        out: dict[str, dict] = {}
        for row in rows:
            if isinstance(row, dict) and row.get("d"):
                out[row.get("path") or ""] = row["d"]
        return out

    def collect_sellers(self, urls: list[str], max_items: int = 5000,
                        log_progress: Optional[Callable[[int, int, str], None]] = None) -> dict:
        """对一组输入链接（主页或商品）枚举全部商品。

        返回 {"sellers": {seller: [items]}}。调用方负责 close()：
        浏览器要保持打开，后续取详情仍走同一个客户端。
        """
        from .parser import parse_link_kind, parse_seller_username

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

            return result
        except Exception as e:
            self.log(f"[浏览器] 枚举异常: {e}")
            return result

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
