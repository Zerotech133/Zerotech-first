# -*- coding: utf-8 -*-
"""panel_daily（研报面板日常化运行器）契约测试 —— 不调 LLM、不写真实门户目录。

覆盖：
  1. 状态台账读写：首次/追加/截断/损坏容错，latest 指向最新一条；
  2. 变更摘要：首次无对比、候选新增/移除/数量变化、页码越界与被拦截变化；
  3. `run`：用真实 v3 面板 JSON 刷新门户 JSON（写临时路径）→ 校验通过 + 台账记录 + 二次运行变更摘要；
  4. 校验失败：源数据不合法 → 不发布门户 JSON、退出非 0、台账记录 ok=false；
  5. `--check-only`：只校验不重写（门户文件不变）、缺失/篡改 JSON → 退出非 0；
  6. CLI --help 与 subprocess run。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
DAILY_PATH = REPO / "tools" / "research" / "panel_daily.py"
V3_JSON = REPO / "research" / "reports" / "report_panel_v3_20261101.json"


def _load_daily():
    spec = importlib.util.spec_from_file_location("panel_daily_under_test", DAILY_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pd = _load_daily()


# ---------------------------------------------------------------------------
# 1. 状态台账
# ---------------------------------------------------------------------------
def test_status_append_latest_and_trim(tmp_path):
    status = tmp_path / "status.json"
    assert pd.load_status(status)["runs"] == []
    for i in range(3):
        pd.append_status({"run_at": f"2026-11-01T0{i}:00:00+08:00", "mode": "run",
                          "ok": True, "candidates": i}, status, max_runs=2)
    data = pd.load_status(status)
    assert len(data["runs"]) == 2
    assert data["latest"]["candidates"] == 2
    assert data["updated_at"]
    assert not (tmp_path / "status.json.tmp").exists()
    # 损坏台账容错：返回空骨架，不抛异常
    status.write_text("{broken json", encoding="utf-8")
    assert pd.load_status(status)["runs"] == []
    # 非对象 JSON 同样容错
    status.write_text("[]", encoding="utf-8")
    assert pd.load_status(status)["runs"] == []


def test_file_meta_missing_and_existing(tmp_path):
    missing = pd.file_meta(tmp_path / "nope.json")
    assert missing["exists"] is False and missing["mtime"] is None
    f = tmp_path / "x.json"
    f.write_text("{}", encoding="utf-8")
    meta = pd.file_meta(f)
    assert meta["exists"] is True and meta["size"] == 2 and meta["mtime_epoch"] > 0


# ---------------------------------------------------------------------------
# 2. 变更摘要
# ---------------------------------------------------------------------------
def test_change_summary_first_run_and_deltas():
    first = pd.change_summary(None, {"candidates": 1, "candidate_ids": ["A"]})
    assert first["has_prev"] is False
    prev = {"run_at": "t0", "candidates": 2, "candidate_ids": ["A", "B"],
            "page_invalid_facts": 0, "rejected": 1, "source_generated_at": "g1"}
    cur = {"run_at": "t1", "candidates": 1, "candidate_ids": ["B"],
           "page_invalid_facts": 2, "rejected": 2, "source_generated_at": "g2"}
    change = pd.change_summary(prev, cur)
    assert change["has_prev"] is True
    assert change["candidates"]["delta"] == -1
    assert change["candidates"]["removed"] == ["A"]
    assert change["candidates"]["added"] == []
    assert change["page_invalid_facts"] == {"prev": 0, "cur": 2, "delta": 2}
    assert change["rejected"]["delta"] == 1
    assert change["source_generated_at_changed"] is True
    text = pd.format_change(change)
    assert "候选 2→1" in text and "页码越界 0→2" in text and "需人工复核" in text


# ---------------------------------------------------------------------------
# 3. run：刷新门户 + 台账 + 二次变更摘要
# ---------------------------------------------------------------------------
def test_run_refreshes_portal_and_records_status(tmp_path):
    portal = tmp_path / "portal" / "report_panel.json"
    status = tmp_path / "status.json"
    code, entry = pd.run_daily(source_path=V3_JSON, portal_path=portal, status_path=status)
    assert code == 0, entry.get("errors")
    assert entry["ok"] is True and entry["mode"] == "run"
    assert entry["validation"]["ok"] is True
    assert entry["candidates"] == 1 and entry["reports"] == 3
    assert entry["page_invalid_facts"] == 1 and entry["disclaimer_ok"] is True
    assert entry["source_meta"]["exists"] and entry["source_meta"]["mtime"]
    assert portal.exists() and pd.pe.check_portal_json(portal) == []
    saved = json.loads(status.read_text(encoding="utf-8"))
    assert saved["latest"]["run_at"] == entry["run_at"]
    assert len(saved["runs"]) == 1
    # 二次运行：变更摘要应感知历史且 delta=0
    code2, entry2 = pd.run_daily(source_path=V3_JSON, portal_path=portal, status_path=status)
    assert code2 == 0
    assert entry2["change"]["has_prev"] is True
    assert entry2["change"]["candidates"]["delta"] == 0
    assert len(pd.load_status(status)["runs"]) == 2


# ---------------------------------------------------------------------------
# 4. 校验失败 → 非 0 退出、不发布坏 JSON
# ---------------------------------------------------------------------------
def test_validation_failure_exits_nonzero_and_keeps_portal(tmp_path):
    bad_source = tmp_path / "bad_v3.json"
    bad_source.write_text(json.dumps({"schema_version": 3, "panel": [], "candidates": []},
                                     ensure_ascii=False), encoding="utf-8")
    portal = tmp_path / "report_panel.json"
    portal.write_text('{"old": true}', encoding="utf-8")  # 旧门户文件应保留
    status = tmp_path / "status.json"
    code, entry = pd.run_daily(source_path=bad_source, portal_path=portal, status_path=status)
    assert code == 1
    assert entry["ok"] is False and entry["validation"]["ok"] is False
    assert any("reports" in e for e in entry["errors"])
    assert portal.read_text(encoding="utf-8") == '{"old": true}'  # 未被坏数据覆盖
    assert pd.load_status(status)["latest"]["ok"] is False
    # 输入文件缺失同样非 0
    code2, entry2 = pd.run_daily(source_path=tmp_path / "nope.json", portal_path=portal,
                                 status_path=status)
    assert code2 == 1 and entry2["ok"] is False and entry2["errors"]


def test_dry_run_does_not_write_portal(tmp_path):
    portal = tmp_path / "report_panel.json"
    status = tmp_path / "status.json"
    code, entry = pd.run_daily(source_path=V3_JSON, portal_path=portal, status_path=status,
                               dry_run=True)
    assert code == 0 and entry["ok"] is True and entry["dry_run"] is True
    assert not portal.exists()
    assert pd.load_status(status)["latest"]["dry_run"] is True


# ---------------------------------------------------------------------------
# 5. --check-only
# ---------------------------------------------------------------------------
def test_check_only_validates_without_rewriting(tmp_path):
    portal = tmp_path / "report_panel.json"
    status = tmp_path / "status.json"
    pd.pe.export_panel(V3_JSON, portal)             # 先准备一份合法门户 JSON
    before = portal.read_bytes()
    code, entry = pd.check_only(portal_path=portal, source_path=V3_JSON, status_path=status)
    assert code == 0 and entry["mode"] == "check_only" and entry["ok"] is True
    assert entry["candidates"] == 1 and entry["validation"]["ok"] is True
    assert portal.read_bytes() == before            # 巡检不重写
    assert pd.load_status(status)["latest"]["mode"] == "check_only"
    # 缺失门户 JSON → 非 0
    code2, entry2 = pd.check_only(portal_path=tmp_path / "nope.json", source_path=V3_JSON,
                                  status_path=status)
    assert code2 == 1 and entry2["ok"] is False
    # 篡改（缺免责声明）→ 非 0
    bad = json.loads(before.decode("utf-8"))
    bad.pop("disclaimer")
    portal.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    code3, entry3 = pd.check_only(portal_path=portal, source_path=V3_JSON, status_path=status)
    assert code3 == 1 and entry3["ok"] is False


def test_cli_subprocess_run_and_help(tmp_path):
    portal = tmp_path / "report_panel.json"
    status = tmp_path / "status.json"
    proc = subprocess.run([sys.executable, "-X", "utf8", str(DAILY_PATH), "run",
                           "--source", str(V3_JSON), "--portal", str(portal),
                           "--status", str(status)],
                          capture_output=True, text=True, timeout=120,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    assert "status=OK" in proc.stdout and "mode=run" in proc.stdout
    assert portal.exists() and status.exists()
    proc2 = subprocess.run([sys.executable, "-X", "utf8", str(DAILY_PATH), "run",
                            "--source", str(V3_JSON), "--portal", str(portal),
                            "--status", str(status), "--check-only"],
                           capture_output=True, text=True, timeout=120,
                           encoding="utf-8", errors="replace")
    assert proc2.returncode == 0 and "mode=check_only" in proc2.stdout
    assert "change:" in proc2.stdout
    proc3 = subprocess.run([sys.executable, "-X", "utf8", str(DAILY_PATH), "--help"],
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
    assert proc3.returncode == 0 and "run" in proc3.stdout
