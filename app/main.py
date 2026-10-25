# -*- coding: utf-8 -*-
"""入口：GUI（默认）或 CLI 批量模式。

GUI:
    python -m app.main

CLI:
    python -m app.main --root D:/materials --urls url1 url2 ... [--proxy socks5://...] [--workers 4]
"""
import argparse
import io
import sys
from datetime import datetime
from pathlib import Path


def _fix_console_utf8():
    """Windows 控制台默认 GBK，强制 UTF-8 输出防乱码。"""
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
                sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
            except Exception:
                pass


def _setup_file_log(root: str):
    """把 engine 日志同步写入 logs/collector_日期.log。"""
    logs = Path(root) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / f"collector_{datetime.now():%Y%m%d}.log"
    fh = open(path, "a", encoding="utf-8")
    return fh, path


def run_cli(args) -> int:
    from .engine import CollectorEngine

    _fix_console_utf8()
    fh, logpath = _setup_file_log(args.root)

    def log(msg: str):
        line = f"{datetime.now():%H:%M:%S} {msg}"
        print(line, flush=True)
        fh.write(line + "\n")
        fh.flush()

    log(f"日志文件: {logpath}")
    engine = CollectorEngine(args.root, log=log, proxy=args.proxy, img_workers=args.workers)
    stats = engine.collect(args.urls)
    fh.close()
    return 0 if stats else 1


def main():
    parser = argparse.ArgumentParser(description="Carousell SKU 素材采集工具")
    parser.add_argument("--root", help="素材保存根路径")
    parser.add_argument("--urls", nargs="+", help="卖家链接（可多条）")
    parser.add_argument("--proxy", default="", help="可选代理 socks5://host:port")
    parser.add_argument("--workers", type=int, default=4, help="图片下载并发（默认 4）")
    parser.add_argument("--no-gui", action="store_true", help="CLI 模式（需提供 --root/--urls）")
    args = parser.parse_args()

    if args.no_gui or (args.root and args.urls):
        sys.exit(run_cli(args))

    # GUI 模式
    from .gui import App
    _fix_console_utf8()
    App().run()


if __name__ == "__main__":
    main()
