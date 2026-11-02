# -*- coding: utf-8 -*-
"""W1/UF-09 merge train 门测试（tools/merge_train.py）。

覆盖：主窗内/禁并期/窗外/文档旁路/compositions 旁路/--files 判定/--json/--force/
边界 17:00、21:40/跨日下一窗口/参数错误。全部走 CLI 子进程（与仓库其它工具测试同风格）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
TOOL = "tools/merge_train.py"


def _run(args):
    p = subprocess.run([PY, "-X", "utf8", TOOL, *args],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    return p.returncode, p.stdout or "", p.stderr or ""


def _json(args):
    rc, out, err = _run(args)
    return rc, json.loads(out), err


# ---------- 窗口内 ----------
def test_main_window_boundary_1630_allowed():
    rc, payload, _ = _json(["check", "--now", "2026-11-02T16:30:00+08:00", "--json"])
    assert rc == 0, payload
    assert payload["allowed"] is True
    assert payload["mode"] == "window"
    assert payload["window"]["name"] == "main"
    assert "主窗口" in payload["reason"]


def test_main_window_mid_allowed():
    rc, payload, _ = _json(["check", "--now", "2026-11-02T16:45:00+08:00", "--json"])
    assert rc == 0, payload
    assert payload["allowed"] is True
    assert payload["next_window"]["name"] == "secondary"


def test_secondary_window_boundary_2140_allowed():
    rc, payload, _ = _json(["check", "--now", "2026-11-02T21:40:00+08:00", "--json"])
    assert rc == 0, payload
    assert payload["allowed"] is True
    assert payload["window"]["name"] == "secondary"
    assert payload["forbidden_until"] is None


# ---------- 边界：17:00 关窗 / 禁并期 ----------
def test_boundary_1700_blocked():
    rc, payload, _ = _json(["check", "--now", "2026-11-02T17:00:00+08:00", "--json"])
    assert rc == 1, payload
    assert payload["allowed"] is False
    assert payload["mode"] == "outside_window"
    assert payload["window"] is None
    assert payload["next_window"]["start"] == "2026-11-02T21:40:00+08:00"


def test_forbidden_morning_blocked():
    rc, payload, _ = _json(["check", "--now", "2026-11-02T09:30:00+08:00", "--json"])
    assert rc == 1, payload
    assert payload["mode"] == "forbidden"
    assert payload["forbidden_until"] == "2026-11-02T15:05:00+08:00"


def test_forbidden_evening_blocked():
    rc, payload, _ = _json(["check", "--now", "2026-11-02T17:30:00+08:00", "--json"])
    assert rc == 1, payload
    assert payload["mode"] == "forbidden"
    assert payload["forbidden_until"] == "2026-11-02T18:40:00+08:00"


def test_forbidden_before_secondary_allows_at_2140():
    # 21:20–21:40 为禁并期（次窗口前缓冲），21:40 起开放
    rc1, p1, _ = _json(["check", "--now", "2026-11-02T21:25:00+08:00", "--json"])
    assert rc1 == 1 and p1["mode"] == "forbidden"
    assert p1["forbidden_until"] == "2026-11-02T21:40:00+08:00"


# ---------- 窗外/跨日 ----------
def test_outside_window_blocked_next_main():
    rc, payload, _ = _json(["check", "--now", "2026-11-02T15:30:00+08:00", "--json"])
    assert rc == 1, payload
    assert payload["mode"] == "outside_window"
    assert payload["forbidden_until"] is None
    assert payload["next_window"]["start"] == "2026-11-02T16:30:00+08:00"


def test_after_secondary_next_day_main():
    rc, payload, _ = _json(["check", "--now", "2026-11-02T22:30:00+08:00", "--json"])
    assert rc == 1, payload
    assert payload["next_window"]["name"] == "main"
    assert payload["next_window"]["start"] == "2026-11-03T16:30:00+08:00"


# ---------- 文档/组合声明旁路 ----------
def test_docs_bypass_in_forbidden():
    rc, payload, _ = _json(["check", "--files", "docs/plans/x.md",
                            "--now", "2026-11-02T10:00:00+08:00", "--json"])
    assert rc == 0, payload
    assert payload["allowed"] is True
    assert payload["mode"] == "docs_bypass"
    assert payload["bypass_files"] == ["docs/plans/x.md"]


def test_compositions_bypass_at_night():
    rc, payload, _ = _json(["check", "--files", "compositions/acad1-legion/manifest.yaml",
                            "--now", "2026-11-02T20:10:00+08:00", "--json"])
    assert rc == 0, payload
    assert payload["mode"] == "docs_bypass"


def test_deliverables_and_root_md_bypass():
    rc1, p1, _ = _json(["check", "--files", "deliverables/x.csv",
                        "--now", "2026-11-02T11:00:00+08:00", "--json"])
    assert rc1 == 0 and p1["mode"] == "docs_bypass"
    rc2, p2, _ = _json(["check", "--files", "README.md",
                        "--now", "2026-11-02T11:00:00+08:00", "--json"])
    assert rc2 == 0 and p2["mode"] == "docs_bypass"


def test_mixed_files_not_bypass():
    rc, payload, _ = _json(["check", "--files", "docs/a.md,portal/api_kg.py",
                            "--now", "2026-11-02T10:00:00+08:00", "--json"])
    assert rc == 1, payload
    assert payload["allowed"] is False
    assert payload["mode"] == "forbidden"
    assert payload["non_bypass_files"] == ["portal/api_kg.py"]


def test_code_files_in_window_not_needed_for_bypass():
    rc, payload, _ = _json(["check", "--files", "portal/api_kg.py",
                            "--now", "2026-11-02T16:45:00+08:00", "--json"])
    assert rc == 0, payload
    assert payload["mode"] == "window"
    assert payload["bypass_files"] == []


# ---------- --force / --json / 参数 ----------
def test_force_allows_and_warns():
    rc, payload, _ = _json(["check", "--now", "2026-11-02T10:00:00+08:00",
                            "--files", "portal/api_kg.py", "--force", "--json"])
    assert rc == 0, payload
    assert payload["allowed"] is True
    assert payload["mode"] == "force"
    assert payload["force"] is True
    assert "紧急放行" in payload["reason"]
    assert payload["force_warning"]


def test_json_contract_fields():
    rc, payload, _ = _json(["status", "--now", "2026-11-02T16:45:00+08:00", "--json"])
    assert rc == 0, payload
    for key in ("allowed", "reason", "window", "next_window", "forbidden_until",
                "mode", "windows", "forbidden_periods", "now", "timezone"):
        assert key in payload, key
    assert len(payload["windows"]) == 2
    assert len(payload["forbidden_periods"]) == 4
    assert payload["schema"].startswith("zerobound/merge-train/")


def test_naive_now_treated_as_shanghai():
    rc, payload, _ = _json(["check", "--now", "2026-11-02T16:45:00", "--json"])
    assert rc == 0, payload
    assert payload["allowed"] is True
    assert payload["now"].endswith("+08:00")


def test_invalid_now_rc2():
    rc, out, err = _run(["check", "--now", "not-a-date"])
    assert rc == 2, (rc, out, err)
    assert "无法解析" in (out + err)


def test_status_text_mentions_rules():
    rc, out, err = _run(["status", "--now", "2026-11-02T16:45:00+08:00"])
    assert rc == 0, (rc, out, err)
    for word in ("主窗口", "次窗口", "禁并期", "文档旁路", "YES"):
        assert word in out, word
