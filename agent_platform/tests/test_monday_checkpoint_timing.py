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


class _SinkTime:
    """只提供 sleep 的假 time —— **只替换 monday_checkpoint 模块内的 time 引用**。

    为什么不能用 `monkeypatch.setattr(mc.time, "sleep", ...)`：`mc.time` 就是
    全局 time 模块本身，那样会把 time.sleep 全局改掉。本仓 test_run_shadow_guard
    故意留下常驻僵尸线程 `_DeadloopAgent.propose = while True: sleep(0.05)`
    （daemon，存活到进程退出，其 docstring 已论证这是超时语义所需），该线程的
    sleep 一旦变空转就会向本用例的 sleeps 列表灌入千万级调用——实测 98,030,663
    次，`assert len(sleeps) == 3` 假红，且空转抢 GIL 让整个 pytest 跑到 SIGTERM。
    替换 mc.time（模块属性）则真 time 不受影响，僵尸线程照常 sleep。

    monday_checkpoint 内 time 的唯一用法是 time.sleep(WAIT_SECONDS)（:327），
    故只实现 sleep 即可。
    """

    def __init__(self, sink):
        self._sink = sink

    def sleep(self, seconds):
        self._sink.append(seconds)


def _rows(n_done, day=DAY, n_failed=0):
    rows = [
        {"date": day, "symbol": "60051%d" % i, "status": "done"} for i in range(n_done)
    ]
    rows += [
        {"date": day, "symbol": "F%03d" % i, "status": "failed"}
        for i in range(n_failed)
    ]
    return rows


# ---------------------------------------------------------------- 计数


def test_today_counts_splits_done_and_failed():
    rows = _rows(2, n_failed=3)
    assert mc._today_counts(rows, DAY) == (2, 3)


def test_today_counts_empty_latest():
    assert mc._today_counts([], None) == (0, 0)


def test_today_counts_ignores_other_days():
    rows = _rows(5, day="2026-10-14") + _rows(2, n_failed=1)
    assert mc._today_counts(rows, DAY) == (2, 1)


# ---------------------------------------------------------------- 等待条件


def test_partial_day_must_wait():
    """★核心回归：部分落账（1..4 行）必须继续等待——v1 实现此处返回 False。"""
    for n in (1, 2, 3, 4):
        assert mc._needs_wait(_rows(n), DAY, True) is True, "n=%d 应等待" % n


def test_full_day_does_not_wait():
    assert mc._needs_wait(_rows(mc.BASKET_N), DAY, True) is False


def test_batch_landed_with_failure_does_not_wait():
    """4 done + 1 failed = 这批已跑完，不能继续等。

    run_shadow 每票恰好写一行、失败也在两次尝试后落 failed 行（run_shadow.py
    :338），所以「已落地 5 行」= 「这批结束」。v2 实现按 done<5 判断，此处会
    白等满上限（最长 180 分钟）才判 FAIL——FAIL 越晚，人工介入窗口越小。
    """
    assert mc._needs_wait(_rows(4, n_failed=1), DAY, True) is False


def test_all_failed_day_does_not_wait():
    """全失败的一天：done 恒为 0，但整批已落地，必须立刻判定而不是等满上限。"""
    assert mc._needs_wait(_rows(0, n_failed=5), DAY, True) is False


def test_partial_landing_with_failures_still_waits():
    """1 failed + 4 未落地：这批还在跑，必须继续等（failed 不能推动提前结束）。"""
    assert mc._needs_wait(_rows(0, n_failed=1), DAY, True) is True
    assert mc._needs_wait(_rows(3, n_failed=1), DAY, True) is True


def test_no_new_rows_must_wait():
    assert mc._needs_wait(_rows(0, day=mc.LAST_V0_DAY), mc.LAST_V0_DAY, True) is True


def test_empty_ledger_must_wait():
    assert mc._needs_wait([], None, True) is True


def test_non_monday_never_blocks():
    """时间窗外（非周一）不阻塞，PENDING 中性。"""
    assert mc._needs_wait(_rows(2), DAY, False) is False
    assert mc._needs_wait([], None, False) is False


def test_wait_budget_covers_measured_worst_case():
    """等待预算必须覆盖**实测最慢**，而不是最乐观。

    三次实测（同代码、同机器，只有负载不同）：
    · 空载 2026-10-17（venv py3.13）：5 票 27m55s = 5.6 分钟/票；
    · 竞争 2026-10-18 03:29（生产 py311，22GB 大模型占显存 → 9b 被挤到 CPU）：
      5 票 4116s = 68.6 分钟，单票 latency_s = 1101 / 2283 / 227 / 253 / 237s
      ——**双峰**，最慢一票 38 分钟。
    5 票都按最慢一票跑 = 190 分钟。

    假 FAIL 与真 FAIL 在日志形态上一模一样，是门 3 最难判的一类风险；
    而放宽预算在正常路径上零成本（首轮即 break）——所以按慢的算。
    """
    budget_min = mc.MAX_WAIT_ROUNDS * mc.WAIT_SECONDS / 60.0
    assert budget_min >= 13, "预算 %.1f 分钟不足以覆盖 17:25 后的剩余 %.0f 分钟" % (
        budget_min,
        28 - 15,
    )
    assert budget_min >= 68.6, (
        "预算 %.1f 分钟连**实测**的竞争态全程（68.6 分钟）都不够——必假 FAIL"
        % budget_min
    )
    # 实测最慢单票 2283s=38.05 分钟；预算应能容纳 4 张以上这样的票，
    # 而不是只够「按最慢票跑 2 张」。
    worst_ticket_min = 2283 / 60.0
    assert budget_min / worst_ticket_min >= 4, (
        "预算只够 %.1f 张实测最慢票（%.0f 分钟/票）——上限对竞争态没有意义"
        % (budget_min / worst_ticket_min, worst_ticket_min)
    )


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


# --------------------------------------------- 集成：真跑 main() 的等待行为


def test_main_does_not_sleep_when_batch_landed_with_failure(
    tmp_path, monkeypatch, capsys
):
    """★集成回归：4 done + 1 failed 时 main() 必须**立刻**判定，不进等待循环。

    只测谓词不够——上一版 bug 就出在 main() 的循环与断言之间（等待条件与断言
    上界不同源）。这里真跑 main()，把 sleep 换成记录器：任何一次 sleep 都说明
    巡检又去「等一个永远不会再落账的日子」。
    """
    import datetime as _dt
    import json as _json

    rows = _rows(4, n_failed=1)
    ledger = tmp_path / "shadow_ledger.jsonl"
    ledger.write_text(
        "".join(_json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )

    class _Monday(_dt.date):
        @classmethod
        def today(cls):
            return _dt.date(2026, 10, 19)

    sleeps = []
    monkeypatch.setattr(mc, "LEDGER", ledger)
    monkeypatch.setattr(mc, "PERF", tmp_path / "perf.jsonl")
    monkeypatch.setattr(mc, "LOG", tmp_path / "cp.log")
    monkeypatch.setattr(mc, "_LOG_HANDLE", None)
    monkeypatch.setattr(mc, "date", _Monday)
    monkeypatch.setattr(mc, "time", _SinkTime(sleeps))

    mc.main()
    mc._close_log()
    out = capsys.readouterr().out

    assert sleeps == [], "整批已落地仍在等待（sleep %d 次）——FAIL 会被推迟到上限" % len(
        sleeps
    )
    assert "done 行数=4（预期 5）" in out
    assert "当日 failed 1 票" in out, (
        "FAIL 文案必须点明有 failed 票，否则会被误读成「还没跑完」"
    )


def test_main_waits_then_stops_at_ceiling(tmp_path, monkeypatch, capsys):
    """整批**未**落地时必须真的进等待循环，并到上限停下（不能死等）。

    这是「等待」的另一半契约：上面那个用例证明「已落地就不等」，这里证明
    「未落地会等且必定终止」。两半缺一，周一要么假 FAIL、要么任务永不返回。
    """
    import datetime as _dt
    import json as _json

    rows = _rows(2)  # 只有 2 票落地，剩 3 票还在跑
    ledger = tmp_path / "shadow_ledger.jsonl"
    ledger.write_text(
        "".join(_json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )

    class _Monday(_dt.date):
        @classmethod
        def today(cls):
            return _dt.date(2026, 10, 19)

    sleeps = []
    monkeypatch.setattr(mc, "LEDGER", ledger)
    monkeypatch.setattr(mc, "PERF", tmp_path / "perf.jsonl")
    monkeypatch.setattr(mc, "LOG", tmp_path / "cp.log")
    monkeypatch.setattr(mc, "_LOG_HANDLE", None)
    monkeypatch.setattr(mc, "date", _Monday)
    monkeypatch.setattr(mc, "MAX_WAIT_ROUNDS", 3)
    monkeypatch.setattr(mc, "time", _SinkTime(sleeps))

    mc.main()
    mc._close_log()
    out = capsys.readouterr().out

    assert len(sleeps) == 3, "应等满 3 轮后停止，实际 %d 轮" % len(sleeps)
    assert "已达等待上限 3 轮" in out
    assert "当日 done 2 / failed 0（共 5 票）" in out, (
        "等待日志必须显示进度，人才分得清「在推进」与「卡死」"
    )
    assert "done 行数=2（预期 5）" in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
