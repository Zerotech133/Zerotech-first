# -*- coding: utf-8 -*-
"""night_batch 无推进退避重试 + 2026-10-25 EXEC-R13 超时改造回归。

背景：原逻辑"连续 2 轮无推进即退出"。2026-10-25 09:21 实测，一次瞬时故障
（JEV 云侧空返 + 与 M3 抢 ollama 锁）就让 2 万条积压停摆 1 小时（09:21–10:40）。
改为线性退避重试后，瞬时故障只会让本轮慢下来，不会让整条长跑退出。

EXEC-R13 追加：单实例锁遗留判定、JEV 外层宽限、雪球自适应条数、软预算收口、
连续 rc=124 告警、旧版实例日志探测——全部为纯函数/可单测，不启动批处理。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import night_batch  # noqa: E402
import gen_health_card as health_card  # noqa: E402


def test_retry_until_limit_then_give_up():
    assert night_batch.stall_decision(1, 3, 300) == (True, 300.0)
    assert night_batch.stall_decision(2, 3, 300) == (True, 600.0)
    keep, wait = night_batch.stall_decision(3, 3, 300)
    assert keep is False and wait == 0.0


def test_zero_retries_disables_retry():
    assert night_batch.stall_decision(1, 0, 300) == (False, 0.0)


def test_backoff_scales_with_count_and_base():
    assert night_batch.stall_decision(1, 5, 60) == (True, 60.0)
    assert night_batch.stall_decision(3, 5, 60) == (True, 180.0)


def test_negative_backoff_clamped_to_zero():
    keep, wait = night_batch.stall_decision(1, 3, -10)
    assert keep is True and wait == 0.0


# --- 2026-10-25 EXEC-R13：超时改造回归 -------------------------------------
def test_hard_timeout_jev_adds_group_grace():
    assert (night_batch.hard_timeout_jev(1500)
            == 1500 + night_batch.JEV_GROUP_WORST_S)
    assert night_batch.hard_timeout_jev(0) == 3600.0


def test_next_xq_limit_fits_soft_budget_with_margin():
    # 实测 9.5s/条、软预算 1500s → 预算内 85% 拟合 ≈134 条（旧 600 条必撞 1800s）
    assert night_batch.next_xq_limit(100, 950.0, 1500.0, 600) == 134
    # 显式 cap（--batch-xq）优先
    assert night_batch.next_xq_limit(1000, 100.0, 1500.0, 150) == 150
    # 无进度/无耗时 → 下限，不再拿大批撞超时
    assert (night_batch.next_xq_limit(0, 0.0, 1500.0, 600)
            == night_batch.XQ_MIN_LIMIT)


def test_timeout_alert_due_at_two_consecutive():
    assert night_batch.timeout_alert_due(0) is False
    assert night_batch.timeout_alert_due(1) is False
    assert night_batch.timeout_alert_due(2) is True


def test_log_looks_live_detects_start_without_end():
    assert night_batch.log_looks_live("[x] START a\n[x] ROUND 1\n") is True
    assert night_batch.log_looks_live("[x] START a\n[x] END 1\n") is False
    assert night_batch.log_looks_live("") is False


def test_lock_is_stale_by_mtime(tmp_path):
    lock = tmp_path / "night_batch.lock"
    lock.write_text("{}", encoding="utf-8")
    meta = {"pid": os.getpid()}          # 活进程（自己）→ 只看 mtime
    assert (night_batch.lock_is_stale(meta, lock, now=time.time(),
                                      stale_s=7200) is False)
    old = time.time() - 9999
    os.utime(lock, (old, old))
    assert night_batch.lock_is_stale(meta, lock, now=time.time(),
                                     stale_s=7200) is True


def test_run_soft_stops_child_at_soft_budget():
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    rc, _tail, elapsed, stop_kind = night_batch._run_soft(
        cmd, night_batch.ROOT, soft_budget_s=1.5, hard_timeout_s=20)
    assert stop_kind == "soft-stop"
    assert elapsed < 15


def test_run_soft_hard_kill_when_soft_never_reached():
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    _rc, _tail, _elapsed, stop_kind = night_batch._run_soft(
        cmd, night_batch.ROOT, soft_budget_s=100, hard_timeout_s=1.5)
    assert stop_kind == "hard-kill"


def test_batch_backlog_parser_streak_and_unfinished_round(tmp_path):
    """ops.batch_backlog 解析：连续 rc=124 计数 + 末轮未收口不吞掉前面的超时。"""
    log = tmp_path / "night_batch.log"
    lines = [
        "[2026-10-25 10:00:00] START 收工时间=10-25 22:00 | 起始积压 JEV=100 雪球=200",
        "[2026-10-25 10:00:00] ROUND 1 | 剩余 JEV=100 雪球=200",
        "[2026-10-25 10:30:00]   JEV rc=124 1620s TIMEOUT(1620s)",
        "[2026-10-25 11:00:00]   XQ  rc=124 1800s TIMEOUT(1800s)",
        "[2026-10-25 11:00:01] ROUND 2 | 剩余 JEV=90 雪球=180",
        "[2026-10-25 11:30:00]   JEV rc=0 277s BACKFILL judged=27/600",
        "[2026-10-25 12:00:00]   XQ  rc=0 52s round end rc=0",
        "[2026-10-25 12:00:01] ROUND 3 | 剩余 JEV=80 雪球=160",
        "[2026-10-25 12:30:00]   JEV rc=124 1620s TIMEOUT(1620s)",
        "[2026-10-25 13:00:00]   XQ  rc=124 1800s TIMEOUT(1800s)",
        "[2026-10-25 13:00:01] ROUND 4 | 剩余 JEV=70 雪球=150",
    ]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    bl = health_card.collect_batch_backlog(str(log))
    assert bl["available"] is True
    assert bl["jev_backlog"] == 70 and bl["xq_backlog"] == 150
    assert bl["timeout_streak"] == 1          # R3 是连续超时第 1 轮（R2 已收口打断）
    assert bl["unfinished_last_round"] is True
    assert bl["jev_delta"] == 10              # 80 - 70 = 10（正=下降）
    level, detail = health_card._batch_backlog_level(bl)
    assert level in ("red", "yellow")
    assert "未收口" in detail


def test_pause_m3_flag_parsed_default_off():
    """2026-10-26 OS-FIX-DAYBATCH：--pause-m3 默认关，显式传参才生效。"""
    ap = night_batch.build_parser()
    assert ap.parse_args([]).pause_m3 is False
    assert ap.parse_args(["--pause-m3"]).pause_m3 is True
