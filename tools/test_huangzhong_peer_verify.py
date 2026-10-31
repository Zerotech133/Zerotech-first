# -*- coding: utf-8 -*-
"""huangzhong_peer_verify.py 单测（tmp 夹具；不读真实 work/、不写真实 deliverables）。

覆盖：
  · 全一致 → status=ok（一致率 100%，md 落 --out-dir）；
  · 分歧（consistent=false / 显式 verdict=FAIL）→ status=fail + 分歧清单；
  · 缺数据：audit 缺失 → fail；acceptance 未匹配 → 缺数据 warn；无 sha/无 file 标注；
  · 样本缺 sha：按 file/文件名 sha8 尽力匹配 + missing_sha/inferred 如实标注；
  · 同 commit 多单取最新（重复计数）；演练单匹配 → 缺数据+warning；
  · 坏 JSON 容错（audit/acceptance/repair_lane）；顶层数组 audit 变体；checks 推导判定；
  · 关羽修复车道探测（实际路径 agent_platform/data/repair/lane.json / 候选 repair_lane）；
  · 只读：不写 work/、不改数据文件；CLI --json 与退出码 0/1/2。
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 31, 19, 0, tzinfo=TZ)


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_peer_verify", _TOOLS / "huangzhong_peer_verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


def _write(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _sha(n: int) -> str:
    return ("%08x" % n) + "0" * 32


def _work(tmp_path: Path) -> Path:
    work = tmp_path / "work" / "huangzhong"
    (work / "drills").mkdir(parents=True, exist_ok=True)
    return work


def _accept(work: Path, sha: str, verdict: str = "PASS",
            gen: datetime | None = None, *, filename=None, drill=False) -> Path:
    obj = {"generated_at": _iso(gen or (NOW - timedelta(hours=1))),
           "verdict": verdict, "commit": {"sha": sha}, "mode": "observe",
           "blocking": False}
    if drill:
        obj["drill"] = True
    return _write(work / (filename or ("acceptance_%s.json" % sha[:8])), obj)


def _good_checks() -> dict:
    return {"commit_readable": True, "is_master_ancestor": True,
            "report_md_exists": True, "verdict_pass": True,
            "rollback_point": True, "evidence_baseline_ok": True,
            "evidence_redteam_ok": True, "mode_observe_no_block": True}


def _sample(sha: str, *, consistent=True, file=None, **extra) -> dict:
    s = {"sha": sha, "sha8": sha[:8],
         "file": file or ("acceptance_%s.json" % sha[:8]),
         "checks": _good_checks(), "consistent": consistent, "note": ""}
    s.update(extra)
    return s


def _audit_obj(samples, **meta) -> dict:
    obj = {"schema": "huangzhong_audit_sample/v1",
           "generated_at": _iso(NOW), "seed": "seed-test", "result": "3/3",
           "consistent": len(samples), "total": len(samples),
           "executor": "DSH-Lead（代赵云执行）", "reviewer": "老田"}
    obj.update(meta)
    obj["samples"] = samples
    return obj


def _setup(tmp_path: Path, *, samples=None, audits_variant="dict",
           acceptances=None, audit_meta=None, repair_lane=None) -> Path:
    work = _work(tmp_path)
    for sha, verdict, gen in (acceptances if acceptances is not None else []):
        _accept(work, sha, verdict, gen)
    if samples is not None:
        if audits_variant == "list":
            _write(work / "audit_sample_3.json", samples)
        else:
            _write(work / "audit_sample_3.json", _audit_obj(samples,
                                                            **(audit_meta or {})))
    if repair_lane is not None:
        _write(tmp_path / "agent_platform" / "data" / "repair" / "lane.json",
               repair_lane)
    return tmp_path


def _run(root: Path, out: Path, **kw):
    h = _load()
    kw.setdefault("now", NOW)
    return h, h.run(repo_root=root, out_dir=out, **kw)


def _row(rep: dict, idx: int) -> dict:
    return next(r for r in rep["rows"] if r["index"] == idx)


# ------------------------------------------------------------------ 一致

def test_all_agree_ok(tmp_path):
    shas = [_sha(0xA000 + i) for i in range(3)]
    root = _setup(tmp_path,
                  samples=[_sample(s) for s in shas],
                  acceptances=[(s, "PASS", NOW - timedelta(hours=1))
                               for s in shas])
    out = tmp_path / "out"
    _, rep = _run(root, out)
    assert rep["status"] == "ok"
    assert rep["ok"] is True
    assert rep["summary"]["agree"] == 3
    assert rep["summary"]["disagree"] == 0
    assert rep["summary"]["missing"] == 0
    assert rep["summary"]["agreement_rate"] == 1.0
    assert rep["data_sources"]["audit"]["ok"] is True
    assert rep["data_sources"]["audit"]["seed"] == "seed-test"
    assert rep["data_sources"]["acceptance"]["unique_commits"] == 3
    md = Path(rep["report_md"])
    assert md.exists() and md.parent == out
    text = md.read_text(encoding="utf-8")
    assert "跨 Agent 互验报告" in text and "一致率" in text


# ------------------------------------------------------------------ 分歧

def test_disagree_consistent_false_fail(tmp_path):
    shas = [_sha(0xA100 + i) for i in range(2)]
    samples = [_sample(shas[0], consistent=False), _sample(shas[1])]
    root = _setup(tmp_path, samples=samples,
                  acceptances=[(s, "PASS", NOW - timedelta(hours=1))
                               for s in shas])
    _, rep = _run(root, tmp_path / "out")
    assert rep["status"] == "fail"
    assert rep["summary"]["agree"] == 1
    assert rep["summary"]["disagree"] == 1
    bad = _row(rep, 1)
    assert bad["kind"] == "disagree" and bad["audit_verdict"] == "FAIL"
    assert bad["huang_verdict"] == "PASS"
    text = Path(rep["report_md"]).read_text(encoding="utf-8")
    assert "分歧清单" in text and shas[0][:8] in text


def test_disagree_explicit_verdict_fail(tmp_path):
    sha = _sha(0xA200)
    root = _setup(tmp_path,
                  samples=[{"sha": sha, "file": "acceptance_x.json",
                            "verdict": "FAIL"}],
                  acceptances=[(sha, "PASS", NOW - timedelta(hours=1))])
    _, rep = _run(root, tmp_path / "out")
    assert rep["status"] == "fail"
    assert rep["rows"][0]["audit_verdict_source"] == "verdict"


def test_audit_fail_and_huang_non_pass_is_agree(tmp_path):
    sha = _sha(0xA300)
    root = _setup(tmp_path,
                  samples=[_sample(sha, consistent=False)],
                  acceptances=[(sha, "REJECT", NOW - timedelta(hours=1))])
    _, rep = _run(root, tmp_path / "out")
    assert rep["summary"]["agree"] == 1
    assert rep["status"] == "ok"


# -------------------------------------------------------------- 缺数据/缺 sha

def test_audit_missing_fail(tmp_path):
    sha = _sha(0xA400)
    root = _setup(tmp_path, acceptances=[(sha, "PASS", NOW - timedelta(hours=1))])
    _, rep = _run(root, tmp_path / "out")
    assert rep["status"] == "fail"
    assert rep["data_sources"]["audit"]["exists"] is False
    assert "赵云抽查记录缺失" in "；".join(rep["warnings"])


def test_acceptance_not_found_missing_warn(tmp_path):
    root = _setup(tmp_path, samples=[_sample(_sha(0xA500))],
                  acceptances=[(_sha(0xA501), "PASS", NOW - timedelta(hours=1))])
    _, rep = _run(root, tmp_path / "out")
    assert rep["status"] == "warn"
    row = rep["rows"][0]
    assert row["kind"] == "missing" and row["reason"] == "acceptance_not_found"


def test_missing_sha_matched_by_file_and_annotated(tmp_path):
    sha = _sha(0xA600)
    sample = {"file": "acceptance_%s.json" % sha[:8], "consistent": True}
    root = _setup(tmp_path, samples=[sample],
                  acceptances=[(sha, "PASS", NOW - timedelta(hours=1))])
    _, rep = _run(root, tmp_path / "out")
    row = rep["rows"][0]
    assert row["kind"] == "agree" and row["match_basis"] == "sha"   # 文件名推断 sha8
    assert row["sha_missing"] is True and row["sha_inferred"] is True
    assert len(rep["summary"]["missing_sha"]) == 1
    assert len(rep["summary"]["inferred_sha"]) == 1
    assert rep["status"] == "warn"          # 缺 sha 如实标注 → warn
    text = Path(rep["report_md"]).read_text(encoding="utf-8")
    assert "样本缺 commit sha" in text


def test_no_sha_no_file_missing_warn(tmp_path):
    root = _setup(tmp_path, samples=[{"consistent": True, "note": "无 sha"}],
                  acceptances=[(_sha(0xA601), "PASS", NOW - timedelta(hours=1))])
    _, rep = _run(root, tmp_path / "out")
    assert rep["rows"][0]["reason"] == "no_sha_no_file"
    assert rep["status"] == "warn"


def test_matched_drill_sheet_missing(tmp_path):
    sha = _sha(0xA700)
    root = _setup(tmp_path, samples=[_sample(sha)],
                  acceptances=[(sha, "PASS", NOW - timedelta(hours=1))])
    # 覆盖为演练单
    _accept(_work(root), sha, "OBSERVE", NOW - timedelta(hours=1), drill=True)
    _, rep = _run(root, tmp_path / "out")
    row = rep["rows"][0]
    assert row["kind"] == "missing" and row["reason"] == "matched_drill_sheet"
    assert any("演练单" in w for w in rep["warnings"])


# ------------------------------------------------------ 匹配逻辑：同 sha 取最新

def test_duplicate_sha_uses_latest(tmp_path):
    sha = _sha(0xA800)
    work = _work(tmp_path)
    _accept(work, sha, "REJECT", NOW - timedelta(hours=5),
            filename="acceptance_old00000.json")
    _accept(work, sha, "PASS", NOW - timedelta(hours=1),
            filename="acceptance_%s.json" % sha[:8])
    samples = [_sample(sha)]
    _write(work / "audit_sample_3.json", _audit_obj(samples))
    _, rep = _run(tmp_path, tmp_path / "out")
    assert rep["data_sources"]["acceptance"]["duplicates"] == 1
    assert rep["rows"][0]["huang_verdict"] == "PASS"
    assert rep["rows"][0]["kind"] == "agree"


# ------------------------------------------------------- audit 变体 / checks

def test_audit_list_variant_and_checks_derived(tmp_path):
    sha = _sha(0xA900)
    bad_checks = dict(_good_checks())
    bad_checks["verdict_pass"] = False          # checks 推导 → FAIL
    sample = {"sha": sha, "file": "x.json", "checks": bad_checks}
    root = _setup(tmp_path, samples=[sample], audits_variant="list",
                  acceptances=[(sha, "PASS", NOW - timedelta(hours=1))])
    _, rep = _run(root, tmp_path / "out")
    assert rep["rows"][0]["audit_verdict"] == "FAIL"
    assert rep["rows"][0]["audit_verdict_source"] == "checks"
    assert rep["status"] == "fail"


# ------------------------------------------------- 关羽修复车道探测

def test_repair_lane_detected(tmp_path):
    sha = _sha(0xAA00)
    lane = {"schema": "guanyu_repair_lane/v1", "updated_at": "2026-10-27T07:50:00+08:00",
            "batches": [
                {"id": "RB-1", "module": "portal/portal_api.py",
                 "status": "pending", "created_at": "2026-10-27T07:41:43+0800"},
                {"id": "RB-2", "module": "tools/_mixin_integrity.py",
                 "status": "passed", "created_at": "2026-10-27T08:00:00+0800",
                 "last_run_at": "2026-10-27T08:05:00+0800"},
            ]}
    root = _setup(tmp_path, samples=[_sample(sha)],
                  acceptances=[(sha, "PASS", NOW - timedelta(hours=1))],
                  repair_lane=lane)
    _, rep = _run(root, tmp_path / "out")
    repair = rep["data_sources"]["repair_lane"]
    assert repair["found"] is True
    assert repair["source"].endswith("lane.json")
    assert repair["count"] == 2
    assert repair["status_counts"] == {"pending": 1, "passed": 1}
    assert any(p["exists"] for p in repair["candidates"])
    text = Path(rep["report_md"]).read_text(encoding="utf-8")
    assert "关羽修复车道" in text


def test_repair_lane_alternate_candidate(tmp_path):
    sha = _sha(0xAB00)
    root = _setup(tmp_path, samples=[_sample(sha)],
                  acceptances=[(sha, "PASS", NOW - timedelta(hours=1))])
    _write(root / "work" / "p1" / "repair_lane" / "lane.json", {
        "schema": "guanyu_repair_lane/v1",
        "batches": [{"id": "RB-9", "module": "m.py", "status": "failed"}]})
    _, rep = _run(root, tmp_path / "out")
    repair = rep["data_sources"]["repair_lane"]
    assert repair["found"] is True
    assert "repair_lane" in repair["source"]
    assert repair["status_counts"] == {"failed": 1}


def test_repair_lane_bad_json_tolerated(tmp_path):
    sha = _sha(0xAC00)
    root = _setup(tmp_path, samples=[_sample(sha)],
                  acceptances=[(sha, "PASS", NOW - timedelta(hours=1))])
    _write(root / "agent_platform" / "data" / "repair" / "lane.json", {})
    # 结构异常（无 batches）→ found False + errors，不影响互验状态
    _, rep = _run(root, tmp_path / "out")
    assert rep["data_sources"]["repair_lane"]["found"] is False
    assert rep["status"] == "ok"


# ------------------------------------------------------------- 坏 JSON 容错

def test_bad_acceptance_json_tolerated_warn(tmp_path):
    sha = _sha(0xAD00)
    root = _setup(tmp_path, samples=[_sample(sha)],
                  acceptances=[(sha, "PASS", NOW - timedelta(hours=1))])
    (_work(root) / "acceptance_bad00000.json").write_text("{oops",
                                                          encoding="utf-8")
    _, rep = _run(root, tmp_path / "out")
    assert rep["data_sources"]["acceptance"]["invalid"]
    assert rep["status"] == "warn"
    assert any("坏 JSON" in w for w in rep["warnings"])


def test_bad_audit_json_fail_visible(tmp_path):
    sha = _sha(0xAE00)
    root = _setup(tmp_path, acceptances=[(sha, "PASS", NOW - timedelta(hours=1))])
    (_work(root) / "audit_sample_3.json").write_text("{oops", encoding="utf-8")
    _, rep = _run(root, tmp_path / "out")
    assert rep["status"] == "fail"
    assert rep["data_sources"]["audit"]["ok"] is False
    assert rep["data_sources"]["audit"]["error"]


# ------------------------------------------------------------------ 只读

def test_read_only_no_work_writes(tmp_path):
    sha = _sha(0xAF00)
    root = _setup(tmp_path, samples=[_sample(sha)],
                  acceptances=[(sha, "PASS", NOW - timedelta(hours=1))])
    work = _work(root)
    _write(work / "drills" / "drill_ledger_20261031.json", {"drills": []})
    before = {p.relative_to(work).as_posix(): p.read_bytes()
              for p in work.rglob("*") if p.is_file()}
    out = tmp_path / "out"
    _, rep = _run(root, out)
    after = {p.relative_to(work).as_posix(): p.read_bytes()
             for p in work.rglob("*") if p.is_file()}
    assert before == after                 # work/ 数据零改动
    assert Path(rep["report_md"]).parent == out


# ---------------------------------------------------------------------- CLI

def test_cli_json_ok_exit_code(tmp_path, capsys):
    sha = _sha(0xB000)
    root = _setup(tmp_path, samples=[_sample(sha)],
                  acceptances=[(sha, "PASS", NOW - timedelta(hours=1))])
    h = _load()
    rc = h.main(["--json", "--repo-root", str(root),
                 "--out-dir", str(tmp_path / "out")])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["status"] == "ok"
    assert data["summary"]["agree"] == 1


def test_cli_disagree_exit_code(tmp_path, capsys):
    sha = _sha(0xB100)
    root = _setup(tmp_path, samples=[_sample(sha, consistent=False)],
                  acceptances=[(sha, "PASS", NOW - timedelta(hours=1))])
    h = _load()
    rc = h.main(["--json", "--repo-root", str(root),
                 "--out-dir", str(tmp_path / "out")])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert data["status"] == "fail"


def test_cli_warn_exit_code(tmp_path, capsys):
    sha = _sha(0xB200)
    root = _setup(tmp_path, samples=[{"file": "acceptance_%s.json" % sha[:8],
                                      "consistent": True}],
                  acceptances=[(sha, "PASS", NOW - timedelta(hours=1))])
    h = _load()
    rc = h.main(["--json", "--repo-root", str(root),
                 "--out-dir", str(tmp_path / "out")])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["status"] == "warn"
