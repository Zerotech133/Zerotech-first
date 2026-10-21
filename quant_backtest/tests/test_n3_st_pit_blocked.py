"""N3 ST 公告 PIT 化：is_st_pit_blocked 逐事件阻塞判定反例（先红后绿，合成注入 intervals）。

隔离原则：仅测试纯函数 is_st_pit_blocked，通过注入 intervals 绕过真实
ts_namechange.parquet 依赖；真实数据加载已由 a3_st_pit_diff_probe.py 在测量阶段
实证（见 reports/a3_st_pit_diff_report.json）。本文件固化「逐事件 PIT 不回写未来」
的契约，供 BACK1/STRAT1 签字采纳后在引擎侧接入的回归护栏。
"""
import os
import sys
from pathlib import Path

QBT = Path(__file__).resolve().parents[1]
if str(QBT) not in sys.path:
    sys.path.insert(0, str(QBT))

from market_scan import is_st_pit_blocked  # noqa: E402


def _iv(*pairs):
    """构造 {code: [(public_start, end), ...]} 注入式 intervals。"""
    return {"X": list(pairs)}


def test_pit_keeps_pre_st_event_no_lookahead():
    """核心反例：事件早于 ST 公开起点 → 不阻塞（杜绝用未来 ST 回写删除过去信号）。"""
    iv = _iv(("20260110", None))  # 2026-01-10 起 ST，至今
    assert is_st_pit_blocked("X", "20251231", intervals=iv) is False
    assert is_st_pit_blocked("X", "20260109", intervals=iv) is False


def test_pit_blocks_active_st_event():
    """ST 生效中（public_start<=day 且 end 未到）→ 阻塞。"""
    iv = _iv(("20260110", None))
    assert is_st_pit_blocked("X", "20260110", intervals=iv) is True  # 边界当日即剔
    assert is_st_pit_blocked("X", "20260201", intervals=iv) is True


def test_pit_blocks_closed_interval():
    """有 end 的区间：区间内阻塞，结束后放行；end 当日不剔（end>day 才剔）。"""
    iv = _iv(("20240101", "20240601"))
    assert is_st_pit_blocked("X", "20240501", intervals=iv) is True
    assert is_st_pit_blocked("X", "20240601", intervals=iv) is False
    assert is_st_pit_blocked("X", "20240701", intervals=iv) is False


def test_pit_unknown_code_never_blocked():
    """不在 ST 区间的票永不阻塞。"""
    iv = _iv(("20260110", None))
    assert is_st_pit_blocked("Y", "20260201", intervals=iv) is False


def test_p1_max_start_ann_defensive():
    """P1 防御性：public_start=max(start,ann) 严于 raw start；即便 raw start<=day，
    若 pub(=ann) > day，仍不阻塞（仅释放 [start, ann) 窄窗，不回写未来）。"""
    iv = _iv(("20260110", None))  # 注入 pub=20260110（模拟 ann 晚于 start）
    assert is_st_pit_blocked("X", "20260105", intervals=iv) is False  # 落在 [start, ann) 被释放
    assert is_st_pit_blocked("X", "20260115", intervals=iv) is True   # 越过 pub 正常阻塞


def test_day_string_and_int_accepted():
    """day 接受 str/int 同义（事件缓存 day 为 str，函数内部归一）。"""
    iv = _iv(("20260110", None))
    assert is_st_pit_blocked("X", "20260201", intervals=iv) is True
    assert is_st_pit_blocked("X", 20260201, intervals=iv) is True


def test_real_loader_integration():
    """集成护栏：真实 ts_namechange 可加载且函数不抛；缺失则跳过重真实依赖。"""
    try:
        import pytest
    except ImportError:
        pytest = None
    nc_fp = QBT / "data" / "tdx" / "ts_namechange.parquet"
    if not nc_fp.exists():
        if pytest is not None:
            pytest.skip("ts_namechange.parquet 缺失，跳过真实加载集成测试")
        return
    import market_scan as ms
    ms._ST_PIT_INTERVALS_CACHE = None
    iv = ms._load_st_pit_intervals()
    assert isinstance(iv, dict)
    res = ms.is_st_pit_blocked("000004.SZ", "20230101")
    assert isinstance(res, bool)
