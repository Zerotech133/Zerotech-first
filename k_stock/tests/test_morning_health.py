# -*- coding: utf-8 -*-
"""晨批健康自检测试（WO-QT-20261020-019，tmp 全隔离）。

覆盖契约：
  1. 各判据 red / yellow / green 构造用例（含今晨实证场景）；
  2. 整体 status 取最高 severity；
  3. 纯只读：跑完前后目标文件 mtime/大小不变、sqlite 字节不变；
  4. 坏输入 closed on failure（非法日期不按今天算，不抛裸异常）；
  5. fail-open 禁令：证据缺失/解析失败显式落红，不静默当绿。

今晨（2026-10-20）实证用例直接进测试：
  - HK 段无落盘文件但有 note 证据 → **黄**（不是红）；
  - last_run.log 缺 → 红；
  - 断线源超期 → 红；
  - 快照零落单且无灰态说明 → 红。
"""
import json
import os
import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))

import morning_health as MH  # noqa: E402

DATE = "20261020"


def _f(result, check_id):
    """按 check_id 取唯一 finding（判据须 1 条 1 finding，便于精确断言）。"""
    hits = [f for f in result["findings"] if f["check_id"] == check_id]
    assert len(hits) == 1, "check_id=%s 期望恰好 1 条，实际 %d" % (check_id, len(hits))
    return hits[0]


class _Base(unittest.TestCase):
    """构造 tmp 仓骨架，所有路径注入 HealthPaths。"""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="mhealth_"))
        self.repo = self.tmp / "repo"
        self.shadow = self.repo / "agent_platform" / "data" / "shadow"
        self.data = self.repo / "k_stock" / "data"
        self.reports = self.repo / "quant_backtest" / "reports"
        for d in (self.shadow, self.data, self.reports):
            d.mkdir(parents=True, exist_ok=True)
        self.paths = MH.HealthPaths(
            repo_root=self.repo, shadow_dir=self.shadow, kstock_data=self.data,
            reports_dir=self.reports, automation_runs=self.repo / "automation_runs",
            paper_db=self.data / "paper.db",
            automation_log=self.shadow / "automation.log",
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 脚手架 ----
    def w_shadow_log(self, text):
        (self.shadow / "last_run.log").write_text(text, encoding="utf-8")

    def w_ledger(self, lines):
        (self.shadow / "shadow_ledger.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
            encoding="utf-8")

    def w_poc(self, prefix, date, extra=""):
        f = self.data / ("%s%s%s.json" % (prefix, date, extra))
        f.write_text("{}", encoding="utf-8")
        return f

    def w_db(self, with_snapshot_table=True, rows=()):
        con = sqlite3.connect(str(self.data / "paper.db"))
        if with_snapshot_table:
            con.execute("CREATE TABLE decision_snapshots"
                        " (snapshot_id TEXT, captured_at TEXT)")
            for r in rows:
                con.execute("INSERT INTO decision_snapshots VALUES (?,?)", r)
        else:
            con.execute("CREATE TABLE orders (id INTEGER)")
        con.commit()
        con.close()

    def w_feed(self, name, date):
        (self.reports / ("%s.json" % name)).write_text(
            json.dumps({"date": date}), encoding="utf-8")

    # feeds 的路径是相对 repo_root 的（与 DEFAULT_FEEDS 同口径）
    FEED_REL = "quant_backtest/reports/xq.json"
    FEED_GLOB_REL = "quant_backtest/reports/xq_*.json"

    def w_report(self, name, date_iso):
        (self.shadow / ("%s_%s.md" % (name, date_iso))).write_text("x", encoding="utf-8")

    def run_check(self, **kw):
        kw.setdefault("feeds", {})
        kw.setdefault("artifacts", {})
        return MH.check_morning(DATE, paths=self.paths, **kw)

    # ---- 全绿场景脚手架 ----
    def make_all_green(self):
        """把每条判据都喂成绿（供「全绿/仅黄」聚合用例复用）。

        H1 当日 done；H2 四通道当日产物；H3 当日快照；H4 两段当日产物；
        H5 雪球当日；H6 日报当日。
        """
        self.w_shadow_log("shadow day 2026-10-20 done: {'done': 5}\n")
        for pfx in MH.CHANNEL_PREFIXES.values():
            self.w_poc(pfx, DATE, "_080000")
        for _seg, (pfx, _p) in MH.SEGMENTS.items():
            self.w_poc(pfx, DATE, "_080000")
        self.w_db(rows=[("s1", "2026-10-20 07:00:00")])
        self.w_feed("xq", DATE)
        self.w_report("daily_report", "2026-10-20")

    def run_green_cfg(self):
        return {"feeds": {"雪球": (self.FEED_REL, 2)},
                "artifacts": {"日报": "agent_platform/data/shadow/daily_report_*.md"}}


# ---------------------------------------------------------------------------
# H1 影子晨批心跳
# ---------------------------------------------------------------------------

class TestH1ShadowHeartbeat(_Base):
    def test_green_when_today_done(self):
        self.w_shadow_log("===== shadow run start 2026-10-20 03:47:59 =====\n"
                          "shadow day 2026-10-20 done: {'done': 5}\n")
        self.assertEqual(_f(self.run_check(), "H1")["severity"], "green")

    def test_red_when_last_run_log_missing(self):
        """今晨实证：last_run.log 缺 = 红（无心跳证据=调度静默失败嫌疑）。"""
        f = _f(self.run_check(), "H1")
        self.assertEqual(f["severity"], "red")
        self.assertIn("不存在", f["evidence"])

    def test_red_when_no_done_line(self):
        """文件在但无 done 行 = 跑崩了，不是正常。"""
        self.w_shadow_log("===== shadow run start 2026-10-20 03:47:59 =====\n"
                          "[strategy] 600519 产出 4 条\n")
        f = _f(self.run_check(), "H1")
        self.assertEqual(f["severity"], "red")
        self.assertIn("无任何", f["evidence"])

    def test_yellow_when_only_yesterday(self):
        self.w_shadow_log("shadow day 2026-10-19 done: {'done': 5}\n")
        self.assertEqual(_f(self.run_check(), "H1")["severity"], "yellow")

    def test_red_when_two_days_stale(self):
        self.w_shadow_log("shadow day 2026-10-18 done: {'done': 5}\n")
        self.assertEqual(_f(self.run_check(), "H1")["severity"], "red")

    def test_yellow_when_historical_lookback_not_replayable(self):
        """历史回看日：日志最新心跳晚于目标日→ 不可回溯核验 = 黄（非绿非红）。"""
        self.w_shadow_log("shadow day 2026-10-20 done: {'done': 5}\n")
        r = MH.check_morning("20261015", paths=self.paths, feeds={}, artifacts={})
        f = _f(r, "H1")
        self.assertEqual(f["severity"], "yellow")
        self.assertIn("历史回看", f["evidence"])


# ---------------------------------------------------------------------------
# H2 各通道最新 run 时效
# ---------------------------------------------------------------------------

class TestH2ChannelFreshness(_Base):
    def test_all_green_when_every_channel_has_today(self):
        for pfx in MH.CHANNEL_PREFIXES.values():
            self.w_poc(pfx, DATE, "_080000")
        r = self.run_check()
        for ch in MH.CHANNELS:
            self.assertEqual(_f(r, "H2.%s" % ch)["severity"], "green", ch)

    def test_red_when_channel_has_no_product_at_all(self):
        """缺=红（不能当「没这项业务」）。"""
        for pfx in list(MH.CHANNEL_PREFIXES.values())[1:]:
            self.w_poc(pfx, DATE, "_080000")
        r = self.run_check()
        f = _f(r, "H2.qd")
        self.assertEqual(f["severity"], "red")
        self.assertIn("无任何", f["evidence"])

    def test_yellow_when_one_day_stale(self):
        for pfx in MH.CHANNEL_PREFIXES.values():
            self.w_poc(pfx, "20261019", "_080000")
        r = self.run_check()
        for ch in MH.CHANNELS:
            self.assertEqual(_f(r, "H2.%s" % ch)["severity"], "yellow", ch)

    def test_red_when_three_days_stale(self):
        for pfx in MH.CHANNEL_PREFIXES.values():
            self.w_poc(pfx, "20261017", "_080000")
        r = self.run_check()
        for ch in MH.CHANNELS:
            self.assertEqual(_f(r, "H2.%s" % ch)["severity"], "red", ch)

    def test_two_days_stale_is_red_by_default_threshold(self):
        """默认阈值：>2*stale_days(1) = 3 天以上红；2 天档落红（>1 即黄，>2 红）。"""
        for pfx in MH.CHANNEL_PREFIXES.values():
            self.w_poc(pfx, "20261018", "_080000")
        r = self.run_check()
        self.assertEqual(_f(r, "H2.qd")["severity"], "red")

    def test_stale_days_threshold_is_configurable(self):
        """阈值可配：把stale_days 调宽后同一批数据降级为黄/绿。"""
        for pfx in MH.CHANNEL_PREFIXES.values():
            self.w_poc(pfx, "20261018", "_080000")
        r = self.run_check(stale_days=5)
        for ch in MH.CHANNELS:
            self.assertEqual(_f(r, "H2.%s" % ch)["severity"], "yellow", ch)

    def test_yellow_when_filename_has_no_date_stamp(self):
        """无日期戳=不可判定=黄，禁当绿（fail-open 禁令）。"""
        for pfx in MH.CHANNEL_PREFIXES.values():
            (self.data / (pfx + "nodate.json")).write_text("{}", encoding="utf-8")
        r = self.run_check()
        f = _f(r, "H2.qd")
        self.assertEqual(f["severity"], "yellow")
        self.assertIn("无日期戳", f["evidence"])

    def test_prefix_alignment_with_trainee_constants(self):
        """口径对齐 trainee._NON_AI_PREFIXES（读取该常量，不复制字面量）。"""
        sys.path.insert(0, str(HERE / "paper"))
        try:
            import trainee
        except Exception:
            self.skipTest("trainee 不可导入（裁剪部署）")
        for pfx in MH.CHANNEL_PREFIXES.values():
            self.assertIn(pfx, trainee._NON_AI_PREFIXES)

    def test_mtime_tie_broken_deterministically(self):
        """同刻落盘的多个 run 必须有确定性破平（glob 顺序是 FS 返回序）。"""
        for pfx in MH.CHANNEL_PREFIXES.values():
            self.w_poc(pfx, DATE, "_080000")
        newest = MH._newest_prefix_file(self.data, "poc_qd_")
        self.assertEqual(newest.name, "poc_qd_%s_080000.json" % DATE)


# ---------------------------------------------------------------------------
# H3 D-1 决策快照
# ---------------------------------------------------------------------------

class TestH3DecisionSnapshot(_Base):
    def test_green_when_snapshot_exists_today(self):
        self.w_db(rows=[("s1", "2026-10-20 07:10:00")])
        f = _f(self.run_check(), "H3")
        self.assertEqual(f["severity"], "green")

    def test_red_when_zero_snapshot_and_no_gray_note(self):
        """今晨实证：零落单且无灰态说明 = 红（静默失败形态）。"""
        self.w_db(rows=[])
        f = _f(self.run_check(), "H3")
        self.assertEqual(f["severity"], "red")
        self.assertIn("无任何灰态说明", f["evidence"])

    def test_yellow_when_gray_state_note_present(self):
        """灰态=黄且 evidence 必含「灰态无样本」，禁当绿。"""
        self.w_db(rows=[])
        r = MH.check_morning(DATE, paths=self.paths, feeds={}, artifacts={},
                             gray_notes={"snapshot": "1.3灰度未起算，无决策样本"})
        f = _f(r, "H3")
        self.assertEqual(f["severity"], "yellow")
        self.assertIn("灰态无样本", f["evidence"])

    def test_yellow_when_snapshot_table_absent(self):
        """表未落地=未核验=黄（未启用≠已核验通过）。"""
        self.w_db(with_snapshot_table=False)
        f = _f(self.run_check(), "H3")
        self.assertEqual(f["severity"], "yellow")

    def test_red_when_db_missing(self):
        """账本缺失不可当干净 →红，且 check_error 标记。"""
        f = _f(self.run_check(), "H3")
        self.assertEqual(f["severity"], "red")
        self.assertTrue(f.get("check_error"))

    def test_snapshot_of_other_day_not_counted(self):
        """只认当日快照：昨日有今日无 → 仍判红（不能被邻日样本洗绿）。"""
        self.w_db(rows=[("s1", "2026-10-19 07:10:00")])
        f = _f(self.run_check(), "H3")
        self.assertEqual(f["severity"], "red")


# ---------------------------------------------------------------------------
# H4 段完整性（含今晨实证：HK 段无文件但有 note →黄）
# ---------------------------------------------------------------------------

class TestH4SegmentIntegrity(_Base):
    def test_green_when_segment_product_today(self):
        for _, (_pfx, pat) in MH.SEGMENTS.items():
            (self.data / pat.replace("*", DATE)).write_text("{}", encoding="utf-8")
        r = self.run_check()
        for seg in MH.SEGMENTS:
            self.assertEqual(_f(r, "H4.%s" % seg)["severity"], "green", seg)

    def test_yellow_when_no_file_but_note_evidence(self):
        """今晨实证：HK 段灰态零落盘，只有 stdout note → 黄而非红。"""
        r = MH.check_morning(DATE, paths=self.paths, feeds={}, artifacts={},
                             gray_notes={"4e": "4e HK 段灰态：港股未开盘，无样本"})
        f = _f(r, "H4.4e")
        self.assertEqual(f["severity"], "yellow")
        self.assertIn("灰态零落盘", f["evidence"])

    def test_red_when_no_file_and_no_evidence(self):
        """无文件且无任何证据 → 红（这才是静默失败形态）。"""
        r = self.run_check()
        for seg in MH.SEGMENTS:
            f = _f(r, "H4.%s" % seg)
            self.assertEqual(f["severity"], "red", seg)
            self.assertIn("无任何 note 证据", f["evidence"])

    def test_note_evidence_found_in_shadow_ledger(self):
        """note 证据可从 shadow_ledger.jsonl 读到（只读扫尾）。"""
        self.w_ledger([{"date": "2026-10-20", "note": "4e HK 段灰态，无落盘"}])
        r = MH.check_morning(DATE, paths=self.paths, feeds={}, artifacts={})
        f = _f(r, "H4.4e")
        self.assertEqual(f["severity"], "yellow")
        self.assertIn("4e", f["evidence"])

    def test_segment_token_not_matched_inside_hex_run_id(self):
        """回归：run_id 含子串 '4d'（实测 14dad5866f5a）不得被当成 4d 段 note 证据。

        子串匹配会把无关落单洗成「有note 证据」，让该判红的静默失败降级成黄——
        正是本判据要抓的东西，不能自己先漏。
        """
        self.w_ledger([{"date": "2026-10-20", "run_id": "14dad5866f5a",
                        "symbol": "600519", "status": "done"}])
        r = MH.check_morning(DATE, paths=self.paths, feeds={}, artifacts={})
        self.assertEqual(_f(r, "H4.4d")["severity"], "red")

    def test_stale_segment_product_is_yellow(self):
        """有历史产物但无当日 → 黄（可能降频/灰态）。"""
        for _seg, (pfx, _pat) in MH.SEGMENTS.items():
            self.w_poc(pfx, "20261016", "_161000")
        r = self.run_check()
        for seg in MH.SEGMENTS:
            self.assertEqual(_f(r, "H4.%s" % seg)["severity"], "yellow", seg)


# ---------------------------------------------------------------------------
# H5 数据断线（fail-open 禁令核心）
# ---------------------------------------------------------------------------

class TestH5DataFeedDisconnect(_Base):
    def test_red_when_feed_missing_entirely(self):
        """断线必须显式：产物不存在 = 红，绝不当正常。"""
        r = self.run_check(feeds={"雪球": ("reports/xq.json", 2)})
        f = _f(r, "H5.雪球")
        self.assertEqual(f["severity"], "red")
        self.assertIn("不存在", f["evidence"])

    def test_red_when_feed_stale_over_threshold(self):
        """今晨实证：断线源超期 = 红。"""
        self.w_feed("xq", "20261016")
        r = self.run_check(feeds={"雪球": ("reports/xq.json", 2)})
        f = _f(r, "H5.雪球")
        self.assertEqual(f["severity"], "red")
        self.assertIn("断线", f["evidence"])

    def test_green_when_fresh_within_threshold(self):
        self.w_feed("xq", DATE)
        r = self.run_check(feeds={"雪球": (self.FEED_REL, 2)})
        self.assertEqual(_f(r, "H5.雪球")["severity"], "green")

    def test_threshold_boundary_is_inclusive(self):
        """age == max_age 仍算绿（超期才红）。"""
        self.w_feed("xq", "20261018")
        r = self.run_check(feeds={"雪球": (self.FEED_REL, 2)})
        self.assertEqual(_f(r, "H5.雪球")["severity"], "green")

    def test_red_when_json_unparseable(self):
        """解析失败≠正常 → 红 + check_error。"""
        (self.reports / "xq.json").write_text("{not json", encoding="utf-8")
        f = _f(self.run_check(feeds={"雪球": (self.FEED_REL, 2)}), "H5.雪球")
        self.assertEqual(f["severity"], "red")
        self.assertTrue(f.get("check_error"))

    def test_red_when_no_date_field(self):
        (self.reports / "xq.json").write_text(json.dumps({"foo": 1}), encoding="utf-8")
        f = _f(self.run_check(feeds={"雪球": (self.FEED_REL, 2)}), "H5.雪球")
        self.assertEqual(f["severity"], "red")
        self.assertIn("不可判定", f["evidence"])

    def test_glob_pattern_picks_newest_by_business_date(self):
        """glob 形态按业务日期取最新（不是按落盘时间/字典序）。"""
        for d in ("20261010", DATE, "20261016"):
            (self.reports / ("xq_%s.json" % d)).write_text(
                json.dumps({"date": d}), encoding="utf-8")
        r = self.run_check(feeds={"雪球": (self.FEED_GLOB_REL, 2)})
        self.assertEqual(_f(r, "H5.雪球")["severity"], "green")


# ---------------------------------------------------------------------------
# H6 产物新鲜度
# ---------------------------------------------------------------------------

class TestH6ArtifactFreshness(_Base):
    def test_green_when_artifact_today(self):
        self.w_report("daily_report", "2026-10-20")
        r = self.run_check(artifacts={"日报": "agent_platform/data/shadow/daily_report_*.md"})
        self.assertEqual(_f(r, "H6.日报")["severity"], "green")

    def test_yellow_when_artifact_missing_today(self):
        self.w_report("daily_report", "2026-10-19")
        r = self.run_check(artifacts={"日报": "agent_platform/data/shadow/daily_report_*.md"})
        self.assertEqual(_f(r, "H6.日报")["severity"], "yellow")

    def test_yellow_when_artifact_dir_missing(self):
        r = self.run_check(artifacts={"三查": "quant_backtest/reports/nope_*.md"})
        self.assertEqual(_f(r, "H6.三查")["severity"], "yellow")

    def test_dashed_date_names_are_parsed(self):
        """回归：仓内daily_report 用横线日期戳，只认 YYYYMMDD 会把「有产物」
        误判成「无日期戳不可判定」，白挂一个黄。"""
        self.w_report("daily_report", "2026-10-20")
        r = self.run_check(artifacts={"日报": "agent_platform/data/shadow/daily_report_*.md"})
        f = _f(r, "H6.日报")
        self.assertEqual(f["severity"], "green")


# ---------------------------------------------------------------------------
# 汇总 / 严重度 / 只读 / 坏输入
# ---------------------------------------------------------------------------

class TestAggregateAndSeverity(_Base):
    def test_status_takes_max_severity(self):
        r = self.run_check()
        sev = {f["severity"] for f in r["findings"]}
        self.assertIn("red", sev)
        self.assertEqual(r["status"], "red")

    def test_status_green_when_all_checks_green(self):
        self.make_all_green()
        r = MH.check_morning(DATE, paths=self.paths, **self.run_green_cfg())
        bad = [(f["check_id"], f["evidence"]) for f in r["findings"]
               if f["severity"] != "green"]
        self.assertEqual(bad, [], "全绿场景存在非绿判据: %s" % bad)
        self.assertEqual(r["status"], "green")

    def test_status_yellow_when_only_yellow(self):
        """全绿基线 + H6 缺当日日报（黄）→整体黄（status 取最高，非「有黄就红」）。"""
        self.make_all_green()
        (self.shadow / "daily_report_2026-10-20.md").unlink()
        self.w_report("daily_report", "2026-10-19")     # 只剩昨日那期
        r = MH.check_morning(DATE, paths=self.paths, **self.run_green_cfg())
        self.assertEqual(_f(r, "H6.日报")["severity"], "yellow")
        self.assertEqual(r["status"], "yellow")

    def test_summary_counts_add_up(self):
        r = self.run_check()
        s = r["summary"]
        self.assertEqual(s["red"] + s["yellow"] + s["green"], s["total"])
        self.assertEqual(s["total"], len(r["findings"]))

    def test_findings_sorted_stably_by_check_id(self):
        r = self.run_check()
        ids = [f["check_id"] for f in r["findings"]]
        self.assertEqual(ids, sorted(ids))

    def test_every_finding_has_required_fields(self):
        r = self.run_check()
        for f in r["findings"]:
            for k in ("check_id", "severity", "title", "evidence", "action"):
                self.assertIn(k, f)
                self.assertTrue(str(f[k]).strip(), "%s 字段 %s 为空" % (f["check_id"], k))
            self.assertIn(f["severity"], MH.SEVERITY_ORDER)

    def test_dryrun_mode_flag_present(self):
        """DRYRUN 语义：输出里带 mode=dryrun，未接推送。"""
        self.assertEqual(self.run_check()["mode"], "dryrun")


class TestReadOnlyGuarantee(_Base):
    def test_files_untouched_by_full_run(self):
        """跑完前后目标文件mtime/大小/内容不变（纯只读硬保证）。"""
        self.w_shadow_log("shadow day 2026-10-20 done\n")
        self.w_ledger([{"date": "2026-10-20", "symbol": "600519"}])
        self.w_poc("poc_qd_", DATE, "_080000")
        self.w_db(rows=[("s1", "2026-10-20 07:00:00")])
        self.w_feed("xq", DATE)
        self.w_report("daily_report", "2026-10-20")
        targets = [self.shadow / "last_run.log", self.shadow / "shadow_ledger.jsonl",
                   self.data / ("poc_qd_%s_080000.json" % DATE),
                   self.data / "paper.db", self.reports / "xq.json",
                   self.shadow / "daily_report_2026-10-20.md"]
        before = {p: (p.stat().st_mtime_ns, p.stat().st_size,
                      p.read_bytes()) for p in targets}
        self.run_check(feeds={"雪球": (self.FEED_REL, 2)},
                       artifacts={"日报": "agent_platform/data/shadow/daily_report_*.md"})
        for p in targets:
            after = (p.stat().st_mtime_ns, p.stat().st_size, p.read_bytes())
            self.assertEqual(before[p], after, "只读被破坏: %s" % p)

    def test_db_not_created_when_absent(self):
        """库缺失时不得新建文件（mode=ro 契约）。"""
        self.run_check()
        self.assertFalse((self.data / "paper.db").exists())

    def test_query_only_guard_enabled(self):
        con = MH._open_ro(self.data / "paper.db") if (self.data / "paper.db").exists() else None
        if con is None:
            self.w_db()
            con = MH._open_ro(self.data / "paper.db")
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("CREATE TABLE probe_x (a INT)")
        finally:
            con.close()


class TestBadInput(_Base):
    def test_illegal_date_raises_value_error(self):
        for bad in ("2026-13-32", "notadate", "2026102", "", "abcdefgh"):
            with self.assertRaises(ValueError):
                MH.check_morning(bad, paths=self.paths)

    def test_illegal_date_does_not_default_to_today(self):
        """坏输入不许悄悄按今天算（会伪造「今日健康」假绿）。"""
        with self.assertRaises(ValueError):
            MH.check_morning("2026-13-01", paths=self.paths)

    def test_accepted_date_formats(self):
        self.assertEqual(MH.normalize_date("2026-10-20"), "20261020")
        self.assertEqual(MH.normalize_date("20261020"), "20261020")
        self.assertEqual(MH.normalize_date("2026/10/20"), "20261020")

    def test_non_string_date_raises(self):
        for bad in (None, 20261020, [], {}):
            with self.assertRaises(ValueError):
                MH.check_morning(bad, paths=self.paths)

    def test_cli_bad_date_exits_3(self):
        self.assertEqual(MH.main(["--date", "20261332"]), 3)

    def test_cli_exit_codes_reflect_status(self):
        self.w_shadow_log("shadow day 2026-10-20 done\n")
        # 全红场景 → 2
        rc = MH.main(["--date", DATE, "--repo-root", str(self.repo), "--json"])
        self.assertIn(rc, (1, 2))


class TestRendering(_Base):
    def test_markdown_contains_all_severities_and_principles(self):
        """全绿场景 + 人工构造一条黄一条红，确认三色图标都渲染得出。"""
        self.make_all_green()
        r = MH.check_morning(DATE, paths=self.paths, **self.run_green_cfg())
        r["findings"].append({
            "check_id": "H9.demo", "severity": "yellow", "title": "演示黄",
            "evidence": "e", "action": "a"})
        r["findings"].append({
            "check_id": "H8.demo", "severity": "red", "title": "演示红",
            "evidence": "e", "action": "a"})
        r["status"] = "red"
        md = MH.render_markdown(r)
        self.assertIn("晨批健康自检", md)
        self.assertIn("没采到 ≠ 正常", md)
        self.assertIn("灰态 ≠ 绿", md)
        self.assertIn("DRYRUN", md)
        for icon in ("🟢", "🟡", "🔴"):
            self.assertIn(icon, md)
        # 明细按严重度分组，红在最前
        self.assertLess(md.index("演示红"), md.index("演示黄"))

    def test_markdown_real_red_run_lists_every_finding(self):
        md = MH.render_markdown(self.run_check())
        for f in self.run_check()["findings"]:
            self.assertIn(f["check_id"], md)

    def test_json_contract_shape(self):
        d = self.run_check()
        self.assertTrue({"date", "status", "findings", "summary", "mode"} <= set(d))
        json.dumps(d, ensure_ascii=False)          # 必须可序列化

    def test_human_render_lists_only_non_green(self):
        """紧凑输出只列非绿项（H1 绿时不该出现）。"""
        self.make_all_green()
        r = MH.check_morning(DATE, paths=self.paths, **self.run_green_cfg())
        self.assertEqual(r["status"], "green")
        txt = MH.render_human(r)
        self.assertIn("晨批健康自检", txt)
        self.assertIn("全部判据绿", txt)
        self.assertNotIn("H1", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)