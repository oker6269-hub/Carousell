# -*- coding: utf-8 -*-
"""存储层：SKU 目录 + state.json 断点 + filters.json 类目过滤 + Excel（模板格式）。

Excel 规则（对齐素材保存表格模版.xlsx Sheet1）：
表头 12 列：SKU, BrowserID, ProductNameCn, ProductNameEn, GenderEn, HKPrice,
           SGPrice, MYPrice, Brand, Folder, ShopID, Desc
首行表头 + 数据行追加；重跑按 SKU 去重。
"""
import json
import threading
from pathlib import Path
from typing import Optional

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment

HEADERS = ["SKU", "BrowserID", "ProductNameCn", "ProductNameEn", "GenderEn", "HKPrice",
           "SGPrice", "MYPrice", "Brand", "Folder", "ShopID", "Desc"]
XLSX_NAME = "素材保存表格.xlsx"


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
    """追加式 Excel 记录，按模板 12 列。"""

    def __init__(self, root: str, log=print):
        self.root = Path(root)
        self.path = self.root / XLSX_NAME
        self.log = log
        self._lock = threading.Lock()
        self._ensure()

    def _ensure(self):
        if not self.path.exists():
            wb = Workbook()
            ws = wb.active
            ws.title = "Sheet1"
            ws.append(HEADERS)
            header_font = Font(bold=True, color="FFFFFF")
            header_fill = PatternFill("solid", fgColor="4472C4")
            for i, _ in enumerate(HEADERS, 1):
                c = ws.cell(row=1, column=i)
                c.font = header_font
                c.fill = header_fill
                c.alignment = Alignment(horizontal="center", vertical="center")
            widths = [14, 10, 44, 44, 10, 10, 10, 10, 16, 40, 14, 40]
            for i, w in enumerate(widths, 1):
                ws.column_dimensions[chr(64 + i) if i <= 26 else "A" + chr(64 + i - 26)].width = w
            ws.freeze_panes = "A2"  # 冻结表头
            wb.save(self.path)

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
                ws.append([rec.get(h, "") for h in HEADERS])
                row_idx = ws.max_row
                # Desc 自动换行 + 顶部对齐，便于阅读
                for col in range(1, len(HEADERS) + 1):
                    cell = ws.cell(row=row_idx, column=col)
                    cell.alignment = Alignment(vertical="top",
                                               wrap_text=(HEADERS[col - 1] in ("Desc", "Folder")))
                existing.add(sku)
                added += 1
            wb.save(self.path)
        self.log(f"[Excel] 追加 {added} 行 → {self.path.name}")
