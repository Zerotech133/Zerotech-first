"""P0-2 日终收口轮测试（WO-LEGION-P0FIX-20261026）。

覆盖：真实账本 → Z2 draft 卡落盘、窗口不可用如实报错不造卡、
签字入库需 cue/lesson、渐进模式记录 unverified、不签字不入库。
"""
from __future__ import annotations

import datetime as dt
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


def _fresh_summary(hours_old: float = 1.0) -> dict:
    ts = dt.datetime.now().astimezone() - dt.timedelta(hours=hours_old)
    s = dict(SUMMARY)
    s["generated_at"] = ts.isoformat(timespec="seconds")
    return s


@pytest.fixture()
def stub_ledger(monkeypatch):
    monkeypatch.setattr(CR.ta, "summary_only", lambda: _fresh_summary())
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


# ---------------- WO-LEGION-CLOCK-20261026：as-of / 去重 / 保留 / 元数据 ----------------

def test_card_metadata_fields(tmp_path, stub_ledger):
    r = CR.run(experiments_root=tmp_path / "experiments")
    card = json.loads((tmp_path / "experiments" / (r["card_id"] + ".json"))
                      .read_text(encoding="utf-8"))
    assert card["kind"] == "observation"
    assert card["decision_required"] is True
    assert card["created_at"]
    ws = card["window_summary"]
    assert ws["fixed_in_window"] == 2 and ws["failed_snapshot"] == 0
    assert r["summary_age_hours"] is not None


def test_stale_summary_refuses_card(tmp_path, monkeypatch):
    """摘要龄 >12h：拒出卡（不拿旧账本当 24h 窗口）。"""
    monkeypatch.setattr(CR.ta, "summary_only", lambda: _fresh_summary(13.0))
    monkeypatch.setattr(CR.ta, "window_diff", lambda hours=24.0: dict(WINDOW))
    root = tmp_path / "experiments"
    r = CR.run(experiments_root=root, max_age_hours=12.0)
    assert r["ok"] is False and r["stage"] == "stale"
    assert not root.exists()


def test_aged_summary_warns_but_writes(tmp_path, monkeypatch):
    """6-12h：告警但允许出卡（黄，不判红）。"""
    monkeypatch.setattr(CR.ta, "summary_only", lambda: _fresh_summary(8.0))
    monkeypatch.setattr(CR.ta, "window_diff", lambda hours=24.0: dict(WINDOW))
    r = CR.run(experiments_root=tmp_path / "experiments",
               warn_age_hours=6.0, max_age_hours=12.0)
    assert r["ok"] is True and r["stale_warning"]
    assert r["summary_age_hours"] >= 7.5


def test_if_changed_skips_duplicate(tmp_path, stub_ledger):
    root = tmp_path / "experiments"
    r1 = CR.run(experiments_root=root, if_changed=True)
    r2 = CR.run(experiments_root=root, if_changed=True)
    assert r1["ok"] is True and not r1["skipped"]
    assert r2["ok"] is True and r2["skipped"] is True
    assert len(list(root.glob("EX-*.json"))) == 1


def test_retention_moves_old_cards(tmp_path, monkeypatch):
    root = tmp_path / "experiments"
    for i in range(3):
        win = dict(WINDOW)
        win["fixed_in_window"] = ["C", "D"][: (i % 2) + 1]
        win["new_failures"] = ["A"] * (i + 1)
        monkeypatch.setattr(CR.ta, "summary_only", lambda: _fresh_summary())
        monkeypatch.setattr(CR.ta, "window_diff", lambda hours=24.0, w=win: dict(w))
        CR.run(experiments_root=root, keep=2, if_changed=False)
    assert len(list(root.glob("EX-*.json"))) == 2
    expired = list((root / "_expired").glob("EX-*.json"))
    assert len(expired) == 1


def test_prune_ignores_dot_state_file(tmp_path):
    """CLOCKFIX：.closeout_state.json 不是卡片，轮转不得把它移走。"""
    root = tmp_path / "experiments"
    root.mkdir()
    for i in range(3):
        (root / ("EX-%d.json" % i)).write_text(json.dumps({
            "card_id": "EX-%d" % i,
            "created_at": "2026-10-26T09:0%d:00+0800" % i,
        }), encoding="utf-8")
    (root / ".closeout_state.json").write_text('{"last_digest": "x"}', encoding="utf-8")
    moved = CR.prune_cards(root, keep=2)
    assert moved == 1
    assert (root / ".closeout_state.json").is_file()
    assert len(list(root.glob("EX-*.json"))) == 2
