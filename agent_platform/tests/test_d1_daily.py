# -*- coding: utf-8 -*-
"""D1 部门日报测试（WO-D1-DAILY-20261101）。

覆盖：成员解析 / 进度计数 / 超期识别 / 坏 created 跳过 / 空任务 /
done 不计超期 / Markdown 结构 / 只读字段与零写盘 / CLI。

纪律：只读生产数据（真实 org.json 只读）；夹具一律落 tmp_path，
不写真实 ``.worktrees/tasks.json``、不写生产台账。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_platform import d1_daily as D  # noqa: E402

NOW = datetime(2026, 11, 1, 12, 0, 0)
REAL_ORG = ROOT / "agent_platform" / "registry" / "org.json"
INVEST_IDS = [
    "T1", "T2", "N1", "F1", "D1", "ST1",
    "WA1", "QS1", "TR1", "WA2", "WA3", "TR2", "TR3",
]


# ----------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------
def _org_doc():
    """最小 invest 部门（3 员）+ 一个同名诱饵部门，验证只读 invest 段。"""
    return {
        "org": "zerobound",
        "departments": [
            {
                "dept": "invest",
                "name": "量化交易部",
                "lead": "D1",
                "agents": ["T1", "T2", "D1"],
                "agent_profiles": [
                    {"agent_id": "T1", "cn_name": "庞统", "role_profile": "t1_bull",
                     "reports_to": "D1"},
                    {"agent_id": "T2", "cn_name": "贾诩", "role_profile": "t2_bear",
                     "reports_to": "D1"},
                    {"agent_id": "D1", "cn_name": "鲁肃", "role_profile": "d1_decision",
                     "reports_to": "A0"},
                ],
            },
            {
                "dept": "dev",
                "name": "研发部",
                "lead": "DEV1",
                "agents": ["DEV1", "T1"],
                "agent_profiles": [
                    {"agent_id": "DEV1", "cn_name": "张辽", "role_profile": "dev"},
                    {"agent_id": "T1", "cn_name": "别部门诱饵", "role_profile": "decoy"},
                ],
            },
        ],
    }


def _write(tmp_path, name, obj):
    path = tmp_path / name
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _fixture(tmp_path, tasks=None):
    """→ (org_path, tasks_path)。"""
    return _write(tmp_path, "org.json", _org_doc()), _write(
        tmp_path, "tasks.json", {} if tasks is None else tasks
    )


def _ts(hours_ago, now=NOW):
    return (now - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _task(owner, status, hours_ago=None, desc="", created=...):
    row = {"owner": owner, "status": status, "desc": desc}
    if created is not ...:
        row["created"] = created
    elif hours_ago is not None:
        row["created"] = _ts(hours_ago)
    return row


def _build(tasks, tmp_path, day=None, now=NOW):
    org_p, tasks_p = _fixture(tmp_path, tasks)
    return D.build_daily(day=day, tasks_path=tasks_p, org_path=org_p, now=now)


# ----------------------------------------------------------------------
# 成员解析
# ----------------------------------------------------------------------
def test_members_from_real_org_registry():
    rows = D.members(REAL_ORG)
    ids = [row["id"] for row in rows]
    assert ids == INVEST_IDS, "成员口径必须是 org.json invest 部门 agents 的声明顺序"
    assert len(rows) == 13
    names = {row["id"]: row["name"] for row in rows}
    assert names["D1"] == "鲁肃"
    assert names["T1"] == "庞统" and names["ST1"] == "程昱"
    assert names["WA1"] == "许褚" and names["TR1"] == "周瑜"
    assert all(row["name"] for row in rows), "13 员都必须解析出中文名"
    assert [row["id"] for row in rows if row["is_lead"]] == ["D1"]
    assert next(row for row in rows if row["id"] == "D1")["reports_to"] == "A0"
    assert next(row for row in rows if row["id"] == "WA2")["reports_to"] == "WA1"


def test_members_scoped_to_invest_department(tmp_path):
    org_p = _write(tmp_path, "org.json", _org_doc())
    rows = D.members(org_p)
    assert [row["id"] for row in rows] == ["T1", "T2", "D1"]
    # 同名 agent 在研发部有诱饵档案——必须取 invest 段的「庞统」
    assert {row["id"]: row["name"] for row in rows}["T1"] == "庞统"


def test_members_name_falls_back_to_persona(tmp_path):
    org = {
        "departments": [
            {
                "dept": "invest",
                "name": "量化交易部",
                "lead": "D1",
                "agents": ["WA9"],
                "agent_profiles": [
                    {"agent_id": "WA9", "persona": {"name": "许褚"}, "role_profile": "watch_monitor"}
                ],
            }
        ]
    }
    rows = D.members(_write(tmp_path, "org.json", org))
    assert rows == [
        {"id": "WA9", "name": "许褚", "role": "watch_monitor",
         "is_lead": False, "reports_to": ""}
    ]


def test_members_missing_org_returns_empty(tmp_path):
    assert D.members(tmp_path / "no-such-org.json") == []


# ----------------------------------------------------------------------
# 进度计数
# ----------------------------------------------------------------------
def test_progress_counts_doing_review_done(tmp_path):
    tasks = {
        "WO-A": _task("T1", "doing", 1),
        "WO-B": _task("T1", "done", 100),
        "WO-C": _task("T2", "review", 2),
        "WO-D": _task("D1", "done", created=None),
        "WO-E": _task("DEV1", "doing", 1),                 # 非本部门 → 不归属
        "WO-F": _task("T1 席位（多头）", "doing", 3),        # ID+分隔符 → T1
        "WO-G": _task("T1", "aborted", 1),                  # 非三态 → 跳过
    }
    rep = _build(tasks, tmp_path)
    prog = rep["progress"]
    assert prog["T1"] == {"doing": 2, "review": 0, "done": 1, "total": 3, "overdue": 0}
    assert prog["T2"] == {"doing": 0, "review": 1, "done": 0, "total": 1, "overdue": 0}
    assert prog["D1"] == {"doing": 0, "review": 0, "done": 1, "total": 1, "overdue": 0}
    assert prog["total"] == {"doing": 2, "review": 1, "done": 2, "total": 5, "overdue": 0}
    assert rep["scope"] == {
        "tasks_total": 7,
        "tasks_attributed": 5,
        "tasks_unattributed": 1,
        "created_unparsable": 0,
    }


def test_progress_accepts_chinese_name_owner(tmp_path):
    tasks = {"WO-H": _task("鲁肃", "doing", 1)}
    rep = _build(tasks, tmp_path)
    assert rep["progress"]["D1"]["doing"] == 1
    assert rep["scope"]["tasks_unattributed"] == 0


# ----------------------------------------------------------------------
# 超期识别
# ----------------------------------------------------------------------
def test_overdue_identifies_doing_and_review_over_24h(tmp_path):
    tasks = {
        "WO-OLD-DOING": _task("T1", "doing", 30, desc="超期 doing"),
        "WO-OLD-REVIEW": _task("T1", "review", 25, desc="超期 review"),
        "WO-EXACT-24H": _task("T1", "doing", 24, desc="恰好 24h 不算"),
        "WO-FRESH": _task("T1", "doing", 23.5, desc="未到期"),
        "WO-DONE-OLD": _task("T2", "done", 100, desc="done 不算超期"),
    }
    rep = _build(tasks, tmp_path)
    rows = rep["overdue"]
    assert [row["id"] for row in rows] == ["WO-OLD-DOING", "WO-OLD-REVIEW"]
    assert [row["age_hours"] for row in rows] == [30.0, 25.0]
    assert rows[0]["owner"] == "T1" and rows[0]["owner_name"] == "庞统"
    assert rows[0]["status"] == "doing" and "超期 doing" in rows[0]["desc"]
    assert rep["progress"]["T1"]["overdue"] == 2
    assert rep["progress"]["T2"]["overdue"] == 0
    assert rep["progress"]["total"]["overdue"] == 2
    assert rep["overdue_hours"] == 24


def test_overdue_skips_unparsable_created(tmp_path):
    tasks = {
        "WO-BAD-1": _task("T1", "doing", created="not-a-date"),
        "WO-BAD-2": _task("T1", "review", created="2026/10/01"),
        "WO-BAD-3": _task("T1", "doing", created=""),
        "WO-BAD-4": _task("T1", "doing"),                   # created 缺失
    }
    rep = _build(tasks, tmp_path)
    assert rep["overdue"] == []
    # 状态照常计入进度（只是超期不可判定）
    assert rep["progress"]["T1"]["doing"] == 3
    assert rep["progress"]["T1"]["review"] == 1
    assert rep["progress"]["T1"]["overdue"] == 0
    assert rep["scope"]["created_unparsable"] == 4
    assert any("不猜" in note for note in rep["notes"])


def test_done_never_counted_as_overdue(tmp_path):
    tasks = {
        "WO-DONE-1": _task("T2", "done", 500),
        "WO-DONE-2": _task("T2", "done", created="2026-01-01"),
    }
    rep = _build(tasks, tmp_path)
    assert rep["overdue"] == []
    assert rep["progress"]["T2"] == {
        "doing": 0, "review": 0, "done": 2, "total": 2, "overdue": 0
    }


def test_accepted_created_formats(tmp_path):
    # tasks.json 三代格式：秒 / 分钟 / 仅日期（当日 00:00 起算）
    tasks = {
        "WO-FMT-MIN": _task("T1", "doing", created="2026-10-31 10:30"),
        "WO-FMT-DAY": _task("T1", "doing", created="2026-10-30"),
        "WO-FMT-ISO": _task("T1", "doing", created="2026-10-31T06:00:00"),
    }
    rep = _build(tasks, tmp_path)
    rows = {row["id"]: row for row in rep["overdue"]}
    assert set(rows) == {"WO-FMT-MIN", "WO-FMT-DAY", "WO-FMT-ISO"}
    assert rows["WO-FMT-MIN"]["age_hours"] == 25.5
    assert rows["WO-FMT-DAY"]["age_hours"] == 60.0
    assert rows["WO-FMT-ISO"]["age_hours"] == 30.0


# ----------------------------------------------------------------------
# 空任务 / 空源
# ----------------------------------------------------------------------
def test_empty_tasks_renders_zero_progress(tmp_path):
    rep = _build({}, tmp_path, day="2026-11-01")
    assert rep["schema"] == "d1_daily/v1"
    assert rep["date"] == "2026-11-01"
    assert rep["overdue"] == []
    assert rep["notes"] == []
    assert rep["scope"]["tasks_total"] == 0
    for mid in ("T1", "T2", "D1"):
        assert rep["progress"][mid] == {
            "doing": 0, "review": 0, "done": 0, "total": 0, "overdue": 0
        }
    assert rep["progress"]["total"]["total"] == 0
    md = D.render_markdown(rep)
    assert "无超期工单。" in md
    assert "| **合计** | — | — | 0 | 0 | 0 | 0 |" in md


def test_missing_sources_degrade_without_crash(tmp_path):
    rep = D.build_daily(
        tasks_path=tmp_path / "no-tasks.json",
        org_path=tmp_path / "no-org.json",
        now=NOW,
    )
    assert rep["schema"] == "d1_daily/v1"
    assert rep["members"] == []
    assert rep["lead"] == "D1" and rep["lead_name"] == "鲁肃"
    assert rep["sources"]["tasks_ok"] is False
    assert rep["sources"]["org_ok"] is False
    assert len(rep["notes"]) == 2
    md = D.render_markdown(rep)
    assert "成员名单缺失" in md
    assert "缺失" in md


# ----------------------------------------------------------------------
# Markdown 结构
# ----------------------------------------------------------------------
def test_markdown_structure(tmp_path):
    tasks = {
        "WO-OLD-1": _task("T1", "doing", 30, desc="旧单 | 含竖线"),
        "WO-OK-1": _task("T2", "done", 2, desc="已完成"),
    }
    rep = _build(tasks, tmp_path, day="2026-11-01")
    md = D.render_markdown(rep)
    assert md.startswith("# D1 部门日报 2026-11-01（鲁肃 → A0）")
    assert "量化交易部（invest）" in md
    assert "部长：D1 鲁肃" in md
    assert "报呈：A0" in md
    assert "| 成员 | 中文名 | 角色 | doing | review | done | 超期 |" in md
    for text in ("庞统", "贾诩", "鲁肃", "t1_bull", "**合计**"):
        assert text in md
    assert "## 二、超期未完成（doing/review 且 >24 小时）" in md
    assert "| WO-OLD-1 | T1 庞统 | doing |" in md
    assert "旧单 丨 含竖线" in md, "摘要里的竖线必须转义，避免表格错列"
    assert "| 含竖线" not in md
    assert "## 三、口径与边界" in md
    assert "只读" in md and "paper_only=true" in md and "read_only=true" in md
    assert md.endswith("\n")


# ----------------------------------------------------------------------
# 只读字段 / 零写盘
# ----------------------------------------------------------------------
def test_report_readonly_contract(tmp_path):
    rep = _build({"WO-A": _task("T1", "doing", 1)}, tmp_path, day="2026-11-01")
    required = {
        "schema", "date", "lead", "lead_name", "members", "progress", "overdue",
        "reports_to", "paper_only", "read_only",
    }
    assert required <= set(rep)
    assert rep["schema"] == "d1_daily/v1"
    assert rep["lead"] == "D1" and rep["lead_name"] == "鲁肃"
    assert rep["reports_to"] == "A0"
    assert rep["paper_only"] is True
    assert rep["read_only"] is True
    assert D.READ_ONLY is True and D.PAPER_ONLY is True
    # 必须可 JSON 序列化（Portal/API 直接消费）
    again = json.loads(json.dumps(rep, ensure_ascii=False))
    assert again["schema"] == rep["schema"]
    assert again["progress"] == rep["progress"]


def test_build_daily_writes_nothing(tmp_path):
    org_p, tasks_p = _fixture(tmp_path, {"WO-A": _task("T1", "review", 40)})

    def snapshot():
        return {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in sorted(tmp_path.iterdir())
        }

    before = snapshot()
    rep = D.build_daily(tasks_path=tasks_p, org_path=org_p, now=NOW)
    D.render_markdown(rep)
    after = snapshot()
    assert before == after, "日报只读：不得改写 tasks.json/org.json，也不得新增文件"
    assert set(after) == {"org.json", "tasks.json"}


def test_default_tasks_path_resolves_worktree_checkout(tmp_path, monkeypatch):
    # 仓库根检出：<root>/.worktrees/tasks.json 存在
    root = tmp_path / "repo"
    (root / ".worktrees").mkdir(parents=True)
    repo_tasks = root / ".worktrees" / "tasks.json"
    repo_tasks.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(D, "ROOT", root)
    assert D._default_tasks_path() == repo_tasks

    # worktree 检出：<repo>/.worktrees/<TID>/ 内没有 tasks.json → 用上级 .worktrees
    wt = tmp_path / "repo" / ".worktrees" / "WO-X"
    wt.mkdir(parents=True)
    repo_tasks.unlink()
    (root / ".worktrees" / "tasks.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(D, "ROOT", wt)
    assert D._default_tasks_path() == root / ".worktrees" / "tasks.json"


# ----------------------------------------------------------------------
# 参数校验 / CLI
# ----------------------------------------------------------------------
def test_bad_day_and_now_rejected(tmp_path):
    org_p, tasks_p = _fixture(tmp_path, {})
    with pytest.raises(D.D1DailyError) as err:
        D.build_daily(day="2026/11/01", tasks_path=tasks_p, org_path=org_p)
    assert err.value.code == "bad_day"
    with pytest.raises(D.D1DailyError) as err:
        D.build_daily(now="nonsense", tasks_path=tasks_p, org_path=org_p)
    assert err.value.code == "bad_now"


def _run_cli(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "agent_platform" / "d1_daily.py"), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_cli_json(tmp_path):
    org_p, tasks_p = _fixture(tmp_path, {})
    res = _run_cli("--json", "--day", "2026-11-01", "--tasks", str(tasks_p),
                   "--org", str(org_p))
    assert res.returncode == 0, res.stderr
    rep = json.loads(res.stdout)
    assert rep["schema"] == "d1_daily/v1"
    assert rep["date"] == "2026-11-01"
    assert len(rep["members"]) == 3
    assert rep["read_only"] is True and rep["paper_only"] is True


def test_cli_markdown_and_bad_day(tmp_path):
    org_p, tasks_p = _fixture(tmp_path, {})
    res = _run_cli("--day", "2026-11-01", "--tasks", str(tasks_p), "--org", str(org_p))
    assert res.returncode == 0, res.stderr
    assert res.stdout.startswith("# D1 部门日报 2026-11-01")
    assert "无超期工单。" in res.stdout

    bad = _run_cli("--day", "2026/11/01", "--tasks", str(tasks_p), "--org", str(org_p))
    assert bad.returncode == 2
    assert "bad_day" in bad.stderr
