# -*- coding: utf-8 -*-
"""黄忠验收单证据 schema lint 单测（tmp 夹具，绝不触碰真实 work/ 或 deliverables）。

覆盖 P8 要求：
  · v1 完整单 → ok/完整率 100%/无 issues/md 报告生成；
  · v1 缺字段 → issues 正确（mode/blocking/rollback_point/verdict/evidence 字段）；
  · legacy（缺证据年龄字段）→ 归类正确、--strict 退出码 1；
  · 坏 JSON → error 文件逐条列出、不编造；默认 rc0、--strict rc1；
  · 同 sha 去重保留最新 + dup 统计；
  · 连续率计算（legacy 不计入 v1 完整率分母）；
  · work 目录缺失 fail-visible；env HUANGZHONG_WORK_DIR / HUANGZHONG_DELIVERABLES_DIR 覆盖；
  · CLI --json / 人类可读输出 / 退出码。
"""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_evidence_lint", _TOOLS / "huangzhong_evidence_lint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _setup(tmp_path, monkeypatch):
    """隔离环境：work/out 都指向 tmp；清掉可能干扰的环境变量。"""
    work = tmp_path / "work"
    out = tmp_path / "deliverables"
    work.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HUANGZHONG_WORK_DIR", str(work))
    monkeypatch.setenv("HUANGZHONG_DELIVERABLES_DIR", str(out))
    return work, out


def _now():
    return datetime(2026, 10, 31, 13, 0, 0, tzinfo=timezone.utc)


def _v1(sha="a" * 40, **over):
    """v1 完整单（含证据年龄字段）。"""
    data = {
        "ok": True,
        "acceptance_id": "HZ-test",
        "generated_at": "2026-10-31T10:00:00+08:00",
        "mode": "observe",
        "blocking": False,
        "commit": {"ok": True, "sha": sha, "subject": "test"},
        "evidence": {
            "baseline": {"ok": True, "generated_at": "2026-10-31T00:00:00+08:00",
                         "age_hours": 10.0, "stale": False},
            "redteam": {"ok": True, "generated_at": "2026-10-31T00:00:00+08:00",
                        "age_hours": 10.0, "stale": False},
        },
        "verdict": "PASS",
        "reasons": [],
        "rollback_point": "git revert " + sha[:12],
        "closed_by": "老田（人工）",
        "read_only": True,
    }
    for key, value in over.items():
        data[key] = value
    return data


def _legacy(sha="b" * 40, generated_at="2026-10-28T07:18:01+08:00"):
    """v0 legacy 单（缺证据 age_hours/stale 字段）。"""
    return {
        "ok": True,
        "acceptance_id": "HZ-legacy",
        "generated_at": generated_at,
        "mode": "observe",
        "blocking": False,
        "commit": {"ok": True, "sha": sha, "subject": "legacy"},
        "evidence": {
            "baseline": {"ok": True, "passed": 100, "failed": 0, "error": 0,
                         "generated_at": "2026-10-28T00:09:51+08:00", "path": "x"},
            "redteam": {"ok": True, "total": 3, "passed": 3, "pass_rate": 1.0,
                        "generated_at": "2026-10-27T15:59:16+08:00", "path": "y"},
        },
        "verdict": "PASS",
        "reasons": [],
        "rollback_point": "git revert " + sha[:12],
        "closed_by": "老田（人工）",
        "read_only": True,
    }


def _write(work: Path, name: str, data) -> Path:
    p = work / name
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


# ---------------------------------------------------------------- 基础路径

def test_v1_complete_ok_and_md(tmp_path, monkeypatch):
    work, out = _setup(tmp_path, monkeypatch)
    h = _load()
    _write(work, "acceptance_aaaa1111.json", _v1("a" * 40))
    rep = h.lint(now=_now())
    assert rep["ok"] is True
    assert rep["scanned"] == 1 and rep["unique"] == 1 and rep["total"] == 1
    assert rep["legacy_count"] == 0 and rep["v1_count"] == 1
    assert rep["v1_complete_count"] == 1 and rep["completeness"] == 1.0
    assert rep["error_count"] == 0 and rep["strict_pass"] is True
    assert rep["files"][0]["class"] == "v1" and rep["files"][0]["issues"] == []
    assert rep["md_report"] and Path(rep["md_report"]).is_file()
    assert Path(rep["md_report"]).name == "2026-10-31_黄忠验收单证据lint_130000.md"
    assert Path(rep["md_report"]).parent == out
    md = Path(rep["md_report"]).read_text(encoding="utf-8")
    for key in ("汇总", "legacy 清单", "v1 问题清单", "坏文件", "完整率"):
        assert key in md, key


def test_v1_missing_fields_issues(tmp_path, monkeypatch):
    """v1 单保留证据年龄字段但其它必需字段缺失/不合法 → issues 逐项列出。"""
    work, out = _setup(tmp_path, monkeypatch)
    h = _load()
    data = _v1("c" * 40)
    data["mode"] = "enforce"
    data["blocking"] = True
    data["rollback_point"] = ""
    data["verdict"] = "MAYBE"
    data["commit"] = {"ok": True, "sha": ""}
    _write(work, "acceptance_cccc2222.json", data)
    rep = h.lint(now=_now())
    assert rep["v1_count"] == 1 and rep["v1_complete_count"] == 0
    assert rep["completeness"] == 0.0
    issues = rep["v1_issues"][0]["issues"]
    joined = " | ".join(issues)
    for key in ("commit.sha", "verdict=MAYBE", "mode", "blocking", "rollback_point"):
        assert key in joined, (key, issues)
    assert rep["strict_pass"] is False
    md = Path(rep["md_report"]).read_text(encoding="utf-8")
    assert "acceptance_cccc2222.json" in md and "rollback_point" in md


def test_v1_evidence_field_issues(tmp_path, monkeypatch):
    work, out = _setup(tmp_path, monkeypatch)
    h = _load()
    data = _v1("d" * 40)
    data["evidence"]["baseline"]["generated_at"] = ""
    data["evidence"]["redteam"]["age_hours"] = None   # 保留键 → 仍归 v1，但字段非法
    data["evidence"]["redteam"]["stale"] = "no"
    _write(work, "acceptance_dddd3333.json", data)
    rep = h.lint(now=_now())
    assert rep["v1_count"] == 1 and rep["v1_complete_count"] == 0
    issues = rep["v1_issues"][0]["issues"]
    assert "evidence.baseline.generated_at" in issues
    assert "evidence.redteam.age_hours" in issues
    assert "evidence.redteam.stale" in issues


def test_legacy_classified_and_strict_rc1(tmp_path, monkeypatch):
    work, out = _setup(tmp_path, monkeypatch)
    h = _load()
    _write(work, "acceptance_bbbb1111.json", _legacy("b" * 40))
    rep = h.lint(strict=True, now=_now())
    assert rep["ok"] is True
    assert rep["legacy_count"] == 1 and rep["v1_count"] == 0
    assert rep["legacy"][0]["file"] == "acceptance_bbbb1111.json"
    assert set(rep["legacy"][0]["missing"]) == {
        "evidence.baseline.age_hours", "evidence.baseline.stale",
        "evidence.redteam.age_hours", "evidence.redteam.stale"}
    assert rep["strict"] is True and rep["strict_pass"] is False
    assert rep["strict_fail_reasons"]
    assert h.main(["--work-dir", str(work), "--out-dir", str(out),
                   "--strict", "--json"]) == 1
    # 默认（非 strict）：仅报告 → rc0
    assert h.main(["--work-dir", str(work), "--out-dir", str(out), "--json"]) == 0


def test_completeness_excludes_legacy(tmp_path, monkeypatch):
    work, out = _setup(tmp_path, monkeypatch)
    h = _load()
    _write(work, "acceptance_aaaa0001.json", _v1("a" * 40))
    bad = _v1("c" * 40)
    bad["mode"] = "blocking"
    _write(work, "acceptance_cccc0001.json", bad)
    _write(work, "acceptance_bbbb0001.json", _legacy("b" * 40))
    rep = h.lint(now=_now())
    assert rep["legacy_count"] == 1
    assert rep["v1_count"] == 2 and rep["v1_complete_count"] == 1
    assert rep["completeness"] == 0.5
    assert len(rep["files"]) == 3


# ---------------------------------------------------------------- 坏文件

def test_bad_json_files_fail_visible_default_rc0_strict_rc1(tmp_path, monkeypatch):
    work, out = _setup(tmp_path, monkeypatch)
    h = _load()
    _write(work, "acceptance_bad00001.json", "{not json")
    _write(work, "acceptance_bad00002.json", "[]")
    _write(work, "acceptance_good0001.json", _v1())
    rep = h.lint(now=_now())
    assert rep["ok"] is True
    assert rep["scanned"] == 3 and rep["unique"] == 1
    assert rep["error_count"] == 2
    files = {e["file"] for e in rep["errors"]}
    assert files == {"acceptance_bad00001.json", "acceptance_bad00002.json"}
    reasons = " | ".join(e["error"] for e in rep["errors"])
    assert "JSON 解析失败" in reasons and "顶层不是对象" in reasons
    md = Path(rep["md_report"]).read_text(encoding="utf-8")
    assert "acceptance_bad00001.json" in md and "acceptance_bad00002.json" in md
    assert rep["strict_pass"] is False
    assert h.main(["--work-dir", str(work), "--out-dir", str(out), "--json"]) == 0
    assert h.main(["--work-dir", str(work), "--out-dir", str(out),
                   "--strict", "--json"]) == 1


# ---------------------------------------------------------------- 去重

def test_dedupe_same_sha_keeps_latest(tmp_path, monkeypatch):
    work, out = _setup(tmp_path, monkeypatch)
    h = _load()
    older = _v1("e" * 40)
    older["generated_at"] = "2026-10-31T08:00:00+08:00"
    older["note"] = "old"
    newer = _v1("e" * 40)
    newer["generated_at"] = "2026-10-31T12:00:00+08:00"
    newer["note"] = "new"
    _write(work, "acceptance_eeee0001.json", older)
    _write(work, "acceptance_eeee0002.json", newer)
    rep = h.lint(now=_now())
    assert rep["scanned"] == 2 and rep["unique"] == 1 and rep["total"] == 2
    assert rep["duplicates"] == 1
    assert rep["duplicates_detail"] == [{
        "sha": "e" * 40,
        "kept": "acceptance_eeee0002.json",
        "dropped": ["acceptance_eeee0001.json"]}]
    assert rep["files"][0]["file"] == "acceptance_eeee0002.json"
    assert rep["v1_count"] == 1 and rep["v1_complete_count"] == 1


def test_dedupe_mixed_legacy_keeps_v1(tmp_path, monkeypatch):
    """同 sha 最新单是 v1、旧单是 legacy → 只 lint 最新，legacy 不计入。"""
    work, out = _setup(tmp_path, monkeypatch)
    h = _load()
    _write(work, "acceptance_ffff0001.json",
           _legacy("f" * 40, generated_at="2026-10-30T08:00:00+08:00"))
    _write(work, "acceptance_ffff0002.json",
           _v1("f" * 40, generated_at="2026-10-31T12:00:00+08:00"))
    rep = h.lint(now=_now())
    assert rep["duplicates"] == 1
    assert rep["legacy_count"] == 0 and rep["v1_count"] == 1


# ------------------------------------------------------------ 路径/env/CLI

def test_empty_work_dir_ok(tmp_path, monkeypatch):
    work, out = _setup(tmp_path, monkeypatch)
    h = _load()
    rep = h.lint(now=_now())
    assert rep["ok"] is True and rep["scanned"] == 0
    assert rep["legacy_count"] == 0 and rep["v1_count"] == 0
    assert rep["completeness"] == 1.0
    assert rep["md_report"] and Path(rep["md_report"]).is_file()
    assert h.main(["--work-dir", str(work), "--out-dir", str(out), "--json"]) == 0


def test_work_dir_missing_fail_visible(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    h = _load()
    rep = h.lint(work_dir=tmp_path / "nope", out_dir=tmp_path / "out")
    assert rep["ok"] is False and "work 目录不存在" in rep["error"]
    assert rep["strict_pass"] is False
    assert h.main(["--work-dir", str(tmp_path / "nope"),
                   "--out-dir", str(tmp_path / "out"), "--json"]) == 1


def test_env_override_paths(tmp_path, monkeypatch):
    work, out = _setup(tmp_path, monkeypatch)
    h = _load()
    _write(work, "acceptance_aaaa1111.json", _v1())
    rep = h.lint(now=_now())  # 不传参 → env 覆盖生效
    assert rep["work_dir"] == str(work)
    assert Path(rep["md_report"]).parent == out
    # --work-dir/--out-dir 显式参数优先于 env
    other_out = tmp_path / "out_explicit"
    rep2 = h.lint(work_dir=work, out_dir=other_out, now=_now())
    assert rep2["ok"] is True
    assert Path(rep2["md_report"]).parent == other_out


def test_cli_json_and_human_output(tmp_path, monkeypatch, capsys):
    work, out = _setup(tmp_path, monkeypatch)
    h = _load()
    _write(work, "acceptance_aaaa1111.json", _v1())
    _write(work, "acceptance_bbbb1111.json", _legacy("b" * 40))
    rc = h.main(["--json", "--work-dir", str(work), "--out-dir", str(out)])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    for key in ("ok", "generated_at", "work_dir", "out_dir", "scanned", "total",
                "unique", "duplicates", "legacy_count", "v1_count",
                "v1_complete_count", "completeness", "files", "legacy",
                "v1_issues", "errors", "error_count", "strict", "strict_pass",
                "md_report"):
        assert key in data, key
    assert data["scanned"] == 2 and data["legacy_count"] == 1
    rc = h.main(["--work-dir", str(work), "--out-dir", str(out)])
    assert rc == 0
    human = capsys.readouterr().out
    assert "证据lint" in human and "legacy 1" in human and "v1 1" in human
