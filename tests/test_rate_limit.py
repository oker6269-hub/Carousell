# -*- coding: utf-8 -*-
"""站点限流处理回归测试。

背景：页面内 fetch 实测在 ~270 条之后被站点掐断（HTTP 499），且此后每个请求
都瞬间失败。旧实现把单条失败静默丢掉，会以全速空跑剩余几百条。这里验证：
失败原因可见、命中限流会冷却重试、冷却无效时上报 blocked_reason、引擎及时中止。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.browser import BrowserSession          # noqa: E402
from app.engine import CollectorEngine          # noqa: E402


def blocked_row(u):
    return {"path": u, "status": 499, "reason": "http499", "d": None}


def ok_row(u):
    return {"path": u, "status": 200, "reason": "ok",
            "d": {"id": "1", "title": "t", "images": [], "price": "1"}}


class FetchBlockTest(unittest.TestCase):
    def make_session(self, rows_fn, logs=None):
        bs = BrowserSession(tempfile.mkdtemp(prefix="bs-"),
                            log=(logs.append if logs is not None else (lambda m: None)),
                            browser_path=None)
        bs._page = lambda: {"webSocketDebuggerUrl": "ws://x"}
        bs._fetch_rows = lambda page, urls, timeout: rows_fn(urls)
        bs._reload_page = lambda page: None
        bs._sleep_interruptible = lambda s: True      # 不真睡
        return bs

    def test_block_sets_reason_and_logs(self):
        logs = []
        bs = self.make_session(lambda urls: [blocked_row(u) for u in urls], logs)
        # 把冷却循环里的固定 sleep 也跳过
        import app.browser as bmod
        orig, bmod.time.sleep = bmod.time.sleep, lambda s: None
        try:
            got = bs.fetch_listings(["u1", "u2"], cooldown=(1, 2))
        finally:
            bmod.time.sleep = orig
        self.assertEqual(got, {})
        self.assertEqual(bs.blocked_reason, "http499")
        self.assertTrue(any("[限流]" in l for l in logs), logs)
        self.assertTrue(any("http499" in l for l in logs))

    def test_recovers_after_cooldown(self):
        calls = {"n": 0}

        def rows_fn(urls):
            calls["n"] += 1
            return [blocked_row(u) for u in urls] if calls["n"] == 1 else [ok_row(u) for u in urls]

        bs = self.make_session(rows_fn)
        import app.browser as bmod
        orig, bmod.time.sleep = bmod.time.sleep, lambda s: None
        try:
            got = bs.fetch_listings(["u1", "u2"], cooldown=(1,))
        finally:
            bmod.time.sleep = orig
        self.assertEqual(len(got), 2)
        self.assertIsNone(bs.blocked_reason)

    def test_404_is_not_treated_as_block(self):
        logs = []
        bs = self.make_session(
            lambda urls: [{"path": u, "status": 404, "reason": "http404", "d": None} for u in urls],
            logs)
        got = bs.fetch_listings(["u1"], retries=0)
        self.assertEqual(got, {})
        self.assertIsNone(bs.blocked_reason)              # 单个 404 不算限流
        self.assertTrue(any("http404" in l for l in logs))  # 但要能看见原因


class EngineAbortTest(unittest.TestCase):
    class FakeBrowser:
        blocked_reason = "http499"

        def fetch_listings(self, urls, timeout=180):
            return {}

    def test_engine_stops_when_blocked(self):
        eng = CollectorEngine(tempfile.mkdtemp(prefix="eng-"), log=lambda m: None)
        eng._browser = self.FakeBrowser()
        items = [{"id": "1", "path": "/p/a-1/"}, {"id": "2", "path": "/p/b-2/"}]
        eng._collect_seller_items("seller", items, [])
        self.assertTrue(eng.blocked)
        # 被限流时不应把 SKU 记成 failed（下次重跑要能继续）
        self.assertIsNone(eng.state.status("1"))
        self.assertIsNone(eng.state.status("2"))


if __name__ == "__main__":
    unittest.main()
