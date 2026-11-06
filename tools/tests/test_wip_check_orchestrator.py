# -*- coding: utf-8 -*-
"""wip_check 编排席盲区修复单测（R10 / 2026-11-06）。

背景：修前 `analyze()` 用 `if owner not in seats: continue` 把所有非交付席静默丢弃，
DSH-Lead 名下 27 单不入统计 → 报 over=0 假绿。本文件覆盖新增的
`orchestrator_cap` / `exclude_owners` 两个参数及对应 CLI 开关。

约定：`analyze()` 不传 orchestrator_cap 时保持旧口径（历史单测依赖），
只有 CLI 默认开启。因此本文件的函数级用例都显式传参。
"""
import importlib.util
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOL_PATH = os.path.join(REPO, "tools", "wip_check.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("wip_check_orch_under_test", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wipcheck = _load_module()


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _orch_tasks(n, owner="DSH-Lead"):
    return {"T%d" % i: {"owner": owner, "status": "doing"} for i in range(n)}


def make_org(tmp_path):
    return _write_json(
        tmp_path / "org.json",
        {"departments": [{"name": "研发部", "agents": ["DEV1", "DEV2"]}]},
    )


def make_routing(tmp_path, max_keep=5, exclude=None):
    routing = {
        "version": 1,
        "dev_seats": ["DEV1", "DEV2"],
        "wip_caps": {"DEV1": 2, "member_default": 2},
        "dsh_lead_exception": {"seat": "DSH-Lead", "max_keep": max_keep},
    }
    if exclude is not None:
        routing["wip_exclude_owners"] = exclude
    return _write_json(tmp_path / "dev_routing_rules.json", routing)


# ------------------------------------------------------------ analyze 编排席口径
def test_orchestrator_over_detected_when_cap_passed():
    res = wipcheck.analyze(_orch_tasks(7), {"DEV1"}, 3, orchestrator_cap=5)
    assert res["ok"] is False
    over = res["over"]
    assert len(over) == 1
    assert over[0]["owner"] == "DSH-Lead"
    assert over[0]["wip"] == 7
    assert over[0]["cap"] == 5
    assert over[0]["kind"] == "orchestrator"
    assert res["orchestrator_owners"] == {"DSH-Lead": 7}


def test_orchestrator_ignored_by_default():
    """不传 orchestrator_cap = 旧口径，编排席不入统计（history 行为）。"""
    res = wipcheck.analyze(_orch_tasks(7), {"DEV1"}, 3)
    assert res["ok"] is True
    assert res["over"] == []
    assert res["orchestrator_owners"] == {}
    assert res["orchestrator_cap"] is None


def test_orchestrator_within_cap_not_reported():
    res = wipcheck.analyze(_orch_tasks(3), {"DEV1"}, 3, orchestrator_cap=5)
    assert res["ok"] is True
    assert res["over"] == []
    assert res["orchestrator_owners"] == {"DSH-Lead": 3}


def test_delivery_and_orchestrator_both_reported():
    tasks = {
        "A": {"owner": "DEV1", "status": "doing"},
        "B": {"owner": "DEV1", "status": "review"},
        "C": {"owner": "DEV1", "status": "review"},
        "X": {"owner": "DSH-Lead", "status": "doing"},
        "Y": {"owner": "DSH-Lead", "status": "doing"},
    }
    res = wipcheck.analyze(tasks, {"DEV1"}, 3, caps={"DEV1": 2}, orchestrator_cap=1)
    kinds = {o["owner"]: o["kind"] for o in res["over"]}
    assert kinds == {"DEV1": "delivery", "DSH-Lead": "orchestrator"}
    # 两类互不污染：owners 只含交付席，total_wip 只算交付
    assert res["owners"] == {"DEV1": 3}
    assert res["total_wip"] == 3
    assert res["total_wip_all"] == 5


def test_exclude_owners_skips_tool_identity():
    tasks = {
        "A": {"owner": "exec-devtask", "status": "doing"},
        "B": {"owner": "DSH-Lead", "status": "doing"},
    }
    res = wipcheck.analyze(tasks, {"DEV1"}, 3, orchestrator_cap=5,
                           exclude_owners={"exec-devtask"})
    assert "exec-devtask" not in res["orchestrator_owners"]
    assert res["orchestrator_owners"] == {"DSH-Lead": 1}


def test_exclude_parked_applies_to_orchestrator_rows():
    tasks = {
        "A": {"owner": "DSH-Lead", "status": "doing", "parked_candidate": True},
        "B": {"owner": "DSH-Lead", "status": "doing"},
    }
    res = wipcheck.analyze(tasks, {"DEV1"}, 3, orchestrator_cap=5, exclude_parked=True)
    assert res["orchestrator_owners"] == {"DSH-Lead": 1}


# ------------------------------------------------------------ routing 读取
def test_load_orchestrator_cap_reads_max_keep(tmp_path):
    path = make_routing(tmp_path, max_keep=4)
    assert wipcheck.load_orchestrator_cap(path) == 4


def test_load_orchestrator_cap_defaults_on_bad_or_missing(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert wipcheck.load_orchestrator_cap(str(bad)) == wipcheck.ORCHESTRATOR_CAP_DEFAULT
    assert wipcheck.load_orchestrator_cap(str(tmp_path / "missing.json")) == 5
    # 有 routing 但缺 dsh_lead_exception → 仍回落默认
    plain = _write_json(tmp_path / "plain.json", {"version": 1, "dev_seats": ["DEV1"]})
    assert wipcheck.load_orchestrator_cap(plain) == 5


def test_load_exclude_owners_merges_config(tmp_path):
    default_only = wipcheck.load_exclude_owners(str(tmp_path / "missing.json"))
    assert "exec-devtask" in default_only
    path = make_routing(tmp_path, exclude=["some-bot", "exec-devtask"])
    got = wipcheck.load_exclude_owners(path)
    assert got == {"exec-devtask", "some-bot"}


# ------------------------------------------------------------ CLI
def _cli_fixture(tmp_path, n_orch=7):
    tasks = dict(_orch_tasks(n_orch))
    tasks["D"] = {"owner": "DEV1", "status": "doing"}
    tasks_path = _write_json(tmp_path / "tasks.json", tasks)
    return tasks_path, make_org(tmp_path), make_routing(tmp_path)


def test_cli_default_reports_orchestrator(tmp_path, capsys):
    tasks_path, org, routing = _cli_fixture(tmp_path)
    rc = wipcheck.main(["--tasks", tasks_path, "--org", org, "--routing", routing])
    out = capsys.readouterr().out
    assert "OVER DSH-Lead" in out
    assert "kind=orchestrator" in out
    assert "orchestrators: cap=5" in out
    assert rc == 0


def test_cli_no_orchestrators_hides_them(tmp_path, capsys):
    tasks_path, org, routing = _cli_fixture(tmp_path)
    rc = wipcheck.main(["--tasks", tasks_path, "--org", org, "--routing", routing,
                        "--no-orchestrators"])
    out = capsys.readouterr().out
    assert "OVER DSH-Lead" not in out
    assert "orchestrators:" not in out
    assert rc == 0


def test_cli_orchestrator_cap_override(tmp_path, capsys):
    tasks_path, org, routing = _cli_fixture(tmp_path, n_orch=3)
    rc = wipcheck.main(["--tasks", tasks_path, "--org", org, "--routing", routing,
                        "--orchestrator-cap", "2", "--strict"])
    out = capsys.readouterr().out
    assert "OVER DSH-Lead" in out
    assert "cap=2" in out
    assert rc == 1


def test_cli_strict_fails_on_orchestrator_over(tmp_path, capsys):
    """--strict 下编排席超限必须 rc=1 暴露，不能沿用修前的假绿。"""
    tasks_path, org, routing = _cli_fixture(tmp_path, n_orch=6)
    rc = wipcheck.main(["--tasks", tasks_path, "--org", org, "--routing", routing,
                        "--strict"])
    assert rc == 1
