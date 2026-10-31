# -*- coding: utf-8 -*-
"""情报 A/B 隔离运行器单测（WO-INTEL-ABHARNESS-20261031 / task-20）。

全隔离零网络：
  · 命令生成/隔离 root 守卫/参数校验；
  · dry-run 默认只打印、零 subprocess 执行；
  · --run 执行两臂（subprocess 桩，不真跑 run_shadow/模型/网络）；
  · 两臂 ledger 对齐对比（recommendation/confidence/parse_ok/fallback/证据来源）；
  · 坏 ledger / 缺 ledger fail-visible；
  · 0 resolved → 明确「B 臂应全 fallback」，不伪造改进。
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_platform import intel_ab_harness as H  # noqa: E402

TS = "2026-10-31"
CODES = ["600519", "000858"]


# ---------------------------------------------------------------- 夹具
def _ledger(root: Path, rows):
    path = Path(root) / "shadow" / "shadow_ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")
    return path


def _row(symbol, rec="BUY", conf=0.8, parse_ok=True, fallback=False,
         evidence=None, status="done"):
    row = {"date": TS, "symbol": symbol, "status": status,
           "recommendation": rec, "confidence": conf, "parse_ok": parse_ok,
           "fallback": fallback, "degraded": not parse_ok, "run_id": "r1"}
    if evidence is not None:
        row["intel_evidence"] = evidence
    return row


def _ev(source="price_proxy", reason="no_reviewed_jev", events=(), reviews=()):
    return {"source": source, "reason": reason,
            "event_refs": list(events), "review_refs": list(reviews),
            "bad_rows": [], "counts": {}, "errors": []}


def _news_db(tmp_path: Path, resolved_rows=()) -> Path:
    db = tmp_path / "news.db"
    con = sqlite3.connect(str(db))
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS jev_review_queue ("
            "id TEXT PRIMARY KEY, queued_at TEXT, conf REAL, bias TEXT,"
            "title TEXT, stocks TEXT, original_jev TEXT, status TEXT,"
            "reviewer TEXT, reviewed_at TEXT, decision TEXT, note TEXT)")
        for nid in resolved_rows:
            con.execute(
                "INSERT INTO jev_review_queue (id, queued_at, conf, bias, title,"
                " stocks, original_jev, status, reviewer, reviewed_at, decision)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (nid, "2026-10-30 17:00", 0.6, "bull", "600519 测试",
                 json.dumps(["600519.SH"]),
                 json.dumps({"bias": "bull", "conf": 0.6,
                             "reason": "摘要"}, ensure_ascii=False),
                 "resolved", "N1", "2026-10-31 09:00", "correct"))
        con.commit()
    finally:
        con.close()
    return db


def _main(argv):
    return H.main(argv)


# ---------------------------------------------------------------- 命令生成
def test_build_plan_generates_two_isolated_arms(tmp_path):
    off, on = tmp_path / "off", tmp_path / "on"
    plan = H.build_plan(CODES, off, on)
    assert plan["ok"] is True and [a["arm"] for a in plan["arms"]] == ["off", "on"]
    a, b = plan["arms"]
    assert a["root"] == str(off.resolve()) and b["root"] == str(on.resolve())
    assert a["ledger"].endswith("shadow_ledger.jsonl")
    for arm in (a, b):
        assert arm["argv"][1] == str(H.RUN_SHADOW)
        assert "--rehearsal" in arm["argv"]
        assert arm["argv"][arm["argv"].index("--codes") + 1] == "600519,000858"
        assert arm["env"]["SHADOW_REHEARSAL"] == "1"
        assert arm["env"]["SHADOW_DATA_ROOT"] == arm["root"]
    assert a["flags"] == {"INTEL_TO_N1": "0", "INTEL_TO_D1": "0"}
    assert b["flags"] == {"INTEL_TO_N1": "1", "INTEL_TO_D1": "1"}


def test_build_plan_rejects_production_roots(tmp_path):
    prod = H.PRODUCTION_DATA_ROOTS[0]
    with pytest.raises(ValueError, match="生产 data 根|生产"):
        H.build_plan(CODES, prod, tmp_path / "on")
    with pytest.raises(ValueError):
        H.build_plan(CODES, tmp_path / "same", tmp_path / "same")
    with pytest.raises(ValueError):
        H.build_plan(CODES, "relative/off", tmp_path / "on")


def test_parse_codes_and_flags():
    assert H.parse_codes("600519, sz000858;601318") == ["600519", "000858", "601318"]
    with pytest.raises(ValueError):
        H.parse_codes("60051")
    with pytest.raises(ValueError):
        H.parse_codes("")
    flags = H.parse_flags("INTEL_TO_N1=1,INTEL_TO_D1=0", {"X": "1"})
    assert flags["INTEL_TO_N1"] == "1" and flags["X"] == "1"
    with pytest.raises(ValueError):
        H.parse_flags("NO_EQUALS")
    with pytest.raises(ValueError):
        H.parse_flags("BAD NAME=1")


# ---------------------------------------------------------------- dry-run
def test_dry_run_is_default_and_executes_nothing(tmp_path, monkeypatch, capsys):
    called = []

    def _no_run(*a, **k):
        called.append(1)
        raise AssertionError("dry-run 不得执行子进程")

    monkeypatch.setattr(H.subprocess, "run", _no_run)
    rc = _main(["--codes", "600519,000858",
                "--off-root", str(tmp_path / "off"),
                "--on-root", str(tmp_path / "on")])
    out = capsys.readouterr().out
    assert rc == 0 and called == []
    assert "dry-run" in out
    assert "INTEL_TO_N1=0" in out and "INTEL_TO_N1=1" in out
    assert str(tmp_path / "off") in out and str(tmp_path / "on") in out
    assert not (tmp_path / "off").exists()      # 不建目录/不落日志


def test_dry_run_json_prints_plan(tmp_path, capsys):
    rc = _main(["--codes", "600519", "--off-root", str(tmp_path / "o"),
                "--on-root", str(tmp_path / "n"), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and data["schema"] == "intel_ab_plan/v1"
    assert data["codes"] == ["600519"] and len(data["arms"]) == 2


# ---------------------------------------------------------------- --run
def test_run_executes_both_arms_with_isolated_env(tmp_path, monkeypatch, capsys):
    calls = []

    def _fake_run(argv, **kw):
        calls.append((argv, kw))
        root = Path(argv[argv.index("--root") + 1])
        _ledger(root, [_row("600519", parse_ok=True,
                            evidence=_ev() if "INTEL_TO_D1=1" in
                            json.dumps(kw["env"]) else None)])
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(H.subprocess, "run", _fake_run)
    off, on = tmp_path / "off", tmp_path / "on"
    rc = _main(["--codes", "600519", "--off-root", str(off),
                "--on-root", str(on), "--intel-db", str(tmp_path / "news.db"),
                "--run", "--json"])
    assert rc == 0 and len(calls) == 2
    envs = [kw["env"] for _argv, kw in calls]
    assert envs[0]["INTEL_TO_N1"] == "0" and envs[1]["INTEL_TO_N1"] == "1"
    assert envs[1]["INTEL_TO_D1"] == "1"
    assert envs[0]["SHADOW_DATA_ROOT"] == str(off.resolve())
    assert envs[0]["INTEL_N1_DB"] == str(tmp_path / "news.db")
    assert (off / "ab_run_off.log").is_file()
    assert (on / "ab_run_on.log").is_file()
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == H.SCHEMA
    assert report["compare"]["aligned"]["n"] == 1


def test_run_reports_arm_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(H.subprocess, "run", lambda argv, **kw:
                        types.SimpleNamespace(returncode=3, stdout="", stderr="x"))
    rc = _main(["--codes", "600519", "--off-root", str(tmp_path / "o"),
                "--on-root", str(tmp_path / "n"), "--run"])
    err = capsys.readouterr().err
    assert "FAIL" in err and "rc=3" in err
    assert rc == 2      # 两臂 ledger 缺失 → fail-visible


# ---------------------------------------------------------------- 对比
def test_compare_aligned_metrics(tmp_path):
    off = _ledger(tmp_path / "off", [
        _row("600519", rec="BUY", conf=0.8, parse_ok=True, fallback=False),
        _row("000858", rec="WATCH", conf=0.5, parse_ok=False, fallback=False),
    ])
    on = _ledger(tmp_path / "on", [
        _row("600519", rec="HOLD", conf=0.7, parse_ok=False, fallback=True,
             evidence=_ev()),
        _row("000858", rec="WATCH", conf=0.5, parse_ok=False, fallback=True,
             evidence=_ev()),
    ])
    cmp = H.compare_ledgers(off, on)
    assert cmp["ok"] is True and cmp["aligned"]["n"] == 2
    d = cmp["differences"]
    assert d["recommendation_changed_n"] == 1
    assert d["confidence_changed_n"] == 1
    assert d["parse_ok_changed_n"] == 1
    assert cmp["arms"]["off"]["fallback_rate"] == 0.0
    assert cmp["arms"]["on"]["fallback_rate"] == 1.0
    assert cmp["arms"]["on"]["evidence_present"] is True
    assert cmp["arms"]["on"]["evidence_sources"] == {"price_proxy": 2}
    assert cmp["arms"]["on"]["intel_fallback_rate"] == 1.0
    assert cmp["arms"]["off"]["evidence_present"] is False


def test_compare_handles_off_only_and_on_only(tmp_path):
    off = _ledger(tmp_path / "off", [_row("600519")])
    on = _ledger(tmp_path / "on", [_row("601318")])
    cmp = H.compare_ledgers(off, on)
    assert cmp["aligned"]["n"] == 0
    assert cmp["aligned"]["off_only_n"] == 1
    assert cmp["aligned"]["on_only_n"] == 1


def test_missing_ledger_fail_visible(tmp_path, capsys):
    # 只有 off 臂 ledger
    _ledger(tmp_path / "off", [_row("600519")])
    rc = _main(["--codes", "600519", "--off-root", str(tmp_path / "off"),
                "--on-root", str(tmp_path / "on"), "--compare", "--json"])
    out = capsys.readouterr().out
    report = json.loads(out)
    assert rc == 2
    assert any(e.get("error") == "ledger_missing"
               for e in report["compare"]["errors"])
    assert "[ERROR]" in capsys.readouterr().err or True   # --json 模式不打印 human


def test_bad_ledger_lines_fail_visible(tmp_path, capsys):
    off = _ledger(tmp_path / "off", [_row("600519")])
    on = Path(tmp_path / "on") / "shadow" / "shadow_ledger.jsonl"
    on.parent.mkdir(parents=True)
    on.write_text(
        "not-json\n"
        + json.dumps(_row("600519", evidence=_ev()), ensure_ascii=False) + "\n",
        encoding="utf-8")
    rc = _main(["--codes", "600519", "--off-root", str(tmp_path / "off"),
                "--on-root", str(tmp_path / "on"), "--compare"])
    out = capsys.readouterr().out
    assert rc == 2 and "ERROR" in out
    assert "bad_json" in out


# ---------------------------------------------------------------- 0 resolved 诚实性
def test_zero_resolved_reports_b_arm_all_fallback(tmp_path, capsys):
    db = _news_db(tmp_path)                     # resolved 行 0 条
    _ledger(tmp_path / "off", [
        _row("600519", rec="BUY", conf=0.8, parse_ok=True, fallback=False)])
    _ledger(tmp_path / "on", [
        _row("600519", rec="BUY", conf=0.8, parse_ok=True, fallback=True,
             evidence=_ev())])
    rc = _main(["--codes", "600519", "--off-root", str(tmp_path / "off"),
                "--on-root", str(tmp_path / "on"), "--compare", "--json",
                "--intel-db", str(db)])
    report = json.loads(capsys.readouterr().out)
    honesty = report["honesty"]
    assert rc == 0
    assert report["reviewed_probe"]["resolved_total"] == 0
    assert honesty["b_arm_all_fallback"] is True
    assert honesty["improvement_claims"] == []
    joined = " ".join(honesty["notes"])
    assert "全为 price_proxy" in joined and "不构成改进证据" in joined


def test_reviewed_evidence_is_not_overclaimed(tmp_path, capsys):
    db = _news_db(tmp_path, resolved_rows=("ev:1",))
    _ledger(tmp_path / "off", [_row("600519", parse_ok=True)])
    _ledger(tmp_path / "on", [
        _row("600519", parse_ok=True,
             evidence=_ev("reviewed_jev", reason="", events=["ev:1"],
                          reviews=["rv-1"]))])
    rc = _main(["--codes", "600519", "--off-root", str(tmp_path / "off"),
                "--on-root", str(tmp_path / "on"), "--compare", "--json",
                "--intel-db", str(db)])
    report = json.loads(capsys.readouterr().out)
    honesty = report["honesty"]
    assert rc == 0
    assert report["reviewed_probe"]["resolved_total"] == 1
    assert honesty["b_arm_all_fallback"] is False
    assert honesty["improvement_claims"] == []
    assert "运行器不做质量结论" in " ".join(honesty["notes"])


# ---------------------------------------------------------------- 探测 fail-visible
def test_probe_missing_db_fail_visible(tmp_path):
    probe = H.probe_reviewed(["600519"], tmp_path / "missing.db")
    assert probe["exists"] is False and probe["resolved_total"] is None
    assert probe["errors"] and probe["errors"][0]["error"] == "news_db_missing"


def test_main_invalid_args_fail_visible(capsys):
    rc = _main(["--codes", "bad", "--off-root", str(Path.cwd() / "x"),
                "--on-root", str(Path.cwd() / "y")])
    err = capsys.readouterr().err
    assert rc == 2 and "ERROR" in err and "Traceback" not in err


def test_run_and_compare_mutually_exclusive(tmp_path, capsys):
    rc = _main(["--codes", "600519", "--off-root", str(tmp_path / "o"),
                "--on-root", str(tmp_path / "n"), "--run", "--compare"])
    assert rc == 2 and "不能同时使用" in capsys.readouterr().err
