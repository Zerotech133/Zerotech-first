# -*- coding: utf-8 -*-
"""P4 批 6：回测域 Mixin（backtest 页族/pulse/custom 批族）。
Handler 壳经基座多继承并入（禁 import *）。
"""

# 2026-10-30 P71（拆分 B6）：作业/状态辅助迁至 api_backtest_jobs.py，再导出兼容。
from api_backtest_jobs import (  # noqa: F401  再导出（拆分 B6）
    _BT_CUSTOM_BATCHES, _BT_CUSTOM_REPORT, _BT_CUSTOM_STATE, _BT_CUSTOM_LOG, get_backtest,
    _bt_norm_code, _bt_custom_start, _bt_custom_status, _CBT_JOBS_JSONL, _CBT_STATE_JSON,
    _CBT_GATE, _cbt_start, _cbt_status, _bt_resolve, _bt_jev_news,
    _pulse_exit_line, _pulse_fresh, _pulse_ago, build_daily_pulse, _sa_worker,
    _sa_hist_read, _sa_hist_append, SA_HIST, SA_BUSY, SA_JOBS,
    strategies_data, _STRAT_MTIME, STRATEGIES_FILE,
)

# 兼容再导出（B6）：旧调用/测试按模块属性使用这些名字，保留模块级绑定。
import datetime  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
from api_common import _read_json  # noqa: F401


# ── K255 勾选股票自定义批次回测（前端勾选 → 批次 JSONL → 引擎子进程 → 轮询）──










# 领域第二行导航分组（单一数据源）：组名 None = 该领域平铺不折叠。
# pill_nav 按组渲染 details 折叠（当前页所在组自动展开），
# SECTION_PAGES 由它拍平派生——两处永不漂移。
# 2026-10-30 P69（拆分方案 B2）：回测页 HTML builder 迁至 api_backtest_pages_html.py。
from api_backtest_pages_html import (  # noqa: F401  再导出（拆分 B2）
    fut_page_html,
    sellpoint_page_html,
)







# ── K-STOCK-275 组合回测任务队列（paper.html 组合回测器真接线）──────────
# 动线：前端登记 → 本队列真实落盘 →（10-16 开闸后包 C 引擎消费）跑批 →
# 结果回填。开闸前不跑批（验收冻结区拍板，不造假）：任务如实「排队中」，
# 进度条显示队列位置与开闸闸门；引擎侧后续只写 _CBT_STATE_JSON 覆盖状态
# （queued→running(pct/stage)→done(result)/dead），本模块零改动兼容。






# ── 个股全景页辅助（量化中心下钻目标） ──────────────────













# ── 个股分析（极简入口：一个输入框，后台跑 analyze_detail） ──
# ── 策略资产注册表（策略中心/首页组合区/导航单一数据源，K36）──
# 上新三步（详见 /strategies 页 SOP）：strategies.json 加条目 →
# 策略页面挂路由 → 计划任务上线；本表驱动三处 UI，前端零改动。


class Handler:  # Mixin 壳：经基座多继承并入
    _BT_CSS = ("<style>@import url('/static/tokens.css');"
               "body{background:var(--bg-page,#0d1117);color:var(--text,#c9d1d9);"
               "font:14px/1.6 system-ui;margin:0;padding:24px}"
               "h1{font-size:20px;color:var(--link,#58a6ff);margin:0 0 4px}"
               "h2{font-size:15px;color:#3fd6c5;margin:24px 0 8px}"
               "table{border-collapse:collapse;width:100%;margin-top:8px}"
               "td,th{border:1px solid var(--border,#30363d);padding:6px 10px;"
               "font-size:13px;text-align:left}"
               "th{background:var(--bg-raised,#161b22);color:var(--text-dim,#8b949e)}"
               ".card{border:1px solid var(--border,#30363d);border-radius:8px;"
               "padding:12px 16px;background:var(--bg-raised,#161b22);margin-top:8px}"
               ".dim{font-size:11px;color:var(--text-dim,#8b949e)}"
               ".buy{color:var(--color-up,#f85149)}.watch{color:var(--color-warn,#d29922)}"
               ".avoid{color:var(--color-dn,#3fb950)}"
               "a{color:var(--link,#58a6ff);text-decoration:none}"
               ".nav{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 18px}"
               ".nav a,.nav span{display:inline-block;padding:6px 13px;"
               "border:1px solid var(--border,#30363d);border-radius:16px;"
               "color:var(--link,#58a6ff);"
               "text-decoration:none;font-size:12.5px;background:var(--bg-raised,#161b22)}"
               ".nav span{color:var(--text,#e6edf3);border-color:var(--link,#58a6ff)}"
               ".tag{display:inline-block;padding:1px 8px;border-radius:10px;"
               "font-size:11.5px;margin-right:8px}"
               ".t-bull{background:#3a1219;color:var(--color-up,#f85149)}"
               ".t-bear{background:#0f2e1b;color:var(--color-dn,#3fb950)}"
               ".t-neu{background:#2b2f1a;color:var(--color-warn,#d29922)}"
               ".b-long{color:var(--color-up,#f85149);font-weight:600}"
               ".b-flat{color:var(--text-dim,#8b949e)}"
               ".b-short{color:var(--color-dn,#3fb950);font-weight:600}"
               "input{background:var(--bg-page,#0d1117);color:var(--text,#c9d1d9);"
               "border:1px solid var(--border,#30363d);border-radius:8px;"
               "padding:10px 14px;font-size:15px;width:280px}"
               "button{background:#238636;color:#fff;border:none;"
               "border-radius:8px;padding:10px 18px;font-size:15px;"
               "cursor:pointer}"
               "/* 方法论矩阵：8 张卡一字排开，点卡下钻 */"
               ".mgrid{display:grid;grid-template-columns:"
               "repeat(auto-fill,minmax(165px,1fr));gap:8px;margin-top:8px}"
               ".mcard{border:1px solid var(--border,#30363d);border-radius:8px;"
               "padding:10px 12px;background:var(--bg-raised,#161b22);display:block;"
               "color:var(--text,#c9d1d9);text-decoration:none}"
               ".mcard:hover{border-color:var(--link,#58a6ff);background:#1c2a3a}"
               ".mcard .mt{font-size:11px;color:var(--text-dim,#8b949e);margin-bottom:4px}"
               ".mcard .mv{font-size:14px;font-weight:600;line-height:1.3}"
               ".mcard .mu{font-size:11px;color:var(--link,#58a6ff);margin-top:4px}"
               ".mcard.mute{opacity:.55}"
               "</style>")


    def _bt_sectors(self, *args, **kwargs):
        """委托 api_backtest_pages_a（拆分 B3，纯移动）。"""
        from api_backtest_pages_a import _bt_sectors as _impl
        return _impl(self, *args, **kwargs)


    def _bt_report_page(self, *args, **kwargs):
        """委托 api_backtest_pages_a（拆分 B3，纯移动）。"""
        from api_backtest_pages_a import _bt_report_page as _impl
        return _impl(self, *args, **kwargs)


    def _bt_ind_drill_page(self, *args, **kwargs):
        """委托 api_backtest_pages_a（拆分 B3，纯移动）。"""
        from api_backtest_pages_a import _bt_ind_drill_page as _impl
        return _impl(self, *args, **kwargs)


    def _bt_lhb_page(self, *args, **kwargs):
        """委托 api_backtest_pages_a（拆分 B3，纯移动）。"""
        from api_backtest_pages_a import _bt_lhb_page as _impl
        return _impl(self, *args, **kwargs)


    def _bt_seat_page(self, *args, **kwargs):
        """委托 api_backtest_pages_a（拆分 B3，纯移动）。"""
        from api_backtest_pages_a import _bt_seat_page as _impl
        return _impl(self, *args, **kwargs)


    def _bt_stock_page(self, *args, **kwargs):
        """委托 api_backtest_pages_a（拆分 B3，纯移动）。"""
        from api_backtest_pages_a import _bt_stock_page as _impl
        return _impl(self, *args, **kwargs)


    def _bt_stock_form(self, *args, **kwargs):
        """委托 api_backtest_pages_a（拆分 B3，纯移动）。"""
        from api_backtest_pages_a import _bt_stock_form as _impl
        return _impl(self, *args, **kwargs)


    def _bt_jev(self, *args, **kwargs):
        """委托 api_backtest_pages_a（拆分 B3，纯移动）。"""
        from api_backtest_pages_a import _bt_jev as _impl
        return _impl(self, *args, **kwargs)


    def _bt_brief(self, *args, **kwargs):
        """委托 api_backtest_pages_a（拆分 B3，纯移动）。"""
        from api_backtest_pages_a import _bt_brief as _impl
        return _impl(self, *args, **kwargs)


    def _bt_news(self, *args, **kwargs):
        """委托 api_backtest_pages_a（拆分 B3，纯移动）。"""
        from api_backtest_pages_a import _bt_news as _impl
        return _impl(self, *args, **kwargs)
    def _sa_result(self, *args, **kwargs):
        """委托 api_backtest_json_api（拆分 B4/B5，纯移动）。"""
        from api_backtest_json_api import _sa_result as _impl
        return _impl(self, *args, **kwargs)
    def _sa_start(self, *args, **kwargs):
        """委托 api_backtest_json_api（拆分 B4/B5，纯移动）。"""
        from api_backtest_json_api import _sa_start as _impl
        return _impl(self, *args, **kwargs)
    def _weak_cases_page(self, *args, **kwargs):
        """委托 api_backtest_pages_b（拆分 B4/B5，纯移动）。"""
        from api_backtest_pages_b import _weak_cases_page as _impl
        return _impl(self, *args, **kwargs)
    # ── 策略集市 · 多策略共振看板（L1 发现层，全下钻） ──
    def _cross_page(self, *args, **kwargs):
        """委托 api_backtest_pages_b（拆分 B4/B5，纯移动）。"""
        from api_backtest_pages_b import _cross_page as _impl
        return _impl(self, *args, **kwargs)
    # ── L2 策略工作台：九维量化选股（影子评分，纯门户不推送） ──
    def _nine_dim_page(self, *args, **kwargs):
        """委托 api_backtest_pages_b（拆分 B4/B5，纯移动）。"""
        from api_backtest_pages_b import _nine_dim_page as _impl
        return _impl(self, *args, **kwargs)
    def _algo_market_page(self, *args, **kwargs):
        """委托 api_backtest_pages_b（拆分 B4/B5，纯移动）。"""
        from api_backtest_pages_b import _algo_market_page as _impl
        return _impl(self, *args, **kwargs)
    def _algo_activate(self, *args, **kwargs):
        """委托 api_backtest_json_api（拆分 B4/B5，纯移动）。"""
        from api_backtest_json_api import _algo_activate as _impl
        return _impl(self, *args, **kwargs)
    def _fin_page(self, *args, **kwargs):
        """委托 api_backtest_pages_b（拆分 B4/B5，纯移动）。"""
        from api_backtest_pages_b import _fin_page as _impl
        return _impl(self, *args, **kwargs)
    def _strategies_page(self, *args, **kwargs):
        """委托 api_backtest_pages_b（拆分 B4/B5，纯移动）。"""
        from api_backtest_pages_b import _strategies_page as _impl
        return _impl(self, *args, **kwargs)
    def _sa_page(self, *args, **kwargs):
        """委托 api_backtest_pages_b（拆分 B4/B5，纯移动）。"""
        from api_backtest_pages_b import _sa_page as _impl
        return _impl(self, *args, **kwargs)






# 2026-10-30 P68（拆分方案 B1）：minute 框架与 bt2 机制迁至 portal/backtest/。
from api_backtest_minute import (  # noqa: F401  再导出（拆分 B1）
    _CALENDAR_VERSION_PATTERN,
    _BT2_DIR,
    _BT2_STATE_DIR,
    _BT2_LOCK,
    _MINUTE_SOURCE_REGISTRY,
    _MINUTE_SOURCE_FACTORIES,
    _MINUTE_CALENDAR_FACTORIES,
    _MINUTE_FEE_FACTORIES,
    _MINUTE_FILL_MODELS,
    _MINUTE_MARKET_RULE_FACTORIES,
    _MINUTE_MARKET_POLICIES,
    _JOB_SPEC_CATALOG,
    _JOB_SPEC_REGISTRY,
    _calendar_error_code,
    _minute_error_code,
    _descriptor_failure,
    _minute_transaction_policy,
    _validate_minute_order_policy,
    _minute_execution_config,
    _minute_output_root,
    _safe_minute_artifact,
    _calendar_provider_for,
    _validate_runtime_calendar,
    _rehydrate_minute_source,
    _rehydrate_catalog_sources,
    register_minute_calendar_factory,
    register_minute_factory,
    register_minute_source,
)
from api_backtest_bt2 import (  # noqa: F401  再导出（拆分 B1）
    _bt2_state_path,
    _MY_STRAT_FILE,
    _MY_STRAT_KEYS,
    _my_strat_load,
    _my_strat_save,
    _api_my_strategies,
    _bt2_write_state,
    _bt2_start,
    _bt2_worker,
    _bt2_nl,
    _bt2_status,
    _MINUTE_ARTIFACT_MAX_BYTES,
    _read_minute_artifact,
    _minute_artifact_unavailable,
    _valid_minute_payload,
    _bt2_result,
)
