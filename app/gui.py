# -*- coding: utf-8 -*-
"""GUI — 深色科技风（Tkinter 实现）

设计依据（ui-ux-pro-max）：
- 风格：Dark Mode / HUD-terminal（深空底 + 青色高光 + 等宽数字）
- 排版：代码/工具类气质，正文 13pt 起（不堆小字），日志 12pt 等宽
- 可访问性：正文对比度 ≥ 4.5:1、输入框聚焦有可见高光、按钮 hover 反馈
- 不用 emoji 当图标：状态用几何符号（● ○ ■ ▶）与文字标签

布局：顶栏（品牌 + 内核状态）→ 页签（采集 / 链接库）→ 状态栏
功能与旧版完全一致，只重做视觉层。
"""
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from app import __version__
from app.engine import CollectorEngine
from app.link_library import LinkLibrary
from app.proxy_pool import STRATEGIES, ProxyPool, mask_proxy
from app.title_rules import TitleRules

# ---------- 设计 token（深色科技风） ----------
C_BG = "#0A101C"          # 窗口底
C_SURFACE = "#0E1828"     # 顶栏/状态栏
C_CARD = "#121C30"        # 卡片
C_CARD_HI = "#16233B"     # 卡片悬停 / 次级面
C_MUTED_BG = "#16223A"    # 输入框/统计块底
C_BORDER = "#22314C"      # 1px 描边
C_BORDER_HI = "#2E4265"   # 悬停描边
C_FG = "#E6EEF9"          # 主文字
C_FG_DIM = "#93A7C4"      # 次要文字（对卡片 ≈ 6:1）
C_FG_FAINT = "#6B7F9B"    # 更弱（装饰/禁用）
C_ACCENT = "#22D3EE"      # 科技青（主操作/聚焦）
C_ACCENT_HI = "#67E8F9"
C_ACCENT_DIM = "#0E3A4A"  # 选中底
C_ON_ACCENT = "#04202A"   # 青底上的文字
C_SUCCESS = "#34D399"
C_WARN = "#FBBF24"
C_DANGER = "#F87171"
C_DANGER_BG = "#3A1620"
C_INFO = "#60A5FA"
C_TERM_BG = "#060B14"     # 控制台
C_TERM_FG = "#C9D7EA"

# 字体族栈（按本机可用性解析；正文尺寸刻意偏大）
UI_STACK = ["Helvetica Neue", "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Arial"]
MONO_STACK = ["Menlo", "SF Mono", "Consolas", "DejaVu Sans Mono", "Courier New"]

FONT = ("Helvetica Neue", 13)
FONT_SM = ("Helvetica Neue", 12)
FONT_LABEL = ("Helvetica Neue", 12)
FONT_BOLD = ("Helvetica Neue", 13, "bold")
FONT_H2 = ("Helvetica Neue", 15, "bold")
FONT_TITLE = ("Helvetica Neue", 22, "bold")
FONT_STAT = ("Menlo", 24, "bold")
FONT_MONO = ("Menlo", 12)
FONT_MONO_SM = ("Menlo", 11)
FONT_TABS = ("Helvetica Neue", 14, "bold")


def _pick(root: tk.Misc, stack: list, size: int, weight: str = "normal"):
    try:
        avail = set(tkfont.families(root))
    except Exception:
        avail = set()
    for fam in stack:
        if fam in avail:
            return (fam, size, weight)
    return (stack[-1], size, weight)


def init_fonts(root: tk.Misc):
    """按本机实际可用字体解析一次，全局生效。"""
    global FONT, FONT_SM, FONT_LABEL, FONT_BOLD, FONT_H2, FONT_TITLE
    global FONT_STAT, FONT_MONO, FONT_MONO_SM, FONT_TABS
    FONT = _pick(root, UI_STACK, 13)
    FONT_SM = _pick(root, UI_STACK, 12)
    FONT_LABEL = _pick(root, UI_STACK, 12)
    FONT_BOLD = _pick(root, UI_STACK, 13, "bold")
    FONT_H2 = _pick(root, UI_STACK, 15, "bold")
    FONT_TITLE = _pick(root, UI_STACK, 22, "bold")
    FONT_STAT = _pick(root, MONO_STACK, 24, "bold")
    FONT_MONO = _pick(root, MONO_STACK, 12)
    FONT_MONO_SM = _pick(root, MONO_STACK, 11)
    FONT_TABS = _pick(root, UI_STACK, 14, "bold")


# ---------- 基础控件 ----------


class Card(tk.Frame):
    """深色卡片：1px 描边 + 标题前的青色标记条。"""

    def __init__(self, parent, title: str = "", description: str = "", accent: str = C_ACCENT):
        super().__init__(parent, bg=C_CARD, highlightbackground=C_BORDER,
                         highlightthickness=1, bd=0)
        if title:
            head = tk.Frame(self, bg=C_CARD)
            head.pack(fill="x", padx=18, pady=(14, 0))
            tk.Frame(head, bg=accent, width=3, height=16).pack(side="left", padx=(0, 9))
            box = tk.Frame(head, bg=C_CARD)
            box.pack(side="left", fill="x", expand=True)
            tk.Label(box, text=title, bg=C_CARD, fg=C_FG, font=FONT_H2).pack(anchor="w")
            if description:
                tk.Label(box, text=description, bg=C_CARD, fg=C_FG_DIM,
                         font=FONT_SM).pack(anchor="w", pady=(2, 0))
        self.body = tk.Frame(self, bg=C_CARD)
        self.body.pack(fill="both", expand=True, padx=18, pady=(12, 16))


class TechButton(tk.Frame):
    """带 1px 描边的按钮：Frame 画边框、Label 画填充（深色下才看得见边界）。"""

    PALETTE = {
        "default":     dict(fill=C_ACCENT,    fg=C_ON_ACCENT, border=C_ACCENT,  hover=C_ACCENT_HI),
        "secondary":   dict(fill=C_CARD_HI,   fg=C_FG,        border=C_BORDER,  hover=C_BORDER),
        "outline":     dict(fill=C_CARD,      fg=C_FG,        border=C_BORDER,  hover=C_CARD_HI),
        "ghost":       dict(fill=C_CARD,      fg=C_FG_DIM,    border=C_CARD,    hover=C_CARD_HI),
        "destructive": dict(fill=C_DANGER_BG, fg=C_DANGER,    border="#5A2634", hover="#4A1B27"),
    }

    def __init__(self, parent, text, command, kind="default"):
        self._p = self.PALETTE.get(kind, self.PALETTE["default"])
        self._enabled = True
        super().__init__(parent, bg=self._p["border"], bd=0, highlightthickness=0)
        self.inner = tk.Label(self, text=text, font=FONT_BOLD, bg=self._p["fill"],
                              fg=self._p["fg"], padx=16, pady=9, cursor="hand2")
        self.inner.pack(padx=1, pady=1, fill="both", expand=True)
        for w in (self, self.inner):
            w.bind("<Button-1>", lambda e: command() if self._enabled else None)
            w.bind("<Enter>", lambda e: self._hover(True))
            w.bind("<Leave>", lambda e: self._hover(False))

    def _hover(self, on: bool):
        if self._enabled:
            self.inner.configure(bg=self._p["hover"] if on else self._p["fill"])

    def configure(self, **kw):
        text = kw.pop("text", None)
        if text is not None:
            self.inner.configure(text=text)
        if kw:
            super().configure(**kw)

    config = configure

    def set_enabled(self, on: bool):
        self._enabled = bool(on)
        p = self._p
        super().configure(bg=p["border"] if on else C_BORDER)
        self.inner.configure(bg=p["fill"] if on else C_CARD_HI,
                             fg=p["fg"] if on else C_FG_FAINT,
                             cursor="hand2" if on else "arrow")


def btn(parent, text, command, kind="default"):
    return TechButton(parent, text, command, kind)


def field_label(parent, text, bg=None):
    return tk.Label(parent, text=text, bg=bg or C_CARD, fg=C_FG_DIM, font=FONT_LABEL)


def entry(parent, textvariable=None, width=None, font=None):
    e = tk.Entry(parent, textvariable=textvariable, font=font or FONT,
                 relief="flat", bd=0, bg=C_MUTED_BG, fg=C_FG,
                 insertbackground=C_ACCENT, insertwidth=2,
                 highlightthickness=1, highlightbackground=C_BORDER,
                 highlightcolor=C_ACCENT,
                 disabledbackground=C_MUTED_BG, disabledforeground=C_FG_FAINT)
    if width:
        e.configure(width=width)
    return e


class TechProgress(tk.Canvas):
    """细条进度：轨道 + 青色填充 + 10% 刻度；兼容 configure(value=/maximum=)。"""

    def __init__(self, parent, height: int = 10, **kw):
        super().__init__(parent, height=height, bg=C_CARD, highlightthickness=0, bd=0, **kw)
        self._value = 0
        self._max = 100
        self._bar = C_ACCENT
        self.bind("<Configure>", lambda e: self._draw())

    def configure(self, **kw):
        if "value" in kw:
            self._value = kw.pop("value")
        if "maximum" in kw:
            self._max = kw.pop("maximum") or 1
        if "bar_color" in kw:
            self._bar = kw.pop("bar_color")
        if kw:
            super().configure(**kw)
        self._draw()

    def _draw(self):
        self.delete("all")
        w = max(self.winfo_width(), 1)
        h = max(self.winfo_height(), 1)
        self.create_rectangle(0, 0, w - 1, h - 1, fill="#1B2B47", outline=C_BORDER)
        ratio = 0.0 if not self._max else max(0.0, min(1.0, self._value / self._max))
        if ratio > 0:
            self.create_rectangle(1, 1, max(2, int((w - 2) * ratio)), h - 1,
                                  fill=self._bar, outline="")
        for i in range(1, 10):
            x = int(w * i / 10)
            self.create_line(x, 0, x, h, fill=C_CARD)


class SlimScrollbar(tk.Canvas):
    """自绘细滚动条：原生 NSScroller 不跟主题，会突兀成白色，故自行绘制。"""

    def __init__(self, parent, target, bg: str = C_CARD, **kw):
        super().__init__(parent, width=6, bg=bg, highlightthickness=0, bd=0, **kw)
        self.target = target
        self._lo, self._hi = 0.0, 1.0
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._jump)
        self.bind("<B1-Motion>", self._jump)

    def set(self, lo, hi):
        self._lo, self._hi = float(lo), float(hi)
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = max(self.winfo_width(), 1), max(self.winfo_height(), 1)
        if self._hi - self._lo >= 0.999:
            return
        y1 = int(h * self._lo)
        y2 = max(y1 + 24, int(h * self._hi))
        self.create_rectangle(1, y1, max(2, w - 1), min(y2, h - 1),
                              fill=C_ACCENT_DIM, outline="")
        self.create_rectangle(1, y1, max(2, w - 1), min(y1 + 24, h - 1),
                              fill=C_ACCENT, outline="")

    def _jump(self, e):
        h = max(self.winfo_height(), 1)
        span = self._hi - self._lo
        self.target.yview_moveto(max(0.0, min(1.0, e.y / h - span / 2)))


class StatTile(tk.Frame):
    """统计块：顶部色条 + 标签 + 等宽大数字。"""

    def __init__(self, parent, label: str, color: str):
        super().__init__(parent, bg=C_MUTED_BG, highlightbackground=C_BORDER,
                         highlightthickness=1, bd=0)
        tk.Frame(self, bg=color, height=2).pack(fill="x")
        tk.Label(self, text=label, bg=C_MUTED_BG, fg=C_FG_DIM,
                 font=FONT_LABEL).pack(anchor="w", padx=12, pady=(8, 0))
        self.var = tk.StringVar(value="0")
        tk.Label(self, textvariable=self.var, bg=C_MUTED_BG, fg=color,
                 font=FONT_STAT).pack(anchor="w", padx=12, pady=(0, 8))


class App:
    def __init__(self):
        self.root = tk.Tk()
        init_fonts(self.root)
        self.root.title("Carousell SKU 素材采集")
        self.root.geometry("1240x880")
        self.root.minsize(1080, 720)
        self.root.configure(bg=C_BG)
        self.logq: "queue.Queue[str]" = queue.Queue()
        self.engine: Optional[CollectorEngine] = None
        self.worker: Optional[threading.Thread] = None
        self.sched = None
        self.library = LinkLibrary()
        self.tstore = TitleRules(log=self.log)
        self.ppool = ProxyPool(log=self.log)
        self._stats_vars: dict[str, tk.StringVar] = {}
        self._setup_ttk()
        self._build()
        self.root.after(150, self._poll_log)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            self.root.resizable(True, True)
        except Exception:
            pass

    # ---------------- ttk 主题 ----------------

    def _setup_ttk(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TCombobox", fieldbackground=C_MUTED_BG, background=C_MUTED_BG,
                        foreground=C_FG, arrowcolor=C_FG_DIM, bordercolor=C_BORDER,
                        lightcolor=C_MUTED_BG, darkcolor=C_MUTED_BG,
                        selectbackground=C_MUTED_BG, selectforeground=C_FG, padding=4)
        style.map("TCombobox",
                  fieldbackground=[("readonly", C_MUTED_BG), ("disabled", C_CARD)],
                  foreground=[("readonly", C_FG), ("disabled", C_FG_FAINT)],
                  bordercolor=[("focus", C_ACCENT), ("active", C_BORDER_HI)])
        style.configure("TSpinbox", fieldbackground=C_MUTED_BG, background=C_MUTED_BG,
                        foreground=C_FG, arrowcolor=C_FG_DIM, bordercolor=C_BORDER,
                        lightcolor=C_MUTED_BG, darkcolor=C_MUTED_BG, padding=4)
        style.map("TSpinbox", bordercolor=[("focus", C_ACCENT)])
        style.configure("Treeview", background=C_CARD, fieldbackground=C_CARD,
                        foreground=C_FG, rowheight=36, font=FONT, borderwidth=0)
        style.configure("Treeview.Heading", background=C_MUTED_BG, foreground=C_FG_DIM,
                        font=FONT_LABEL, relief="flat", borderwidth=0, padding=(8, 8))
        style.map("Treeview.Heading", background=[("active", C_CARD_HI)])
        style.map("Treeview", background=[("selected", C_ACCENT_DIM)],
                  foreground=[("selected", C_FG)])
        style.configure("Vertical.TScrollbar", background=C_BORDER, troughcolor=C_BG,
                        bordercolor=C_BG, arrowcolor=C_FG_DIM, width=12)
        self.root.option_add("*TCombobox*Listbox.background", C_CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", C_FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", C_ACCENT_DIM)
        self.root.option_add("*TCombobox*Listbox.selectForeground", C_FG)

    # ---------------- layout ----------------

    def _build(self):
        self._build_header()
        self._build_tabs()
        self._build_status()
        self.root.after(60, self._detect_browser_async)

    def _build_header(self):
        head = tk.Frame(self.root, bg=C_SURFACE)
        head.pack(fill="x")
        inner = tk.Frame(head, bg=C_SURFACE)
        inner.pack(fill="x", padx=22, pady=(14, 13))

        tk.Frame(inner, bg=C_ACCENT, width=6, height=36).pack(side="left", padx=(0, 12))
        titles = tk.Frame(inner, bg=C_SURFACE)
        titles.pack(side="left")
        tk.Label(titles, text="CAROUSELL COLLECTOR", bg=C_SURFACE, fg=C_FG,
                 font=(FONT_MONO[0], 15, "bold")).pack(anchor="w")
        tk.Label(titles, text="SKU 素材采集台", bg=C_SURFACE, fg=C_FG_DIM,
                 font=FONT_SM).pack(anchor="w")

        chip = tk.Frame(inner, bg=C_MUTED_BG, highlightbackground=C_BORDER,
                        highlightthickness=1)
        chip.pack(side="right")
        self.var_browser = tk.StringVar(value="检测浏览器中…")
        self.lbl_kernel_dot = tk.Label(chip, text="●", bg=C_MUTED_BG, fg=C_FG_FAINT,
                                       font=FONT_SM)
        self.lbl_kernel_dot.pack(side="left", padx=(12, 6), pady=7)
        tk.Label(chip, textvariable=self.var_browser, bg=C_MUTED_BG, fg=C_FG_DIM,
                 font=FONT_SM).pack(side="left", padx=(0, 12), pady=7)

        tk.Frame(self.root, bg=C_ACCENT, height=2).pack(fill="x")

    def _build_tabs(self):
        bar = tk.Frame(self.root, bg=C_BG)
        bar.pack(fill="x", padx=22, pady=(14, 0))
        self._tab_btns: dict = {}
        self._tab_marks: dict = {}
        self._pages: dict = {}

        holder = tk.Frame(self.root, bg=C_BG)
        holder.pack(fill="both", expand=True, padx=22, pady=(10, 0))

        for key, text in (("collect", "采集"), ("library", "链接库"), ("pool", "IP 池")):
            wrap = tk.Frame(bar, bg=C_BG)
            wrap.pack(side="left", padx=(0, 22))
            lab = tk.Label(wrap, text=text, bg=C_BG, fg=C_FG_DIM, font=FONT_TABS,
                           cursor="hand2", padx=2, pady=6)
            lab.pack()
            mark = tk.Frame(wrap, bg=C_BG, height=2)
            mark.pack(fill="x")
            lab.bind("<Button-1>", lambda e, k=key: self._switch_tab(k))
            self._tab_btns[key] = lab
            self._tab_marks[key] = mark
            self._pages[key] = tk.Frame(holder, bg=C_BG)

        self._build_collect(self._pages["collect"])
        self._build_library(self._pages["library"])
        self._build_pool(self._pages["pool"])
        self._switch_tab("collect")

    def _switch_tab(self, key: str):
        for k, page in self._pages.items():
            if k == key:
                page.pack(fill="both", expand=True)
                self._tab_btns[k].configure(fg=C_FG)
                self._tab_marks[k].configure(bg=C_ACCENT)
            else:
                page.pack_forget()
                self._tab_btns[k].configure(fg=C_FG_DIM)
                self._tab_marks[k].configure(bg=C_BG)

    def _build_status(self):
        bar = tk.Frame(self.root, bg=C_SURFACE, highlightbackground=C_BORDER,
                       highlightthickness=1)
        bar.pack(fill="x", side="bottom")
        self.lbl_status = tk.Label(bar, text="就绪", bg=C_SURFACE, fg=C_FG_DIM,
                                   font=FONT_SM, anchor="w")
        self.lbl_status.pack(side="left", fill="x", expand=True, padx=22, pady=8)
        tk.Label(bar, text=f"v{__version__} · 单一客户端采集", bg=C_SURFACE, fg=C_FG_FAINT,
                 font=FONT_MONO_SM).pack(side="right", padx=22)

    # ---------------- 采集页 ----------------

    def _build_collect(self, parent):
        content = tk.Frame(parent, bg=C_BG)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=3, minsize=560)
        content.columnconfigure(1, weight=2, minsize=380)
        content.rowconfigure(0, weight=1)

        left = tk.Frame(content, bg=C_BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))

        # 卡1 配置
        c1 = Card(left, "采集配置", "链接可混合 /u/ 卖家主页与 /p/ 商品")
        c1.pack(fill="x")
        g = c1.body
        g.columnconfigure(1, weight=1)

        field_label(g, "保存根路径").grid(row=0, column=0, sticky="w", pady=(0, 8))
        path_row = tk.Frame(g, bg=C_CARD)
        path_row.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        path_row.columnconfigure(0, weight=1)
        self.var_root = tk.StringVar()
        entry(path_row, self.var_root).grid(row=0, column=0, sticky="ew", ipady=6)
        btn(path_row, "浏览", self._pick_root, "outline").grid(row=0, column=1, padx=(8, 0))

        field_label(g, "链接").grid(row=1, column=0, sticky="nw", pady=(4, 8))
        self.txt_urls = tk.Text(g, height=4, wrap="char", font=FONT_MONO,
                                relief="flat", bd=0, bg=C_MUTED_BG, fg=C_FG,
                                insertbackground=C_ACCENT, insertwidth=2,
                                highlightthickness=1, highlightbackground=C_BORDER,
                                highlightcolor=C_ACCENT, padx=10, pady=8)
        self.txt_urls.grid(row=1, column=1, sticky="ew", pady=(4, 8))
        tk.Label(g, text="每行一条", bg=C_CARD, fg=C_FG_FAINT,
                 font=FONT_MONO_SM).grid(row=2, column=1, sticky="e", pady=(0, 8))

        opts = tk.Frame(g, bg=C_CARD)
        opts.grid(row=3, column=0, columnspan=2, sticky="ew")

        field_label(opts, "图片并发").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.var_workers = tk.StringVar(value="4")
        ttk.Spinbox(opts, from_=1, to=16, increment=1, textvariable=self.var_workers,
                    width=5, font=FONT).grid(row=0, column=1, sticky="w", padx=(0, 18))

        field_label(opts, "每卖家上限").grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.var_max_items = tk.StringVar(value="5000")
        ttk.Spinbox(opts, from_=20, to=20000, increment=100,
                    textvariable=self.var_max_items, width=7,
                    font=FONT).grid(row=0, column=3, sticky="w")

        field_label(opts, "Folder 路径").grid(row=1, column=0, sticky="w",
                                              pady=(10, 0), padx=(0, 8))
        self.var_path_style = tk.StringVar(value="native")
        ttk.Combobox(opts, textvariable=self.var_path_style,
                     values=["native", "posix", "relative"], width=9,
                     state="readonly", font=FONT).grid(row=1, column=1, sticky="w",
                                                       pady=(10, 0), padx=(0, 18))

        field_label(opts, "代理").grid(row=1, column=2, sticky="w", pady=(10, 0), padx=(0, 8))
        self.var_proxy = tk.StringVar()
        entry(opts, self.var_proxy).grid(row=1, column=3, sticky="ew", pady=(10, 0), ipady=6)
        opts.columnconfigure(3, weight=1)

        # 卡2 运行
        c2 = Card(left, "运行", "开始后浏览器自动过验证，日志实时回传")
        c2.pack(fill="both", expand=True, pady=(14, 0))

        stats = tk.Frame(c2.body, bg=C_CARD)
        stats.pack(fill="x")
        self._stat_tile(stats, "新增", "new", C_SUCCESS)
        self._stat_tile(stats, "跳过", "skipped", C_WARN)
        self._stat_tile(stats, "失败", "failed", C_DANGER)
        self._stat_tile(stats, "图片", "images", C_INFO)

        ctrl = tk.Frame(c2.body, bg=C_CARD)
        ctrl.pack(fill="x", pady=(14, 0))
        self.btn_start = btn(ctrl, "▶  开始采集", self._start, "default")
        self.btn_start.pack(side="left")
        self.btn_stop = btn(ctrl, "■  停止", self._stop, "destructive")
        self.btn_stop.pack(side="left", padx=10)
        self.btn_stop.set_enabled(False)

        au = tk.Frame(c2.body, bg=C_CARD)
        au.pack(fill="x", pady=(14, 0))
        self.var_auto_on = tk.BooleanVar(value=False)
        tk.Checkbutton(au, text="自动更新", variable=self.var_auto_on,
                       command=self._toggle_auto, bg=C_CARD, fg=C_FG, font=FONT,
                       selectcolor=C_MUTED_BG, activebackground=C_CARD,
                       activeforeground=C_FG, highlightthickness=0,
                       bd=0).pack(side="left")
        self.var_auto_mode = tk.StringVar(value="interval")
        acb = ttk.Combobox(au, textvariable=self.var_auto_mode,
                           values=["interval", "daily"], width=9,
                           state="readonly", font=FONT)
        acb.pack(side="left", padx=(10, 6))
        acb.bind("<<ComboboxSelected>>", lambda e: self._auto_mode_changed())
        field_label(au, "间隔(分)").pack(side="left", padx=(4, 6))
        self.var_auto_interval = tk.StringVar(value="60")
        ttk.Spinbox(au, from_=1, to=1440, textvariable=self.var_auto_interval,
                    width=5, font=FONT).pack(side="left")
        field_label(au, "每日").pack(side="left", padx=(10, 6))
        self.var_auto_daily = tk.StringVar(value="09:00")
        entry(au, self.var_auto_daily, width=7).pack(side="left", ipady=4)
        btn(au, "立即更新", self._trigger_now, "outline").pack(side="left", padx=12)

        self.lbl_sched = tk.Label(c2.body, text="自动更新 · 关", bg=C_CARD, fg=C_FG_FAINT,
                                  font=FONT_MONO_SM, anchor="w")
        self.lbl_sched.pack(fill="x", pady=(10, 0))

        self.lbl_progress = tk.Label(c2.body, text="就绪", bg=C_CARD, fg=C_FG_DIM,
                                     font=FONT_MONO_SM, anchor="w")
        self.lbl_progress.pack(side="bottom", fill="x", pady=(6, 0))
        self.progress = TechProgress(c2.body, height=12)
        self.progress.pack(side="bottom", fill="x", pady=(16, 0))

        # 右列：日志（自适应）+ 类目过滤（贴底）
        right_col = tk.Frame(content, bg=C_BG)
        right_col.grid(row=0, column=1, sticky="nsew")

        log_card = tk.Frame(right_col, bg=C_CARD, highlightbackground=C_BORDER,
                            highlightthickness=1)
        log_card.pack(fill="both", expand=True)
        rt = tk.Frame(log_card, bg=C_CARD)
        rt.pack(fill="x", padx=18, pady=(14, 10))
        tk.Frame(rt, bg=C_ACCENT, width=3, height=16).pack(side="left", padx=(0, 9))
        tk.Label(rt, text="运行日志", bg=C_CARD, fg=C_FG, font=FONT_H2).pack(side="left")
        tk.Label(rt, text="● LIVE", bg=C_CARD, fg=C_SUCCESS,
                 font=FONT_MONO_SM).pack(side="right")

        log_wrap = tk.Frame(log_card, bg=C_TERM_BG, highlightthickness=1,
                            highlightbackground=C_BORDER)
        log_wrap.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.txt_log = tk.Text(log_wrap, state="disabled", font=FONT_MONO, bg=C_TERM_BG,
                               fg=C_TERM_FG, insertbackground=C_TERM_FG, relief="flat",
                               bd=0, selectbackground=C_ACCENT_DIM, selectforeground=C_FG,
                               padx=14, pady=12, wrap="word", highlightthickness=0)
        vsb = SlimScrollbar(log_wrap, self.txt_log, bg=C_TERM_BG)
        self.txt_log.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y", padx=(6, 6), pady=8)
        self.txt_log.pack(side="left", fill="both", expand=True)
        for tag, fg in (("ok", C_SUCCESS), ("warn", C_WARN), ("err", C_DANGER),
                        ("phase", C_INFO), ("dim", "#7C8FA8")):
            self.txt_log.tag_config(tag, foreground=fg)
        self.txt_log.tag_config("phase", font=(FONT_MONO[0], 12, "bold"))

        # 规则面板：类目跳过 / 标题清洗（子页签，省纵向空间）
        panel = tk.Frame(right_col, bg=C_CARD, highlightbackground=C_BORDER,
                         highlightthickness=1)
        panel.pack(fill="x", pady=(14, 0))
        phead = tk.Frame(panel, bg=C_CARD)
        phead.pack(fill="x", padx=18, pady=(12, 0))
        tk.Frame(phead, bg=C_ACCENT, width=3, height=16).pack(side="left", padx=(0, 9))
        self._rules_tabs = {}
        self._rules_marks = {}
        for key, text in (("cats", "类目跳过"), ("title", "标题清洗")):
            wrap = tk.Frame(phead, bg=C_CARD)
            wrap.pack(side="left", padx=(0, 12))
            lab = tk.Label(wrap, text=text, bg=C_CARD, fg=C_FG_DIM, font=FONT_BOLD,
                           cursor="hand2", padx=2, pady=2)
            lab.pack()
            mark = tk.Frame(wrap, bg=C_CARD, height=2)
            mark.pack(fill="x", pady=(3, 0))
            lab.bind("<Button-1>", lambda e, k=key: self._switch_rules(k))
            self._rules_tabs[key] = lab
            self._rules_marks[key] = mark

        pbody = tk.Frame(panel, bg=C_CARD)
        pbody.pack(fill="both", expand=True, padx=18, pady=(8, 14))
        self._rules_pages = {k: tk.Frame(pbody, bg=C_CARD) for k in ("cats", "title")}
        self._build_cats_page(self._rules_pages["cats"])
        self._build_title_page(self._rules_pages["title"])
        self._switch_rules("cats")

    def _stat_tile(self, parent, label, key, color):
        tile = StatTile(parent, label, color)
        tile.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self._stats_vars[key] = tile.var

    # ---------------- 规则：类目 / 标题 ----------------

    def _switch_rules(self, key: str):
        for k, page in self._rules_pages.items():
            if k == key:
                page.pack(fill="both", expand=True)
                self._rules_tabs[k].configure(fg=C_FG)
                self._rules_marks[k].configure(bg=C_ACCENT)
            else:
                page.pack_forget()
                self._rules_tabs[k].configure(fg=C_FG_DIM)
                self._rules_marks[k].configure(bg=C_CARD)
        if key == "pool":
            self._render_pool()
        elif key == "title":
            self._render_title_rules()
        elif getattr(self, "fstore", None) is not None:
            self._render_cats()

    def _build_cats_page(self, b):
        b.columnconfigure(1, weight=1)
        self.var_skip_on = tk.BooleanVar(value=True)
        tk.Checkbutton(b, text="启用", variable=self.var_skip_on,
                       command=self._toggle_skip, bg=C_CARD, fg=C_FG, font=FONT,
                       selectcolor=C_MUTED_BG, activebackground=C_CARD,
                       activeforeground=C_FG, highlightthickness=0,
                       bd=0).grid(row=0, column=0, sticky="w")
        self.var_newcat = tk.StringVar()
        entry(b, self.var_newcat).grid(row=0, column=1, sticky="ew", padx=10, ipady=5)
        btn(b, "添加", self._add_cat, "outline").grid(row=0, column=2)

        self.lst_cats = tk.Listbox(b, height=3, font=FONT, relief="flat", bd=0,
                                   bg=C_MUTED_BG, fg=C_FG,
                                   selectbackground=C_ACCENT_DIM, selectforeground=C_FG,
                                   highlightthickness=1, highlightbackground=C_BORDER,
                                   activestyle="none", selectborderwidth=0,
                                   borderwidth=0)
        self.lst_cats.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 0))

        foot = tk.Frame(b, bg=C_CARD)
        foot.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        btn(foot, "开 / 关", self._toggle_cat, "ghost").pack(side="left")
        btn(foot, "删除", self._del_cat, "ghost").pack(side="left", padx=8)
        tk.Label(foot, text="filters.json", bg=C_CARD, fg=C_FG_FAINT,
                 font=FONT_MONO_SM).pack(side="right")

    def _build_title_page(self, b):
        b.columnconfigure(1, weight=1)
        self.var_title_on = tk.BooleanVar(value=self.tstore.enabled)
        tk.Checkbutton(b, text="启用", variable=self.var_title_on,
                       command=self._toggle_title_on, bg=C_CARD, fg=C_FG, font=FONT,
                       selectcolor=C_MUTED_BG, activebackground=C_CARD,
                       activeforeground=C_FG, highlightthickness=0,
                       bd=0).grid(row=0, column=0, sticky="w")
        self.var_title_pattern = tk.StringVar()
        entry(b, self.var_title_pattern, font=FONT_MONO).grid(
            row=0, column=1, sticky="ew", padx=8, ipady=5)
        tk.Label(b, text="→", bg=C_CARD, fg=C_FG_DIM, font=FONT).grid(row=0, column=2)
        self.var_title_replace = tk.StringVar()
        entry(b, self.var_title_replace, width=6).grid(row=0, column=3, padx=(6, 8), ipady=5)
        btn(b, "添加", self._add_title_rule, "outline").grid(row=0, column=4)

        self.lst_title = tk.Listbox(b, height=3, font=FONT_MONO_SM, relief="flat", bd=0,
                                    bg=C_MUTED_BG, fg=C_FG,
                                    selectbackground=C_ACCENT_DIM, selectforeground=C_FG,
                                    highlightthickness=1, highlightbackground=C_BORDER,
                                    activestyle="none", selectborderwidth=0,
                                    borderwidth=0)
        self.lst_title.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(10, 0))

        foot = tk.Frame(b, bg=C_CARD)
        foot.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(8, 0))
        btn(foot, "开 / 关", self._toggle_title_rule, "ghost").pack(side="left")
        btn(foot, "删除", self._del_title_rule, "ghost").pack(side="left", padx=8)
        btn(foot, "↑ 上移", lambda: self._move_title_rule(-1), "ghost").pack(side="left")
        btn(foot, "↓ 下移", lambda: self._move_title_rule(1), "ghost").pack(side="left", padx=8)
        btn(foot, "恢复默认", self._reset_title_rules, "ghost").pack(side="left")
        tk.Label(foot, text="自上而下执行", bg=C_CARD, fg=C_FG_FAINT,
                 font=FONT_MONO_SM).pack(side="right")
        self._render_title_rules()

    def _render_title_rules(self):
        self.lst_title.delete(0, "end")
        for r in self.tstore.rules():
            mark = "●" if r.get("enabled", True) else "○"
            rep = f"  →  {r['replace']}" if r.get("replace") else ""
            self.lst_title.insert("end", f"{mark} {r['pattern']}{rep}")
        if hasattr(self, "var_title_on"):
            self.var_title_on.set(self.tstore.enabled)

    def _toggle_title_on(self):
        self.tstore.set_enabled(self.var_title_on.get())

    def _add_title_rule(self):
        pat = self.var_title_pattern.get().strip()
        rep = self.var_title_replace.get()
        if not pat:
            return
        if not self.tstore.add(pat, rep):
            messagebox.showwarning("正则无效", f"这条正则无法编译，已忽略：\n\n{pat}")
            return
        self.var_title_pattern.set("")
        self.var_title_replace.set("")
        self._render_title_rules()

    def _toggle_title_rule(self):
        sel = self.lst_title.curselection()
        if sel:
            self.tstore.toggle(sel[0])
            self._render_title_rules()

    def _move_title_rule(self, delta: int):
        sel = self.lst_title.curselection()
        if not sel:
            return
        idx = sel[0]
        self.tstore.move(idx, delta)
        self._render_title_rules()
        new = idx + delta
        if 0 <= new < self.lst_title.size():
            self.lst_title.selection_set(new)
            self.lst_title.see(new)

    def _del_title_rule(self):
        sel = self.lst_title.curselection()
        if sel:
            self.tstore.remove(sel[0])
            self._render_title_rules()

    def _reset_title_rules(self):
        if messagebox.askyesno("恢复默认", "将标题清洗规则恢复为内置默认值？"):
            self.tstore.reset()
            self._render_title_rules()

    # ---------------- 链接库页 ----------------

    def _build_library(self, parent):
        wrap = tk.Frame(parent, bg=C_BG)
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        card = Card(wrap, "链接库",
                    "所有运行过的卖家自动记录于此（全局共享 ~/.carousell-collector/link_library.json）")
        card.grid(row=0, column=0, sticky="nsew")

        bar = tk.Frame(card.body, bg=C_CARD)
        bar.pack(fill="x")
        btn(bar, "↻  刷新", self._render_library, "outline").pack(side="left")
        btn(bar, "＋  全选采集", self._collect_all_enabled, "default").pack(side="left", padx=10)
        btn(bar, "开关选中", self._toggle_lib_entry, "secondary").pack(side="left")
        btn(bar, "删除选中", self._remove_lib_entry, "ghost").pack(side="left", padx=8)
        self.lbl_lib_count = tk.Label(bar, text="", bg=C_CARD, fg=C_FG_DIM, font=FONT_MONO_SM)
        self.lbl_lib_count.pack(side="right")

        table = tk.Frame(card.body, bg=C_CARD)
        table.pack(fill="both", expand=True, pady=(12, 0))
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)

        cols = ("seller", "items", "runs", "new", "imgs", "last_run", "on")
        headings = ("卖家", "商品数", "次数", "上次新增", "图片", "最近运行", "状态")
        widths = (200, 80, 70, 90, 80, 160, 70)
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=16)
        for c, h, w in zip(cols, headings, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w,
                             anchor="w" if c in ("seller", "last_run") else "center")
        vsb = SlimScrollbar(table, self.tree)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns", padx=(6, 6), pady=6)

        self.tree.bind("<Double-1>", self._lib_row_dblclick)
        self._render_library()

    # ---------------- IP 池页 ----------------

    def _build_pool(self, parent):
        wrap = tk.Frame(parent, bg=C_BG)
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        card = Card(wrap, "代理池", f"全局配置 {self.ppool.path}")
        card.grid(row=0, column=0, sticky="nsew")

        bar = tk.Frame(card.body, bg=C_CARD)
        bar.pack(fill="x")
        self.var_pool_on = tk.BooleanVar(value=self.ppool.enabled)
        tk.Checkbutton(bar, text="启用", variable=self.var_pool_on, command=self._toggle_pool,
                       bg=C_CARD, fg=C_FG, font=FONT, selectcolor=C_MUTED_BG,
                       activebackground=C_CARD, activeforeground=C_FG,
                       highlightthickness=0, bd=0).pack(side="left")
        field_label(bar, "轮换策略").pack(side="left", padx=(14, 6))
        self.var_pool_strategy = tk.StringVar(value=STRATEGIES.get(self.ppool.strategy, "轮询"))
        cb = ttk.Combobox(bar, textvariable=self.var_pool_strategy,
                          values=list(STRATEGIES.values()), width=9,
                          state="readonly", font=FONT)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self._pool_strategy_changed())
        field_label(bar, "每 IP 请求数").pack(side="left", padx=(14, 6))
        self.var_pool_rpi = tk.StringVar(value=str(self.ppool.requests_per_ip))
        sp = ttk.Spinbox(bar, from_=20, to=5000, increment=20,
                         textvariable=self.var_pool_rpi, width=6, font=FONT,
                         command=self._pool_rpi_changed)
        sp.pack(side="left")
        sp.bind("<FocusOut>", lambda e: self._pool_rpi_changed())
        self.lbl_pool_count = tk.Label(bar, text="", bg=C_CARD, fg=C_FG_DIM, font=FONT_MONO_SM)
        self.lbl_pool_count.pack(side="right")

        table = tk.Frame(card.body, bg=C_CARD)
        table.pack(fill="both", expand=True, pady=(12, 0))
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        cols = ("label", "url", "state", "used", "fails", "last", "test")
        heads = ("标签", "代理", "状态", "已用", "失败", "最近使用", "上次测试")
        widths = (90, 300, 100, 70, 70, 140, 150)
        self.tree_pool = ttk.Treeview(table, columns=cols, show="headings", height=9)
        for c, h, w in zip(cols, heads, widths):
            self.tree_pool.heading(c, text=h)
            self.tree_pool.column(c, width=w,
                                  anchor="w" if c in ("url", "last", "test") else "center")
        vsb = SlimScrollbar(table, self.tree_pool)
        self.tree_pool.configure(yscrollcommand=vsb.set)
        self.tree_pool.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns", padx=(6, 6), pady=6)

        foot = tk.Frame(card.body, bg=C_CARD)
        foot.pack(fill="x", pady=(10, 0))
        btn(foot, "测试选中", self._pool_test, "outline").pack(side="left")
        btn(foot, "启用 / 停用", self._pool_toggle, "ghost").pack(side="left", padx=8)
        btn(foot, "删除选中", self._pool_del, "ghost").pack(side="left")
        btn(foot, "立即轮换", self._pool_rotate_now, "secondary").pack(side="left", padx=8)
        btn(foot, "清空统计", self._pool_clear, "ghost").pack(side="left")

        imp = Card(wrap, "批量导入", "每行一条：host:port:user:pass（可带 http:// 或 socks5:// 前缀）")
        imp.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        self.txt_proxies = tk.Text(imp.body, height=3, wrap="char", font=FONT_MONO,
                                   relief="flat", bd=0, bg=C_MUTED_BG, fg=C_FG,
                                   insertbackground=C_ACCENT, insertwidth=2,
                                   highlightthickness=1, highlightbackground=C_BORDER,
                                   highlightcolor=C_ACCENT, padx=10, pady=8)
        self.txt_proxies.pack(fill="x")
        ibar = tk.Frame(imp.body, bg=C_CARD)
        ibar.pack(fill="x", pady=(10, 0))
        btn(ibar, "导入", self._pool_import, "default").pack(side="left")
        btn(ibar, "从剪贴板粘贴", self._pool_paste, "outline").pack(side="left", padx=8)
        self.lbl_pool_hint = tk.Label(ibar, text="", bg=C_CARD, fg=C_FG_FAINT, font=FONT_MONO_SM)
        self.lbl_pool_hint.pack(side="right")
        self._render_pool()

    def _render_pool(self):
        import time as _t
        now = _t.time()
        for iid in self.tree_pool.get_children():
            self.tree_pool.delete(iid)
        items = self.ppool.proxies()
        for i, p in enumerate(items):
            if not p.get("enabled", True):
                state = "○ 停用"
            elif p.get("cooldown_until", 0) > now:
                state = f"⏸ {int((p['cooldown_until'] - now) / 60) + 1}m"
            else:
                state = "● 可用"
            self.tree_pool.insert("", "end", iid=str(i), values=(
                p.get("label") or f"IP{i + 1}",
                mask_proxy(p.get("url", "")),
                state,
                p.get("used", 0),
                p.get("fails", 0),
                p.get("last_used") or "—",
                p.get("last_test") or "—",
            ))
        self.lbl_pool_count.configure(
            text=f"{len(items)} 个出口 · 可用 {self.ppool.available_count()}")

    def _pool_sel_index(self):
        sel = self.tree_pool.selection()
        return int(sel[0]) if sel else None

    def _toggle_pool(self):
        self.ppool.set_enabled(self.var_pool_on.get())
        self._render_pool()

    def _pool_strategy_changed(self):
        label = self.var_pool_strategy.get()
        key = next((k for k, v in STRATEGIES.items() if v == label), "round_robin")
        self.ppool.set_strategy(key)

    def _pool_rpi_changed(self):
        try:
            self.ppool.set_requests_per_ip(int(self.var_pool_rpi.get()))
        except (TypeError, ValueError):
            self.var_pool_rpi.set(str(self.ppool.requests_per_ip))

    def _pool_import(self):
        raw = self.txt_proxies.get("1.0", "end").splitlines()
        urls = [l.strip() for l in raw if l.strip() and not l.strip().startswith("#")]
        if not urls:
            return
        added = self.ppool.add(urls)
        self.txt_proxies.delete("1.0", "end")
        self.lbl_pool_hint.configure(text=f"新增 {added} 个出口")
        self._render_pool()

    def _pool_paste(self):
        try:
            text = self.root.clipboard_get()
        except Exception:
            return
        self.txt_proxies.insert("end", ("\n" if self.txt_proxies.get("1.0", "end").strip() else "") + text)

    def _pool_toggle(self):
        i = self._pool_sel_index()
        if i is not None:
            self.ppool.toggle(i)
            self._render_pool()

    def _pool_del(self):
        i = self._pool_sel_index()
        if i is not None:
            self.ppool.remove(i)
            self._render_pool()

    def _pool_clear(self):
        if messagebox.askyesno("清空统计", "把已用/失败/冷却全部归零？"):
            self.ppool.clear_stats()
            self._render_pool()

    def _pool_rotate_now(self):
        self.ppool.request_rotation()
        self.log("[IP] 已请求立即轮换（下一批生效）")

    def _pool_test(self):
        i = self._pool_sel_index()
        if i is None:
            return
        url = self.ppool.proxies()[i]["url"]

        def work():
            ok, note = False, ""
            try:
                from curl_cffi import requests as creq
                r = creq.get("https://api.ipify.org?format=json",
                             proxies={"http": url, "https": url}, timeout=12)
                note = (r.json() or {}).get("ip", "") or ""
                ok = bool(note)
            except Exception as e:
                note = type(e).__name__
            self.ppool.set_proxy_status(i, ok, note)
            self.log(f"[IP] {mask_proxy(url)} → {'出口 ' + note if ok else '测试失败 ' + note}")
            self.root.after(0, self._render_pool)

        self.log(f"[IP] 正在测试 {mask_proxy(url)} …")
        threading.Thread(target=work, daemon=True).start()

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
                "● 启用" if e.get("enabled", True) else "○ 停用",
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

    def _detect_browser_async(self):
        threading.Thread(target=self._detect_browser, daemon=True).start()

    def _detect_browser(self):
        try:
            from app.cf_session import find_browser, find_cloakbrowser
            from pathlib import Path
            cb = find_cloakbrowser()
            if cb:
                ver = ""
                for part in Path(cb).parts:
                    if part.startswith("chromium-"):
                        ver = part.replace("chromium-", "")
                        break
                self.var_browser.set(f"内核 CloakBrowser v{ver} · 指纹伪装")
                self.root.after(0, lambda: self.lbl_kernel_dot.configure(fg=C_SUCCESS))
            else:
                found = find_browser()
                if found:
                    kind = {"chrome": "Chrome", "edge": "Edge"}.get(found[1], found[1])
                    self.var_browser.set(f"内核 {kind}（建议安装 CloakBrowser）")
                    self.root.after(0, lambda: self.lbl_kernel_dot.configure(fg=C_WARN))
                else:
                    self.var_browser.set("未找到浏览器内核")
                    self.root.after(0, lambda: self.lbl_kernel_dot.configure(fg=C_DANGER))
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
            self.lst_cats.insert("end", f"{'● 跳过' if on else '○ 采集'}  {name}")

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
        self.log(f"[启动] 根路径 {root} | 链接 {len(urls)} 条 | 图片并发 {workers} | 上限 {max_items}"
                 + (f" | 代理 {proxy}" if proxy else " | 直连"))
        self.lbl_status.configure(text="采集中…", fg=C_ACCENT)
        self._run_collect(urls, root, workers, proxy, max_items=max_items,
                          path_style=self.var_path_style.get())

    def _set_running(self, running: bool):
        if running:
            self.btn_start.set_enabled(False)
            self.btn_start.configure(text="▶  采集中…")
            self.btn_stop.set_enabled(True)
            self.progress.configure(value=0, maximum=100, bar_color=C_ACCENT)
        else:
            self.btn_start.set_enabled(True)
            self.btn_start.configure(text="▶  开始采集")
            self.btn_stop.set_enabled(False)

    def _run_collect(self, urls, root, workers, proxy, quiet=False,
                     max_items=5000, path_style="native"):
        if self.worker and self.worker.is_alive():
            self.log("[跳过] 采集正在进行中")
            return
        self._set_running(True)
        self.progress.configure(value=0, maximum=100)
        self.engine = CollectorEngine(root, log=self.log, progress=self._on_progress,
                                      proxy=proxy, img_workers=workers,
                                      max_items=max_items, path_style=path_style)
        self.engine.filters = self.fstore
        self.engine.title_rules = self.tstore
        self.engine.pool = self.ppool

        def work():
            try:
                self.engine.collect(urls)
            except Exception as e:
                self.log(f"[异常] {e}")
            finally:
                self.log("[结束] 采集线程退出")
                self.root.after(0, lambda: self._set_running(False))
                self.root.after(0, lambda: self.lbl_status.configure(text="就绪", fg=C_FG_DIM))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stop(self):
        if self.engine:
            self.engine.stop()
            self.log("[停止] 已请求中止并回收浏览器进程")
            self.lbl_status.configure(text="已停止", fg=C_DANGER)
        if getattr(self, "sched", None):
            self.sched.stop()
            self.lbl_sched.configure(text="自动更新 · 关")

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
            self.lbl_sched.configure(text="自动更新 · 关")

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
                        text=f"自动更新 · 开 | {mode} | 下次 {nr:%m-%d %H:%M}" if nr
                        else f"自动更新 · 开 | {mode}")
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
