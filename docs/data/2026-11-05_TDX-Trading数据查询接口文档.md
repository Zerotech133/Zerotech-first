# TDX-Trading 数据查询接口文档

> 服务：`tdx_monitor_system.py`（Python，SQLite 本地库 ~225GB）
> **Base URL：`http://rookie.xtcloud-tech.com:48088`**（内网等价：`http://192.168.203.48:8088`）
> 更新日期：2026-11-05（表目录按库实时状态重新生成：181 张表 / 19 大类） ｜ 本文档聚焦**数据查询类**接口；监控/分组/QMT 类见 `API.md`

通用约定：

- 所有响应均为 JSON；错误格式 `{"error": "<原因>"}`（状态码 400/404）
- 日期参数统一 `YYYYMMDD`（如 20260926）；**传空 = 库内最新交易日**
- 实时口径：交易日盘中（9:15-17:00 自动判定），查最新（不传 date，或 date ≥ 库内最大日期）自动走实时快照；严格早于库内最大日期按历史查询
- 无需鉴权（内网/白名单服务）

---

## 一、数据表总览与明细查询（/api/data）

### 1. 全部表状态（181 张，实时缓存 60s）

```
GET /api/data/status
```

返回本地库全部表的状态（60 秒缓存，启动预热）。

```json
{
  "db_path": "/home/xundao/TDX-Trading/db/vibe_trading.db",
  "db_size": 241458614272,
  "tables": [
    {"table": "ts_daily", "name_cn": "日线行情", "category": "quote",
     "rows": 8202736, "latest": "20261104", "all_dt": "2026-11-04 17:00:00"}
  ],
  "token_set": true
}
```

### 2. 全部可查表清单（181 张 / 19 大类，2026-11-05 实测）

> 行数为当时快照，随每日采集持续增长；`/api/data/status` 可随时取最新。数据由 Tushare Pro（结构化行情/财务/宏观/港美股/期货期权等）+ akshare（资讯）+ 通达信协议（板块）自动增量更新；`ts_rt_min` 交易日盘中每分钟采集。

**基础信息**（basic）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_bak_basic | 备用基础列表 | 8,099,210 | 20261104 |
| ts_bse_mapping | 北交所代码映射 | 248 | — |
| ts_hs_const | 沪深股通成份股 | 823 | — |
| ts_index_classify | 申万行业分类 | 642 | — |
| ts_index_member_all | 申万行业成分 | 5,914 | — |
| ts_namechange | 股票曾用名 | 4,065 | 20261104 |
| ts_new_share | IPO新股列表 | 2,072 | 20261104 |
| ts_stk_ah_comparison | AH股比价 | 30,979 | 20261104 |
| ts_stk_managers | 上市公司管理层 | 318,364 | — |
| ts_stk_premarket | 每日股本(盘前) | 8,154,322 | 20261104 |
| ts_stk_rewards | 管理层薪酬 | 1,643,408 | — |
| ts_stock_basic | 股票基础列表 | 5,913 | — |
| ts_stock_company | 上市公司信息 | 6,298 | — |
| ts_stock_hsgt | 沪深港通股票列表 | 1,386,786 | 20261104 |
| ts_trade_cal | 交易日历 | 22,287 | — |

**股票行情**（quote）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_adj_factor | 复权因子 | 8,341,523 | 20261104 |
| ts_cyq_chips | 每日筹码分布 | 129,259,428 | — |
| ts_cyq_perf | 每日筹码及胜率 | 8,147,659 | 20261103 |
| ts_daily | 日线行情 | 8,202,736 | 20261104 |
| ts_daily_basic | 每日指标 | 8,154,167 | 20261104 |
| ts_monthly | 月线行情 | 412,419 | 20261030 |
| ts_stk_auction | 当日集合竞价 | 2,373,440 | 20261104 |
| ts_stk_auction_c | 收盘集合竞价 | 8,538,708 | 20261103 |
| ts_stk_auction_o | 开盘集合竞价 | 8,118,757 | 20261103 |
| ts_stk_factor | 股票技术因子 | 8,419,763 | 20261103 |
| ts_stk_factor_pro | 技术面因子(专业版) | 8,425,408 | 20261103 |
| ts_stk_limit | 每日涨跌停价 | 8,267,862 | 20261104 |
| ts_stk_mins | 股票分钟行情 | 345,339,323 | — |
| ts_stk_week_month_adj | 周月复权因子 | 4,421,418 | — |
| ts_stk_weekly_monthly | 周月线行情 | 4,370,441 | — |
| ts_suspend_d | 每日停复牌 | 30,597 | 20261104 |
| ts_weekly | 周线行情 | 1,754,225 | 20261030 |

**资金流向**（flow）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_moneyflow | 个股资金流向 | 7,907,278 | 20261104 |
| ts_moneyflow_dc | 东财个股资金流 | 4,017,851 | 20261104 |
| ts_moneyflow_hsgt | 沪深港通资金流向 | 765 | 20261104 |
| ts_moneyflow_ind_dc | 东财行业资金流 | 384,959 | 20261104 |
| ts_moneyflow_ind_ths | 同花顺行业资金流 | 46,440 | 20261104 |
| ts_moneyflow_mkt_dc | 东财大盘资金流 | 860 | 20261104 |
| ts_moneyflow_ths | 同花顺个股资金流 | 2,306,424 | 20261104 |

**涨跌停/打板**（limit）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_hm_detail | 游资每日明细 | 150,937 | 20261104 |
| ts_hm_list | 游资名录 | 117 | — |
| ts_kpl_concept | 开盘啦概念题材 | 5,000 | 20241117 |
| ts_kpl_concept_cons | 开盘啦题材成分 | 4,860,916 | 20261104 |
| ts_kpl_list | 开盘啦榜单 | 205,837 | 20261103 |
| ts_limit_cpt_list | 涨停最强板块 | 14,454 | 20261104 |
| ts_limit_list_d | 涨跌停列表 | 0 | — |
| ts_limit_list_ths | 同花顺涨跌停 | 56,552 | 20261104 |
| ts_limit_step | 涨停晋级统计 | 6,668 | 20261104 |
| ts_top_inst | 龙虎榜机构席位 | 885,281 | 20261103 |
| ts_top_list | 龙虎榜每日明细 | 106,709 | 20261104 |

**两融与股东**（margin）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_block_trade | 大宗交易 | 42,722 | 20261104 |
| ts_margin | 融资融券汇总 | 4,207 | 20261103 |
| ts_margin_detail | 融资融券明细 | 5,308,226 | 20261103 |
| ts_margin_secs | 两融标的列表 | 5,977,929 | 20261104 |
| ts_pledge_detail | 股权质押明细 | 97,062 | — |
| ts_pledge_stat | 股权质押统计 | 865,247 | 20261030 |
| ts_repurchase | 股票回购 | 48,109 | 20261104 |
| ts_share_float | 限售股解禁 | 2,198,492 | 20261029 |
| ts_slb_len | 转融通出借费率 | 1,347 | 20250725 |
| ts_slb_sec | 转融通标的证券 | 2,137,157 | 20240930 |
| ts_stk_holdernumber | 股东人数 | 285,470 | 20261103 |
| ts_stk_holdertrade | 股东增减持 | 74,063 | 20261104 |
| ts_stk_surv | 机构调研 | 28,850 | 20261103 |
| ts_top10_floatholders | 前十大流通股东 | 1,341,915 | 20260930 |
| ts_top10_holders | 前十大股东 | 1,408,560 | 20260930 |

**沪深港通/市场统计**（hsgt）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_daily_info | 市场交易统计 | 22,159 | 20261104 |
| ts_ggt_daily | 港股通每日成交 | 1,563 | 20261103 |
| ts_ggt_top10 | 港股通十大成交 | 20,861 | 20261104 |
| ts_hk_hold | 港股通持股 | 2,549,294 | 20261103 |
| ts_hsgt_top10 | 沪深股通十大成交 | 8,480 | 20261104 |
| ts_sz_daily_info | 深圳市场统计 | 14,238 | 20261104 |

**指数**（index）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_index_basic | 指数基本信息 | 9,766 | — |
| ts_index_daily | 指数日线 | 16,266,637 | 20261104 |
| ts_index_dailybasic | 指数每日指标 | 24,662 | 20261104 |
| ts_index_global | 国际指数 | 35,064 | 20261104 |
| ts_index_member | 指数成分股 | 0 | — |
| ts_index_monthly | 指数月线 | 322,940 | 20260930 |
| ts_index_weekly | 指数周线 | 1,324,185 | 20261009 |
| ts_index_weight | 指数成分权重 | 25,494 | — |

**板块(同花顺/东财/通达信)**（sector）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_dc_daily | 东财板块日线 | 1,270,845 | 20261104 |
| ts_dc_hot | 东财热榜 | 18,131 | 20261103 |
| ts_dc_index | 东方财富板块 | 319,415 | 20261104 |
| ts_dc_member | 东财板块成分 | 32,509,294 | 20261104 |
| ts_tdx_daily | 通达信板块日线 | 0 | — |
| ts_tdx_index | 通达信板块指数 | 1,000 | 20250417 |
| ts_tdx_member | 通达信板块成分 | 34,771,564 | 20261029 |
| ts_ths_daily | 同花顺板块日线 | 2,502,374 | 20261104 |
| ts_ths_hot | 同花顺热榜 | 4,799 | 20261103 |
| ts_ths_index | 同花顺板块 | 1,666 | — |
| ts_ths_member | 同花顺板块成分股 | 382,731 | — |

**财务(含VIP)**（fina）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_balancesheet | 资产负债表 | 288,278 | 20260930 |
| ts_balancesheet_vip | 资产负债表VIP | 143,773 | 20260930 |
| ts_cashflow | 现金流量表 | 310,953 | 20260930 |
| ts_cashflow_vip | 现金流量表VIP | 145,292 | 20260930 |
| ts_disclosure_date | 财报披露计划 | 303,492 | — |
| ts_dividend | 分红送股 | 199,249 | — |
| ts_dividend_vip | 分红送股VIP | 103,960 | 20260930 |
| ts_express | 业绩快报 | 11,664 | 20261021 |
| ts_express_vip | 业绩快报VIP | 11,664 | 20261021 |
| ts_fina_audit | 财务审计意见 | 95,704 | 20260630 |
| ts_fina_indicator | 财务指标 | 266,132 | 20260930 |
| ts_fina_indicator_vip | 财务指标VIP | 157,142 | 20260930 |
| ts_fina_mainbz | 主营业务构成 | 784,383 | 20260630 |
| ts_fina_mainbz_vip | 主营业务成分VIP | 962,545 | 20260930 |
| ts_forecast | 业绩预告 | 34,342 | 20271231 |
| ts_forecast_vip | 业绩预告VIP | 40,054 | 20260930 |
| ts_income | 利润表 | 347,031 | 20260930 |
| ts_income_vip | 利润表VIP | 145,476 | 20260930 |

**基金**（fund）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_etf_basic | ETF列表 | 1,829 | — |
| ts_etf_index | ETF基准指数 | 0 | — |
| ts_etf_mins | ETF分钟行情 | 93,791,175 | — |
| ts_fund_adj | 基金复权因子 | 2,187,989 | 20261104 |
| ts_fund_basic | 基金列表 | 18,586 | — |
| ts_fund_company | 基金管理人 | 214 | — |
| ts_fund_daily | 基金日线 | 2,101,916 | 20261104 |
| ts_fund_div | 基金分红 | 46,266 | 20261104 |
| ts_fund_manager | 基金经理 | 36,186 | — |
| ts_fund_nav | 基金净值 | 28,815,550 | 20261103 |
| ts_fund_portfolio | 基金持仓 | 5,574,899 | 20260630 |
| ts_fund_share | 基金份额 | 1,581,052 | 20261104 |

**债券/可转债**（bond）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_cb_basic | 可转债基本信息 | 1,171 | — |
| ts_cb_call | 可转债赎回 | 2,962 | 20261103 |
| ts_cb_daily | 可转债日线 | 328,816 | 20261104 |
| ts_cb_issue | 可转债发行 | 778 | — |
| ts_cb_price_chg | 转股价变动 | 6,157 | — |
| ts_cb_rate | 可转债票面利率 | 1,169 | — |
| ts_cb_share | 可转债转股 | 89,887 | — |
| ts_repo_daily | 债券回购日线 | 54,893 | 20261104 |

**期货/期权**（futopt）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_fut_basic | 期货合约列表 | 11,294 | — |
| ts_fut_daily | 期货日线 | 1,273,836 | 20261104 |
| ts_fut_holding | 期货持仓排名 | 14,082,326 | 20261104 |
| ts_fut_index_daily | 期货指数日线 | 0 | — |
| ts_fut_mapping | 主力合约映射 | 283,808 | 20261104 |
| ts_fut_settle | 期货结算参数 | 1,122,454 | 20261104 |
| ts_fut_weekly_detail | 期货周度详情 | 278,332 | 20200424 |
| ts_fut_weekly_monthly | 期货周月线 | 740,115 | — |
| ts_fut_wsr | 期货仓单日报 | 1,513,193 | 20261104 |
| ts_opt_basic | 期权列表 | 235,096 | — |
| ts_opt_daily | 期权日线 | 23,839,272 | 20261104 |
| ts_opt_mins | 期权分钟行情 | 0（未建） | — |

**黄金/外汇**（sge）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_fx_daily | 外汇日线 | 29,954 | 20261103 |
| ts_fx_obasic | 外汇基础信息 | 69 | — |
| ts_sge_basic | 上海黄金现货基础 | 13 | — |
| ts_sge_daily | 上海黄金现货日线 | 42,741 | 20261104 |

**宏观**（macro）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_cn_cpi | CPI | 80 | 202608 |
| ts_cn_gdp | GDP | 26 | 2026Q2 |
| ts_cn_m | 货币供应量 | 80 | 202608 |
| ts_cn_pmi | PMI | 81 | 202609 |
| ts_cn_ppi | PPI | 80 | 202608 |
| ts_eco_cal | 宏观经济日历 | 8,912 | 20261104 |
| ts_hibor | Hibor利率 | 0 | — |
| ts_libor | Libor利率 | 0 | — |
| ts_shibor | Shibor利率 | 0 | — |
| ts_shibor_quote | Shibor报价 | 0 | — |
| ts_us_tbr | 美国国库券利率 | 1,708 | 20261103 |
| ts_us_trycr | 美国实际收益率 | 1,708 | 20261103 |
| ts_us_tycr | 美国国债收益率 | 1,708 | 20261103 |
| ts_wz_index | 温州民间借贷指数 | 1,528 | 20261103 |

**港股/美股**（hkus）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_hk_basic | 港股列表 | 3,555 | — |
| ts_hk_daily | 港股日线 | 456,875 | 20261104 |
| ts_hk_daily_adj | 港股复权行情 | 3,287,109 | 20261104 |
| ts_hk_mins | 港股分钟行情 | 228,792,488 | — |
| ts_hk_tradecal | 港股交易日历 | 2,500 | — |
| ts_us_basic | 美股列表 | 24,215 | — |
| ts_us_daily | 美股日线 | 16,315,286 | — |
| ts_us_tradecal | 美股交易日历 | 2,500 | — |

**新闻资讯**（news）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_anns_d | 上市公司公告 | 5,730,841 | 20261104 |
| ts_broker_recommend | 券商月度金股 | 18,671 | 202611 |
| ts_cctv_news | 新闻联播文字稿 | 37,287 | 20261104 |
| ts_major_news | 长篇新闻通讯 | 2,692,869 | 2026-11-04 20:54:59 |
| ts_news | 新闻快讯 | 2,500,283 | — |
| ts_report_rc | 卖方盈利预测 | 532,485 | 20261104 |

**实时采集**（rt）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| ts_rt_k | 实时K线 | 0（未建） | — |
| ts_rt_min | 实时分钟线 | 71,668,313 | 2026-11-05 13:17:00 |

**通达信板块(自建)**（tdx_sector）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| tdx_sector_daily | 通达信板块指数日线 | 3,741,012 | — |
| tdx_sector_def | 通达信板块定义 | 1,119 | — |
| tdx_sector_member | 通达信板块成分股 | 9,273 | — |
| tdx_sector_min | 通达信板块指数分钟线 | 36,251,854 | 20261022 |

**资讯(akshare自采)**（stock_info）

| 表名 | 中文名 | 行数 | 最新日期 |
|---|---|---|---|
| stock_news | 个股新闻 | 323,452 | 2026-11-05 |
| stock_notice | 股票公告 | 142,905 | 2026-11-05 |
| stock_report | 研报 | 148,879 | 2026-11-05 |

### 3. 表数据分页查询

```
GET /api/data/table/{tbl}?code=&start_date=&end_date=&limit=&offset=
```

| 参数 | 说明 |
|---|---|
| tbl | 上表 30 张表名之一 |
| code | 可选。股票代码，6 位数字（`002156`）或 ts_code 格式（`002156.SZ`）均可 |
| start_date / end_date | 可选。YYYYMMDD 或 YYYY-MM-DD，按该表日期列过滤 |
| limit | 可选。默认 100，**上限 5000** |
| offset | 可选。默认 0 |

```json
{
  "columns": ["ts_code", "trade_date", "open", "high", "low", "close",
              "pre_close", "change", "pct_chg", "vol", "amount", "_fetched_at"],
  "rows": [["000001.SZ", "20260924", 11.35, 11.47, 11.29, 11.3, ...]],
  "total": 4136843,
  "name_cn": "日线行情",
  "date_col": "trade_date"
}
```

> `total` 为该过滤条件下的总行数，可用于翻页计算。大表（ts_rt_min）建议必带 code 或日期范围。

### 4. 下载/更新控制（管理类，附带列出）

```
POST /api/data/update                    # 启动增量更新 {}
POST /api/data/download                  # 启动全量下载 {"tables": ["ts_daily"]?}
POST /api/data/stop                      # 停止下载
POST /api/data/token                     # 保存 Tushare token {"token": "..."}
GET  /api/data/progress                  # 后台下载进度与日志（running, pct, log[]）
```

---

## 二、板块数据查询（/api/tdx-sector）

数据来源：通达信行情协议（tdxpy 直连）。**seg 七类**：`concept`(880 主题概念) / `industry`(官方 56 个一级行业，默认) / `industry_detail`(881 细分行业 467 个) / `style`(风格) / `region`(地区) / `old_industry`(旧版行业) / `stat`(统计指数)。

### 1. 板块数据表状态

```
GET /api/tdx-sector/status
```

```json
{"tables": [
  {"table": "tdx_sector_def", "rows": 1119, "latest": null},
  {"table": "tdx_sector_daily", "rows": 3565709, "latest": "20260924"}
]}
```

### 2. 板块涨幅排名 ⭐

```
GET /api/tdx-sector/rank?seg=industry&top=20&date=
```

| 参数 | 说明 |
|---|---|
| seg | 可选，默认 `industry`（官方 56 一级行业）。可选 `industry_detail` / `concept` / `style` / `region` / `old_industry` / `stat` |
| top | 可选，默认 20 |
| date | 可选 YYYYMMDD。**不传 = 最新**；盘中查最新自动走实时快照 |

```json
{
  "trade_date": "20260926",
  "realtime": true,
  "seg": "industry",
  "sectors": [
    {"code": "881166", "name": "光伏设备", "seg": "industry_l1",
     "close": 2525.01, "pre_close": 2487.93, "pct": 1.49,
     "up_count": 346, "down_count": 152, "has_members": "tdx"}
  ]
}
```

- `pct` = 最新收盘（盘中为实时价）÷ 上一交易日收盘 − 1
- `up_count/down_count` 为板块内上涨/下跌家数（TDX 分钟线，收盘后为终值）
- `has_members`：`tdx`=TDX 行业树权威成分 / `ths`=同花顺兜底 / `corr`=相关性推导 / None

### 3. 板块成分股（含当日行情）⭐

```
GET /api/tdx-sector/members?code=881385&date=
```

| 参数 | 说明 |
|---|---|
| code | **必填**。板块指数代码（880/881 段，从 rank/list 接口获取） |
| date | 可选。不传 = 最新；盘中自动实时 |

```json
{
  "sector": {"code": "881385", "name": "通信设备", "seg": "industry881"},
  "source": "tdx_industry",
  "trade_date": "20260926",
  "realtime": true,
  "realtime_source": "local_rt_min",
  "trade_time": "14:59",
  "count": 88,
  "tdx_index_member_count": 88,
  "members": [
    {"code": "000063.SZ", "name": "中兴通讯", "pct_chg": 3.29,
     "close": 102.5, "amount": 12345678.9}
  ]
}
```

- `members` 按 `pct_chg` 降序；`source` 标成分口径：`tdx_industry`(权威) / `tdx_block`(板块文件) / `ths_industry` / `ths_concept` / `tdx_corr`(相关性推导，附 `corr` 字段)
- 盘中实时优先走本地分钟线（`realtime_source: local_rt_min`，延迟 <1 分钟），采集器未跑时回落 TDX 快照（`tdx_snapshot`）
- `tdx_index_member_count` 为 TDX 指数权威家数，可校验口径一致性

### 4. 板块列表（含最新涨跌幅）

```
GET /api/tdx-sector/list?seg=&kw=&limit=
```

| 参数 | 说明 |
|---|---|
| seg | 可选。见七类说明 |
| kw | 可选。板块名或代码模糊搜索（URL 编码） |
| limit | 可选，默认 100，上限 2000 |

```json
{"sectors": [{"code": "880966", "name": "消费电子", "seg": "concept",
  "close": 2525.01, "pre_close": 2487.93, "pct": 1.49}]}
```

### 5. 板块详情（定义 + 近 N 日日线 + 成分）

```
GET /api/tdx-sector/detail?code=880966&days=60
```

```json
{
  "sector": {"code": "880966", "name": "消费电子", "seg": "concept"},
  "daily": [{"trade_date": "20260926", "open": 2503.46, "high": 2525.21,
             "low": 2480.67, "close": 2525.01, "vol": 12345678, "amount": 9876543}],
  "member_count": 88,
  "members": ["000001.SZ", "000063.SZ"]
}
```

> `daily` 按日期降序；`members` 最多返回 200 只，`member_count` 为全量。

### 6. 板块当日分钟线（含涨跌家数）

```
GET /api/tdx-sector/minutes?code=880966
```

```json
{"code": "880966", "date": "20260926", "minutes": [
  {"time": "15:00", "open": 2525.15, "high": 2525.21, "low": 2525.01,
   "close": 2525.01, "vol": 2100, "amount": 5300,
   "up_count": 346, "down_count": 152}]}
```

---

## 三、个股指标 / 涨停统计 / 公式选股（/api/backtest）

### 1. 技术指标计算

```
GET /api/backtest/indicator/{code}?start_date=20240101&end_date=20241231&indicators=MA,MACD,RSI,KDJ,BOLL
```

| 参数 | 说明 |
|---|---|
| code | 路径参数。6 位数字或 ts_code 格式 |
| indicators | 可选，默认 `MA,MACD,RSI,KDJ,BOLL`（逗号分隔） |
| start_date / end_date | 可选，默认 2024 全年 |

> 指标口径与回测引擎一致（EMA/MACD `adjust=False` 首值=首数据；RSI loss=0→50；ATR 用 SMA；KDJ ewm(com=2)；BOLL 样本 std）。

### 2. 涨停统计（本地日线精确判定）⭐

```
GET /api/backtest/limit-up-stats?days=20&min_count=4&top=50&code=
```

| 参数 | 说明 |
|---|---|
| days | 回看交易日数，默认 20 |
| min_count | 收盘涨停次数下限，默认 1（"近 20 日至少 4 板"传 4） |
| top | 最多返回条数，默认 50 |
| code | 可选。指定单股统计（如 002156.SZ） |

判定标准（与交易所一致）：`close == ROUND_HALF_UP(pre_close × (1+档位), 2)`；主板 10%（ST 5%）/ 创业板·科创板 20% / 北交所 30%；盘中触板未封住单独计 `touched`；排除新股无限制期与停牌日。**勿用龙虎榜近似**。

```json
{
  "window": "近20个交易日(20260828~20260924)",
  "total_scanned": 5569,
  "matched": 52,
  "records": [
    {"ts_code": "601086.SH", "name": "国芳集团", "board": "main",
     "close_limit_count": 9, "touched_count": 1,
     "last_limit_date": "20260921", "max_streak": 5,
     "last_touched_date": "20260911",
     "limit_dates": ["20260828", "20260831", "20260901", "20260902"]}
  ],
  "note": "收盘涨停=close==涨停价(ROUND_HALF_UP(昨收×(1+档位),2)); touched=盘中触及涨停价(含炸板); excluded=no-limit-period新股与停牌日"
}
```

字段说明：`board` 板块（main 主板 / st / chinext 创业板 / star 科创板 / bj 北交所）；`close_limit_count` 收盘涨停次数；`touched_count` 盘中触板未封住次数；`max_streak` 最长连续涨停；`limit_dates` 最近 10 次涨停日。

### 3. TDX 公式全市场选股 ⭐⭐

```
POST /api/backtest/screen
Content-Type: application/json

{
  "formula": "MA24:=MA(C,24);\nCOND1:=C>=MA24;\nCOND2:=EVERY(C>O,4);\nXG:COND1 AND COND2 AND NOT(CODELIKE('9'));",
  "date": null,
  "top": 100,
  "include_st": false,
  "include_bj": false
}
```

| 参数 | 说明 |
|---|---|
| formula | **必填**。通达信公式原文（支持 `:=` 中间变量 / `XG:` 输出行 / AND OR NOT / `{}` 与 `//` 注释 / CODELIKE·NAMELIKE 前缀过滤） |
| date | 可选 YYYYMMDD。空/传今天 = 库内最新交易日；传今天盘中自动回退库内最新 |
| top | 可选，默认 100。按当日成交额降序截断 |
| include_st / include_bj | 可选，默认 false（排除 ST 与北交所） |

```json
{
  "date": "20260924",
  "scanned": 5025,
  "matched": 1,
  "records": [
    {"ts_code": "300196.SZ", "name": "长海股份", "industry": "玻璃玻纤",
     "close": 18.5, "pct_chg": -1.59, "amount": 123456789.0, "vol": 987654}
  ],
  "cond_stats": {"COND1": 2345, "COND2": 118, "XG": 1},
  "window_bars": 24
}
```

- `cond_stats`：各中间条件当日命中数（排查哪个条件过滤最狠）
- 全市场 5000+ 只耗时约 7.5 秒
- 公式内置变量：`C 收盘 / O 开盘 / H 最高 / L 最低 / V 成交量 / AMO 成交额 / PREC 昨收 / PCT 涨跌幅 / ISUP 收阳 / ISDOWN 收阴 / AMPLITUDE 振幅`
- 支持函数白名单（34 个）：MA EMA SMA REF HHV LLV SUM COUNT EVERY EXIST STD AVEDEV ABS MAX MIN POW SQRT BETWEEN IF CROSS BARSLAST VALUEWHEN BARSCOUNT HHVALL LLVALL CODELIKE NAMELIKE 等
- 语法自助：`GET /api/backtest/screen/help` 返回变量/函数/语法说明与示例

### 4. 数据源覆盖查询

```
GET  /api/backtest/data-sources                      # 14 张核心表覆盖（行数+日期范围）
GET  /api/backtest/data-sources/{dataType}?code=&start_date=&end_date=   # 按类型查明细
```

### 5. 因子库查询

```
GET  /api/backtest/factors            # 因子名列表（~430+：Alpha101/Qlib158/GTJA191/量价资金面）
GET  /api/backtest/factors/info       # 因子详情
```

---

## 四、健康检查与示例

```
GET /api/backtest/health    # 回测引擎健康
GET /api/status             # 行情连接 + 采集器状态
```

**curl 示例**：

```bash
# 板块涨幅排名（一级行业 Top5）
curl 'http://rookie.xtcloud-tech.com:48088/api/tdx-sector/rank?seg=industry&top=5'

# 某板块成分股（含当日实时涨跌幅）
curl 'http://rookie.xtcloud-tech.com:48088/api/tdx-sector/members?code=881385'

# 个股日线
curl 'http://rookie.xtcloud-tech.com:48088/api/data/table/ts_daily?code=002156&limit=10'

# 近 20 日至少 4 个涨停
curl 'http://rookie.xtcloud-tech.com:48088/api/backtest/limit-up-stats?days=20&min_count=4'

# TDX 公式选股（连阳首阴）
curl -X POST 'http://rookie.xtcloud-tech.com:48088/api/backtest/screen' \
  -H 'Content-Type: application/json' \
  -d '{"formula":"COND2:=EVERY(C>O,4);XG:COND2 AND C<REF(C,1);","top":50}'
```

**Python 示例**：

```python
import requests

BASE = 'http://rookie.xtcloud-tech.com:48088'

# 板块排名
r = requests.get(f'{BASE}/api/tdx-sector/rank', params={'seg': 'industry', 'top': 5}).json()
for s in r['sectors']:
    print(s['code'], s['name'], s['pct'])

# 成分股
r = requests.get(f'{BASE}/api/tdx-sector/members', params={'code': r['sectors'][0]['code']}).json()
print(r['sector']['name'], r['count'], '只', '实时' if r['realtime'] else '收盘')

# 公式选股
r = requests.post(f'{BASE}/api/backtest/screen', json={
    'formula': "MA5:=MA(C,5); MA10:=MA(C,10); XG:CROSS(MA5,MA10) AND C>=5 AND NOT(CODELIKE('9'));",
    'top': 50}).json()
print('命中', r['matched'], '只：', [x['ts_code'] for x in r['records']])
```
