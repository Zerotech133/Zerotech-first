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
        self.minutes_path = os.path.join(self.root, "minutes.json")
        self.linkage_path = os.path.join(self.root, "linkage_shadow.jsonl")
        self.jev_path = os.path.join(self.root, "triples.jsonl")
        self.out_path = os.path.join(self.root, "wall.json")
        self._write_json(self.picks_path, PICKS)

    def _write_json(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def _write_jsonl(self, path, rows):
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def _run(self, extra=None):
        argv = ["--todays-picks", self.picks_path,
                "--verdicts", self.verdicts_path, "--out", self.out_path,
                "--minutes", self.minutes_path,
                "--linkage", self.linkage_path, "--jev", self.jev_path]
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
        # minutes 缺失（可选源）→ 留空 + 记 missing，绝不编造
        self.assertEqual(wall["committee_line"], "")
        self.assertIsNone(wall["provenance"]["committee_source"])
        self.assertTrue(any("committee_line" in m
                            for m in wall["provenance"]["missing"]))
        self.assertEqual(wall["provenance"]["todays_picks_path"],
                         os.path.abspath(self.picks_path))
        self.assertTrue(wall["provenance"]["missing"])

    # ── 投研会一行：minutes.json 可选真源 ─────────────────────────

    MINUTES = {"items": [
        {"id": "min_old", "kind": "post_close",
         "created_at": "2026-10-14T15:41:36", "title": "旧会",
         "body": "**会议结论**：旧的。"},
        {"id": "min_new", "kind": "pre_open",
         "created_at": "2026-10-15T09:22:15", "title": "新会",
         "body": "前文\n**会议结论**：维持 T1/T2 精选执行、T3 只看不动。\n后文"},
    ]}

    def test_committee_line_from_latest_minutes(self):
        self._write_json(self.minutes_path, self.MINUTES)
        wall = json.loads(self._run())
        # 取 created_at 最新一条；展示层署名换算（三国 v1.2 §四）：盘前朝会主持=司马懿·仲达；
        # 结论行原文引用不改写，真署名（title）留痕 provenance
        self.assertEqual(wall["committee_line"],
                         "10-15 盘前朝会｜司马懿·仲达 主持｜维持 T1/T2 精选执行、T3 只看不动。")
        src = wall["provenance"]["committee_source"]
        self.assertTrue(src["source"].endswith("minutes.json"))
        self.assertEqual(src["meeting_id"], "min_new")
        self.assertEqual(src["kind"], "pre_open")
        self.assertEqual(src["host"], "司马懿·仲达")
        self.assertEqual(src["title"], "新会")
        self.assertFalse(any("committee_line" in m
                             for m in wall["provenance"]["missing"]))

    def test_committee_unknown_kind_no_host(self):
        # 未知会议类型 → 不猜主持（灰态不编造）：仅日期+类型+结论，host 为空
        self._write_json(self.minutes_path, {"items": [
            {"id": "m1", "kind": "weekly", "created_at": "2026-10-15T09:00:00",
             "title": "周会", "body": "**会议结论**：周结论。"}]})
        wall = json.loads(self._run())
        self.assertEqual(wall["committee_line"], "10-15 weekly｜周结论。")
        src = wall["provenance"]["committee_source"]
        self.assertEqual(src["host"], "")
        self.assertEqual(src["kind"], "weekly")

    def test_minutes_without_conclusion_line(self):
        self._write_json(self.minutes_path, {"items": [
            {"id": "m1", "kind": "pre_open", "created_at": "2026-10-15T09:00:00",
             "title": "无结论段", "body": "只有正文没有结论行"}]})
        wall = json.loads(self._run())
        self.assertEqual(wall["committee_line"], "")
        self.assertIsNone(wall["provenance"]["committee_source"])
        self.assertTrue(any("会议结论" in m
                            for m in wall["provenance"]["missing"]))

    def test_bad_minutes_json_fails(self):
        # minutes 存在但坏 JSON → fail-visible 非零退出（与 verdicts 同语义）
        with open(self.minutes_path, "w", encoding="utf-8") as f:
            f.write("{oops")
        with self.assertRaises(SystemExit) as ctx:
            self._run()
        self.assertNotEqual(ctx.exception.code, 0)
        self.assertFalse(os.path.exists(self.out_path))

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

    # ── G1 自有产物：linkage 影子源三态（有/无/坏） ────────────────

    LINKAGE_ROWS = [
        {"ref_date": "2026-10-13", "signal_name": "抗跌", "code": "600001.SH"},
        {"ref_date": "2026-10-13", "signal_name": "抗跌", "code": "600002.SH"},
        {"ref_date": "2026-10-13", "signal_name": "滞涨(弱于板块)",
         "code": "301216.SZ"},
    ]

    def test_linkage_missing_records_missing(self):
        # 无产物：继续跑，记 missing，既有 linkage 卡不动
        wall = json.loads(self._run())
        self.assertTrue(any("linkage view" in m
                            for m in wall["provenance"]["missing"]))
        linkage = next(c for c in wall["strategists"] if c["id"] == "linkage")
        self.assertEqual(linkage["picks_count"], 0)

    def test_linkage_view_merged_into_existing_card(self):
        # 有产物：既有 linkage 卡（n=0）view 补实数，合并不重复建卡
        self._write_jsonl(self.linkage_path, self.LINKAGE_ROWS)
        wall = json.loads(self._run())
        ids = [c["id"] for c in wall["strategists"]]
        self.assertEqual(ids.count("linkage"), 1)
        linkage = next(c for c in wall["strategists"] if c["id"] == "linkage")
        # picks_count 不动（个股级工具不产名单）
        self.assertEqual(linkage["picks_count"], 0)
        # 最近信号日 + 五信号命中计数（计数降序→名称升序，全确定性）
        self.assertIn("最近信号日 2026-10-13：五信号命中 3 条", linkage["view"])
        self.assertIn("抗跌 2", linkage["view"])
        self.assertIn("滞涨(弱于板块) 1", linkage["view"])
        self.assertIn("观察池口径", linkage["view"])
        self.assertFalse(any("linkage view" in m
                             for m in wall["provenance"]["missing"]))

    def test_linkage_empty_file_records_missing(self):
        # 空产物（零字节/无记录）→ 与缺文件同语义：记 missing 继续跑
        open(self.linkage_path, "w", encoding="utf-8").close()
        wall = json.loads(self._run())
        self.assertTrue(any("linkage view" in m
                            for m in wall["provenance"]["missing"]))

    def test_linkage_bad_jsonl_fails(self):
        # 坏产物：fail-visible 非零退出，禁静默吞
        with open(self.linkage_path, "w", encoding="utf-8") as f:
            f.write("{oops\n")
        with self.assertRaises(SystemExit) as ctx:
            self._run()
        self.assertNotEqual(ctx.exception.code, 0)
        self.assertFalse(os.path.exists(self.out_path))

    def test_linkage_non_object_line_fails(self):
        with open(self.linkage_path, "w", encoding="utf-8") as f:
            f.write("[1,2]\n")
        with self.assertRaises(SystemExit) as ctx:
            self._run()
        self.assertNotEqual(ctx.exception.code, 0)

    # ── G1 自有产物：jev 复核门三态（有/无/坏） ───────────────────

    JEV_ROWS = [
        {"pool_date": "2026-10-15", "status": "reviewed", "decision": "PASS"},
        {"pool_date": "2026-10-15", "status": "reviewed", "decision": "FOCUS"},
        {"pool_date": "2026-10-15", "status": "reviewed", "decision": "REJECT"},
        {"pool_date": "2026-10-16", "status": "pending", "decision": None},
    ]

    def test_jev_missing_records_missing(self):
        # 无产物：继续跑，记 missing，strategists 无 jev 卡
        wall = json.loads(self._run())
        self.assertTrue(any("jev 卡" in m
                            for m in wall["provenance"]["missing"]))
        self.assertFalse(any(c["id"] == "jev" for c in wall["strategists"]))

    def test_jev_card_appended_with_review_stats(self):
        # 有产物：追加 jev 新卡（STRATEGIST_KEYS 同构），top 留空不冒充选股
        self._write_jsonl(self.jev_path, self.JEV_ROWS)
        wall = json.loads(self._run())
        jev = next(c for c in wall["strategists"] if c["id"] == "jev")
        self.assertEqual(list(jev.keys()), wa.STRATEGIST_KEYS)
        self.assertEqual(jev["top"], [])
        self.assertIsNone(jev["score"])
        self.assertEqual(jev["picks_count"], 3)      # 最近复核日 3 只
        # 最近复核通过率 + 日期 + 否决数（通过=非 REJECT）
        self.assertIn("最近复核 10-15：复核 3 只，门通过 2 只（通过率 66.7%）",
                      jev["view"])
        self.assertIn("否决 1 只", jev["view"])
        # 其后候选池存在但未复核 → 如实标注
        self.assertIn("2026-10-16 起候选未复核", jev["view"])
        self.assertIn("影子·只标注不拦截", jev["view"])
        self.assertFalse(any("jev 卡" in m
                             for m in wall["provenance"]["missing"]))

    def test_jev_no_reviewed_records(self):
        # 全是未复核 → 无 jev 卡，记 missing 继续跑，绝不编造
        self._write_jsonl(self.jev_path, [
            {"pool_date": "2026-10-16", "status": "pending",
             "decision": None}])
        wall = json.loads(self._run())
        self.assertFalse(any(c["id"] == "jev" for c in wall["strategists"]))
        self.assertTrue(any("无已复核记录" in m
                            for m in wall["provenance"]["missing"]))

    def test_jev_bad_jsonl_fails(self):
        # 坏产物：fail-visible 非零退出
        with open(self.jev_path, "w", encoding="utf-8") as f:
            f.write("{oops\n")
        with self.assertRaises(SystemExit) as ctx:
            self._run()
        self.assertNotEqual(ctx.exception.code, 0)
        self.assertFalse(os.path.exists(self.out_path))

    def test_jev_empty_file_records_missing(self):
        open(self.jev_path, "w", encoding="utf-8").close()
        wall = json.loads(self._run())
        self.assertTrue(any("jev 卡" in m
                            for m in wall["provenance"]["missing"]))
        self.assertFalse(any(c["id"] == "jev" for c in wall["strategists"]))

    # ── 幂等 ──────────────────────────────────────────────────────

    def test_idempotent_same_input_same_bytes(self):
        first = self._run()
        second = self._run()
        self.assertEqual(first, second)

    def test_idempotent_with_linkage_jev_same_bytes(self):
        # 带自有产物跑两次：接卡后输出仍字节一致
        self._write_jsonl(self.linkage_path, self.LINKAGE_ROWS)
        self._write_jsonl(self.jev_path, self.JEV_ROWS)
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
        # 投研会一行：有真实纪要结论 → 非空且带出处（committee_source）
        self.assertTrue(self.wall["committee_line"])
        src = self.wall["provenance"]["committee_source"]
        self.assertTrue(src and src["meeting_id"] and src["meeting_at"])
        self.assertRegex(self.wall["generated_at"],
                         r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$")
        self.assertRegex(self.wall["scan_run_date"], r"^\d{8}$")
        self.assertTrue(self.wall["provenance"]["todays_picks_path"])
        self.assertTrue(self.wall["provenance"]["generated_from"])
        self.assertTrue(self.wall["provenance"]["missing"])

    def test_real_wall_linkage_jev_cards(self):
        # G1 自有产物接卡：id 不重复；linkage 卡 view 有实数；jev 卡同构
        ids = [c["id"] for c in self.wall["strategists"]]
        self.assertEqual(len(ids), len(set(ids)))
        linkage = next((c for c in self.wall["strategists"]
                        if c["id"] == "linkage"), None)
        if linkage is not None:
            # 真实 linkage_shadow.jsonl 存在 → view 已补实数（最近信号日口径）
            self.assertTrue(linkage["view"])
            self.assertIn("最近信号日", linkage["view"])
            self.assertEqual(linkage["picks_count"], 0)
        jev = next((c for c in self.wall["strategists"]
                    if c["id"] == "jev"), None)
        if jev is not None:
            self.assertEqual(list(jev.keys()), wa.STRATEGIST_KEYS)
            self.assertEqual(jev["top"], [])
            self.assertIsNone(jev["score"])
            self.assertIn("最近复核", jev["view"])


if __name__ == "__main__":
    unittest.main()
