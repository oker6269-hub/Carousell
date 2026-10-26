# -*- coding: utf-8 -*-
"""采集引擎：多链接编排、断点增量、类目过滤、图片下载、Excel 记录、汇总。

单一客户端原则：
- 枚举与详情都由**同一个浏览器**发出（同 cookie / UA / TLS），不做跨客户端复用
- 只有图片走 CDN 直连（media.karousell.com 无 CF）
"""
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from .downloader import download_sku_images
from .http_client import ImageFetcher, StopRequested
from .parser import detail_from_listing, parse_link_kind, parse_listing_id_from_url
from .rules import infer_brand, infer_gender
from .storage import ExcelStore, FilterStore, StateStore
from .link_library import LinkLibrary

BASE = "https://www.carousell.com.hk"


class CollectorEngine:
    def __init__(self, root: str, log: Callable[[str], None] = print,
                 progress: Callable[[int, int, str], None] = lambda c, t, m: None,
                 proxy: str = "", min_delay: float = 0.6, max_delay: float = 1.6,
                 img_workers: int = 4, detail_concurrency: int = 4, max_items: int = 5000,
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
        # 浏览器 profile 固定放共享目录：指纹身份稳定，二次运行免重复过人机验证
        self.profile_dir = Path.home() / ".carousell-collector" / "chrome-profile"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        # 唯一的非浏览器出口：图片下载
        self.images = ImageFetcher(log=log, stop_fn=self._stopped)
        self._browser = None
        self.min_delay, self.max_delay = min_delay, max_delay
        self.img_workers = img_workers
        # 详情页并发（浏览器内 Promise.all 的批大小）
        self.detail_concurrency = max(1, int(detail_concurrency))

        # 运行统计
        self.stats = {"new": 0, "skipped": 0, "failed": 0, "images": 0}
        # 链接库：全局记录所有跑过的卖家（~/.carousell-collector/link_library.json）
        self.library = LinkLibrary()
        # 退出兜底：解释器退出时确保浏览器被回收
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

    def _sleep(self, seconds: float):
        """可中断睡眠：每 0.2s 检查一次停止标志。"""
        end = time.time() + max(0.0, seconds)
        while time.time() < end:
            if self._stopped():
                raise StopRequested("用户停止")
            time.sleep(min(0.2, max(0.0, end - time.time())))

    # ---------------- collection ----------------

    def collect(self, urls: list[str]) -> dict:
        """主入口：采集多个链接（卖家主页 / 单商品均转入卖家全量）。"""
        t0 = time.time()
        self.stats = {"new": 0, "skipped": 0, "failed": 0, "images": 0}
        self.stop_event.clear()
        self.log("=" * 60)
        self.log(f"[开始] 共 {len(urls)} 个链接，根目录: {self.root}")
        self.log(f"[过滤] 类目跳过: {'开' if self.filters.enabled else '关'}，规则: {self.filters.categories()}")

        from .browser import BrowserSession

        pending_excel: list[dict] = []
        per_seller_stats: dict[str, dict] = {}
        stopped_by_user = False
        enum_sellers: dict[str, list[dict]] = {}

        bs = BrowserSession(str(self.profile_dir), log=self.log, proxy=self.proxy,
                            stop_fn=self._stopped)
        self._browser = bs
        try:
            res = bs.collect_sellers(urls, max_items=self.max_items,
                                     log_progress=lambda c, t, m: self.progress(c, t, f"枚举 {m}"))
            enum_sellers = res.get("sellers", {})
        except Exception as e:
            self.log(f"[浏览器] 枚举失败: {e}")
            traceback.print_exc()
        try:
            if enum_sellers:
                for i, (seller, items) in enumerate(enum_sellers.items(), 1):
                    if self._stopped():
                        self.log("[中止] 用户停止")
                        stopped_by_user = True
                        break
                    self.log("-" * 60)
                    self.log(f"[卖家 {i}/{len(enum_sellers)}] {seller}（枚举到 {len(items)} 个商品）")
                    before = dict(self.stats)
                    try:
                        self._collect_seller_items(seller, items, pending_excel)
                    except StopRequested:
                        self.log("[中止] 用户停止（已完成部分已保存）")
                        stopped_by_user = True
                        per_seller_stats[seller] = self._delta(before)
                        self._record_library(seller, items, per_seller_stats[seller])
                        break
                    except Exception as e:
                        self.log(f"[错误] {seller} 采集失败: {e}")
                        traceback.print_exc()
                    per_seller_stats[seller] = self._delta(before)
                    self._record_library(seller, items, per_seller_stats[seller])
            else:
                # 兜底：枚举没结果时，单商品链接仍然可以只抓这一条
                if not self._stopped():
                    self.log("[回退] 枚举无结果，尝试按单商品链接直接采集")
                for url in urls:
                    if self._stopped():
                        stopped_by_user = True
                        break
                    if parse_link_kind(url) == "listing":
                        try:
                            self._collect_listing(url, pending_excel)
                        except StopRequested:
                            stopped_by_user = True
                            break
                        except Exception as e:
                            self.log(f"[错误] 单商品采集失败: {e}")
        finally:
            # 停止/结束时：把已完成的行落盘（增量安全）
            if pending_excel:
                try:
                    self.excel.append(pending_excel)
                except Exception as e:
                    self.log(f"[Excel] 落盘失败: {e}")
            bs.close()
            self._browser = None

        self.log("=" * 60)
        c = self.state.counts()
        tag = "（用户中止，已保存完成部分）" if stopped_by_user else ""
        self.log(f"[汇总] 新增 {self.stats['new']} | 类目跳过 {self.stats['skipped']} | 失败 {self.stats['failed']}"
                 f" | 图片 {self.stats['images']} 张 | 耗时 {time.time()-t0:.0f}s {tag}")
        self.log(f"[状态] 历史累计: {c}")
        return self.stats

    def _delta(self, before: dict) -> dict:
        return {k: self.stats[k] - before.get(k, 0) for k in self.stats}

    def _abs(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return BASE + (path if path.startswith("/") else "/" + path)

    def _collect_seller_items(self, seller: str, items: list[dict], pending_excel: list[dict]):
        """按枚举结果批量取详情（items: [{id, path}]）。"""
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
        batch = self.detail_concurrency
        for start in range(0, total, batch):
            if self._stopped():
                raise StopRequested("用户停止")
            chunk = todo[start:start + batch]
            self.progress(min(start + len(chunk), total), total, f"{chunk[0]['id']}…")
            urls = [self._abs(it.get("path") or f"/p/-{it['id']}/") for it in chunk]
            pages = self._browser.fetch_listings(urls, timeout=180) if self._browser else {}
            for it, url in zip(chunk, urls):
                if self._stopped():
                    raise StopRequested("用户停止")
                sku = it["id"]
                payload = pages.get(url)
                if not payload:
                    self.log(f"[SKU] {sku} 详情抓取失败")
                    self.state.record(sku, "failed", error="详情抓取失败")
                    self.stats["failed"] += 1
                    continue
                try:
                    self._save_sku(sku, detail_from_listing(payload), seller, pending_excel)
                except StopRequested:
                    raise
                except Exception as e:
                    self.log(f"[SKU] {sku} 保存失败: {e}")
                    self.state.record(sku, "failed", error=str(e)[:200])
                    self.stats["failed"] += 1
            if start + batch < total:
                self._sleep(self.min_delay)

    def _collect_listing(self, url: str, pending_excel: list[dict]):
        """单商品链接直接采集（枚举失败时的兜底）。"""
        lid = parse_listing_id_from_url(url) or ""
        if lid and self.state.status(lid) == "done":
            self.log(f"[增量] {lid} 已完成，跳过")
            return
        self.progress(1, 1, lid or url[:24])
        pages = self._browser.fetch_listings([url], timeout=120) if self._browser else {}
        payload = pages.get(url)
        if not payload:
            self.log(f"[SKU] {lid or url[:40]} 详情抓取失败")
            if lid:
                self.state.record(lid, "failed", error="详情抓取失败")
                self.stats["failed"] += 1
            return
        detail = detail_from_listing(payload)
        self._save_sku(detail.get("listing_id") or lid, detail, "", pending_excel)

    def _save_sku(self, sku: str, detail: dict, seller: str, pending_excel: list[dict]):
        if not sku:
            raise RuntimeError("缺少 listing id")
        title = detail["name"]
        category = detail["category"]
        price = detail["price"]
        if not seller:
            seller = detail.get("seller") or ""

        # 类目过滤
        if category and self.filters.should_skip(category):
            self.log(f"[跳过] {sku} 分类「{category}」命中过滤")
            self.state.record(sku, "skipped", category=category)
            self.stats["skipped"] += 1
            return

        # 图片下载（CDN 直连，无 CF）
        images = detail["images"]
        if images:
            saved = download_sku_images(self.images, sku, images, str(self.root),
                                        log=self.log, workers=self.img_workers)
            self.stats["images"] += len(saved)
            if len(saved) < len(images):
                self.log(f"[SKU] {sku} 图片 {len(saved)}/{len(images)}（部分失败已重试）")

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
            "Folder": self._folder_value(sku),
            "ShopID": seller,
            "Desc": detail["description"],
        }
        pending_excel.append(record)
        self.state.record(sku, "done", title=title, category=category, images=len(images))
        self.stats["new"] += 1
        self.log(f"[完成] {sku} {title[:36]} | HK${price} | {category or '无分类'} | 图 {len(images)}")

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

    def _record_library(self, seller: str, items: list, delta: dict):
        """把本次运行写入全局链接库。"""
        try:
            self.library.record(
                seller,
                url=f"{BASE}/u/{seller}/",
                delta=delta,
                total_items=len(items) if items else None,
            )
        except Exception as e:
            self.log(f"[链接库] 记录失败: {e}")
