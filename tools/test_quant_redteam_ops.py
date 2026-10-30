# -*- coding: utf-8 -*-
"""P2 单测：量化只读（fake MCP）+ 红队定时（fake responder/临时账本）。"""
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


qr = _load("quant_readonly")
rd = _load("redteam_daily")


def test_quant_allowlist_and_query(monkeypatch):
    calls = []

    def fake_call(tool, args, timeout=30.0):
        calls.append((tool, args))
        return {"ok": True, "payload": {"items": [{"ts_code": "000001.SZ",
                                                   "name": "平安银行"}]}}
    monkeypatch.setattr(qr, "_call", fake_call)
    rep = qr.query("stock_basic", limit=3)
    assert rep["ok"] is True and rep["count"] == 1
    assert calls[0][0] == "stock_basic"
    bad = qr.query("drop_table", limit=3)
    assert bad["ok"] is False and bad["error"] == "tool_not_in_allowlist"


def test_quant_failure_visible(monkeypatch):
    monkeypatch.setattr(qr, "_call", lambda *a, **k: {"ok": False,
                                                      "error": "mcp_down"})
    rep = qr.query("daily", code="000001.SZ")
    assert rep["ok"] is False and rep["error"] == "mcp_down"


def test_quant_markdown_and_cli(monkeypatch, capsys):
    monkeypatch.setattr(qr, "_call", lambda *a, **k: {
        "ok": True, "payload": {"items": [{"ts_code": "X", "close": 1.0}]}})
    assert "X" in qr.to_markdown(qr.query("daily"))
    rc = qr.main(["--tool", "stock_basic", "--limit", "1", "--json"])
    out = capsys.readouterr().out
    assert rc == 0 and json.loads(out)["ok"] is True


def test_quant_extract_content_wrapper():
    rows = qr._extract_rows({"content": [{"type": "text",
                                          "text": '{"items":[{"a":1}]}'}]})
    assert rows == [{"a": 1}]


def test_redteam_daily_dry(tmp_path):
    daily = tmp_path / "daily.jsonl"
    rep = rd.run_daily(limit=2, dry=True, daily_path=daily)
    assert rep["mode"] == "dry" and rep["total"] == 2 and rep["failed"] == 0
    lines = daily.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["passed"] == 2


def test_redteam_daily_appends(tmp_path):
    daily = tmp_path / "daily.jsonl"
    rd.run_daily(limit=1, dry=True, daily_path=daily)
    rd.run_daily(limit=1, dry=True, daily_path=daily)
    assert len(daily.read_text(encoding="utf-8").splitlines()) == 2


def test_redteam_weekly_aggregates(tmp_path):
    daily = tmp_path / "daily.jsonl"
    rd.run_daily(limit=2, dry=True, daily_path=daily)
    with daily.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "mode": "live", "total": 2, "passed": 2, "failed": 0,
            "pass_rate": 1.0, "failed_ids": [], "case_ids": ["L1", "L2"],
        }, ensure_ascii=False) + "\n")
    rep = rd.weekly(days=7, daily_path=daily, out_dir=tmp_path)
    assert rep["runs_live"] == 1 and rep["runs_dry"] == 1
    assert rep["runs"] == 1 and rep["total_cases"] == 2
    assert rep["pass_rate"] == 1.0
    assert Path(rep["report"]).exists()
    assert "红队周报" in Path(rep["report"]).read_text(encoding="utf-8")
