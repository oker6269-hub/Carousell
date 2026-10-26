# -*- coding: utf-8 -*-
"""解析器：链接形态判定 + 浏览器取回的 listing 字段归一化。

详情数据不再由 Python 解析整页 HTML：浏览器在页面上下文里 fetch，
直接从内联状态 JSON 抽出 listingsMap，这里只做字段清洗。
"""
import re
from typing import Optional

BASE = "https://www.carousell.com.hk"

# 状态 JSON 里的类目是模板串，形如 `在 {{{行山及露營}}}`
_CATEGORY_TMPL = re.compile(r"\{\{\{\s*([^{}]+?)\s*\}\}\}")


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


def clean_category(raw: str) -> str:
    """把状态 JSON 的类目模板串归一成纯类目名。

    `在 {{{行山及露營}}}` → `行山及露營`；已是纯文本则原样返回。
    """
    raw = (raw or "").strip()
    m = _CATEGORY_TMPL.search(raw)
    if m:
        return m.group(1).strip()
    return re.sub(r"^在\s*", "", raw).strip()


def _to_price(raw) -> Optional[float]:
    try:
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def detail_from_listing(d: dict) -> dict:
    """浏览器取回的 listing 字段 → 采集器内部统一的 detail 结构。"""
    d = d or {}
    images = [u for u in (d.get("images") or []) if isinstance(u, str) and u.startswith("http")]
    return {
        "listing_id": (d.get("id") or "").strip() or None,
        "name": (d.get("title") or "").strip(),
        "price": _to_price(d.get("price")),
        "currency": (d.get("currency") or "HKD").strip() or "HKD",
        "description": (d.get("desc") or "").strip(),
        "images": images,
        "category": clean_category(d.get("category") or ""),
        "seller": (d.get("seller") or "").strip(),
    }
