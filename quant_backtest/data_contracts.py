# -*- coding: utf-8 -*-
"""数据契约表 —— 「每张表应该在哪一天」的单一事实源。

存在理由（2026-11-12）
----------------------
tdx_sector_daily 自 2026-10-22 17:00 起上游停止投喂，三周无人发现。
四道防线全都没报错，因为没有一道的判据是「数据有没有往前走」：

  · 上游 xtcloud   : 安静地不发了，_fetched_at 停在 10-22 17:00
  · TdxTailSync    : 每天 1 秒跑完、0 行新增、rc=0        —— 假绿
  · strong_scan    : rc=3 正确拦截，但没人读那个 3        —— 真红沉默
  · AIColAudit     : 查列存在性，183 表只登记 18 张       —— 查错维度
  · 下游 T1 共振层 : 恒空三周，页面照常渲染、看不出缺层

判据口径（硬约束，改之前先读上面这段）
--------------------------------------
1. 只用「表内业务日期 asof」与「上游投喂时间 _fetched_at」。
2. **禁用 rc，禁用 mtime**。增量任务每天都会写文件，mtime 天天是新的，
   而数据可以一动不动 —— 账本已经踩过一次「mtime 冒充数据日」。
3. 落后天数一律按【交易日】算。按自然日算的话，每个周一整张表都会报红，
   报警第一天就失去可信度。
4. 期望值只是「容差」，不是「承诺」。标 review="auto" 的是按表族推定的，
   红的时候要先怀疑期望值本身，再怀疑数据。
"""
from __future__ import annotations

import datetime as _dt

# ── 交易日历表（体检工具自身的依赖，必须自己也受检）────────────────────
CAL_TABLE = "ts_trade_cal"
CAL_EXCHANGE = "SSE"
# 收盘后多久算「当天数据该到了」。A 股 15:00 收盘，日线同步 16:47 起
CLOSE_READY_HHMM = 1730

# ── 同步任务 → 表清单（抄自 2026-11-12 TdxSync_A/B/C/D 的 --tables 参数）──
# 任务定义改了这里要同步改；改了不会静默失效 —— 体检会报 owner=unknown。
TASK_TABLES = {
    "TdxSync_A_高频": (
        "ts_daily,ts_daily_basic,ts_index_daily,ts_daily_info,ts_moneyflow,"
        "ts_margin,ts_margin_detail,ts_limit_cpt_list,ts_limit_step,ts_stk_limit,"
        "ts_stk_auction_c,ts_cyq_perf,ts_hk_daily,ts_fx_daily,ts_fut_daily,"
        "ts_opt_daily,ts_repo_daily,ts_sge_daily"
    ),
    "TdxSync_B_财务": (
        "ts_income,ts_income_vip,ts_balancesheet,ts_balancesheet_vip,ts_cashflow,"
        "ts_cashflow_vip,ts_fina_indicator,ts_fina_indicator_vip,ts_fina_mainbz,"
        "ts_forecast,ts_forecast_vip,ts_express,ts_express_vip,ts_dividend,"
        "ts_dividend_vip,ts_disclosure_date,ts_fina_audit,ts_stk_holdernumber"
    ),
    "TdxSync_C_低频": (
        "ts_stock_basic,ts_hk_basic,ts_index_basic,ts_index_classify,"
        "ts_index_member_all,ts_index_monthly,ts_index_weekly,ts_index_weight,"
        "ts_kpl_concept,ts_ths_member,ts_tdx_index,ts_trade_cal,ts_suspend_d,"
        "ts_namechange,ts_repurchase,ts_stk_managers,ts_stk_rewards,"
        "ts_top10_holders,stock_notice,stock_news,stock_report"
    ),
    "TdxSync_D_缺口": (
        "ts_bse_mapping,ts_cb_basic,ts_cn_cpi,ts_cn_gdp,ts_cn_m,ts_cn_pmi,ts_cn_ppi,"
        "ts_fund_basic,ts_fut_basic,ts_fut_weekly_monthly,ts_fx_obasic,ts_hm_list,"
        "ts_hs_const,ts_opt_basic,ts_pledge_detail,ts_sge_basic,ts_slb_len,"
        "ts_top10_floatholders,ts_us_basic,ts_etf_basic,ts_ths_index,ts_anns_d,"
        "ts_cb_call,ts_cb_issue,ts_cb_share,ts_fund_div,ts_fund_manager,"
        "ts_fund_portfolio,ts_pledge_stat,ts_share_float,ts_slb_sec,"
        "ts_stk_holdertrade,ts_weekly"
    ),
}
PASSIVE_OWNER = "TdxTailSync(仅镜像增量,无主动拉取)"

OWNER_OF: dict[str, str] = {}
for _task, _tbls in TASK_TABLES.items():
    for _t in _tbls.split(","):
        _t = _t.strip()
        if _t:
            OWNER_OF[_t] = _task


# ── 契约自身的更正记录（2026-11-12）──────────────────────────────────
# 体检报 absent 不代表「数据没来」—— 也可能是名单写错了表名。判据只能是对上游自证：
# 上游 HTTP 400 = 上游没有这张表 = 我们的契约错，永远修不好，必须从这里改。
# 每一条都要写明「怎么发现的」，否则下次会被人当成笔误随手改回去。
RENAMED = {
    "ts_limit_list_d": (
        "ts_stk_limit",
        "上游 /api/data/table/ts_limit_list_d 返回 400，本地 manifest 恒为 "
        "part=0/rows=0/done=False，从未落地成功过。2026-11-12 探上游确认真实表为 "
        "ts_stk_limit（每日涨跌停价，842 万行，date_col=trade_date），"
        "涨停判定用它 + ts_limit_step（涨停晋级统计）替代。"),
}

# 上游确认不存在的表：不进体检报警（报了也修不好），只在 --audit 里列
GHOST = {k for k in RENAMED}


# ── P0 契约：下游强依赖、断了会直接改变结论的表 ────────────────────────
# (表, 中文名, 容差交易日, 为什么是 P0)
P0 = (
    ("ts_daily",          "A股日线",        1, "全部结构层/回测的底座，断了整个平台是昨天"),
    ("ts_daily_basic",    "A股每日指标",    1, "换手/市值/流通盘，选股排序用"),
    ("ts_hk_daily",       "港股日线",       1, "港股市场页"),
    ("ts_us_daily",       "美股日线",       1, "美股市场页（无主动拉取任务，靠上游喂）"),
    ("ts_index_daily",    "指数日线",       1, "大盘判定、相对强度基准"),
    ("tdx_sector_daily",  "板块日线·通达信", 1, "行业881 板块锚，strong_scan 守卫直接读它"),
    ("ts_ths_daily",      "板块日线·同花顺", 1, "同花顺板块池，tdx 板块源的备选"),
    ("ts_top_list",       "龙虎榜",         1, "游资/机构席位，选股证据链"),
    ("ts_stk_limit",      "每日涨跌停价",    1, "涨停/跌停判定（原 ts_limit_list_d 上游不存在，2026-11-12 更正）"),
    ("ts_limit_step",     "涨停晋级统计",    1, "连板高度与晋级率，情绪面"),
    ("ts_moneyflow_hsgt", "北向资金",       1, "隔夜资金面"),
    ("ts_margin_detail",  "两融明细",       1, "杠杆资金"),
    ("ts_fina_indicator", "财务指标",       130, "季度披露，容差按财报节奏"),
    ("ts_trade_cal",      "交易日历",       30, "体检工具自身的依赖：它停了所有容差计算都失效"),
    ("ts_rt_min",         "实时分钟",       0, "盘中实时价，收盘后需当天覆盖"),
)


# ── 表族推定（P0 之外的表按名推断容差，标 review="auto"）────────────────
# 维度/字典表：没有日期概念，不该参与新鲜度报警（否则天天红，报警就废了）
NO_DATE = {
    "tdx_sector_def", "tdx_sector_member", "ts_index_classify", "ts_index_member",
    "ts_ths_member", "ts_hm_list", "ts_hm_detail", "ts_fx_obasic", "ts_fut_mapping",
    "ts_stock_basic", "ts_hk_basic", "ts_us_basic", "ts_index_basic", "ts_etf_basic",
    "ts_fund_basic", "ts_fut_basic", "ts_opt_basic", "ts_cb_basic", "ts_sge_basic",
    "ts_bse_mapping", "ts_cb_rate", "ts_index_weight", "ts_index_member_all",
}

# 读取层日期列提示：tdx_read.date_col_of 猜不到的，这里显式给出
# （2026-11-12 实测 42 张表的 date_col 猜成 trade_date，其中 ts_rt_min 因此静默返回空）
DATE_COL_HINT = {
    "ts_rt_min": "time", "ts_rt_k": "time", "ts_hk_mins": "time",
    "ts_opt_mins": "time", "ts_stk_premarket": "time",
    "ts_trade_cal": "cal_date", "ts_hk_tradecal": "cal_date",
    "ts_us_tradecal": "cal_date",
    "ts_cctv_news": "date", "ts_us_tbr": "date", "ts_us_trycr": "date",
    "ts_us_tycr": "date", "ts_wz_index": "date", "ts_interest_rate": "date",
    "ts_news": "datetime", "ts_major_news": "pub_time",
    "ts_new_share": "ipo_date", "ts_stk_surv": "surv_date",
    "ts_fut_weekly_detail": "week_date",
    # SQLite 后端（tdx_info.db），列里没有 trade_date；不写清楚会退回猜列，
    # 2026-11-12 曾把 stock_notice 的 symbol 当成日期，整表判成 absent。
    "stock_news": "pub_date", "stock_report": "pub_date",
    "stock_notice": "notice_date",
}

_FIN_KEYS = ("fina", "income", "balancesheet", "cashflow", "forecast",
             "express", "dividend", "disclosure", "_audit")
# 事件/存量类：只在「发生了」才写一行，没有固定节奏 —— 按日频容差报红全是误报
_EVENT_KEYS = ("holdernumber", "holdertrade", "managers", "namechange",
               "share_float", "pledge", "repurchase", "top10_", "fund_portfolio",
               "fund_manager", "kpl_concept", "slb_", "stk_rewards", "cb_issue",
               "cb_rate", "hs_const", "tdx_index", "index_classify", "broker_recommend")
_MACRO_KEYS = ("shibor", "libor", "hibor", "sge_")
_STATIC_KEYS = ("_basic", "_def", "_member", "_mapping", "_classify",
                "_index_weight", "tradecal", "trade_cal", "bse_mapping",
                "_company", "_ths_index", "index_basic")
_INTRA_KEYS = ("ts_rt_", "stk_auction", "stk_premarket", "stk_factor_pro")
_SLOW_KEYS = ("_weekly", "_monthly", "_week_month")


def _infer(tbl: str) -> tuple[int, str]:
    """按表族推定容差交易日。返回 (容差, 推定依据)。"""
    n = tbl.lower()
    if n.startswith(_INTRA_KEYS):
        return 0, "盘中实时表"
    if any(k in n for k in _FIN_KEYS):
        return 130, "财务/披露表，季度节奏"
    if any(k in n for k in _EVENT_KEYS):
        return 130, "事件/存量表，只在发生时才写行"
    if n.startswith("ts_cn_") or any(k in n for k in _MACRO_KEYS):
        return 45, "宏观/利率表，月度节奏"
    if any(k in n for k in _STATIC_KEYS):
        return 400, "静态字典表，几乎不变"
    if any(k in n for k in _SLOW_KEYS):
        return 12, "周/月频表"
    return 1, "日频表（按名推定）"


_P0MAP = {t: (cn, lag, why) for t, cn, lag, why in P0}


def contract(tbl: str) -> dict:
    """取一张表的契约。P0 显式登记优先，其余按表族推定。"""
    owner = OWNER_OF.get(tbl) or PASSIVE_OWNER
    active = tbl in OWNER_OF
    dcol = DATE_COL_HINT.get(tbl)
    if tbl in GHOST:
        new, why = RENAMED[tbl][:2]
        return {"table": tbl, "cn": tbl, "lag": None, "tier": "GHOST",
                "why": why, "owner": owner, "active_fetch": active,
                "review": "explicit", "date_col": dcol, "superseded_by": new}
    if tbl in NO_DATE:
        return {"table": tbl, "cn": tbl, "lag": None, "tier": "DIM",
                "why": "维度/字典表，无日期概念", "owner": owner,
                "active_fetch": active, "review": "explicit", "date_col": dcol}
    if tbl in _P0MAP:
        cn, lag, why = _P0MAP[tbl]
        return {"table": tbl, "cn": cn, "lag": lag, "tier": "P0",
                "why": why, "owner": owner, "active_fetch": active,
                "review": "explicit", "date_col": dcol}
    lag, basis = _infer(tbl)
    return {"table": tbl, "cn": tbl, "lag": lag, "tier": "P1" if lag <= 12 else "P2",
            "why": basis, "owner": owner, "active_fetch": active,
            "review": "auto", "date_col": dcol}


def all_contracts(tables) -> list[dict]:
    return [contract(t) for t in sorted(tables)]


# ── 交易日历：把「今天该到哪天」算准 ──────────────────────────────────
def _num(s):
    """is_open 可能是 1 / 1.0 / '1'，统一成数值。"""
    try:
        return s.astype(float)
    except Exception:
        return (s.astype(str).str.strip()
                 .map({"1": 1.0, "1.0": 1.0, "0": 0.0, "0.0": 0.0}).fillna(0.0))


def load_open_days() -> list[str]:
    """读交易日历，返回【降序】的开市日列表（'YYYYMMDD'）。

    降序是关键：closed_days[0] 就是「最近一个已收盘的交易日」，
    closed_days[k] 就是「再往前 k 个交易日」。
    """
    import tdx_read
    df = tdx_read.read_all(CAL_TABLE, columns=["exchange", "cal_date", "is_open"])
    ex = df["exchange"].astype(str).str.upper()
    sub = df[ex == CAL_EXCHANGE] if (ex == CAL_EXCHANGE).any() else df
    if "is_open" in sub.columns:
        sub = sub[_num(sub["is_open"]) == 1]
    days = {str(v)[:8] for v in sub["cal_date"].astype(str) if str(v)[:8].isdigit()}
    return sorted(days, reverse=True)


def closed_days(open_days: list[str], now: _dt.datetime) -> list[str]:
    """只要「该出数据」的交易日：降序。

    今天若还没到 CLOSE_READY_HHMM，当天数据不该有，基准就退到昨天。
    这一步是防「每天 09:00 整表报红」的关键。
    """
    today = now.strftime("%Y%m%d")
    ready = now.hour * 100 + now.minute >= CLOSE_READY_HHMM
    return [d for d in open_days if d < today or (d == today and ready)]


def base_asof(open_days: list[str], now: _dt.datetime) -> str | None:
    """最近一个已收盘交易日 —— 日频表「应该」到的那一天。"""
    cd = closed_days(open_days, now)
    return cd[0] if cd else None


def lag_in_trade_days(open_days: list[str], asof: str | None, ref: str | None) -> int | None:
    """asof 落后 ref 多少个交易日（asof 已含在 ref 之前则返回正数）。"""
    if not asof or not ref:
        return None
    if asof >= ref:
        return 0
    # 数 (asof, ref] 之间有多少个开市日
    return sum(1 for d in open_days if asof < d <= ref)
