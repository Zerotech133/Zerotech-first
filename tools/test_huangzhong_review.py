# -*- coding: utf-8 -*-
"""黄忠认证 v2 六条复核工具单测（tmp 夹具，不依赖真实 work/）。

覆盖：
  · 全达标（六条全 ✓）→ overall_pass=True，JSON/MD 报告落盘；
  · 缺 PASS 样本 → ① ✗；
  · drill 台账命中（dict / 顶层数组 / JSONL 三种写法、verdict 缺失回填）→ ② ✓；
    drill 台账缺失 → ② pending；
  · 证据超龄 / 缺字段 → ⑤ ✗；
  · ⑤ legacy v0 豁免（v1.1）：缺证据年龄字段的旧 PASS 单不计入分母 →
    exempt 豁免后⑤达标；--legacy-v0-policy count → 旧行为判 ✗；无非 legacy → pending；
  · 赵云抽查缺失 → ⑥ pending；抽查不一致 → ⑥ ✗；
  · 误拒绝率：自然 REJECT<20 → 逐单人工复核（清单覆盖全部 REJECT 且 0 推翻 → ✓；
    清单缺项 → pending；有推翻 → ✗；无 REJECT 样本 → pending）；
    自然 REJECT≥20 且推翻率≥5% → ✗；复核清单缺失 → pending；
  · 漏放：满 7 天窗口内事故 → ④ ✗；未满窗 → ④ tracking；
  · commit sha 去重（同 commit 取最新）+ 演练单排除；
  · 坏行/坏 JSON 容错（验收单/daily/incidents/台账/抽查 + JSONL 退路）；
  · CLI 冒烟：退出码与 review_<ts>.json/md 落盘；--legacy-v0-policy 透传。
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 6, 10, 0, tzinfo=TZ)
GEN_OLD = NOW - timedelta(days=8)   # 已满 7 天窗口


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_review", _TOOLS / "huangzhong_review.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


def _sha(n: int) -> str:
    """构造 40 位十六进制 sha（前 8 位唯一，便于按 sha8 命名验收单）。"""
    return ("%08x" % n) + "0" * 32


def _work(tmp_path: Path) -> Path:
    work = tmp_path / "work" / "huangzhong"
    (work / "drills").mkdir(parents=True, exist_ok=True)
    return work


def _write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def _sheet_obj(sha: str, verdict: str = "PASS", gen: datetime | None = None, *,
               mode: str = "observe", blocking=False, rollback=None,
               baseline_age: float = 1.5, redteam_age: float = 2.0,
               baseline_stale: bool = False, redteam_stale: bool = False,
               evidence_scope: str = "default_latest") -> dict:
    gen = gen or GEN_OLD

    def _ev(kind: str, age: float, stale: bool) -> dict:
        return {"ok": True, "kind": kind, "path": "x", "source": "default",
                "generated_at": _iso(gen - timedelta(hours=age)),
                "age_hours": age, "age_source": "generated_at", "stale": stale,
                "max_age_hours": 48.0}

    base = _ev("baseline", baseline_age, baseline_stale)
    base.update({"passed": 100, "failed": 0, "error": 0})
    red = _ev("redteam", redteam_age, redteam_stale)
    red.update({"total": 10, "passed": 10, "failed": 0, "pass_rate": 1.0})
    return {"ok": True, "acceptance_id": "HZ-%s" % sha[:8],
            "generated_at": _iso(gen), "mode": mode, "blocking": blocking,
            "commit": {"ok": True, "sha": sha, "author": "tester",
                       "date": _iso(gen), "subject": "test"},
            "evidence": {"baseline": base, "redteam": red},
            "max_evidence_age_hours": 48.0,
            "evidence_scope": evidence_scope, "verdict": verdict, "reasons": [],
            "rollback_point": rollback or ("git revert %s" % sha[:12]),
            "closed_by": "老田（人工）", "read_only": True}


def _add_sheet(work: Path, sha: str, verdict: str = "PASS",
               gen: datetime | None = None, *, filename: str | None = None,
               mutate=None, **kw) -> Path:
    obj = _sheet_obj(sha, verdict, gen, **kw)
    if mutate is not None:
        mutate(obj)
    return _write_json(work / (filename or ("acceptance_%s.json" % sha[:8])), obj)


def _run(tmp_path: Path, work: Path, **kw):
    h = _load()
    rep = h.review(work_dir=work, out_dir=tmp_path / "out", now=NOW,
                   repo_root=tmp_path, **kw)
    return h, rep


def _full_fixture(tmp_path: Path) -> dict:
    """六条全达标的完整夹具：10 PASS + 1 drill(OBSERVE) + 20 自然 REJECT +
    20 单复核清单（无误拒）+ 3/3 抽查 + daily/dev_events 台账。"""
    work = _work(tmp_path)
    pass_shas = [_sha(0xA000 + i) for i in range(10)]
    for i, sha in enumerate(pass_shas):
        _add_sheet(work, sha, "PASS", GEN_OLD + timedelta(minutes=i),
                   baseline_age=1.0 + i * 0.1, redteam_age=2.0)
    drill_sha = _sha(0xD000)
    _add_sheet(work, drill_sha, "OBSERVE", NOW - timedelta(hours=5))
    _write_json(work / "drills" / "drill_ledger_20261030.json", {
        "generated_at": _iso(NOW - timedelta(hours=4)),
        "witness": "ZHAOYUN",
        "drills": [{"file": "acceptance_%s.json" % drill_sha[:8],
                    "verdict": "OBSERVE", "commit": drill_sha,
                    "method": "证据超龄显式绑定", "witness": "ZHAOYUN"}],
    })
    reject_shas = [_sha(0xB000 + i) for i in range(20)]
    for i, sha in enumerate(reject_shas):
        _add_sheet(work, sha, "REJECT", NOW - timedelta(hours=3 + i))
    _write_json(work / "reject_review_20261030.json", {
        "generated_at": _iso(NOW),
        "reviews": [{"file": "acceptance_%s.json" % s[:8], "sha": s,
                     "overturned": False, "reviewer": "老田"}
                    for s in reject_shas],
    })
    _write_json(work / "audit_sample_3.json", {
        "generated_at": _iso(NOW), "seed": "0x5eed",
        "samples": [{"file": "acceptance_%s.json" % pass_shas[i][:8],
                     "sha": pass_shas[i], "consistent": True}
                    for i in range(3)],
    })
    daily_rows = [{"ts": _iso(NOW - timedelta(hours=i + 1)),
                   "commit": pass_shas[i][:12], "verdict": "PASS",
                   "blocking": False, "reasons": [], "baseline_failed": 0,
                   "redteam_rate": 1.0, "report": "x"} for i in range(6)]
    daily_rows.append({"ts": _iso(NOW - timedelta(hours=7)),
                       "commit": reject_shas[0][:12], "verdict": "REJECT",
                       "blocking": False, "reasons": ["证据超龄"]})
    (work / "daily.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in daily_rows) + "\n",
        encoding="utf-8")
    events = tmp_path / "portal" / "data" / "dev_events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"ts": (NOW - timedelta(hours=2)).strftime(
        "%Y-%m-%d %H:%M:%S"), "kind": "merge_done", "task": "X"})
        for _ in range(100)]
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"work": work, "pass_shas": pass_shas,
            "reject_shas": reject_shas, "drill_sha": drill_sha}


# ------------------------------------------------------------ 全达标 / 报告

def test_full_compliance_overall_pass(tmp_path):
    fx = _full_fixture(tmp_path)
    _, rep = _run(tmp_path, fx["work"])
    statuses = [it["status"] for it in rep["items"]]
    assert statuses == ["✓"] * 6
    assert rep["summary"]["overall_pass"] is True
    assert rep["summary"]["passed"] == 6
    assert rep["items"][0]["value"]["count"] == 10
    assert rep["items"][1]["value"]["hits"] == 1
    assert rep["items"][2]["value"]["rate"] == 0.0
    assert rep["items"][3]["value"]["mature"] == 10
    assert rep["items"][4]["value"]["completeness_rate"] == 1.0
    assert rep["items"][5]["value"]["consistent"] == 3
    assert rep["kpi"]["runs"] == 7 and rep["kpi"]["merge_done"] == 100
    jp, mp = Path(rep["report_json"]), Path(rep["report_md"])
    assert jp.exists() and mp.exists()
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["summary"]["overall_pass"] is True
    md = mp.read_text(encoding="utf-8")
    assert "overall_pass = True" in md and "①" in md and "六条总览" in md


# ------------------------------------------------------------------ ① / ②

def test_missing_pass_samples_marks_fail(tmp_path):
    work = _work(tmp_path)
    for i in range(3):
        _add_sheet(work, _sha(0x1000 + i), "PASS", GEN_OLD)
    _, rep = _run(tmp_path, work)
    it = rep["items"][0]
    assert it["status"] == "✗"
    assert it["value"]["count"] == 3
    assert rep["summary"]["overall_pass"] is False


def test_commit_dedup_and_drill_exclusion(tmp_path):
    work = _work(tmp_path)
    sha_a = _sha(0x2000)
    _add_sheet(work, sha_a, "PASS", GEN_OLD)
    _add_sheet(work, sha_a, "PASS", GEN_OLD + timedelta(hours=2),
               filename="acceptance_%s_resubmit.json" % sha_a[:8])
    sha_drill = _sha(0x2001)
    _add_sheet(work, sha_drill, "PASS", GEN_OLD)
    sha_c = _sha(0x2002)
    _add_sheet(work, sha_c, "PASS", GEN_OLD)
    _write_json(work / "drills" / "drill_ledger_x.json", {"drills": [
        {"file": "acceptance_%s.json" % sha_drill[:8], "verdict": "REJECT",
         "commit": sha_drill}]})
    _, rep = _run(tmp_path, work)
    it = rep["items"][0]
    assert it["value"]["count"] == 2          # sha_a（去重后 1）+ sha_c
    files = [s["file"] for s in it["detail"]["samples"]]
    assert "acceptance_%s_resubmit.json" % sha_a[:8] in files  # 同 commit 取最新
    excluded = [x["file"] for x in it["detail"]["excluded_drills"]]
    assert "acceptance_%s.json" % sha_drill[:8] in excluded


def test_drill_ledger_hit_marks_ok(tmp_path):
    work = _work(tmp_path)
    sha = _sha(0x3000)
    _add_sheet(work, sha, "REJECT", NOW - timedelta(hours=2))
    # 顶层数组写法 + verdict 缺失（工具按 file/commit 从验收单回填）
    _write_json(work / "drills" / "drill_ledger_a.json", [
        {"file": "acceptance_%s.json" % sha[:8], "commit": sha,
         "witness": "ZHAOYUN"}])
    _, rep = _run(tmp_path, work)
    it = rep["items"][1]
    assert it["status"] == "✓"
    assert it["value"]["hits"] == 1
    assert it["detail"]["hits"][0]["verdict"] == "REJECT"


def test_drill_ledger_lead_schema_supported(tmp_path):
    """兼容 Lead 实际台账 schema：acceptance_report/target_commit/kind/result_at。"""
    work = _work(tmp_path)
    rej_sha = _sha(0x3100)
    obs_sha = _sha(0x3101)
    _add_sheet(work, rej_sha, "REJECT", NOW - timedelta(hours=2))
    _add_sheet(work, obs_sha, "OBSERVE", NOW - timedelta(hours=2))
    _write_json(work / "drills" / "drill_ledger_20261030.json", {
        "schema": "huangzhong_drill_ledger/v1",
        "generated_at": _iso(NOW),
        "drills": [
            {"drill_id": "DRILL-REJECT-01", "drill": True, "kind": "REJECT",
             "target_commit": rej_sha,
             "acceptance_report": "work/huangzhong/acceptance_%s.json" % rej_sha[:8],
             "method": "历史失败证据·显式绑定",
             "result_at": _iso(NOW - timedelta(hours=1)), "witness": "待签字"},
            {"drill_id": "DRILL-OBSERVE-01", "drill": True, "kind": "OBSERVE",
             "target_commit": obs_sha,
             "acceptance_report": "work/huangzhong/acceptance_%s.json" % obs_sha[:8],
             "method": "证据超龄·显式绑定",
             "result_at": _iso(NOW - timedelta(hours=1)), "witness": "待签字"},
        ]})
    _, rep = _run(tmp_path, work)
    it = rep["items"][1]
    assert it["status"] == "✓"
    assert it["value"]["hits"] == 2
    hits = it["detail"]["hits"]
    assert {h["file"] for h in hits} == {
        "acceptance_%s.json" % rej_sha[:8], "acceptance_%s.json" % obs_sha[:8]}
    assert {h["sha8"] for h in hits} == {rej_sha[:8], obs_sha[:8]}
    assert rep["items"][2]["detail"]["natural_reject_sheets"] == 0


def test_drill_ledger_missing_pending(tmp_path):
    work = _work(tmp_path)
    _, rep = _run(tmp_path, work)
    it = rep["items"][1]
    assert it["status"] == "pending"
    assert it["detail"]["ledger_missing"] is True
    assert "drill_ledger_*.json" in it["detail"]["expected_glob"]
    assert rep["summary"]["overall_pass"] is False


# --------------------------------------------------------------------- ③

def test_misreject_rate_fail_when_over_5pct(tmp_path):
    work = _work(tmp_path)
    rejects = [_sha(0x7000 + i) for i in range(20)]
    for i, sha in enumerate(rejects):
        _add_sheet(work, sha, "REJECT", NOW - timedelta(hours=2 + i))
    _write_json(work / "reject_review_20261030.json", {"reviews": [
        {"sha": sha, "overturned": i < 2} for i, sha in enumerate(rejects)]})
    _, rep = _run(tmp_path, work)
    it = rep["items"][2]
    assert it["status"] == "✗"
    assert it["value"]["natural_reject"] == 20
    assert it["value"]["rate"] == pytest.approx(0.1)
    assert rep["summary"]["overall_pass"] is False


def test_misreject_review_missing_pending(tmp_path):
    work = _work(tmp_path)
    for i in range(20):
        _add_sheet(work, _sha(0x7100 + i), "REJECT", NOW - timedelta(hours=2 + i))
    _, rep = _run(tmp_path, work)
    it = rep["items"][2]
    assert it["status"] == "pending"
    assert len(it["detail"]["unreviewed"]) == 20


def test_misreject_low_sample_manual_review_clean_ok(tmp_path):
    """自然 REJECT<20：复核清单覆盖全部 REJECT 且 0 推翻 → ③ ✓（v1.1 口径）。"""
    work = _work(tmp_path)
    rejects = [_sha(0x7200 + i) for i in range(3)]
    for i, sha in enumerate(rejects):
        _add_sheet(work, sha, "REJECT", NOW - timedelta(hours=2 + i))
    _write_json(work / "reject_review_20261030.json", {"reviews": [
        {"sha": sha, "overturned": False, "reviewer": "老田"} for sha in rejects]})
    _, rep = _run(tmp_path, work)
    it = rep["items"][2]
    assert it["status"] == "✓"
    assert it["value"]["overturned"] == 0
    assert it["value"]["reviewed"] == 3
    assert it["detail"]["review_mode"] == "manual_per_sheet"
    assert "逐单复核 0 推翻（清单 3 条）" in it["measured"]
    md = Path(rep["report_md"]).read_text(encoding="utf-8")
    assert "逐单人工复核（自然 REJECT<20）" in md


def test_misreject_low_sample_drill_reject_reviewed_ok(tmp_path):
    """真实 10-30 形态：自然 REJECT 0<20 + 演练 REJECT 1，复核清单覆盖 → ③ ✓。"""
    work = _work(tmp_path)
    sha = _sha(0x7300)
    _add_sheet(work, sha, "REJECT", NOW - timedelta(hours=2))
    _write_json(work / "drills" / "drill_ledger_a.json", {"drills": [
        {"file": "acceptance_%s.json" % sha[:8], "verdict": "REJECT",
         "commit": sha}]})
    _write_json(work / "reject_review_20261030.json", {"reviews": [
        {"file": "acceptance_%s.json" % sha[:8], "sha": sha,
         "overturned": False, "reviewer": "老田"}]})
    _, rep = _run(tmp_path, work)
    it = rep["items"][2]
    assert it["status"] == "✓"
    assert it["value"]["natural_reject"] == 0
    assert it["measured"] == "自然 REJECT 0<20；逐单复核 0 推翻（清单 1 条）"


def test_misreject_low_sample_review_incomplete_pending(tmp_path):
    """自然 REJECT<20 且复核清单缺项 → ③ pending。"""
    work = _work(tmp_path)
    rejects = [_sha(0x7400 + i) for i in range(2)]
    for i, sha in enumerate(rejects):
        _add_sheet(work, sha, "REJECT", NOW - timedelta(hours=2 + i))
    _write_json(work / "reject_review_20261030.json", {"reviews": [
        {"sha": rejects[0], "overturned": False}]})
    _, rep = _run(tmp_path, work)
    it = rep["items"][2]
    assert it["status"] == "pending"
    assert it["detail"]["reviewed"] == 1
    assert len(it["detail"]["unreviewed"]) == 1
    assert "清单不完整" in it["measured"]


def test_misreject_low_sample_overturned_marks_fail(tmp_path):
    """自然 REJECT<20 但复核有推翻 → ③ ✗（逐单口径要求 0 推翻）。"""
    work = _work(tmp_path)
    rejects = [_sha(0x7500 + i) for i in range(2)]
    for i, sha in enumerate(rejects):
        _add_sheet(work, sha, "REJECT", NOW - timedelta(hours=2 + i))
    _write_json(work / "reject_review_20261030.json", {"reviews": [
        {"sha": rejects[0], "overturned": False},
        {"sha": rejects[1], "overturned": True}]})
    _, rep = _run(tmp_path, work)
    it = rep["items"][2]
    assert it["status"] == "✗"
    assert it["value"]["overturned"] == 1
    assert rep["summary"]["overall_pass"] is False


def test_misreject_no_reject_samples_pending(tmp_path):
    """零 REJECT 样本：无可复核对象 → ③ pending。"""
    work = _work(tmp_path)
    _add_sheet(work, _sha(0x7600), "PASS", GEN_OLD)
    _, rep = _run(tmp_path, work)
    it = rep["items"][2]
    assert it["status"] == "pending"
    assert it["value"]["total_reject"] == 0
    assert "无 REJECT 样本可复核" in it["measured"]


# --------------------------------------------------------------------- ④

def test_leakage_incident_marks_fail(tmp_path):
    work = _work(tmp_path)
    sha = _sha(0x8000)
    _add_sheet(work, sha, "PASS", GEN_OLD)
    inc_ts = GEN_OLD + timedelta(days=2)
    (work / "incidents.jsonl").write_text(
        json.dumps({"ts": _iso(inc_ts), "commit": sha, "kind": "revert",
                    "desc": "事故"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    _, rep = _run(tmp_path, work)
    it = rep["items"][3]
    assert it["status"] == "✗"
    assert it["value"]["leaks"] == 1
    assert it["detail"]["mature_samples"][0]["leak"] is True


def test_leakage_not_mature_tracking(tmp_path):
    work = _work(tmp_path)
    _add_sheet(work, _sha(0x8100), "PASS", NOW - timedelta(days=2))
    _, rep = _run(tmp_path, work)
    it = rep["items"][3]
    assert it["status"] == "tracking"
    assert it["value"]["mature"] == 0
    assert it["value"]["tracking"] == 1


# --------------------------------------------------------------------- ⑤

def test_evidence_stale_marks_fail(tmp_path):
    work = _work(tmp_path)
    _add_sheet(work, _sha(0x4000), "PASS", GEN_OLD,
               baseline_age=60.0, redteam_age=60.0,
               baseline_stale=True, redteam_stale=True)
    _, rep = _run(tmp_path, work)
    it = rep["items"][4]
    assert it["status"] == "✗"
    assert it["value"]["fresh"] == 0
    assert it["value"]["completeness_rate"] == 1.0
    assert it["detail"]["samples"][0]["fresh"] is False


def test_evidence_missing_field_marks_fail(tmp_path):
    work = _work(tmp_path)
    sha = _sha(0x4100)

    def _mutate(obj):
        del obj["evidence"]["baseline"]["age_hours"]

    _add_sheet(work, sha, "PASS", GEN_OLD, mutate=_mutate)
    _, rep = _run(tmp_path, work, legacy_v0_policy="count")
    it = rep["items"][4]
    assert it["status"] == "✗"
    sample = it["detail"]["samples"][0]
    assert sample["complete"] is False
    assert "evidence.baseline.age_hours" in sample["missing"]
    assert sample["fresh"] is None


def _legacy_v0_mutate(obj):
    """模拟 v0 旧验收单：无 evidence、无 max_evidence_age_hours（旧工具不落年龄字段）。"""
    obj["evidence"] = {}
    obj.pop("max_evidence_age_hours", None)
    obj["evidence_scope"] = None


def test_legacy_v0_missing_age_fields_exempt_pending(tmp_path):
    """缺证据年龄字段 → 默认 exempt 判 legacy、不计入⑤分母；无非 legacy → pending。"""
    work = _work(tmp_path)
    _add_sheet(work, _sha(0x4110), "PASS", GEN_OLD, mutate=_legacy_v0_mutate)
    _, rep = _run(tmp_path, work)
    it = rep["items"][4]
    assert it["status"] == "pending"
    assert it["detail"]["legacy_policy"] == "exempt"
    assert it["detail"]["legacy_detected_count"] == 1
    assert it["detail"]["legacy_excluded"]["count"] == 1
    assert it["detail"]["legacy_excluded"]["files"] == ["acceptance_00004110.json"]
    assert it["detail"]["total"] == 0
    assert "无非 legacy" in it["measured"]
    md = Path(rep["report_md"]).read_text(encoding="utf-8")
    assert "legacy v0 豁免 1 单" in md


def test_legacy_v0_exempt_item5_ok_and_count_old_behavior(tmp_path, capsys):
    """2 张 v1 新单 + 2 张 v0 旧单：exempt → ⑤ ✓（只按新单判）；count → 旧行为 ✗。"""
    work = _work(tmp_path)
    for i in range(2):
        _add_sheet(work, _sha(0x4200 + i), "PASS", GEN_OLD,
                   baseline_age=1.0 + i, redteam_age=2.0)
    for i in range(2):
        _add_sheet(work, _sha(0x4210 + i), "PASS", GEN_OLD,
                   mutate=_legacy_v0_mutate)
    _, rep = _run(tmp_path, work)
    it = rep["items"][4]
    assert it["status"] == "✓"
    assert it["value"]["total"] == 2
    assert it["value"]["complete"] == 2
    assert it["value"]["fresh"] == 2
    assert it["value"]["legacy_excluded"] == 2
    assert it["detail"]["legacy_detected_count"] == 2
    assert len(it["detail"]["legacy_excluded"]["files"]) == 2
    assert "legacy v0 豁免 2 单" in it["measured"]
    md = Path(rep["report_md"]).read_text(encoding="utf-8")
    assert "legacy v0 豁免 2 单" in md
    # count = 旧行为（CLI --legacy-v0-policy 透传）：旧单计入分母 → ⑤ ✗
    h = _load()
    rc = h.main(["--repo-root", str(tmp_path), "--legacy-v0-policy", "count",
                 "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["legacy_v0_policy"] == "count"
    it5 = data["items"][4]
    assert it5["status"] == "✗"
    assert it5["detail"]["total"] == 4
    assert it5["detail"]["complete_count"] == 2
    assert it5["detail"]["legacy_excluded"]["count"] == 0
    assert "count 旧口径" in it5["detail"]["note"]


# --------------------------------------------------------------------- ⑥

def test_audit_missing_pending(tmp_path):
    work = _work(tmp_path)
    _add_sheet(work, _sha(0x5000), "PASS", GEN_OLD)
    _, rep = _run(tmp_path, work)
    assert rep["items"][5]["status"] == "pending"
    assert rep["summary"]["overall_pass"] is False


def test_audit_inconsistent_marks_fail(tmp_path):
    work = _work(tmp_path)
    shas = [_sha(0x5100 + i) for i in range(3)]
    for sha in shas:
        _add_sheet(work, sha, "PASS", GEN_OLD)
    _write_json(work / "audit_sample_3.json", {"samples": [
        {"sha": shas[0], "consistent": True},
        {"sha": shas[1], "consistent": True},
        {"sha": shas[2], "consistent": False}]})
    _, rep = _run(tmp_path, work)
    it = rep["items"][5]
    assert it["status"] == "✗"
    assert it["value"]["consistent"] == 2


# ------------------------------------------------------ 坏行容错 + CLI 冒烟

def test_bad_inputs_tolerated(tmp_path):
    work = _work(tmp_path)
    (work / "acceptance_bad00000.json").write_text("{not-json", encoding="utf-8")
    _add_sheet(work, _sha(0x6000), "PASS", GEN_OLD)
    sha_observe = _sha(0x6001)
    _add_sheet(work, sha_observe, "OBSERVE", NOW - timedelta(hours=2))
    (work / "daily.jsonl").write_text(
        json.dumps({"ts": _iso(NOW - timedelta(hours=1)), "verdict": "PASS"})
        + "\n{坏行\n", encoding="utf-8")
    (work / "incidents.jsonl").write_text(
        "{oops\n" + json.dumps({"ts": _iso(NOW), "commit": "x"}) + "\n",
        encoding="utf-8")
    # drill 台账用 JSONL（多行 → 整文件非法 JSON → 工具按行退路解析）
    (work / "drills" / "drill_ledger_b.json").write_text(
        json.dumps({"file": "acceptance_%s.json" % sha_observe[:8],
                    "verdict": "OBSERVE"}) + "\n"
        + json.dumps({"file": "acceptance_deadbeef.json",
                      "verdict": "OBSERVE"}) + "\n",
        encoding="utf-8")
    (work / "audit_sample_3.json").write_text("{bad", encoding="utf-8")
    _, rep = _run(tmp_path, work)
    assert rep["ok"] is True
    assert rep["items"][1]["status"] == "✓"       # JSONL 台账退路
    assert rep["items"][5]["status"] == "pending"  # 抽查不可读 → pending
    assert any("acceptance_bad00000.json" in w for w in rep["warnings"])
    assert rep["kpi"]["daily_bad_lines"] == 1


def test_cli_writes_report_and_exit_code(tmp_path, capsys):
    h = _load()
    rc = h.main(["--repo-root", str(tmp_path), "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert rc == 1
    assert data["summary"]["overall_pass"] is False
    work = tmp_path / "work" / "huangzhong"
    assert sorted(work.glob("review_*.json"))
    assert sorted(work.glob("review_*.md"))
    assert Path(data["report_json"]).exists()
