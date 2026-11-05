# -*- coding: utf-8 -*-
"""JEV-EVALCOV-20261105 单测：月周期口径 + 回填覆盖率守卫（**全 mock，不推送、不碰生产**）。

覆盖：
  1) jev_eval_guard：monthly=35 天 / weekly=3 天 超龄边界（tmp results 注入假结果）
  2) 超龄消息含「上次评测日期 / 期望周期 / 补救命令」
  3) jev_coverage_guard：id 口径覆盖率、basis=lines 退回、门槛判定、
     连续 N 天升级（状态文件）、毕业 JSON 过期、--min-coverage 参数、
     stdout 单行 JSON
  4) push 全部 monkeypatch，不产生真实飞书消息

运行（worktree 根目录）：
  "C:/Program Files/Python311/python.exe" -m pytest jev-eval -q
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

JEV_DIR = Path(__file__).resolve().parents[1]
if str(JEV_DIR) not in sys.path:
    sys.path.insert(0, str(JEV_DIR))

import jev_coverage_guard as cg  # noqa: E402
import jev_eval_guard as eg  # noqa: E402

NOW = datetime(2026, 11, 5, 12, 0, 0)


# ── helpers ─────────────────────────────────────────────────
def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def fresh_grad(tmp: Path, now: datetime = NOW) -> Path:
    p = tmp / cg.GRAD_FILE
    p.write_text(
        json.dumps({"computed_at": now.isoformat(timespec="seconds")}),
        encoding="utf-8",
    )
    return p


def build_data(
    tmp: Path,
    pool_n: int = 10,
    arch_n: int = 4,
    outcome_n: int = 4,
    dup: bool = True,
) -> Path:
    """pool=triples，arch=archive，outcome 取 pool 前缀。dup 时重复写一遍以验去重。"""
    pool = [{"candidate_id": f"cand-{i}", "ts_code": f"T{i}"} for i in range(pool_n)]
    arch = [
        {"candidate_id": f"cand-arch-{i}", "ts_code": f"A{i}"} for i in range(arch_n)
    ]
    outcome = [
        {"candidate_id": f"cand-{i}", "outcome_5d": 0.5} for i in range(outcome_n)
    ]
    if dup:
        outcome = outcome + outcome[:1]
        pool = pool + pool[:2]
    write_jsonl(tmp / "triples.jsonl", pool)
    write_jsonl(tmp / "triples_archive.jsonl", arch)
    write_jsonl(tmp / "triples_outcome.jsonl", outcome)
    return tmp


def fake_result(
    rdir: Path,
    run_at: datetime,
    accuracy: float = 0.99,
    by_type: dict | None = None,
    repeats: int = 3,
    error_rate: float = 0.0,
    total_tokens: int = 30000,
) -> Path:
    rdir.mkdir(parents=True, exist_ok=True)
    p = rdir / f"result-{run_at:%Y%m%d-%H%M%S}.json"
    p.write_text(
        json.dumps(
            {
                "summary": {
                    "run_at": run_at.replace(microsecond=0).isoformat(),
                    "repeats": repeats,
                    "accuracy": accuracy,
                    "self_consistency": 0.95,
                    "error_rate": error_rate,
                    "total_tokens": total_tokens,
                    "latency_ms_median": 500,
                    "by_type_accuracy": (
                        {"noul": 0.99, "choice": 0.99} if by_type is None else by_type
                    ),
                    "high_conf_wrong_ids": [],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return p


# ── eval_guard：月/周阈值 ────────────────────────────────────
def test_max_age_by_cadence():
    assert eg.max_age_days("monthly") == 35
    assert eg.max_age_days("weekly") == 3
    # 默认口径必须是 monthly（需求是月度回归）
    assert eg.DEFAULT_CADENCE == "monthly"
    with pytest.raises(ValueError):
        eg.max_age_days("daily")


def test_guard_monthly_boundary_35_days(tmp_path):
    rdir = tmp_path / "r35"
    fake_result(rdir, NOW - timedelta(days=35))
    ok = eg.check(rdir, "monthly", now=NOW)
    assert ok["age_days"] == 35
    assert ok["max_age"] == 35
    assert ok["code"] == 0, ok["problems"]

    rdir2 = tmp_path / "r36"
    fake_result(rdir2, NOW - timedelta(days=36))
    bad = eg.check(rdir2, "monthly", now=NOW)
    assert bad["age_days"] == 36
    assert bad["code"] == 1
    assert any("36 天" in p and "35" in p for p in bad["problems"])


def test_guard_weekly_boundary_3_days(tmp_path):
    rdir = tmp_path / "w3"
    fake_result(rdir, NOW - timedelta(days=3))
    assert eg.check(rdir, "weekly", now=NOW)["code"] == 0

    rdir2 = tmp_path / "w4"
    fake_result(rdir2, NOW - timedelta(days=4))
    res = eg.check(rdir2, "weekly", now=NOW)
    assert res["code"] == 1
    assert res["max_age"] == 3


def test_guard_monthly_alert_message_fields(tmp_path):
    rdir = tmp_path / "results"
    run_at = NOW - timedelta(days=40)
    fake_result(rdir, run_at)
    res = eg.check(rdir, "monthly", now=NOW)
    msg = res["msg"]
    assert "上次评测 2026-09-26" in msg          # 上次评测日期
    assert "期望周期 月度" in msg                # 期望周期
    assert "补救命令" in msg                     # 补救命令
    assert "2026-09-26" in msg and "≤35 天" in msg


def test_guard_no_results_is_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(eg, "push", lambda text: None)
    rdir = tmp_path / "empty"
    rdir.mkdir()
    res = eg.check(rdir, "monthly", now=NOW)
    assert res["code"] == 1
    assert "无评测结果文件" in res["msg"]
    assert eg.main(["--no-push", "--results-dir", str(rdir)]) == 1


def test_guard_parse_args_defaults_and_forms():
    cfg = eg.parse_args([])
    assert cfg["cadence"] == "monthly" and cfg["no_push"] is False
    assert eg.parse_args(["--cadence", "weekly"])["cadence"] == "weekly"
    assert eg.parse_args(["--cadence=WEEKLY"])["cadence"] == "weekly"
    with pytest.raises(ValueError):
        eg.parse_args(["--cadence", "hourly"])


def test_guard_main_pushes_via_mocked_channel(tmp_path, monkeypatch):
    rdir = tmp_path / "results"
    fake_result(rdir, datetime.now() - timedelta(days=1))
    sent: list[str] = []
    monkeypatch.setattr(eg, "push", sent.append)
    rc = eg.main(["--results-dir", str(rdir)])
    assert rc == 0
    assert len(sent) == 1 and sent[0].startswith("[Jev评测] 月度回归正常")


# ── coverage：口径 ──────────────────────────────────────────
def test_coverage_id_basis_dedup(tmp_path):
    build_data(tmp_path, pool_n=10, arch_n=4, outcome_n=4)
    m = cg.measure(tmp_path)
    assert m["basis"] == "id:candidate_id"
    assert m["denom"] == 14          # 10 + 4，去重后
    assert m["numer"] == 4           # outcome 去重后
    assert m["coverage"] == pytest.approx(4 / 14)
    assert m["files"]["triples.jsonl"]["uniq_ids"] == 10


def test_coverage_basis_lines_fallback(tmp_path):
    # 无 candidate_id / id 字段 → 行数口径
    write_jsonl(tmp_path / "triples.jsonl", [{"ts_code": "A"}] * 4)
    write_jsonl(tmp_path / "triples_archive.jsonl", [{"ts_code": "B"}] * 6)
    write_jsonl(tmp_path / "triples_outcome.jsonl", [{"ts_code": "A"}] * 2)
    m = cg.measure(tmp_path)
    assert m["basis"] == "lines"
    assert m["numer"] == 2 and m["denom"] == 10
    assert m["coverage"] == pytest.approx(0.2)


def test_coverage_empty_denominator(tmp_path):
    (tmp_path / "triples.jsonl").write_text("", encoding="utf-8")
    out = cg.evaluate(
        data_dir=tmp_path,
        grad_file=fresh_grad(tmp_path),
        state_file=tmp_path / "state.json",
        now=NOW,
    )
    rep = out["report"]
    assert rep["coverage"] is None and rep["code"] == 1
    assert any("分母为 0" in p for p in rep["problems"])


# ── coverage：门槛 / 连续天数 ────────────────────────────────
def test_coverage_below_threshold_warns(tmp_path):
    build_data(tmp_path, pool_n=10, arch_n=10, outcome_n=4)  # 4/20 = 20%
    out = cg.evaluate(
        data_dir=tmp_path,
        grad_file=fresh_grad(tmp_path),
        state_file=tmp_path / "state.json",
        now=NOW,
    )
    rep = out["report"]
    assert rep["below"] is True
    assert rep["level"] == "WARN" and rep["code"] == 1
    assert rep["streak_days"] == 1
    assert "门槛 50%" in out["msg"]


def test_coverage_above_threshold_ok(tmp_path):
    build_data(tmp_path, pool_n=10, arch_n=2, outcome_n=8)  # 8/12 = 66.7%
    out = cg.evaluate(
        data_dir=tmp_path,
        grad_file=fresh_grad(tmp_path),
        state_file=tmp_path / "state.json",
        now=NOW,
    )
    rep = out["report"]
    assert rep["coverage"] == pytest.approx(8 / 12, abs=1e-4)
    assert rep["level"] == "OK" and rep["code"] == 0
    assert rep["problems"] == []


def test_streak_escalates_to_critical(tmp_path):
    build_data(tmp_path, pool_n=10, arch_n=10, outcome_n=4)
    sf = tmp_path / "state.json"
    sf.write_text(
        json.dumps(
            {
                "last_date": (NOW.date() - timedelta(days=1)).isoformat(),
                "streak": 2,
                "streak_start": (NOW.date() - timedelta(days=2)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    rep = cg.evaluate(
        data_dir=tmp_path,
        grad_file=fresh_grad(tmp_path),
        state_file=sf,
        now=NOW,
        streak_days=3,
    )["report"]
    assert rep["streak_days"] == 3
    assert rep["persistent"] is True
    assert rep["level"] == "CRITICAL" and rep["code"] == 2
    assert any("连续 3 天" in p for p in rep["problems"])

    # 状态文件已落盘，次日重跑会继续累加
    saved = json.loads(sf.read_text(encoding="utf-8"))
    assert saved["streak"] == 3 and saved["last_date"] == NOW.date().isoformat()


def test_streak_same_day_idempotent(tmp_path):
    build_data(tmp_path, pool_n=10, arch_n=10, outcome_n=4)
    sf = tmp_path / "state.json"
    cg.evaluate(
        data_dir=tmp_path, grad_file=fresh_grad(tmp_path), state_file=sf, now=NOW
    )
    second = cg.evaluate(
        data_dir=tmp_path, grad_file=fresh_grad(tmp_path), state_file=sf,
        now=NOW + timedelta(hours=6),
    )["report"]
    assert second["streak_days"] == 1  # 同日重跑不重复计数


def test_streak_resets_when_recovered(tmp_path):
    build_data(tmp_path, pool_n=10, arch_n=2, outcome_n=8)  # 66.7% 达标
    sf = tmp_path / "state.json"
    sf.write_text(
        json.dumps({"last_date": (NOW.date() - timedelta(days=1)).isoformat(),
                    "streak": 5, "streak_start": "2026-10-30"}),
        encoding="utf-8",
    )
    rep = cg.evaluate(
        data_dir=tmp_path, grad_file=fresh_grad(tmp_path), state_file=sf, now=NOW
    )["report"]
    assert rep["streak_days"] == 0 and rep["level"] == "OK"


def test_streak_gap_resets_to_one(tmp_path):
    build_data(tmp_path, pool_n=10, arch_n=10, outcome_n=4)
    sf = tmp_path / "state.json"
    sf.write_text(
        json.dumps({"last_date": (NOW.date() - timedelta(days=4)).isoformat(),
                    "streak": 5, "streak_start": "2026-10-25"}),
        encoding="utf-8",
    )
    rep = cg.evaluate(
        data_dir=tmp_path, grad_file=fresh_grad(tmp_path), state_file=sf, now=NOW
    )["report"]
    assert rep["streak_days"] == 1 and rep["level"] == "WARN"


# ── coverage：毕业 JSON 新鲜度 ──────────────────────────────
def test_grad_stale_detection(tmp_path):
    build_data(tmp_path, pool_n=10, arch_n=2, outcome_n=8)
    g = fresh_grad(tmp_path)
    old = (NOW - timedelta(days=10)).timestamp()
    os.utime(g, (old, old))
    chk = cg.check_grad(g, NOW)
    assert chk["stale"] is True and chk["age_days"] == 10

    rep = cg.evaluate(
        data_dir=tmp_path, grad_file=g, state_file=tmp_path / "s.json", now=NOW
    )["report"]
    assert rep["grad_stale"] is True
    assert rep["code"] == 1 and rep["level"] == "WARN"
    assert any("毕业状态" in p for p in rep["problems"])


def test_grad_missing_is_warn(tmp_path):
    build_data(tmp_path, pool_n=10, arch_n=2, outcome_n=8)
    rep = cg.evaluate(
        data_dir=tmp_path,
        grad_file=tmp_path / "nope.json",
        state_file=tmp_path / "s.json",
        now=NOW,
    )["report"]
    assert rep["grad_stale"] is True
    assert any("缺失" in p for p in rep["problems"])


# ── coverage：参数 / CLI ────────────────────────────────────
def test_min_coverage_param(tmp_path):
    build_data(tmp_path, pool_n=10, arch_n=10, outcome_n=4)  # 20%
    args = dict(data_dir=tmp_path, grad_file=fresh_grad(tmp_path),
                state_file=tmp_path / "s.json", now=NOW)
    assert cg.evaluate(min_coverage=0.5, **args)["report"]["code"] == 1
    assert cg.evaluate(min_coverage=0.1, **args)["report"]["code"] == 0


def test_parse_args_forms():
    cfg = cg.parse_args([])
    assert cfg["min_coverage"] == 0.5 and cfg["streak_days"] == 3
    cfg = cg.parse_args(["--min-coverage", "0.7", "--streak-days=5"])
    assert cfg["min_coverage"] == 0.7 and cfg["streak_days"] == 5
    with pytest.raises(ValueError):
        cg.parse_args(["--min-coverage", "1.5"])
    with pytest.raises(ValueError):
        cg.parse_args(["--streak-days", "0"])
    with pytest.raises(ValueError):
        cg.parse_args(["--bogus"])


def test_cli_stdout_single_json_line(tmp_path, capsys):
    build_data(tmp_path, pool_n=10, arch_n=10, outcome_n=4)
    rc = cg.main([
        "--no-push",
        "--data-dir", str(tmp_path),
        "--state-file", str(tmp_path / "s.json"),
        "--grad-file", str(fresh_grad(tmp_path)),
    ])
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    rep = json.loads(out[0])
    assert rep["basis"] == "id:candidate_id"
    assert rep["code"] == rc == 1
    assert rep["coverage"] == pytest.approx(0.2)


def test_cli_invalid_min_coverage_exits_2(tmp_path):
    assert cg.main(["--min-coverage", "0"]) == 2


def test_coverage_push_is_mocked(tmp_path, monkeypatch):
    build_data(tmp_path, pool_n=10, arch_n=10, outcome_n=4)
    sent: list[str] = []
    monkeypatch.setattr(cg, "push", lambda text: sent.append(text) or 0)
    rc = cg.main([
        "--data-dir", str(tmp_path),
        "--state-file", str(tmp_path / "s.json"),
        "--grad-file", str(fresh_grad(tmp_path, datetime.now())),
    ])
    assert rc == 1
    assert len(sent) == 1 and "回填覆盖率" in sent[0]


# ── eval_guard：临时跑（repeats<3）不参与基线判定（2026-11-05 修） ──
def test_guard_prefers_formal_run_over_newer_temp(tmp_path):
    """目录里存在更新的 repeats=1 临时跑时，仍按最近正式跑判定，不误报。"""
    rdir = tmp_path / "rmix"
    fake_result(rdir, NOW - timedelta(days=21), accuracy=0.96)
    temp = fake_result(
        rdir, NOW - timedelta(days=1), accuracy=0.333, by_type={"score": 0.333}, repeats=1
    )
    r = eg.check(rdir, "monthly", now=NOW)
    assert r["latest"] == "result-%s.json" % (NOW - timedelta(days=21)).strftime(
        "%Y%m%d-%H%M%S"
    ), r["latest"]
    assert r["code"] == 0, r["problems"]
    # 临时跑会被提及（说明为何忽略），但不得进入准确率判定
    assert "33.3%" not in r["msg"]


def test_guard_temp_only_flags_no_formal_result(tmp_path):
    """只有临时跑 → 明确报「无正式结果」，而不是按 33.3% 判漂移。"""
    rdir = tmp_path / "rtemp"
    fake_result(
        rdir, NOW - timedelta(days=1), accuracy=0.333, by_type={"score": 0.333}, repeats=1
    )
    r = eg.check(rdir, "monthly", now=NOW)
    assert r["code"] == 1
    assert any("无正式评测结果" in p for p in r["problems"]), r["problems"]
    assert "33.3% < 90%" not in r["msg"]


def test_guard_orders_by_run_at_not_filename(tmp_path):
    """文件名时间戳与 run_at 不一致时，以 run_at 为准。"""
    rdir = tmp_path / "rorder"
    # 文件名更晚、run_at 更早（正式跑）
    old = fake_result(rdir, NOW - timedelta(days=20), accuracy=0.95)
    old.rename(rdir / "result-29991231-235959.json")
    # 文件名更早、run_at 更晚（正式跑）
    fake_result(rdir, NOW - timedelta(days=2), accuracy=0.97).rename(
        rdir / "result-20000101-000000.json"
    )
    r = eg.check(rdir, "monthly", now=NOW)
    assert r["age_days"] == 2, r
    assert r["code"] == 0, r["problems"]



# ── eval_guard：接口故障不判漂移（2026-11-05，typesafe 451 实测） ──
def test_guard_infra_failure_not_drift(tmp_path):
    """30 题全 451 → 准确率 0%，但这是接口不可达，不能判「基线漂移」。"""
    rdir = tmp_path / "rinfra"
    fake_result(
        rdir, NOW - timedelta(days=1), accuracy=0.0,
        by_type={"noul": 0.0, "choice": 0.0, "score": 0.0},
        error_rate=1.0, total_tokens=0,
    )
    r = eg.check(rdir, "monthly", now=NOW)
    assert r["code"] == 1
    joined = " ".join(r["problems"])
    assert "评测未能完成" in joined, r["problems"]
    assert "总准确率 0.0% < 90%" not in joined
    assert "noul 题型 0.0% < 95%" not in joined
    # 提示里必须给出定位手段
    assert "--probe-endpoint" in joined


def test_guard_partial_failures_still_judged(tmp_path):
    """失败率不高（10%）时仍按准确率判定 —— 不能因为偶发失败就放过真漂移。"""
    rdir = tmp_path / "rpartial"
    fake_result(
        rdir, NOW - timedelta(days=1), accuracy=0.5,
        by_type={"noul": 0.5, "choice": 0.5}, error_rate=0.1,
    )
    r = eg.check(rdir, "monthly", now=NOW)
    joined = " ".join(r["problems"])
    assert "总准确率 50.0% < 90%" in joined, r["problems"]
    assert "评测未能完成" not in joined
