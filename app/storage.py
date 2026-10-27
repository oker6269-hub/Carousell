# -*- coding: utf-8 -*-
"""存储层：SKU 目录 + state.json 断点 + filters.json 类目过滤 + Excel（模板格式）。

Excel 规则（版式对齐「最干净的上品记录/001/测试.xlsx」Sheet1）：
表头 12 列：SKU, BrowserID, ProductNameCn, ProductNameEn, GenderEn, HKPrice,
           SGPrice, MYPrice, Brand, Folder, ShopID, Desc
- 字体：等线 12（表头与数据行一致），表头垂直居中、行高 22
- 列宽：A 17 / B 41.33 / C 75.83 / D 65.66 / E-H 13 / I 27.33 / J 75 / K-L 13
- 自动筛选 A1:L1，不冻结窗格
首行表头 + 数据行追加；重跑按 SKU 去重。
"""
import json
import threading
from pathlib import Path
from typing import Optional

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

HEADERS = ["SKU", "BrowserID", "ProductNameCn", "ProductNameEn", "GenderEn", "HKPrice",
           "SGPrice", "MYPrice", "Brand", "Folder", "ShopID", "Desc"]
XLSX_NAME = "素材保存表格.xlsx"

# ---- 版式（照抄用户模板 测试.xlsx） ----
TEMPLATE_FONT = "等线"
TEMPLATE_SIZE = 12
TEMPLATE_WIDTHS = [17.0, 41.33, 75.83, 65.66, 13.0, 13.0, 13.0, 13.0, 27.33, 75.0, 13.0, 13.0]
HEADER_ROW_HEIGHT = 22
FILTER_REF = "A1:L1"


# ---------------- state.json（断点/增量） ----------------

class StateStore:
    """SKU 状态持久化：done / skipped / failed。"""

    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = {"skus": {}}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {"skus": {}}
        self._data.setdefault("skus", {})

    def status(self, sku: str) -> Optional[str]:
        return self._data["skus"].get(sku, {}).get("status")

    def record(self, sku: str, status: str, **meta):
        with self._lock:
            entry = self._data["skus"].get(sku, {})
            entry.update({"status": status, **meta})
            self._data["skus"][sku] = entry
            self._save()

    def known_skus(self) -> set:
        return set(self._data["skus"].keys())

    def counts(self) -> dict:
        from collections import Counter
        c = Counter(v.get("status") for v in self._data["skus"].values())
        return dict(c)

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)


# ---------------- filters.json（类目过滤） ----------------

class FilterStore:
    """类目跳过管理：{"skip_enabled": true, "categories": {"其他": true, ...}}"""

    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = {"skip_enabled": True, "categories": {}}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        self._data.setdefault("skip_enabled", True)
        self._data.setdefault("categories", {})

    @property
    def enabled(self) -> bool:
        return bool(self._data.get("skip_enabled"))

    def set_enabled(self, on: bool):
        with self._lock:
            self._data["skip_enabled"] = bool(on)
            self._save()

    def categories(self) -> dict:
        return dict(self._data.get("categories", {}))

    def add(self, category: str, enabled: bool = True):
        with self._lock:
            self._data["categories"][category] = bool(enabled)
            self._save()

    def remove(self, category: str):
        with self._lock:
            self._data["categories"].pop(category, None)
            self._save()

    def toggle(self, category: str):
        with self._lock:
            cur = self._data["categories"].get(category, False)
            self._data["categories"][category] = not cur
            self._save()

    def should_skip(self, category: str) -> bool:
        if not self.enabled or not category:
            return False
        cats = self._data.get("categories", {})
        # 精确匹配或子串匹配（类目名可能带层级）
        for name, on in cats.items():
            if on and (name == category or name in category or category in name):
                return True
        return False

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)


# ---------------- Excel（模板格式） ----------------

class ExcelStore:
    """追加式 Excel 记录，版式与用户模板一致（12 列）。"""

    def __init__(self, root: str, log=print):
        self.root = Path(root)
        self.path = self.root / XLSX_NAME
        self.log = log
        self._lock = threading.Lock()
        self._ensure()

    # ---------- 版式 ----------

    @staticmethod
    def _cell_font():
        return Font(name=TEMPLATE_FONT, size=TEMPLATE_SIZE)

    def _apply_layout(self, ws):
        """列宽 / 筛选 / 不冻结：整张表级别的设置。"""
        for i, w in enumerate(TEMPLATE_WIDTHS, 1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
        ws.auto_filter.ref = FILTER_REF
        ws.freeze_panes = None

    def _style_header(self, ws):
        font = self._cell_font()
        for col in range(1, len(HEADERS) + 1):
            c = ws.cell(row=1, column=col)
            c.font = font
            c.fill = PatternFill(fill_type=None)
            c.alignment = Alignment(vertical="center")
        ws.row_dimensions[1].height = HEADER_ROW_HEIGHT

    def _normalize_numeric(self, ws, row_idx: int):
        """SKU 与三档价格若是数字字符串，转成数值（模板里就是数值）。"""
        for col in (1, 6, 7, 8):
            c = ws.cell(row=row_idx, column=col)
            c.value = self._typed(c.value)

    def _style_row(self, ws, row_idx: int):
        font = self._cell_font()
        for col in range(1, len(HEADERS) + 1):
            c = ws.cell(row=row_idx, column=col)
            c.font = font

    # ---------- 初始化 / 迁移 ----------

    def _ensure(self):
        if not self.path.exists():
            wb = Workbook()
            ws = wb.active
            ws.title = "Sheet1"
            ws.append(HEADERS)
            self._style_header(ws)
            self._apply_layout(ws)
            wb.save(self.path)
            return
        self._migrate_if_needed()

    def _migrate_if_needed(self):
        """旧版式文件（粗体彩头、冻结窗格）就地改成模板版式。"""
        try:
            wb = load_workbook(self.path)
            ws = wb["Sheet1"]
            already = (ws["A1"].font.name == TEMPLATE_FONT
                       and ws.freeze_panes is None
                       and ws.auto_filter.ref == FILTER_REF)
            if already:
                wb.close()
                return
            self._style_header(ws)
            for r in range(2, ws.max_row + 1):
                self._style_row(ws, r)
                self._normalize_numeric(ws, r)
            self._apply_layout(ws)
            wb.save(self.path)
            wb.close()
            self.log("[Excel] 已把旧表格版式对齐模板（字体/列宽/筛选）")
        except PermissionError:
            self.log("[Excel] 文件正被占用，版式迁移跳过（新行仍用新版式）")
        except Exception as e:
            self.log(f"[Excel] 版式迁移失败: {e}")

    # ---------- 读写 ----------

    def existing_skus(self) -> set:
        with self._lock:
            if not self.path.exists():
                return set()
            wb = load_workbook(self.path, read_only=True)
            ws = wb["Sheet1"]
            skus = set()
            for row in ws.iter_rows(min_row=2, max_col=1, values_only=True):
                if row[0]:
                    skus.add(str(row[0]))
            wb.close()
            return skus

    @staticmethod
    def _typed(value):
        """SKU / 价格按数值写入（与模板一致），其余保持原值。"""
        if isinstance(value, bool) or value is None or value == "":
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    def append(self, records: list[dict]):
        """records: 与 HEADERS 同序的字段 dict 列表。"""
        if not records:
            return
        with self._lock:
            wb = load_workbook(self.path)
            ws = wb["Sheet1"]
            existing = set()
            for row in ws.iter_rows(min_row=2, max_col=1, values_only=True):
                if row[0]:
                    existing.add(str(row[0]))
            added = 0
            for rec in records:
                sku = str(rec.get("SKU", ""))
                if not sku or sku in existing:
                    continue
                ws.append([self._typed(rec.get(h, "")) for h in HEADERS])
                self._style_row(ws, ws.max_row)
                existing.add(sku)
                added += 1
            saved_to = self._save_with_retry(wb)
        self.log(f"[Excel] 追加 {added} 行 → {Path(saved_to).name}")

    def _save_with_retry(self, wb) -> str:
        """保存；若文件被 Excel 打开占用，重试后另存副本。"""
        import time as _t
        from datetime import datetime as _dt

        for attempt in range(3):
            try:
                wb.save(self.path)
                return str(self.path)
            except PermissionError:
                if attempt < 2:
                    self.log(f"[Excel] 文件被占用，{1.5 * (attempt + 1):.1f}s 后重试…")
                    _t.sleep(1.5 * (attempt + 1))
                continue
        alt = self.path.with_name(f"{self.path.stem}_{_dt.now():%Y%m%d_%H%M%S}{self.path.suffix}")
        wb.save(alt)
        self.log(f"[Excel] 原文件被占用，已另存为 {alt.name}（请关闭 Excel 后合并）")
        return str(alt)
