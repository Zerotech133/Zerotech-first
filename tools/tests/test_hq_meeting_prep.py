# -*- coding: utf-8 -*-
"""HQ 五角色晨会议程准备测试（C2，task-37）。

覆盖：真实数据冒烟、无数据 fail-visible、优先级排序+上限、无候选 fail-visible、
Markdown/JSON 输出、角色提示不伪造、--out 临时文件、到期判断。
全部只读；合成数据写临时目录，不写生产会议/任务。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
TOOL = ROOT / "tools" / "hq_meeting_prep.py"

spec = importlib.util.spec_from_file_location("hq_meeting_prep_under_test", TOOL)
hmp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hmp)


def _run(args, cwd=ROOT):
    p = subprocess.run([PY, "-X", "utf8", str(TOOL), *args], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180)
    return p.returncode, p.stdout or "", p.stderr or ""


def _write_meeting(dirpath: Path, doc: dict) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / ((doc.get("meeting_id") or "hq-20261103-050000") + ".json")
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


def _write_council(dirpath: Path) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "council_new_topics.json").write_text("[]", encoding="utf-8")


def _write_tasks(path: Path, tasks: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")


def _meeting(decisions=None, orders=None, mid="hq-20261103-050000") -> dict:
    return {
        "schema_version": "2.0",
        "meeting_id": mid,
        "created_at": "2026-11-03T05:00:00",
        "type": "morning",
        "status": "done",
        "agenda": [],
        "attendees": ["A0"],
        "transcript": [],
        "decisions": decisions or [],
        "orders": orders or [],
    }


def _red_reports(legion="data-warehouse", status="red") -> dict:
    return {"ok": True, "warnings": [], "legions": [{
        "scope": {"legion": legion},
        "status": status,
        "status_reason": "active_tasks>0",
        "blockers_count": 1,
        "blockers": [{"task_id": "T-1", "desc": "阻塞样例", "status": "parked"}],
        "eta": "2026-11-04",
    }]}


def _synthetic_workspace(tmp: Path) -> dict:
    """4 个候选：未派单 / 红军团已派单 / 到期已派单 / 绿军团无到期。"""
    meetings = tmp / "meetings"
    council = tmp / "council"
    tasks = tmp / "tasks.json"
    _write_council(council)
    _write_tasks(tasks, {"T-1": {"status": "parked", "owner": "DATA1", "desc": "阻塞样例"},
                         "T-9": {"status": "doing", "owner": "DSH-Lead", "desc": "到期单"},
                         "T-10": {"status": "done", "owner": "ACAD1", "desc": "绿军团单"},
                         "T-11": {"status": "todo", "owner": "DATA1", "desc": "红军团工单"}})
    _write_meeting(meetings, _meeting(
        decisions=[
            {"id": "D1", "decision": "研发：未派单的治理议题（最高优先）",
             "owner": "ARCH1", "due": "2026-11-03", "task_id": ""},
            {"id": "D2", "decision": "商业：到期但已派单议题",
             "owner": "DSH-Lead", "due": "2026-11-04", "task_id": "T-9"},
            {"id": "D3", "decision": "分析：绿军团无到期议题",
             "owner": "ACAD1", "due": "2026-11-30", "task_id": "T-10"},
        ],
        orders=[
            {"id": "O1", "title": "数据：红军团已派单工单",
             "owner": "DATA1", "due": "2026-11-20", "task_id": "T-11",
             "dispatch": {"ok": True}},
        ],
    ))
    return {"meetings": meetings, "council": council, "tasks": tasks}


# --------------------------------------------------------------------------- #
# 1. 真实数据冒烟
# --------------------------------------------------------------------------- #
def test_real_data_smoke():
    payload = hmp.build_meeting_prep()
    assert payload["ok"] is True
    assert payload["schema"] == "hq/meeting_prep/v1"
    assert payload["read_only"] is True and payload["no_write"] is True
    assert 1 <= len(payload["topics"]) <= 3
    assert len(payload["agenda_items"]) == len(payload["topics"])
    for t in payload["topics"]:
        for key in ("topic_id", "kind", "title", "owner", "roles_expected",
                    "role_prompts", "evidence_needed", "desired_output", "due",
                    "agenda_items"):
            assert key in t, key
        assert t["topic_id"].startswith("HQ-")
        assert len(t["roles_expected"]) == 5
        assert set(t["role_prompts"]) == set(hmp.ROLE_ORDER)
        assert t["evidence_needed"]
        assert t["agenda_items"]
    assert payload["sources"]["directives"] > 0


# --------------------------------------------------------------------------- #
# 2. 无数据 fail-visible（无真源 → ok=false + rc=2，不伪造空草案）
# --------------------------------------------------------------------------- #
def test_no_source_data_fail_visible(tmp_path):
    empty_meet = tmp_path / "empty_meetings"
    empty_meet.mkdir()
    empty_council = tmp_path / "empty_council"
    empty_council.mkdir()
    empty_tasks = tmp_path / "empty_tasks.json"
    payload = hmp.build_meeting_prep(meetings_dir=empty_meet,
                                     council_dir=empty_council,
                                     tasks_file=empty_tasks)
    assert payload["ok"] is False
    assert payload["error"] == "no_source_data"
    assert payload["topics"] == []
    rc, out, err = _run(["--json", "--meetings-dir", str(empty_meet),
                         "--council-dir", str(empty_council),
                         "--tasks-file", str(empty_tasks)])
    assert rc == 2
    doc = json.loads(out)
    assert doc["ok"] is False and doc["error"] == "no_source_data"
    # 文本模式：stderr 有 FAIL，stdout 有失败说明（不静默成功）
    rc2, out2, err2 = _run(["--meetings-dir", str(empty_meet),
                            "--council-dir", str(empty_council),
                            "--tasks-file", str(empty_tasks)])
    assert rc2 == 2
    assert "生成失败" in out2
    assert "[FAIL]" in err2


# --------------------------------------------------------------------------- #
# 3. 优先级排序 + 上限
# --------------------------------------------------------------------------- #
def test_selection_priority_and_limit(tmp_path):
    ws = _synthetic_workspace(tmp_path)
    payload = hmp.build_meeting_prep(
        meetings_dir=ws["meetings"], council_dir=ws["council"],
        tasks_file=ws["tasks"], reports=_red_reports(), date_str="2026-11-03",
        max_topics=3)
    assert payload["ok"] is True
    topics = payload["topics"]
    assert len(topics) == 3
    # 未派单最高优先；红军团其次；到期/其他最后
    assert "未派单" in topics[0]["priority"]["category"]
    assert "红黄军团" in topics[1]["priority"]["category"]
    # 上限：max_topics=2 → 只有 2 项
    payload2 = hmp.build_meeting_prep(
        meetings_dir=ws["meetings"], council_dir=ws["council"],
        tasks_file=ws["tasks"], reports=_red_reports(), date_str="2026-11-03",
        max_topics=2)
    assert len(payload2["topics"]) == 2
    # CLI 上限参数（真实数据）可跑
    rc, out, _ = _run(["--json", "--max-topics", "2"])
    assert rc == 0
    assert len(json.loads(out)["topics"]) <= 2


# --------------------------------------------------------------------------- #
# 4. 无候选 fail-visible（有真源但无议题，也不能假成功）
# --------------------------------------------------------------------------- #
def test_no_candidates_fail_visible(tmp_path):
    meetings = tmp_path / "meetings"
    council = tmp_path / "council"
    tasks = tmp_path / "tasks.json"
    _write_council(council)
    _write_tasks(tasks, {"T-x": {"status": "doing", "owner": "ACAD1", "desc": "远期单"}})
    _write_meeting(meetings, _meeting(decisions=[
        {"id": "D1", "decision": "分析：绿军团远期议题",
         "owner": "ACAD1", "due": "2026-12-30", "task_id": "T-x"},
    ]))
    payload = hmp.build_meeting_prep(
        meetings_dir=meetings, council_dir=council, tasks_file=tasks,
        reports={"ok": True, "warnings": [], "legions": []},
        date_str="2026-11-03")
    assert payload["ok"] is False
    assert payload["error"] == "no_candidates"


# --------------------------------------------------------------------------- #
# 5. JSON / Markdown 输出
# --------------------------------------------------------------------------- #
def test_json_and_markdown_output():
    rc, out, err = _run(["--json"])
    assert rc == 0 and err.strip() == ""
    doc = json.loads(out)  # stdout 纯 JSON
    assert doc["schema"] == "hq/meeting_prep/v1" and doc["ok"] is True
    rc2, out2, _ = _run(["--markdown"])
    assert rc2 == 0
    assert "# 五角色晨会草案" in out2
    assert "只读草案" in out2
    assert "角色提示" in out2
    # 默认（无参数）= Markdown
    rc3, out3, _ = _run([])
    assert rc3 == 0 and "# 五角色晨会草案" in out3


# --------------------------------------------------------------------------- #
# 6. 角色提示不伪造（prompts 只是提示；无 opinions/结论被伪造）
# --------------------------------------------------------------------------- #
def test_role_prompts_not_fabricated():
    payload = hmp.build_meeting_prep(max_topics=1)
    assert payload["ok"] is True
    topic = payload["topics"][0]
    assert "opinions" not in topic and "role_opinions" not in topic
    for role, prompt in topic["role_prompts"].items():
        assert role in hmp.ROLE_ORDER
        assert "未调用模型、未产生角色意见" in prompt
        assert "RoleOpinion" in prompt
        assert hmp.ROLE_CN[role] in prompt or role in prompt
        # 不得把"角色意见"写死为结论
        assert "position=support" not in prompt
    # --no-prompts：角色清单仍在，prompts 为空（显式未生成，不假装）
    rc, out, _ = _run(["--json", "--no-prompts"])
    assert rc == 0
    doc = json.loads(out)
    assert len(doc["topics"][0]["roles_expected"]) == 5
    assert doc["topics"][0]["role_prompts"] == {}


# --------------------------------------------------------------------------- #
# 7. --out 只写指定文件（临时文件；不写生产会议）
# --------------------------------------------------------------------------- #
def test_out_writes_only_given_file(tmp_path):
    meetings_dir = ROOT / "agent_platform" / "data" / "meetings" / "hq"
    before = sorted(p.name for p in meetings_dir.glob("hq-*.json"))
    out_json = tmp_path / "prep.json"
    rc, out, _ = _run(["--out", str(out_json), "--json"])
    assert rc == 0 and out_json.is_file()
    assert "已写出" in out
    doc = json.loads(out_json.read_text(encoding="utf-8"))
    assert doc["ok"] is True and doc["topics"]
    out_md = tmp_path / "prep.md"
    rc2, _, _ = _run(["--out", str(out_md)])
    assert rc2 == 0 and out_md.is_file()
    assert "# 五角色晨会草案" in out_md.read_text(encoding="utf-8")
    after = sorted(p.name for p in meetings_dir.glob("hq-*.json"))
    assert before == after  # 生产会议目录未被写


# --------------------------------------------------------------------------- #
# 8. 到期判断（overdue / due_soon）
# --------------------------------------------------------------------------- #
def test_due_state_and_red_legion_filter(tmp_path):
    today = date.today()
    meetings = tmp_path / "meetings"
    council = tmp_path / "council"
    tasks = tmp_path / "tasks.json"
    _write_council(council)
    _write_tasks(tasks, {})
    _write_meeting(meetings, _meeting(decisions=[
        {"id": "D1", "decision": "研发：逾期事项",
         "owner": "ARCH1", "due": (today - timedelta(days=1)).isoformat(),
         "task_id": "T-1"},
        {"id": "D2", "decision": "研发：临期事项",
         "owner": "ARCH1", "due": (today + timedelta(days=1)).isoformat(),
         "task_id": "T-2"},
    ]))
    payload = hmp.build_meeting_prep(
        meetings_dir=meetings, council_dir=council, tasks_file=tasks,
        reports={"ok": True, "warnings": [], "legions": []},
        date_str=today.isoformat(), max_topics=2)
    assert payload["ok"] is True
    states = {t["title"]: t["due_state"] for t in payload["topics"]}
    assert states["研发：逾期事项"] == "overdue"
    assert states["研发：临期事项"] == "due_soon"
