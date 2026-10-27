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
import time
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


def _install_signal_handlers():
    """SIGTERM / SIGHUP（macOS 关终端窗口、kill 命令）→ 走正常退出流程，触发 atexit 回收 Chrome。"""
    import signal

    def _handler(signum, frame):
        raise SystemExit(0)

    for name in ("SIGTERM", "SIGHUP", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handler)
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
    from .scheduler import UpdateScheduler

    _fix_console_utf8()
    _install_signal_handlers()
    fh, logpath = _setup_file_log(args.root)

    def log(msg: str):
        line = f"{datetime.now():%H:%M:%S} {msg}"
        print(line, flush=True)
        fh.write(line + "\n")
        fh.flush()

    log(f"日志文件: {logpath}")
    _holder: dict = {}

    def run_once() -> dict:
        engine = CollectorEngine(args.root, log=log, proxy=args.proxy, img_workers=args.workers,
                                 max_items=getattr(args, "max_items", 5000),
                                 detail_concurrency=getattr(args, "detail_concurrency", 2),
                                 detail_delay=getattr(args, "detail_delay", 0.8),
                                 path_style=getattr(args, "path_style", "native"))
        _holder["engine"] = engine
        try:
            return engine.collect(args.urls)
        finally:
            engine.kill_browser()

    sched = None
    try:
        if args.watch_daily or args.watch:
            # 定时模式：首次全量 + 之后增量
            mode = "daily" if args.watch_daily else "interval"
            sched = UpdateScheduler(run_once, log=log)
            sched.configure(mode, interval_min=args.watch or 60, daily_hhmm=args.watch_daily or "09:00")
            log(f"[定时] 模式={mode} 间隔={args.watch}分钟 每日={args.watch_daily or '-'}")
            run_once()  # 启动立即跑一次
            sched.start()
            while True:
                time.sleep(60)
        stats = run_once()
        return 0 if stats else 1
    except KeyboardInterrupt:
        log("[退出] Ctrl+C，正在回收浏览器进程…")
        eng = _holder.get("engine")
        if eng:
            try:
                eng.stop()
            except Exception:
                pass
        return 130
    finally:
        if sched:
            sched.stop()
        fh.close()


def main():
    parser = argparse.ArgumentParser(description="Carousell SKU 素材采集工具")
    parser.add_argument("--root", help="素材保存根路径")
    parser.add_argument("--urls", nargs="+", help="卖家链接（可多条）")
    parser.add_argument("--proxy", default="", help="可选代理 socks5://host:port")
    parser.add_argument("--workers", type=int, default=4, help="图片下载并发（默认 4）")
    parser.add_argument("--detail-concurrency", type=int, default=2, dest="detail_concurrency",
                        metavar="N", help="详情页批量并发（默认 2；太高容易被站点限流）")
    parser.add_argument("--detail-delay", type=float, default=0.8, dest="detail_delay",
                        metavar="SEC", help="详情批次间隔秒数（默认 0.8）")
    parser.add_argument("--max-items", type=int, default=5000, dest="max_items",
                        help="每个卖家最多枚举商品数（默认 5000）")
    parser.add_argument("--path-style", default="native", choices=["native", "posix", "relative"],
                        dest="path_style",
                        help="Excel Folder 列路径风格：native=系统绝对路径(默认) / posix=正斜杠绝对路径 / relative=仅SKU")
    parser.add_argument("--watch", type=int, default=0, metavar="MINUTES",
                        help="定时自动更新：每 N 分钟增量拉取新品（0=关闭）")
    parser.add_argument("--watch-daily", default="", metavar="HH:MM",
                        help="定时自动更新：每天固定时间运行（优先于 --watch）")
    parser.add_argument("--no-gui", action="store_true", help="CLI 模式（需提供 --root/--urls）")
    args = parser.parse_args()

    if args.no_gui or (args.root and args.urls):
        sys.exit(run_cli(args))

    # GUI 模式
    from .gui import App
    _fix_console_utf8()
    _install_signal_handlers()
    App().run()


if __name__ == "__main__":
    main()
