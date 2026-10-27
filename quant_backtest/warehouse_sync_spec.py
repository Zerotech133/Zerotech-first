"""Tushare 原增量范围的唯一声明；仅标准库，不触发配置、I/O 或采集。"""

import calendar


IDX_CORE = [
    "000001.SH",
    "000016.SH",
    "000300.SH",
    "000905.SH",
    "000852.SH",
    "000688.SH",
    "399001.SZ",
    "399006.SZ",
    "399106.SZ",
    "899050.BJ",
]
ETF_CORE = [
    "510300.SH",
    "510050.SH",
    "510500.SH",
    "512100.SH",
    "588000.SH",
    "159915.SZ",
    "159949.SZ",
    "512880.SH",
    "512690.SH",
    "515790.SH",
    "512480.SH",
    "516160.SH",
    "515030.SH",
    "512010.SH",
    "512800.SH",
    "510880.SH",
    "513100.SH",
    "513050.SH",
    "518880.SH",
    "159920.SZ",
]
TRADE_CAL_PARAMS = {"exchange": "SSE", "start_date": "19910101", "end_date": "20301231"}
TRADE_CAL_CAP = 120000
SHALLOW_COMMON = (
    ("stock_basic", "stock_basic", {"list_status": "L"}, 10000),
    ("stock_company", "stock_company", {}, 10000),
    ("cb_basic", "cb_basic", {}, 2000),
    ("hk_basic", "hk_basic", {}, 3000),
    ("npr", "npr", {}, 500),
    ("shibor_lpr", "shibor_lpr", {}, 2000),
    (
        "shibor",
        "shibor",
        {},
        5000,
    ),  # 2026-10-26 SHIBOR：日频拆借利率（LPR 上游停更后接棒）
    ("sf_month", "sf_month", {}, 2000),
)
SHALLOW_US = ("us_basic", "us_basic", {}, 6000)
SHALLOW_CLASSIFY = (
    "sw_classify_l1",
    "index_classify",
    {"src": "SW2021", "lv": "L1"},
    5000,
)
RECENT_TABLES = (
    ("news", "news", 1500),
    ("major_news", "major_news", 1500),
    ("research_report", "research_report", 1000),
)
RECENT_MAX_OFF = 6000
RECENT_DAY_COUNT = 7
DATE_TABLES = (
    ("anns_d", "ann_date", 6000, 120000),
    ("top_list", "trade_date", 1000, None),
)
CODE_DAILY = (
    ("cb_daily", "cb_basic", 2000, ("all", "cn")),
    ("bak_basic", "stock_basic", 7000, ("all", "cn")),
    ("us_daily", "us_basic", 8000, ("all", "us")),
)
# table、码单来源、分页上限、半月、日终请求边界、执行市场。
MINUTE_TABLES = (
    ("idx_mins", "IDX_CORE", 8000, False, "15:00:00", ("all", "cn")),
    ("etf_mins", "ETF_CORE", 8000, False, "15:00:00", ("all", "cn")),
    ("sw_mins", "sw_classify_l1", 5000, True, "15:00:00", ("all", "cn")),
    (
        "stk_mins",
        "ts_index_weight/index_weight:000906.SH",
        8000,
        False,
        "15:00:00",
        ("all", "cn"),
    ),
    ("hk_mins", "hk_hold", 8000, False, "16:00:00", ("all", "hk")),
)
INDEX_WEIGHT_PARAMS = {"index_code": "000906.SH"}
INDEX_WEIGHT_CAP = 7000
US_FINA_TABLES = ("us_income", "us_balancesheet", "us_cashflow")
US_FINA_SYNC = False


def shallow_tables(market):
    return (
        SHALLOW_COMMON
        + ((SHALLOW_US,) if market in ("all", "us") else ())
        + (SHALLOW_CLASSIFY,)
    )


def minute_entry(table, codes):
    """原采集器消费同一声明，保留原码单对象和执行顺序。"""
    item = next(item for item in MINUTE_TABLES if item[0] == table)
    return (table, codes, item[2], item[3], item[4])


def month_pair(today):
    """原逻辑的上月和当月，窗口按整月而非截至今天。"""
    result = []
    for offset in (1, 0):
        month, year = today.month - offset, today.year
        if month < 1:
            year, month = year - 1, 12
        last = calendar.monthrange(year, month)[1]
        result.append(
            (
                "%04d%02d" % (year, month),
                "%04d%02d01" % (year, month),
                "%04d%02d%02d" % (year, month, last),
            )
        )
    return result


def recent_periods(today):
    result = []
    for offset in (0, 1, 2):
        quarter, year = (today.month - 1) // 3 + 1 - offset, today.year
        if quarter <= 0:
            quarter, year = quarter + 4, year - 1
        result.append("%dQ%d" % (year, quarter))
    return result


def month_half_windows(ym):
    year, month = int(ym[:4]), int(ym[4:])
    last = calendar.monthrange(year, month)[1]
    return [(ym + "01", ym + "15"), (ym + "16", ym + "%02d" % last)]


def minute_request_windows(windows, *, half, end_h):
    result = []
    for ym, start, end in windows:
        pairs = month_half_windows(ym) if half else [(start, end)]
        result.extend([[s + " 09:00:00", e + " " + end_h] for s, e in pairs])
    return result
