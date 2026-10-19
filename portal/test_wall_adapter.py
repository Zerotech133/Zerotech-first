# -*- coding: utf-8 -*-
"""F4 观点墙适配器测试：wall.json schema 键齐全、幂等、坏输入 fail-visible、
真实落盘 wall.json 与契约一致。全程用 tmp 替身文件，禁碰真实数据。"""
import json
import os
import unittest

try:
    from portal import wall_adapter as wa
except ImportError:
    import wall_adapter as wa

REAL_WALL = os.path.join(wa.REPO_ROOT, "portal", "data", "wall.json")

# 最小替身：与 portal/data/todays_picks.json 真实结构同构
PICKS = {
    "generated_at": "2026-10-18 22:56:38",
    "scan_run_date": "2026-10-16",
    "trade_basis": "2026-10-16 收盘（测试替身）",
    "strategies": {
        "strong": {"label": "强势板块领涨", "n": 2, "lead": 1, "observe": 1,
                   "note": "", "ok": True},
        "chanlun": {"label": "缠论信号池", "n": 1, "note": "三类买卖点", "ok": True},
        "linkage": {"label": "板块联动", "n": None, "note": "今日无名单", "ok": True},
    },
    "t1_gate": {"ok": True, "failed": [], "note": ""},
    "counts": {"total": 3},
    "stocks": [
        {"code": "000001.SZ", "name": "甲", "pct_chg": 1.0, "n_hit": 2,
         "sources": [{"s": "strong"}, {"s": "chanlun"}]},
        {"code": "000002.SZ", "name": "乙", "pct_chg": 5.0, "n_hit": 1,
         "sources": [{"s": "strong"}]},
        {"code": "000003.SZ", "name": "丙", "pct_chg": -2.0, "n_hit": 1,
         "sources": [{"s": "strong"}]},
        {"code": "000004.SZ", "name": "丁", "pct_chg": 9.9, "n_hit": 1,
         "sources": [{"s": "strong"}]},
    ],
}
VERDICTS = {"generated_at": "2026-10-18 23:00:00",
            "summary": {"by_strategy": {"strong": {
                "1": {"n": 100, "hit": 50.0, "avg": 1.2, "med": 1.0}}}}}


class WallAdapterTests(unittest.TestCase):
    """契约：schema 键齐全 + 幂等 + fail-visible + 真实墙与 schema 一致。"""

    def setUp(self):
        self.temp = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.picks_path = os.path.join(self.root, "todays_picks.json")
        self.verdicts_path = os.path.join(self.root, "pick_verdicts.json")
        self.out_path = os.path.join(self.root, "wall.json")
        self._write_json(self.picks_path, PICKS)

    def _write_json(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def _run(self, extra=None):
        argv = ["--todays-picks", self.picks_path,
                "--verdicts", self.verdicts_path, "--out", self.out_path]
        if extra:
            argv += extra
        wa.main(argv)
        with open(self.out_path, "rb") as f:
            return f.read()

    # ── schema 键齐全 ──────────────────────────────────────────────

    def test_wall_top_level_keys_complete_and_ordered(self):
        wall = json.loads(self._run())
        self.assertEqual(list(wall.keys()), wa.WALL_KEYS)

    def test_strategist_keys_complete(self):
        wall = json.loads(self._run())
        self.assertTrue(wall["strategists"])
        for card in wall["strategists"]:
            self.assertEqual(list(card.keys()), wa.STRATEGIST_KEYS)
            for row in card["top"]:
                self.assertEqual(sorted(row.keys()),
                                 sorted(["ticker", "name", "score"]))

    def test_generated_at_iso8601_with_tz_and_scan_date(self):
        wall = json.loads(self._run())
        self.assertEqual(wall["generated_at"], "2026-10-18T22:56:38+08:00")
        self.assertEqual(wall["scan_run_date"], "20261016")

    def test_markets_and_committee_line(self):
        wall = json.loads(self._run())
        self.assertEqual(wall["markets"],
                         {"A": "active", "HK": "planned",
                          "US": "planned", "CRYPTO": "planned"})
        self.assertEqual(wall["committee_line"], "")
        self.assertEqual(wall["provenance"]["todays_picks_path"],
                         os.path.abspath(self.picks_path))
        self.assertTrue(wall["provenance"]["missing"])

    def test_strategists_mapped_from_todays_picks(self):
        wall = json.loads(self._run())
        by_id = {c["id"]: c for c in wall["strategists"]}
        # 键序保持 todays_picks.strategies 原序
        self.assertEqual([c["id"] for c in wall["strategists"]],
                         list(PICKS["strategies"].keys()))
        self.assertEqual(by_id["strong"]["picks_count"], 2)
        self.assertEqual(by_id["linkage"]["picks_count"], 0)
        # top 按 n_hit 降序 → pct_chg 降序，TOP_N=3 截断：丙不入
        self.assertEqual([r["ticker"] for r in by_id["strong"]["top"]],
                         ["000001.SZ", "000004.SZ", "000002.SZ"])
        # 无策略师/逐股评分源 → null 绝不编造
        self.assertIsNone(by_id["strong"]["score"])
        self.assertTrue(all(r["score"] is None
                            for c in wall["strategists"] for r in c["top"]))

    def test_verdicts_optional_missing_and_present(self):
        # 缺 verdicts：继续跑 + provenance 记缺失
        wall = json.loads(self._run())
        self.assertTrue(any("pick_verdicts" in m
                            for m in wall["provenance"]["missing"]))
        # 有 verdicts：view 附加考核行，缺失记录消失
        self._write_json(self.verdicts_path, VERDICTS)
        wall = json.loads(self._run())
        self.assertFalse(any("pick_verdicts" in m
                             for m in wall["provenance"]["missing"]))
        strong = next(c for c in wall["strategists"] if c["id"] == "strong")
        self.assertIn("考核 1日 hit 50.0% avg 1.2%", strong["view"])

    # ── 幂等 ──────────────────────────────────────────────────────

    def test_idempotent_same_input_same_bytes(self):
        first = self._run()
        second = self._run()
        self.assertEqual(first, second)

    # ── fail-visible：坏输入非零退出，禁静默出空墙 ────────────────

    def test_missing_todays_picks_fails(self):
        os.remove(self.picks_path)
        with self.assertRaises(SystemExit) as ctx:
            self._run()
        self.assertNotEqual(ctx.exception.code, 0)
        self.assertFalse(os.path.exists(self.out_path))

    def test_bad_json_fails(self):
        with open(self.picks_path, "w", encoding="utf-8") as f:
            f.write("{oops")
        with self.assertRaises(SystemExit) as ctx:
            self._run()
        self.assertNotEqual(ctx.exception.code, 0)

    def test_missing_required_key_fails(self):
        bad = {k: v for k, v in PICKS.items() if k != "t1_gate"}
        self._write_json(self.picks_path, bad)
        with self.assertRaises(SystemExit):
            self._run()
        self.assertFalse(os.path.exists(self.out_path))

    def test_empty_strategies_fails(self):
        bad = dict(PICKS, strategies={})
        self._write_json(self.picks_path, bad)
        with self.assertRaises(SystemExit):
            self._run()

    def test_dry_run_writes_nothing(self):
        wa.main(["--todays-picks", self.picks_path,
                 "--verdicts", self.verdicts_path, "--out", self.out_path,
                 "--dry-run"])
        self.assertFalse(os.path.exists(self.out_path))


@unittest.skipUnless(os.path.isfile(REAL_WALL), "真实 wall.json 未落盘")
class RealWallSchemaTests(unittest.TestCase):
    """真实落盘的 wall.json 与两工位契约一致。"""

    @classmethod
    def setUpClass(cls):
        with open(REAL_WALL, "r", encoding="utf-8") as f:
            cls.wall = json.load(f)

    def test_real_wall_top_keys(self):
        self.assertEqual(list(self.wall.keys()), wa.WALL_KEYS)

    def test_real_wall_strategists(self):
        self.assertTrue(self.wall["strategists"])
        for card in self.wall["strategists"]:
            self.assertEqual(list(card.keys()), wa.STRATEGIST_KEYS)
            self.assertTrue(card["id"])
            for row in card["top"]:
                self.assertEqual(sorted(row.keys()),
                                 sorted(["ticker", "name", "score"]))

    def test_real_wall_fixed_fields(self):
        self.assertEqual(self.wall["markets"],
                         {"A": "active", "HK": "planned",
                          "US": "planned", "CRYPTO": "planned"})
        self.assertEqual(self.wall["committee_line"], "")
        self.assertRegex(self.wall["generated_at"],
                         r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$")
        self.assertRegex(self.wall["scan_run_date"], r"^\d{8}$")
        self.assertTrue(self.wall["provenance"]["todays_picks_path"])
        self.assertTrue(self.wall["provenance"]["generated_from"])
        self.assertTrue(self.wall["provenance"]["missing"])


if __name__ == "__main__":
    unittest.main()
