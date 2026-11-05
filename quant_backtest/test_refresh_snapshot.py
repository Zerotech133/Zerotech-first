"""refresh_snapshot 的离线单测：边界探测与验收闸门。

背景 bug（2026-11-05 首版）：probe 用 limit=100 判空，却把 probe(0)=100
当成"整表 100 行"，导致 15,593,536 的虚报总量被探成 100 行、77 页被算成
1 页。这类"探测口径与使用口径不一致"的错误必须用测试钉住。
"""

from __future__ import annotations

import refresh_snapshot as R


def _fake_probe(real_rows: int, limit: int = 100):
    """模拟远端：offset >= real_rows 返回空，否则返回 min(limit, 剩余)。"""

    def probe(offset: int) -> int:
        if offset >= real_rows:
            return 0
        return min(limit, real_rows - offset)

    return probe


def test_boundary_ignores_inflated_total():
    """ts_ths_member 实况：total 声称 1559 万，真实 382,731。"""
    b = R.bisect_boundary(_fake_probe(382731), 15593536)
    assert b == 382731


def test_boundary_grows_when_claim_underestimates():
    """claim 低估（表被追加过）时必须扩张，不能停在 claim 上漏拉。"""
    assert R.bisect_boundary(_fake_probe(6000), 5000) == 6000
    assert R.bisect_boundary(_fake_probe(200000), 1000) == 200000


def test_boundary_empty_table():
    assert R.bisect_boundary(lambda off: 0, 999) == 0
    assert R.bisect_boundary(lambda off: 0, 0) == 0


def test_boundary_small_table():
    """整表装得下一页时，边界仍是真实行数而不是探测 limit。"""
    for n in (1, 7, 50, 99):
        assert R.bisect_boundary(_fake_probe(n), 100000) == n


def test_boundary_never_returns_probe_limit():
    """回归：probe(0) 的返回值是 limit，不是总行数（首版 bug）。"""
    probe = _fake_probe(382731, limit=100)
    assert probe(0) == 100
    assert R.bisect_boundary(probe, 15593536) != 100


def test_boundary_exact_multiple():
    """真实行数正好是 limit 整数倍时，边界不应偏大或偏小。"""
    assert R.bisect_boundary(_fake_probe(5000), 10**7) == 5000
    assert R.bisect_boundary(_fake_probe(10000), 10**7) == 10000


def test_verify_accepts_full_pull():
    ok, msg = R.verify_pull(382731, 382731, [54676] * 7)
    assert ok, msg


def test_verify_rejects_short_pull():
    ok, msg = R.verify_pull(1000, 382731, [1000])
    assert not ok and "行数不足" in msg


def test_verify_rejects_empty_part():
    ok, msg = R.verify_pull(382731, 382731, [382731, 0])
    assert not ok and "空分片" in msg


def test_verify_rejects_zero_boundary():
    """边界探测失败时绝不能覆盖正式数据（空表 ≠ 该清空）。"""
    ok, msg = R.verify_pull(0, 0, [])
    assert not ok and "边界" in msg


def test_verify_tolerates_small_drift():
    """对端抖动 0.05% 内应放行（382731 * 0.999 → 382348）。"""
    ok, _ = R.verify_pull(382400, 382731, [382400])
    assert ok
    ok2, _ = R.verify_pull(380000, 382731, [380000])
    assert not ok2


def test_plan_pages_rounds_up():
    assert R.plan_pages(382731) == 77
    assert R.plan_pages(5000) == 1
    assert R.plan_pages(5001) == 2
    assert R.plan_pages(0) == 0
