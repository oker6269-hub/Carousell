# -*- coding: utf-8 -*-
"""GUI — shadcn/ui 设计语言（Tkinter 实现）

设计 token（对照 shadcn new-york-v4）：
- 黑白主色（zinc），radius 8px，细边框 #E4E4E7
- muted-foreground #71717A 次要文字
- destructive #DC2626 仅用于停止/删除
- 深色控制台（zinc-950 底 zinc-200 字）

布局：左侧链接库 + 右侧主操作区（笔记本双页签：采集 / 链接库）
"""
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Optional

from app.engine import CollectorEngine
from app.link_library import LinkLibrary

# ---------- shadcn tokens (zinc, light) ----------
C_BG = "#FFFFFF"          # background
C_FG = "#09090B"          # foreground
C_CARD = "#FFFFFF"
C_BORDER = "#E4E4E7"      # border
C_INPUT = "#E4E4E7"
C_RING = "#A1A1AA"
C_PRIMARY = "#18181B"     # zinc-900（主按钮黑）
C_PRIMARY_FG = "#FAFAFA"
C_SECONDARY = "#F4F4F5"   # secondary
C_MUTED = "#F4F4F5"
C_MUTED_FG = "#71717A"    # muted-foreground
C_ACCENT = "#F4F4F5"
C_DESTRUCTIVE = "#DC2626"
C_DESTRUCTIVE_FG = "#FAFAFA"
C_SUCCESS = "#16A34A"
C_WARN = "#D97706"
C_INFO = "#2563EB"
# 控制台（dark）
C_TERM_BG = "#09090B"
C_TERM_FG = "#E4E4E7"

FONT = ("Microsoft YaHei UI", 9)
FONT_SM = ("Microsoft YaHei UI", 8)
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 13, "bold")
FONT_STAT = ("Microsoft YaHei UI", 15, "bold")
MONO = ("Consolas", 9)


class Card(tk.Frame):
    """shadcn Card：白底、1px 细边框、8px 圆角（Tk 用 flat 模拟）、内边距 16。"""

    def __init__(self, parent, title: str = "", description: str = ""):
        super().__init__(parent, bg=C_CARD, highlightbackground=C_BORDER,
                         highlightthickness=1, bd=0)
        self._title = title
        if title:
            head = tk.Frame(self, bg=C_CARD)
            head.pack(fill="x", padx=16, pady=(12, 0))
            tk.Label(head, text=title, bg=C_CARD, fg=C_FG, font=FONT_BOLD).pack(anchor="w")
            if description:
                tk.Label(head, text=description, bg=C_CARD, fg=C_MUTED_FG,
                         font=FONT_SM).pack(anchor="w")
        self.body = tk.Frame(self, bg=C_CARD)
        self.body.pack(fill="both", expand=True, padx=16, pady=(8, 14))


def btn(parent, text, command, kind="default", **kw):
    """shadcn Button：default(黑)/secondary/destructive/outline/ghost。"""
    style = {
        "default": dict(bg=C_PRIMARY, fg=C_PRIMARY_FG, activebackground="#27272A"),
        "secondary": dict(bg=C_SECONDARY, fg=C_FG, activebackground="#E4E4E7"),
        "destructive": dict(bg=C_DESTRUCTIVE, fg=C_DESTRUCTIVE_FG, activebackground="#B91C1C"),
        "outline": dict(bg=C_CARD, fg=C_FG, activebackground=C_SECONDARY),
        "ghost": dict(bg=C_CARD, fg=C_FG, activebackground=C_SECONDARY),
    }[kind]
    b = tk.Label(parent, text=text, font=FONT, cursor="hand2",
                 padx=14, pady=5, **style)
    b.bind("<Button-1>", lambda e: command())
    b.configure(**{k: v for k, v in kw.items() if k in b.keys()})
    return b


def field_label(parent, text):
    return tk.Label(parent, text=text, bg=C_CARD, fg=C_MUTED_FG, font=FONT_SM)


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Carousell SKU 素材采集")
        self.root.geometry("1120x800")
        self.root.minsize(940, 660)
        self.root.configure(bg=C_BG)
        self.logq: "queue.Queue[str]" = queue.Queue()
        self.engine: Optional[CollectorEngine] = None
        self.worker: Optional[threading.Thread] = None
        self.sched = None
        self.library = LinkLibrary()
        self._stats_vars: dict[str, tk.StringVar] = {}
        self._build()
        self.root.after(150, self._poll_log)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # mac 上让绿色按钮可用（zoom）
        try:
            self.root.resizable(True, True)
        except Exception:
            pass

    # ---------------- layout ----------------

    def _build(self):
        # 顶栏
        top = tk.Frame(self.root, bg=C_BG)
        top.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(top, text="Carousell 采集", bg=C_BG, fg=C_FG, font=FONT_TITLE).pack(side="left")
        self.var_browser = tk.StringVar(value="检测浏览器中…")
        tk.Label(top, textvariable=self.var_browser, bg=C_BG, fg=C_MUTED_FG,
                 font=FONT_SM).pack(side="right")
        threading.Thread(target=self._detect_browser, daemon=True).start()

        # 页签（采集 / 链接库）
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TNotebook", background=C_BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=C_SECONDARY, foreground=C_FG,
                        padding=(16, 7), font=FONT)
        style.map("TNotebook.Tab", background=[("selected", C_CARD)],
                  foreground=[("selected", C_FG)])

        page_collect = tk.Frame(nb, bg=C_BG)
        page_library = tk.Frame(nb, bg=C_BG)
        nb.add(page_collect, text="  采集  ")
        nb.add(page_library, text=f"  链接库  ")

        self._build_collect(page_collect)
        self._build_library(page_library)

        # 状态栏
        status = tk.Frame(self.root, bg=C_BG, highlightbackground=C_BORDER,
                          highlightthickness=1)
        status.pack(fill="x", side="bottom")
        self.lbl_status = tk.Label(status, text="就绪", bg=C_BG, fg=C_MUTED_FG,
                                   font=FONT_SM, anchor="w")
        self.lbl_status.pack(fill="x", padx=16, pady=4)

    def _build_collect(self, parent):
        # 左：配置+运行；右：日志
        content = tk.Frame(parent, bg=C_BG)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=3)
        content.columnconfigure(1, weight=2)
        content.rowconfigure(0, weight=1)

        left = tk.Frame(content, bg=C_BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        # 卡1 配置
        c1 = Card(left, "采集配置", "链接可混合 /u/ 主页与 /p/ 商品")
        c1.pack(fill="x")
        row1 = tk.Frame(c1.body, bg=C_CARD)
        row1.pack(fill="x")
        field_label(row1, "保存根路径").pack(side="left")
        self.var_root = tk.StringVar()
        e = tk.Entry(row1, textvariable=self.var_root, font=FONT, relief="solid",
                     bd=1, bg=C_CARD, fg=C_FG, insertbackground=C_FG,
                     highlightthickness=1, highlightbackground=C_INPUT)
        e.pack(side="left", fill="x", expand=True, padx=(8, 8))
        btn(row1, "浏览", self._pick_root, "outline").pack(side="left")

        row2 = tk.Frame(c1.body, bg=C_CARD)
        row2.pack(fill="x", pady=(8, 0))
        field_label(row2, "链接（每行一条）").pack(anchor="w")
        self.txt_urls = tk.Text(row2, height=4, wrap="char", font=MONO,
                                relief="solid", bd=1, bg=C_CARD, fg=C_FG,
                                insertbackground=C_FG, highlightthickness=1,
                                highlightbackground=C_INPUT, pady=4)
        self.txt_urls.pack(fill="x")

        row3 = tk.Frame(c1.body, bg=C_CARD)
        row3.pack(fill="x", pady=(10, 0))
        for text, var, w, opts in (
            ("并发", "workers", 4, (1, 16, 1)),
            ("每卖家上限", "max_items", 5000, (20, 20000, 100)),
        ):
            field_label(row3, text).pack(side="left")
            sv = tk.StringVar(value=str(opts[2]))
            setattr(self, f"var_{var}", sv)
            ttk.Spinbox(row3, from_=opts[0], to=opts[1], increment=opts[2],
                        textvariable=sv, width=6, font=FONT,
                        style="TSpinbox").pack(side="left", padx=(4, 12))
        field_label(row3, "路径").pack(side="left")
        self.var_path_style = tk.StringVar(value="native")
        cb = ttk.Combobox(row3, textvariable=self.var_path_style,
                          values=["native", "posix", "relative"], width=8,
                          state="readonly", font=FONT)
        cb.pack(side="left", padx=(4, 12))
        field_label(row3, "代理").pack(side="left")
        self.var_proxy = tk.StringVar()
        tk.Entry(row3, textvariable=self.var_proxy, width=22, font=FONT, relief="solid",
                 bd=1, bg=C_CARD, fg=C_FG, insertbackground=C_FG,
                 highlightthickness=1, highlightbackground=C_INPUT).pack(side="left", padx=4)

        # 卡2 运行
        c2 = Card(left, "运行", "")
        c2.pack(fill="x", pady=(10, 0))
        stats = tk.Frame(c2.body, bg=C_CARD)
        stats.pack(fill="x")
        self._stat_chip(stats, "新增", "new", C_SUCCESS)
        self._stat_chip(stats, "跳过", "skipped", C_WARN)
        self._stat_chip(stats, "失败", "failed", C_DESTRUCTIVE)
        self._stat_chip(stats, "图片", "images", C_INFO)

        ctrl = tk.Frame(c2.body, bg=C_CARD)
        ctrl.pack(fill="x", pady=(10, 0))
        self.btn_start = btn(ctrl, "▶ 开始采集", self._start, "default")
        self.btn_start.pack(side="left")
        self.btn_stop = btn(ctrl, "■ 停止", self._stop, "destructive")
        self.btn_stop.pack(side="left", padx=8)

        au = tk.Frame(c2.body, bg=C_CARD)
        au.pack(fill="x", pady=(10, 0))
        self.var_auto_on = tk.BooleanVar(value=False)
        tk.Checkbutton(au, text="自动更新", variable=self.var_auto_on, command=self._toggle_auto,
                       bg=C_CARD, fg=C_FG, font=FONT, selectcolor=C_CARD,
                       activebackground=C_CARD, activeforeground=C_FG).pack(side="left")
        self.var_auto_mode = tk.StringVar(value="interval")
        acb = ttk.Combobox(au, textvariable=self.var_auto_mode,
                           values=["interval", "daily"], width=8, state="readonly", font=FONT)
        acb.pack(side="left", padx=(8, 2))
        acb.bind("<<ComboboxSelected>>", lambda e: self._auto_mode_changed())
        field_label(au, "间隔(分)").pack(side="left", padx=(8, 2))
        self.var_auto_interval = tk.StringVar(value="60")
        ttk.Spinbox(au, from_=1, to=1440, textvariable=self.var_auto_interval,
                    width=6, font=FONT).pack(side="left")
        field_label(au, "每日").pack(side="left", padx=(8, 2))
        self.var_auto_daily = tk.StringVar(value="09:00")
        tk.Entry(au, textvariable=self.var_auto_daily, width=7, font=FONT).pack(side="left")
        btn(au, "立即更新", self._trigger_now, "outline").pack(side="left", padx=12)
        self.lbl_sched = tk.Label(au, text="自动更新: 关", bg=C_CARD, fg=C_MUTED_FG, font=FONT_SM)
        self.lbl_sched.pack(side="left")

        self.progress = ttk.Progressbar(c2.body, mode="determinate", style="TProgressbar")
        self.progress.pack(fill="x", pady=(10, 4))
        self.lbl_progress = tk.Label(c2.body, text="就绪", bg=C_CARD, fg=C_MUTED_FG,
                                     font=FONT_SM, anchor="w")
        self.lbl_progress.pack(fill="x")

        # 卡3 类目过滤
        c3 = Card(left, "类目跳过", "持久化于根路径 filters.json")
        c3.pack(fill="both", expand=True, pady=(10, 0))
        frow = tk.Frame(c3.body, bg=C_CARD)
        frow.pack(fill="x")
        self.var_skip_on = tk.BooleanVar(value=True)
        tk.Checkbutton(frow, text="启用", variable=self.var_skip_on, command=self._toggle_skip,
                       bg=C_CARD, fg=C_FG, font=FONT, selectcolor=C_CARD,
                       activebackground=C_CARD, activeforeground=C_FG).pack(side="left")
        self.var_newcat = tk.StringVar()
        tk.Entry(frow, textvariable=self.var_newcat, width=12, font=FONT, relief="solid",
                 bd=1, bg=C_CARD, fg=C_FG, insertbackground=C_FG,
                 highlightthickness=1, highlightbackground=C_INPUT).pack(side="left", padx=8)
        btn(frow, "添加", self._add_cat, "outline").pack(side="left")
        btn(frow, "开/关", self._toggle_cat, "ghost").pack(side="left", padx=6)
        btn(frow, "删除", self._del_cat, "ghost").pack(side="left")
        self.lst_cats = tk.Listbox(c3.body, height=3, font=FONT, relief="flat",
                                   bg=C_MUTED, fg=C_FG, selectbackground="#D4D4D8",
                                   highlightthickness=0, activestyle="none")
        self.lst_cats.pack(fill="both", expand=True, pady=(8, 0))

        # 右：日志控制台
        right = Card(content, "日志")
        right.grid(row=0, column=1, sticky="nsew")
        self.txt_log = scrolledtext.ScrolledText(right.body, state="disabled", font=MONO,
                                                 bg=C_TERM_BG, fg=C_TERM_FG,
                                                 insertbackground=C_TERM_FG,
                                                 relief="flat", selectbackground="#27272A",
                                                 wrap="word")
        self.txt_log.pack(fill="both", expand=True)
        for tag, fg in (("ok", "#4ADE80"), ("warn", "#FACC15"), ("err", "#F87171"),
                        ("phase", "#93C5FD"), ("dim", "#71717A")):
            self.txt_log.tag_config(tag, foreground=fg)

    def _build_library(self, parent):
        """链接库页：所有跑过的卖家 + 统计 + 启停 + 一键重跑。"""
        wrap = tk.Frame(parent, bg=C_BG)
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        card = Card(wrap, "链接库", "所有运行过的卖家自动记录于此（全局共享 ~/.carousell-collector/link_library.json）")
        card.grid(row=0, column=0, sticky="nsew")

        # 工具行
        bar = tk.Frame(card.body, bg=C_CARD)
        bar.pack(fill="x")
        btn(bar, "↻ 刷新", self._render_library, "outline").pack(side="left")
        btn(bar, "＋ 从链接库全选采集", self._collect_all_enabled, "default").pack(side="left", padx=8)
        btn(bar, "开关选中", self._toggle_lib_entry, "secondary").pack(side="left")
        btn(bar, "删除选中", self._remove_lib_entry, "ghost").pack(side="left", padx=6)
        self.lbl_lib_count = tk.Label(bar, text="", bg=C_CARD, fg=C_MUTED_FG, font=FONT_SM)
        self.lbl_lib_count.pack(side="right")

        # 表格
        cols = ("seller", "items", "runs", "new", "imgs", "last_run", "on")
        headings = ("卖家", "商品数", "次数", "上次新增", "图片", "最近运行", "状态")
        widths = (170, 70, 60, 80, 70, 150, 60)
        self.tree = ttk.Treeview(card.body, columns=cols, show="headings", height=16)
        for c, h, w in zip(cols, headings, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w" if c in ("seller", "last_run") else "center")
        vsb = ttk.Scrollbar(card.body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        style = ttk.Style()
        style.configure("Treeview", font=FONT, rowheight=30, background=C_CARD,
                        foreground=C_FG, fieldbackground=C_CARD, borderwidth=0)
        style.configure("Treeview.Heading", font=FONT_SM, background=C_MUTED,
                        foreground=C_MUTED_FG, relief="flat")
        style.map("Treeview", background=[("selected", "#D4D4D8")])
        style.configure("Vertical.TScrollbar", background=C_BORDER, troughcolor=C_BG,
                        borderwidth=0, arrowsize=12)
        self.tree.pack(fill="both", expand=True, side="left", pady=(8, 0))
        vsb.pack(fill="y", side="right", pady=(8, 0))

        # 双击行 = 填入采集页
        self.tree.bind("<Double-1>", self._lib_row_dblclick)
        self._render_library()

    def _stat_chip(self, parent, label, key, color):
        chip = tk.Frame(parent, bg=C_MUTED, highlightthickness=0)
        chip.pack(side="left", padx=(0, 10))
        tk.Label(chip, text=label, bg=C_MUTED, fg=C_MUTED_FG, font=FONT_SM).pack(anchor="w", padx=8, pady=(5, 0))
        v = tk.StringVar(value="0")
        self._stats_vars[key] = v
        tk.Label(chip, textvariable=v, bg=C_MUTED, fg=color, font=FONT_STAT).pack(anchor="w", padx=8, pady=(0, 5))

    # ---------------- library ----------------

    def _render_library(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        entries = self.library.entries()
        for seller, e in entries.items():
            stats = e.get("last_stats", {})
            self.tree.insert("", "end", values=(
                seller,
                e.get("total_items", "—"),
                e.get("run_count", "—"),
                stats.get("new", "—"),
                stats.get("images", "—"),
                str(e.get("last_run", ""))[:16].replace("T", " "),
                "启用" if e.get("enabled", True) else "停用",
            ))
        self.lbl_lib_count.configure(text=f"{len(entries)} 个卖家")

    def _lib_sel(self) -> Optional[str]:
        sel = self.tree.selection()
        if not sel:
            return None
        return self.tree.item(sel[0])["values"][0]

    def _lib_row_dblclick(self, _e):
        seller = self._lib_sel()
        if seller:
            self.txt_urls.delete("1.0", "end")
            self.txt_urls.insert("1.0", f"https://www.carousell.com.hk/u/{seller}/\n")

    def _toggle_lib_entry(self):
        seller = self._lib_sel()
        if seller:
            cur = self.library.get(seller).get("enabled", True)
            self.library.set_enabled(seller, not cur)
            self._render_library()

    def _remove_lib_entry(self):
        seller = self._lib_sel()
        if seller and messagebox.askyesno("删除", f"从链接库删除 {seller}？"):
            self.library.remove(seller)
            self._render_library()

    def _collect_all_enabled(self):
        urls = self.library.enabled_urls()
        if not urls:
            messagebox.showinfo("链接库为空", "还没有运行过任何卖家")
            return
        self.txt_urls.delete("1.0", "end")
        self.txt_urls.insert("1.0", "\n".join(urls) + "\n")
        messagebox.showinfo("已填入", f"已将 {len(urls)} 个启用中的卖家填入采集页，点击「开始采集」执行")
        self._start()

    # ---------------- browser detect ----------------

    def _detect_browser(self):
        try:
            from app.cf_session import find_browser, find_cloakbrowser
            from pathlib import Path
            cb = find_cloakbrowser()
            if cb:
                ver = Path(cb).parent.name.replace("chromium-", "")
                self.var_browser.set(f"内核：CloakBrowser v{ver} · 指纹伪装")
            else:
                found = find_browser()
                if found:
                    kind = {"chrome": "Chrome", "edge": "Edge"}.get(found[1], found[1])
                    self.var_browser.set(f"内核：{kind}（建议安装 CloakBrowser）")
                else:
                    self.var_browser.set("未找到浏览器内核")
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
                if msg.startswith("[完成]") or msg.startswith("[Excel]") or msg.startswith("[增量]"):
                    tag = "ok"
                elif msg.startswith("[跳过]") or "[警告]" in msg:
                    tag = "warn"
                elif msg.startswith("[错误]") or "失败" in msg:
                    tag = "err"
                elif msg.startswith(("=", "---", "[启动", "[汇总", "[浏览器", "[枚举", "[会话")):
                    tag = "phase"
                self.txt_log.insert("end", msg + "\n", tag)
                self.txt_log.see("end")
                self.txt_log.configure(state="disabled")
                self._bump_stats(msg)
        except queue.Empty:
            pass
        self.root.after(150, self._poll_log)

    def _bump_stats(self, msg: str):
        try:
            import re
            if msg.startswith("[完成]"):
                self._stats_vars["new"].set(str(int(self._stats_vars["new"].get()) + 1))
            elif msg.startswith("[跳过]"):
                self._stats_vars["skipped"].set(str(int(self._stats_vars["skipped"].get()) + 1))
            elif msg.startswith("[SKU]") and "失败" in msg:
                self._stats_vars["failed"].set(str(int(self._stats_vars["failed"].get()) + 1))
            elif msg.startswith("[汇总]"):
                for key, pat in (("new", r"新增 (\d+)"), ("skipped", r"跳过 (\d+)"),
                                 ("failed", r"失败 (\d+)"), ("images", r"图片 (\d+)")):
                    m = re.search(pat, msg)
                    if m and key in self._stats_vars:
                        self._stats_vars[key].set(m.group(1))
                self._render_library()
        except Exception:
            pass

    def _reset_stats(self):
        for v in self._stats_vars.values():
            v.set("0")

    # ---------------- filters ----------------

    def _pick_root(self):
        d = filedialog.askdirectory(title="选择素材保存根路径")
        if d:
            self.var_root.set(d)
            self._reload_filters()

    def _filters_path(self) -> str:
        import os
        r = self.var_root.get().strip() or os.getcwd()
        return str(r).rstrip("/\\") + "/filters.json"

    def _reload_filters(self):
        from app.storage import FilterStore
        self.fstore = FilterStore(self._filters_path())
        self.var_skip_on.set(self.fstore.enabled)
        self._render_cats()

    def _render_cats(self):
        self.lst_cats.delete(0, "end")
        for name, on in self.fstore.categories().items():
            self.lst_cats.insert("end", f"{'✅ 跳过' if on else '⛔ 采集'}  {name}")

    def _toggle_skip(self):
        try:
            self.fstore.set_enabled(self.var_skip_on.get())
            self._render_cats()
        except Exception:
            pass

    def _add_cat(self):
        name = self.var_newcat.get().strip()
        if name:
            self.fstore.add(name, True)
            self.var_newcat.set("")
            self._render_cats()

    def _toggle_cat(self):
        sel = self.lst_cats.curselection()
        if sel:
            self.fstore.toggle(self.lst_cats.get(sel[0]).split("  ", 1)[-1])
            self._render_cats()

    def _del_cat(self):
        sel = self.lst_cats.curselection()
        if sel:
            self.fstore.remove(self.lst_cats.get(sel[0]).split("  ", 1)[-1])
            self._render_cats()

    # ---------------- run ----------------

    def _start(self):
        root = self.var_root.get().strip()
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
        self.lbl_status.configure(text="采集中…", fg=C_INFO)
        self._run_collect(urls, root, workers, proxy, max_items=max_items,
                          path_style=self.var_path_style.get())

    def _run_collect(self, urls, root, workers, proxy, quiet=False,
                     max_items=5000, path_style="native"):
        if self.worker and self.worker.is_alive():
            self.log("[跳过] 采集正在进行中")
            return
        self.btn_start.configure(bg="#A1A1AA")
        self.btn_stop.configure(state="normal")
        self.progress.configure(value=0, maximum=100)
        self.engine = CollectorEngine(root, log=self.log, progress=self._on_progress,
                                       proxy=proxy, img_workers=workers,
                                       max_items=max_items, path_style=path_style)
        self.engine.filters = self.fstore

        def work():
            try:
                self.engine.collect(urls)
            except Exception as e:
                self.log(f"[异常] {e}")
            finally:
                self.log("[结束] 采集线程退出")
                self.root.after(0, lambda: self.btn_start.configure(bg=C_PRIMARY))
                self.root.after(0, lambda: self.lbl_status.configure(text="就绪", fg=C_MUTED_FG))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stop(self):
        if self.engine:
            self.engine.stop()
            self.log("[停止] 已请求中止并回收浏览器进程")
            self.lbl_status.configure(text="已停止", fg=C_DESTRUCTIVE)
        if getattr(self, "sched", None):
            self.sched.stop()
            self.lbl_sched.configure(text="自动更新: 关")

    def _on_progress(self, cur, total, msg):
        def upd():
            if total > 0:
                self.progress.configure(maximum=total, value=cur)
                self.lbl_progress.configure(text=f"{cur}/{total}  {msg}")
        self.root.after(0, upd)

    # ---------------- auto ----------------

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
        from app.scheduler import UpdateScheduler
        root = self.var_root.get().strip()
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
            self._run_collect(urls, root, workers, proxy, max_items=max_items,
                              path_style=path_style)

        self.sched = UpdateScheduler(do_run, log=self.log)
        self.sched.configure(mode, interval_min=interval, daily_hhmm=daily)
        self.sched.start()

        def refresh():
            while True:
                try:
                    nr = self.sched.next_run
                    self.lbl_sched.configure(
                        text=f"自动更新: 开 | {mode} | 下次 {nr:%m-%d %H:%M}" if nr
                        else f"自动更新: 开 | {mode}")
                except Exception:
                    pass
                import time as _t
                _t.sleep(30)
        threading.Thread(target=refresh, daemon=True).start()

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
