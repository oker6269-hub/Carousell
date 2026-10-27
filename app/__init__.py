# -*- coding: utf-8 -*-
"""Carousell Collector — SKU 素材批量采集工具。

单一客户端：枚举与详情都由同一个浏览器发出（页面内 fetch），
图片走 CDN 直连；不做 cf_clearance 跨客户端复用。
"""
__version__ = "1.4.1"
