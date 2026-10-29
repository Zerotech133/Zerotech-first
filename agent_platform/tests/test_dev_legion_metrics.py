# -*- coding: utf-8 -*-
"""LS-04 聚合层测试：在制/指标/红线；与 API payload 一致性。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))
import dev_legion_aggregate as G  # noqa: E402
import api_dev_legion as A  # noqa: E402


def _tasks():
    return {
        "T-DOING": {"owner": "DEV1", "status": "doing", "desc": "x"},
        "T-REVIEW": {"owner": "DEV5", "status": "review", "desc": "y"},
        "T-DONE": {"owner": "DEV1", "status": "done", "desc": "z"},
        "T-OTHER": {"owner": "ZHAOYUN", "status": "doing", "desc": "q"},
    }


def test_wip_only_active_dev_seats():
    wip = G.build_wip(_tasks(), ["DEV1", "DEV5"])
    owners = {w["owner"] for w in wip}
    assert owners == {"DEV1", "DEV5"}
    dev1 = next(w for w in wip if w["owner"] == "DEV1")
    assert [t["id"] for t in dev1["tasks"]] == ["T-DOING"]


def test_metrics_and_redlines():
    daily = {"window_days": 7, "metrics": {
        "dev_tasks_recent": 26, "dev_merged_recent": 11,
        "dev_capture_share_recent": 0.042, "owner_legal_rate_recent": 0.85,
        "dev_dept_tagged": 34, "dev_dept_with_acceptance": 33,
        "e3_trigger": {"A": {"ok": 1}, "B": {"ok": 1}, "C": {"ok": 1}, "D": {"ok": 1}},
        "dev_leadtime_p50_min": 40.5}}
    m = G.build_metrics(daily)
    assert m["verification_rate"] == round(33 / 34, 4)
    assert m["e3_trigger"] == "4/4"
    r = G.build_redlines(m)
    assert {x["id"]: x["ok"] for x in r}["owner_legal_rate"] is True
    assert {x["id"]: x["ok"] for x in r}["e3_trigger"] is True


def test_empty_is_safe():
    m = G.build_metrics({})
    assert m["verification_rate"] is None
    assert m["e3_trigger"] == ""
    r = G.build_redlines(m)
    assert all(x["ok"] is None for x in r if x["id"] in
               ("owner_legal_rate", "e3_trigger"))


def test_malformed_daily_shape_no_crash():
    # 合法 JSON 但类型错误（metrics 是 list）→ 降级空态，不抛异常
    m = G.build_metrics({"metrics": [1]})
    assert m["verification_rate"] is None
    assert m["e3_trigger"] == ""
    assert isinstance(G.build_redlines([1]), list)


def test_malformed_root_no_crash(tmp_path):
    import json
    root = tmp_path
    (root / "agent_platform/registry").mkdir(parents=True)
    (root / "agent_platform/registry/org.json").write_text(
        "[]", encoding="utf-8")  # org 合法 JSON 但非 dict
    (root / ".worktrees").mkdir()
    (root / ".worktrees/tasks.json").write_text("[1]", encoding="utf-8")
    (root / "agent_platform/data/dev").mkdir(parents=True)
    (root / "agent_platform/data/dev/daily_metrics.json").write_text(
        json.dumps({"metrics": [1]}), encoding="utf-8")
    p = A.build_payload(root)  # 不应抛 AttributeError
    assert p["seats"] == []
    assert p["errors"], "畸形数据源必须留 errors"


def test_api_uses_aggregation():
    p = A.build_payload(A.ROOT)
    # API payload 与聚合层同源（有 daily 时指标一致）
    m = G.build_metrics({"metrics": {}, "window_days": 7})
    assert set(p["metrics"]) == set(m)


def test_governance_new_vs_historical():
    tasks = {
        "T-NEW-OK": {"dept": "dev", "status": "done", "merged": "2026-10-28 10:00",
                     "created": "2026-10-28 09:00", "approver": "ARCH1"},
        "T-NEW-MISS": {"dept": "dev", "status": "done", "merged": "2026-10-28 11:00",
                       "created": "2026-10-28 10:00"},
        "T-HIST": {"dept": "dev", "status": "done", "merged": "2026-10-27 15:00",
                   "created": "2026-10-27 14:00"},
        "T-OTHER": {"dept": "quant", "status": "done", "merged": "2026-10-28 12:00"},
    }
    g = G.build_governance(tasks)
    assert g["new_done"] == 2
    assert g["new_missing"] == 1
    assert g["historical_missing"] == 1
    assert g["merge_approval_coverage"] == 0.5
    assert g["approval_ok"] is False


def test_api_has_governance():
    p = A.build_payload(A.ROOT)
    assert isinstance(p.get("governance"), dict)
    assert "merge_approval_coverage" in p["governance"]


def test_wip_desc_sanitized():
    tasks = {"T": {"owner": "DEV1", "status": "doing",
                   "desc": r"路径 C:\项目\x token=abc；secret 文本"}}
    wip = G.build_wip(tasks, ["DEV1"])
    desc = wip[0]["tasks"][0]["desc"]
    assert r"C:\项目" not in desc
    assert "token" not in desc.lower()
    assert "secret" not in desc.lower()
    assert "[path]" in desc and "***" in desc
