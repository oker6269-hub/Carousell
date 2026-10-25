# -*- coding: utf-8 -*-
"""解析器：列表页商品卡 + 详情页 ld+json/分类。"""
import json
import re
from typing import Optional
from urllib.parse import urljoin

BASE = "https://www.carousell.com.hk"


def parse_seller_username(url: str) -> Optional[str]:
    """从任意 /u/ 链接提取用户名。"""
    m = re.search(r"/u/([A-Za-z0-9_.]+)/?", url)
    return m.group(1) if m else None


def parse_link_kind(url: str) -> str:
    """判定链接类型: 'seller'（/u/ 卖家主页）/ 'listing'（/p/ 单商品）/ 'unknown'。"""
    path = url.split("?")[0]
    if re.search(r"/u/[A-Za-z0-9_.]+/?$", path):
        return "seller"
    if re.search(r"/p/[^/]+", path):
        return "listing"
    return "unknown"


def parse_listing_id_from_url(url: str) -> Optional[str]:
    """从 /p/ 商品链接提取 listing ID（链接尾部 -数字）。"""
    path = url.split("?")[0]
    m = re.search(r"-(\d{6,})/?$", path.rstrip("/"))
    return m.group(1) if m else None


def extract_seller_from_detail(html: str) -> str:
    """从详情页 HTML 提取卖家用户名。

    优先级：图片 alt「由 xxx 提交」> JSON username 最高频者 > /u/ 链接最高频者。
    """
    m = re.search(r"由\s+([A-Za-z0-9_.]{2,40})\s+提交", html)
    if m:
        return m.group(1)
    from collections import Counter

    hits = re.findall(r'"username"\s*:\s*"([A-Za-z0-9_.]{2,40})"', html)
    if hits:
        return Counter(hits).most_common(1)[0][0]
    hits = re.findall(r"/u/([A-Za-z0-9_.]{2,40})", html)
    if hits:
        # 排除导航类
        hits = [h for h in hits if h.lower() not in ("categories", "login", "signup")]
        if hits:
            return Counter(hits).most_common(1)[0][0]
    return ""


def parse_listing_cards(html: str) -> list[dict]:
    """解析 profile SSR HTML 的商品卡。

    返回: [{listing_id, slug_url, title, price, thumb, condition}]
    """
    cards: list[dict] = []
    seen: set[str] = set()
    # 商品链接: /p/slug-数字/（链接带 ?t-id= 查询串，需先整段捕获再截断）
    for m in re.finditer(r'href="(/p/[^"]+)"', html):
        full = m.group(1)
        path = full.split("?")[0].split("#")[0]
        idm = re.search(r"-(\d{6,})/?$", path)
        if not idm:
            continue
        lid = idm.group(1)
        if lid in seen:
            continue
        seen.add(lid)
        # 卡片上下文：商品卡 DOM 中 href 前有图片 alt，后有标题/价格文本
        ctx = html[max(0, m.start() - 600): m.end() + 2500]
        title = _card_title(ctx)
        price = _card_price(ctx)
        thumb = _card_thumb(ctx)
        condition = _card_condition(ctx)
        cards.append({
            "listing_id": lid,
            "url": urljoin(BASE, path),
            "title": title,
            "price": price,
            "thumb": thumb,
            "condition": condition,
        })
    return cards


def _card_title(ctx: str) -> str:
    # alt= 在卡片图片上，最贴近商品标题
    m = re.search(r'alt="([^"]{4,150})"', ctx)
    if not m:
        m = re.search(r'title="([^"]{4,150})"', ctx)
    return (m.group(1).strip() if m else "").replace("&amp;", "&")


def _card_price(ctx: str) -> Optional[float]:
    m = re.search(r"HK\$\s*([\d,]+(?:\.\d+)?)", ctx)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _card_thumb(ctx: str) -> str:
    m = re.search(r'(https://media\.karousell\.com/media/photos/products/[^"\s\\]+)', ctx)
    return m.group(1) if m else ""


def _card_condition(ctx: str) -> str:
    for kw, label in (("全新", "全新"), ("二手", "二手"), ("new", "全新")):
        if kw in ctx:
            return label
    return ""


def parse_detail(html: str, url: str = "") -> Optional[dict]:
    """解析详情页：ld+json Product schema + 正文分类。"""
    ld = None
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            ld = data
            break
    if not ld:
        return None
    offers = ld.get("offers", {}) or {}
    price = None
    try:
        price = float(offers.get("price"))
    except (TypeError, ValueError):
        pass
    images = [u for u in (ld.get("image") or []) if isinstance(u, str) and u.startswith("http")]
    desc = (ld.get("description") or "").strip()
    name = (ld.get("name") or "").strip()
    # listing id 从 URL 提取
    lid = None
    idm = re.search(r"-(\d{6,})/?$", (offers.get("url") or url).rstrip("/"))
    if idm:
        lid = idm.group(1)
    # 成色
    condition = "全新" if "NewCondition" in str(offers.get("itemCondition", "")) else "二手"
    # 分类：详情页 SSR 文本（alt 属性「（分類：xxx）」优先）
    category = _detail_category(html)
    # 卖家：图片 alt 的「由 xxx 提交」标记（单商品链接场景兜底）
    seller = ""
    sm = re.search(r"由\s+([A-Za-z0-9_.]{2,40})\s+提交", html)
    if not sm:
        sm = re.search(r"submitted by\s+([A-Za-z0-9_.]{2,40})", html, re.I)
    if sm:
        seller = sm.group(1)
    return {
        "listing_id": lid,
        "name": name,
        "price": price,
        "currency": offers.get("priceCurrency", "HKD"),
        "condition": condition,
        "description": desc,
        "images": images,
        "category": category,
        "seller": seller,
    }


def _detail_category(html: str) -> str:
    """提取分类。优先图片 alt 的「（分類：xxx）位於」，fallback 正文「分类
在
xxx」。"""
    # 首选：详情页主图 alt 属性（curl_cffi HTML 稳定含此标记）
    m = re.search(r"[（(]分類[:：]\s*([^）)]{1,40})[）)]", html)
    if not m:
        m = re.search(r"[（(]分类[:：]\s*([^）)]{1,40})[）)]", html)
    if m:
        return m.group(1).strip()
    # 备用：正文区块（Chrome 渲染态）
    text = re.sub(r"<[^>]+>", "\n", html)
    for mm in re.finditer(r"分[类類]\s*\n+\s*在\s*\n+\s*([^\n<]{2,30})", text):
        cat = mm.group(1).strip()
        if cat:
            return cat
    return ""
