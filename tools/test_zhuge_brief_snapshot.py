# -*- coding: utf-8 -*-
"""zhuge_brief_snapshot 单测（K-QUANT-20261026-ZHUGESNAP）。"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOD_PATH = HERE / "zhuge_brief_snapshot.py"


def _load():
    spec = importlib.util.spec_from_file_location("zhuge_brief_snapshot_t", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()

PAYLOAD = {
    "engine": "rule-based", "model_invoked": False, "dispatched": False,
    "generated_at": "2026-10-26T19:05:13+08:00", "business_day": "2026-10-26",
    "suggestions": [
        {"id": "notify:deadline:us:postclose", "domain": "通知", "priority": 1,
         "reason": "到点未就绪", "source": "/api/notify/queue", "as_of": "x"},
        {"id": "pushflow:daily_rate", "domain": "通知", "priority": 1,
         "reason": "失败率 94.6%", "source": "/api/zhuge/brief.pushflow",
         "as_of": None},
    ],
}


def test_build_ok():
    snap = M.build({"ok": True, "http": 200, "payload": PAYLOAD},
                   "http://127.0.0.1:8091/api/zhuge/brief")
    assert snap["schema"] == "zhuge_brief_snapshot/v1"
    assert snap["seat"] == "zhuge"
    assert snap["ok"] is True and snap["http"] == 200
    assert snap["business_day"] == "2026-10-26"
    assert [s["id"] for s in snap["suggestions"]] == [
        "notify:deadline:us:postclose", "pushflow:daily_rate"]
    assert len(snap["suggestions"][0]["reason"]) <= 300


def test_build_error_not_crash():
    snap = M.build({"ok": False, "error": "boom", "payload": None}, "url")
    assert snap["ok"] is False and snap["error"] == "boom"
    assert snap["suggestions"] == []


def test_render_md():
    snap = M.build({"ok": True, "http": 200, "payload": PAYLOAD}, "url")
    md = M.render_md(snap)
    assert "诸葛亮证据快照" in md
    assert "notify:deadline:us:postclose" in md
    assert "失败率 94.6%" in md
    assert "人工审批" in md


def test_cli_writes_files(tmp_path, monkeypatch):
    # 不联网：替换 fetch 为固定返回
    monkeypatch.setattr(M, "fetch",
                        lambda url, timeout=30: {"ok": True, "http": 200, "payload": PAYLOAD})
    out = tmp_path / "brief"
    rc = M.main(["--out", str(out), "--quiet"])
    assert rc == 0
    files = sorted(p.name for p in out.glob("zhuge_brief_*"))
    assert any(f.endswith(".json") for f in files)
    assert any(f.endswith(".md") for f in files)
    data = json.loads(next(out.glob("zhuge_brief_*.json")).read_text(encoding="utf-8"))
    assert data["ok"] is True
