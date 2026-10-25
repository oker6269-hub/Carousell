# -*- coding: utf-8 -*-
"""采集引擎：多链接编排、断点增量、类目过滤、图片下载、Excel 记录、汇总。"""
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

from .cf_session import CFSession
from .downloader import download_sku_images
from .http_client import HttpClient
from .parser import (parse_detail, parse_listing_cards, parse_seller_username,
                     parse_link_kind, parse_listing_id_from_url, extract_seller_from_detail)
from .rules import infer_brand, infer_gender
from .storage import ExcelStore, FilterStore, StateStore

BASE = "https://www.carousell.com.hk"


class CollectorEngine:
    def __init__(self, root: str, log: Callable[[str], None] = print,
                 progress: Callable[[int, int, str], None] = lambda c, t, m: None,
                 proxy: str = "", min_delay: float = 0.6, max_delay: float = 1.6,
                 img_workers: int = 4, detail_concurrency: int = 2, max_items: int = 5000,
                 path_style: str = "native"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "logs").mkdir(exist_ok=True)
        self.log = log
        self.progress = progress
        self.stop_event = threading.Event()
        self.proxy = proxy
        self.max_items = max_items
        # Folder 列的路径风格：native=系统原生绝对路径 / posix=正斜杠绝对路径 / relative=仅 SKU
        self.path_style = path_style if path_style in ("native", "posix", "relative") else "native"

        self.state = StateStore(str(self.root / "state.json"))
        self.filters = FilterStore(str(self.root / "filters.json"))
        self.excel = ExcelStore(str(self.root), log=log)
        # Chrome profile 放共享目录：cf_clearance 跨任务复用，避免每次全新挑战
        self.profile_dir = Path.home() / ".carousell-collector" / "chrome-profile"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.cf = CFSession(str(self.profile_dir), log=log, proxy=proxy)
        self.client: Optional[HttpClient] = None
        self.min_delay, self.max_delay = min_delay, max_delay
        self.img_workers = img_workers
        self.detail_concurrency = detail_concurrency

        # 运行统计
        self.stats = {"new": 0, "skipped": 0, "failed": 0, "images": 0}
        # 退出兜底：解释器退出时确保 Chrome 被回收（macOS/Linux SIGTERM/Ctrl+C 场景）
        import atexit
        atexit.register(self.kill_browser)

    # ---------------- lifecycle ----------------

    def stop(self):
        """请求停止：置停止标志并立刻回收浏览器进程。"""
        self.stop_event.set()
        self.kill_browser()

    def kill_browser(self):
        bs = getattr(self, "_browser", None)
        if bs is not None:
            try:
                bs.close()
            except Exception:
                pass

    def _stopped(self) -> bool:
        return self.stop_event.is_set()

    # ---------------- session ----------------

    def _load_cached_session(self) -> bool:
        """仅从缓存恢复会话（不启动浏览器）。"""
        cache = self.root / ".cf-session.json"
        if self.cf.load(str(cache)) and self.cf.cookies.get("cf_clearance"):
            self.client = HttpClient(self.cf, log=self.log, min_delay=self.min_delay,
                                     max_delay=self.max_delay, refresh_fn=self._reauth_once)
            return True
        return False

    def _reauth_once(self) -> bool:
        """CF 失效时重建 cookie：一次短会话，最多执行一次。"""
        if getattr(self, "_reauth_done", False):
            return False
        self._reauth_done = True
        from .browser import BrowserSession

        bs = BrowserSession(str(self.profile_dir), log=self.log, proxy=self.proxy)
        self._browser = bs
        try:
            if not bs.open_seller(BASE + "/"):
                return False
            ok = bs.export_cookies()
        finally:
            bs.close()
            self._browser = None
        if ok:
            self.cf.cookies = bs.cookies
            self.cf.user_agent = bs.user_agent or self.cf.user_agent
            self.cf.save(str(self.root / ".cf-session.json"))
            self.log("[会话] 已续期 cf_clearance")
        return ok

    def ensure_session(self, force: bool = False) -> bool:
        """兼容旧接口：先试缓存，失败才开一次浏览器。"""
        if not force and self._load_cached_session():
            try:
                if self.client.get(BASE + "/", timeout=20).status_code == 200:
                    return True
            except Exception:
                pass
        return self._reauth_once()

    # ---------------- collection ----------------

    def collect(self, urls: list[str]) -> dict:
        """主入口：采集多个链接（卖家主页 / 单商品均转入卖家全量）。"""
        t0 = time.time()
        self.stats = {"new": 0, "skipped": 0, "failed": 0, "images": 0}
        self.stop_event.clear()
        self.log("=" * 60)
        self.log(f"[开始] 共 {len(urls)} 个链接，根目录: {self.root}")
        self.log(f"[过滤] 类目跳过: {'开' if self.filters.enabled else '关'}，规则: {self.filters.categories()}")

        # ---- 1. 浏览器会话（只开一次）：过 CF + 枚举全部商品 + 导出 cookie ----
        from .browser import BrowserSession

        enum_sellers: dict[str, list[dict]] = {}
        bs = BrowserSession(str(self.profile_dir), log=self.log, proxy=self.proxy)
        self._browser = bs
        try:
            res = bs.collect_sellers(urls, max_items=self.max_items,
                                     log_progress=lambda c, t, m: self.progress(c, t, f"枚举 {m}"))
            enum_sellers = res.get("sellers", {})
            if bs.cookies:
                self.cf.cookies = bs.cookies
                self.cf.user_agent = bs.user_agent or self.cf.user_agent
                self.cf.save(str(self.root / ".cf-session.json"))
                self.log(f"[会话] 已用浏览器 cookie 更新会话（{len(bs.cookies)} 个）")
        except Exception as e:
            self.log(f"[浏览器] 枚举失败: {e}")
        finally:
            bs.close()
            self._browser = None

        # ---- 2. 建立 HTTP 会话（优先浏览器 cookie，其次缓存；不再另起浏览器） ----
        if self.cf.cookies:
            self.client = HttpClient(self.cf, log=self.log, min_delay=self.min_delay,
                                     max_delay=self.max_delay, refresh_fn=self._reauth_once)
        elif self._load_cached_session():
            self.log("[会话] 使用缓存的 cf_clearance")
        else:
            self.log("[错误] 未能取得 cf_clearance（浏览器未就绪），请确认 Chrome 可正常访问 Carousell 后重试")
            return self.stats

        # ---- 3. 逐卖家采集 ----
        pending_excel: list[dict] = []
        if enum_sellers:
            for i, (seller, items) in enumerate(enum_sellers.items(), 1):
                if self._stopped():
                    self.log("[中止] 用户停止")
                    break
                self.log("-" * 60)
                self.log(f"[卖家 {i}/{len(enum_sellers)}] {seller}（枚举到 {len(items)} 个商品）")
                try:
                    self._collect_seller_items(seller, items, pending_excel)
                except Exception as e:
                    self.log(f"[错误] {seller} 采集失败: {e}")
                    traceback.print_exc()
        else:
            # 兜底：浏览器不可用 → SSR 列表（每卖家首屏 20 个）
            self.log("[回退] 使用 SSR 列表模式（每卖家最多首屏商品）")
            for i, url in enumerate(urls, 1):
                if self._stopped():
                    break
                kind = parse_link_kind(url)
                if kind == "seller":
                    seller = parse_seller_username(url) or url
                elif kind == "listing":
                    lid = parse_listing_id_from_url(url)
                    if lid:
                        try:
                            self._collect_single(lid, url.split("?")[0], pending_excel)
                        except Exception as e:
                            self.log(f"[SKU] {lid} 采集失败: {e}")
                    continue
                else:
                    continue
                self.log("-" * 60)
                self.log(f"[卖家 {i}/{len(urls)}] {seller}（SSR 模式）")
                try:
                    self._collect_seller(seller, url, pending_excel)
                except Exception as e:
                    self.log(f"[错误] 链接采集失败: {e}")

        if pending_excel and not self._stopped():
            self.excel.append(pending_excel)
        self.log("=" * 60)
        c = self.state.counts()
        self.log(f"[汇总] 新增 {self.stats['new']} | 类目跳过 {self.stats['skipped']} | 失败 {self.stats['failed']}"
                 f" | 图片 {self.stats['images']} 张 | 耗时 {time.time()-t0:.0f}s")
        self.log(f"[状态] 历史累计: {c}")
        return self.stats

    def _folder_value(self, sku: str) -> str:
        """Folder 列：图片目录路径。

        native   → Windows: D:\\root\\SKU ；macOS: /root/SKU（系统原生分隔符）
        posix    → 统一正斜杠（跨平台导入 Excel / 其他系统时更安全）
        relative → 仅 SKU（相对根路径）
        """
        if self.path_style == "relative":
            return sku
        p = (self.root / sku)
        try:
            p = p.resolve()
        except Exception:
            p = p.absolute()
        return p.as_posix() if self.path_style == "posix" else str(p)

    def _collect_seller_items(self, seller: str, items: list[dict], pending_excel: list[dict]):
        """按枚举结果采集（items: [{id, path}]）。"""
        known = self.state.known_skus()
        todo = [it for it in items
                if it["id"] not in known or self.state.status(it["id"]) == "failed"]
        done_cnt = len(items) - len(todo)
        if done_cnt:
            self.log(f"[增量] {done_cnt} 个已完成（断点跳过），待采集 {len(todo)}")
        if not todo:
            self.log("[增量] 无新品")
            return
        total = len(todo)
        for idx, it in enumerate(todo, 1):
            if self._stopped():
                break
            sku = it["id"]
            url = it.get("path") or ""
            if url.startswith("/"):
                url = BASE + url
            self.progress(idx, total, sku)
            try:
                self._collect_sku(sku, {"listing_id": sku, "url": url, "title": "", "price": None},
                                  seller, pending_excel)
            except Exception as e:
                self.log(f"[SKU] {sku} 采集失败: {e}")
                self.state.record(sku, "failed", error=str(e)[:200])
                self.stats["failed"] += 1

    def _seller_from_listing(self, url: str) -> str:
        """从 /p/ 单商品链接解析卖家用户名（访问详情页提取）。"""
        try:
            path = url.split("?")[0]
            resp = self.client.get(path, timeout=40, referer=BASE + "/")
            seller = extract_seller_from_detail(resp.text)
            if seller:
                self.log(f"[解析] 单商品 {url.rsplit('/', 2)[-1][:40] if '/' in url else ''} → 卖家 {seller}")
            return seller
        except Exception as e:
            self.log(f"[解析] 卖家解析失败: {e}")
            return ""

    def _collect_single(self, lid: str, url: str, pending_excel: list[dict]):
        """兜底：单商品直接采集（卖家解析失败时）。"""
        if self.state.status(lid) == "done":
            self.log(f"[增量] {lid} 已完成，跳过")
            return
        self.progress(1, 1, lid)
        try:
            self._collect_sku(lid, {"listing_id": lid, "url": url, "title": "", "price": None}, "", pending_excel)
        except Exception as e:
            self.log(f"[SKU] {lid} 采集失败: {e}")
            self.state.record(lid, "failed", error=str(e)[:200])
            self.stats["failed"] += 1

    def _collect_seller(self, seller: str, url: str, pending_excel: list[dict]):
        # 1. 列表页
        self.log(f"[列表] 拉取 {seller} 商品列表…")
        resp = self.client.get(url, timeout=40, referer=BASE + "/")
        cards = parse_listing_cards(resp.text)
        if not cards:
            self.log(f"[列表] 未解析到商品卡（可能页面为壳或账号不存在），原始长度 {len(resp.text)}")
            return
        self.log(f"[列表] 共 {len(cards)} 个商品")

        # 2. 增量过滤
        known = self.state.known_skus()
        excel_known = self.excel.existing_skus()
        todo = [c for c in cards if c["listing_id"] not in known or self.state.status(c["listing_id"]) == "failed"]
        skipped_done = len(cards) - len(todo)
        if skipped_done:
            self.log(f"[增量] {skipped_done} 个已完成（断点跳过），待采集 {len(todo)}")

        total = len(todo)
        for idx, card in enumerate(todo, 1):
            if self._stopped():
                break
            sku = card["listing_id"]
            self.progress(idx, total, f"{sku} {card['title'][:30]}")
            try:
                self._collect_sku(sku, card, seller, pending_excel)
            except Exception as e:
                self.log(f"[SKU] {sku} 采集失败: {e}")
                self.state.record(sku, "failed", error=str(e)[:200])
                self.stats["failed"] += 1

    def _collect_sku(self, sku: str, card: dict, seller: str, pending_excel: list[dict]):
        # 详情页
        referer = BASE + f"/u/{seller}/" if seller else BASE + "/"
        resp = self.client.get(card["url"], timeout=40, referer=referer)
        detail = parse_detail(resp.text, url=card["url"])
        if not detail:
            raise RuntimeError("详情页无 ld+json 数据")

        title = detail["name"] or card["title"]
        category = detail["category"]
        price = detail["price"] or card["price"]
        # 卖家名兜底：详情页提取（单商品链接场景）
        if not seller:
            seller = detail.get("seller") or ""

        # 类目过滤
        if category and self.filters.should_skip(category):
            self.log(f"[跳过] {sku} 分类「{category}」命中过滤")
            self.state.record(sku, "skipped", category=category)
            self.stats["skipped"] += 1
            return

        # 图片下载
        images = detail["images"]
        if images:
            saved = download_sku_images(self.client, sku, images, str(self.root),
                                        log=self.log, workers=self.img_workers)
            self.stats["images"] += len(saved)
            if len(saved) < len(images):
                self.log(f"[SKU] {sku} 图片 {len(saved)}/{len(images)}（部分失败已重试）")

        # Excel 行
        folder = self._folder_value(sku)
        record = {
            "SKU": sku,
            "BrowserID": "",
            "ProductNameCn": title,
            "ProductNameEn": "",
            "GenderEn": infer_gender(title),
            "HKPrice": price if price is not None else "",
            "SGPrice": "",
            "MYPrice": "",
            "Brand": infer_brand(title),
            "Folder": folder,
            "ShopID": seller,
            "Desc": detail["description"],
        }
        pending_excel.append(record)
        self.state.record(sku, "done", title=title, category=category, images=len(images))
        self.stats["new"] += 1
        self.log(f"[完成] {sku} {title[:36]} | HK${price} | {category or '无分类'} | 图 {len(images)}")
