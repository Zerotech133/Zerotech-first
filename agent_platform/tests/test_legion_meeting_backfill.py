# -*- coding: utf-8 -*-
"""W42 晨会纪要第④段回填器 legion_meeting_backfill.py 离线测试。

测试纪律（工位红线）：
- 全部使用自建临时夹具（tempfile），零读写 agent_platform/data/ 真实数据、零读写 .worktrees/tasks.json 真源；
- 覆盖：段级三态（OK / INCONCLUSIVE / DEGRADED）、文件级处置三态（待填→已回填 / 已回填幂等 / 失源）、
  出处锚覆盖率、lessons 行尾候选标记、禁创作语汇 grep 机审 + 阳性对照拒载、academy 第五段标题定位、
  dry-run 零写入（closed on failure：失源/异常一律保留「（待填）」占位）。
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import time
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "legion_meeting_backfill.py"
SPEC = importlib.util.spec_from_file_location("legion_meeting_backfill_test", TOOL_PATH)
bf = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bf)

NOW = time.mktime(time.strptime("2026-10-20 17:55:00", "%Y-%m-%d %H:%M:%S"))
OLD = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW - 4 * 86400))

SKEL_DEV = "\n".join([
    "# 研发部 晨会 2026-10-20（主持：ARCH1）",
    "**与会**：DEV7、DEV8、ARCH1",
    "## 一、昨日完成（读复盘卡）",
    "- DEV7（DEV7）：✅ 有任务｜attr_granted",
    "- DEV8（DEV8）：❌ 今日无任务｜—",
    "- ARCH1（ARCH1）：✅ 有任务｜dept_daily_report",
    "## 二、今日任务（排程+派单）",
    "- （无排程/在途单——部长现场派单或成员领单）",
    "## 三、阻塞求助（超期/依赖）",
    "- 无阻塞",
    "## 四、决议与教训（会后人填/提炼器自动回填）",
    "- decisions:（待填）",
    "- lessons:（待填——须可证伪可复用，部务会拍板后入知识环）",
]) + "\n"

SKEL_ACADEMY = (SKEL_DEV
                .replace("研发部", "研究院")
                .replace("2026-10-20_dev_晨会", "2026-10-20_academy_晨会")
                .replace("## 四、决议与教训", "## 五、决议与教训"))


def _tmp():
    return tempfile.TemporaryDirectory()


def make_fixture(root: Path, tasks: dict, cards: dict, summary=None, corps="",
                 skeletons=None) -> Path:
    meet = root / "agent_platform" / "data" / "meetings"
    rev = root / "agent_platform" / "data" / "daily_review" / "2026-10-20"
    (root / ".worktrees").mkdir(parents=True)
    meet.mkdir(parents=True)
    rev.mkdir(parents=True)
    (root / ".worktrees" / "tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    if summary is not None:
        (rev / "_summary.json").write_text(json.dumps(summary, ensure_ascii=False),
                                           encoding="utf-8")
    for aid, text in cards.items():
        (rev / ("%s.md" % aid)).write_text(text, encoding="utf-8")
    if corps:
        (rev / "_corps_daily.md").write_text(corps, encoding="utf-8")
    for name, text in (skeletons or {}).items():
        (meet / name).write_text(text, encoding="utf-8")
    return meet / next(iter(skeletons)) if skeletons else meet / "2026-10-20_dev_晨会.md"


def draft_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def entries_of(text: str) -> list:
    return [ln for ln in text.splitlines() if ln.startswith("  - ")]


def lessons_of(text: str) -> list:
    lines = text.splitlines()
    lidx = lines.index("- lessons:（草稿候选，待部务会拍板后入知识环）")
    return [ln for i, ln in enumerate(lines) if i > lidx and ln.startswith("  - ")]


def test_import_smoke_real_module():
    """import smoke：真实脚本可加载，回填入口与判据复用、禁语汇清单在案。"""
    assert callable(bf.run)
    assert callable(bf.check_tasks_doc)
    assert isinstance(bf.BANNED_PHRASES, tuple) and bf.BANNED_PHRASES


def test_ok_dept_with_warn_and_card():
    """部门级素材 + 履职卡：dry-run 零写入；write 后占位替换、锚覆盖率 100%、
    lessons 候选标记 100%、军团级 WARN 如实标注。"""
    with _tmp() as root:
        root = Path(root)
        path = make_fixture(
            root,
            tasks={
                "K-TEST-001": {"status": "done", "created": "2026-10-19"},  # 军团级空壳 WARN
                "K-TEST-002": {"status": "doing", "created": OLD,
                               "desc": "某任务 [DEV7]"},  # 部门级超期
            },
            cards={"DEV7": "# DEV7 每日复盘 2026-10-20\n\n**③ 我的进化**：固化重复动作为工具。\n"},
            summary={"day": "2026-10-20", "n": 1, "no_task": [],
                     "depts": {"dev": {"n": 1, "no_task": []}}},
            skeletons={"2026-10-20_dev_晨会.md": SKEL_DEV},
        )
        rep = bf.run("2026-10-20", root=root, now=NOW)
        f = rep["files"]["2026-10-20_dev_晨会.md"]
        assert f["disposition"].startswith("待填")
        assert not f["written"]  # dry-run 零写入
        assert "（待填）" in draft_text(path)  # closed on failure：dry-run 原状保留

        rep2 = bf.run("2026-10-20", root=root, now=NOW, write=True)
        f2 = rep2["files"]["2026-10-20_dev_晨会.md"]
        assert f2["written"] and f2["state"] == "DEGRADED"
        # 部门级超期（[DEV7]）与骨架§三「无阻塞」矛盾 → DEGRADED 双列
        text = draft_text(path)
        assert "（待填）" not in text
        assert "草稿 DEGRADED" in text
        assert "[DEGRADED 勾稽冲突]" in text and "K-TEST-002" in text
        assert "骨架§三原文「- 无阻塞」" in text
        ents = entries_of(text)
        assert ents and all(bf.ANCHOR_MARK in ln for ln in ents)  # 锚覆盖率 100%
        assert all(ln.endswith(bf.CANDIDATE_MARK) for ln in lessons_of(text))
        assert "K-TEST-001" in text and "军团级" in text
        assert not [w for w in bf.BANNED_PHRASES if w in text]  # 禁语汇 grep 零命中


def test_inconclusive_zero_material():
    """INCONCLUSIVE 态：源齐但零部门级素材，空集显式声明「非缺勤」（判例 6 空集≠通过）。"""
    with _tmp() as root:
        root = Path(root)
        path = make_fixture(
            root, tasks={}, cards={},
            summary={"day": "2026-10-20", "n": 0, "no_task": [],
                     "depts": {"dev": {"n": 0, "no_task": []}}},
            skeletons={"2026-10-20_dev_晨会.md": SKEL_DEV},
        )
        rep = bf.run("2026-10-20", root=root, now=NOW, write=True)
        assert rep["files"]["2026-10-20_dev_晨会.md"]["state"] == "INCONCLUSIVE"
        text = draft_text(path)
        assert "当日无账本素材（INCONCLUSIVE），非缺勤" in text
        assert not [w for w in bf.BANNED_PHRASES if w in text]


def test_degraded_summary_count_mismatch():
    """DEGRADED 态②：_summary.json n 与个人卡文件数不一致 → 勾稽冲突双列。"""
    with _tmp() as root:
        root = Path(root)
        path = make_fixture(
            root,
            tasks={},
            cards={"DEV7": "# DEV7 每日复盘\n\n**③ 我的进化**：x。\n"},
            summary={"day": "2026-10-20", "n": 5, "no_task": [],
                     "depts": {"dev": {"n": 1, "no_task": []}}},
            skeletons={"2026-10-20_dev_晨会.md": SKEL_DEV},
        )
        rep = bf.run("2026-10-20", root=root, now=NOW, write=True)
        assert rep["files"]["2026-10-20_dev_晨会.md"]["state"] == "DEGRADED"
        assert "_summary.json n=5 vs 个人卡文件数 1" in draft_text(path)


def test_lost_source_keeps_placeholder():
    """失源：tasks.json / 复盘卡链目录不可读 → 失源，骨架「（待填）」原状保留。"""
    with _tmp() as root:
        root = Path(root)
        meet = root / "agent_platform" / "data" / "meetings"
        rev = root / "agent_platform" / "data" / "daily_review" / "2026-10-20"
        (root / "agent_platform" / "data").mkdir(parents=True)
        meet.mkdir()
        rev.mkdir(parents=True)
        (meet / "2026-10-20_dev_晨会.md").write_text(SKEL_DEV, encoding="utf-8")
        rep = bf.run("2026-10-20", root=root, now=NOW)
        assert rep["disposition"] == "失源"
        assert "（待填）" in draft_text(meet / "2026-10-20_dev_晨会.md")
        # 复盘卡链目录缺失同理失源
        (root / ".worktrees").mkdir()
        (root / ".worktrees" / "tasks.json").write_text("{}", encoding="utf-8")
        rep2 = bf.run("2026-10-21", root=root, now=NOW)
        assert rep2["disposition"] == "失源"


def test_idempotent_skip_after_backfill():
    """已回填：二次跑幂等跳过，不改已回填内容。"""
    with _tmp() as root:
        root = Path(root)
        path = make_fixture(
            root, tasks={}, cards={},
            summary={"day": "2026-10-20", "n": 0, "no_task": [], "depts": {}},
            skeletons={"2026-10-20_dev_晨会.md": SKEL_DEV},
        )
        bf.run("2026-10-20", root=root, now=NOW, write=True)
        before = draft_text(path)
        rep = bf.run("2026-10-20", root=root, now=NOW, write=True)
        assert rep["files"]["2026-10-20_dev_晨会.md"]["disposition"] == "已回填"
        assert draft_text(path) == before


def test_academy_fifth_section_located_by_title():
    """防段号漂移：academy 部门「决议与教训」在第五段，按标题定位照常回填。"""
    with _tmp() as root:
        root = Path(root)
        path = make_fixture(
            root, tasks={}, cards={},
            summary={"day": "2026-10-20", "n": 0, "no_task": [],
                     "depts": {"academy": {"n": 0, "no_task": []}}},
            skeletons={"2026-10-20_academy_晨会.md": SKEL_ACADEMY},
        )
        rep = bf.run("2026-10-20", root=root, now=NOW, write=True)
        assert rep["files"]["2026-10-20_academy_晨会.md"]["state"] == "INCONCLUSIVE"
        assert "（待填）" not in draft_text(path)


def test_banned_word_positive_control_rejected():
    """阳性对照（判例 6）：素材含禁语汇 → 条目拒载且全文 grep 零命中、拒载行保锚。"""
    bad = "下阶段" + bf.BANNED_PHRASES[0] + " 落单补 desc"  # 注入清单首位语汇
    with _tmp() as root:
        root = Path(root)
        path = make_fixture(
            root,
            tasks={},
            cards={"DEV7": "# DEV7 每日复盘 2026-10-20\n\n**③ 我的进化**：%s。\n" % bad},
            summary={"day": "2026-10-20", "n": 1, "no_task": [],
                     "depts": {"dev": {"n": 1, "no_task": []}}},
            skeletons={"2026-10-20_dev_晨会.md": SKEL_DEV},
        )
        bf.run("2026-10-20", root=root, now=NOW, write=True)
        text = draft_text(path)
        assert "[拒载]" in text
        assert not [w for w in bf.BANNED_PHRASES if w in text]
        rej = [ln for ln in lessons_of(text) if "[拒载]" in ln]
        assert rej and all(bf.ANCHOR_MARK in ln for ln in rej)


def test_lessons_candidate_from_card_and_corps():
    """lessons 素材：四问卡③缩引（≤44 字符截断）+ no_task 催办事实，行尾候选标记。"""
    with _tmp() as root:
        root = Path(root)
        path = make_fixture(
            root,
            tasks={},
            cards={"DEV7": "# DEV7 每日复盘\n\n**③ 我的进化**：" + "长" * 60 + "。\n"},
            summary={"day": "2026-10-20", "n": 2, "no_task": ["DEV8"],
                     "depts": {"dev": {"n": 2, "no_task": ["DEV8"]}}},
            corps="## dev（ARCH1）\ndev日报：催办 DEV8明日领单。\n",
            skeletons={"2026-10-20_dev_晨会.md": SKEL_DEV},
        )
        bf.run("2026-10-20", root=root, now=NOW, write=True)
        text = draft_text(path)
        ls = lessons_of(text)
        assert any("DEV8" in ln and "催办" in ln for ln in ls)
        card_line = [ln for ln in ls if "DEV7" in ln][0]
        assert "长" * 44 in card_line and "长" * 45 not in card_line  # ≤44 截断口径
        assert all(ln.endswith(bf.CANDIDATE_MARK) for ln in ls)
        assert not [w for w in bf.BANNED_PHRASES if w in text]


def test_zero_auto_finalize_boundary():
    """零自动定稿边界：write=False 恒不落盘；脚本唯一写点=骨架占位替换，knowledge 环零触碰。"""
    with _tmp() as root:
        root = Path(root)
        path = make_fixture(
            root, tasks={}, cards={},
            summary={"day": "2026-10-20", "n": 0, "no_task": [], "depts": {}},
            skeletons={"2026-10-20_dev_晨会.md": SKEL_DEV},
        )
        before = draft_text(path)
        for _ in range(2):
            bf.run("2026-10-20", root=root, now=NOW, write=False)
        assert draft_text(path) == before
    src = TOOL_PATH.read_text(encoding="utf-8")
    # 唯一业务写点 = backfill_meeting 内的骨架占位替换（self-test 夹具写入不计）
    assert src.count('.write_text("\\n".join(new_lines)') == 1
    assert "knowledge.jsonl" not in src  # 知识环入环通道零触碰


def test_self_test_builtin():
    """脚本内置 --self-test 阳性对照自检通过（三态化 + 阳性对照）。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = bf._self_test()
    assert rc == 0
    assert "FAIL" not in buf.getvalue()
