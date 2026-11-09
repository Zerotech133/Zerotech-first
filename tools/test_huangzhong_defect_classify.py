# -*- coding: utf-8 -*-
"""黄忠缺陷分类与周报单测（tmp 夹具，只读数据源，零网络）。

覆盖：taxonomy v1 全类映射 + 优先级 + unknown；演练/自然分离（台账与 drill 标记）；
按日聚合/窗口过滤；按 commit 去重；坏 json 容错；md 周报渲染；CLI --json/--days/
--out-dir；参数与环境错误。
"""
import datetime as dt
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import huangzhong_defect_classify as dc  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 10, 31, 18, 0, 0, tzinfo=TZ)


# ------------------------------------------------------------------- 夹具

def _work(tmp_path: Path) -> Path:
    work = tmp_path / "work" / "huangzhong"
    (work / "drills").mkdir(parents=True)
    return work


def _sheet(work: Path, name: str, verdict: str, reasons, ts="2026-10-30T10:00:00+08:00",
           sha="abcdef1234567890", drill=None, mode="observe"):
    data = {
        "ok": True,
        "generated_at": ts,
        "mode": mode,
        "blocking": False,
        "commit": {"ok": True, "sha": sha},
        "verdict": verdict,
        "reasons": reasons,
        "rollback_point": "git revert %s" % sha[:12],
    }
    if drill is not None:
        data["drill"] = drill
    (work / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def _drill_ledger(work: Path, entries: list):
    target = work / "drills" / "drill_ledger_20261030.json"
    target.write_text(json.dumps({"schema": "huangzhong_drill_ledger/v1",
                                  "drills": entries}, ensure_ascii=False),
                      encoding="utf-8")


def _report(tmp_path: Path, work: Path, **kw):
    kw.setdefault("out_dir", tmp_path / "deliverables")
    kw.setdefault("now", NOW)
    return dc.build_report(work_dir=work, **kw)


# --------------------------------------------------------- taxonomy 映射

def test_taxonomy_each_class():
    cases = {
        "红队证据超龄 59.0 h > 上限 48.0 h（generated_at=2026-10-27T15:02:25+08:00）":
            "evidence_stale",
        "红队报告缺失/不可读（missing）": "evidence_missing",
        "基线失败：failed=3 error=0": "baseline_failed",
        "红队通过率 50% < 80%": "redteam_low",
        "无回滚点，rollback 不可用": "rollback_missing",
        "schema 字段非法，json 解析失败": "schema_invalid",
        "违反观察模式口径（blocking=true）": "mode_violation",
        "完全未知的原因描述": "unknown",
    }
    for text, expected in cases.items():
        got = dc.classify_reason(text)
        assert got["category"] == expected, (text, got)
        assert isinstance(got["matched_keywords"], list)


def test_taxonomy_priority_stale_beats_redteam():
    # 同时命中「红队」与「超龄」：按优先级归 evidence_stale，但命中词留档
    got = dc.classify_reason("红队证据超龄 58.1 h > 上限 48.0 h")
    assert got["category"] == "evidence_stale"
    assert "超龄" in got["matched_keywords"]


def test_classify_reasons_tolerates_odd_input():
    assert dc.classify_reasons(None) == []
    assert dc.classify_reasons("单条原因")[0]["category"] == "unknown"
    out = dc.classify_reasons([{"不是": "字符串"}, ""])
    assert len(out) == 2 and out[0]["category"] == "unknown"


# ------------------------------------------------------------ 扫描与分离

def test_report_counts_natural_and_drill(tmp_path):
    work = _work(tmp_path)
    _sheet(work, "acceptance_aaaaaaaa.json", "REJECT",
           ["基线失败：failed=3 error=0"], sha="aaaaaaaa11111111")
    _sheet(work, "acceptance_bbbbbbbb.json", "OBSERVE",
           ["红队证据超龄 59.0 h > 上限 48.0 h"], sha="bbbbbbbb22222222")
    _drill_ledger(work, [{"acceptance_report": "work/huangzhong/acceptance_bbbbbbbb.json",
                          "target_commit": "bbbbbbbb22222222", "kind": "OBSERVE"}])
    rep = _report(tmp_path, work)
    assert rep["ok"] is True
    assert rep["nonpass_sheets_in_window"] == 2
    assert rep["natural"]["sheets"] == 1 and rep["drill"]["sheets"] == 1
    assert rep["natural"]["by_category"].get("baseline_failed") == 1
    assert rep["drill"]["by_category"].get("evidence_stale") == 1
    assert rep["combined"]["by_category"].get("redteam_low") is None


def test_drill_flag_on_sheet_without_ledger(tmp_path):
    work = _work(tmp_path)
    _sheet(work, "acceptance_cccccccc.json", "REJECT",
           ["红队通过率 50% < 80%"], drill=True, sha="cccccccc33333333")
    rep = _report(tmp_path, work)
    assert rep["drill"]["sheets"] == 1 and rep["natural"]["sheets"] == 0

    work2 = _work(tmp_path / "noledger")
    _sheet(work2, "acceptance_dddddddd.json", "REJECT",
           ["红队通过率 50% < 80%"], sha="dddddddd44444444")
    rep2 = _report(tmp_path / "noledger", work2)
    assert rep2["drill"]["sheets"] == 0 and rep2["natural"]["sheets"] == 1
    assert any("drill 台账缺失" in w for w in rep2["warnings"])


def test_natural_zero_drill_two_matches_expected_smoke(tmp_path):
    work = _work(tmp_path)
    _sheet(work, "acceptance_76081eea.json", "REJECT",
           ["红队通过率 50% < 80%",
            "红队证据超龄 59.0 h > 上限 48.0 h"], sha="76081eeaa7dcf512")
    _sheet(work, "acceptance_02317909.json", "OBSERVE",
           ["红队证据超龄 58.1 h > 上限 48.0 h"], sha="02317909bee62c1c")
    _drill_ledger(work, [
        {"acceptance_report": "acceptance_76081eea.json",
         "target_commit": "76081eeaa7dcf512", "verdict": "REJECT"},
        {"acceptance_report": "acceptance_02317909.json",
         "target_commit": "02317909bee62c1c", "verdict": "OBSERVE"},
    ])
    rep = _report(tmp_path, work)
    assert rep["natural"]["sheets"] == 0
    assert rep["drill"]["sheets"] == 2
    assert rep["combined"]["verdicts"] == {"REJECT": 1, "OBSERVE": 1}
    # 红队通过率 → redteam_low；红队证据超龄 → evidence_stale（优先级）
    cats = rep["combined"]["by_category"]
    assert cats.get("redteam_low") == 1
    assert cats.get("evidence_stale") == 2
    assert rep["combined"]["ratios"]["evidence_stale"] == round(2 / 3, 4)


def test_pass_sheets_ignored(tmp_path):
    work = _work(tmp_path)
    _sheet(work, "acceptance_pass1.json", "PASS", [])
    _sheet(work, "acceptance_pass2.json", "PASS", ["无关原因"])
    rep = _report(tmp_path, work)
    assert rep["nonpass_sheets_in_window"] == 0
    assert rep["combined"]["sheets"] == 0


# ------------------------------------------------------- 窗口 / 趋势 / commit

def test_days_window_filter(tmp_path):
    work = _work(tmp_path)
    _sheet(work, "acceptance_new.json", "REJECT", ["基线失败：failed=1 error=0"],
           ts="2026-10-30T10:00:00+08:00")
    _sheet(work, "acceptance_old.json", "REJECT", ["基线失败：failed=2 error=0"],
           ts="2026-09-01T10:00:00+08:00")
    rep = _report(tmp_path, work, days=7)
    assert rep["nonpass_sheets_in_window"] == 1
    assert rep["natural"]["sheets"] == 1
    rep30 = _report(tmp_path, work, days=90)
    assert rep30["nonpass_sheets_in_window"] == 2


def test_by_day_trend(tmp_path):
    work = _work(tmp_path)
    _sheet(work, "acceptance_d1.json", "REJECT", ["基线失败：failed=1 error=0"],
           ts="2026-10-29T10:00:00+08:00", sha="1111111111111111")
    _sheet(work, "acceptance_d2.json", "OBSERVE", ["红队证据超龄 50 h > 上限 48 h"],
           ts="2026-10-30T10:00:00+08:00", sha="2222222222222222")
    rep = _report(tmp_path, work, days=3)
    days = {row["date"]: row for row in rep["by_day"]}
    assert days["2026-10-29"]["natural"] == 1
    assert days["2026-10-30"]["natural"] == 1
    assert days["2026-10-31"]["natural"] == 0
    assert days["2026-10-30"]["verdicts"]["OBSERVE"] == 1
    # 窗口起点 10-28 18:00 → 趋势覆盖 10-28..10-31 共 4 个自然日（含 0 值日）
    assert len(days) == 4
    assert days["2026-10-28"]["natural"] == 0


def test_by_commit_dedup(tmp_path):
    work = _work(tmp_path)
    sha = "abcdef1234567890"
    _sheet(work, "acceptance_a1.json", "REJECT", ["基线失败：failed=1 error=0"],
           sha=sha, ts="2026-10-29T10:00:00+08:00")
    _sheet(work, "acceptance_a2.json", "OBSERVE", ["红队证据超龄 50 h > 上限 48 h"],
           sha=sha, ts="2026-10-30T10:00:00+08:00")
    rep = _report(tmp_path, work)
    assert len(rep["by_commit"]) == 1
    row = rep["by_commit"][0]
    assert row["sha8"] == sha[:8] and row["sheets"] == 2
    assert row["verdicts"] == {"REJECT": 1, "OBSERVE": 1}


# ------------------------------------------------------------- 坏数据容错

def test_bad_json_tolerated_and_counted(tmp_path):
    work = _work(tmp_path)
    _sheet(work, "acceptance_good.json", "REJECT", ["基线失败：failed=1 error=0"])
    (work / "acceptance_broken.json").write_text("{not json", encoding="utf-8")
    (work / "acceptance_list.json").write_text("[1,2,3]", encoding="utf-8")
    rep = _report(tmp_path, work)
    assert rep["ok"] is True
    assert rep["bad_files"] == 2
    assert rep["natural"]["sheets"] == 1
    assert {b["file"] for b in rep["bad_file_list"]} == {
        "acceptance_broken.json", "acceptance_list.json"}


def test_bad_generated_at_counted(tmp_path):
    work = _work(tmp_path)
    _sheet(work, "acceptance_badts.json", "REJECT", ["基线失败：failed=1 error=0"],
           ts="不是时间")
    _sheet(work, "acceptance_okts.json", "REJECT", ["基线失败：failed=1 error=0"],
           ts="2026-10-30T10:00:00+08:00")
    rep = _report(tmp_path, work)
    assert len(rep["bad_generated_at"]) == 1
    assert rep["nonpass_sheets_in_window"] == 1


def test_reasons_missing_or_odd_tolerated(tmp_path):
    work = _work(tmp_path)
    (work / "acceptance_noreason.json").write_text(json.dumps({
        "generated_at": "2026-10-30T10:00:00+08:00", "verdict": "OBSERVE",
        "commit": {"sha": "99999999aaaa"}}, ensure_ascii=False), encoding="utf-8")
    (work / "acceptance_oddreason.json").write_text(json.dumps({
        "generated_at": "2026-10-30T11:00:00+08:00", "verdict": "REJECT",
        "reasons": [123, {"k": "v"}], "commit": {"sha": "88888888bbbb"}},
        ensure_ascii=False), encoding="utf-8")
    rep = _report(tmp_path, work)
    assert rep["combined"]["sheets"] == 2
    # 非字符串 reason 不丢弃（fail-visible）：记为 unknown，避免静默少统计
    assert rep["combined"]["reasons"] == 2
    assert rep["combined"]["by_category"] == {"unknown": 2}


# ------------------------------------------------------------------ md 周报

def test_md_report_written(tmp_path):
    work = _work(tmp_path)
    _sheet(work, "acceptance_x1.json", "REJECT",
           ["红队通过率 50% < 80%", "红队证据超龄 59 h > 上限 48 h"])
    _drill_ledger(work, [])
    rep = _report(tmp_path, work)
    assert rep["ok"] is True
    path = Path(rep["report_md"])
    assert path.exists()
    assert path.parent == (tmp_path / "deliverables")
    assert path.name.startswith("2026-10-31_黄忠缺陷分类周报_")
    text = path.read_text(encoding="utf-8")
    for section in ("# 黄忠缺陷分类周报", "## 一、总览", "## 二、按缺陷类别",
                    "## 三、按日趋势", "## 四、按 commit 去重",
                    "## 五、最近样本", "## 六、数据质量", "## 七、口径与边界"):
        assert section in text, section
    assert "redteam_low" in text and "evidence_stale" in text


def test_recent_samples_limit(tmp_path):
    work = _work(tmp_path)
    for i in range(5):
        _sheet(work, "acceptance_s%d.json" % i, "REJECT",
               ["基线失败：failed=1 error=0"],
               ts="2026-10-2%dT10:00:00+08:00" % (5 + i),
               sha=("%08d" % i) + "aabbccdd")
    rep = _report(tmp_path, work, limit=3)
    assert len(rep["recent_samples"]) == 3
    stamps = [s["generated_at"] for s in rep["recent_samples"]]
    assert stamps == sorted(stamps, reverse=True)


# -------------------------------------------------------------------- 错误

def test_missing_work_dir_fail_visible(tmp_path):
    rep = dc.build_report(work_dir=tmp_path / "nope", out_dir=tmp_path / "d", now=NOW)
    assert rep["ok"] is False and rep["error"] == "work_dir_unreadable"


def test_invalid_days_and_limit(tmp_path):
    work = _work(tmp_path)
    assert dc.build_report(work_dir=work, days=0, out_dir=tmp_path / "d",
                           now=NOW)["error"] == "invalid_days"
    assert dc.build_report(work_dir=work, limit=0, out_dir=tmp_path / "d",
                           now=NOW)["error"] == "invalid_limit"


# -------------------------------------------------------------------- CLI

def test_cli_json_and_md(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(dc, "now_local", lambda: NOW)
    work = _work(tmp_path)
    _sheet(work, "acceptance_cli.json", "REJECT", ["红队通过率 50% < 80%"])
    out = tmp_path / "out"
    code = dc.main(["--days", "7", "--json", "--work-dir", str(work),
                    "--out-dir", str(out)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["window_days"] == 7
    assert payload["natural"]["by_category"]["redteam_low"] == 1
    assert Path(payload["report_md"]).exists()


def test_cli_human_output(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(dc, "now_local", lambda: NOW)
    work = _work(tmp_path)
    _sheet(work, "acceptance_cli2.json", "OBSERVE", ["红队证据超龄 50 h > 上限 48 h"])
    code = dc.main(["--work-dir", str(work), "--out-dir", str(tmp_path / "out")])
    assert code == 0
    text = capsys.readouterr().out
    assert "缺陷分类周报" in text and "evidence_stale" in text


def test_cli_invalid_days_exit_2(tmp_path, capsys):
    work = _work(tmp_path)
    code = dc.main(["--days", "0", "--json", "--work-dir", str(work),
                    "--out-dir", str(tmp_path / "out")])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["error"] == "invalid_days"


def test_cli_missing_work_dir_exit_2(tmp_path, capsys):
    code = dc.main(["--json", "--work-dir", str(tmp_path / "nope"),
                    "--out-dir", str(tmp_path / "out")])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "work_dir_unreadable"
