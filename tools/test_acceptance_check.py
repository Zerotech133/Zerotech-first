# -*- coding: utf-8 -*-
"""验收清单检查器测试（2026-11-16 WO-ACCEPTANCE-CHECK）。

全部在 tmp 内构造证据文件，不碰生产。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load():
    spec = importlib.util.spec_from_file_location(
        "acceptance_check_t", REPO / "tools" / "acceptance_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ac = _load()


def test_file_exists(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    assert ac.check_file_exists({"path": "a.txt"}, tmp_path)[0] is True
    ok, ev = ac.check_file_exists({"path": "missing.txt"}, tmp_path)
    assert ok is False and "0 B" in ev


def test_file_contains_reports_line(tmp_path):
    (tmp_path / "a.py").write_text("line1\nNO_FILL_YET = 1\n", encoding="utf-8")
    ok, ev = ac.check_file_contains({"path": "a.py", "pattern": "NO_FILL_YET"}, tmp_path)
    assert ok is True and ev.startswith("L2:")


def test_not_contains_flags_unexpected_hit(tmp_path):
    (tmp_path / "a.py").write_text("devtask.py drop T1\n", encoding="utf-8")
    ok, ev = ac.check_not_contains({"path": "a.py", "pattern": r"devtask\.py drop"}, tmp_path)
    assert ok is False and "意外命中" in ev


def test_json_field_with_pointer_and_set(tmp_path):
    (tmp_path / "r.json").write_text(json.dumps({"a": {"b": {"c": "NO_FILL_YET"}}}), encoding="utf-8")
    ok, _ = ac.check_json_field({"path": "r.json", "pointer": "a.b.c", "in": ["PASS", "NO_FILL_YET"]}, tmp_path)
    assert ok is True
    ok2, _ = ac.check_json_field({"path": "r.json", "pointer": "a.b.c", "in": ["PASS"]}, tmp_path)
    assert ok2 is False


def test_json_field_bad_json(tmp_path):
    (tmp_path / "bad.json").write_text("{oops", encoding="utf-8")
    ok, ev = ac.check_json_field({"path": "bad.json", "pointer": "a"}, tmp_path)
    assert ok is False and "解析失败" in ev


def test_regex_count(tmp_path):
    (tmp_path / "m.md").write_text("| **A** |\n| **B** |\n| x |\n", encoding="utf-8")
    ok, _ = ac.check_regex_count({"path": "m.md", "pattern": r"\*\*[ABCDE]\*\*", "expect": 2}, tmp_path)
    assert ok is True
    ok2, _ = ac.check_regex_count({"path": "m.md", "pattern": r"\*\*[ABCDE]\*\*", "expect": 3}, tmp_path)
    assert ok2 is False


def test_count_equals(tmp_path):
    (tmp_path / "m.md").write_text("共 **2** 条\n| **A** |\n| **B** |\n", encoding="utf-8")
    ok, ev = ac.check_count_equals({"path": "m.md", "pattern_a": r"共 \*\*(\d+)\*\* 条",
                                    "pattern_b": r"\*\*[ABCDE]\*\*"}, tmp_path)
    assert ok is True and "声明=2" in ev


def test_cmd_expect_rc(tmp_path):
    ok, _ = ac.check_cmd({"cmd": [sys.executable, "-c", "print('hello')"], "expect_rc": 0}, tmp_path)
    assert ok is True
    ok2, _ = ac.check_cmd({"cmd": [sys.executable, "-c", "import sys; sys.exit(3)"], "expect_rc": 0}, tmp_path)
    assert ok2 is False


def test_cmd_unchanged_detects_mutation(tmp_path):
    target = tmp_path / "ledger.jsonl"
    target.write_text("x\n", encoding="utf-8")
    before = target.stat().st_mtime
    # 命令不改文件 → unchanged=True
    ok, ev = ac.check_cmd({"cmd": [sys.executable, "-c", "print(1)"], "expect_rc": 0,
                           "unchanged": "ledger.jsonl"}, tmp_path)
    assert ok is True and "unchanged=True" in ev
    # 命令写文件 → unchanged=False
    time.sleep(0.01)
    ok2, ev2 = ac.check_cmd({"cmd": [sys.executable, "-c",
                                     "open('ledger.jsonl','a').write('y')"],
                             "expect_rc": 0, "unchanged": "ledger.jsonl"}, tmp_path)
    assert ok2 is False and "unchanged=False" in ev2
    assert target.stat().st_mtime >= before


def test_task_next_run_variants(monkeypatch):
    """task_next_run：可读即过、N/A 需 allow_na、任务缺失必 FAIL。"""
    import types

    def fake_run(*_a, **_k):
        return types.SimpleNamespace(
            stdout="PortalKeepalive|11/16/2026 04:49:06\nD1T1Check|N/A\n", returncode=0)

    monkeypatch.setattr(ac, "subprocess", types.SimpleNamespace(run=fake_run))
    ok, ev = ac.check_task_next_run({"tasks": ["PortalKeepalive"]}, Path("."))
    assert ok is True and "PortalKeepalive=" in ev
    # 一次性任务跑完 → N/A，未声明 allow_na 时判 FAIL 并给出提示
    ok2, ev2 = ac.check_task_next_run({"tasks": ["D1T1Check"]}, Path("."))
    assert ok2 is False and "allow_na" in ev2
    ok3, _ = ac.check_task_next_run({"tasks": ["D1T1Check"], "allow_na": True}, Path("."))
    assert ok3 is True
    # 任务不存在 → MISSING
    ok4, ev4 = ac.check_task_next_run({"tasks": ["NoSuchTask"]}, Path("."))
    assert ok4 is False and "MISSING" in ev4
    # 混合：一个有未来时间 + 一个 allow_na 的 N/A
    ok5, _ = ac.check_task_next_run({"tasks": ["PortalKeepalive", "D1T1Check"], "allow_na": True}, Path("."))
    assert ok5 is True


def test_run_spec_unknown_kind_and_exit_code(tmp_path):
    spec = {"checks": [
        {"id": "1", "section": "s", "name": "ok", "kind": "file_exists", "path": "a.txt"},
        {"id": "2", "section": "s", "name": "bad kind", "kind": "nope"},
    ]}
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    res = ac.run_spec(spec, tmp_path)
    assert res[0]["ok"] is True
    assert res[1]["ok"] is False and "未知 kind" in res[1]["evidence"]
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    rc = ac.main(["--spec", str(spec_path), "--repo", str(tmp_path), "--json"])
    assert rc == 1  # 退出码 = 失败项数
