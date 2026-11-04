# -*- coding: utf-8 -*-
"""E2E 回归（task-159）：pipeline 重建 → curation --apply → effective，契约与幂等。

场景（模拟真实 BUG）：每日 learning_pipeline 重建 units 会覆盖人工治理结果
（重复行回流 + 指标说明书垃圾单元回流）。本测试验证：
  1) pipeline 重建后跑 learning_curation --apply：去重计数正确、effective 排除指标说明书生效；
  2) **再次重建（相同输入）后再 curation**：结果仍一致（治理可重复收敛，不漂移）；
  3) 全部在 C:\\Temp（tmp_path）临时 KB/reports 跑，不碰生产数据根/知识库实文件；
     knowledge_dedupe 子进程用 fake 替换（不写生产报告）。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_learning_pipeline_curation.py
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import learning_curation as lc  # noqa: E402


def _pipeline_rebuild(draft: Path) -> None:
    """模拟 pipeline 重建：写回未治理的 units（含 1 条精确重复 + 2 条指标说明书单元）。"""
    units = [
        {"unit_id": "u1", "project_id": "p1", "claim": "止损是仓位管理的第一道闸门。",
         "evidence": {"file": "a.md", "line": 1}},
        {"unit_id": "u1", "project_id": "p1", "claim": "止损是仓位管理的第一道闸门。",
         "evidence": {"file": "a.md", "line": 1}},                      # 重复回流
        {"unit_id": "u2", "project_id": "p1", "claim": "指标说明书：KDJ 参数设置说明。",
         "evidence": {"file": "指标说明书.md", "line": 5}},
        {"unit_id": "u3", "project_id": "p1", "claim": "指标说明书：MACD 金叉定义。",
         "evidence": {"file": "指标说明书.md", "line": 9}},
        {"unit_id": "u4", "project_id": "p1", "claim": "趋势跟踪的核心是让利润奔跑。",
         "evidence": {"file": "b.md", "line": 2}},
    ]
    draft.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in units) + "\n",
                     encoding="utf-8")


def _fake_dedupe(cmd, **kwargs):
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


def _curate(monkeypatch, kb: Path, reports: Path) -> dict:
    monkeypatch.setattr(lc, "KB", kb)
    monkeypatch.setattr(lc, "R", reports)
    monkeypatch.setattr(lc, "projects", lambda: ["p1"])
    monkeypatch.setattr(lc.subprocess, "run", _fake_dedupe)
    monkeypatch.setattr(sys, "argv", ["learning_curation.py", "--apply", "--date", "20261104"])
    assert lc.main() == 0
    return json.loads((reports / "learning_curation_20261104.json").read_text(encoding="utf-8"))


def _state(row: dict) -> tuple:
    return (row["units_before"], row["units_after"], row["effective"], row["excluded"], row["dedupe_rc"])


def test_pipeline_curation_effective_idempotent(tmp_path, monkeypatch):
    kb = tmp_path / "KB"
    draft = kb / "30_知识单元" / "p1_units_draft.jsonl"
    draft.parent.mkdir(parents=True)
    reports = tmp_path / "reports"
    reports.mkdir()

    # 第 1 轮：pipeline 重建（5 行：1 重复 + 2 指标说明书）→ curation
    _pipeline_rebuild(draft)
    rep1 = _curate(monkeypatch, kb, reports)
    row1 = rep1["projects"][0]
    assert _state(row1) == (5, 4, 2, 2, 0), f"curation 计数不符: {row1}"
    eff = draft.with_name("p1_units_effective.jsonl")
    eff_units = [json.loads(x) for x in eff.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(eff_units) == 2
    assert all("指标说明书" not in str((u.get("evidence") or {}).get("file")) for u in eff_units)
    assert {u["unit_id"] for u in eff_units} == {"u1", "u4"}

    # 第 2 轮：pipeline 再次重建同样输入（覆盖治理结果）→ curation 再收敛，结果与第 1 轮一致
    _pipeline_rebuild(draft)
    rep2 = _curate(monkeypatch, kb, reports)
    row2 = rep2["projects"][0]
    assert _state(row2) == _state(row1), f"pipeline 重建后 curation 不幂等: {_state(row1)} vs {_state(row2)}"
    eff_units2 = [json.loads(x) for x in eff.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert {u["unit_id"] for u in eff_units2} == {"u1", "u4"}
    # 报告 md 每次覆盖且含两项目计数
    md = (reports / "learning_curation_20261104.md").read_text(encoding="utf-8")
    assert "学习单元后置治理" in md and "| p1 |" in md
