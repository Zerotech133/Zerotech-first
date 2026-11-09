# -*- coding: utf-8 -*-
"""huangzhong_drill.py 单测（tmp 夹具，不写真实 work/、不碰生产证据/历史）。

覆盖：
  · 默认 dry-run：零写入，但输出将写演练单/台账路径与台账条目预览；
  · --apply：生成 drill 验收单（drill=true/mode=observe/blocking=false/evidence 受控构造/
    rollback_point）+ 追加 drill 台账条目（file/verdict/commit/reason/method/witness/created_at）；
  · 幂等：重复 apply 不重复写、不重复台账（同 file → skip）；
  · 口令错误 / --dry-run 与 --apply 冲突 / 前置不满足 → 拒绝且零写入；
  · 前置口径：真实证据将判 REJECT/OBSERVE 才允许对应 kind（与 accept 同口径）；
  · 不触碰正式 acceptance/daily/events/incidents（字节级不变）；
  · 坏 JSON 台账容错（apply 拒绝零写入；dry-run 告警）；坏/缺失证据容错；
  · 台账追加保留既有条目；CLI --json 干跑 rc=0 且零写入。
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


_TOOLS = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 31, 13, 30, tzinfo=TZ)
SHA = "a" * 40
DATE = "20261031"


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_drill", _TOOLS / "huangzhong_drill.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


def _write(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _setup(tmp_path: Path, baseline_age: float = 1.0, baseline_failed: int = 0,
           redteam_age: float = 1.0, redteam_rate: float = 1.0,
           write_baseline: bool = True, write_redteam: bool = True) -> Path:
    root = tmp_path
    if write_baseline:
        _write(root / "agent_platform" / "data" / "test_baseline" / "latest.json", {
            "generated_at": _iso(NOW - timedelta(hours=baseline_age)),
            "summary": {"passed": 100, "failed": baseline_failed, "error": 0}})
    if write_redteam:
        _write(root / "work" / "p1" / "redteam" / "redteam_20261031_120000.json", {
            "generated_at": _iso(NOW - timedelta(hours=redteam_age)),
            "total": 10, "passed": int(round(redteam_rate * 10)),
            "pass_rate": redteam_rate})
    return root


def _work(root: Path) -> Path:
    return root / "work" / "huangzhong"


def _call(h, root: Path, kind: str, confirm=None, **kw):
    kw.setdefault("commit", SHA)
    if confirm is None:
        confirm = h.CONFIRM[kind]
    return h.run(kind=kind, confirm=confirm, repo_root=root, now=NOW, **kw)


def _sheet(root: Path, kind: str = "observe") -> dict:
    p = _work(root) / ("acceptance_drill_%s_%s.json" % (kind, DATE))
    return json.loads(p.read_text(encoding="utf-8"))


def _ledger(root: Path) -> dict:
    p = _work(root) / "drills" / ("drill_ledger_%s.json" % DATE)
    return json.loads(p.read_text(encoding="utf-8"))


# ------------------------------------------------------------ dry-run / apply

def test_default_is_dry_run_zero_write(tmp_path):
    root = _setup(tmp_path, redteam_age=60)          # 超龄 → 真实证据将判 OBSERVE
    h = _load()
    rep = _call(h, root, "observe")
    assert rep["ok"] is True
    assert rep["dry_run"] is True and rep["request"]["dry_run"] is True
    assert rep["precondition"]["expected_verdict"] == "OBSERVE"
    assert rep["precondition"]["satisfied"] is True
    plan = rep["plan"]
    assert plan["acceptance_file"].endswith(
        "acceptance_drill_observe_%s.json" % DATE)
    assert plan["ledger_file"].endswith("drill_ledger_%s.json" % DATE)
    assert "drills" in plan["ledger_file"]
    entry = plan["ledger_entry"]
    for key in ("file", "verdict", "commit", "method", "witness", "created_at"):
        assert entry.get(key), key
    assert entry["verdict"] == "OBSERVE" and entry["witness"] == "待老田/赵云签字"
    assert rep["plan"]["apply_would_refuse"] is False
    assert rep["written"]["acceptance_file"] is None
    assert not (_work(root) / (
        "acceptance_drill_observe_%s.json" % DATE)).exists()
    assert not (_work(root) / "drills").exists()


def test_apply_observe_creates_sheet_and_ledger(tmp_path):
    root = _setup(tmp_path, redteam_age=60)
    h = _load()
    rep = _call(h, root, "observe", apply=True)
    assert rep["ok"] is True and rep["dry_run"] is False
    sheet_path = _work(root) / ("acceptance_drill_observe_%s.json" % DATE)
    ledger_path = _work(root) / "drills" / ("drill_ledger_%s.json" % DATE)
    assert sheet_path.exists() and ledger_path.exists()
    assert rep["written"]["acceptance_file"] == str(sheet_path)
    sheet = _sheet(root)
    assert sheet["drill"] is True
    assert sheet["mode"] == "observe" and sheet["blocking"] is False
    assert sheet["kind"] == "OBSERVE" and sheet["verdict"] == "OBSERVE"
    assert sheet["commit"]["sha"] == SHA
    assert sheet["rollback_point"]
    construction = sheet["evidence"]["construction"]
    assert construction["expected_verdict"] == "OBSERVE"
    assert "手段2" in construction["method"]
    assert any("超龄" in r for r in sheet["reasons"])
    assert sheet["reproduce_command"].startswith("python -X utf8 tools\\huangzhong_accept.py")
    ledger = _ledger(root)
    assert ledger["schema"] == "huangzhong_drill_ledger/v1"
    assert len(ledger["drills"]) == 1
    entry = ledger["drills"][0]
    assert entry["drill"] is True and entry["verdict"] == "OBSERVE"
    assert entry["commit"] == SHA
    assert entry["file"].startswith("work/huangzhong/acceptance_drill_observe_")
    assert entry["acceptance_report"] == entry["file"]
    assert entry["witness"] == "待老田/赵云签字"
    assert entry["reason"] and entry["method"] and entry["created_at"]
    assert entry["dry_run"] is False


def test_apply_reject_with_failing_redteam(tmp_path):
    root = _setup(tmp_path, redteam_age=1.0, redteam_rate=0.5)
    h = _load()
    rep = _call(h, root, "reject", apply=True)
    assert rep["ok"] is True
    assert rep["precondition"]["expected_verdict"] == "REJECT"
    sheet = _sheet(root, "reject")
    assert sheet["verdict"] == "REJECT" and sheet["kind"] == "REJECT"
    assert any("红队通过率" in r for r in sheet["reasons"])
    assert "手段1" in sheet["evidence"]["construction"]["method"]
    assert _ledger(root)["drills"][0]["verdict"] == "REJECT"


def test_idempotent_repeat_apply(tmp_path):
    root = _setup(tmp_path, redteam_age=60)
    h = _load()
    rep1 = _call(h, root, "observe", apply=True)
    assert rep1["ok"] is True and rep1["skipped"] is False
    rep2 = _call(h, root, "observe", apply=True)
    assert rep2["ok"] is True and rep2["skipped"] is True
    assert rep2["written"]["acceptance_file"] is None
    assert len(_ledger(root)["drills"]) == 1                # 台账未重复
    assert len(list((_work(root) / "drills").glob("drill_ledger_*.json"))) == 1


def test_ledger_append_preserves_existing_entries(tmp_path):
    root = _setup(tmp_path, redteam_age=60)
    old = {"schema": "huangzhong_drill_ledger/v1", "generated_at": "2026-10-30T02:03:25+08:00",
           "note": "旧台账", "drills": [
               {"drill_id": "DRILL-20261030-REJECT-01", "file": "acceptance_76081eea.json",
                "verdict": "REJECT"}]}
    _write(_work(root) / "drills" / ("drill_ledger_%s.json" % DATE), old)
    h = _load()
    rep = _call(h, root, "observe", apply=True)
    assert rep["ok"] is True
    ledger = _ledger(root)
    assert len(ledger["drills"]) == 2
    assert ledger["drills"][0]["drill_id"] == "DRILL-20261030-REJECT-01"
    assert ledger["note"] == "旧台账"
    assert ledger["updated_at"]


# --------------------------------------------------------------- 拒绝零写入

def test_wrong_confirm_refused_zero_write(tmp_path):
    root = _setup(tmp_path, redteam_age=1.0, redteam_rate=0.5)
    h = _load()
    rep = _call(h, root, "reject", confirm="DRILL-OBSERVE", apply=True)
    assert rep["ok"] is False
    assert "确认字符串" in rep["refused"]["reason"]
    assert not (_work(root) / "drills").exists()
    assert not list(_work(root).glob("acceptance_drill_*"))


def test_conflicting_flags_refused_zero_write(tmp_path):
    root = _setup(tmp_path, redteam_age=60)
    h = _load()
    rep = _call(h, root, "observe", apply=True, dry_run_flag=True)
    assert rep["ok"] is False
    assert "同时" in rep["refused"]["reason"]
    assert not (_work(root) / "drills").exists()


def test_precondition_mismatch_apply_refused(tmp_path):
    root = _setup(tmp_path, baseline_age=1.0, redteam_age=1.0)  # 证据新鲜 → 将判 PASS
    h = _load()
    rep = _call(h, root, "observe", apply=True)
    assert rep["ok"] is False
    assert "受控构造成立性不足" in rep["refused"]["reason"]
    assert not (_work(root) / "drills").exists()
    assert not list(_work(root).glob("acceptance_drill_*"))
    # 同一场景 dry-run：ok=True 但 precondition 不成立 + warning + 明示 apply 将拒绝
    rep2 = _call(h, root, "observe")
    assert rep2["ok"] is True
    assert rep2["precondition"]["satisfied"] is False
    assert rep2["plan"]["apply_would_refuse"] is True
    assert any("受控构造成立性不足" in w for w in rep2["warnings"])


def test_bad_ledger_refused_on_apply_dry_run_warns(tmp_path):
    root = _setup(tmp_path, redteam_age=60)
    drills = _work(root) / "drills"
    drills.mkdir(parents=True)
    (drills / ("drill_ledger_%s.json" % DATE)).write_text("{oops", encoding="utf-8")
    h = _load()
    rep = _call(h, root, "observe", apply=True)
    assert rep["ok"] is False
    assert "台账不可读" in rep["refused"]["reason"]
    assert not (_work(root) / ("acceptance_drill_observe_%s.json" % DATE)).exists()
    rep2 = _call(h, root, "observe")
    assert rep2["ok"] is True
    assert any("台账不可读" in w for w in rep2["warnings"])


# ------------------------------------------------------- 不污染生产历史 / 容错

def test_does_not_touch_formal_history(tmp_path):
    root = _setup(tmp_path, redteam_age=60)
    work = _work(root)
    work.mkdir(parents=True, exist_ok=True)
    _write(work / "acceptance_bbbbbbbb.json",
           {"ok": True, "verdict": "PASS", "commit": {"sha": "b" * 40}})
    (work / "daily.jsonl").write_text(
        '{"ts": "2026-10-31T11:45:00+08:00", "verdict": "PASS"}\n',
        encoding="utf-8")
    (work / "events.jsonl").write_text(
        '{"kind": "acceptance_done"}\n', encoding="utf-8")
    (work / "incidents.jsonl").write_text(
        '{"ts": "2026-10-31T00:00:00+08:00", "commit": "x"}\n', encoding="utf-8")
    protected = ["acceptance_bbbbbbbb.json", "daily.jsonl", "events.jsonl",
                 "incidents.jsonl"]
    before = {name: (work / name).read_bytes() for name in protected}
    h = _load()
    rep = _call(h, root, "observe", apply=True)
    assert rep["ok"] is True
    for name in protected:
        assert (work / name).read_bytes() == before[name], name
    names = sorted(p.name for p in work.glob("acceptance_*"))
    assert "acceptance_bbbbbbbb.json" in names
    assert ("acceptance_drill_observe_%s.json" % DATE) in names


def test_missing_or_unreadable_evidence_tolerated(tmp_path):
    root = _setup(tmp_path, write_baseline=False, write_redteam=False)
    h = _load()
    rep = _call(h, root, "observe", apply=True)
    assert rep["ok"] is True
    assert rep["precondition"]["expected_verdict"] == "OBSERVE"
    sheet = _sheet(root)
    assert any("缺失/不可读" in r for r in sheet["reasons"])
    # 红队坏 JSON 同样按「不可读 → OBSERVE」处理
    root2 = _setup(tmp_path / "bad_json", redteam_age=1.0)
    (root2 / "work" / "p1" / "redteam" / "redteam_20261031_120000.json").write_text(
        "{bad", encoding="utf-8")
    h2 = _load()
    rep2 = _call(h2, root2, "observe", apply=True)
    assert rep2["ok"] is True
    assert rep2["precondition"]["expected_verdict"] == "OBSERVE"


# ------------------------------------------------------------------- CLI

def test_cli_json_dry_run_zero_write(tmp_path, capsys, monkeypatch):
    root = _setup(tmp_path, redteam_age=60)
    h = _load()
    # 2026-11-01 K-ORG-20261031-MIDNIGHTTESTS：CLI 默认用真实时钟，
    # 跨日会生成 20261101 演练单而用例固定 DATE=20261031 → 冻结时钟。
    monkeypatch.setattr(h, "_now", lambda: NOW)
    rc = h.main(["--kind", "observe", "--confirm", "DRILL-OBSERVE",
                 "--dry-run", "--json", "--repo-root", str(root),
                 "--commit", SHA])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert rc == 0
    assert data["dry_run"] is True and data["ok"] is True
    # 2026-11-01 P143：日期翻页修复——产物文件名用真实当天日期（非测试 NOW），
    # 断言改为「前缀+8位数字日期+.json」形状，避免跨日/跨月变红。
    _af = Path(data["plan"]["acceptance_file"]).name
    assert _af.startswith("acceptance_drill_observe_") and _af.endswith(".json")
    assert len(_af) == len("acceptance_drill_observe_") + 8 + len(".json")
    assert _af[len("acceptance_drill_observe_"):-len(".json")].isdigit()
    assert not (_work(root) / "drills").exists()


def test_cli_missing_kind_rejected(tmp_path, capsys):
    h = _load()
    rc = h.main(["--confirm", "DRILL-OBSERVE", "--json"])
    capsys.readouterr()
    assert rc == 1
