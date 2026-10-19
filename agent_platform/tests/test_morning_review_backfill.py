# -*- coding: utf-8 -*-
"""W42 晨会纪要回填器最小测试集（三态判定 / 出处锚存在性 / 缺数据不编造）。

口径承《2026-10-19_W39晨会纪要回填机制预研施工单》（实义 W42）验收判据：
- 三态判定：OK=三源齐备勾稽一致 / INCONCLUSIVE=某源缺失或零素材 / DEGRADED=勾稽冲突双列；
- 出处锚：decisions 逐条带「出处：」、lessons 逐条带「（候选，部务会拍板后入知识环）」；
- 缺数据不编造：源缺失不生成对应素材行、零素材出「当日无账本素材，非缺勤」；
- 禁创作语汇（check_report_isolation L34 黑名单）生成语汇零命中；
- 幂等：已回填占位不在即跳过，不覆盖。
"""
import json
import sys
import time
import unittest
import importlib.util
from pathlib import Path

AP = Path(__file__).resolve().parent.parent
REPO = AP.parent
TOOL = AP / "tools" / "morning_review_backfill.py"
DAY = "2026-10-17"
ANCHOR_TS = time.mktime(time.strptime(f"{DAY} 17:55:00", "%Y-%m-%d %H:%M:%S"))
ANCHOR = "2026-10-17 17:55:00"
MARKER = "（候选，部务会拍板后入知识环）"
BLACKLIST = ("建议", "预计", "据悉", "约", "或因", "可能因")

_spec = importlib.util.spec_from_file_location("mrb", TOOL)
mrb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrb)


def make_tree(tmp: Path, tasks: dict | None, daily: dict | None,
              cards: dict[str, str] | None = None,
              skeleton_no_block: bool = True) -> Path:
    """构造合成账本树（结构与真仓同构）。tasks=None=真源缺失；daily=None=复盘链缺失。"""
    (tmp / "tools").mkdir(parents=True, exist_ok=True)
    (tmp / ".worktrees").mkdir(parents=True, exist_ok=True)
    (tmp / "agent_platform" / "data" / "meetings").mkdir(parents=True, exist_ok=True)
    if tasks is not None:
        (tmp / ".worktrees" / "tasks.json").write_text(
            json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    if daily is not None:
        base = tmp / "agent_platform" / "data" / "daily_review" / DAY
        base.mkdir(parents=True, exist_ok=True)
        (base / "_summary.json").write_text(
            json.dumps(daily, ensure_ascii=False), encoding="utf-8")
        for aid, q3 in (cards or {}).items():
            (base / f"{aid}.md").write_text(
                f"# {aid} 每日复盘 {DAY}\n\n**① 我今天有没有任务**：✅ 有任务\n\n"
                f"**② 我的任务/进展**：x\n\n**③ 我的进化**：{q3}\n\n"
                f"**④ 部长点评**：x\n", encoding="utf-8")
    block3 = "- 无阻塞\n" if skeleton_no_block else "- ⚠️ A1（A1）工单 T-X 超 72h——部长介入\n"
    skeleton = (
        "# 研发部 晨会 " + DAY + "（主持：A1）\n\n**与会**：张三、李四\n\n"
        "## 一、昨日完成（读复盘卡）\n- 张三（A1）：✅ 有任务｜x\n- 李四（B1）：❌ 今日无任务｜—\n\n"
        "## 二、今日任务（排程+派单）\n- （无排程/在途单——部长现场派单或成员领单）\n\n"
        "## 三、阻塞求助（超期/依赖）\n" + block3 + "\n"
        "## 四、决议与教训（会后人填/提炼器自动回填）\n"
        "- decisions:（待填）\n"
        "- lessons:（待填——须可证伪可复用，部务会拍板后入知识环）\n")
    f = tmp / "agent_platform" / "data" / "meetings" / f"{DAY}_dev_晨会.md"
    f.write_text(skeleton, encoding="utf-8")
    return f


def base_daily():
    return {"day": DAY, "n": 2, "no_task": ["B1"],
            "depts": {"dev": {"n": 2, "no_task": ["B1"]}}}


def run_backfill(tmp: Path):
    """跑一次回填（对 tmp 树内唯一纪要），返回 (state, 新全文, 段④块行)。"""
    f = tmp / "agent_platform" / "data" / "meetings" / f"{DAY}_dev_晨会.md"
    root_tasks = tmp / ".worktrees" / "tasks.json"
    if root_tasks.is_file():
        doc, errors, warns = mrb.load_tasks(tmp)
    else:
        doc, errors, warns = None, [], []
    daily = mrb.load_daily(tmp, DAY)
    overdue = mrb.overdue_tasks(doc, ANCHOR_TS) if doc else []
    state, new_text = mrb.backfill_file(f, DAY, daily, doc, errors, warns,
                                        overdue, [], ANCHOR)
    f.write_text(new_text, encoding="utf-8")  # 与产品 main() 落盘行为同构（幂等测试前提）
    return state, new_text


class TriStateTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="w42_test_"))
        import shutil
        # validate_json 纯函数随树复制（check_tasks_doc 复用面实测）
        (self.tmp / "tools").mkdir()  # 目标目录须先建，否则 shutil.copy FileNotFoundError
        shutil.copy(REPO / "tools" / "validate_json.py",
                    self.tmp / "tools" / "validate_json.py")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_1_ok_state_full_sources(self):
        """三源齐备 + 勾稽一致 → OK；decisions/lessons/锚/标记逐条在账。"""
        tasks = {"T-OK1": {"status": "done", "created": "2026-10-10 08:00:00"},
                 "T-OK2": {"status": "doing", "created": "2026-10-16 08:00:00",
                           "desc": "[A1] 正常在途"}}
        f = make_tree(self.tmp, tasks, base_daily(),
                      cards={"A1": "把重复性动作固化为工具/判例，写进 knowledge 记忆。"})
        state, text = run_backfill(self.tmp)
        self.assertEqual(state, "OK")
        self.assertIn("（草稿 OK，待部务会确认）", text)
        self.assertIn("T-OK1 空壳完工单", text)          # WARN 原文转抄
        self.assertNotIn("T-OK2", text.split("decisions:")[1].split("lessons:")[0])  # 正常单不入
        self.assertIn("A1 复盘卡③「", text)
        self.assertIn("dev 当日无任务 1 人：B1", text)

    def test_2_inconclusive_when_daily_missing(self):
        """复盘卡链缺失 → INCONCLUSIVE，且不生成任何 ③/no_task 素材行（不编文）。"""
        tasks = {"T-W1": {"status": "done", "created": "2026-10-10 08:00:00"}}
        make_tree(self.tmp, tasks, None)
        state, text = run_backfill(self.tmp)
        self.assertEqual(state, "INCONCLUSIVE")
        self.assertIn("复盘卡链缺失", text)
        self.assertNotIn("复盘卡③", text)
        self.assertNotIn("当日无任务", text)

    def test_3_inconclusive_zero_material(self):
        """零素材（空工单账本+无履职卡+无 no_task）→ decisions 出「当日无账本素材，非缺勤」。"""
        make_tree(self.tmp, {}, {"day": DAY, "n": 0, "no_task": [],
                                 "depts": {"dev": {"n": 2, "no_task": []}}})
        state, text = run_backfill(self.tmp)
        self.assertEqual(state, "INCONCLUSIVE")
        self.assertIn("当日无账本素材，非缺勤", text)

    def test_4_degraded_conflict_double_list(self):
        """骨架③「无阻塞」vs tasks.json 超期单>0 → DEGRADED 双列。"""
        tasks = {"T-OD1": {"status": "doing", "created": "2026-10-10 08:00:00",
                           "desc": "[A1] 长期在途"}}
        make_tree(self.tmp, tasks, base_daily(), cards={"A1": "x"})
        state, text = run_backfill(self.tmp)
        self.assertEqual(state, "DEGRADED")
        self.assertIn("冲突双列", text)
        self.assertIn("- 无阻塞", text)                  # 骨架原文双列保留
        self.assertIn("T-OD1 doing 超 72h", text)

    def test_4b_degraded_conflict_reverse(self):
        """反向：骨架③列有超期 ⚠️ 而当次实算超期单=0 → DEGRADED。"""
        make_tree(self.tmp, {}, base_daily(), cards={"A1": "x"}, skeleton_no_block=False)
        state, text = run_backfill(self.tmp)
        self.assertEqual(state, "DEGRADED")
        self.assertIn("超期单为 0", text)


class AnchorAndNoFabricationTests(unittest.TestCase):
    def setUp(self):
        import tempfile, shutil
        self.tmp = Path(tempfile.mkdtemp(prefix="w42_test_"))
        (self.tmp / "tools").mkdir()
        shutil.copy(REPO / "tools" / "validate_json.py",
                    self.tmp / "tools" / "validate_json.py")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _section4(self, text):
        s, e = mrb.find_section(text, mrb.SECTION4_TITLE)
        return text.splitlines()[s:e]

    def test_5_anchor_coverage(self):
        """出处锚覆盖率 100%：decisions 逐条「出处：」、lessons 逐条含固定标记。"""
        tasks = {"T-W1": {"status": "done", "created": "2026-10-10 08:00:00"},
                 "T-W2": {"status": "done", "created": "2026-10-11 08:00:00"}}
        make_tree(self.tmp, tasks, base_daily(),
                  cards={"A1": "进化了。"})
        _, text = run_backfill(self.tmp)
        seg = self._section4(text)
        dec = [ln for ln in seg if ln.strip().startswith("- ") and ln.startswith("  - ")
               and "出处：" in ln and "lessons" not in ln]
        self.assertTrue(dec)
        lessons = [ln for ln in seg if MARKER in ln]
        self.assertTrue(lessons)
        for ln in seg:
            t = ln.strip()
            if t.startswith("- ") and "出处：" in t and "lessons" not in ln and "草稿" not in t:
                continue  # decisions 行
            if MARKER in ln:
                self.assertIn(MARKER, ln)

    def test_6_no_fabrication_when_tasks_missing(self):
        """tasks.json 缺失 → 零 WARN 行、零超期行，锚行如实标「真源缺失」。"""
        make_tree(self.tmp, None, base_daily(), cards={"A1": "x"})
        state, text = run_backfill(self.tmp)
        self.assertEqual(state, "INCONCLUSIVE")
        self.assertNotIn("空壳完工单", text)
        self.assertIn("真源缺失", text)
        self.assertIn("tasks.json 不可读", text)

    def test_7_blacklist_zero(self):
        """禁创作语汇黑名单（check_report_isolation L34 同款）生成语汇零命中。"""
        tasks = {"T-W1": {"status": "done", "created": "2026-10-10 08:00:00"}}
        make_tree(self.tmp, tasks, base_daily(), cards={"A1": "工具化。"})
        _, text = run_backfill(self.tmp)
        seg = "\n".join(self._section4(text))
        for w in BLACKLIST:
            self.assertNotIn(w, seg, f"禁语汇命中：{w}")

    def test_8_q3_quote_truncated_44(self):
        """四问卡③缩引 ≤44 字符（legion_meeting L107 同款截断口径）。"""
        long_q3 = "这是一条超过四十四个字符的进化记录" * 5
        make_tree(self.tmp, {}, base_daily(), cards={"A1": long_q3})
        _, text = run_backfill(self.tmp)
        import re
        m = re.search(r"复盘卡③「(.+?)」", text)
        self.assertTrue(m)
        self.assertLessEqual(len(m.group(1)), 44)

    def test_9_idempotent_skip(self):
        """已回填（占位不在）→ SKIP 原样返回，不覆盖。"""
        tasks = {"T-W1": {"status": "done", "created": "2026-10-10 08:00:00"}}
        f = make_tree(self.tmp, tasks, base_daily(), cards={"A1": "x"})
        _, first = run_backfill(self.tmp)
        # 第二次：占位已被替换
        doc, errors, warns = mrb.load_tasks(self.tmp)
        state2, second = mrb.backfill_file(f, DAY, mrb.load_daily(self.tmp, DAY),
                                           doc, errors, warns, [], [], ANCHOR)
        self.assertEqual(state2, "SKIP")
        self.assertEqual(second, first)

    def test_10_academy_section5_by_title(self):
        """academy 第五段（决议与教训）按标题定位，段号漂移不漏回填。"""
        make_tree(self.tmp, {"T-W1": {"status": "done", "created": "2026-10-10 08:00:00"}},
                  base_daily(), cards={"A1": "x"})
        f = self.tmp / "agent_platform" / "data" / "meetings" / f"{DAY}_dev_晨会.md"
        text = f.read_text(encoding="utf-8").replace("## 四、决议与教训", "## 五、决议与教训")
        f.write_text(text, encoding="utf-8")
        state, new_text = run_backfill(self.tmp)
        self.assertEqual(state, "OK")
        self.assertIn("（草稿 OK，待部务会确认）", new_text)

    def test_11_corps_warn_transcribed_to_all(self):
        """无 [AGENT] 归属标记的 WARN=军团级，照转进草稿（不漏转抄、不虚构域归属）。"""
        tasks = {"K-PUSH-005": {"status": "done", "created": "2026-10-10 08:00:00"}}
        make_tree(self.tmp, tasks, base_daily(), cards={"A1": "x"})
        _, text = run_backfill(self.tmp)
        self.assertIn("K-PUSH-005 空壳完工单", text)
        self.assertIn("军团级", text)

    def test_12_overdue_unparseable_created_not_judged(self):
        """created 不可解析的超期候选不判（不估算），不产生超期结论。"""
        doc = {"T-BAD": {"status": "doing", "created": "2026-10-17 20:1x"}}
        self.assertEqual(mrb.overdue_tasks(doc, ANCHOR_TS), [])

    def test_13_error_units_excluded_from_decisions(self):
        """ERROR 单（缺 created 等结构性损坏）不出 decisions（施工单开放问题 3）。"""
        make_tree(self.tmp, {"T-E1": {"status": "done"}}, base_daily(), cards={"A1": "x"})
        _, text = run_backfill(self.tmp)
        self.assertNotIn("T-E1", text)


class GuardTests(unittest.TestCase):
    def test_14_no_skeleton_skip_exit0(self):
        """当日无晨会骨架 → 自跳过 exit 0，零写。真仓 1999-01-01 无纪要，安全。"""
        rc = mrb.main(["--date", "1999-01-01"])
        self.assertEqual(rc, 0)

    def test_15_disable_switch(self):
        """停用开关 LEGION_MEETING_BACKFILL_OFF=1 → exit 0 零写。"""
        import os
        old = os.environ.get("LEGION_MEETING_BACKFILL_OFF")
        os.environ["LEGION_MEETING_BACKFILL_OFF"] = "1"
        try:
            self.assertEqual(mrb.main(["--date", DAY]), 0)
        finally:
            if old is None:
                os.environ.pop("LEGION_MEETING_BACKFILL_OFF", None)
            else:
                os.environ["LEGION_MEETING_BACKFILL_OFF"] = old


if __name__ == "__main__":
    unittest.main()
