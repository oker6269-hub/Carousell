# -*- coding: utf-8 -*-
"""GUI：现代化 Tkinter 面板 — 卡片式布局 / 状态统计 / 实时日志 / 类目过滤管理 / 自动更新。

视觉：等宽日志区 + 分组卡片 + 状态栏；浅色主题为主，强调色 #2563EB。
"""
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Optional

from .engine import CollectorEngine

ACCENT = "#2563EB"
BG = "#F5F6F8"
CARD = "#FFFFFF"
TEXT_DIM = "#6B7280"
OK = "#059669"
WARN = "#D97706"
ERR = "#DC2626"

MONO = ("Consolas", 9)
UI = ("Microsoft YaHei UI", 9)
UI_TITLE = ("Microsoft YaHei UI", 11, "bold")


def _card(parent, title: str) -> ttk.LabelFrame:
    f = ttk.LabelFrame(parent, text=f"  {title}  ", style="Card.TLabelframe")
    return f


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Carousell SKU 素材采集")
        self.root.geometry("1020x820")
        self.root.minsize(860, 640)
        self.root.configure(bg=BG)
        self.logq: "queue.Queue[str]" = queue.Queue()
        self.engine: Optional[CollectorEngine] = None
        self.worker: Optional[threading.Thread] = None
        self.sched = None
        self._stats_vars: dict[str, tk.StringVar] = {}
        self._build_style()
        self._build()
        self.root.after(150, self._poll_log)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- style ----------------

    def _build_style(self):
        s = ttk.Style(self.root)
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure(".", background=BG, font=UI, foreground="#111827")
        s.configure("Card.TLabelframe", background=CARD, borderwidth=1, relief="solid")
        s.configure("Card.TLabelframe.Label", background=CARD, foreground=TEXT_DIM, font=UI)
        s.configure("TButton", padding=(10, 5), font=UI)
        s.configure("Primary.TButton", background=ACCENT, foreground="#ffffff", padding=(16, 6))
        s.map("Primary.TButton", background=[("active", "#1D4ED8"), ("disabled", "#93C5FD")])
        s.configure("Danger.TButton", background=ERR, foreground="#ffffff")
        s.map("Danger.TButton", background=[("active", "#B91C1C")])
        s.configure("TEntry", padding=4)
        s.configure("TSpinbox", padding=2)
        s.configure("TCombobox", padding=3)
        s.configure("TProgressbar", thickness=8, background=ACCENT)

    # ---------------- ui ----------------

    def _build(self):
        pad = {"padx": 10, "pady": (4, 2)}

        # 标题栏
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(header, text="Carousell SKU 素材采集工具", font=UI_TITLE, bg=BG, fg="#111827").pack(side="left")
        self.var_browser = tk.StringVar(value="检测浏览器中…")
        tk.Label(header, textvariable=self.var_browser, font=UI, bg=BG, fg=TEXT_DIM).pack(side="right")

        # ① 配置卡
        cfg = _card(self.root, "① 采集配置")
        cfg.pack(fill="x", **pad)

        row1 = ttk.Frame(cfg)
        row1.pack(fill="x", padx=10, pady=6)
        ttk.Label(row1, text="保存根路径").pack(side="left")
        self.var_root = tk.StringVar()
        ttk.Entry(row1, textvariable=self.var_root).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(row1, text="浏览…", command=self._pick_root).pack(side="left")

        row2 = ttk.Frame(cfg)
        row2.pack(fill="x", padx=10, pady=(0, 6))
        ttk.Label(row2, text="链接（每行一条，/u/ 主页或 /p/ 商品均可）").pack(anchor="w")
        self.txt_urls = tk.Text(row2, height=4, wrap="char", font=MONO, relief="solid",
                                bd=1, bg="#FFFFFF", fg="#111827", insertbackground="#111827")
        self.txt_urls.pack(fill="x")

        row3 = ttk.Frame(cfg)
        row3.pack(fill="x", padx=10, pady=(0, 8))
        for (label, var, width, values) in (
            ("图片并发", None, None, None),
        ):
            pass
        ttk.Label(row3, text="图片并发").pack(side="left")
        self.var_workers = tk.StringVar(value="4")
        ttk.Spinbox(row3, from_=1, to=16, textvariable=self.var_workers, width=5).pack(side="left", padx=(2, 12))
        ttk.Label(row3, text="每卖家上限").pack(side="left")
        self.var_max_items = tk.StringVar(value="5000")
        ttk.Spinbox(row3, from_=20, to=20000, increment=100, textvariable=self.var_max_items,
                    width=7).pack(side="left", padx=(2, 12))
        ttk.Label(row3, text="路径风格").pack(side="left")
        self.var_path_style = tk.StringVar(value="native")
        ttk.Combobox(row3, textvariable=self.var_path_style, values=["native", "posix", "relative"],
                     width=9, state="readonly").pack(side="left", padx=(2, 12))
        ttk.Label(row3, text="代理(可选)").pack(side="left")
        self.var_proxy = tk.StringVar()
        ttk.Entry(row3, textvariable=self.var_proxy, width=26).pack(side="left", padx=2)

        # ② 运行卡（统计 + 控制 + 进度）
        run = _card(self.root, "② 运行")
        run.pack(fill="x", **pad)

        stats = ttk.Frame(run)
        stats.pack(fill="x", padx=10, pady=(6, 2))
        self._stat_label(stats, "新增", "new", OK)
        self._stat_label(stats, "跳过", "skipped", WARN)
        self._stat_label(stats, "失败", "failed", ERR)
        self._stat_label(stats, "图片", "images", ACCENT)

        ctrl = ttk.Frame(run)
        ctrl.pack(fill="x", padx=10, pady=4)
        self.btn_start = ttk.Button(ctrl, text="▶  开始采集", style="Primary.TButton", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(ctrl, text="■ 停止并回收", style="Danger.TButton",
                                   command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=8)

        # 自动更新
        au = ttk.Frame(run)
        au.pack(fill="x", padx=10, pady=(0, 4))
        self.var_auto_on = tk.BooleanVar(value=False)
        ttk.Checkbutton(au, text="自动更新", variable=self.var_auto_on,
                        command=self._toggle_auto).pack(side="left")
        ttk.Label(au, text="模式").pack(side="left", padx=(10, 2))
        self.var_auto_mode = tk.StringVar(value="interval")
        cmb = ttk.Combobox(au, textvariable=self.var_auto_mode, values=["interval", "daily"],
                           width=8, state="readonly")
        cmb.pack(side="left")
        cmb.bind("<<ComboboxSelected>>", lambda e: self._auto_mode_changed())
        ttk.Label(au, text="间隔(分)").pack(side="left", padx=(10, 2))
        self.var_auto_interval = tk.StringVar(value="60")
        ttk.Spinbox(au, from_=1, to=1440, textvariable=self.var_auto_interval, width=6).pack(side="left")
        ttk.Label(au, text="每日时间").pack(side="left", padx=(10, 2))
        self.var_auto_daily = tk.StringVar(value="09:00")
        ttk.Entry(au, textvariable=self.var_auto_daily, width=7).pack(side="left")
        ttk.Button(au, text="立即更新一次", command=self._trigger_now).pack(side="left", padx=12)
        self.lbl_sched = tk.Label(au, text="自动更新: 关", fg=TEXT_DIM, bg=CARD)
        self.lbl_sched.pack(side="left", padx=8)

        self.progress = ttk.Progressbar(run, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=(2, 4))
        self.lbl_progress = tk.Label(run, text="就绪", fg=TEXT_DIM, bg=CARD, anchor="w")
        self.lbl_progress.pack(fill="x", padx=10, pady=(0, 6))

        # ③ 类目过滤卡
        filt = _card(self.root, "③ 类目跳过（持久化 filters.json）")
        filt.pack(fill="x", **pad)
        frow = ttk.Frame(filt)
        frow.pack(fill="x", padx=10, pady=4)
        self.var_skip_on = tk.BooleanVar(value=True)
        ttk.Checkbutton(frow, text="启用类目跳过", variable=self.var_skip_on,
                        command=self._toggle_skip).pack(side="left")
        ttk.Label(frow, text="新类目").pack(side="left", padx=(14, 2))
        self.var_newcat = tk.StringVar()
        ttk.Entry(frow, textvariable=self.var_newcat, width=14).pack(side="left")
        ttk.Button(frow, text="添加", command=self._add_cat).pack(side="left", padx=4)
        ttk.Button(frow, text="开/关选中", command=self._toggle_cat).pack(side="left", padx=(12, 0))
        ttk.Button(frow, text="删除选中", command=self._del_cat).pack(side="left", padx=4)

        self.lst_cats = tk.Listbox(filt, height=3, font=UI, relief="solid", bd=1,
                                   bg="#FFFFFF", selectbackground="#DBEAFE")
        self.lst_cats.pack(fill="x", padx=10, pady=(2, 8))

        # ④ 日志卡
        logf = _card(self.root, "④ 日志面板")
        logf.pack(fill="both", expand=True, **pad)
        self.txt_log = scrolledtext.ScrolledText(logf, height=14, state="disabled", font=MONO,
                                                 bg="#0F172A", fg="#E2E8F0",
                                                 insertbackground="#E2E8F0", relief="flat",
                                                 selectbackground="#334155")
        self.txt_log.pack(fill="both", expand=True, padx=10, pady=8)
        self.txt_log.tag_config("dim", foreground="#94A3B8")
        self.txt_log.tag_config("ok", foreground="#6EE7B7")
        self.txt_log.tag_config("warn", foreground="#FCD34D")
        self.txt_log.tag_config("err", foreground="#FCA5A5")
        self.txt_log.tag_config("phase", foreground="#93C5FD")

        # 状态栏
        status = tk.Frame(self.root, bg="#E5E7EB")
        status.pack(fill="x", side="bottom")
        self.lbl_status = tk.Label(status, text="就绪", bg="#E5E7EB", fg=TEXT_DIM, font=UI, anchor="w")
        self.lbl_status.pack(fill="x", padx=12, pady=3)

        # 浏览器检测（后台线程）
        threading.Thread(target=self._detect_browser, daemon=True).start()

    def _stat_label(self, parent, label, key, color):
        box = ttk.Frame(parent)
        box.pack(side="left", padx=(0, 22))
        tk.Label(box, text=label, fg=TEXT_DIM, bg=CARD, font=UI).pack(anchor="w")
        v = tk.StringVar(value="0")
        self._stats_vars[key] = v
        tk.Label(box, textvariable=v, fg=color, bg=CARD,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")

    def _detect_browser(self):
        try:
            from .cf_session import find_browser, find_cloakbrowser
            cb = find_cloakbrowser()
            if cb:
                from pathlib import Path
                ver = Path(cb).parent.name.replace("chromium-", "")
                self.var_browser.set(f"浏览器内核：CloakBrowser v{ver}（指纹伪装）")
            else:
                found = find_browser()
                if found:
                    kind = {"chrome": "Chrome", "edge": "Edge"}.get(found[1], found[1])
                    self.var_browser.set(f"浏览器内核：{kind}（未检测到 CloakBrowser）")
                else:
                    self.var_browser.set("浏览器内核：未找到（请安装 Chrome 或 CloakBrowser）")
        except Exception:
            pass

    # ---------------- logging ----------------

    def log(self, msg: str):
        self.logq.put(msg)

    def _poll_log(self):
        try:
            while True:
                msg = self.logq.get_nowait()
                self.txt_log.configure(state="normal")
                tag = "dim"
                if msg.startswith("[完成]") or msg.startswith("[Excel]") or "✅" in msg:
                    tag = "ok"
                elif msg.startswith("[跳过]") or "[警告]" in msg or "[增量]" in msg:
                    tag = "warn"
                elif msg.startswith("[错误]") or "失败" in msg:
                    tag = "err"
                elif msg.startswith("=") or msg.startswith("---") or msg.startswith("["):
                    tag = "phase"
                self.txt_log.insert("end", msg + "\n", tag)
                self.txt_log.see("end")
                self.txt_log.configure(state="disabled")
                # 统计联动
                self._bump_stats_from_log(msg)
        except queue.Empty:
            pass
        self.root.after(150, self._poll_log)

    def _bump_stats_from_log(self, msg: str):
        try:
            if msg.startswith("[完成]"):
                self._stats_vars["new"].set(str(int(self._stats_vars["new"].get()) + 1))
            elif msg.startswith("[跳过]"):
                self._stats_vars["skipped"].set(str(int(self._stats_vars["skipped"].get()) + 1))
            elif msg.startswith("[SKU]") and "失败" in msg:
                self._stats_vars["failed"].set(str(int(self._stats_vars["failed"].get()) + 1))
            elif msg.startswith("[汇总]"):
                import re

                for key, pat in (("new", r"新增 (\d+)"), ("skipped", r"跳过 (\d+)"),
                                 ("failed", r"失败 (\d+)"), ("images", r"图片 (\d+)")):
                    m = re.search(pat, msg)
                    if m and key in self._stats_vars:
                        self._stats_vars[key].set(m.group(1))
        except Exception:
            pass

    def _reset_stats(self):
        for v in self._stats_vars.values():
            v.set("0")

    # ---------------- helpers ----------------

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
            mark = "✅ 跳过" if on else "⛔ 采集"
            self.lst_cats.insert("end", f"{mark}  {name}")

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
        name = self.lst_cats.get(sel[0]).split("  ", 1)[-1]
        self.fstore.toggle(name)
        self._render_cats()

    def _del_cat(self):
        sel = self.lst_cats.curselection()
        if not sel:
            return
        name = self.lst_cats.get(sel[0]).split("  ", 1)[-1]
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
            messagebox.showwarning("缺少配置", "请至少输入一条链接")
            return
        try:
            workers = max(1, int(self.var_workers.get()))
        except ValueError:
            workers = 4
        try:
            max_items = max(20, int(self.var_max_items.get()))
        except ValueError:
            max_items = 5000
        proxy = self.var_proxy.get().strip()

        self._reset_stats()
        self.log("=" * 56)
        self.log(f"[启动] 根路径 {root} | 链接 {len(urls)} 条 | 并发 {workers} | 上限 {max_items}"
                 + (f" | 代理 {proxy}" if proxy else " | 直连"))
        self.lbl_status.configure(text="采集中…")
        self._run_collect(urls, root, workers, proxy, max_items=max_items,
                          path_style=self.var_path_style.get())

    def _run_collect(self, urls, root, workers, proxy, quiet=False, max_items=5000, path_style="native"):
        if self.worker and self.worker.is_alive():
            self.log("[跳过] 采集正在进行中")
            return
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.progress.configure(value=0, maximum=100)
        self.engine = CollectorEngine(root, log=self.log, progress=self._on_progress, proxy=proxy,
                                      img_workers=workers, max_items=max_items, path_style=path_style)
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
                self.root.after(0, lambda: self.lbl_status.configure(text="就绪"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stop(self):
        if self.engine:
            self.engine.stop()
            self.log("[停止] 已请求中止并回收浏览器进程")
            self.lbl_status.configure(text="已停止")
        if getattr(self, "sched", None):
            self.sched.stop()
            self.lbl_sched.configure(text="自动更新: 关")

    def _on_progress(self, cur: int, total: int, msg: str):
        def upd():
            if total > 0:
                self.progress.configure(maximum=total, value=cur)
                self.lbl_progress.configure(text=f"{cur}/{total}  {msg}")
        self.root.after(0, upd)

    # ---------------- auto update ----------------

    def _toggle_auto(self):
        if self.var_auto_on.get():
            self._start_auto()
        else:
            if getattr(self, "sched", None):
                self.sched.stop()
            self.lbl_sched.configure(text="自动更新: 关")

    def _auto_mode_changed(self):
        if self.var_auto_on.get():
            self._start_auto()

    def _start_auto(self):
        from .scheduler import UpdateScheduler
        root = self._root()
        urls = [l.strip() for l in self.txt_urls.get("1.0", "end").splitlines() if l.strip()]
        if not root or not urls:
            messagebox.showwarning("缺少配置", "自动更新需先设置根路径与链接")
            self.var_auto_on.set(False)
            return
        try:
            workers = max(1, int(self.var_workers.get()))
        except ValueError:
            workers = 4
        try:
            max_items = max(20, int(self.var_max_items.get()))
        except ValueError:
            max_items = 5000
        proxy = self.var_proxy.get().strip()
        mode = self.var_auto_mode.get()
        try:
            interval = max(1, int(self.var_auto_interval.get()))
        except ValueError:
            interval = 60
        daily = self.var_auto_daily.get().strip() or "09:00"
        path_style = self.var_path_style.get()

        if getattr(self, "sched", None):
            self.sched.stop()

        def do_run():
            self._reset_stats()
            self._run_collect(urls, root, workers, proxy, max_items=max_items, path_style=path_style)

        self.sched = UpdateScheduler(do_run, log=self.log)
        self.sched.configure(mode, interval_min=interval, daily_hhmm=daily)
        self.sched.start()

        def refresh_label():
            while True:
                try:
                    nr = self.sched.next_run
                    text = f"自动更新: 开 | {mode} | 下次 {nr:%m-%d %H:%M}" if nr else f"自动更新: 开 | {mode}"
                    self.lbl_sched.configure(text=text)
                except Exception:
                    pass
                import time as _t
                _t.sleep(30)
        threading.Thread(target=refresh_label, daemon=True).start()

    def _trigger_now(self):
        if getattr(self, "sched", None) and self.sched._thread and self.sched._thread.is_alive():
            self.sched.trigger_now()
            self.log("[调度] 已请求立即更新")
        else:
            self._start()

    def _on_close(self):
        try:
            if getattr(self, "sched", None):
                self.sched.stop()
        except Exception:
            pass
        try:
            if self.engine:
                self.engine.stop()
                self.engine.kill_browser()
        except Exception:
            pass
        try:
            self.root.destroy()
        finally:
            import os
            os._exit(0)

    def run(self):
        self._reload_filters()
        self.root.mainloop()
