"""P0-2 日终收口轮测试（WO-LEGION-P0FIX-20261026）。

覆盖：真实账本 → Z2 draft 卡落盘、窗口不可用如实报错不造卡、
签字入库需 cue/lesson、渐进模式记录 unverified、不签字不入库。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

_PLATFORM = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for _p in (_PLATFORM, os.path.join(_PLATFORM, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import closeout_round as CR  # noqa: E402


SUMMARY = {
    "generated_at": "2026-10-26T00:08:48+08:00",
    "total_scanned": 46, "ok": 38, "failed": 0,
    "upstream_hold": 1, "disabled": 3, "never_run": 2,
}

WINDOW = {
    "window_hours": 24.0,
    "compared": {"prior_scan_at": "2026-10-25T00:00:00+08:00",
                 "latest_scan_at": "2026-10-26T00:00:00+08:00"},
    "new_failures": ["A"], "persisting_failures": ["B"], "fixed_in_window": ["C", "D"],
}


@pytest.fixture()
def stub_ledger(monkeypatch):
    monkeypatch.setattr(CR.ta, "summary_only", lambda: dict(SUMMARY))
    monkeypatch.setattr(CR.ta, "window_diff", lambda hours=24.0: dict(WINDOW))


def test_unsigned_only_saves_card(tmp_path, stub_ledger):
    exp_root = tmp_path / "experiments"
    lib_root = tmp_path / "experience"
    r = CR.run(experience_root=lib_root, experiments_root=exp_root)

    assert r["ok"] is True and r["decision_required"] is True
    assert r["experience"] is None
    card_file = exp_root / (r["card_id"] + ".json")
    assert card_file.is_file()
    card = json.loads(card_file.read_text(encoding="utf-8"))
    assert card["status"] == "probing"
    assert "窗口" in card["hypothesis"]
    # 指标准确反映真实账本（不造数）
    metrics = card["arms"]["baseline"]["metrics"]
    assert metrics["failed_snapshot_lower_better"] == 0.0
    assert metrics["fixed_in_window"] == 2.0
    # 不签字 → 经验库零写入
    assert not lib_root.exists()


def test_window_unavailable_no_card(tmp_path, monkeypatch):
    monkeypatch.setattr(CR.ta, "summary_only", lambda: dict(SUMMARY))
    monkeypatch.setattr(CR.ta, "window_diff",
                        lambda hours=24.0: {"error": "账本仅 1 条，不足两次扫描"})
    exp_root = tmp_path / "experiments"
    r = CR.run(experiments_root=exp_root)
    assert r["ok"] is False and r["stage"] == "window"
    assert not exp_root.exists(), "窗口不可用时不得生成半成品卡"


def test_summary_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(CR.ta, "summary_only",
                        lambda: {"error": "尚无摘要文件，请先 --scan"})
    monkeypatch.setattr(CR.ta, "window_diff", lambda hours=24.0: dict(WINDOW))
    r = CR.run(experiments_root=tmp_path / "experiments")
    assert r["ok"] is False and r["stage"] == "summary"


def test_signed_requires_cue_lesson(tmp_path, stub_ledger):
    r = CR.run(experiments_root=tmp_path / "experiments",
               experience_root=tmp_path / "lib",
               signed_by="老田")
    assert r["ok"] is False and r["stage"] == "signature_input"
    assert not (tmp_path / "lib").exists()


def test_signed_legacy_mode_writes_experience(tmp_path, stub_ledger, monkeypatch):
    monkeypatch.delenv("EVOLVE_SIGNATURE_STRICT", raising=False)
    monkeypatch.delenv("EVOLVE_PRINCIPAL_PUBKEY_HEX", raising=False)
    r = CR.run(experiments_root=tmp_path / "experiments",
               experience_root=tmp_path / "lib",
               signed_by="老田",
               cue="日终收口：窗口三分类 vs 快照", lesson="窗口口径与快照数字对齐后可用")
    assert r["ok"] is True and r["decision_required"] is False
    assert r["experience"]["verified"] is False  # 渐进模式如实记未验签
    assert (tmp_path / "lib" / "_index.jsonl").is_file()


def test_cli_json(tmp_path, stub_ledger, capsys):
    rc = CR.main(["--json",
                  "--experiments-root", str(tmp_path / "experiments")])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True and out["card_id"].startswith("EX-")
