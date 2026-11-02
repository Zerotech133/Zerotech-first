# -*- coding: utf-8 -*-
"""统一发布器 learning_release 测试（task-132，全部 mock，不真合并/不联网）。

覆盖：
  1) dry-run 正常路径：gate 预检 ok → approved 校验 → card_merge dry-run → 回执（不写卡、不 digest）；
  2) proposals 无 approved 卡 → 拒绝并停止（不调用 card_merge）；
  3) gate 预检失败 → 立即停止（不调用 card_merge）；
  4) mock 全流程 apply 成功：gate→approved→card_merge --apply→digest→gate strict→回执 ok；
  5) 后置 gate strict 失败 → 停止并给回滚提示；
  6) 回执 schema/JSON+MD 落盘（tmp）；真实学习卡未被测试改动。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
S = REPO / "portal" / "static"
CARDS = S / "kb_learning_cards.json"
SCRIPT = REPO / "tools" / "kb_corpus" / "learning_release.py"

_spec = importlib.util.spec_from_file_location("lr_test", SCRIPT)
lr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = lr
_spec.loader.exec_module(lr)  # type: ignore[union-attr]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_proposals(path: Path, approved: int, total: int = 2) -> None:
    cards = []
    for i in range(total):
        cards.append({"card_id": f"C{i+1}", "concept": f"概念{i+1}", "q": "q", "a": "a",
                      "review": {"checkboxes": {"pass": i < approved, "revise": False, "reject": False}}})
    path.write_text(json.dumps({"cards": cards}, ensure_ascii=False), encoding="utf-8")


def ok_gate(stage="publish"):
    return {"ok": True, "fail": 0, "summary": {"projects": 1, "checks": 3, "fail": 0}}


def merge_runner(stats: dict, rc: int = 0):
    def run(cmd):
        return {"rc": rc, "stdout": json.dumps({"stats": stats, "backup": "backup/bak.json"},
                                               ensure_ascii=False), "stderr": ""}
    return run


# ---------------------------------------------------------------------------
# 1) dry-run 正常路径
# ---------------------------------------------------------------------------
def test_dryrun_success(tmp_path):
    proposals = tmp_path / "props.json"
    write_proposals(proposals, approved=1)
    before = sha(CARDS)
    calls = {"n": 0}

    def run(cmd):
        calls["n"] += 1
        assert "--no-preview" in cmd and "--apply" not in cmd
        return {"rc": 0, "stdout": json.dumps({"stats": {"would_add": 0, "resulting_cards": 172,
                                                          "approved": 1, "base_cards": 172}}), "stderr": ""}

    rec = lr.release(project="laomu", proposals=proposals, apply=False,
                     out_dir=tmp_path, gate_fn=ok_gate, run_fn=run)
    assert rec["ok"] is True and rec["apply"] is False and rec["mode"] == "dry-run"
    names = [s["name"] for s in rec["steps"]]
    assert names == ["gate_publish_precheck", "validate_approved", "card_merge_dry_run"]
    assert rec["merge_stats"]["would_add"] == 0
    assert "learning_digest" not in names and "gate_publish_post_strict" not in names
    assert Path(rec["receipt"]["json"]).is_file() and Path(rec["receipt"]["md"]).is_file()
    assert sha(CARDS) == before, "dry-run 不得改学习卡"
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# 2) 无 approved → 停止
# ---------------------------------------------------------------------------
def test_no_approved_stops(tmp_path):
    proposals = tmp_path / "props.json"
    write_proposals(proposals, approved=0)
    called = {"n": 0}

    def run(cmd):
        called["n"] += 1
        return {"rc": 0, "stdout": "{}", "stderr": ""}

    rec = lr.release(project="p", proposals=proposals, out_dir=tmp_path, gate_fn=ok_gate, run_fn=run)
    assert rec["ok"] is False
    assert rec["stopped_at"] == "validate_approved" and "无 approved" in rec["reason"]
    assert called["n"] == 0, "无 approved 不得调用 card_merge"


# ---------------------------------------------------------------------------
# 3) gate 预检失败 → 停止
# ---------------------------------------------------------------------------
def test_gate_precheck_fail_stops(tmp_path):
    proposals = tmp_path / "props.json"
    write_proposals(proposals, approved=1)
    called = {"n": 0}

    def run(cmd):
        called["n"] += 1
        return {"rc": 0, "stdout": "{}", "stderr": ""}

    def bad_gate(stage="publish"):
        return {"ok": False, "fail": 2, "summary": {"fail": 2}}

    rec = lr.release(project="p", proposals=proposals, out_dir=tmp_path, gate_fn=bad_gate, run_fn=run)
    assert rec["ok"] is False and rec["stopped_at"] == "gate_publish_precheck"
    assert called["n"] == 0


# ---------------------------------------------------------------------------
# 4) apply 全流程成功（mock）
# ---------------------------------------------------------------------------
def test_apply_full_mock_success(tmp_path):
    proposals = tmp_path / "props.json"
    write_proposals(proposals, approved=2)
    cmds = []

    def run(cmd):
        cmds.append(cmd)
        assert "--apply" in cmd and "--no-preview" in cmd
        return {"rc": 0, "stdout": json.dumps({"stats": {"would_add": 2, "resulting_cards": 174,
                                                          "approved": 2, "base_cards": 172},
                                                "backup": "research/reports/backups/x.bak.json"}), "stderr": ""}

    gate_calls = {"n": 0}

    def gate(stage="publish"):
        gate_calls["n"] += 1
        return {"ok": True, "fail": 0, "summary": {"fail": 0}}

    rec = lr.release(project="p", proposals=proposals, apply=True, out_dir=tmp_path,
                     gate_fn=gate, run_fn=run,
                     digest_fn=lambda: {"rc": 0, "stdout": "digest ok", "stderr": ""})
    assert rec["ok"] is True and rec["apply"] is True and rec["mode"] == "apply"
    names = [s["name"] for s in rec["steps"]]
    assert names == ["gate_publish_precheck", "validate_approved", "card_merge_apply",
                     "learning_digest", "gate_publish_post_strict"]
    assert len(cmds) == 1
    assert gate_calls["n"] == 2, "apply 应执行前置+后置两次 gate"
    assert Path(rec["receipt"]["json"]).is_file()


# ---------------------------------------------------------------------------
# 5) 后置 gate strict 失败 → 停止 + 回滚提示
# ---------------------------------------------------------------------------
def test_post_gate_fail(tmp_path):
    proposals = tmp_path / "props.json"
    write_proposals(proposals, approved=1)
    state = {"n": 0}

    def gate(stage="publish"):
        state["n"] += 1
        if state["n"] == 1:
            return ok_gate()
        return {"ok": False, "fail": 1, "summary": {"fail": 1}}

    rec = lr.release(project="p", proposals=proposals, apply=True, out_dir=tmp_path,
                     gate_fn=gate,
                     run_fn=merge_runner({"would_add": 1, "resulting_cards": 173}),
                     digest_fn=lambda: {"rc": 0, "stdout": "", "stderr": ""})
    assert rec["ok"] is False and rec["stopped_at"] == "gate_publish_post_strict"
    assert rec["rollback"] and "备份" in rec["rollback"]


# ---------------------------------------------------------------------------
# 6) 回执 schema / 真实卡未动
# ---------------------------------------------------------------------------
def test_receipt_schema_and_no_real_merge(tmp_path):
    proposals = tmp_path / "props.json"
    write_proposals(proposals, approved=1)
    before = sha(CARDS)
    rec = lr.release(project="laomu", proposals=proposals, out_dir=tmp_path,
                     gate_fn=ok_gate, run_fn=merge_runner({"would_add": 0, "resulting_cards": 172}))
    saved = json.loads(Path(rec["receipt"]["json"]).read_text(encoding="utf-8"))
    for key in ("schema_version", "date", "project", "apply", "mode", "proposals",
                "steps", "merge_stats", "cards", "ok", "stopped_at", "receipt", "disclaimer"):
        assert key in saved, key
    assert saved["proposals"]["approved"] == 1
    assert saved["cards"]["before"]["count"] is not None
    assert sha(CARDS) == before, "测试不得真实合并/改动学习卡"
    md = Path(rec["receipt"]["md"]).read_text(encoding="utf-8")
    for marker in ("learning_release 回执", "步骤结果", "合并统计", "卡池"):
        assert marker in md, marker
