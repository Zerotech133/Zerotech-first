"""
N3 引擎接入步集成测试（WO-QT-20261021-N3 · ④ 引擎接入）。

验证：calc_chanlun_signals_market 新增 pit_st 参数后——
- 默认（pit_st=False）不触 PIT，生产行为不变；
- pit_st=True 走逐事件 PIT 阻塞，删除数 = 真实基线报告（a3_st_pit_diff_report.json）的 388；
- hist_st=True（legacy 整窗）删除数 = 929；
- 二者差值 = 541（前视误删释放），即 PIT 严格少于 legacy；
- 组合模式 pit 生效 + legacy 对照计数正确，signal_source 含 PIT 标记。

注意：本测试使用真实事件缓存（~28K 事件）与真实 ts_namechange 区间，属离线集成，
不写库、不触生产、不修改任何缓存文件。golden 数字来自 reports/a3_st_pit_diff_report.json。
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backtest_engine_v2 as eng  # noqa: E402

D0, D1 = "20231010", "20261013"

# 来自 reports/a3_st_pit_diff_report.json 的真实基线（golden，数据不变则稳定）
LEGACY_DROP = 929
PIT_DROP = 388
FORELOOK_RELEASE = 541


def test_legacy_hist_st_drop_baseline():
    _, _, info, err = eng.calc_chanlun_signals_market(D0, D1, hist_st=True, pit_st=False)
    assert err is None, err
    assert info["hist_st"]["on"] is True
    assert info["hist_st"]["n_events_drop"] == LEGACY_DROP, info["hist_st"]["n_events_drop"]


def test_pit_st_blocks_fewer_events():
    _, _, info, err = eng.calc_chanlun_signals_market(D0, D1, hist_st=False, pit_st=True)
    assert err is None, err
    assert info["pit_st"]["on"] is True
    assert info["pit_st"]["n_events_blocked"] == PIT_DROP, info["pit_st"]["n_events_blocked"]
    # PIT 严格少于 legacy（释放前视误删）
    assert info["pit_st"]["n_events_blocked"] < LEGACY_DROP
    assert LEGACY_DROP - info["pit_st"]["n_events_blocked"] == FORELOOK_RELEASE


def test_combined_pit_plus_legacy_control():
    _, _, info, err = eng.calc_chanlun_signals_market(D0, D1, hist_st=True, pit_st=True)
    assert err is None, err
    assert info["pit_st"]["n_events_blocked"] == PIT_DROP
    assert info["pit_st"]["hist_st_legacy_drop"] == LEGACY_DROP
    assert "PIT_ST阻塞" in info["signal_source"]


def test_default_no_st_filter_no_crash():
    sb, ss, info, err = eng.calc_chanlun_signals_market(D0, D1)
    assert err is None, err
    assert info["hist_st"]["on"] is False
    assert info["pit_st"]["on"] is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
