# -*- coding: utf-8 -*-
"""GUI：Tkinter 面板 — 链接输入 / 根路径 / 类目过滤管理 / 实时日志 / 进度条。"""
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Callable

from .engine import CollectorEngine


class LogPanel:
    """线程安全日志面板（GUI 线程轮询队列渲染）。"""

    def __init__(self, widget: scrolledtext.ScrolledText, q: "queue.Queue[str]"):
        self.widget = widget
        self.q = q
        self.widget.configure(state="disabled")

    def log(self, msg: str):
        self.q.put(msg)

    def poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                self.widget.configure(state="normal")
                self.widget.insert("end", msg + "\n")
                self.widget.see("end")
                self.widget.configure(state="disabled")
        except queue.Empty:
            pass


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Carousell SKU 素材采集工具 v1.0")
        self.root.geometry("980x760")
        self.logq: "queue.Queue[str]" = queue.Queue()
        self.engine: CollectorEngine | None = None
        self.worker: threading.Thread | None = None
        self._build()
        self.root.after(150, self._poll_log)

    # ---------------- UI ----------------

    def _build(self):
        pad = {"padx": 8, "pady": 4}

        top = ttk.LabelFrame(self.root, text="① 采集配置")
        top.pack(fill="x", **pad)

        row1 = ttk.Frame(top)
        row1.pack(fill="x", padx=8, pady=4)
        ttk.Label(row1, text="素材保存根路径:").pack(side="left")
        self.var_root = tk.StringVar()
        ttk.Entry(row1, textvariable=self.var_root).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row1, text="浏览…", command=self._pick_root).pack(side="left")

        row2 = ttk.Frame(top)
        row2.pack(fill="x", padx=8, pady=4)
        ttk.Label(row2, text="卖家链接(每行一条):").pack(anchor="w")
        self.txt_urls = tk.Text(row2, height=5, wrap="char")
        self.txt_urls.pack(fill="x")

        row3 = ttk.Frame(top)
        row3.pack(fill="x", padx=8, pady=4)
        ttk.Label(row3, text="代理(可选, 如 socks5://127.0.0.1:1080):").pack(side="left")
        self.var_proxy = tk.StringVar()
        ttk.Entry(row3, textvariable=self.var_proxy, width=36).pack(side="left", padx=6)
        ttk.Label(row3, text="图片并发:").pack(side="left")
        self.var_workers = tk.StringVar(value="4")
        ttk.Spinbox(row3, from_=1, to=16, textvariable=self.var_workers, width=5).pack(side="left", padx=4)

        # 过滤管理
        filt = ttk.LabelFrame(self.root, text="② 类目跳过管理（持久化，保存于根路径 filters.json）")
        filt.pack(fill="x", **pad)
        frow = ttk.Frame(filt)
        frow.pack(fill="x", padx=8, pady=4)
        self.var_skip_on = tk.BooleanVar(value=True)
        ttk.Checkbutton(frow, text="启用类目跳过", variable=self.var_skip_on, command=self._toggle_skip).pack(side="left")
        ttk.Label(frow, text="新类目:").pack(side="left", padx=(16, 2))
        self.var_newcat = tk.StringVar()
        ttk.Entry(frow, textvariable=self.var_newcat, width=16).pack(side="left")
        ttk.Button(frow, text="添加", command=self._add_cat).pack(side="left", padx=4)
        self.lst_cats = tk.Listbox(filt, height=4)
        self.lst_cats.pack(fill="x", padx=8, pady=4)
        btnrow = ttk.Frame(filt)
        btnrow.pack(fill="x", padx=8, pady=2)
        ttk.Button(btnrow, text="开/关选中类目", command=self._toggle_cat).pack(side="left")
        ttk.Button(btnrow, text="删除选中类目", command=self._del_cat).pack(side="left", padx=6)
        self.lbl_cats = ttk.Label(filt, text="", foreground="#666")
        self.lbl_cats.pack(anchor="w", padx=8)

        # 运行控制
        run = ttk.LabelFrame(self.root, text="③ 运行")
        run.pack(fill="x", **pad)
        rrow = ttk.Frame(run)
        rrow.pack(fill="x", padx=8, pady=4)
        self.btn_start = ttk.Button(rrow, text="▶ 开始采集", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(rrow, text="■ 停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        self.progress = ttk.Progressbar(run, mode="determinate")
        self.progress.pack(fill="x", padx=8, pady=4)
        self.lbl_progress = ttk.Label(run, text="就绪")
        self.lbl_progress.pack(anchor="w", padx=8)

        # 日志
        logf = ttk.LabelFrame(self.root, text="④ 日志面板")
        logf.pack(fill="both", expand=True, **pad)
        self.txt_log = scrolledtext.ScrolledText(logf, height=16, state="disabled", font=("Consolas", 9))
        self.txt_log.pack(fill="both", expand=True, padx=8, pady=6)

    # ---------------- helpers ----------------

    def log(self, msg: str):
        self.logq.put(msg)

    def _poll_log(self):
        try:
            while True:
                msg = self.logq.get_nowait()
                self.txt_log.configure(state="normal")
                self.txt_log.insert("end", msg + "\n")
                self.txt_log.see("end")
                self.txt_log.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_log)

    def _pick_root(self):
        d = filedialog.askdirectory(title="选择素材保存根路径")
        if d:
            self.var_root.set(d)
            self._reload_filters()

    def _root(self) -> str:
        return self.var_root.get().strip()

    def _reload_filters(self):
        from .storage import FilterStore
        self.fstore = FilterStore(self._filters_path())
        self.var_skip_on.set(self.fstore.enabled)
        self._render_cats()

    def _filters_path(self) -> str:
        import os
        r = self._root() or os.getcwd()
        return str(r).rstrip("/\\") + "/filters.json"

    def _render_cats(self):
        self.lst_cats.delete(0, "end")
        cats = self.fstore.categories()
        for name, on in cats.items():
            mark = "✅ 开(跳过)" if on else "⛔ 关(采集)"
            self.lst_cats.insert("end", f"{mark}  {name}")
        self.lbl_cats.configure(text=f"共 {len(cats)} 条规则 | 状态保存于 filters.json")

    def _toggle_skip(self):
        try:
            self.fstore.set_enabled(self.var_skip_on.get())
            self._render_cats()
        except Exception:
            pass

    def _add_cat(self):
        name = self.var_newcat.get().strip()
        if not name:
            return
        self.fstore.add(name, True)
        self.var_newcat.set("")
        self._render_cats()

    def _toggle_cat(self):
        sel = self.lst_cats.curselection()
        if not sel:
            return
        text = self.lst_cats.get(sel[0])
        name = text.split("  ", 1)[-1]
        self.fstore.toggle(name)
        self._render_cats()

    def _del_cat(self):
        sel = self.lst_cats.curselection()
        if not sel:
            return
        text = self.lst_cats.get(sel[0])
        name = text.split("  ", 1)[-1]
        self.fstore.remove(name)
        self._render_cats()

    # ---------------- run ----------------

    def _start(self):
        root = self._root()
        urls = [l.strip() for l in self.txt_urls.get("1.0", "end").splitlines() if l.strip()]
        if not root:
            messagebox.showwarning("缺少配置", "请先选择素材保存根路径")
            return
        if not urls:
            messagebox.showwarning("缺少配置", "请至少输入一条卖家链接")
            return
        try:
            workers = max(1, int(self.var_workers.get()))
        except ValueError:
            workers = 4
        proxy = self.var_proxy.get().strip()

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.progress.configure(value=0, maximum=100)
        self.log("=" * 56)
        self.log(f"[启动] 根路径 {root} | 链接 {len(urls)} 条 | 图片并发 {workers}" + (f" | 代理 {proxy}" if proxy else " | 直连"))

        self.engine = CollectorEngine(root, log=self.log, progress=self._on_progress, proxy=proxy, img_workers=workers)
        # 同步过滤器到引擎
        self.engine.filters = self.fstore

        def work():
            try:
                self.engine.collect(urls)
            except Exception as e:
                self.log(f"[异常] {e}")
            finally:
                self.log("[结束] 采集线程退出")
                self.root.after(0, lambda: self.btn_start.configure(state="normal"))
                self.root.after(0, lambda: self.btn_stop.configure(state="disabled"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stop(self):
        if self.engine:
            self.engine.stop()
            self.log("[停止] 已请求中止（将在当前 SKU 完成后停止）")

    def _on_progress(self, cur: int, total: int, msg: str):
        def upd():
            if total > 0:
                self.progress.configure(maximum=total, value=cur)
                self.lbl_progress.configure(text=f"{cur}/{total}  {msg}")
        self.root.after(0, upd)

    def run(self):
        self._reload_filters()
        self.root.mainloop()
