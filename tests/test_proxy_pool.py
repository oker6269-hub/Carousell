# -*- coding: utf-8 -*-
"""代理池（动态 IP 轮换）回归测试。

策略、计数、冷却、持久化，以及引擎在「被站点拒绝」时的换 IP 重试。
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine import CollectorEngine      # noqa: E402
from app.proxy_pool import ProxyPool, mask_proxy   # noqa: E402


def make_pool(n=3, **kw):
    d = tempfile.mkdtemp(prefix="pool-")
    pool = ProxyPool(path=str(Path(d) / "proxies.json"), log=lambda m: None)
    pool.add([f"socks5://u:p@10.0.0.{i}:1080" for i in range(1, n + 1)])
    pool.set_enabled(True)
    for k, v in kw.items():
        pool._data[k] = v
    return pool


class ProxyPoolTest(unittest.TestCase):
    def test_parse_and_dedupe(self):
        pool = make_pool(0)
        added = pool.add(["1.2.3.4:8080", "http://1.2.3.4:8080", "  "])
        self.assertEqual(added, 1)                       # 补协议后重复，只加一条
        self.assertEqual(pool.proxies()[0]["url"], "http://1.2.3.4:8080")

    def test_round_robin_order(self):
        pool = make_pool(3, strategy="round_robin")
        seq = [pool.next()["url"] for _ in range(4)]
        self.assertEqual(len(set(seq[:3])), 3)           # 前三个各不相同
        self.assertEqual(seq[3], seq[0])                 # 第四个回到起点

    def test_least_used(self):
        pool = make_pool(2, strategy="least_used")
        first = pool.next()
        pool.mark_requests(5)
        second = pool.next()
        self.assertNotEqual(first["url"], second["url"])
        third = pool.next()
        self.assertEqual(third["url"], first["url"])     # 回到使用次数少的那条

    def test_rotation_threshold(self):
        pool = make_pool(2)
        pool.set_requests_per_ip(3)
        pool.next()
        self.assertFalse(pool.should_rotate())
        pool.mark_requests(2)
        self.assertFalse(pool.should_rotate())
        pool.mark_requests(1)
        self.assertTrue(pool.should_rotate())

    def test_blocked_proxy_cools_down(self):
        pool = make_pool(2, cooldown_minutes=10)
        first = pool.next()
        pool.mark_blocked("http499")
        p = pool.proxies()[0]
        self.assertGreater(p["cooldown_until"], time.time())
        self.assertEqual(p["fails"], 1)
        self.assertNotEqual(pool.next()["url"], first["url"])   # 冷却中的被跳过
        self.assertEqual(pool.available_count(), 1)

    def test_persistence(self):
        pool = make_pool(2)
        pool.set_requests_per_ip(42)
        pool.next()
        pool.mark_requests(3)
        again = ProxyPool(path=str(pool.path), log=lambda m: None)
        self.assertEqual(again.requests_per_ip, 42)
        self.assertEqual(again.proxies()[0]["used"] + again.proxies()[1]["used"], 3)

    def test_mask_hides_password(self):
        self.assertEqual(mask_proxy("socks5://user:secret@1.2.3.4:1080"),
                         "socks5://user:***@1.2.3.4:1080")

    def test_request_rotation_flag(self):
        pool = make_pool(2)
        pool.next()
        self.assertFalse(pool.should_rotate())
        pool.request_rotation()
        self.assertTrue(pool.should_rotate())


class FakeBrowser:
    """第一次被站点拒绝，换 IP 后恢复正常。"""

    def __init__(self, blocked_reason=None):
        self.blocked_reason = blocked_reason

    def fetch_listings(self, urls, timeout=180):
        if self.blocked_reason:
            return {}
        return {u: {"id": u.strip("/").split("-")[-1], "title": "t", "desc": "",
                    "price": "10", "category": "", "seller": "s", "images": []}
                for u in urls}

    def open_seller(self, url, challenge_timeout=120):
        return True

    def close(self):
        pass


class EngineRotationTest(unittest.TestCase):
    def test_engine_switches_ip_after_block(self):
        eng = CollectorEngine(tempfile.mkdtemp(prefix="eng-ip-"), log=lambda m: None)
        eng.pool = make_pool(2)
        made = []

        def fake_new_browser(proxy):
            b = FakeBrowser("http499" if not made else None)
            made.append(proxy)
            return b

        eng._new_browser = fake_new_browser
        eng._browser = fake_new_browser("first")
        items = [{"id": "111", "path": "/p/a-111/"}, {"id": "222", "path": "/p/b-222/"}]
        eng._collect_seller_items("seller", items, [])

        self.assertFalse(eng.blocked)                  # 换 IP 后恢复了
        self.assertEqual(eng.stats["new"], 2)
        self.assertGreaterEqual(len(made), 2)          # 确实重启了浏览器换了出口
        self.assertEqual(eng.state.status("111"), "done")

    def test_engine_aborts_when_no_ip_left(self):
        eng = CollectorEngine(tempfile.mkdtemp(prefix="eng-ip2-"), log=lambda m: None)
        eng.pool = make_pool(1)
        eng._new_browser = lambda proxy: FakeBrowser("http499")
        eng._browser = FakeBrowser("http499")
        eng._collect_seller_items("seller", [{"id": "333", "path": "/p/c-333/"}], [])
        self.assertTrue(eng.blocked)
        self.assertIsNone(eng.state.status("333"))     # 不算失败，下次重跑继续


if __name__ == "__main__":
    unittest.main()


class ParseFormatTest(unittest.TestCase):
    """代理商常见的 host:port:user:pass 一行一条格式。"""

    def test_host_port_user_pass(self):
        from app.proxy_pool import parse_proxy
        out = parse_proxy("proxy.example.com:443:user-region-HK-sid-abc:pa55w0rd")
        self.assertEqual(out, "http://user-region-HK-sid-abc:pa55w0rd@proxy.example.com:443")

    def test_host_port_only(self):
        from app.proxy_pool import parse_proxy
        self.assertEqual(parse_proxy("1.2.3.4:8080"), "http://1.2.3.4:8080")

    def test_scheme_passthrough(self):
        from app.proxy_pool import parse_proxy
        self.assertEqual(parse_proxy("socks5://u:p@h:1080"), "socks5://u:p@h:1080")

    def test_blank_and_comment(self):
        from app.proxy_pool import parse_proxy
        self.assertEqual(parse_proxy(""), "")
        self.assertEqual(parse_proxy("# 注释"), "")
