# -*- coding: utf-8 -*-
"""portal_api GET 精确路由表（B4a-lite，2026-10-31 关羽）。

由 B4a 生成规则产出；仅收录“单语句直接调 self._handler()”的分支（32 条/16 段），
保持原 if/elif 顺序语义；等价测试见 tools/tests/test_route_table_equivalence.py。

⚠ 契约（2026-10-31 P0 修复，WO-INTEL-PRIVATE-20261031）：
表值 = **Handler 方法名字符串**，不是可调用对象。dispatch 必须统一走
`getattr(self, table[path])()`；直接 `table[path]()` 会得到
`TypeError: 'str' object is not callable` → 502（/intel/stream 等 32 条全中）。
本模块提供 iter_routes()/unresolved() 供契约测试校验“每个值都能解析为
portal_api.Handler 上的可调用方法”，防止表值写错或方法改名后静默 404/500。
"""

_GET_EXACT_1 = {
    '/api/health': '_api_health',
    '/api/jev-stack': '_api_jev_stack',
}

_GET_EXACT_2 = {
    '/api/reception': '_api_reception',
    '/api/manager_roster': '_api_manager_roster',
}

_GET_EXACT_3 = {
    '/kg/flow': '_kg_flow_page',
    '/kg/label': '_kg_label_page',
    '/kg/card': '_kg_card_page',
    '/kg/canvas': '_kg_canvas_page',
    '/kg/canvasv2': '_kg_canvas_v2_page',
    '/kg/ask': '_kg_ask_page',
    '/kg/battle': '_kg_battle_page',
}

_GET_EXACT_4 = {
    '/kg/lexicon': '_kg_lexicon_page',
}

_GET_EXACT_5 = {
    '/fin': '_fin_page',
    '/strategies': '_strategies_page',
}

_GET_EXACT_6 = {
    '/intel/stream': '_intel_page',
    '/gzh': '_gzh_list',
}

_GET_EXACT_7 = {
    '/vd': '_vd_list',
}

_GET_EXACT_8 = {
    '/bt-sectors': '_bt_sectors',
    '/bt-jev': '_bt_jev',
    '/bt-brief': '_bt_brief',
    '/sys-health': '_sys_health',
}

_GET_EXACT_9 = {
    '/audit-ai': '_audit_ai_page',
}

_GET_EXACT_10 = {
    '/devops': '_devops_page',
}

_GET_EXACT_11 = {
    '/org': '_org_page',
}

_GET_EXACT_12 = {
    '/aim': '_aim_page',
}

_GET_EXACT_13 = {
    '/bt-chanlun': '_chanlun_page',
}

_GET_EXACT_14 = {
    '/chanlun-anno-report': '_chanlun_anno_report',
}

_GET_EXACT_15 = {
    '/bt-weak': '_weak_cases_page',
    '/bt-9dim': '_nine_dim_page',
    '/bt-cross': '_cross_page',
}

_GET_EXACT_16 = {
    '/screen-sig': '_screen_sig_page',
    '/bt-data-health': '_data_health_page',
}

# 16 段顺序即原 if/elif 链顺序（组内/组间匹配互不重叠；保持顺序供审计）
_TABLES = (_GET_EXACT_1, _GET_EXACT_2, _GET_EXACT_3, _GET_EXACT_4, _GET_EXACT_5,
           _GET_EXACT_6, _GET_EXACT_7, _GET_EXACT_8, _GET_EXACT_9, _GET_EXACT_10,
           _GET_EXACT_11, _GET_EXACT_12, _GET_EXACT_13, _GET_EXACT_14,
           _GET_EXACT_15, _GET_EXACT_16,)

GET_EXACT = {}
for _d in _TABLES:
    GET_EXACT.update(_d)


def iter_routes():
    """遍历全部精确路由：(route, handler_method_name)，供契约测试与审计。"""
    for table in _TABLES:
        for route, handler in table.items():
            yield route, handler


def unresolved(handler_cls):
    """契约校验：返回表值无法解析为 handler_cls 可调用方法的 (route, name) 列表。"""
    bad = []
    for route, handler in iter_routes():
        if not callable(getattr(handler_cls, handler, None)):
            bad.append((route, handler))
    return bad
