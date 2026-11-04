# -*- coding: utf-8 -*-
"""curation 回归测试（task-159）：learning_curation 去重 + 指标说明书排除 + 幂等。

契约：pipeline 重建 units 后，`learning_curation --apply` 必须
  1) 调 knowledge_dedupe 去重（失败 rc 非 0 要可见）；
  2) 生成 <pid>_units_effective.jsonl 并排除「指标说明书」整文件来源的单元；
  3) 落 learning_curation_<date>.json/md 报告（计数自洽）；
  4) 重复执行幂等（去重后无新重复、effective 计数不变）。

全部在 C:\\Temp（pytest tmp_path）内用临时 KB/reports 目录跑，monkeypatch KB/R/projects；
knowledge_dedupe 子进程用 fake 替换（不写生产数据根/报告）。运行：
  C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_learning_curation.py
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import learning_curation as lc  # noqa: E402


# ------------------------------------------------------------------ 工具
def _mk_kb(tmp_path: Path) -> Path:
    kb = tmp_path / "KB"
    units_dir = kb / "30_知识单元"
    units_dir.mkdir(parents=True)
    return kb


def _write_units(path: Path) -> None:
    lines = [
        {"unit_id": "u1", "project_id": "p1", "claim": "真实用户痛点是创业第一性原理。",
         "evidence": {"file": "a.md", "line": 1}},
        {"unit_id": "u1", "project_id": "p1", "claim": "真实用户痛点是创业第一性原理。",
         "evidence": {"file": "a.md", "line": 1}},                      # 精确重复 → 应被去重
        {"unit_id": "u2", "project_id": "p1", "claim": "指标说明书的垃圾转写内容。",
         "evidence": {"file": "指标说明书.md", "line": 2}},              # 应被 effective 排除
        {"unit_id": "u3", "project_id": "p1", "claim": "仓位管理决定长期生存率。",
         "evidence": {"file": "b.md", "line": 3}},
    ]
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                    encoding="utf-8")


def _fake_dedupe(cmd, **kwargs):
    """模拟 knowledge_dedupe --apply：去掉同文件内精确重复行，rc=0。"""
    if "--apply" in cmd and "--input" in cmd:
        inp = Path(cmd[cmd.index("--input") + 1])
        seen, kept = set(), []
        for line in inp.read_text(encoding="utf-8").splitlines():
            key = line.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            kept.append(line)
        inp.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    kb = _mk_kb(tmp_path)
    reports = tmp_path / "reports"
    reports.mkdir()
    draft = kb / "30_知识单元" / "p1_units_draft.jsonl"
    _write_units(draft)
    monkeypatch.setattr(lc, "KB", kb)
    monkeypatch.setattr(lc, "R", reports)
    monkeypatch.setattr(lc, "projects", lambda: ["p1"])
    monkeypatch.setattr(lc.subprocess, "run", _fake_dedupe)
    return {"kb": kb, "reports": reports, "draft": draft,
            "effective": draft.with_name("p1_units_effective.jsonl")}


def _run(monkeypatch, apply: bool, date: str = "20261104") -> int:
    argv = ["learning_curation.py", "--date", date] + (["--apply"] if apply else [])
    monkeypatch.setattr(sys, "argv", argv)
    return lc.main()


def _report(reports: Path, date: str = "20261104") -> dict:
    return json.loads((reports / f"learning_curation_{date}.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ 1. helpers
def test_helpers_lines_and_projects(tmp_path, monkeypatch):
    p = tmp_path / "x.jsonl"
    assert lc.lines(p) == 0
    p.write_text("a\nb\nc\n", encoding="utf-8")
    assert lc.lines(p) == 3
    # projects() 读注册表：返回非 archived 的项目（只读、不写）
    projs = lc.projects()
    assert isinstance(projs, list) and projs and all(isinstance(x, str) for x in projs)
    assert lc.EXCLUDE_PAT == "指标说明书"


# ------------------------------------------------------------------ 2. dry-run：不写 effective/不改 units
def test_dry_run_counts_without_writing(env, monkeypatch):
    draft = env["draft"]
    before = draft.read_text(encoding="utf-8")
    rc = _run(monkeypatch, apply=False)
    assert rc == 0
    assert draft.read_text(encoding="utf-8") == before, "dry-run 不得改 units"
    assert not env["effective"].exists(), "dry-run 不得写 effective 文件"
    rep = _report(env["reports"])
    row = rep["projects"][0]
    assert rep["mode"] == "dry-run"
    assert row["units_before"] == row["units_after"] == 4
    # dry-run 不执行知识去重：4 行中排除 1 条指标说明书 → 3 条有效（含 1 条未去重重复）
    assert row["effective"] == 3 and row["excluded"] == 1
    assert row["dedupe_rc"] is None
    assert (env["reports"] / "learning_curation_20261104.md").is_file()


# ------------------------------------------------------------------ 3. apply：去重 + effective 排除生效
def test_apply_dedupes_and_writes_effective(env, monkeypatch):
    rc = _run(monkeypatch, apply=True)
    assert rc == 0
    draft_lines = env["draft"].read_text(encoding="utf-8").strip().splitlines()
    assert len(draft_lines) == 3, "精确重复行应被去重（4→3）"
    eff = env["effective"]
    assert eff.is_file()
    eff_lines = [json.loads(x) for x in eff.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(eff_lines) == 2
    assert all("指标说明书" not in str((u.get("evidence") or {}).get("file")) for u in eff_lines)
    rep = _report(env["reports"])
    row = rep["projects"][0]
    assert rep["mode"] == "apply"
    assert row["units_before"] == 4 and row["units_after"] == 3
    assert row["effective"] == 2 and row["excluded"] == 1 and row["dedupe_rc"] == 0


# ------------------------------------------------------------------ 4. 幂等：重复 apply 不改变结果
def test_apply_idempotent(env, monkeypatch):
    _run(monkeypatch, apply=True)
    first_units = env["draft"].read_text(encoding="utf-8")
    first_eff = env["effective"].read_text(encoding="utf-8")
    rep1 = _report(env["reports"])["projects"][0]

    _run(monkeypatch, apply=True)
    rep2 = _report(env["reports"])["projects"][0]
    assert env["draft"].read_text(encoding="utf-8") == first_units, "二次去重不应再改 units"
    assert env["effective"].read_text(encoding="utf-8") == first_eff, "effective 应幂等"
    # 第 2 次运行的 units_before = 第 1 次 units_after（去重后基线），其余计数必须完全不变
    assert rep2["units_before"] == rep1["units_after"]
    for key in ("units_after", "effective", "excluded", "dedupe_rc"):
        assert rep1[key] == rep2[key], f"{key} 不幂等: {rep1[key]} != {rep2[key]}"
