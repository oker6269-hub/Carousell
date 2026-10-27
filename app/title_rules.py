# -*- coding: utf-8 -*-
"""标题清洗规则：用正则从商品标题里删掉多余信息（可配置、持久化）。

配置位置：~/.carousell-collector/title_rules.json（全局，跨 root 复用）
结构：
    {
      "enabled": true,
      "rules": [
        {"name": "去掉成色/包裝前綴", "pattern": "^(?:原裝有盒|全新有盒)\\s*",
         "replace": "", "enabled": true}
      ]
    }

规则按顺序逐条 re.sub 到标题上，最后合并多余空格并去掉首尾空白。
正则写错不会中断采集：会跳过该条并写日志。
"""
import json
import re
import threading
from pathlib import Path
from typing import Callable, Optional

# 旧版默认规则（用于配置迁移）
LEGACY_PREFIX_PATTERN = r"^\s*(?:原裝有盒|全新有盒|原裝|全新|二手|近全新|有盒|無盒|冇盒)\s*"

DEFAULT_RULES = [
    {
        # 用「重复组」而不是罗列组合：全新正貨 / 原裝有盒 / 全新有盒 都能一次吃干净，
        # 否则单条 全新 先命中会把 全新正貨 拆成 正貨（v1.4.x 的 bug）
        "name": "去掉成色/包裝前綴（可疊加：全新正貨、原裝有盒…）",
        "pattern": r"^\s*(?:(?:原裝|全新|近全新|二手|九成新|正貨|正品|有盒|無盒|冇盒|未拆|未開封)\s*)+",
        "replace": "",
    },
    {
        "name": "去掉結尾標點",
        "pattern": r"[\s.。,，、·;；:：!！?？]+$",
        "replace": "",
    },
]


class TitleRules:
    """标题清洗规则的读写与执行。"""

    def __init__(self, path: Optional[str] = None, log: Callable[[str], None] = print):
        self.path = Path(path) if path else Path.home() / ".carousell-collector" / "title_rules.json"
        self.log = log
        self._lock = threading.Lock()
        self._data = {
            "enabled": True,
            "rules": [dict(r, enabled=True) for r in DEFAULT_RULES],
        }
        self._load()

    # ---------- 读写 ----------

    def _load(self):
        try:
            if not self.path.exists():
                return
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            self.log(f"[标题清洗] 配置读取失败，使用默认规则: {e}")
            return
        if not isinstance(data, dict):
            return
        rules = data.get("rules")
        if isinstance(rules, list):
            cleaned = []
            for r in rules:
                if isinstance(r, dict) and str(r.get("pattern", "")).strip():
                    cleaned.append({
                        "name": str(r.get("name", ""))[:60],
                        "pattern": str(r["pattern"]),
                        "replace": str(r.get("replace", "")),
                        "enabled": bool(r.get("enabled", True)),
                    })
            self._data["rules"] = cleaned
        self._data["enabled"] = bool(data.get("enabled", True))
        if self._migrate_legacy_prefix():
            self._save()

    def _migrate_legacy_prefix(self) -> bool:
        """把旧默认前缀规则升级成可叠加版本（只改这一条，不动用户规则）。"""
        for r in self._data.get("rules", []):
            if r.get("pattern") == LEGACY_PREFIX_PATTERN:
                r["pattern"] = DEFAULT_RULES[0]["pattern"]
                r["name"] = DEFAULT_RULES[0]["name"]
                self.log("[标题清洗] 已升级内置前缀规则（支持 全新正貨 这类叠加词）")
                return True
        return False

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        except Exception as e:
            self.log(f"[标题清洗] 配置保存失败: {e}")

    # ---------- 查询 / 修改 ----------

    @property
    def enabled(self) -> bool:
        return bool(self._data.get("enabled", True))

    def set_enabled(self, on: bool):
        with self._lock:
            self._data["enabled"] = bool(on)
            self._save()

    def rules(self) -> list:
        with self._lock:
            return [dict(r) for r in self._data.get("rules", [])]

    def add(self, pattern: str, replace: str = "", name: str = "") -> bool:
        """新增规则；同 pattern 已存在则更新。返回是否成功（正则会校验）。"""
        pattern = (pattern or "").strip()
        if not pattern:
            return False
        try:
            re.compile(pattern)
        except re.error as e:
            self.log(f"[标题清洗] 正则无效: {pattern} → {e}")
            return False
        with self._lock:
            for r in self._data["rules"]:
                if r["pattern"] == pattern:
                    r["replace"] = replace or ""
                    r["enabled"] = True
                    self._save()
                    return True
            self._data["rules"].append({
                "name": (name or "自定义规则")[:60],
                "pattern": pattern,
                "replace": replace or "",
                "enabled": True,
            })
            self._save()
            return True

    def remove(self, index: int):
        with self._lock:
            rules = self._data.get("rules", [])
            if 0 <= index < len(rules):
                rules.pop(index)
                self._save()

    def move(self, index: int, delta: int):
        """调整规则顺序：规则自上而下执行，顺序会影响结果。"""
        with self._lock:
            rules = self._data.get("rules", [])
            target = index + delta
            if 0 <= index < len(rules) and 0 <= target < len(rules):
                rules[index], rules[target] = rules[target], rules[index]
                self._save()

    def toggle(self, index: int):
        with self._lock:
            rules = self._data.get("rules", [])
            if 0 <= index < len(rules):
                rules[index]["enabled"] = not rules[index].get("enabled", True)
                self._save()

    def reset(self):
        with self._lock:
            self._data["rules"] = [dict(r, enabled=True) for r in DEFAULT_RULES]
            self._data["enabled"] = True
            self._save()

    # ---------- 执行 ----------

    def clean(self, title: str) -> str:
        """按启用中的规则依次清洗标题；任何异常都退回原标题。"""
        raw = (title or "").strip()
        if not raw or not self.enabled:
            return raw
        out = raw
        for r in self.rules():
            if not r.get("enabled", True):
                continue
            try:
                out = re.sub(r["pattern"], r.get("replace", ""), out)
            except re.error as e:
                self.log(f"[标题清洗] 规则跳过（{r.get('pattern')}）: {e}")
        return re.sub(r"\s{2,}", " ", out).strip()
