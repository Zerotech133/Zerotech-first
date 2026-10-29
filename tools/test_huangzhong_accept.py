# -*- coding: utf-8 -*-
"""黄忠验收工具单测（fake git/证据，不联网）。

覆盖 D3/A 证据口径升级（2026-10-30）：
  · 证据对象含 generated_at/age_hours/age_source/来源/是否超龄；
  · 证据年龄门（默认 48h）→ 超龄 OBSERVE；
  · --baseline/--redteam 显式证据覆盖默认「全局最新」；
  · 红队 pass_rate<0.8 / 基线 failed>0/error>0 → REJECT；
  · 证据缺失/不可读 → OBSERVE；commit 不可读 → OBSERVE；
  · 旧默认行为（PASS 路径、report json/md、mode=observe/blocking=False/rollback_point）保持。
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

_TOOLS = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("huangzhong_accept",
                                                  _TOOLS / "huangzhong_accept.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git_ok(sha="a" * 40):
    def run(cmd, **kw):
        return SimpleNamespace(returncode=0,
                               stdout=sha + "\n田丰\n2026-10-28 07:00:00\nfeat: x\n",
                               stderr="")
    return run


def _iso_hours_ago(hours: float) -> str:
    ts = datetime.now(timezone.utc) - timedelta(hours=hours)
    return ts.isoformat(timespec="seconds")


def _write_baseline(path: Path, failed=0, error=0, generated_at="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generated_at": generated_at,
                                "summary": {"passed": 100, "failed": failed,
                                            "error": error}}), encoding="utf-8")
    return path


def _write_redteam(path: Path, rate=1.0, generated_at="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    total = 10
    path.write_text(json.dumps({"total": total, "passed": int(rate * total),
                                "pass_rate": rate,
                                "generated_at": generated_at}), encoding="utf-8")
    return path


def _evidence(tmp_path, failed=0, rate=1.0, generated_at="x", error=0):
    """默认路径证据：baseline 文件 + redteam 目录（含一份报告）。"""
    base = _write_baseline(tmp_path / "latest.json", failed=failed, error=error,
                           generated_at=generated_at)
    rt = tmp_path / "rt"
    rt.mkdir(parents=True, exist_ok=True)
    _write_redteam(rt / "redteam_1.json", rate=rate, generated_at=generated_at)
    return base, rt


def _accept_default(h, base, rt, out_dir, **kw):
    """以 monkeypatch 后的默认路径跑验收（fake git，不联网）。"""
    old_base, old_rt = h.BASELINE, h.REDTEAM_DIR
    h.BASELINE, h.REDTEAM_DIR = base, rt
    try:
        return h.accept("HEAD", out_dir=out_dir, runner=_git_ok(), **kw)
    finally:
        h.BASELINE, h.REDTEAM_DIR = old_base, old_rt


# ---------------------------------------------------------------- 旧用例保持

def test_accept_pass(monkeypatch, tmp_path):
    h = _load()
    base, rt = _evidence(tmp_path, failed=0, rate=1.0)
    monkeypatch.setattr(h, "BASELINE", base)
    monkeypatch.setattr(h, "REDTEAM_DIR", rt)
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok())
    assert rep["verdict"] == "PASS" and rep["blocking"] is False
    assert rep["rollback_point"].startswith("git revert")
    assert Path(rep["report_json"]).exists() and Path(rep["report_md"]).exists()


def test_accept_reject_on_baseline_failed(monkeypatch, tmp_path):
    h = _load()
    base, rt = _evidence(tmp_path, failed=3, rate=1.0)
    monkeypatch.setattr(h, "BASELINE", base)
    monkeypatch.setattr(h, "REDTEAM_DIR", rt)
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok())
    assert rep["verdict"] == "REJECT"
    assert any("基线失败" in r for r in rep["reasons"])


def test_accept_reject_on_redteam_low(monkeypatch, tmp_path):
    h = _load()
    base, rt = _evidence(tmp_path, failed=0, rate=0.5)
    monkeypatch.setattr(h, "BASELINE", base)
    monkeypatch.setattr(h, "REDTEAM_DIR", rt)
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok())
    assert rep["verdict"] == "REJECT"
    assert any("红队" in r for r in rep["reasons"])


def test_accept_observe_when_missing(monkeypatch, tmp_path):
    h = _load()
    monkeypatch.setattr(h, "BASELINE", tmp_path / "nope.json")
    monkeypatch.setattr(h, "REDTEAM_DIR", tmp_path / "nope_rt")
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok())
    assert rep["verdict"] == "OBSERVE"
    assert rep["blocking"] is False


def test_git_failure_fail_visible(monkeypatch, tmp_path):
    h = _load()
    monkeypatch.setattr(h, "BASELINE", tmp_path / "nope.json")
    monkeypatch.setattr(h, "REDTEAM_DIR", tmp_path / "nope_rt")

    def bad(cmd, **kw):
        return SimpleNamespace(returncode=1, stdout="", stderr="bad rev")
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=bad)
    assert rep["commit"]["ok"] is False
    assert rep["verdict"] == "OBSERVE"


def test_verdict_rules_pure():
    h = _load()
    assert h._verdict({"ok": True, "failed": 0, "error": 0},
                      {"ok": True, "pass_rate": 0.9})[0] == "PASS"
    assert h._verdict({"ok": True, "failed": 1, "error": 0},
                      {"ok": True, "pass_rate": 1.0})[0] == "REJECT"
    assert h._verdict({"ok": False}, {"ok": True, "pass_rate": 1.0})[0] == "OBSERVE"


# ------------------------------------------------------- D3/A 证据口径新用例

def test_evidence_objects_have_age_source(monkeypatch, tmp_path):
    """证据对象补 generated_at/age_hours/age_source/来源/是否超龄；JSON+MD 同步。"""
    h = _load()
    base, rt = _evidence(tmp_path, generated_at=_iso_hours_ago(1))
    monkeypatch.setattr(h, "BASELINE", base)
    monkeypatch.setattr(h, "REDTEAM_DIR", rt)
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok())
    assert rep["verdict"] == "PASS"
    assert rep["mode"] == "observe" and rep["blocking"] is False
    assert rep["max_evidence_age_hours"] == h.MAX_EVIDENCE_AGE_HOURS == 48.0
    assert rep["evidence_scope"] == "default_latest"
    assert rep["evidence_scope_detail"] == {"baseline": "default", "redteam": "default"}
    for kind in ("baseline", "redteam"):
        ev = rep["evidence"][kind]
        for key in ("ok", "path", "source", "generated_at", "age_hours",
                    "age_source", "stale", "max_age_hours"):
            assert key in ev, (kind, key, ev)
        assert ev["source"] == "default"
        assert ev["age_source"] == "generated_at"
        assert ev["stale"] is False
        assert 0.9 <= ev["age_hours"] <= 2.0
    assert rep["evidence_age"]["baseline"] is not None
    assert rep["evidence_age"]["unit"] == "hours"
    assert rep["evidence_age"]["stale"] == {"baseline": False, "redteam": False}
    md = Path(rep["report_md"]).read_text(encoding="utf-8")
    for key in ("证据口径", "年龄", "来源", "新鲜"):
        assert key in md
    data = json.loads(Path(rep["report_json"]).read_text(encoding="utf-8"))
    assert data["evidence_scope"] == "default_latest"
    assert data["max_evidence_age_hours"] == 48.0
    assert "evidence_age" in data and "rollback_point" in data


def test_accept_observe_when_stale(monkeypatch, tmp_path):
    """证据超龄（60h > 默认 48h）→ OBSERVE，reason 写明超龄与上限。"""
    h = _load()
    base, rt = _evidence(tmp_path, generated_at=_iso_hours_ago(60))
    monkeypatch.setattr(h, "BASELINE", base)
    monkeypatch.setattr(h, "REDTEAM_DIR", rt)
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok())
    assert rep["verdict"] == "OBSERVE"
    assert rep["evidence"]["baseline"]["stale"] is True
    assert rep["evidence"]["redteam"]["stale"] is True
    stale = [r for r in rep["reasons"] if "超龄" in r]
    assert len(stale) == 2
    assert all("48.0 h" in r for r in stale)
    md = Path(rep["report_md"]).read_text(encoding="utf-8")
    assert "超龄" in md


def test_stale_overridden_by_explicit_max_age(monkeypatch, tmp_path):
    """--max-evidence-age-hours 显式放宽 → 同批旧证据可判 PASS（覆盖生效）。"""
    h = _load()
    base, rt = _evidence(tmp_path, generated_at=_iso_hours_ago(60))
    monkeypatch.setattr(h, "BASELINE", base)
    monkeypatch.setattr(h, "REDTEAM_DIR", rt)
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok(),
                   max_age_hours=1000)
    assert rep["verdict"] == "PASS"
    assert rep["max_evidence_age_hours"] == 1000.0
    assert rep["evidence"]["baseline"]["stale"] is False


def test_explicit_evidence_override(monkeypatch, tmp_path):
    """--baseline/--redteam 显式指定 → 覆盖默认缺失路径，evidence_scope=explicit。"""
    h = _load()
    monkeypatch.setattr(h, "BASELINE", tmp_path / "nope" / "latest.json")
    monkeypatch.setattr(h, "REDTEAM_DIR", tmp_path / "no-rt")
    good_base = _write_baseline(tmp_path / "explicit" / "base.json",
                                generated_at=_iso_hours_ago(1))
    good_rt = _write_redteam(tmp_path / "explicit" / "redteam_20261030.json",
                             rate=1.0, generated_at=_iso_hours_ago(1))
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok(),
                   baseline=good_base, redteam=good_rt)
    assert rep["verdict"] == "PASS"
    assert rep["evidence_scope"] == "explicit"
    assert rep["evidence_scope_detail"] == {"baseline": "explicit",
                                            "redteam": "explicit"}
    assert rep["evidence"]["baseline"]["source"] == "explicit"
    assert rep["evidence"]["baseline"]["path"] == str(good_base)
    assert rep["evidence"]["redteam"]["source"] == "explicit"
    assert rep["evidence"]["redteam"]["path"] == str(good_rt)


def test_explicit_redteam_dir_override(monkeypatch, tmp_path):
    """--redteam 也可给目录（取目录内最新 redteam_*.json）。"""
    h = _load()
    base, _ = _evidence(tmp_path, generated_at=_iso_hours_ago(1))
    rt_dir = tmp_path / "explicit_rt"
    _write_redteam(rt_dir / "redteam_1.json", rate=1.0,
                   generated_at=_iso_hours_ago(1))
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok(),
                   baseline=base, redteam=rt_dir)
    assert rep["verdict"] == "PASS"
    assert rep["evidence"]["redteam"]["path"] == str(rt_dir / "redteam_1.json")


def test_mixed_evidence_scope(monkeypatch, tmp_path):
    """只显式给基线 → evidence_scope=mixed。"""
    h = _load()
    base, rt = _evidence(tmp_path, generated_at=_iso_hours_ago(1))
    monkeypatch.setattr(h, "REDTEAM_DIR", rt)
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok(),
                   baseline=base)
    assert rep["verdict"] == "PASS"
    assert rep["evidence_scope"] == "mixed"
    assert rep["evidence_scope_detail"] == {"baseline": "explicit",
                                            "redteam": "default"}


def test_reject_on_redteam_below_threshold_fresh(monkeypatch, tmp_path):
    h = _load()
    base, rt = _evidence(tmp_path, rate=0.7, generated_at=_iso_hours_ago(1))
    monkeypatch.setattr(h, "BASELINE", base)
    monkeypatch.setattr(h, "REDTEAM_DIR", rt)
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok())
    assert rep["verdict"] == "REJECT"
    assert any("红队通过率 70%" in r for r in rep["reasons"])


def test_reject_on_baseline_failed_fresh(monkeypatch, tmp_path):
    h = _load()
    base, rt = _evidence(tmp_path, failed=2, generated_at=_iso_hours_ago(1))
    monkeypatch.setattr(h, "BASELINE", base)
    monkeypatch.setattr(h, "REDTEAM_DIR", rt)
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok())
    assert rep["verdict"] == "REJECT"
    assert any("基线失败" in r and "failed=2" in r for r in rep["reasons"])


def test_reject_on_baseline_error_fresh(monkeypatch, tmp_path):
    h = _load()
    base, rt = _evidence(tmp_path, error=1, generated_at=_iso_hours_ago(1))
    monkeypatch.setattr(h, "BASELINE", base)
    monkeypatch.setattr(h, "REDTEAM_DIR", rt)
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok())
    assert rep["verdict"] == "REJECT"
    assert any("error=1" in r for r in rep["reasons"])


def test_reject_precedence_over_stale(monkeypatch, tmp_path):
    """可读证据显示硬失败时 REJECT 优先于「超龄→OBSERVE」（失败不因过期失效）。"""
    h = _load()
    base, rt = _evidence(tmp_path, failed=2, generated_at=_iso_hours_ago(60))
    monkeypatch.setattr(h, "BASELINE", base)
    monkeypatch.setattr(h, "REDTEAM_DIR", rt)
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok())
    assert rep["verdict"] == "REJECT"
    assert any("基线失败" in r for r in rep["reasons"])
    assert any("超龄" in r for r in rep["reasons"])


def test_accept_observe_when_evidence_unreadable(monkeypatch, tmp_path):
    """证据文件坏 JSON / 缺 summary → OBSERVE（不可读）。"""
    h = _load()
    bad_base = tmp_path / "bad.json"
    bad_base.write_text("{not json", encoding="utf-8")
    rt = tmp_path / "rt"
    rt.mkdir()
    (rt / "redteam_999.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(h, "BASELINE", bad_base)
    monkeypatch.setattr(h, "REDTEAM_DIR", rt)
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok())
    assert rep["verdict"] == "OBSERVE"
    assert rep["evidence"]["baseline"]["error"] == "baseline_unreadable"
    assert rep["evidence"]["redteam"]["error"] == "redteam_unreadable"
    assert any("不可读" in r for r in rep["reasons"])


def test_default_max_age_logic(monkeypatch, tmp_path):
    """默认上限=48h：30h 新鲜 → PASS；60h 超龄 → OBSERVE；显式 24h 收紧 → OBSERVE。"""
    h = _load()
    assert h.MAX_EVIDENCE_AGE_HOURS == 48.0
    base, rt = _evidence(tmp_path / "a", generated_at=_iso_hours_ago(30))
    rep = _accept_default(h, base, rt, tmp_path / "out_a")
    assert rep["verdict"] == "PASS"
    assert rep["evidence"]["baseline"]["stale"] is False

    base2, rt2 = _evidence(tmp_path / "b", generated_at=_iso_hours_ago(60))
    rep2 = _accept_default(h, base2, rt2, tmp_path / "out_b")
    assert rep2["verdict"] == "OBSERVE"
    assert rep2["evidence"]["baseline"]["stale"] is True

    rep3 = _accept_default(h, base, rt, tmp_path / "out_c", max_age_hours=24)
    assert rep3["verdict"] == "OBSERVE"
    assert rep3["max_evidence_age_hours"] == 24.0
    assert any("24.0 h" in r for r in rep3["reasons"])


def test_freshness_parse_and_mtime_fallback(tmp_path):
    """generated_at 解析：ISO 带偏移/epoch；不可解析回退 mtime（age_source 标记）。"""
    h = _load()
    now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    f = h._freshness(Path("x"), "2026-10-30T00:00:00+00:00", 48, now=now)
    assert f["age_hours"] == 12.0 and f["stale"] is False
    f_old = h._freshness(Path("x"), "2026-10-25T00:00:00+00:00", 48, now=now)
    assert f_old["stale"] is True
    p = tmp_path / "e.json"
    p.write_text("{}", encoding="utf-8")
    f_mtime = h._freshness(p, "not-a-date", 48)
    assert f_mtime["age_source"] == "mtime" and f_mtime["stale"] is False
    assert f_mtime["age_hours"] < 1.0


def test_verdict_pure_stale_and_age_override():
    h = _load()
    stale_base = {"ok": True, "failed": 0, "error": 0, "age_hours": 60.0,
                  "stale": True, "generated_at": "x"}
    fresh_rt = {"ok": True, "pass_rate": 1.0, "age_hours": 1.0,
                "stale": False, "generated_at": "y"}
    assert h._verdict(stale_base, fresh_rt)[0] == "OBSERVE"
    assert h._verdict(stale_base, fresh_rt, max_age_hours=100)[0] == "PASS"
    assert any("超龄" in r for r in h._verdict(stale_base, fresh_rt)[1])


def test_cli_wiring_and_defaults(monkeypatch, tmp_path, capsys):
    """CLI：--baseline/--redteam/--max-evidence-age-hours/--out-dir 透传；默认 48h。"""
    h = _load()
    calls = {}
    fake_rep = {"commit": {"ok": True, "sha": "a" * 40},
                "verdict": "PASS", "report_md": str(tmp_path / "x.md"),
                "mode": "observe", "blocking": False,
                "reasons": [], "evidence": {}}

    def fake_accept(commit="HEAD", out_dir=None, runner=None, baseline=None,
                    redteam=None, max_age_hours=None, now=None):
        calls.update(commit=commit, out_dir=out_dir, baseline=baseline,
                     redteam=redteam, max_age_hours=max_age_hours)
        return fake_rep

    monkeypatch.setattr(h, "accept", fake_accept)
    rc = h.main(["--commit", "abc123", "--baseline", "b.json", "--redteam", "rt.json",
                 "--max-evidence-age-hours", "24", "--out-dir", str(tmp_path / "out"),
                 "--json"])
    assert rc == 0
    assert calls["commit"] == "abc123"
    assert calls["baseline"] == "b.json" and calls["redteam"] == "rt.json"
    assert calls["max_age_hours"] == 24.0
    assert calls["out_dir"] == tmp_path / "out"
    assert '"verdict": "PASS"' in capsys.readouterr().out

    # 默认行为向后兼容：不带新参数 → 默认 48h、证据/输出走缺省
    calls.clear()
    assert h.main(["--commit", "HEAD"]) == 0
    assert calls["max_age_hours"] == 48.0
    assert calls["baseline"] is None and calls["redteam"] is None
    assert calls["out_dir"] is None

    # --redteam-dir 是 --redteam 的兼容别名
    calls.clear()
    h.main(["--redteam-dir", "dir/*.json"])
    assert calls["redteam"] == "dir/*.json"

    # 非法年龄上限 → argparse 拒收（exit 2）
    with pytest.raises(SystemExit):
        h.main(["--max-evidence-age-hours", "-1"])


def test_cli_reject_exit_code(monkeypatch, tmp_path):
    """非 PASS 时 CLI 退出码=1（旧行为保持）。"""
    h = _load()

    def fake_accept(commit="HEAD", out_dir=None, runner=None, baseline=None,
                    redteam=None, max_age_hours=None, now=None):
        return {"commit": {"ok": True, "sha": "b" * 40}, "verdict": "OBSERVE",
                "report_md": "x.md", "mode": "observe", "blocking": False,
                "reasons": ["证据缺失"], "evidence": {}}

    monkeypatch.setattr(h, "accept", fake_accept)
    assert h.main(["--commit", "HEAD"]) == 1
