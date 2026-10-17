# -*- coding: utf-8 -*-
"""巡检「首跑未完成」等待条件回归测试（10-17 隔离预演驱动）。

真实故障形态（旧实现 `pending` 条件只判断「完全没有新行」）：

    17:10 run_shadow 开跑 → ~17:14 首行落账(date=周一) → latest 立刻越过
    LAST_V0_DAY → 旧条件 `latest <= LAST_V0_DAY` 为假 → 循环 break →
    17:25 巡检带着 2-3/5 行去断言 `len(todays) == BASKET_N` → 判 FAIL。

即「等待条件与最终断言不同源」：等的是「是否开始」，断言的是「是否完成」。
预演实测 5 篮子全程 27m55s，远大于 15 分钟窗口，故该窗口必被撞上。
"""
import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM / "engine"))

import monday_checkpoint as mc  # noqa: E402

DAY = "2026-10-19"


def _rows(n_done, day=DAY, n_failed=0):
    rows = [{"date": day, "symbol": "60051%d" % i, "status": "done"} for i in range(n_done)]
    rows += [{"date": day, "symbol": "F%03d" % i, "status": "failed"} for i in range(n_failed)]
    return rows


# ---------------------------------------------------------------- 计数

def test_today_done_counts_only_done():
    rows = _rows(2, n_failed=3)
    assert mc._today_done(rows, DAY) == 2


def test_today_done_empty_latest():
    assert mc._today_done([], None) == 0


# ---------------------------------------------------------------- 等待条件

def test_partial_day_must_wait():
    """★核心回归：部分落账（1..4 行）必须继续等待——旧实现此处返回 False。"""
    for n in (1, 2, 3, 4):
        assert mc._needs_wait(_rows(n), DAY, True) is True, "n=%d 应等待" % n


def test_full_day_does_not_wait():
    assert mc._needs_wait(_rows(mc.BASKET_N), DAY, True) is False


def test_failed_rows_do_not_count_as_progress():
    """failed 行不算完成：4 done + 1 failed 仍须等待。"""
    assert mc._needs_wait(_rows(4, n_failed=1), DAY, True) is True


def test_no_new_rows_must_wait():
    assert mc._needs_wait(_rows(0, day=mc.LAST_V0_DAY), mc.LAST_V0_DAY, True) is True


def test_empty_ledger_must_wait():
    assert mc._needs_wait([], None, True) is True


def test_non_monday_never_blocks():
    """时间窗外（非周一）不阻塞，PENDING 中性。"""
    assert mc._needs_wait(_rows(2), DAY, False) is False
    assert mc._needs_wait([], None, False) is False


def test_wait_budget_covers_rehearsal_window():
    """等待预算必须覆盖预演实测全程（28 分钟）减去 17:10→17:25 的 15 分钟。"""
    budget_min = mc.MAX_WAIT_ROUNDS * mc.WAIT_SECONDS / 60.0
    assert budget_min >= 13, "预算 %.1f 分钟不足以覆盖 17:25 后的剩余 %.0f 分钟" % (
        budget_min, 28 - 15)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
