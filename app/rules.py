# -*- coding: utf-8 -*-
"""字段推断规则：品牌词典 + 性别。"""
import re

BRANDS = [
    "New Balance", "Onitsuka Tiger", "Asics", "ASICS", "Sacai", "Nike", "Adidas", "adidas",
    "Jordan", "Air Jordan", "Mizuno", "Converse", "Vans", "Puma", "Reebok", "Fila",
    "Salomon", "Hoka", "HOKA", "NewBalance", "NB", "Balenciaga", "Gucci", "Prada",
    "Louis Vuitton", "LV", "Yeezy", "Crocs", "Dr. Martens", "Timberland", "UGG",
    "Veja", "Karhu", "Kangaroos", "Le Coq Sportif", "Wilson", "Yonex", "Li Ning",
    "Anta", "Xtep", "Pierre Cardin", "Skechers", "Under Armour", "Lululemon",
]


def infer_brand(title: str) -> str:
    t = title or ""
    # 长名优先（避免 NB 抢先于 New Balance）
    for b in sorted(BRANDS, key=len, reverse=True):
        if re.search(re.escape(b), t, re.I):
            # 规范显示
            canon = {"adidas": "Adidas", "ASICS": "ASICS", "Asics": "ASICS", "NB": "New Balance",
                     "NewBalance": "New Balance", "Hoka": "HOKA", "Air Jordan": "Jordan", "LV": "Louis Vuitton"}
            return canon.get(b, b)
    return ""


def infer_gender(title: str) -> str:
    """标题关键词推断（与模板样例口径一致：男女同款 → men）。"""
    t = title or ""
    has_w = "女" in t or "women" in t.lower() or "wmns" in t.lower()
    has_m = "男" in t or "men" in t.lower()
    if has_m:
        return "men"
    if has_w:
        return "women"
    return ""
