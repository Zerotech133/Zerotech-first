# -*- coding: utf-8 -*-
"""IFS F7 隔离全链端到端验收工具回归（WO-IFS-E2E-20261104）。

覆盖：
1) fixture 池全链（不依赖 PG）：pool→decision→handoff v2→watch_plan→trigger→confirmation→
   paper receipt→outcome→training episode，ID 链无断点（trigger_id/card_id 已知残余显式披露）；
2) 固定行情 fixture 明示（disclosed=True，非实时行情）；outcome 数值与 fixture 一致；
3) 隔离/只读：全部写入落在 tmp 根；生产 artifact 前后零变化（production_untouched）；
4) 只读边界：无执行字段（order/qty/broker/…）；confirmation 为 paper 记录；
5) CLI：`--pool fixture --json --out` rc0；指定池外 code → rc1 且 failure=pool 步（失败诚实，不假绿）；
6) live 只读池 smoke（PG 不可用自动 skip）。
"""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "ifs_e2e_acceptance.py"
PY = sys.executable


def _load_tool():
    spec = importlib.util.spec_from_file_location("ifs_e2e_acceptance_t", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load_tool()

CHAIN_KEYS = ("pool_id", "decision_id", "handoff_id", "watch_plan_id",
              "confirmation_id", "trade_id", "outcome_id", "training_episode_id")
ID_FORMATS = {
    "decision_id": r"^d1-\d{14}-[0-9a-f]{6}$",
    "handoff_id": r"^d1h-\d{14}-[0-9a-f]{6}$",
    "watch_plan_id": r"^wp-\d{8}-[0-9a-f]{6}$",
    "confirmation_id": r"^CFM-[0-9A-F]{12}$",
    "trade_id": r"^pt-\d{14}-[0-9a-f]{6}$",
    "outcome_id": r"^out-\d{8}-[0-9A-F]{8}$",
    "training_episode_id": r"^ep-\d{8}-[0-9A-F]{8}$",
}


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    """fixture 池全链跑一次（不依赖 PG / 不写仓库）。"""
    root = tmp_path_factory.mktemp("ifs_e2e_tool")
    return MOD.run_acceptance(pool_snapshot=MOD.FIXTURE_POOL_SNAPSHOT,
                              tmp_root=str(root))


def test_fixture_chain_pass(report):
    assert report["ok"] is True, report.get("failure")
    assert report["failure"] is None
    assert report["schema"] == MOD.REPORT_SCHEMA
    assert len(report["steps"]) == 8
    assert all(step["ok"] for step in report["steps"])
    assert report["checks"] and all(item["ok"] for item in report["checks"])


def test_id_chain_no_break(report):
    chain = report["id_chain"]
    for key in CHAIN_KEYS:
        assert chain.get(key), "ID 链断点: %s" % key
    # 已知残余显式披露（不是静默缺失）
    assert chain.get("trigger_id") is None
    assert chain.get("card_id") is None
    residuals = {item["id"]: item for item in report["residuals"]}
    assert residuals["trigger_id"]["status"] == "absent"
    assert residuals["card_id"]["status"] == "absent"
    assert residuals["decision_id_format"]["status"] == "deviation"
    assert residuals["handoff_id_alias"]["status"] == "deviation"


def test_id_formats(report):
    chain = report["id_chain"]
    for key, pattern in ID_FORMATS.items():
        assert re.match(pattern, chain[key]), "%s 格式不符: %r" % (key, chain[key])


def test_outcome_and_episode_from_fixture(report):
    chain = report["id_chain"]
    outcome = None
    episode = None
    for path in report["artifacts"]:
        p = Path(path["path"])
        if p.name == "outcomes.jsonl":
            outcome = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
        if p.name == "training_episode.json":
            episode = json.loads(p.read_text(encoding="utf-8"))
    assert outcome and episode, "缺 outcome/episode 证据文件"
    assert outcome["outcome_id"] == chain["outcome_id"]
    assert outcome["status"] == "complete"
    assert outcome["next_day_return"] == round(MOD.QUOTE_FIXTURE["expect_next_day_return"], 6)
    assert bool(outcome["hit"]) == bool(MOD.QUOTE_FIXTURE["expect_hit"])
    assert outcome["quote_status"] == "complete"
    assert all(outcome.get(k) for k in
               ("decision_id", "handoff_id", "watch_plan_id",
                "confirmation_id", "trade_id", "outcome_id"))
    assert episode["training_episode_id"] == chain["training_episode_id"]
    assert chain["outcome_id"] in (episode["ids"] or {}).get("outcome_ids", [])
    assert (episode["metrics"] or {}).get("outcomes") == 1


def test_quote_fixture_disclosed(report):
    fixture = report["quote_fixture"]
    assert fixture["disclosed"] is True
    assert fixture["schema"] == MOD.FIXTURE_SCHEMA
    assert "fixture" in fixture["note"].lower() or "E2E" in fixture["note"]
    assert fixture["entry_price"] > 0 and fixture["next_close"] > 0
    assert fixture["expect_next_day_return"] == round(
        fixture["next_close"] / fixture["entry_price"] - 1.0, 6)


def test_isolation_and_production_untouched(report):
    workdir = Path(report["workdir"]).resolve()
    assert report["artifacts"], "缺证据文件"
    for item in report["artifacts"]:
        p = Path(item["path"]).resolve()
        assert workdir == p or workdir in p.parents, "证据文件越界: %s" % p
        assert re.match(r"^[0-9a-f]{64}$", item["sha256"])
        assert item["bytes"] >= 0
    guard = report["production_untouched"]
    assert guard["before"] == guard["after"]
    assert guard["unchanged"] is True
    assert [c for c in report["checks"] if c["check"] == "production_untouched"][0]["ok"] is True


def test_readonly_no_execution_fields(report):
    checks = {c["check"]: c for c in report["checks"]}
    assert checks["no_execution_fields"]["ok"] is True
    assert not checks["no_execution_fields"]["detail"]
    assert checks["confirmation_is_paper_record"]["ok"] is True
    assert checks["receipt_paper_only"]["ok"] is True
    assert checks["episode_readonly_flags"]["ok"] is True


def test_failure_is_honest(tmp_path):
    """池步骤失败 → ok=False + failure 明细（不假绿、不吞）。"""
    bad_pool = {"ok": True, "items": [], "count": 0,
                "view": "full", "source_mode": "fixture_offline"}
    rep = MOD.run_acceptance(pool_snapshot=bad_pool, tmp_root=str(tmp_path / "fail"))
    assert rep["ok"] is False
    assert rep["failure"] and rep["failure"]["step"] == "pool_readonly_live"
    assert rep["steps"][-1]["ok"] is False


def test_cli_json_out_rc0(tmp_path):
    work = tmp_path / "cli_work"
    out = tmp_path / "report.json"
    r = subprocess.run([PY, "-X", "utf8", str(TOOL), "--pool", "fixture",
                        "--json", "--out", str(out), "--tmp-root", str(work)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    report = json.loads(r.stdout)
    assert report["ok"] is True
    assert report["out"] == str(out)
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk["ok"] is True and on_disk["id_chain"]["outcome_id"]
    assert Path(on_disk["workdir"]).resolve() == work.resolve()


def test_cli_failure_rc1(tmp_path):
    r = subprocess.run([PY, "-X", "utf8", str(TOOL), "--pool", "fixture",
                        "--code", "NOT_IN_POOL_000000", "--json",
                        "--tmp-root", str(tmp_path / "cli_fail")],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 1, (r.stdout or "")[:500] + (r.stderr or "")[:500]
    report = json.loads(r.stdout)
    assert report["ok"] is False
    assert report["failure"]["step"] == "pool_readonly_live"


def test_live_pool_readonly_smoke():
    """live 池只读 smoke（PG 不可用/非官方 → skip，测试不依赖真实 PG）。"""
    try:
        full = MOD._load_live_pool("full")
    except Exception as exc:  # noqa: BLE001 — 测试环境无 PG 属正常
        pytest.skip("live PG 不可用：%s" % str(exc)[:120])
    assert full.get("official") is True
    assert full.get("source_mode") in ("pg_official", "pg_official_aggregated")
    assert full.get("count") and full.get("items")
    try:
        single = MOD._load_live_pool("single")
    except Exception:  # noqa: BLE001 — 单日视图失败不阻断 full smoke
        single = {}
    # 12/30 现状：full 聚合 ≥ 单日视图（若可读）
    if single.get("count"):
        assert full["count"] >= single["count"]
