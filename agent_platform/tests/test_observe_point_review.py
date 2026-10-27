# -*- coding: utf-8 -*-
"""observe_point_review.py 单测（K-ORG 预约 2026-10-18 19:1x 机械化执行件）。

覆盖：三件首跑日志的 GREEN/RED/PENDING 三态判定路径 + 汇总退出码。
时间全部经 _now() monkeypatch 固定，日志读经 SHADOW monkeypatch 指向
tmp_path——真仓零读写。gate3 经 subprocess.run mock（不真跑）。
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import observe_point_review as opr  # noqa: E402

DAY = opr.TODAY


def _ts(hour, minute, second=0, days_ago=0):
    """按 **DAY（= opr.TODAY，模块级可 rebind 的今日）** 构造 datetime。

    WO-TF-04 §三.6 修既存日历漂移失败：原夹具把 mtime 写死 2026-10-19，
    而 judge_chief 判据是「mtime 的**日期**必须 == TODAY 且小时 ≥17」，
    TODAY 取运行当天 ⇒ 跨日即恒红（既存 2 failed：test_chief_green_with_send_ok
    / test_main_all_green_exit0）。改夹具为**相对 DAY 动态生成**，
    **不放宽判据**（judge_chief 判据本身零改动）。

    days_ago>0 用于构造「陈旧 mtime」场景（须严格早于 DAY）。
    """
    base = datetime.strptime(DAY, "%Y-%m-%d") - timedelta(days=days_ago)
    return base.replace(hour=hour, minute=minute, second=second)


def _reset(monkeypatch, tmp_path, hour=18):
    monkeypatch.setattr(opr, "SHADOW", str(tmp_path))
    monkeypatch.setattr(opr, "_now", lambda: _ts(hour, 0, 0))
    monkeypatch.setattr(opr, "TODAY", DAY)
    opr.results.clear()


def _state(name):
    return next((s for n, s, _ in opr.results if n.startswith(name)), None)


# ---------- 1) last_run.log ----------


def test_last_run_green(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    (tmp_path / "last_run.log").write_text(
        "===== shadow run start %s 17:10:44 =====\n"
        "[done] 600519 HOLD abc\n"
        "shadow day %s done: {'done': 5}\n" % (DAY, DAY),
        encoding="utf-8",
    )
    opr.judge_last_run()
    assert _state("1)") == "GREEN"


def test_last_run_red_half_done(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    (tmp_path / "last_run.log").write_text(
        "===== shadow run start %s 17:10:44 =====\n[done] 600519 HOLD abc\n" % DAY,
        encoding="utf-8",
    )
    opr.judge_last_run()
    assert _state("1)") == "RED"


def test_last_run_pending_before_1710(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path, hour=10)
    (tmp_path / "last_run.log").write_text("", encoding="utf-8")
    opr.judge_last_run()
    assert _state("1)") == "PENDING"


def test_last_run_red_missing_after_1710(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path, hour=18)
    (tmp_path / "last_run.log").write_text("", encoding="utf-8")
    opr.judge_last_run()
    assert _state("1)") == "RED"


# ---------- 2) monday_checkpoint.log ----------

CKPT_OK = (
    "========================================================\n"
    "周一观察点巡检 %s 17:25:0x\n"
    "========================================================\n"
    "[PASS] 影子首跑已发生\n"
    "结论: 7/7 PASS——观察点门全过\n"
    "EXIT=0\n"
)


def test_checkpoint_green(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    (tmp_path / "monday_checkpoint.log").write_text(
        CKPT_OK % "2026-10-12" + CKPT_OK % DAY, encoding="utf-8"
    )
    opr.judge_checkpoint()
    assert _state("2)") == "GREEN"


def test_checkpoint_red_natural_round_failed(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    bad = (
        (CKPT_OK % DAY)
        .replace("EXIT=0", "EXIT=1")
        .replace("7/7 PASS", "6/7 PASS\n[FAIL] 影子首跑未发生")
    )
    (tmp_path / "monday_checkpoint.log").write_text(bad, encoding="utf-8")
    opr.judge_checkpoint()
    assert _state("2)") == "RED"


def test_checkpoint_pending_no_natural_round(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path, hour=10)
    (tmp_path / "monday_checkpoint.log").write_text(
        CKPT_OK % "2026-10-12", encoding="utf-8"
    )
    opr.judge_checkpoint()
    assert _state("2)") == "PENDING"


# ---------- 3) chief_last_run.log ----------


def test_chief_green_with_send_ok(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    p = tmp_path / "chief_last_run.log"
    p.write_text("a0_chief push ...\n[alert] SEND_OK\n", encoding="utf-8")
    t = _ts(17, 35, 10).timestamp()
    os.utime(p, (t, t))
    opr.judge_chief()
    assert _state("3)") == "GREEN"


def test_chief_red_send_fail(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    p = tmp_path / "chief_last_run.log"
    p.write_text("a0_chief push ...\n[alert] SEND_FAIL\n", encoding="utf-8")
    t = _ts(17, 35, 10).timestamp()
    os.utime(p, (t, t))
    opr.judge_chief()
    assert _state("3)") == "RED"


def test_chief_pending_missing(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path, hour=10)
    opr.judge_chief()
    assert _state("3)") == "PENDING"


def test_chief_red_stale_mtime(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    p = tmp_path / "chief_last_run.log"
    p.write_text("old run\nSEND_OK\n", encoding="utf-8")
    # 陈旧 mtime：必须是**早于 DAY** 的日期（判据：mtime 日期 != TODAY ⇒ RED）
    t = _ts(17, 35, 0, days_ago=8).timestamp()
    os.utime(p, (t, t))
    opr.judge_chief()
    assert _state("3)") == "RED"


# ---------- 4) gate3 + main 汇总 ----------


def _fake_gate3(monkeypatch, rc):
    class R:
        returncode = rc
        stdout = "复核结论: %s\n退出码 %d\n" % (
            "PASS 7/7" if rc == 0 else "FAIL 6/7",
            rc,
        )

    monkeypatch.setattr(opr.subprocess, "run", lambda *a, **k: R())


def test_main_all_green_exit0(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    (tmp_path / "last_run.log").write_text(
        "===== shadow run start %s 17:10:44 =====\n"
        "shadow day %s done: {'done': 5}\n" % (DAY, DAY),
        encoding="utf-8",
    )
    (tmp_path / "monday_checkpoint.log").write_text(CKPT_OK % DAY, encoding="utf-8")
    p = tmp_path / "chief_last_run.log"
    p.write_text("SEND_OK\n", encoding="utf-8")
    t = _ts(17, 35, 0).timestamp()
    os.utime(p, (t, t))
    _fake_gate3(monkeypatch, 0)
    assert opr.main() == 0


def test_main_red_exit1(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    _fake_gate3(monkeypatch, 0)
    assert opr.main() == 1  # 三件均缺且 _now=18 点 → 3 红


def test_main_pending_exit2(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path, hour=10)
    _fake_gate3(monkeypatch, 1)
    assert opr.main() == 2


def test_gate3_timeout_not_silent(monkeypatch, tmp_path):
    """P2-4：gate3 超时显式落判定，不静默、不崩溃（17 点后=RED）。"""
    _reset(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise opr.subprocess.TimeoutExpired(cmd="gate3", timeout=300)

    monkeypatch.setattr(opr.subprocess, "run", boom)
    opr.judge_gate3()
    assert _state("4)") == "RED"
