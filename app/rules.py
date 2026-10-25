# -*- coding: utf-8 -*-
"""字段推断规则：品牌词典 + 性别。"""
import re

# 常见品牌（长名优先匹配；覆盖 Carousell HK 常见品类：球鞋/服饰/箱包/数码/手表/打火机等）
BRANDS = [
    # 运动 / 球鞋
    "New Balance", "Onitsuka Tiger", "ASICS", "Sacai", "Nike", "Adidas", "Jordan",
    "Air Jordan", "Mizuno", "Converse", "Vans", "Puma", "Reebok", "Fila", "Salomon",
    "HOKA", "Hoka", "NewBalance", "Skechers", "Under Armour", "Lululemon", "Altra",
    "Veja", "Karhu", "Kangaroos", "Le Coq Sportif", "Wilson", "Yonex", "Li Ning",
    "Li-Ning", "Anta", "Xtep", "MLB", "Descente", "Kappa", "Umbro", "Diadora",
    # 潮流 / 服饰
    "Supreme", "Stussy", "Stüssy", "BAPE", "A Bathing Ape", "CDG", "Comme des Garcons",
    "Yeezy", "Off-White", "Palace", "Kith", "Fear of God", "Essentials", "Champion",
    "Carhartt", "Dickies", "Levi's", "Levis", "Uniqlo", "GU", "Zara", "H&M", "Muji",
    "The North Face", "Columbia", "Patagonia", "Arc'teryx", "Arc'teryx", "Montbell",
    "Snow Peak", "Helly Hansen", "Canada Goose", "Moncler", "Stone Island", "CP Company",
    # 奢侈 / 设计师
    "Louis Vuitton", "Gucci", "Prada", "Balenciaga", "Dior", "Chanel", "Hermes",
    "Hermès", "Loewe", "Celine", "Bottega Veneta", "Saint Laurent", "YSL", "Fendi",
    "Burberry", "Versace", "Givenchy", "Valentino", "Loro Piana", "Brunello Cucinelli",
    "Coach", "Michael Kors", "Kate Spade", "Tory Burch", "Longchamp", "Marc Jacobs",
    "Maison Margiela", "Miu Miu", "Alexander McQueen", "Rick Owens", "Issey Miyake",
    # 手表 / 配饰
    "Casio", "G-Shock", "Seiko", "Citizen", "Tissot", "Swatch", "Rolex", "Omega",
    "Ray-Ban", "RayBan", "Oakley", "Gentle Monster", "Tom Ford", "Lindberg",
    # 打火机 / 工具
    "Zippo", "Dupont", "S.T. Dupont", "Leatherman", "Victorinox", "Gerber",
    # 箱包 / 旅行
    "Rimowa", "Samsonite", "Tumi", "Herschel", "JanSport", "Eastpak", "Porter",
    "MCM", "Delsey", "American Tourister", "Briggs & Riley",
    # 数码 / 家电
    "Apple", "iPhone", "iPad", "MacBook", "AirPods", "Samsung", "Google", "Pixel",
    "Sony", "PlayStation", "Nintendo", "Switch", "Xbox", "Xiaomi", "Redmi", "Huawei",
    "OnePlus", "Oppo", "Vivo", "Anker", "Belkin", "JBL", "Bose", "Beats", "Marshall",
    "Sennheiser", "Audio-Technica", "AirPods", "Dyson", "Philips", "Panasonic",
    "Canon", "Nikon", "Fujifilm", "GoPro", "DJI", "Insta360", "Kindle", "Logitech",
    "Razer", "Corsair", "SteelSeries", "Keychron", "HHKB", "Realforce",
    # 玩具 / 收藏
    "LEGO", "Bandai", "Gundam", "Pokemon", "Pokémon", "Disney", "Marvel", "Hot Toys",
    "Funko", "Pop Mart", "Bearbrick", "Medicom",
]

# 品牌规范显示名
CANON = {
    "adidas": "Adidas", "ASICS": "ASICS", "Asics": "ASICS", "NB": "New Balance",
    "NewBalance": "New Balance", "Hoka": "HOKA", "Air Jordan": "Jordan",
    "LV": "Louis Vuitton", "RayBan": "Ray-Ban", "Hermès": "Hermes", "Stüssy": "Stussy",
    "iPhone": "Apple", "iPad": "Apple", "MacBook": "Apple", "AirPods": "Apple",
    "Pixel": "Google", "PlayStation": "Sony", "Switch": "Nintendo", "Xbox": "Microsoft",
    "Pokémon": "Pokemon", "G-Shock": "Casio", "Li-Ning": "Li Ning",
}

# 品类关键词：命中则说明该商品有性别属性（用于决定 GenderEn 是否推断）
GENDERED_HINTS = [
    "鞋", "波鞋", "球鞋", "跑鞋", "板鞋", "拖鞋", "靴", "涼鞋", "恤", "T恤", "外套",
    "風褸", "褲", "裙", "衛衣", "背心", "帽", "袋", "背包", "手袋", "銀包", "襪",
    "男款", "女款", "男女同款", "男裝", "女裝",
]


def infer_brand(title: str) -> str:
    t = title or ""
    # 长名优先（避免 NB 抢先于 New Balance）
    for b in sorted(BRANDS, key=len, reverse=True):
        if re.search(re.escape(b), t, re.I):
            return CANON.get(b, b)
    return ""


def infer_gender(title: str) -> str:
    """标题关键词推断（与模板样例口径一致：男女同款 → men）。"""
    t = title or ""
    has_w = "女" in t or re.search(r"\bwomen\b|\bwmns\b|\blady\b", t, re.I)
    has_m = "男" in t or re.search(r"\bmen\b|\bman\b", t, re.I)
    if has_m:
        return "men"
    if has_w:
        return "women"
    return ""
