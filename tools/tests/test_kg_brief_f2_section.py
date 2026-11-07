# -*- coding: utf-8 -*-
"""F2-20261107 单测：kg_brief ⑦ 舆情内参段（flag 默认关）。

覆盖：
1. flag 关 → _f2_guba_lines 返回 []（日报与现状逐字节一致的前提）；
2. flag 开 + 有数据 → 输出段头 + 「N 家独立来源在说【词】」命中行；
3. flag 开 + 无数据 → 优雅降级（不输出该标的，无异常抛出）；
4. flag 开 + 独立来源 <2 → 不命中；
5. flag 开 + 热点词帖数 <3 → 不命中；
6. guba_sentiment import 失败 → 降级单行；
7. 分区渲染：⑦ 开头行被识别为新 section；
8. _f2_tset_add 去重保序。

不连 Neo4j、不读真实 guba 数据（guba_sentiment 整模块 monkeypatch）。
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
KG_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "quant_backtest"))
if KG_DIR not in sys.path:
    sys.path.insert(0, KG_DIR)

# kg_brief import 时会连 kg_client → kg_schema，均为纯模块定义不触发连接
import kg_brief  # noqa: E402


def _mk_summary(ok=True, words=None, n_posts=10):
    return {
        "ok": ok,
        "code": "600519.SH",
        "n_posts": n_posts,
        "words": words or [{"w": "提价", "n": 5}, {"w": "跌停", "n": 4}],
        "days": [],
        "bb": {"bull": 1, "bear": 2},
        "events": [],
    }


def _mk_posts(users):
    return [{"user": u, "ts": "2026-11-07 10:00:00", "title": "t"} for u in users]


class TestFlagOff(unittest.TestCase):
    def test_flag_off_returns_empty(self):
        """flag 关：任何输入都返回 []——日报字节零变化的前提。"""
        with mock.patch.object(kg_brief, "F2_GUBA_SECTION", False):
            self.assertEqual(kg_brief._f2_guba_lines([("600519.SH", "贵州茅台")]), [])
            self.assertEqual(kg_brief._f2_guba_lines([]), [])


class TestFlagOn(unittest.TestCase):
    def test_hit_line_with_sources(self):
        """flag 开 + 独立来源≥2 + 词≥3帖 → 输出命中行。"""
        fake = mock.MagicMock()
        fake.guba_summary.return_value = _mk_summary()
        fake._load_posts.return_value = _mk_posts(["u1", "u2", "u3"])
        with mock.patch.object(kg_brief, "F2_GUBA_SECTION", True):
            with mock.patch.dict(sys.modules, {"guba_sentiment": fake}):
                out = kg_brief._f2_guba_lines([("600519.SH", "贵州茅台")])
        self.assertTrue(any("家独立来源在说【提价】" in x for x in out))
        self.assertTrue(any(x.startswith("⑦ 舆情内参") for x in out))

    def test_no_data_graceful(self):
        """flag 开 + guba 无数据（ok=False）→ 跳过该标的，不抛异常。"""
        fake = mock.MagicMock()
        fake.guba_summary.return_value = _mk_summary(ok=False)
        with mock.patch.object(kg_brief, "F2_GUBA_SECTION", True):
            with mock.patch.dict(sys.modules, {"guba_sentiment": fake}):
                out = kg_brief._f2_guba_lines([("600519.SH", "贵州茅台")])
        self.assertEqual(len(out), 1)
        self.assertIn("无达标标的", out[0])

    def test_single_source_no_hit(self):
        """独立来源 1（<2）→ 不命中。"""
        fake = mock.MagicMock()
        fake.guba_summary.return_value = _mk_summary()
        fake._load_posts.return_value = _mk_posts(["u1"])
        with mock.patch.object(kg_brief, "F2_GUBA_SECTION", True):
            with mock.patch.dict(sys.modules, {"guba_sentiment": fake}):
                out = kg_brief._f2_guba_lines([("600519.SH", "贵州茅台")])
        self.assertEqual(len(out), 1)
        self.assertIn("无达标标的", out[0])

    def test_word_below_threshold_no_hit(self):
        """热点词帖数 <3 → 不命中。"""
        fake = mock.MagicMock()
        fake.guba_summary.return_value = _mk_summary(words=[{"w": "提价", "n": 2}])
        fake._load_posts.return_value = _mk_posts(["u1", "u2", "u3"])
        with mock.patch.object(kg_brief, "F2_GUBA_SECTION", True):
            with mock.patch.dict(sys.modules, {"guba_sentiment": fake}):
                out = kg_brief._f2_guba_lines([("600519.SH", "贵州茅台")])
        self.assertEqual(len(out), 1)
        self.assertIn("无达标标的", out[0])

    def test_import_fail_graceful(self):
        """guba_sentiment import 失败 → 降级单行。"""
        with mock.patch.object(kg_brief, "F2_GUBA_SECTION", True):
            with mock.patch.dict(sys.modules, {"guba_sentiment": None}):
                # import None → TypeError → 被 except 捕获 → 降级行
                out = kg_brief._f2_guba_lines([("600519.SH", "贵州茅台")])
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("⑦ 舆情内参"))

    def test_empty_targets_line(self):
        """flag 开 + 当日无①②标的 → 单行提示。"""
        with mock.patch.object(kg_brief, "F2_GUBA_SECTION", True):
            out = kg_brief._f2_guba_lines([])
        self.assertEqual(out, ["⑦ 舆情内参(guba, 近7日): flag 开启但当日无①②标的"])


class TestSectionSplit(unittest.TestCase):
    def test_7_marker_starts_new_section(self):
        """⑦ 开头行被分区逻辑识别为新 section（K-STOCK-360 分区兼容）。"""
        L = ["① 结构信号(20261107): 1 个", "  · x", "⑦ 舆情内参(guba, 近7日): 扫描1只 · 命中1只", "  · 600519 贵州茅台: 3 家独立来源在说【提价】", "· " + kg_brief.DISCLAIMER]
        head = "🕸 KG 每日情报简报 20261107"
        secs, cur = [], [head]
        for line in L:
            if line.startswith("· " + kg_brief.DISCLAIMER):
                cur.append(line)
                break
            if len(line) > 1 and line[0] in "①②③④⑤⑥⑦" + "·" and line[1] in " ":
                if cur:
                    secs.append("\n".join(cur))
                cur = [line]
            else:
                cur.append(line)
        if cur:
            secs.append("\n".join(cur))
        joined = [s.split("\n")[0] for s in secs]
        self.assertIn("⑦ 舆情内参(guba, 近7日): 扫描1只 · 命中1只", joined)
        self.assertEqual(len(secs), 3)  # head+① | ⑦ | 免责

    def test_tset_add_dedup(self):
        """_f2_tset_add：去重保序。"""
        lst = []
        kg_brief._f2_tset_add(lst, [{"tc": "600519.SH", "nm": "A"}, {"tc": "000001.SZ", "nm": "B"}])
        kg_brief._f2_tset_add(lst, [{"tc": "600519.SH", "nm": "A"}, {"tc": "300750.SZ", "nm": "C"}])
        self.assertEqual(lst, [("600519.SH", "A"), ("000001.SZ", "B"), ("300750.SZ", "C")])


class TestXqSection(unittest.TestCase):
    """F2-P2-a：xq 内容层接入（flag F2_XQ_SECTION 默认关）。"""

    def test_xq_flag_off_returns_empty(self):
        """flag 关：_f2_xq_lines 恒空 dict。"""
        with mock.patch.object(kg_brief, "F2_XQ_SECTION", False):
            self.assertEqual(kg_brief._f2_xq_lines([("600000.SH", "浦发")]), {})

    def test_xq_flag_on_legacy_data(self):
        """flag 开 + 真库：/S/ 存量仅 1 用户（<2）→ 不命中但查询不炸。"""
        with mock.patch.object(kg_brief, "F2_XQ_SECTION", True):
            r = kg_brief._f2_xq_lines([("300347.SZ", "x")], days=3000)
        self.assertEqual(r, {})  # 1 user < 2，机制正确 → 空

    def test_xq_flag_on_two_users_hit(self):
        """flag 开 + mock sqlite 两用户 → 命中计数。"""
        import sqlite3 as sq
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "x.db")
            con = sq.connect(db)
            con.execute("CREATE TABLE xq_status(status_id TEXT, created_at TEXT, user_id TEXT, target TEXT)")
            con.executemany(
                "INSERT INTO xq_status VALUES(?,?,?,?)",
                [
                    ("1", "2026-11-06 10:00:00", "u1", "/S/SH600000/1"),
                    ("2", "2026-11-06 11:00:00", "u2", "/S/SH600000/2"),
                    ("3", "2026-11-06 12:00:00", "u1", "/S/SH600000/3"),
                ],
            )
            con.commit(); con.close()
            with mock.patch.object(kg_brief, "F2_XQ_SECTION", True), \
                 mock.patch.object(kg_brief, "F2_XQ_DB", db):
                r = kg_brief._f2_xq_lines([("600000.SH", "浦发")], days=7)
        self.assertEqual(r, {"600000.SH": 2})

    def test_guba_line_with_xq_tail(self):
        """双源行：guba 命中 + xq 计数 → 行尾「 / xq N 家」。"""
        fake = mock.MagicMock()
        fake.guba_summary.return_value = _mk_summary()
        fake._load_posts.return_value = _mk_posts(["u1", "u2", "u3"])
        with mock.patch.object(kg_brief, "F2_GUBA_SECTION", True):
            with mock.patch.dict(sys.modules, {"guba_sentiment": fake}):
                out = kg_brief._f2_guba_lines(
                    [("600519.SH", "贵州茅台")], xq_counts={"600519.SH": 5}
                )
        self.assertTrue(any("/ xq 5 家" in x for x in out))

    def test_xq_db_missing_graceful(self):
        """flag 开 + 库缺失 → 空 dict 不抛。"""
        with mock.patch.object(kg_brief, "F2_XQ_SECTION", True), \
             mock.patch.object(kg_brief, "F2_XQ_DB", os.path.join(tempfile.gettempdir(), "no_such_zq.db")):
            r = kg_brief._f2_xq_lines([("600000.SH", "浦发")])
        self.assertEqual(r, {})


class TestAuditFixes(unittest.TestCase):
    """00:56 代码审计轮的防回归用例。"""

    def test_scan_limit_is_14(self):
        """扫描上限=14（①8+②6 全量），源码级断言防回退到 8。"""
        src = open(os.path.join(KG_DIR, "kg_brief.py"), encoding="utf-8").read()
        self.assertEqual(src.count("targets[:14]"), 2)
        self.assertNotIn("targets[:8]", src)
        self.assertIn("min(len(targets), 14)", src)

    def test_section7_after_section6_in_source(self):
        """⑦ 段调用位于 ⑥ 数据健康块之后（BLIND_NOTE 之后、DISCLAIMER 之前）。"""
        src = open(os.path.join(KG_DIR, "kg_brief.py"), encoding="utf-8").read()
        i_blind = src.find('L.append("  " + BLIND_NOTE)')
        i_f2 = src.find("L.extend(_f2_guba_lines(")
        i_disc = src.find('L.append("· " + DISCLAIMER)')
        self.assertGreater(i_blind, 0)
        self.assertGreater(i_f2, i_blind, "⑦ 应在 ⑥（BLIND_NOTE）之后")
        self.assertGreater(i_disc, i_f2, "免责声明应在 ⑦ 之后")

    def test_no_dead_xq_connect_in_guba_crawl(self):
        """guba_crawl._pool_focus_codes 不再含无用的 xueqiu_info.db 连接死代码。"""
        src = open(os.path.join(KG_DIR, "guba_crawl.py"), encoding="utf-8").read()
        i = src.find("def _pool_focus_codes")
        seg = src[i : src.find("def ", i + 10)]
        self.assertNotIn("xueqiu_info.db", seg)


class TestAuditFixes(unittest.TestCase):
    """00:56 代码审计轮的防回归用例。"""

    def test_scan_limit_is_14(self):
        """扫描上限=14（①8+②6 全量），源码级断言防回退到 8。"""
        src = open(os.path.join(KG_DIR, "kg_brief.py"), encoding="utf-8").read()
        self.assertEqual(src.count("targets[:14]"), 2)
        self.assertNotIn("targets[:8]", src)
        self.assertIn("min(len(targets), 14)", src)

    def test_section7_after_section6_in_source(self):
        """⑦ 段调用位于 ⑥ 数据健康块之后（BLIND_NOTE 之后、DISCLAIMER 之前）。"""
        src = open(os.path.join(KG_DIR, "kg_brief.py"), encoding="utf-8").read()
        i_blind = src.find('L.append("  " + BLIND_NOTE)')
        i_f2 = src.find("L.extend(_f2_guba_lines(")
        i_disc = src.find('L.append("· " + DISCLAIMER)')
        self.assertGreater(i_blind, 0)
        self.assertGreater(i_f2, i_blind, "⑦ 应在 ⑥（BLIND_NOTE）之后")
        self.assertGreater(i_disc, i_f2, "免责声明应在 ⑦ 之后")

    def test_no_dead_xq_connect_in_guba_crawl(self):
        """guba_crawl._pool_focus_codes 不再含无用的 xueqiu_info.db 连接死代码。"""
        src = open(os.path.join(KG_DIR, "guba_crawl.py"), encoding="utf-8").read()
        i = src.find("def _pool_focus_codes")
        seg = src[i : src.find("def ", i + 10)]
        self.assertNotIn("xueqiu_info.db", seg)


if __name__ == "__main__":
    unittest.main()
