# -*- coding: utf-8 -*-
"""图片下载器：线程池并发直连 media CDN（无 CF）。"""
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from .http_client import HttpClient


def download_sku_images(client: HttpClient, sku: str, images: list[str], dest_dir: str,
                        log: Callable[[str], None] = print, workers: int = 4) -> list[str]:
    """下载一个 SKU 的全部图片到 dest_dir，命名 {sku}_{idx:02d}.jpg。

    返回成功保存的文件名列表。
    """
    d = Path(dest_dir) / sku
    d.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    lock = threading.Lock()

    def _dl(idx_url):
        idx, url = idx_url
        name = f"{sku}_{idx:02d}.jpg"
        target = d / name
        if target.exists() and target.stat().st_size > 0:
            with lock:
                saved.append(name)
            return name
        n = client.download(url, str(target))
        if n > 0:
            with lock:
                saved.append(name)
        else:
            log(f"[下载] 失败: {name} ← {url[:60]}…")
        return name

    if not images:
        return saved
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_dl, enumerate(images, 1)))
    return saved
