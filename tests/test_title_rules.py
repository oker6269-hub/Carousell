# -*- coding: utf-8 -*-
"""标题清洗规则回归测试（stdlib unittest，直接 python -m unittest 即可）。

覆盖 v1.4.1 的回归：内置前缀规则把「全新正貨」拆成「正貨」——
根因是罗列式正则先命中「全新」，后面更长的组合词再也匹配不到。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.title_rules import (DEFAULT_RULES, LEGACY_PREFIX_PATTERN,  # noqa: E402
                            TitleRules)


class TitleCleanTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="title-rules-")
        self.path = str(Path(self.dir) / "title_rules.json")
        self.rules = TitleRules(path=self.path, log=lambda m: None)

    def clean(self, raw):
        return self.rules.clean(raw)

    def test_compound_prefix_is_fully_removed(self):
        """全新正貨 / 原裝正貨 这类叠加前缀要一次吃干净。"""
        cases = {
            "全新正貨Zippo煤油火機 富貴唐草 純銅盔甲機": "Zippo煤油火機 富貴唐草 純銅盔甲機",
            "原裝正貨Nike Dunk Low 低幫板鞋": "Nike Dunk Low 低幫板鞋",
            "全新正貨 有盒 New Balance 327": "New Balance 327",
            "全新有盒MLB Chunky Liner 厚底防滑耐磨 休閒.": "MLB Chunky Liner 厚底防滑耐磨 休閒",
            "原裝有盒Zippo煤油火機": "Zippo煤油火機",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(self.clean(raw), expected)

    def test_middle_of_title_is_untouched(self):
        """前缀规则只动开头，中间出现的同名词不能删。"""
        raw = "Zippo 打火機 全新正貨 保證"
        self.assertEqual(self.clean(raw), raw)

    def test_disabled_returns_raw(self):
        self.rules.set_enabled(False)
        raw = "全新正貨Zippo 打火機"
        self.assertEqual(self.clean(raw), raw)

    def test_bad_regex_is_skipped(self):
        # add() 会拒绝非法正则，直接塞进配置模拟手工改坏的情况
        self.rules.set_enabled(True)          # 触发一次落盘，确保文件存在
        data = json.loads(Path(self.path).read_text(encoding="utf-8"))
        data["rules"].append({"pattern": "([unclosed", "replace": "", "enabled": True})
        Path(self.path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        rules = TitleRules(path=self.path, log=lambda m: None)
        self.assertEqual(rules.clean("全新正貨Zippo"), "Zippo")


class MigrationTest(unittest.TestCase):
    def test_legacy_prefix_rule_is_upgraded(self):
        """旧配置里的罗列式前缀规则要被就地升级，且不动用户自定义规则。"""
        d = tempfile.mkdtemp(prefix="title-migrate-")
        path = Path(d) / "title_rules.json"
        path.write_text(json.dumps({
            "enabled": True,
            "rules": [
                {"name": "去掉成色/包裝前綴", "pattern": LEGACY_PREFIX_PATTERN,
                 "replace": "", "enabled": True},
                {"name": "自定义规则", "pattern": "新店特惠", "replace": "", "enabled": True},
            ],
        }, ensure_ascii=False), encoding="utf-8")

        rules = TitleRules(path=str(path), log=lambda m: None)
        patterns = [r["pattern"] for r in rules.rules()]
        self.assertNotIn(LEGACY_PREFIX_PATTERN, patterns)
        self.assertIn(DEFAULT_RULES[0]["pattern"], patterns)
        self.assertIn("新店特惠", patterns)          # 用户规则保留
        self.assertEqual(rules.clean("全新正貨Zippo"), "Zippo")

    def test_order_changes_result(self):
        """规则自上而下执行，顺序不同结果就不同（这就是 全新正貨 那次 bug 的机制）。"""
        d = tempfile.mkdtemp(prefix="title-order-")
        rules = TitleRules(path=str(Path(d) / "r.json"), log=lambda m: None)
        while rules.rules():
            rules.remove(0)
        rules.add("^全新正貨", "[整箱]")     # 具体规则排在前
        rules.add("^全新", "")
        self.assertEqual(rules.clean("全新正貨Zippo"), "[整箱]Zippo")

        rules.move(0, 1)                     # 把具体规则挪到后面
        self.assertEqual(rules.clean("全新正貨Zippo"), "正貨Zippo")


class ReorderTest(unittest.TestCase):
    def test_move_swaps_rules(self):
        d = tempfile.mkdtemp(prefix="title-move-")
        rules = TitleRules(path=str(Path(d) / "r.json"), log=lambda m: None)
        rules.reset()
        rules.add("AAA")
        rules.add("BBB")
        before = [r["pattern"] for r in rules.rules()]
        rules.move(len(before) - 1, -1)
        after = [r["pattern"] for r in rules.rules()]
        self.assertEqual(after[-1], before[-2])
        self.assertEqual(after[-2], before[-1])


if __name__ == "__main__":
    unittest.main()
