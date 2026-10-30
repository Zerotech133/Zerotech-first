# -*- coding: utf-8 -*-
"""api_backtest.py 契约测试（B0 冻结，2026-10-30 · 关羽）。

目的：在 api_backtest（6,395 行）拆分前，把**对外契约**钉死，拆分期间本文件必须全绿：
  1. Handler mixin 的 20 个方法全部保留（portal_api.Handler 多继承依赖）；
  2. 外部模块显式 import 的符号全部保留且可调用
     （portal_api 批6 12 个 + api_misc 2 个 + api_screen 4 个）；
  3. 文件行数守卫：当前 6,395 行，**只减不增**（B1-B5 每批合并后下调本上限）。

拆分方案：docs/plans/2026-10-30_api_backtest拆分方案_v1.md（B0-B6）。
"""
import ast
import sys
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]
REPO = PORTAL.parent
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

import api_backtest  # noqa: E402

# 拆分前快照（2026-10-30 AST 实测）
EXPECTED_HANDLER_METHODS = [
    "_bt_sectors",
    "_bt_report_page",
    "_bt_ind_drill_page",
    "_bt_lhb_page",
    "_bt_seat_page",
    "_bt_stock_page",
    "_bt_stock_form",
    "_bt_jev",
    "_bt_brief",
    "_bt_news",
    "_sa_result",
    "_sa_start",
    "_weak_cases_page",
    "_cross_page",
    "_nine_dim_page",
    "_algo_market_page",
    "_algo_activate",
    "_fin_page",
    "_strategies_page",
    "_sa_page",
]

# portal_api 批6 显式导入（12）+ api_misc（2）+ api_screen（4）
EXPECTED_TOP_SYMBOLS = [
    "fut_page_html",
    "sellpoint_page_html",
    "_bt_custom_start",
    "_bt_custom_status",
    "_cbt_start",
    "_cbt_status",
    "_bt2_start",
    "_bt2_status",
    "_api_my_strategies",
    "_bt2_result",
    "_bt2_nl",
    "get_backtest",
    "strategies_data",
    "_bt_resolve",
    "_bt_jev_news",
    "build_daily_pulse",
    "_pulse_ago",
]

LINE_CEILING = 4890  # 2026-10-30 B2 完成（5248→4890，页面 builder 迁出）；只减不增


def test_handler_keeps_all_methods():
    """Handler mixin 方法集不得缺失（portal_api 多继承依赖）。"""
    assert hasattr(api_backtest, "Handler"), "api_backtest.Handler 缺失"
    missing = [m for m in EXPECTED_HANDLER_METHODS if not hasattr(api_backtest.Handler, m)]
    assert not missing, "Handler 缺失方法：%s" % missing


def test_handler_methods_callable():
    """20 个方法必须可调用（防拆分/改签时被覆盖成属性）。"""
    bad = [m for m in EXPECTED_HANDLER_METHODS
           if not callable(getattr(api_backtest.Handler, m))]
    assert not bad, "Handler 方法不可调用：%s" % bad


def test_top_level_symbols_kept():
    """外部模块显式 import 的 17 个符号必须保留且可调用。"""
    missing = [f for f in EXPECTED_TOP_SYMBOLS if not hasattr(api_backtest, f)]
    assert not missing, "顶层符号缺失：%s" % missing
    bad = [f for f in EXPECTED_TOP_SYMBOLS
           if not callable(getattr(api_backtest, f))]
    assert not bad, "顶层符号不可调用：%s" % bad


def test_file_line_ceiling():
    """冻结守卫：api_backtest.py 行数不得超过 6,395（拆分期只减不增）。"""
    src = (PORTAL / "api_backtest.py").read_text(encoding="utf-8", errors="ignore")
    n = src.count("\n") + 1
    assert n <= LINE_CEILING, (
        "api_backtest.py %d 行 > 上限 %d（拆分守则：只减不增；"
        "每批合并后下调 LINE_CEILING）" % (n, LINE_CEILING)
    )


def test_handler_methods_are_detected_via_ast():
    """AST 复核：Handler 类内方法数 = 20（防止动态注入后快照失真）。"""
    tree = ast.parse((PORTAL / "api_backtest.py").read_text(encoding="utf-8", errors="ignore"))
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Handler"]
    assert len(classes) == 1, "Handler 类应唯一，实际 %d" % len(classes)
    methods = [x.name for x in classes[0].body
               if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert methods == EXPECTED_HANDLER_METHODS, (
        "Handler 方法集漂移：期望 %s，实际 %s" % (EXPECTED_HANDLER_METHODS, methods)
    )
