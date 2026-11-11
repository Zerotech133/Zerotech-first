# -*- coding: utf-8 -*-
"""JEV 盘中降级选票（pick_codes_intraday）门禁 —— 2026-11-09。

背景：tushare 盘中不发布当日日线，load_board 抛 RuntimeError("…无日线数据")。
pick_codes 捕获后自动降级到 pick_codes_intraday（stk_mins成分池 + xtcloud实时分钟
近似涨跌幅选前排），让 jev_mins_fetch/jev_social_fetch 盘中也能选票，避免盘后
一次性大批量（老田 2026-11-09 纠偏）。

门禁锁死（全部 mock 外部源，离线稳定，不碰 xtcloud/datamart/8091）：
1. 降级分支触发：load_board 抛"无日线数据"时，pick_codes 必须走盘中降级且不抛错。
2. 选票口径：涨停必须入选、平/跌必须排除、焦点=涨停票所在行业。
3. 原盘后路径（load_board 成功）不受影响——由 test_jev_kg/drill 等覆盖。
"""
from __future__ import annotations
import os
import sys
import unittest.mock as mock
from pathlib import Path

PORTAL_DIR = Path(__file__).resolve().parents[1]
TOOLS_DIR = PORTAL_DIR.parent / "tools"
for d in (str(PORTAL_DIR), str(TOOLS_DIR)):
    if d not in sys.path:
        sys.path.insert(0, d)

import jev_mins_fetch as J                # noqa: E402
sys.path.insert(0, os.path.join(J.STATION, "portal"))
import jev_graph                         # noqa: E402 —— 预加载，使 mock.patch 能 resolve
import glob as _glob
real_glob = _glob.glob

# 仅拦截 stk_mins 目录的 glob，daily/stock_basic 走真实本地文件（非网络源）
CODES_FILES = ['000001_SZ_202511.parquet', '600519_SH_202511.parquet', '300750_SZ_202511.parquet']
CODE_C6_CLOSE = {'000001.SZ': 100.0, '600519.SH': 50.0, '300750.SZ': 10.0}
SB_ROWS = [('000001.SZ', '平安银行', '银行'),
           ('600519.SH', '贵州茅台', '白酒'),
           ('300750.SZ', '宁德时代', '电池')]


def _fake_glob(pattern):
    if 'stk_mins' in pattern:
        return CODES_FILES
    return real_glob(pattern)


def _fake_mins(code6, day, tbl):
    close = CODE_C6_CLOSE[code6 + ('.SH' if code6.startswith(('600', '300')) else '.SZ')]
    return [['X', '2026-11-09 09:30:00', 1, 1, 1, 1, 1, 1],
            ['X', '2026-11-09 13:50:00', close, close, close, close, 1, 1]]


class _FakeSB:
    columns = ['ts_code', 'name', 'industry']

    def __init__(self, rows):
        self._r = rows

    def itertuples(self):
        for r in self._r:
            yield type('R', (), {'ts_code': r[0], 'name': r[1], 'industry': r[2]})()


def test_intraday_pick_logic():
    """盘中降级：无日线→走 pick_codes_intraday；涨停入选/平跌排除/焦点正确。"""
    with mock.patch('glob.glob', side_effect=_fake_glob), \
         mock.patch('xtcloud_client.client') as mxc, \
         mock.patch('jev_graph.load_board',
                    side_effect=RuntimeError("20261109 无日线数据")), \
         mock.patch('jev_graph._dm_read_retry', return_value=_FakeSB(SB_ROWS)), \
         mock.patch('jev_graph.limit_state',
                    side_effect=lambda c, h, pc, c6, nm, pct: (pct >= 9.5, False, None, None)):
        client = mock.MagicMock()
        client.mins.side_effect = lambda c6, day, tbl: _fake_mins(c6, day, tbl)
        mxc.return_value = client
        picks, focus = J.pick_codes('20261109', 60)
        assert picks, "盘中降级应至少选中1只"
        assert picks[0][0] == '000001.SZ' and picks[0][3] == '涨停', picks
        assert len(picks) == 1, "平/跌票不应入选, got %d" % len(picks)
        assert focus == '银行', "焦点应为涨停票所在行业, got %s" % focus


def test_intraday_fallback_to_intraday_only_on_no_daily():
    """只有『无日线数据』才降级；其他 RuntimeError 必须上抛（不静默吞掉）。"""
    with mock.patch('glob.glob', side_effect=_fake_glob), \
         mock.patch('xtcloud_client.client') as mxc, \
         mock.patch('jev_graph.load_board',
                    side_effect=RuntimeError("其它运行时错误")), \
         mock.patch('jev_graph._dm_read_retry', return_value=_FakeSB(SB_ROWS)), \
         mock.patch('jev_graph.limit_state',
                    side_effect=lambda c, h, pc, c6, nm, pct: (pct >= 9.5, False, None, None)):
        client = mock.MagicMock()
        client.mins.side_effect = lambda c6, day, tbl: _fake_mins(c6, day, tbl)
        mxc.return_value = client
        try:
            J.pick_codes('20261109', 60)
            raise AssertionError("非『无日线』的 RuntimeError 应上抛")
        except RuntimeError as e:
            assert "无日线" not in str(e), "只有无日线才降级"
