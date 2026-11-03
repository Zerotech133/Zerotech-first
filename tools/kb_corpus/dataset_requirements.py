# -*- coding: utf-8 -*-
"""训练/策略所需数据集需求清单（60 项）与状态判定。"""

REQ_ROWS = [
    ("基础", "股票列表/上市日期", "样本域/幸存者偏差", "P0", "stock_basic", "tushare.stock_basic"),
    ("基础", "交易日历", "样本切分/对齐", "P0", "trade_cal", "tushare.trade_cal"),
    ("基础", "停复牌记录", "可交易性/停牌处理", "P0", "suspend", "tushare.suspend_d"),
    ("基础", "ST/风险警示标记", "样本过滤", "P0", "st_", "tushare.stock_basic 名称+status"),
    ("基础", "涨跌停价格", "可交易性/涨停因子", "P0", "stk_limit", "tushare.stk_limit"),
    ("基础", "股本变动", "市值/股本因子", "P1", "share", "tushare.daily_basic 已含"),
    ("基础", "分红送股", "复权/股息因子", "P1", "dividend", "tushare.dividend"),
    ("基础", "复权因子", "收益标签/复权价", "P0", "adj_factor", "tushare.adj_factor"),
    ("行情", "日线行情", "因子/标签", "P0", "tushare.daily", "tushare.daily"),
    ("行情", "周线/月线", "多周期因子", "P2", "weekly", "tushare.weekly/monthly"),
    ("行情", "股票分钟线(1min)", "缠论/日内因子", "P0", "stk_mins", "tushare.stk_mins"),
    ("行情", "指数日线", "基准/大盘因子", "P0", "index_daily", "tushare.index_daily"),
    ("行情", "指数分钟线", "大盘节奏", "P1", "idx_mins", "tushare.idx_mins"),
    ("行情", "ETF日线", "ETF 轮动/基准", "P1", "fund_daily", "tushare.fund_daily"),
    ("行情", "ETF分钟线", "ETF 日内", "P2", "etf_mins", "tushare.etf_mins"),
    ("行情", "可转债日线", "可转债策略", "P2", "cb_daily", "tushare.cb_daily"),
    ("行情", "港股日线", "外围联动", "P2", "hk_daily", "tushare.hk_daily"),
    ("行情", "美股日线", "外围联动", "P2", "us_daily", "tushare.us_daily"),
    ("行情", "期货日线", "商品/宏观", "P2", "fut", "tushare.fut_daily"),
    ("行情", "期权数据", "波动率策略", "P2", "opt", "tushare.opt_basic"),
    ("行情", "集合竞价/盘前", "开盘因子", "P1", "auction", "tushare.stk_auction（需开通）"),
    ("行情", "Level2/逐笔委托", "盘口类战法", "P2", "level2", "券商/交易所（采购）"),
    ("资金", "个股资金流", "资金流因子", "P0", "moneyflow", "tushare.moneyflow"),
    ("资金", "北向/沪深港通", "北向情绪", "P1", "moneyflow_hsgt", "tushare.moneyflow_hsgt"),
    ("资金", "融资融券", "杠杆情绪", "P1", "margin", "tushare.margin_detail"),
    ("资金", "龙虎榜", "游资/席位", "P1", "top_list", "tushare.top_list"),
    ("资金", "大宗交易", "筹码/减持", "P1", "block_trade", "tushare.block_trade"),
    ("资金", "股权质押", "风险因子", "P1", "pledge", "tushare.pledge_stat"),
    ("资金", "股东户数", "筹码集中度", "P1", "holdernumber", "tushare.stk_holdernumber"),
    ("资金", "十大股东", "机构持仓", "P1", "top10", "tushare.top10_holders"),
    ("资金", "基金持仓", "机构持仓", "P1", "fund_portfolio", "tushare.fund_portfolio"),
    ("资金", "机构调研", "事件因子", "P1", "surv", "tushare.stk_surv"),
    ("资金", "限售解禁", "供给冲击", "P1", "share_float", "tushare.share_float"),
    ("资金", "筹码分布", "筹码因子", "P0", "cyq_chips", "tushare.cyq_chips"),
    ("基本面", "财务指标", "基本面因子", "P0", "fina_indicator", "tushare.fina_indicator"),
    ("基本面", "利润表", "基本面", "P0", "income", "tushare.income"),
    ("基本面", "资产负债表", "基本面", "P0", "balancesheet", "tushare.balancesheet"),
    ("基本面", "现金流量表", "基本面", "P0", "cashflow", "tushare.cashflow"),
    ("基本面", "业绩预告", "事件因子", "P1", "forecast", "tushare.forecast"),
    ("基本面", "业绩快报", "事件因子", "P1", "express", "tushare.express"),
    ("基本面", "主营构成", "行业暴露", "P2", "mainbz", "tushare.fina_mainbz"),
    ("基本面", "公告文本", "NLP/事件", "P1", "anns", "tushare.anns_d"),
    ("基本面", "研报评级", "研报因子", "P1", "report_rc", "tushare.report_rc"),
    ("基本面", "研报文本", "NLP/知识", "P1", "research_report", "tushare.research_report"),
    ("基本面", "互动易问答", "互动易情绪", "P2", "irm_qa", "irm_qa"),
    ("行业", "申万/中信行业分类", "行业中性化", "P0", "index_classify", "tushare.index_classify"),
    ("行业", "概念板块", "主题/板块因子", "P1", "ths_index", "tushare.ths_index"),
    ("行业", "板块成分", "板块映射", "P1", "sector_member", "tdx_sector_member"),
    ("行业", "指数成分权重", "基准/中性化", "P0", "index_weight", "tushare.index_weight"),
    ("行业", "板块行情", "板块因子", "P1", "sector_daily", "tdx_sector_daily"),
    ("行业", "板块分钟", "板块节奏", "P2", "sector_min", "tdx_sector_min"),
    ("情绪", "新闻", "情绪/NLP", "P1", "news", "tushare.news/major_news"),
    ("情绪", "股吧", "散户情绪", "P2", "guba", "guba"),
    ("情绪", "雪球", "舆情", "P2", "xueqiu", "xueqiu"),
    ("宏观", "宏观指标(GDP/CPI/PPI/PMI)", "宏观因子", "P1", "macro", "cnopendata/ashare"),
    ("宏观", "利率/汇率/商品", "宏观/商品因子", "P1", "rate", "tushare.shibor/fx"),
    ("质量", "公告时点/披露日期", "PIT 防穿越", "P0", "ann_date", "各表 ann_date 字段"),
    ("质量", "停牌/交易状态", "可交易性", "P0", "trade_status", "daily 交易状态"),
    ("质量", "退市样本", "幸存者偏差", "P1", "delist", "tushare.stock_basic list_status"),
]


def _status_of(datasets, keywords: str):
    kws = [k.strip().lower() for k in keywords.split("/") if k.strip()]
    hits = []
    for d in datasets:
        label = str(d.get("label", "")).lower()
        if any(k in label for k in kws):
            hits.append(d)
    if not hits:
        return "缺失", "", "", ""
    reg = [d for d in hits if d.get("registered")]
    d = (reg or hits)[0]
    nfiles = d.get("files") or 0
    mb = d.get("mb") or 0
    if (nfiles and nfiles <= 5) or (mb and mb < 1 and nfiles and nfiles < 20):
        return "部分覆盖", d.get("label"), d.get("latest"), d.get("path")
    return ("已有" if d.get("registered") else "已有未注册"), d.get("label"), d.get("latest"), d.get("path")


# 人工校正映射（关键词自动匹配易错项）
OVERRIDES = {
    "ST/风险警示标记": ("字段/口径", "stock_basic name+list_status", "-", ""),
    "股本变动": ("已有", "tushare.daily_basic（total_share/float_share）", "2026-10-30", "quant_backtest/data/tushare"),
    "周线/月线": ("缺失", "", "", ""),
    "利润表": ("已有", "tushare.income", "2026-10-30", "quant_backtest/data/tushare/income"),
    "筹码分布": ("部分覆盖", "tushare.cyq_chips（23 文件/覆盖不足）", "2026-10-31", "quant_backtest/data/tushare/cyq_chips"),
    "公告时点/披露日期": ("字段/口径", "各表 ann_date 字段", "-", ""),
    "停牌/交易状态": ("字段/口径", "daily 交易状态/停牌标记", "-", ""),
    "退市样本": ("字段/口径", "stock_basic list_status", "-", ""),
}


def status_of(datasets, keywords: str, name: str = ""):
    if name in OVERRIDES:
        return OVERRIDES[name]
    return _status_of(datasets, keywords)
