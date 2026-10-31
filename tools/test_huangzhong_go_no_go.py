# -*- coding: utf-8 -*-
"""黄忠 11-04 Go/No-Go 决策工具单测（tmp 夹具，不读真实 work/、不写真实 deliverables）。

覆盖：
  · 六条全 ✓ + kpi 覆盖率 12% → GO（含 md 草案落盘、L3 提示）；
  · 六条全 ✓ + 覆盖率 8% → 条件化 GO（列覆盖率缺口/条件）；
  · 存在 ✗ → NO-GO（缺口列出失败项）；
  · ⑤ 缺失/④ tracking 口径：④ tracking 时明确「仅时间项待满窗」并给出满窗样本数；
  · 六条全 ✓ 但覆盖率不可判定 → fail-visible NO-GO；
  · incidents.jsonl 缺失 → 标注「视为无已知事故」；
  · 唯一 commit 去重（同 sha 多单计 1）+ 演练单排除；
  · 最新 review 选择（mtime）、坏 review 跳过、坏 acceptance/incidents/台账容错；
  · review 缺失 → ok=False（退出码 3，不写草案）；review 过期 → warning；
  · CLI 退出码 GO=0 / NO-GO=1 / 条件化=2 与草案目录落盘。
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 4, 9, 30, tzinfo=TZ)


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_go_no_go", _TOOLS / "huangzhong_go_no_go.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


def _sha(n: int) -> str:
    return ("%08x" % n) + "0" * 32


def _write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


_KPI = {"window_days": 7, "runs": 12, "counts": {"PASS": 12, "REJECT": 0,
                                                "OBSERVE": 0},
        "merge_done": 100, "merge_done_available": True, "coverage_rate": 0.12}

_ITEMS_META = {
    "①": ("PASS 验收样本量", "12/10 单", "≥10 单 PASS"),
    "②": ("受控拒收演练", "2 次（REJECT=1 OBSERVE=1）", "≥1 次"),
    "③": ("误拒绝率 <5%", "自然 REJECT 0<20；逐单复核 0 推翻（清单 1 条）", "<5%"),
    "④": ("漏放 = 0", "满窗 5 单，漏放 0", "满 7 天窗口 PASS 单事故数 = 0"),
    "⑤": ("证据链完整率 100% + 年龄 ≤48h", "完整率 100.0%（12/12）",
          "11 项必需字段完整率 100%"),
    "⑥": ("赵云抽查 3/3 一致", "3/3 一致", "3 单抽查一致（3/3）"),
}


def _review(overrides=None, kpi=None, gen=None, pass_count=12) -> dict:
    """默认六条全 ✓ 的复核报告；overrides={id: (status, measured)}。"""
    items = []
    for iid, (title, measured, target) in _ITEMS_META.items():
        status = "✓"
        if overrides and iid in overrides:
            status, measured = overrides[iid]
        it = {"id": iid, "title": title, "status": status,
              "measured": measured, "target": target, "value": {}, "detail": {}}
        if iid == "①":
            it["value"] = {"count": pass_count, "target": 10}
            it["measured"] = "%d/10 单" % pass_count
        if iid == "④":
            samples = [{"file": "acceptance_%s.json" % _sha(0xC000 + i)[:8],
                        "sha8": _sha(0xC000 + i)[:8],
                        "generated_at": _iso(NOW - timedelta(days=2)),
                        "window_end": _iso(NOW + timedelta(days=5))}
                       for i in range(3)]
            it["value"] = {"mature": 0, "leaks": 0, "tracking": 3}
            it["detail"] = {"window_days": 7, "mature_count": 0,
                            "tracking_count": 3, "mature_samples": [],
                            "tracking_samples": samples}
            if status == "tracking":
                it["measured"] = "满窗 0/3 单（3 单跟踪中）"
        items.append(it)
    passed = sum(1 for i in items if i["status"] == "✓")
    kpi = dict(kpi or _KPI)
    gen = gen or NOW
    return {
        "ok": True, "tool": "huangzhong_review.py",
        "version": "v1.1 (2026-10-30)", "generated_at": _iso(gen),
        "window_days": kpi.get("window_days", 7), "repo_root": "x",
        "work_dir": "x", "read_only": True, "targets": {},
        "items": items,
        "summary": {
            "overall_pass": passed == 6, "passed": passed, "total": 6,
            "items": [{"id": i["id"], "title": i["title"], "status": i["status"],
                       "measured": i["measured"]} for i in items],
            "blocking_items": [{"id": i["id"], "title": i["title"],
                                "status": i["status"], "measured": i["measured"]}
                               for i in items if i["status"] != "✓"],
        },
        "kpi": kpi, "inputs": {}, "warnings": [],
    }


def _setup(tmp_path: Path, review=None, pass_shas=None, drill_sha=None,
           incidents=None, bad_acceptance=False, bad_ledger=False,
           review_name="review_20261104_093000.json") -> Path:
    work = tmp_path / "work" / "huangzhong"
    work.mkdir(parents=True, exist_ok=True)
    if review is not None:
        _write_json(work / review_name, review)
    for sha in pass_shas or []:
        _write_json(work / ("acceptance_%s.json" % sha[:8]), {
            "ok": True, "mode": "observe", "blocking": False, "verdict": "PASS",
            "commit": {"sha": sha}, "evidence": {},
            "rollback_point": "git revert %s" % sha[:12]})
    if drill_sha is not None:
        _write_json(work / "drills" / "drill_ledger_20261104.json", {
            "schema": "huangzhong_drill_ledger/v1",
            "drills": [{"kind": "REJECT", "verdict": "REJECT",
                        "target_commit": drill_sha,
                        "acceptance_report": "work/huangzhong/acceptance_%s.json"
                                             % drill_sha[:8]}]})
    if bad_ledger:
        (work / "drills").mkdir(parents=True, exist_ok=True)
        (work / "drills" / "drill_ledger_bad00.json").write_text("{oops",
                                                                 encoding="utf-8")
    if incidents is not None:
        (work / "incidents.jsonl").write_text(incidents, encoding="utf-8")
    if bad_acceptance:
        (work / "acceptance_badbad00.json").write_text("{oops", encoding="utf-8")
    return work


def _run(tmp_path: Path, work: Path, **kw):
    h = _load()
    return h, h.run(work_dir=work, draft_dir=tmp_path / "drafts", now=NOW,
                    repo_root=tmp_path, **kw)


def _md(rep: dict) -> str:
    return Path(rep["draft"]["path"]).read_text(encoding="utf-8")


# ------------------------------------------------------ 决策：GO / 条件化 / NO-GO

def test_go_when_all_ok_and_coverage_12pct(tmp_path):
    shas = [_sha(0x1000 + i) for i in range(12)]
    work = _setup(tmp_path, review=_review(), pass_shas=shas)
    _, rep = _run(tmp_path, work)
    assert rep["ok"] is True
    assert rep["six"]["all_ok"] is True and rep["six"]["passed"] == 6
    assert rep["decision"]["code"] == "GO"
    assert rep["decision"]["l3_required"] is True
    assert rep["coverage"]["unique_commit"] == 12
    assert rep["coverage"]["unique_coverage"] == pytest.approx(0.12)
    assert rep["draft"]["written"] is True
    md = _md(rep)
    assert "建议 GO" in md and "六条明细" in md and "免责" in md
    # 草案只落 draft-dir，不写 work/
    assert not list(work.glob("*.md"))


def test_conditional_go_when_coverage_8pct(tmp_path):
    shas = [_sha(0x1800 + i) for i in range(8)]
    kpi = dict(_KPI, runs=8, coverage_rate=0.08)
    work = _setup(tmp_path, review=_review(kpi=kpi, pass_count=8), pass_shas=shas)
    _, rep = _run(tmp_path, work)
    assert rep["decision"]["code"] == "CONDITIONAL_GO"
    assert rep["six"]["all_ok"] is True
    gap_ids = [g["id"] for g in rep["decision"]["gaps"]]
    assert "COV" in gap_ids
    assert rep["decision"]["conditions"]
    assert "条件化" in _md(rep)


def test_no_go_when_item_fails(tmp_path):
    shas = [_sha(0x1900 + i) for i in range(12)]
    review = _review(overrides={"⑤": ("✗", "完整率 80.0%（8/10）")})
    work = _setup(tmp_path, review=review, pass_shas=shas)
    _, rep = _run(tmp_path, work)
    assert rep["decision"]["code"] == "NO_GO"
    assert rep["six"]["passed"] == 5
    gap_ids = [g["id"] for g in rep["decision"]["gaps"]]
    assert "⑤" in gap_ids
    assert "NO-GO" in _md(rep)


def test_coverage_unknown_all_ok_is_no_go(tmp_path):
    shas = [_sha(0x1A00 + i) for i in range(12)]
    kpi = dict(_KPI)
    kpi.pop("coverage_rate")
    work = _setup(tmp_path, review=_review(kpi=kpi), pass_shas=shas)
    _, rep = _run(tmp_path, work)
    assert rep["decision"]["code"] == "NO_GO"      # fail-visible，不编造覆盖率
    assert any(g["id"] == "COV" for g in rep["decision"]["gaps"])
    assert any("覆盖率" in w for w in rep["warnings"])


def test_tracking_only_gap_notes_maturity(tmp_path):
    shas = [_sha(0x1B00 + i) for i in range(12)]
    review = _review(overrides={"④": ("tracking", "满窗 0/52 单（52 单跟踪中）")})
    # 覆盖 ④ detail：已满窗 5 单、跟踪 52 单
    it4 = [i for i in review["items"] if i["id"] == "④"][0]
    it4["detail"].update({"mature_count": 5, "tracking_count": 52,
                          "mature_samples": [
                              {"file": "acceptance_%s.json" % _sha(0xE000)[:8],
                               "sha8": _sha(0xE000)[:8]}]})
    it4["value"].update({"mature": 5, "tracking": 52})
    work = _setup(tmp_path, review=review, pass_shas=shas)
    _, rep = _run(tmp_path, work)
    assert rep["decision"]["code"] == "NO_GO"
    assert rep["tracking"]["is_tracking"] is True
    assert rep["tracking"]["mature_count"] == 5
    assert rep["tracking"]["tracking_count"] == 52
    note = rep["decision"]["tracking_note"] or ""
    assert "仅时间项待满窗" in note and "满窗样本 5 单" in note
    md = _md(rep)
    assert "仅时间项待满窗" in md and "满窗样本 5 单" in md


# ------------------------------------------------------ 覆盖率：去重 / 演练排除

def test_unique_commit_dedup_and_drill_exclusion(tmp_path):
    sha_a, sha_b, sha_drill = _sha(0x2000), _sha(0x2001), _sha(0x2002)
    work = _setup(tmp_path, review=_review(pass_count=2))
    _write_json(work / ("acceptance_%s.json" % sha_a[:8]),
                {"verdict": "PASS", "commit": {"sha": sha_a}})
    _write_json(work / ("acceptance_%s_resubmit.json" % sha_a[:8]),
                {"verdict": "PASS", "commit": {"sha": sha_a}})
    _write_json(work / ("acceptance_%s.json" % sha_b[:8]),
                {"verdict": "PASS", "commit": {"sha": sha_b}})
    _write_json(work / ("acceptance_%s.json" % sha_drill[:8]),
                {"verdict": "PASS", "commit": {"sha": sha_drill}})
    _write_json(work / "drills" / "drill_ledger_20261104.json", {
        "drills": [{"kind": "REJECT", "target_commit": sha_drill,
                    "acceptance_report": "work/huangzhong/acceptance_%s.json"
                                         % sha_drill[:8]}]})
    _, rep = _run(tmp_path, work)
    cov = rep["coverage"]
    assert cov["unique_commit"] == 2                    # sha_a 去重 + sha_b
    assert cov["pass_sheets_raw"] == 3                  # 去重前 PASS 单（排除演练单）
    assert cov["duplicate_sheets"] == 1
    assert any(sha_drill[:8] in f for f in cov["excluded_drills"])
    assert rep["coverage"]["unique_coverage"] == pytest.approx(0.02)


# ------------------------------------------------------- 输入容错 / 最新报告选择

def test_latest_review_selected_by_mtime(tmp_path):
    shas = [_sha(0x2100 + i) for i in range(12)]
    work = _setup(tmp_path, review=_review(
        overrides={"⑤": ("✗", "完整率 80.0%（8/10）")}), pass_shas=shas,
        review_name="review_20261103_090000.json")
    _write_json(work / "review_20261104_093000.json", _review())  # 后写 → 更新
    _, rep = _run(tmp_path, work)
    assert rep["review"]["path"].endswith("review_20261104_093000.json")
    assert rep["decision"]["code"] == "GO"


def test_bad_review_skipped_with_warning(tmp_path):
    shas = [_sha(0x2200 + i) for i in range(12)]
    work = _setup(tmp_path, review=_review(), pass_shas=shas)
    # 更新但结构不完整的 review → 跳过并用上一份，fail-visible
    _write_json(work / "review_20261104_095000.json", {"items": "bad", "kpi": {}})
    _, rep = _run(tmp_path, work)
    assert rep["ok"] is True
    assert rep["review"]["path"].endswith("review_20261104_093000.json")
    assert any("结构不完整" in w for w in rep["warnings"])


def test_bad_inputs_tolerated(tmp_path):
    shas = [_sha(0x2300 + i) for i in range(12)]
    incidents = (json.dumps({"ts": _iso(NOW - timedelta(hours=1)),
                             "commit": shas[0][:8], "kind": "revert"},
                            ensure_ascii=False) + "\n{oops\n")
    work = _setup(tmp_path, review=_review(), pass_shas=shas,
                  incidents=incidents, bad_acceptance=True, bad_ledger=True)
    _, rep = _run(tmp_path, work)
    assert rep["ok"] is True
    assert rep["incidents"]["exists"] is True
    assert rep["incidents"]["rows"] == 1
    assert rep["incidents"]["bad_lines"] == 1
    assert rep["coverage"]["unique_commit"] == 12
    assert any("验收单不可读" in w for w in rep["warnings"])
    assert any("drill 台账异常" in w for w in rep["warnings"])
    assert rep["draft"]["written"] is True


def test_incidents_missing_annotated(tmp_path):
    shas = [_sha(0x2400 + i) for i in range(12)]
    work = _setup(tmp_path, review=_review(), pass_shas=shas)
    _, rep = _run(tmp_path, work)
    assert rep["incidents"]["exists"] is False
    assert "视为无已知事故" in rep["incidents"]["note"]
    assert "incidents.jsonl 缺失" in _md(rep)


def test_stale_review_warns(tmp_path):
    shas = [_sha(0x2500 + i) for i in range(12)]
    review = _review(gen=NOW - timedelta(hours=48))
    work = _setup(tmp_path, review=review, pass_shas=shas)
    _, rep = _run(tmp_path, work)
    assert any("现场重跑" in w for w in rep["warnings"])


# --------------------------------------------------------------- 缺失 / CLI

def test_missing_review_is_fail_visible(tmp_path):
    work = _setup(tmp_path, review=None)
    h, rep = _run(tmp_path, work)
    assert rep["ok"] is False
    assert "review_*.json" in rep["error"]
    assert rep["draft"]["written"] is False
    assert not (tmp_path / "drafts").exists()   # 报错即不写草案
    # CLI 退出码 3（fail-visible），输出 JSON 错误
    assert h.main(["--work-dir", str(work),
                   "--draft-dir", str(tmp_path / "drafts")]) == 3


def test_cli_exit_codes_and_draft_dir(tmp_path, capsys):
    h = _load()
    # GO → rc 0
    shas = [_sha(0x2600 + i) for i in range(12)]
    work = _setup(tmp_path, review=_review(), pass_shas=shas)
    rc = h.main(["--json", "--work-dir", str(work),
                 "--draft-dir", str(tmp_path / "drafts_go")])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert rc == 0
    assert data["decision"]["code"] == "GO"
    assert Path(data["draft"]["path"]).parent == tmp_path / "drafts_go"
    # NO-GO → rc 1
    work2 = _setup(tmp_path / "nogo", review=_review(
        overrides={"⑤": ("✗", "完整率 80.0%")}), pass_shas=shas)
    rc2 = h.main(["--json", "--work-dir", str(work2),
                  "--draft-dir", str(tmp_path / "drafts_nogo")])
    capsys.readouterr()
    assert rc2 == 1
    # 条件化 GO → rc 2
    kpi = dict(_KPI, runs=8, coverage_rate=0.08)
    work3 = _setup(tmp_path / "cond",
                   review=_review(kpi=kpi, pass_count=8), pass_shas=shas[:8])
    rc3 = h.main(["--json", "--work-dir", str(work3),
                  "--draft-dir", str(tmp_path / "drafts_cond")])
    capsys.readouterr()
    assert rc3 == 2
