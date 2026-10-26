# -*- coding: utf-8 -*-
"""图片下载器：线程池并发直连 media CDN（无 CF）。支持立即停止。"""
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable

from .http_client import HttpClient, StopRequested


def download_sku_images(client: HttpClient, sku: str, images: list[str], dest_dir: str,
                        log: Callable[[str], None] = print, workers: int = 4) -> list[str]:
    """下载一个 SKU 的全部图片到 dest_dir，命名 {sku}_{idx:02d}.jpg。

    返回成功保存的文件名列表。停止请求时立即中断（已下载的保留）。
    """
    d = Path(dest_dir) / sku
    d.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    lock = threading.Lock()

    if not images:
        return saved

    futures = []
    with ThreadPoolExecutor(max_workers=min(workers, len(images))) as ex:
        for idx, url in enumerate(images, 1):
            futures.append(ex.submit(_dl_one, client, sku, idx, url, d, lock, saved, log))

        # 等待期间轮询停止：取消未开始的任务，已在跑的靠 StopRequested 自断
        try:
            while True:
                done, not_done = wait(futures, timeout=0.3)
                if not not_done:
                    break
                try:
                    client._check_stop()
                except StopRequested:
                    for f in not_done:
                        f.cancel()
                    raise
        except StopRequested:
            for f in futures:
                f.cancel()
            # 给已在途的请求一点时间自断（它们会抛 StopRequested）
            wait(futures, timeout=3)
            log(f"[下载] {sku} 已按停止请求中断（已完成 {len(saved)}/{len(images)}）")
            return saved
    return saved


def _dl_one(client: HttpClient, sku: str, idx: int, url: str, d: Path,
            lock: threading.Lock, saved: list, log: Callable[[str], None]):
    name = f"{sku}_{idx:02d}.jpg"
    target = d / name
    if target.exists() and target.stat().st_size > 0:
        with lock:
            saved.append(name)
        return name
    try:
        n = client.download(url, str(target))
        if n > 0:
            with lock:
                saved.append(name)
        else:
            log(f"[下载] 失败: {name} ← {url[:60]}…")
    except StopRequested:
        raise
    except Exception as e:
        log(f"[下载] 异常: {name}: {e}")
    return name
