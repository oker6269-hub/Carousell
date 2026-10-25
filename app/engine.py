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
from .parser import parse_detail, parse_listing_cards, parse_seller_username, parse_link_kind, parse_listing_id_from_url
from .rules import infer_brand, infer_gender
from .storage import ExcelStore, FilterStore, StateStore

BASE = "https://www.carousell.com.hk"


class CollectorEngine:
    def __init__(self, root: str, log: Callable[[str], None] = print,
                 progress: Callable[[int, int, str], None] = lambda c, t, m: None,
                 proxy: str = "", min_delay: float = 0.6, max_delay: float = 1.6,
                 img_workers: int = 4, detail_concurrency: int = 2):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "logs").mkdir(exist_ok=True)
        self.log = log
        self.progress = progress
        self.stop_event = threading.Event()

        self.state = StateStore(str(self.root / "state.json"))
        self.filters = FilterStore(str(self.root / "filters.json"))
        self.excel = ExcelStore(str(self.root), log=log)
        self.cf = CFSession(str(self.root / ".cf-profile"), log=log, proxy=proxy)
        self.client: Optional[HttpClient] = None
        self.min_delay, self.max_delay = min_delay, max_delay
        self.img_workers = img_workers
        self.detail_concurrency = detail_concurrency

        # 运行统计
        self.stats = {"new": 0, "skipped": 0, "failed": 0, "images": 0}

    # ---------------- lifecycle ----------------

    def stop(self):
        self.stop_event.set()

    def _stopped(self) -> bool:
        return self.stop_event.is_set()

    # ---------------- session ----------------

    def ensure_session(self, force: bool = False) -> bool:
        """获取/续期 CF 会话并构建 HTTP 客户端。"""
        cookie_cache = self.root / ".cf-session.json"
        if not force and self.cf.load(str(cookie_cache)):
            self.log("[会话] 复用缓存 cf_clearance（失效将自动重过）")
            self.client = HttpClient(self.cf, log=self.log, min_delay=self.min_delay, max_delay=self.max_delay)
            # 快速探活
            try:
                resp = self.client.get(BASE + "/", timeout=20)
                if resp.status_code == 200:
                    self.log("[会话] 缓存 cookie 有效")
                    return True
            except Exception:
                self.log("[会话] 缓存 cookie 探活失败，重新突破")
        ok = self.cf.obtain(BASE + "/")
        if not ok:
            return False
        self.cf.save(str(cookie_cache))
        self.client = HttpClient(self.cf, log=self.log, min_delay=self.min_delay, max_delay=self.max_delay)
        return True

    # ---------------- collection ----------------

    def collect(self, urls: list[str]) -> dict:
        """主入口：采集多个卖家链接。"""
        t0 = time.time()
        self.log("=" * 60)
        self.log(f"[开始] 共 {len(urls)} 个链接，根目录: {self.root}")
        self.log(f"[过滤] 类目跳过: {'开' if self.filters.enabled else '关'}，规则: {self.filters.categories()}")

        if not self.ensure_session():
            self.log("[错误] CF 会话建立失败，退出")
            return self.stats

        pending_excel: list[dict] = []
        for i, url in enumerate(urls, 1):
            if self._stopped():
                self.log("[中止] 用户停止")
                break
            kind = parse_link_kind(url)
            self.log("-" * 60)
            try:
                if kind == "seller":
                    seller = parse_seller_username(url) or url
                    self.log(f"[链接 {i}/{len(urls)}] 卖家主页: {seller}")
                    self._collect_seller(seller, url, pending_excel)
                elif kind == "listing":
                    lid = parse_listing_id_from_url(url)
                    self.log(f"[链接 {i}/{len(urls)}] 单商品链接: {lid or url[:60]}")
                    if not lid:
                        self.log("[错误] 无法从链接提取商品 ID")
                        continue
                    # 断点去重（与主页采集共用 state）
                    if self.state.status(lid) == "done":
                        self.log(f"[增量] {lid} 已完成，跳过")
                        continue
                    self.progress(1, 1, lid)
                    try:
                        self._collect_sku(lid, {"listing_id": lid, "url": url.split("?")[0],
                                                 "title": "", "price": None}, "", pending_excel)
                    except Exception as e:
                        self.log(f"[SKU] {lid} 采集失败: {e}")
                        self.state.record(lid, "failed", error=str(e)[:200])
                        self.stats["failed"] += 1
                else:
                    self.log(f"[链接 {i}/{len(urls)}] 不支持的链接格式（需 /u/ 主页或 /p/ 商品）: {url[:60]}")
            except Exception as e:
                self.log(f"[错误] 链接采集失败: {e}")
                traceback.print_exc()

        if pending_excel and not self._stopped():
            self.excel.append(pending_excel)
        self.log("=" * 60)
        c = self.state.counts()
        self.log(f"[汇总] 新增 {self.stats['new']} | 类目跳过 {self.stats['skipped']} | 失败 {self.stats['failed']}"
                 f" | 图片 {self.stats['images']} 张 | 耗时 {time.time()-t0:.0f}s")
        self.log(f"[状态] 历史累计: {c}")
        return self.stats

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
        folder = str(Path(sku))
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
