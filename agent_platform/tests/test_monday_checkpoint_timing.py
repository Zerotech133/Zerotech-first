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
    """等待预算必须覆盖**两种实测速度**里慢的那种。

    · 空载（2026-10-17 预演）：5 票 27m55s → 17:25 后只需再等 13 分钟；
    · 有竞争（2026-10-18 03:29 生产 py311 实测）：首票 latency_s=1101s
      = 18.4 分钟/票（22GB 大模型占显存 → 9b 被挤到 CPU），5 票≈92 分钟
      → 17:25 后要再等约 77 分钟。

    假 FAIL 与真 FAIL 在日志形态上一模一样，是门 3 最难判的一类风险，
    而放宽预算在正常路径上零成本（首轮即 break）——所以按慢的算。
    """
    budget_min = mc.MAX_WAIT_ROUNDS * mc.WAIT_SECONDS / 60.0
    assert budget_min >= 13, "预算 %.1f 分钟不足以覆盖 17:25 后的剩余 %.0f 分钟" % (
        budget_min, 28 - 15)
    assert budget_min >= 75, (
        "预算 %.1f 分钟覆盖不了竞争态（首票实测 18.4 分钟 × 5 票 ≈ 92 分钟，"
        "17:25 后仍需等约 77 分钟）——会假 FAIL" % budget_min)


# ------------------------------------------------- 断言与等待同源（篮子齐备）

def test_basket_complete_normal():
    assert mc._basket_complete(_rows(mc.BASKET_N)) is True


def test_basket_complete_duplicate_row_still_complete():
    """★双跑留下 6 行（5 标的 + 1 重复）不该判 FAIL——与 _needs_wait 同源。
    旧断言 `len(todays) == BASKET_N` 在此处会判 FAIL，而等待循环早已正常
    结束（>= 到量即走），FAIL 文案与真正的失败一模一样。"""
    rows = _rows(mc.BASKET_N)
    rows.append(dict(rows[0]))  # 同一标的重复落账
    assert len(rows) == mc.BASKET_N + 1
    assert mc._basket_complete(rows) is True


def test_basket_complete_missing_symbol_fails():
    """5 行但只覆盖 4 个标的（有重复）＝篮子没齐，必须判 FAIL。
    旧断言按行数 ==5 会误判 PASS。"""
    rows = _rows(mc.BASKET_N - 1)
    rows.append(dict(rows[0]))
    assert len(rows) == mc.BASKET_N
    assert mc._basket_complete(rows) is False


def test_basket_complete_partial_and_empty():
    for n in (0, 1, 4):
        assert mc._basket_complete(_rows(n)) is False, "n=%d 不应判齐备" % n


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
