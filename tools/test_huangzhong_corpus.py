# -*- coding: utf-8 -*-
"""黄忠验收语料库与一致性基准 单测（tmp 夹具；不碰真实 work/ 与 deliverables）。

覆盖：
  · 语料导出：条数/字段/排序；JSONL 一行一条 UTF-8；
  · legacy(v0) 识别（缺 evidence.*.age_hours/stale）与 drill（演练台账匹配）；
  · 同 commit 多单一致性（一致组/不一致清单）；
  · 按日趋势 / 原因词频 / 与最新 review 交叉；
  · --limit 上限（取最新 N 条）；--json CLI 结构 + 文件命名；
  · 坏 JSON/坏时间/缺演练台账 fail-visible；
  · 不写 work/（快照比对）；--no-write 模式；空语料可用。
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=TZ)
NOW_ISO = NOW.isoformat(timespec="seconds")
DATE = "2026-10-31"


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_corpus", _TOOLS / "huangzhong_corpus.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ 夹具

def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


def _write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def _acceptance(name: str, *, verdict: str, sha: str, generated_at,
                v1: bool = True, reasons=None, mode="observe",
                blocking=False, drill_flag=None) -> dict:
    evidence = {
        "baseline": {"ok": True, "generated_at": "2026-10-30T00:53:16+08:00",
                     "path": "agent_platform/data/test_baseline/latest.json",
                     "failed": 0, "error": 0},
        "redteam": {"ok": True, "generated_at": "2026-10-27T15:59:16+08:00",
                    "path": "work/p1/redteam/redteam_20261027_155916.json",
                    "pass_rate": 1.0},
    }
    if v1:
        evidence["baseline"].update({"age_hours": 11.1, "stale": False})
        evidence["redteam"].update({"age_hours": 58.0, "stale": True})
    data = {
        "ok": True,
        "acceptance_id": "HZ-%s-20261028120000" % sha[:8],
        "generated_at": generated_at,
        "mode": mode,
        "blocking": blocking,
        "commit": {"ok": True, "sha": sha, "subject": "fixture"},
        "evidence": evidence,
        "verdict": verdict,
        "reasons": list(reasons or []),
        "rollback_point": "git revert %s" % sha[:12],
        "closed_by": "老田（人工）",
    }
    if drill_flag is not None:
        data["drill"] = drill_flag
    return data


SHA_A = "a1d977aebd7e3a8e700b6789be1aeb2404400fd0"
SHA_B = "b2e088bf4e6f3a1c85e3d2a1b0c9f8e7d6a5b4c3"
SHA_E = "e3a1bfedba6756d2172305987e73f143995203e9"
SHA_F = "f4b2c0deab1234567890abcdef1234567890abcdef"


@pytest.fixture()
def env(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    work = tmp_path / "work" / "huangzhong"
    work.mkdir(parents=True)
    out = tmp_path / "out"
    return {"repo": repo, "work": work, "out": out}


def build_corpus(env, *, with_drill_ledger: bool = True,
                 with_review: bool = True):
    """构造 9 个验收单（含 legacy/drill/坏 JSON/坏时间）与台账/review。"""
    work = env["work"]
    t = NOW - timedelta(days=2)
    # sha A：3 单（PASS, PASS, REJECT）→ 同 commit 不一致组
    _write_json(work / "acceptance_a1d977ae.json", _acceptance(
        "a1d977ae", verdict="PASS", sha=SHA_A,
        generated_at=_iso(t), reasons=[]))
    _write_json(work / "acceptance_02fc06f8.json", _acceptance(
        "02fc06f8", verdict="PASS", sha=SHA_A,
        generated_at=_iso(t + timedelta(hours=2)),
        reasons=["红队通过率 50% < 80%"]))
    _write_json(work / "acceptance_438b307b.json", _acceptance(
        "438b307b", verdict="REJECT", sha=SHA_A,
        generated_at=_iso(t + timedelta(hours=4)),
        reasons=["红队通过率 50% < 80%", "红队证据超龄 59.0 h > 上限 48.0 h"]))
    # sha B：2 单 PASS → 同 commit 一致组
    _write_json(work / "acceptance_dc2ecd70.json", _acceptance(
        "dc2ecd70", verdict="PASS", sha=SHA_B,
        generated_at=_iso(t + timedelta(hours=1))))
    _write_json(work / "acceptance_9922d6dc.json", _acceptance(
        "9922d6dc", verdict="PASS", sha=SHA_B,
        generated_at=_iso(t + timedelta(hours=3))))
    # legacy v0（缺证据年龄字段）
    _write_json(work / "acceptance_725b8801.json", _acceptance(
        "725b8801", verdict="PASS", sha=SHA_E,
        generated_at=_iso(t + timedelta(hours=5)), v1=False))
    # 演练单（台账匹配）
    _write_json(work / "acceptance_76081eea.json", _acceptance(
        "76081eea", verdict="REJECT", sha=SHA_F,
        generated_at=_iso(NOW - timedelta(days=1)),
        reasons=["红队通过率 50% < 80%"]))
    # 坏 JSON
    (work / "acceptance_badbad00.json").write_text("{bad json", encoding="utf-8")
    # 坏时间
    _write_json(work / "acceptance_0b0b0b0b.json", _acceptance(
        "0b0b0b0b", verdict="OBSERVE", sha="c" * 40,
        generated_at="not-a-time"))
    # 报告 md（仅 A 单存在）
    (work / "acceptance_a1d977ae.md").write_text("# 验收单\n", encoding="utf-8")
    if with_drill_ledger:
        _write_json(work / "drills" / "drill_ledger_20261030.json", {
            "schema": "huangzhong_drill_ledger/v1",
            "generated_at": _iso(NOW - timedelta(days=1)),
            "drills": [
                {"drill_id": "DRILL-20261030-REJECT-01", "drill": True,
                 "kind": "REJECT", "verdict": "REJECT",
                 "target_commit": SHA_F,
                 "acceptance_report": "work/huangzhong/acceptance_76081eea.json",
                 "result_at": _iso(NOW - timedelta(days=1))},
            ],
        })
    if with_review:
        _write_json(work / "review_20261031_120000.json", {
            "ok": True, "generated_at": _iso(NOW - timedelta(hours=1)),
            "items": [{"id": "①", "title": "PASS 验收样本量", "status": "✓",
                       "measured": "5/10 单"}],
            "summary": {"overall_pass": False, "passed": 5, "total": 6},
            "kpi": {"runs": 59, "merge_done": 1468, "coverage_rate": 0.0402},
        })


def _setup(mod, monkeypatch):
    monkeypatch.setattr(mod, "_log", lambda msg: None)


def _run(mod, env, **kwargs):
    kwargs.setdefault("work_dir", str(env["work"]))
    kwargs.setdefault("out_dir", str(env["out"]))
    kwargs.setdefault("repo_root", str(env["repo"]))
    kwargs.setdefault("now", NOW)
    return mod.run(**kwargs)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in
            Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _work_snapshot(work: Path) -> dict:
    return {str(p.relative_to(work)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(work.rglob("*")) if p.is_file()}


# ------------------------------------------------------------------ 用例：导出/字段

def test_corpus_export_records_and_fields(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch)
    build_corpus(env)
    rep = _run(mod, env)
    assert rep["ok"] is True
    corpus = Path(rep["corpus_path"])
    assert corpus.is_file()
    assert corpus.name == "%s_黄忠验收语料_%s.jsonl" % (DATE, DATE)
    rows = _read_jsonl(corpus)
    # 9 个文件：1 坏 JSON 跳过 → 8 条有效（含坏时间记录）
    assert len(rows) == 8
    assert rep["exported_rows"] == 8
    assert rep["total_files"] == 9
    assert len(rep["stats"]["bad_files"]) == 1
    assert len(rep["stats"]["bad_time"]) == 1
    # 字段完整性
    for row in rows:
        for field in ("acceptance_id", "file", "verdict", "commit", "sha8",
                      "reasons", "generated_at", "date", "mode", "blocking",
                      "legacy", "drill", "evidence", "report_md"):
            assert field in row, "缺字段 %s" % field
        assert row["verdict"] in ("PASS", "REJECT", "OBSERVE")
        assert isinstance(row["reasons"], list)
        assert "age_hours" in row["evidence"]["baseline"]
        assert "stale" in row["evidence"]["redteam"]
    # 按 generated_at 升序（坏时间排最前，用文件名为稳定键）
    good = [r for r in rows if r["generated_at_ts"]]
    times = [r["generated_at_ts"] for r in good]
    assert times == sorted(times)
    # report_md 仅 A 单存在
    a = next(r for r in rows if r["file"] == "acceptance_a1d977ae.json")
    assert a["report_md"] and a["report_md"].endswith("acceptance_a1d977ae.md")
    assert next(r for r in rows if r["file"] == "acceptance_725b8801.json")["report_md"] is None


def test_legacy_and_drill_identification(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch)
    build_corpus(env)
    rep = _run(mod, env)
    rows = _read_jsonl(Path(rep["corpus_path"]))
    legacy = [r["file"] for r in rows if r["legacy"]]
    drill = [r["file"] for r in rows if r["drill"]]
    assert legacy == ["acceptance_725b8801.json"]
    assert "acceptance_76081eea.json" in drill
    assert len(drill) == 1
    assert rep["stats"]["legacy_count"] == 1
    assert rep["stats"]["drill_count"] == 1
    drill_row = next(r for r in rows if r["file"] == "acceptance_76081eea.json")
    assert drill_row["drill_id"] == "DRILL-20261030-REJECT-01"


def test_same_commit_consistency(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch)
    build_corpus(env)
    rep = _run(mod, env)
    consistency = rep["stats"]["consistency"]
    assert consistency["multi_sheet_groups"] == 2       # shaA(3单) + shaB(2单)
    assert consistency["consistent_groups"] == 1        # shaB
    assert consistency["inconsistent_count"] == 1       # shaA
    bad = consistency["inconsistent"][0]
    assert bad["sha8"] == SHA_A[:8]
    assert bad["group_size"] == 3
    assert bad["verdicts"] == {"PASS": 2, "REJECT": 1}
    assert len(bad["records"]) == 3
    text = Path(rep["report_path"]).read_text(encoding="utf-8")
    assert "同 commit 多单一致性" in text
    assert "不一致" in text


def test_daily_trend_and_reason_tokens(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch)
    build_corpus(env)
    rep = _run(mod, env)
    daily = rep["stats"]["daily"]
    assert daily
    total = sum(row["total"] for row in daily)
    # 可用记录 8 条，其中坏时间 1 条无日期 → 7 条进趋势
    assert total == 7
    assert all(set(("PASS", "REJECT", "OBSERVE", "total")) <= set(row)
               for row in daily)
    tokens = [t["token"] for t in rep["stats"]["reason_top"]]
    assert any("红队通过率" in t for t in tokens)
    text = Path(rep["report_path"]).read_text(encoding="utf-8")
    assert "按日趋势" in text
    assert "关键词 Top" in text


def test_review_cross(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch)
    build_corpus(env)
    rep = _run(mod, env)
    cross = rep["stats"]["cross"]
    assert cross["review_passed_samples"] == 5          # review ① measured "5/10 单"
    # PASS unique commit：shaA, shaB, shaE(legacy) = 3
    assert cross["corpus_pass_unique_commits"] == 3
    assert cross["corpus_pass_sheets"] == 5             # A1/A2/B1/B2/E 共 5 张 PASS 单
    assert cross["diff_review_minus_corpus"] == 2
    assert "note" in cross
    text = Path(rep["report_path"]).read_text(encoding="utf-8")
    assert "与最新 review 六条快照交叉" in text


def test_limit_option(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch)
    build_corpus(env)
    rep = _run(mod, env, limit=2)
    rows = _read_jsonl(Path(rep["corpus_path"]))
    assert len(rows) == 2
    assert rep["exported_rows"] == 2
    assert rep["stats"]["total_records"] == 2
    assert rep["limit"] == 2
    # 全量时更多
    full = _run(mod, env, limit=0)
    assert len(_read_jsonl(Path(full["corpus_path"]))) == 8


def test_json_cli_and_naming(monkeypatch, env, capsys):
    mod = _load()
    _setup(mod, monkeypatch)
    build_corpus(env)
    rc = mod.main(["--json", "--repo-root", str(env["repo"]),
                   "--work-dir", str(env["work"]),
                   "--out-dir", str(env["out"]),
                   "--now", NOW_ISO])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["read_only"] is True
    assert out["total_records"] == 8
    assert out["by_verdict"]["PASS"] == 5
    assert out["by_verdict"]["REJECT"] == 2
    assert out["by_verdict"]["OBSERVE"] == 1
    assert len(out["records"]) == 8
    assert out["corpus_path"].endswith("%s_黄忠验收语料_%s.jsonl" % (DATE, DATE))
    assert out["report_path"].endswith("%s_黄忠语料基准_%s.md" % (DATE, DATE))
    assert Path(out["report_path"]).is_file()
    assert out["warnings"]


def test_bad_inputs_fail_visible(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch)
    build_corpus(env, with_drill_ledger=False, with_review=False)
    rep = _run(mod, env)
    assert rep["ok"] is True
    assert rep["stats"]["drill_count"] == 0                # 无台账 → 演练不可识别
    assert rep["stats"]["drill_index"]["status"] == "missing"
    assert rep["stats"]["cross"]["status"] == "missing"
    warnings = "\n".join(rep["warnings"])
    assert "演练台账缺失" in warnings
    assert "坏验收单" in warnings
    assert "generated_at 不可解析" in warnings
    assert "review 缺失" in warnings
    # 坏文件/坏时间仍可导出其余记录
    assert len(_read_jsonl(Path(rep["corpus_path"]))) == 8


def test_empty_corpus_ok(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch)
    rep = _run(mod, env)
    assert rep["ok"] is True
    assert rep["stats"]["total_records"] == 0
    corpus = Path(rep["corpus_path"])
    assert corpus.is_file()
    assert corpus.read_text(encoding="utf-8") == ""
    assert Path(rep["report_path"]).is_file()
    assert rep["warnings"]


def test_no_write_mode_and_no_work_writes(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch)
    build_corpus(env)
    before = _work_snapshot(env["work"])
    rep = _run(mod, env, write_files=False)
    assert rep["ok"] is True
    assert rep["corpus_path"] is None
    assert rep["report_path"] is None
    assert not env["out"].exists() or not list(env["out"].glob("*"))
    # 写盘模式也不改 work/
    rep2 = _run(mod, env)
    assert rep2["ok"] is True
    assert _work_snapshot(env["work"]) == before


def test_cli_invalid_now_returns_rc2(monkeypatch, env, capsys):
    mod = _load()
    _setup(mod, monkeypatch)
    rc = mod.main(["--json", "--repo-root", str(env["repo"]),
                   "--work-dir", str(env["work"]),
                   "--out-dir", str(env["out"]), "--now", "bad-time"])
    assert rc == 2
    assert "参数错误" in capsys.readouterr().err
