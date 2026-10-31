# -*- coding: utf-8 -*-
"""tools/approval_rollup.py 测试（2026-10-31 WO-M4-SIGNERMAP-20261031）。

只读工具契约：夹具全落 tmp（ledger/config），不碰真实 approvals.jsonl；
校验 signed/missing/complete 输出（单签 incomplete、四签 complete、空台账、
未配置不猜、坏配置 fail-safe、annotate 试点不计签）与 CLI --json 形态。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "tools", ROOT / "portal"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import approval_rollup as R  # noqa: E402

FULL_MAP = {"ou_qa1": "QA1", "ou_guanyu": "GUANYU",
            "ou_verify1": "VERIFY1", "ou_arch1": "ARCH1"}


def _rows(*ops, task="M4"):
    return [{"ts": "2026-10-31T09:%02d:00" % i, "task": task,
             "action": "approve", "operator": op}
            for i, op in enumerate(ops)]


def _write_ledger(tmp_path, rows):
    p = tmp_path / "approvals.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")
    return p


def _write_cfg(tmp_path, mapping, raw=None):
    p = tmp_path / "approval_signers.json"
    p.write_text(raw if raw is not None else json.dumps(
        {"map": mapping, "note": "测试夹具"}, ensure_ascii=False),
        encoding="utf-8")
    return p


# ----------------------------------------------------------------------
# rollup 汇总
# ----------------------------------------------------------------------
def test_single_sign_incomplete(tmp_path):
    ledger = _write_ledger(tmp_path, _rows("ou_qa1"))
    cfg = _write_cfg(tmp_path, FULL_MAP)
    out = R.rollup("M4", ledger=ledger, config=cfg)
    assert out["task"] == "M4"
    assert out["state"] == "incomplete" and out["complete"] is False
    assert out["signed"] == ["QA1"]
    assert out["missing"] == ["GUANYU", "VERIFY1", "ARCH1"]
    assert out["required"] == ["QA1", "GUANYU", "VERIFY1", "ARCH1"]
    assert out["signed_count"] == 1
    assert out["role_map_size"] == 4


def test_four_signs_complete(tmp_path):
    ledger = _write_ledger(tmp_path, _rows(
        "ou_qa1", "ou_guanyu", "ou_verify1", "ou_arch1"))
    cfg = _write_cfg(tmp_path, FULL_MAP)
    out = R.rollup("M4", ledger=ledger, config=cfg)
    assert out["complete"] is True and out["state"] == "complete"
    assert out["signed"] == ["QA1", "GUANYU", "VERIFY1", "ARCH1"]
    assert out["missing"] == []
    assert out["signed_count"] == 4


def test_empty_ledger_incomplete(tmp_path):
    ledger = _write_ledger(tmp_path, [])
    cfg = _write_cfg(tmp_path, FULL_MAP)
    out = R.rollup("M4", ledger=ledger, config=cfg)
    assert out["complete"] is False
    assert out["signed"] == []
    assert out["missing"] == ["QA1", "GUANYU", "VERIFY1", "ARCH1"]
    assert out["reason"].startswith("distinct_signers_lt_4")


def test_missing_ledger_file_incomplete_no_crash(tmp_path):
    cfg = _write_cfg(tmp_path, FULL_MAP)
    out = R.rollup("M4", ledger=tmp_path / "nope.jsonl", config=cfg)
    assert out["complete"] is False and out["signed"] == []


def test_unknown_open_ids_not_guessed(tmp_path):
    """未命中映射 → roles_unresolved；绝不把未知 open_id 猜成任何席位。"""
    ledger = _write_ledger(tmp_path, _rows(
        "ou_unknown_a", "ou_unknown_b", "ou_unknown_c", "ou_unknown_d"))
    cfg = _write_cfg(tmp_path, FULL_MAP)
    out = R.rollup("M4", ledger=ledger, config=cfg)
    assert out["complete"] is False
    assert out["signed"] == []
    assert out["signed_count"] == 4
    assert out["reason"] == "roles_unresolved"


def test_empty_config_incomplete(tmp_path):
    ledger = _write_ledger(tmp_path, _rows(
        "ou_qa1", "ou_guanyu", "ou_verify1", "ou_arch1"))
    cfg = _write_cfg(tmp_path, {})
    out = R.rollup("M4", ledger=ledger, config=cfg)
    assert out["complete"] is False
    assert out["role_map_size"] == 0
    assert out["reason"] == "roles_unresolved"


def test_bad_config_fail_safe(tmp_path):
    ledger = _write_ledger(tmp_path, _rows("ou_qa1"))
    cfg = _write_cfg(tmp_path, {}, raw="{ not json")
    out = R.rollup("M4", ledger=ledger, config=cfg)   # 不抛
    assert out["complete"] is False
    assert out["role_map_size"] == 0


def test_annotated_pilot_click_excluded(tmp_path):
    rows = _rows("ou_qa1", "ou_guanyu", "ou_verify1", "ou_arch1")
    rows.append({"ts": "2026-10-31T10:00:00", "task": "M4",
                 "action": "annotate", "operator": "DSH-Lead",
                 "refs": ["2026-10-31T09:00:00"]})
    ledger = _write_ledger(tmp_path, rows)
    cfg = _write_cfg(tmp_path, FULL_MAP)
    out = R.rollup("M4", ledger=ledger, config=cfg)
    assert out["complete"] is False
    assert "QA1" in out["missing"] and "QA1" not in out["signed"]


def test_rollup_is_read_only(tmp_path):
    ledger = _write_ledger(tmp_path, _rows("ou_qa1"))
    cfg = _write_cfg(tmp_path, FULL_MAP)
    before = (ledger.read_bytes(), cfg.read_bytes(),
              sorted(p.name for p in tmp_path.iterdir()))
    R.rollup("M4", ledger=ledger, config=cfg)
    after = (ledger.read_bytes(), cfg.read_bytes(),
             sorted(p.name for p in tmp_path.iterdir()))
    assert before == after            # 只读：不改台账/配置、不新增文件


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def test_cli_json(tmp_path, capsys):
    ledger = _write_ledger(tmp_path, _rows("ou_qa1"))
    cfg = _write_cfg(tmp_path, FULL_MAP)
    rc = R.main(["M4", "--ledger", str(ledger), "--config", str(cfg),
                 "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["complete"] is False
    assert data["state"] == "incomplete"
    assert data["signed"] == ["QA1"]
    assert data["missing"] == ["GUANYU", "VERIFY1", "ARCH1"]
    assert data["signed_signers"][0]["operator"] == "ou_qa1"


def test_cli_human_output(tmp_path, capsys):
    ledger = _write_ledger(tmp_path, _rows(
        "ou_qa1", "ou_guanyu", "ou_verify1", "ou_arch1"))
    cfg = _write_cfg(tmp_path, FULL_MAP)
    rc = R.main(["M4", "--ledger", str(ledger), "--config", str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "complete=True" in out
    assert "signed=QA1,GUANYU,VERIFY1,ARCH1" in out
    assert "missing=-" in out


def test_default_config_is_repo_signers_file():
    assert R.DEFAULT_CONFIG == ROOT / "agent_platform" / "config" / "approval_signers.json"
    assert R.DEFAULT_CONFIG.is_file()
    assert R.load_signers() == {}        # 仓内 pilot 配置为空 map（不伪造签字）


def test_bom_config_and_ledger_tolerated(tmp_path):
    """Windows/PowerShell 写出的 UTF-8 BOM：配置与台账首行都不得静默丢失。"""
    row = json.dumps({"ts": "2026-10-31T09:00:00", "task": "M4",
                      "action": "approve", "operator": "ou_qa1"},
                     ensure_ascii=False)
    ledger = tmp_path / "bom.jsonl"
    ledger.write_bytes(b"\xef\xbb\xbf" + (row + "\n").encode("utf-8"))
    cfg = tmp_path / "bom_signers.json"
    cfg.write_bytes(b"\xef\xbb\xbf" + json.dumps(
        {"map": FULL_MAP}, ensure_ascii=False).encode("utf-8"))
    out = R.rollup("M4", ledger=ledger, config=cfg)
    assert out["role_map_size"] == 4
    assert out["signed"] == ["QA1"]
    assert out["signed_count"] == 1


# ----------------------------------------------------------------------
# 2026-11-01 K-ORG-20261101-M4SIGNOFF：--require-complete 硬门（复用 AC.signoff_gate）
# ----------------------------------------------------------------------
def test_require_complete_cli_gate(tmp_path, capsys):
    # 四签齐 → exit 0
    ledger = _write_ledger(tmp_path, _rows(
        "ou_qa1", "ou_guanyu", "ou_verify1", "ou_arch1"))
    cfg = _write_cfg(tmp_path, FULL_MAP)
    rc = R.main(["M4", "--ledger", str(ledger), "--config", str(cfg),
                 "--require-complete"])
    out = capsys.readouterr()
    assert rc == 0
    assert "OK" in (out.out + out.err)

    # 少一签 → exit 1（默认模式仍 exit 0，只报告）
    ledger2 = _write_ledger(tmp_path, _rows(
        "ou_qa1", "ou_guanyu", "ou_verify1"))
    rc2 = R.main(["M4", "--ledger", str(ledger2), "--config", str(cfg),
                  "--require-complete"])
    err = capsys.readouterr()
    assert rc2 == 1
    assert "BLOCK" in err.err
    rc3 = R.main(["M4", "--ledger", str(ledger2), "--config", str(cfg)])
    capsys.readouterr()
    assert rc3 == 0, "默认报告模式不得误阻断"
