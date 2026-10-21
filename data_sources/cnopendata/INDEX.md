# CnOpenData 数据源档案（股票 / 金融）

> 生成时间：2026-10-22T01:31:56+08:00　｜　条目总数：**506**　｜　唯一数据集：**486**　｜　别名：20　｜　去重丢弃：178
>
> **条目数 ≠ 数据集数**：上游把同一数据集挂在多个类目下（两条 URL 都有效、内容同一份），
> 这些副本保留但标了 `alias_of`，统计覆盖时请用「唯一数据集」那个数。
>
> 档案只登记公开目录页元信息（名称、覆盖、量级、更新频率、获取方式、链接），**不含任何数据内容**；未采购、未调用接口。
> 获取方式一栏：`open` 可免费下载 / `trial` 高校试用 / `direct_sale` 需采购 / `api` 需 API Key。

## 总览

| 维度 | 分布 |
|---|---|
| 优先级 | P3 229、P2 171、P1 89、P0 17 |
| 圈层 | core 236、factor 179、context 91 |
| 更新频率 | 年度 309、未标注 100、不定期 34、不支持更新 21、实时 13、日 12、按需 9、季度 5、月 3 |
| 获取方式 | direct_sale 455、api 29、open 22 |

| 分类组 | 条目数 |
|---|---|
| 因子与事件数据 | 176 |
| SSRD上市公司与资本市场 | 112 |
| 宏观、区域经济与财政税收 | 72 |
| 金融行业数据 | 61 |
| 新闻舆情与文本语料 | 56 |
| API商店 | 29 |

## P0 优先接入候选（17 条）

| 名称 | 分类 | 覆盖 | 更新 | 获取 | 备注 |
|---|---|---|---|---|---|
| [股吧回帖实时数据查询 API](https://www.cnopendata.com/api-store/guba-reply) | 另类数据与文本语料 | 仅针对发帖时间在最近 7 天内 的帖子进行回帖数据抓取（每 12 小时执行一次全量遍历）；页面未标注数据起始年份 | 实时 | api | GET https://apiopendata.com/api/guba_reply，需 API Key，每 12 小时遍历一次（准实时）。 |
| [股吧帖子实时数据查询 API](https://www.cnopendata.com/api-store/guba-posts) | 另类数据与文本语料 | 接口默认仅提供近2年的历史发帖数据（页面未标注起始年份） | 实时 | api | GET https://apiopendata.com/api/guba_posts，需 API Key，每 30 分钟增量抓取（准实时，延 |
| [雪球帖子实时数据查询 API](https://www.cnopendata.com/api-store/xueqiu-posts) | 另类数据与文本语料 | 从 2025 年开始采集；可回溯获取一定历史数据 | 实时 | api | GET https://apiopendata.com/api/xueqiu_posts，需 API Key，2025 年后持续实时获取更新 |
| [A股上市公司股吧文本数据](https://www.cnopendata.com/data/m/listedcompany-special/chinese-listed-company-stocks-comment.html) | A股上市公司另类数据 | 截至2025（实时更新） | 年度 | direct_sale | 简介称实时更新、字段标注年度更新，增量口径需向卖方确认；股吧帖子另有API商店实时查询接口 |
| [A股上市公司股吧文本统计数据](https://www.cnopendata.com/data/m/listedcompany-special/guba-stat.html) | A股上市公司另类数据 | 发帖时间 1988-2024，回帖时间 2007-2024 | 年度 | direct_sale | 统计表非原文，不能回溯单帖；发帖3.3亿/回帖4.2亿与文本库7.78亿/15.2亿量级差一倍以上，两库口径不同不可混用 |
| [雪球网上市公司个人投资者关注和实盘模拟盘交易数据](https://www.cnopendata.com/data/m/listedcompany-special/listed-company-transactions.html) | A股上市公司另类数据 | 截至2025年12月31日（实时更新中） | 年度 | direct_sale | 标题含「关注和实盘模拟盘交易」，实际以发帖文本为主，交易明细口径需核实；年度更新 |
| [雪球网上市公司个人投资者发帖实时更新数据](https://www.cnopendata.com/data/m/listedcompany-special/A-listedco-investorpost.html) | A股上市公司另类数据 | 截至2025年12月31日（实时更新中） | 年度 | direct_sale | 称实时更新中但标注年度更新，需确认增量口径；API商店有雪球帖子实时查询，覆盖字段需核对 |
| [雪球网上市公司新闻实时更新数据](https://www.cnopendata.com/data/m/listedcompany-special/xueqiu-news.html) | A股上市公司另类数据 | 截至2025年12月31日（实时更新中） | 实时 | direct_sale | 全站唯一明确标「实时更新」的条目；API商店的「财经网站新闻实时查询」是否覆盖雪球新闻需核实 |
| [A股上市公司行情数据](https://www.cnopendata.com/data/m/listedcompany-basic/A-market-quotes.html) | A股上市公司基础数据 | 1991年至今 | 年度 | direct_sale | 直接补单股长历史行情缺口，可替代/校验本地通达信缓存；分类页未标量级 |
| [美股上市公司雪球投资者发帖评论数据](https://www.cnopendata.com/data/m/Us-listed-company/US-listedco-investorpost.html) | 美股上市公司数据 | 未标注 | 年度 | direct_sale | 直接服务雪球舆情主线，可对标本方美股帖覆盖缺口 |
| [中国上市公司股票复权行情数据](https://www.cnopendata.com/data/m/trading/gpfqhq.html) | 股票和债券交易数据 | 未标注（沪深京全历史口径） | 年度 | direct_sale | 复权因子是T7因子计算的关键补位；分类页未标时间起点 |
| [中国上市公司股票未复权行情数据](https://www.cnopendata.com/data/m/trading/clcnrr.html) | 股票和债券交易数据 | 未标注（沪深京全历史口径） | 年度 | direct_sale | 直接补单股历史行情缺口，可与本地通达信缓存交叉校验；分类页未标时间起点 |
| [港股上市公司雪球投资者发帖评论数据](https://www.cnopendata.com/data/m/hongkong/HK-listedco-investorpost.html) | 香港上市公司数据 | 未标注 | 年度 | direct_sale | 直接服务雪球舆情主线，可对标本方港股帖池覆盖缺口 |
| [中国财经报纸电子报文本数据](https://www.cnopendata.com/data/m/financial-news/cjbkxw.html) | 财经新闻舆情 | 至2025年底 | 实时 | direct_sale | 标注实时、累计至2025年底；起点年份未写。报纸电子版通常按版面出刊，时效弱于网站新闻，追新时应作为二源。 |
| [中国财经文本语料数据](https://www.cnopendata.com/data/m/financial-news/finance-corpus.html) | 财经新闻舆情 | 未标注 | 实时 | direct_sale | 标注实时更新，最适合追新；但页面未写历史起点与可回溯年限，采购前须确认回溯到哪一年、单日增量与正负面情感是自算还是原文标注。 |
| [中国财经新闻网站文本数据](https://www.cnopendata.com/data/m/financial-news/cjwzxw.html) | 财经新闻舆情 | 未标注 | 实时 | direct_sale | 标注实时、50家站点口径；历史起点与单日增量未写，追新前须确认延迟（是否T+0）。 |
| [舆情云数据](https://www.cnopendata.com/data/m/financial-news/public-opinion.html) | 财经新闻舆情 | 未标注 | 日 | direct_sale | 日度更新，可支撑日频追新（微博/论坛覆盖是我方当前缺口）；但按日批交付而非实时推送，历史起点与可回溯年限未写。 |

## 重复挂载别名（20 条，未删除）

同一数据集在多个类目下重复挂载，两条 URL 都可用。下表列出非正本的一条；
正本选择规则：已分诊 > 优先级高 > id 字典序（不随描述变化，避免再抓取后正本漂移）。

| 别名 id | 正本 id | 名称 |
|---|---|---|
| cnod-report | cnod-fund-broker-report | A股上市公司分析师研报及评论数据 |
| cnod-report-all | cnod-fund-broker-report-all | A股上市公司分析师研报文本数据 |
| cnod-fin-fs | cnod-bank-fin-fs | A股上市金融机构财务报表数据 |
| cnod-bond-credit-rating | cnod-bond-bond-credit-rating | 中国债券信用评级公告数据 |
| cnod-mkt-bond | cnod-bond-mkt-bond | 中国债券行情数据 |
| cnod-qsacct | cnod-fund-broker-qsacct | 中国券商线上招聘数据 |
| cnod-jijin-stocks-comment | cnod-fund-broker-jijin-stocks-comment | 中国基金产品股吧文本数据 |
| cnod-mkt-fund | cnod-fund-broker-mkt-fund | 中国基金行情数据 |
| cnod-chinese-community-information | cnod-api-housing-estate-china | 中国小区信息数据 |
| cnod-fund-broker-ch-reports | cnod-ch-reports | 中国证券公司晨会报告数据 |
| cnod-macros-reports | cnod-fund-broker-macros-reports | 中国证券分析师宏观研究报告数据 |
| cnod-profitforecast-reports | cnod-fund-broker-profitforecast-reports | 中国证券分析师盈利预测研究数据 |
| cnod-cl-reports | cnod-fund-broker-cl-reports | 中国证券分析师策略报告数据 |
| cnod-industry-reports | cnod-fund-broker-industry-reports | 中国证券分析师行业研究报告数据 |
| cnod-fund-broker-securities-firm-employee | cnod-securities-firm-employee | 证券公司从业人员信息数据 |
| cnod-fund-broker-sub-cor-employee | cnod-sub-cor-employee | 证券另类子公司从业人员信息数据 |
| cnod-fund-broker-credit-rating-age-employee | cnod-credit-rating-age-employee | 证券市场资信评级机构从业人员信息数据 |
| cnod-fund-broker-msic-employee | cnod-msic-employee | 证券投资咨询机构从业人员信息数据 |
| cnod-fund-broker-asset-management-cor-employee | cnod-asset-management-cor-employee | 证券资产管理公司从业人员信息数据 |
| cnod-finance-basic | cnod-bank-finance-basic | 金融业工商注册企业基本信息数据 |

## 新鲜度存疑（7 个数据集 / 12 条记录，止期距今超过 18 个月）

> 其中 5 条是别名副本，正本另有记录。按「有多少份数据停更」估算时请用数据集数。

这些记录写着「至今」，但页面给了明确止期且已过去一年半以上。**原 coverage 记载未改动**，页面原文见 `coverage_page`。

| 名称 | 档案记载 | 页面原文 | 滞后 | 优先级 |
|---|---|---|---|---|
| [制造业工商注册企业数量统计数据](https://www.cnopendata.com/data/m/regional/data-on-companies-registered-in-the-manufacturing-industry.html) | 1950-至今 | 1950-2021.12.31 | 58 个月 | P3 |
| [中国能源年鉴地区能源平衡表数据](https://www.cnopendata.com/data/m/regional/hjtjnj.html) | 1997-至今 | 1998~2022 | 46 个月 | P3 |
| [证券公司从业人员信息数据](https://www.cnopendata.com/data/m/fund%26broker/securities-firm-employee.html) `别名` | 2003 至今 | 2003.08-2023.5.30 | 41 个月 | P3 |
| [证券另类子公司从业人员信息数据](https://www.cnopendata.com/data/m/fund%26broker/sub%26cor-employee.html) `别名` | 2003 至今 | 2003.08-2023.5.30 | 41 个月 | P3 |
| [证券市场资信评级机构从业人员信息数据](https://www.cnopendata.com/data/m/fund%26broker/credit-rating%26age-employee.html) `别名` | 2003 至今 | 2003.08-2023.5.30 | 41 个月 | P3 |
| [证券投资咨询机构从业人员信息数据](https://www.cnopendata.com/data/m/fund%26broker/MSIC-employee.html) `别名` | 2003 至今 | 2003.08-2023.5.30 | 41 个月 | P3 |
| [证券资产管理公司从业人员信息数据](https://www.cnopendata.com/data/m/fund%26broker/asset-management%26cor-employee.html) `别名` | 2003 至今 | 2003.08-2023.5.30 | 41 个月 | P3 |
| [纽约时报新闻文本数据](https://www.cnopendata.com/data/m/international-news/nyt.html) | 1851-至今 | 1851~2024.7.31 | 27 个月 | P1 |
| [缺陷产品召回数据](https://www.cnopendata.com/data/m/ESG/recall-of-defective-products.html) | 2004年至今 | 汽车： 2007.11-2024.09.14 ；消费品： 2008.07-2024.09.12 | 25 个月 | P2 |
| [中国海关统计数据](https://www.cnopendata.com/data/m/customs/china-customs-statistics.html) | 1981年至今 | 1981-2024.11 | 23 个月 | P2 |
| [中国全部土地交易数据](https://www.cnopendata.com/data/m/land/land-transaction-data.html) | 2000年至今 | 2000~2024 | 22 个月 | P2 |
| [政府采购公告数据](https://www.cnopendata.com/data/m/tender%26bid/gov-pro-gg.html) | 2013年至今 | 基本信息表、公告源文件：已更新至 2024年12月 ；其他详细公告表：字段全新升级中 | 22 个月 | P2 |

## 频率口径待确认（6 条，页面两个口径多指不同交付版本）

页面「更新频率」写低频（多为年度），同一页「时间区间」却标「实时更新」。
**档案的 `frequency` 未改动**（原样抄录），证据见 `freq_note`。
取证结论：这多半不是互斥事实，而是同一系列下的不同交付版本——
批量历史库按年度滚动更新，实时/增量通道单独售卖（如股吧 API 每 30 分钟增量抓取）。
所以**排期应分两档**：批量库按年度，实时需求走 API；不能一律按年度，也不能一律改日频。

> 逐条取证见 C:/项目/deliverables/数据源档案_CnOpenData_20261021/频率口径取证_待批.md（归档副本，非本目录内文件）

| 名称 | 优先级 | 档案频率 | 页面口径证据 |
|---|---|---|---|
| [A股上市公司股吧文本数据](https://www.cnopendata.com/data/m/listedcompany-special/chinese-listed-company-stocks-comment.html) | P0 | 年度 | 页面时间区间写「截至2025（实时更新）」，与更新频率「年度」口径不一致（待确认，多为不同交付版本） |
| [雪球网上市公司个人投资者关注和实盘模拟盘交易数据](https://www.cnopendata.com/data/m/listedcompany-special/listed-company-transactions.html) | P0 | 年度 | 页面时间区间写「截至2025年12月31日 （实时更新中）」，与更新频率「年度」口径不一致（待确认，多为不同交付版本） |
| [雪球网上市公司个人投资者发帖实时更新数据](https://www.cnopendata.com/data/m/listedcompany-special/A-listedco-investorpost.html) | P0 | 年度 | 页面时间区间写「截至 2025年12月31日 （实时更新中）」，与更新频率「年度」口径不一致（待确认，多为不同交付版本） |
| [A股上市公司行情数据](https://www.cnopendata.com/data/m/listedcompany-basic/A-market-quotes.html) | P0 | 年度 | 页面时间区间写「1990.12~2026.6 （实时更新，各表区间略有差异）」，与更新频率「年度」口径不一致（待确认，多为不同交付版本） |
| [美股上市公司雪球投资者发帖评论数据](https://www.cnopendata.com/data/m/Us-listed-company/US-listedco-investorpost.html) | P0 | 年度 | 页面时间区间写「2026年-（实时更新）」，与更新频率「年度」口径不一致（待确认，多为不同交付版本） |
| [港股上市公司雪球投资者发帖评论数据](https://www.cnopendata.com/data/m/hongkong/HK-listedco-investorpost.html) | P0 | 年度 | 页面时间区间写「2026年-（实时更新）」，与更新频率「年度」口径不一致（待确认，多为不同交付版本） |

## 全量索引（按分类组）

### API商店（29 条）

| 名称 | 分类 | 覆盖 | 量级 | 更新 | 优先级 | 获取 |
|---|---|---|---|---|---|---|
| [股吧回帖实时数据查询 API](https://www.cnopendata.com/api-store/guba-reply) | 另类数据与文本语料 | 仅针对发帖时间在最近 7 天内 的帖子进行回帖数据抓取（每 12 小时执行一次全量遍历）；页面未标注数据起始年份 | 未标注（每 12 小时一轮遍历） | 实时 | P0 | api |
| [股吧帖子实时数据查询 API](https://www.cnopendata.com/api-store/guba-posts) | 另类数据与文本语料 | 接口默认仅提供近2年的历史发帖数据（页面未标注起始年份） | 未标注 | 实时 | P0 | api |
| [雪球帖子实时数据查询 API](https://www.cnopendata.com/api-store/xueqiu-posts) | 另类数据与文本语料 | 从 2025 年开始采集；可回溯获取一定历史数据 | 未标注 | 实时 | P0 | api |
| [上市公司帖子实时数据查询 API](https://www.cnopendata.com/api-store/ssgsxxxx-posts) | 另类数据与文本语料 | 上市日期：回溯至中国股市最早的上市公司；员工人数、注册资本：随年报更新，通常为最新报告期数据。 | 未标注 | 日 | P1 | api |
| [财经网站新闻实时数据查询 API](https://www.cnopendata.com/api-store/financial-website-news) | 另类数据与文本语料 | 接口默认仅提供近2年的历史数据（页面未标注起始年份） | 未标注 | 实时 | P1 | api |
| [ETF 基础信息数据API](https://www.cnopendata.com/api-store/etf-basic) | 指数与板块 | 设立日期：各 ETF 成立之日 | 未标注 | 不定期 | P1 | api |
| [ETF 复权因子数据API](https://www.cnopendata.com/api-store/etf-adjustment-factor) | 指数与板块 | 全历史交易日，可回溯至基金成立之日。 | 未标注 | 日 | P1 | api |
| [ETF 实时日线数据API](https://www.cnopendata.com/api-store/etf-real-time-daily) | 指数与板块 | 实时行情：交易日 9:30 – 15:00 期间持续更新。非交易日：返回最近一个交易日的收盘行情 | 未标注 | 实时 | P1 | api |
| [ETF 日线行情数据API](https://www.cnopendata.com/api-store/etf-daily-ohlcv) | 指数与板块 | 全历史交易日，历史数据超过 10 年。 | 未标注 | 日 | P1 | api |
| [A股上市公司利润表数据API](https://www.cnopendata.com/api-store/listedco-income) | 沪深股票 | 可回溯至各公司上市以来的全部报告期。 | 近百个科目 | 季度 | P1 | api |
| [A股上市公司定期报告数据API](https://www.cnopendata.com/api-store/ashare-periodic-rep) | 沪深股票 | 可回溯至2000年至今的全部公告记录。 | 未标注 | 不定期 | P1 | api |
| [A股上市公司每日指数数据](https://www.cnopendata.com/api-store/ashare-daily-indicator) | 沪深股票 | 全历史交易日数据，每个交易日 15:00～17:00 之间更新当日数据 | 未标注 | 日 | P1 | api |
| [A股上市公司现金表数据API](https://www.cnopendata.com/api-store/listedco-cashflow) | 沪深股票 | 可回溯至各公司上市以来的全部报告期。 | 未标注 | 季度 | P1 | api |
| [A股上市公司资产负债表数据API](https://www.cnopendata.com/api-store/listedco-balance) | 沪深股票 | 可回溯至各公司上市以来的全部报告期。 | 120+ 个科目 | 季度 | P1 | api |
| [A股基础信息数据API](https://www.cnopendata.com/api-store/ashare-basic-api) | 沪深股票 | 上市日期：回溯至中国股市最早的上市公司（1990年12月至今）；退市日期：包含所有已退市公司的退市记录 | 未标注 | 日 | P1 | api |
| [港股上市公司基础信息数据API](https://www.cnopendata.com/api-store/hkshare-basic-api) | 港股数据 | 上市日期：回溯至港股股市最早的上市公司；退市日期：包含所有已退市公司的退市记录 | 未标注 | 日 | P1 | api |
| [港股上市公司定期报告数据API](https://www.cnopendata.com/api-store/hkshare-periodic-rep) | 港股数据 | 可回溯至2005年至今的全部公告记录。 | 未标注 | 不定期 | P1 | api |
| [港股上市公司实时日线API](https://www.cnopendata.com/api-store/hkshare-d1) | 港股数据 | 实时获取当日开盘以来的日内行情数据。 | 未标注 | 实时 | P1 | api |
| [港股上市公司日线行情API](https://www.cnopendata.com/api-store/hkshare-d2) | 港股数据 | 可覆盖各股票上市以来的全部日线数据 | 未标注 | 日 | P1 | api |
| [港股上市公司财务报表数据API](https://www.cnopendata.com/api-store/hk-financials) | 港股数据 | 回溯至港股股市最早的上市公司 | 未标注 | 季度 | P1 | api |
| [中国二手房成交数据API](https://www.cnopendata.com/api-store/resold-apartment-turnover) | 另类数据与文本语料 | 支持回溯至历史成交记录，当前版本以2025年6月前成交数据 | 四百余万条 | 年度 | P2 | api |
| [中国土地交易数据API](https://www.cnopendata.com/api-store/land-result-base-info) | 另类数据与文本语料 | 供地结果基本信息表：2000年-2024年；供地结果分期支付约定信息表：2000年-2024年；土地出租表：2000年-2019年；土地抵押表：2000年-2019年；土地转让表：2000年-2019年 | 未标注 | 年度 | P2 | api |
| [中国法拍房数据](https://www.cnopendata.com/api-store/fapaifang-api) | 另类数据与文本语料 | 拍卖信息：2012年-2025年；其中拍卖须知内容时间截至2022年，后续年份不再更新。 | 六百三十四万条 | 年度 | P2 | api |
| [美国历史未偿公共债务余额数据API](https://www.cnopendata.com/api-store/historical-debt-outstanding) | 美股与全球市场 | 起始年份：1789 年（美国第一个财政年度）；结束年份：当前最近一个完整财政年度 | 每年一条记录 | 年度 | P2 | api |
| [美国每日国债未偿余额数据API](https://www.cnopendata.com/api-store/debt-to-the-penny) | 美股与全球市场 | 公共债务总额：1993 年 1 月 4 日起，每日数据；债务分项明细：每日 2005 年 4 月 4 日起、月度 2001 年 9 月 30 日—2005 年 3 月 31 日、年度（财年）1997 年 9 月 30 日—2001 年 9 月 30 日 | 每日一条 | 日 | P2 | api |
| [美国联邦债务月度明细数据API](https://www.cnopendata.com/api-store/schedules-of-federal-debt) | 美股与全球市场 | 起始年份：2005年4月起完整月度数据。 | 每月一条变动记录 | 月 | P2 | api |
| [中国二手房售卖数据API](https://www.cnopendata.com/api-store/resold-apartment-sell) | 另类数据与文本语料 | 挂牌时间：当前版本以2025年6月前发布的挂牌信息为主。 | 四百余万条（页面更新历史自述） | 年度 | P3 | api |
| [中国各城市新房数据](https://www.cnopendata.com/api-store/apartment-china) | 另类数据与文本语料 | 2010-2023.6 | 未标注 | 年度 | P3 | api |
| [中国小区信息数据](https://www.cnopendata.com/api-store/housing-estate-china) | 另类数据与文本语料 | 截至2024年11月 | 28 个省级行政区、102 个重点城市 | 年度 | P3 | api |

### SSRD上市公司与资本市场（112 条）

| 名称 | 分类 | 覆盖 | 量级 | 更新 | 优先级 | 获取 |
|---|---|---|---|---|---|---|
| [A股上市公司股吧文本数据](https://www.cnopendata.com/data/m/listedcompany-special/chinese-listed-company-stocks-comment.html) | A股上市公司另类数据 | 截至2025（实时更新） | 发帖7.78亿条、回帖15.20亿条，用户超2600万 | 年度 | P0 | direct_sale |
| [A股上市公司股吧文本统计数据](https://www.cnopendata.com/data/m/listedcompany-special/guba-stat.html) | A股上市公司另类数据 | 发帖时间 1988-2024，回帖时间 2007-2024 | 发帖3.3亿条、回帖4.2亿条 | 年度 | P0 | direct_sale |
| [雪球网上市公司个人投资者关注和实盘模拟盘交易数据](https://www.cnopendata.com/data/m/listedcompany-special/listed-company-transactions.html) | A股上市公司另类数据 | 截至2025年12月31日（实时更新中） | 未标注 | 年度 | P0 | direct_sale |
| [雪球网上市公司个人投资者发帖实时更新数据](https://www.cnopendata.com/data/m/listedcompany-special/A-listedco-investorpost.html) | A股上市公司另类数据 | 截至2025年12月31日（实时更新中） | 未标注 | 年度 | P0 | direct_sale |
| [雪球网上市公司新闻实时更新数据](https://www.cnopendata.com/data/m/listedcompany-special/xueqiu-news.html) | A股上市公司另类数据 | 截至2025年12月31日（实时更新中） | 未标注 | 实时 | P0 | direct_sale |
| [A股上市公司行情数据](https://www.cnopendata.com/data/m/listedcompany-basic/A-market-quotes.html) | A股上市公司基础数据 | 1991年至今 | 未标注 | 年度 | P0 | direct_sale |
| [美股上市公司雪球投资者发帖评论数据](https://www.cnopendata.com/data/m/Us-listed-company/US-listedco-investorpost.html) | 美股上市公司数据 | 未标注 | 未标注 | 年度 | P0 | direct_sale |
| [中国上市公司股票复权行情数据](https://www.cnopendata.com/data/m/trading/gpfqhq.html) | 股票和债券交易数据 | 未标注（沪深京全历史口径） | 未标注 | 年度 | P0 | direct_sale |
| [中国上市公司股票未复权行情数据](https://www.cnopendata.com/data/m/trading/clcnrr.html) | 股票和债券交易数据 | 未标注（沪深京全历史口径） | 6类行情表 | 年度 | P0 | direct_sale |
| [港股上市公司雪球投资者发帖评论数据](https://www.cnopendata.com/data/m/hongkong/HK-listedco-investorpost.html) | 香港上市公司数据 | 未标注 | 未标注 | 年度 | P0 | direct_sale |
| [A股上市公司分析师研报及评论数据](https://www.cnopendata.com/data/m/listedcompany-special/report.html) `别名` | A股上市公司另类数据 | 个股研究报告基本信息表：2021.07~2026.1.31；个股研究报告评论表：2021.07~2024.06 | 未标注 | 年度 | P1 | direct_sale |
| [A股上市公司分析师研报文本数据](https://www.cnopendata.com/data/m/listedcompany-special/report-all.html) `别名` | A股上市公司另类数据 | 2006-2024年初 | 未标注 | 年度 | P1 | direct_sale |
| [A股上市公司及其前十大股东经营范围数据](https://www.cnopendata.com/data/m/listedcompany-special/listedco-jyfw.html) | A股上市公司另类数据 | 截至2023年底 | 265485行（经营范围表）、383316行（前十大股东 | 年度 | P1 | direct_sale |
| [A股上市公司微信公众号全文数据](https://www.cnopendata.com/data/m/listedcompany-special/wxgzh.html) | A股上市公司另类数据 | 截至2025年6月30日 | 未标注 | 不定期 | P1 | direct_sale |
| [A股上市公司投资者关系管理数据](https://www.cnopendata.com/data/m/listedcompany-special/chinese-listed-companies-and-investor-relations.html) | A股上市公司另类数据 | 上交所e互动：2012-2024.02及2025.04-2025.05；深交所互动易：2011-2025.05；全景网：2011-2025.06；投资者关系活动记录表：2016-2025.05 | 未标注 | 年度 | P1 | direct_sale |
| [A股上市公司机构调研数据](https://www.cnopendata.com/data/m/listedcompany-special/listedco-survey.html) | A股上市公司另类数据 | 截止2025.06 | 未标注 | 年度 | P1 | direct_sale |
| [A股上市公司消费者投诉文本数据](https://www.cnopendata.com/data/m/listedcompany-special/ashare-complaint.html) | A股上市公司另类数据 | 2018-2024年 | 332万条 | 年度 | P1 | direct_sale |
| [A股上市公司知乎发帖文本数据](https://www.cnopendata.com/data/m/listedcompany-special/ashare-zhihu-post.html) | A股上市公司另类数据 | 截止2026年 | 未标注 | 年度 | P1 | direct_sale |
| [A股上市公司社交媒体账号数据](https://www.cnopendata.com/data/m/listedcompany-special/chinese-listed-companies-opa.html) | A股上市公司另类数据 | 截至2025年6月 | 未标注 | 年度 | P1 | direct_sale |
| [A股上市公司管理层讨论与分析数据](https://www.cnopendata.com/data/m/listedcompany-special/listedco-MD%26A.html) | A股上市公司另类数据 | 2001-2024 | 未标注 | 年度 | P1 | direct_sale |
| [A股上市公司股票高频交易数据](https://www.cnopendata.com/data/m/listedcompany-special/listedco-gpjy.html) | A股上市公司另类数据 | 2017-2025（可根据需要进行更新） | 未标注 | 不定期 | P1 | direct_sale |
| [A股上市公司高管出生地出生日期数据](https://www.cnopendata.com/data/m/listedcompany-special/listedco-ggcs.html) | A股上市公司另类数据 | 2007-2023 | 未标注 | 年度 | P1 | direct_sale |
| [A股上市公司停复牌信息数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-suspend.html) | A股上市公司基础数据 | 未标注 | 2张表 | 年度 | P1 | direct_sale |
| [A股上市公司全部公告数据](https://www.cnopendata.com/data/m/listedcompany-basic/listed-co-announcement.html) | A股上市公司基础数据 | 未标注 | 29类公告 | 年度 | P1 | direct_sale |
| [A股上市公司基本信息数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-jbxx.html) | A股上市公司基础数据 | 截至2026年7月 | 未标注 | 年度 | P1 | direct_sale |
| [A股上市公司大宗交易数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-dzjy.html) | A股上市公司基础数据 | 2002年3月-2025年6月 | 未标注 | 年度 | P1 | direct_sale |
| [A股上市公司股东信息数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-shareholder.html) | A股上市公司基础数据 | 未标注（历年） | 未标注 | 年度 | P1 | direct_sale |
| [A股上市公司财务报表数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-freport.html) | A股上市公司基础数据 | 未标注（含年报/季报/半年报） | 5张表 | 年度 | P1 | direct_sale |
| [A股上市金融机构财务报表数据](https://www.cnopendata.com/data/m/listedcompany-basic/Fin-FS.html) `别名` | A股上市公司基础数据 | 更新至2025年3月 | 5张表 | 年度 | P1 | direct_sale |
| [中国上市公司基本信息数据](https://www.cnopendata.com/data/m/listedcompany-basic/equ.html) | A股上市公司基础数据 | 1992年至今 | 4类 | 年度 | P1 | direct_sale |
| [美股上市公司交易日历数据](https://www.cnopendata.com/data/m/Us-listed-company/Us-stocks-basic-transaction.html) | 美股上市公司数据 | 未标注 | 未标注 | 年度 | P1 | direct_sale |
| [美股上市公司基础信息数据](https://www.cnopendata.com/data/m/Us-listed-company/Us-stocks-basic.html) | 美股上市公司数据 | 未标注 | 未标注 | 年度 | P1 | direct_sale |
| [美股上市公司行情数据](https://www.cnopendata.com/data/m/Us-listed-company/Us-stocks-basic-market-conditions.html) | 美股上市公司数据 | 未标注 | 未标注 | 年度 | P1 | direct_sale |
| [美股上市公司财务报表数据](https://www.cnopendata.com/data/m/Us-listed-company/Us-stocks-finance.html) | 美股上市公司数据 | 截至2025年9月 | 4张表 | 年度 | P1 | direct_sale |
| [ETF基准指数数据](https://www.cnopendata.com/data/m/trading/eft-jzzs.html) | 股票和债券交易数据 | 未标注（覆盖中证、国证、MSCI等） | 未标注 | 按需 | P1 | direct_sale |
| [ETF基础信息数据](https://www.cnopendata.com/data/m/trading/etf-basic-information.html) | 股票和债券交易数据 | 2004-2024年 | 未标注 | 不定期 | P1 | direct_sale |
| [ETF复权因子数据](https://www.cnopendata.com/data/m/trading/etf-fqyz.html) | 股票和债券交易数据 | 未标注 | 119万条 | 按需 | P1 | direct_sale |
| [ETF日线行情数据](https://www.cnopendata.com/data/m/trading/etf-rxhq.html) | 股票和债券交易数据 | 超10年历史 | 117万条 | 按需 | P1 | direct_sale |
| [中国上市公司交易信息数据](https://www.cnopendata.com/data/m/trading/trading.html) | 股票和债券交易数据 | 2002年至今 | 18张表 | 年度 | P1 | direct_sale |
| [中国债券行情数据](https://www.cnopendata.com/data/m/trading/mkt-bond.html) `别名` | 股票和债券交易数据 | 1993-2023年 | 未标注 | 年度 | P1 | direct_sale |
| [中国基金行情数据](https://www.cnopendata.com/data/m/trading/mkt-fund.html) `别名` | 股票和债券交易数据 | 1998-2023年 | 未标注 | 年度 | P1 | direct_sale |
| [中国股票指数数据](https://www.cnopendata.com/data/m/trading/zssj.html) | 股票和债券交易数据 | 1928年至今 | 未标注 | 年度 | P1 | direct_sale |
| [申万行业分类数据](https://www.cnopendata.com/data/m/trading/swhyfl.html) | 股票和债券交易数据 | 未标注 | 未标注 | 不定期 | P1 | direct_sale |
| [申万行业成分构成数据](https://www.cnopendata.com/data/m/trading/swhycfgc.html) | 股票和债券交易数据 | 1989年至今 | 未标注 | 不定期 | P1 | direct_sale |
| [股票指数基本信息数据](https://www.cnopendata.com/data/m/trading/zsjbxx.html) | 股票和债券交易数据 | 1927-2024年 | 未标注 | 不定期 | P1 | direct_sale |
| [股票指数成分和权重数据](https://www.cnopendata.com/data/m/trading/zscfhqz.html) | 股票和债券交易数据 | 未标注 | 未标注 | 不定期 | P1 | direct_sale |
| [股票指数行情数据](https://www.cnopendata.com/data/m/trading/zshq.html) | 股票和债券交易数据 | 未标注 | 11项指标 | 不定期 | P1 | direct_sale |
| [港股上市公司交易日历数据](https://www.cnopendata.com/data/m/hongkong/HK-trading-calendar.html) | 香港上市公司数据 | 未标注 | 未标注 | 按需 | P1 | direct_sale |
| [港股上市公司基础信息数据](https://www.cnopendata.com/data/m/hongkong/HK-basic-information.html) | 香港上市公司数据 | 1921年至今 | 未标注 | 按需 | P1 | direct_sale |
| [港股上市公司行情数据](https://www.cnopendata.com/data/m/hongkong/HK-market-quotes.html) | 香港上市公司数据 | 未标注 | 3个子表 | 按需 | P1 | direct_sale |
| [港股上市公司财务报表数据](https://www.cnopendata.com/data/m/hongkong/HK-financial-statements.html) | 香港上市公司数据 | 1999-2025年 | 4个子表 | 按需 | P1 | direct_sale |
| [A股上市公司专利及引用被引用数据](https://www.cnopendata.com/data/m/listedcompany-special/listed-co-patent-citation.html) | A股上市公司另类数据 | 发明公布按申请公布日统计：2000-2025；授权类按授权公布日统计：2000-2025 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司劳务外包报酬数据](https://www.cnopendata.com/data/m/listedcompany-special/listedco-lwwbbc.html) | A股上市公司另类数据 | 2012年至2023年 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司各类型行政处罚数据](https://www.cnopendata.com/data/m/listedcompany-special/listedco-varpenalty.html) | A股上市公司另类数据 | 2015-2023 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司商标数据](https://www.cnopendata.com/data/m/listedcompany-special/listed-company%20trademark.html) | A股上市公司另类数据 | 2000-2023年 | 126万条 | 年度 | P2 | direct_sale |
| [A股上市公司投服中心行权数据](https://www.cnopendata.com/data/m/listedcompany-special/listedco-exercise.html) | A股上市公司另类数据 | 截止2025年7月初 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司数字经济专利及引用被引用数据](https://www.cnopendata.com/data/m/listedcompany-special/ss-dig-eco-patyyby.html) | A股上市公司另类数据 | 1990-2025（发明公布按申请公布日；授权按授权公布日） | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司数字经济专利数量统计数据](https://www.cnopendata.com/data/m/listedcompany-special/ssdigeco-patstat.html) | A股上市公司另类数据 | 1990-2025年 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司数字经济专利申请与授权数据](https://www.cnopendata.com/data/m/listedcompany-special/ss-dig-eco-pat.html) | A股上市公司另类数据 | 1990~2025年 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司标准数据](https://www.cnopendata.com/data/m/listedcompany-special/listedco-standard.html) | A股上市公司另类数据 | 1963.09~2026.07 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司现场检查随机抽查数据](https://www.cnopendata.com/data/m/listedcompany-special/listed-random-check.html) | A股上市公司另类数据 | 2016-2025.2.17 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司绿色专利及引用被引用数据](https://www.cnopendata.com/data/m/listedcompany-special/listed-companies-green-patent-citation.html) | A股上市公司另类数据 | 1990-2024年 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司网络安全岗位招聘数据](https://www.cnopendata.com/data/m/listedcompany-special/Ashare-cyber-security-recruitment.html) | A股上市公司另类数据 | 2014-2024 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司裁判文书数据](https://www.cnopendata.com/data/m/listedcompany-special/listed-co-judgement-doc.html) | A股上市公司另类数据 | 2014-2020 | 未标注 | 年度 | P2 | direct_sale |
| [保荐代表人信息数据](https://www.cnopendata.com/data/m/listedcompany-special/bjdbr.html) | A股上市公司另类数据 | 2003.08~2025.06 | 未标注 | 年度 | P2 | direct_sale |
| [注册会计师及事务所数据](https://www.cnopendata.com/data/m/listedcompany-special/cpasws.html) | A股上市公司另类数据 | 1986.08~2023.01 | 未标注 | 年度 | P2 | direct_sale |
| [证券公司从业人员信息数据](https://www.cnopendata.com/data/m/listedcompany-special/securities-firm-employee.html) | A股上市公司另类数据 | 2003.08-2023.5.30 | 未标注 | 年度 | P2 | direct_sale |
| [证券另类子公司从业人员信息数据](https://www.cnopendata.com/data/m/listedcompany-special/sub%26cor-employee.html) | A股上市公司另类数据 | 2003.08-2023.5.30 | 未标注 | 年度 | P2 | direct_sale |
| [证券市场资信评级机构从业人员信息数据](https://www.cnopendata.com/data/m/listedcompany-special/credit-rating%26age-employee.html) | A股上市公司另类数据 | 2003.08-2023.5.30 | 未标注 | 年度 | P2 | direct_sale |
| [证券投资咨询机构从业人员信息数据](https://www.cnopendata.com/data/m/listedcompany-special/MSIC-employee.html) | A股上市公司另类数据 | 2003.08-2023.5.30 | 未标注 | 年度 | P2 | direct_sale |
| [证券资产管理公司从业人员信息数据](https://www.cnopendata.com/data/m/listedcompany-special/asset-management%26cor-employee.html) | A股上市公司另类数据 | 2003.08-2023.5.30 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市企业数字化转型指数数据](https://www.cnopendata.com/data/m/listedcompany-basic/DCG.html) | A股上市公司基础数据 | 指标2007-2020年；评级2016-2020年 | 未标注 | 年度 | P2 | open |
| [A股上市公司业绩说明会数据](https://www.cnopendata.com/data/m/listedcompany-basic/EConf-Call.html) | A股上市公司基础数据 | 2016-2025年 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司业绩预告数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-yjpl.html) | A股上市公司基础数据 | 截至2025年12月31日 | 2张表 | 年度 | P2 | direct_sale |
| [A股上市公司主营业务详情数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-zyywxq.html) | A股上市公司基础数据 | 未标注 | 3张表 | 年度 | P2 | direct_sale |
| [A股上市公司交易所问询函数据](https://www.cnopendata.com/data/m/listedcompany-basic/comment-letter.html) | A股上市公司基础数据 | 至2025年6月30日 | 未标注 | 年度 | P2 | open |
| [A股上市公司供应链数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-gyl.html) | A股上市公司基础数据 | 2001-2025年 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司信息披露评分数据](https://www.cnopendata.com/data/m/listedcompany-basic/chinese-listed-company-information-disclosure-score.html) | A股上市公司基础数据 | 深交所2005-2024年；上交所2016-2024年 | 未标注 | 年度 | P2 | open |
| [A股上市公司关联交易数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-related-transaction.html) | A股上市公司基础数据 | 2000年-2026年6月 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司内部交易数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-in-transaction.html) | A股上市公司基础数据 | 2004年11月-2025年6月 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司分红送股数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-dividend.html) | A股上市公司基础数据 | 1990年-2025年6月30日 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司增发信息数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-add-issue.html) | A股上市公司基础数据 | 1994年3月-2025年6月 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司担保数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-guarantee.html) | A股上市公司基础数据 | 2001-2024年 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司招股说明书公告数据](https://www.cnopendata.com/data/m/listedcompany-basic/listed-co-zgsm.html) | A股上市公司基础数据 | 截至2026年7月 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司政府补助数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-zfbz.html) | A股上市公司基础数据 | 2006年以来 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司研报数据](https://www.cnopendata.com/data/m/listedcompany-basic/ybsj.html) | A股上市公司基础数据 | 2016年至今 | 2张表 | 年度 | P2 | direct_sale |
| [A股上市公司社会责任报告数据](https://www.cnopendata.com/data/m/listedcompany-basic/listed-co-shzr.html) | A股上市公司基础数据 | 截至2026年7月 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司经营月报数据](https://www.cnopendata.com/data/m/listedcompany-basic/ssgsjyyb.html) | A股上市公司基础数据 | 2007年以来 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司股权激励数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-equ-inc.html) | A股上市公司基础数据 | 2006年-2026年6月11日 | 2张表 | 年度 | P2 | direct_sale |
| [A股上市公司股票回购数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-repurchase.html) | A股上市公司基础数据 | 2005年6月-2025年6月 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司董监高信息数据](https://www.cnopendata.com/data/m/listedcompany-basic/listed-co-executive.html) | A股上市公司基础数据 | 1999年-2026年7月 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司诉讼仲裁数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-sszc.html) | A股上市公司基础数据 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司财务报告审计意见数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-audit.html) | A股上市公司基础数据 | 1990-2025年 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司重大合同数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-zdht.html) | A股上市公司基础数据 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司限售解禁数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-share-float.html) | A股上市公司基础数据 | 至2026年年中 | 未标注 | 年度 | P2 | direct_sale |
| [中国上市公司主营业务构成数据](https://www.cnopendata.com/data/m/listedcompany-basic/fdmt.html) | A股上市公司基础数据 | 2007年至今 | 3张表 | 年度 | P2 | direct_sale |
| [中国上市公司新质生产力指数数据](https://www.cnopendata.com/data/m/listedcompany-basic/xinzhi-A.html) | A股上市公司基础数据 | 2011-2022年 | 未标注 | 未标注 | P2 | open |
| [Layline美股内幕交易数据](https://www.cnopendata.com/data/m/Us-listed-company/Layline-insider-trading.html) | 美股上市公司数据 | 2003年年中起 | 未标注 | 日 | P2 | open |
| [美国上市公司Twitter账户详情数据](https://www.cnopendata.com/data/m/Us-listed-company/twitter.html) | 美股上市公司数据 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [美国上市公司全部报告数据](https://www.cnopendata.com/data/m/Us-listed-company/USLCAR.html) | 美股上市公司数据 | 1993-2023年 | 约2300万份 | 年度 | P2 | direct_sale |
| [美国上市公司财务报告信息披露质量数据](https://www.cnopendata.com/data/m/Us-listed-company/USLCAR-10-K-qd.html) | 美股上市公司数据 | 1950-2017年 | 未标注 | 年度 | P2 | direct_sale |
| [A股大盘指数每日指标数据](https://www.cnopendata.com/data/m/trading/dpzsmrzb.html) | 股票和债券交易数据 | 2004年1月2日-2025年6月30日 | 未标注 | 不定期 | P2 | direct_sale |
| [中国上市公司行情衍生数据](https://www.cnopendata.com/data/m/trading/hqys.html) | 股票和债券交易数据 | 2006年至今 | 未标注 | 年度 | P2 | direct_sale |
| [港股中概公司族谱数据](https://www.cnopendata.com/data/m/hongkong/ggzg-zp.html) | 香港上市公司数据 | 未标注 | 9张表 | 年度 | P2 | direct_sale |
| [A股上市公司会计利润与所得税费用调整过程数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-bp%26ite.html) | A股上市公司基础数据 | 2005-2022年 | 未标注 | 年度 | P3 | direct_sale |
| [A股上市公司公司债公告数据](https://www.cnopendata.com/data/m/listedcompany-basic/listed-co-gsz.html) | A股上市公司基础数据 | 截至2026年7月 | 未标注 | 年度 | P3 | direct_sale |
| [A股上市公司员工构成数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-yggc.html) | A股上市公司基础数据 | 2004年至今 | 未标注 | 年度 | P3 | direct_sale |
| [A股上市公司股东大会公告数据](https://www.cnopendata.com/data/m/listedcompany-basic/listed-co-gddh.html) | A股上市公司基础数据 | 未标注 | 未标注 | 年度 | P3 | direct_sale |
| [A股上市公司高管薪酬数据](https://www.cnopendata.com/data/m/listedcompany-basic/listedco-exe-com.html) | A股上市公司基础数据 | 2004-2025年 | 2张表 | 年度 | P3 | direct_sale |
| [中国上市公司借款明细数据](https://www.cnopendata.com/data/m/listedcompany-basic/fdmt-jk.html) | A股上市公司基础数据 | 未标注（按报告期） | 未标注 | 年度 | P3 | direct_sale |
| [中国上市公司投资理财数据](https://www.cnopendata.com/data/m/listedcompany-basic/equ_invest_finance.html) | A股上市公司基础数据 | 未标注 | 未标注 | 年度 | P3 | direct_sale |
| [中国上市公司税费情况数据](https://www.cnopendata.com/data/m/listedcompany-basic/fdmt-tax.html) | A股上市公司基础数据 | 2016年至今 | 2张表 | 年度 | P3 | direct_sale |

### 新闻舆情与文本语料（56 条）

| 名称 | 分类 | 覆盖 | 量级 | 更新 | 优先级 | 获取 |
|---|---|---|---|---|---|---|
| [中国财经报纸电子报文本数据](https://www.cnopendata.com/data/m/financial-news/cjbkxw.html) | 财经新闻舆情 | 至2025年底 | 超1449万条 | 实时 | P0 | direct_sale |
| [中国财经文本语料数据](https://www.cnopendata.com/data/m/financial-news/finance-corpus.html) | 财经新闻舆情 | 未标注 | 1.1亿条 | 实时 | P0 | direct_sale |
| [中国财经新闻网站文本数据](https://www.cnopendata.com/data/m/financial-news/cjwzxw.html) | 财经新闻舆情 | 未标注 | 超3600万条 | 实时 | P0 | direct_sale |
| [舆情云数据](https://www.cnopendata.com/data/m/financial-news/public-opinion.html) | 财经新闻舆情 | 未标注 | 日均采集4亿+条 | 日 | P0 | direct_sale |
| [华尔街日报新闻文本数据](https://www.cnopendata.com/data/m/international-news/wsj-news.html) | 国际新闻舆情 | 1997-2025 | 未标注 | 年度 | P1 | direct_sale |
| [纽约时报新闻文本数据](https://www.cnopendata.com/data/m/international-news/nyt.html) | 国际新闻舆情 | 1851-至今 | 1530万条 | 年度 | P1 | direct_sale |
| [CCTV新闻联播文本数据](https://www.cnopendata.com/data/m/government-corpus/cctv-news.html) | 政务文本语料 | 2006-2025 | 未标注 | 季度 | P1 | direct_sale |
| [中国各城市日报数据](https://www.cnopendata.com/data/m/government-corpus/csrb.html) | 政务文本语料 | 未标注 | 300+城市 | 日 | P1 | direct_sale |
| [中国各省份官方报纸数据](https://www.cnopendata.com/data/m/government-corpus/cond-pro.html) | 政务文本语料 | 未标注 | 690万条 | 实时 | P1 | direct_sale |
| [中国消费者在线投诉文本数据](https://www.cnopendata.com/data/m/social-media-corpus/xf-tousu.html) | 社交媒体文本语料 | 未标注 | 未标注 | 月 | P1 | direct_sale |
| [A股上市公司业绩预告公告数据](https://www.cnopendata.com/data/m/financial-news/listed-co-yjyg.html) | 财经新闻舆情 | 未标注 | 未标注 | 年度 | P1 | direct_sale |
| [A股上市公司交易所监管措施数据](https://www.cnopendata.com/data/m/financial-news/regulatory-measures.html) | 财经新闻舆情 | 2013-2026 | 未标注 | 年度 | P1 | direct_sale |
| [中文金融情感词典](https://www.cnopendata.com/data/m/financial-news/chinese-dictionary-of-financial-sentiment.html) | 财经新闻舆情 | 未标注 | 未标注 | 不定期 | P1 | open |
| [CNN新闻文本数据](https://www.cnopendata.com/data/m/international-news/cnn-news.html) | 国际新闻舆情 | 2000-2022 | 未标注 | 年度 | P2 | direct_sale |
| [中国各地区政府工作报告文本数据](https://www.cnopendata.com/data/m/government-corpus/chinese-government-work-report.html) | 政务文本语料 | 2000-2025 | 9595份 | 不定期 | P2 | direct_sale |
| [中国各省市官方媒体微信公众号数据](https://www.cnopendata.com/data/m/government-corpus/china-provinces-cities-Wechat.html) | 政务文本语料 | 未标注 | 31省+300余地市 | 年度 | P2 | direct_sale |
| [中国地方政府规章数据](https://www.cnopendata.com/data/m/government-corpus/gov-dfzf.html) | 政务文本语料 | 1970-2026 | 未标注 | 年度 | P2 | direct_sale |
| [中国税务留言板信息数据](https://www.cnopendata.com/data/m/government-corpus/tax-onlinemessage.html) | 政务文本语料 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [中国纪委监察新闻报纸文本数据](https://www.cnopendata.com/data/m/government-corpus/jwjc-news.html) | 政务文本语料 | 2015-12 至 2024-12 | 未标注 | 年度 | P2 | direct_sale |
| [中国部门规章数据](https://www.cnopendata.com/data/m/government-corpus/gov-bm.html) | 政务文本语料 | 1980 至 2024-06 | 未标注 | 年度 | P2 | direct_sale |
| [商务部留言板信息数据](https://www.cnopendata.com/data/m/government-corpus/mofcom-onlinemessage.html) | 政务文本语料 | 2019 至 2026-02 | 未标注 | 年度 | P2 | direct_sale |
| [国家法律法规数据](https://www.cnopendata.com/data/m/government-corpus/law.html) | 政务文本语料 | 1950 至 2024-06 | 未标注 | 年度 | P2 | direct_sale |
| [谣言数据](https://www.cnopendata.com/data/m/government-corpus/rumor.html) | 政务文本语料 | 未标注 | 未标注 | 年度 | P2 | open |
| [Reddit全社区发帖评论数据](https://www.cnopendata.com/data/m/social-media-corpus/reddit-all.html) | 社交媒体文本语料 | 至2024 | 发帖29亿/评论189亿 | 年度 | P2 | direct_sale |
| [中国基金产品股吧文本数据](https://www.cnopendata.com/data/m/social-media-corpus/jijin-stocks-comment.html) `别名` | 社交媒体文本语料 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司股权激励公告数据](https://www.cnopendata.com/data/m/financial-news/listed-co-gqjl.html) | 财经新闻舆情 | 至2026-07 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司财报公告数据](https://www.cnopendata.com/data/m/financial-news/listed-co-report.html) | 财经新闻舆情 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [中国债券信用评级公告数据](https://www.cnopendata.com/data/m/financial-news/bond-credit-rating.html) `别名` | 财经新闻舆情 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [中国证券公司晨会报告数据](https://www.cnopendata.com/data/m/financial-news/ch-Reports.html) | 财经新闻舆情 | 2021-08-18 至 2026-01-31 | 未标注 | 年度 | P2 | direct_sale |
| [中国证券分析师宏观研究报告数据](https://www.cnopendata.com/data/m/financial-news/Macros-Reports.html) `别名` | 财经新闻舆情 | 2021-08 至 2025-10 | 未标注 | 年度 | P2 | direct_sale |
| [中国证券分析师盈利预测研究数据](https://www.cnopendata.com/data/m/financial-news/Profitforecast-Reports.html) `别名` | 财经新闻舆情 | 未标注（多时间截面） | 未标注 | 年度 | P2 | direct_sale |
| [中国证券分析师策略报告数据](https://www.cnopendata.com/data/m/financial-news/cl-Reports.html) `别名` | 财经新闻舆情 | 2021-08 至 2026-01 | 未标注 | 年度 | P2 | direct_sale |
| [中国证券分析师行业研究报告数据](https://www.cnopendata.com/data/m/financial-news/Industry-Reports.html) `别名` | 财经新闻舆情 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [中国财经报刊微信公众号数据](https://www.cnopendata.com/data/m/financial-news/cjbzgzh.html) | 财经新闻舆情 | 未标注 | 30家公众号 | 年度 | P2 | direct_sale |
| [中国财经新闻微信公众号数据](https://www.cnopendata.com/data/m/financial-news/cjwxgzh.html) | 财经新闻舆情 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [全球企业年报数据](https://www.cnopendata.com/data/m/international-news/annual%20reports.html) | 国际新闻舆情 | 1957-至今 | 未标注 | 年度 | P3 | direct_sale |
| [全球企业社会责任报告数据](https://www.cnopendata.com/data/m/international-news/Responsibility%20Report.html) | 国际新闻舆情 | 未标注（多年份多地区） | 未标注 | 年度 | P3 | direct_sale |
| [美国上市公司年度报告数据](https://www.cnopendata.com/data/m/international-news/USLCAR-10-K.html) | 国际新闻舆情 | 1993-2023 | 20.9万份 | 年度 | P3 | direct_sale |
| [美国企业社会责任报告数据](https://www.cnopendata.com/data/m/international-news/US%20Responsibility%20Report.html) | 国际新闻舆情 | 近30年 | 未标注 | 年度 | P3 | direct_sale |
| [英文学术期刊论文发表数据](https://www.cnopendata.com/data/m/international-news/English-Paper.html) | 国际新闻舆情 | 至2023-2025 | 未标注 | 年度 | P3 | direct_sale |
| [中国全学段教材数据](https://www.cnopendata.com/data/m/government-corpus/Chinese%20all-level%20textbooks.html) | 政务文本语料 | 未标注 | 未标注 | 不支持更新 | P3 | direct_sale |
| [中国各地区县志数据](https://www.cnopendata.com/data/m/government-corpus/cn-xz.html) | 政务文本语料 | 康乾时期至民国（含少量现代） | 六千多册 | 不支持更新 | P3 | direct_sale |
| [中国各地区家谱数据](https://www.cnopendata.com/data/m/government-corpus/cn-jp.html) | 政务文本语料 | 未标注 | 两千余册 | 不支持更新 | P3 | direct_sale |
| [中国对联数据](https://www.cnopendata.com/data/m/government-corpus/chinese-couplet.html) | 政务文本语料 | 未标注 | 未标注 | 不支持更新 | P3 | open |
| [中国条约数据](https://www.cnopendata.com/data/m/government-corpus/treaty.html) | 政务文本语料 | 1875-2018 | 未标注 | 年度 | P3 | direct_sale |
| [中国环境报数据](https://www.cnopendata.com/data/m/government-corpus/env-news.html) | 政务文本语料 | 2013-2023 | 未标注 | 年度 | P3 | direct_sale |
| [中餐食谱数据](https://www.cnopendata.com/data/m/government-corpus/recipe-of-chinese-cuisine.html) | 政务文本语料 | 未标注 | 未标注 | 不支持更新 | P3 | open |
| [唐诗宋词数据](https://www.cnopendata.com/data/m/government-corpus/poetry.html) | 政务文本语料 | 未标注 | 唐诗57613行/宋词21053行/宋诗254278行 | 年度 | P3 | open |
| [IMDb电影评论数据](https://www.cnopendata.com/data/m/social-media-corpus/moive-imdb.html) | 社交媒体文本语料 | 未标注 | 1841万部/814万评论/1265万人员 | 年度 | P3 | direct_sale |
| [Reddit社区WSB板块发帖评论数据](https://www.cnopendata.com/data/m/social-media-corpus/reddit.html) | 社交媒体文本语料 | 2017-12 至 2024 | 未标注 | 年度 | P3 | direct_sale |
| [豆瓣网电影评论数据](https://www.cnopendata.com/data/m/social-media-corpus/movie-rating.html) | 社交媒体文本语料 | 未标注 | 未标注 | 年度 | P3 | direct_sale |
| [A股上市公司IPO发行文本数据](https://www.cnopendata.com/data/m/financial-news/listedco-IPO-issuance.html) | 财经新闻舆情 | 2001-2026 | 未标注 | 年度 | P3 | direct_sale |
| [A股上市公司IPO问询函文本数据](https://www.cnopendata.com/data/m/financial-news/listedco-IPO-letter.html) | 财经新闻舆情 | 未标注 | 未标注 | 年度 | P3 | direct_sale |
| [A股上市公司IPO预披露文本数据](https://www.cnopendata.com/data/m/financial-news/listedco-IPO-pre.html) | 财经新闻舆情 | 2014-04 至 2026-06 | 未标注 | 年度 | P3 | direct_sale |
| [A股上市公司公司章程公告数据](https://www.cnopendata.com/data/m/financial-news/listed-co-gszc.html) | 财经新闻舆情 | 未标注 | 未标注 | 年度 | P3 | direct_sale |
| [中国专利文本数据](https://www.cnopendata.com/data/m/financial-news/patent-text-data.html) | 财经新闻舆情 | 1985-2025 | 超2300万条 | 年度 | P3 | direct_sale |

### 金融行业数据（61 条）

| 名称 | 分类 | 覆盖 | 量级 | 更新 | 优先级 | 获取 |
|---|---|---|---|---|---|---|
| [中国债券信用评级公告数据](https://www.cnopendata.com/data/m/bond/bond-credit-rating.html) | 债券数据 | 未标注 | 未标注 | 年度 | P1 | direct_sale |
| [中国债券发行主体信息数据](https://www.cnopendata.com/data/m/bond/bond-issuing-body.html) | 债券数据 | 2008 至 2022 | 未标注 | 年度 | P1 | direct_sale |
| [中国债券发行信息数据](https://www.cnopendata.com/data/m/bond/bond-issue.html) | 债券数据 | 2008 至 2022 | 未标注 | 年度 | P1 | direct_sale |
| [中国债券行情数据](https://www.cnopendata.com/data/m/bond/mkt-bond.html) | 债券数据 | 1993 至 2023 | 未标注 | 年度 | P1 | direct_sale |
| [A股上市公司分析师研报及评论数据](https://www.cnopendata.com/data/m/fund%26broker/report.html) | 券商与基金数据 | 未标注 | 未标注 | 年度 | P1 | direct_sale |
| [A股上市公司分析师研报文本数据](https://www.cnopendata.com/data/m/fund%26broker/report-all.html) | 券商与基金数据 | 2006 至 2024 初 | 未标注 | 年度 | P1 | direct_sale |
| [中国基金交易分仓与佣金明细数据](https://www.cnopendata.com/data/m/fund%26broker/fund-commission.html) | 券商与基金数据 | 未标注 | 未标注 | 年度 | P1 | direct_sale |
| [中国基金产品股吧文本数据](https://www.cnopendata.com/data/m/fund%26broker/jijin-stocks-comment.html) | 券商与基金数据 | 未标注 | 未标注 | 年度 | P1 | direct_sale |
| [中国基金行情数据](https://www.cnopendata.com/data/m/fund%26broker/mkt-fund.html) | 券商与基金数据 | 1998 至 2023 | 未标注 | 年度 | P1 | direct_sale |
| [中国证券分析师宏观研究报告数据](https://www.cnopendata.com/data/m/fund%26broker/Macros-Reports.html) | 券商与基金数据 | 2021-08 至 2025-10 | 未标注 | 年度 | P1 | direct_sale |
| [中国证券分析师盈利预测研究数据](https://www.cnopendata.com/data/m/fund%26broker/Profitforecast-Reports.html) | 券商与基金数据 | 未标注 | 未标注 | 年度 | P1 | direct_sale |
| [中国证券分析师策略报告数据](https://www.cnopendata.com/data/m/fund%26broker/cl-Reports.html) | 券商与基金数据 | 2021-08 至 2026-01 | 未标注 | 年度 | P1 | direct_sale |
| [中国证券分析师行业研究报告数据](https://www.cnopendata.com/data/m/fund%26broker/Industry-Reports.html) | 券商与基金数据 | 未标注 | 未标注 | 年度 | P1 | direct_sale |
| [公募基金净值数据](https://www.cnopendata.com/data/m/fund%26broker/fund-value.html) | 券商与基金数据 | 1999 至今 | 未标注 | 年度 | P1 | direct_sale |
| [公募基金列表数据](https://www.cnopendata.com/data/m/fund%26broker/funds-list.html) | 券商与基金数据 | 1991 至今 | 未标注 | 年度 | P1 | direct_sale |
| [公募基金持仓数据](https://www.cnopendata.com/data/m/fund%26broker/fund-holdings.html) | 券商与基金数据 | 2001 至 2025-06 | 未标注 | 年度 | P1 | direct_sale |
| [公募基金规模数据](https://www.cnopendata.com/data/m/fund%26broker/fund-size.html) | 券商与基金数据 | 1998 至今 | 未标注 | 年度 | P1 | direct_sale |
| [中国期货每日成交持仓排名数据](https://www.cnopendata.com/data/m/Futures/Futures%2Fcjpm.html) | 期货数据 | 2002 至 2025 | 2220 万条 | 不定期 | P1 | direct_sale |
| [中国期货行情数据](https://www.cnopendata.com/data/m/Futures/FuturesMarket.html) | 期货数据 | 1995 至今 | 330 万条 | 不定期 | P1 | direct_sale |
| [南华期货指数日线行情数据](https://www.cnopendata.com/data/m/Futures/FuturesMarket%2FNH.html) | 期货数据 | 2004-06 至今 | 20 万条 / 56 个指数 | 不定期 | P1 | direct_sale |
| [A股上市金融机构财务报表数据](https://www.cnopendata.com/data/m/bank/Fin-FS.html) | 银行与保险数据 | 按报告期至2025-03 | 未标注 | 年度 | P1 | direct_sale |
| [公募基金场内技术面因子数据](https://www.cnopendata.com/data/m/fund%26broker/fund-factor.html) | 券商与基金数据 | 2004 至 2025 | 67 项技术指标 | 年度 | P1 | direct_sale |
| [中国地方政府债券信息数据](https://www.cnopendata.com/data/m/bond/CNgovernment-bonds.html) | 债券数据 | 2009 至今 | 未标注 | 年度 | P2 | direct_sale |
| [中国证券公司晨会报告数据](https://www.cnopendata.com/data/m/fund%26broker/ch-Reports.html) `别名` | 券商与基金数据 | 2021-08-18 至 2026-01-31 | 未标注 | 年度 | P2 | direct_sale |
| [公募基金分红数据](https://www.cnopendata.com/data/m/fund%26broker/fund-dividends.html) | 券商与基金数据 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [中国期货交易日历数据](https://www.cnopendata.com/data/m/Futures/Futures%2Fjy.html) | 期货数据 | 1998 至今 | 4.5 万条，覆盖所有自然日 | 不定期 | P2 | direct_sale |
| [中国期货仓单日报数据](https://www.cnopendata.com/data/m/Futures/Futures%2Fcd.html) | 期货数据 | 2012-01-04 至 2025-06-24 | 165 万条，覆盖所有交易日 | 不定期 | P2 | direct_sale |
| [中国期货合约信息数据](https://www.cnopendata.com/data/m/Futures/Futures%2Fhy.html) | 期货数据 | 1995-04-17 至今 | 约 1 万条 | 不定期 | P2 | direct_sale |
| [中国期货每日结算参数数据](https://www.cnopendata.com/data/m/Futures/Futures%2Fjs.html) | 期货数据 | 2012-11 至 2025-06 | 75 万条 | 不定期 | P2 | direct_sale |
| [上海票据交易所票据承兑信息数据](https://www.cnopendata.com/data/m/bank/bill-acceptance-information.html) | 银行与保险数据 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [中国全部银行专项指标数据](https://www.cnopendata.com/data/m/bank/bank-zxzb.html) | 银行与保险数据 | 2012 至 2023-08 | 一千五百多家银行 | 不支持更新 | P2 | direct_sale |
| [中国全部银行十大借款人数据](https://www.cnopendata.com/data/m/bank/bank-sdjkr.html) | 银行与保险数据 | 2016 至 2023-08 | 数百家银行 | 不支持更新 | P2 | direct_sale |
| [中国全部银行对外投资信息数据](https://www.cnopendata.com/data/m/bank/bank-ofdi.html) | 银行与保险数据 | 截至2023-08-31 | 一千多家银行 | 年度 | P2 | direct_sale |
| [中国全部银行工商信息变更数据](https://www.cnopendata.com/data/m/bank/bank-change.html) | 银行与保险数据 | 截至2023-08-31 | 三千九百多家银行 | 年度 | P2 | direct_sale |
| [中国全部银行工商注册基本信息数据](https://www.cnopendata.com/data/m/bank/bank-basic.html) | 银行与保险数据 | 未标注 | 三千九百余家银行 | 年度 | P2 | direct_sale |
| [中国全部银行网点数据](https://www.cnopendata.com/data/m/bank/bank-branch.html) | 银行与保险数据 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [中国全部银行股东信息数据](https://www.cnopendata.com/data/m/bank/bank-shareholder.html) | 银行与保险数据 | 未标注 | 3400+家银行 | 年度 | P2 | direct_sale |
| [中国全部银行财务报表数据](https://www.cnopendata.com/data/m/bank/bank-cfe.html) | 银行与保险数据 | 近十年 | 未标注 | 不支持更新 | P2 | direct_sale |
| [中国全部银行贷款五级分类数据](https://www.cnopendata.com/data/m/bank/bank-dkwjfl.html) | 银行与保险数据 | 2016 至 2023-08 | 近千家银行 | 不支持更新 | P2 | direct_sale |
| [中国理财产品数据](https://www.cnopendata.com/data/m/bank/chinese-wealth-management-product.html) | 银行与保险数据 | 2001 至 2021 | 未标注 | 年度 | P2 | direct_sale |
| [中国银行分支机构违规被处罚数据](https://www.cnopendata.com/data/m/bank/bank-penalty.html) | 银行与保险数据 | 2004 至 2020 | 未标注 | 年度 | P2 | direct_sale |
| [中国全部银行专利申请与授权数据](https://www.cnopendata.com/data/m/bank/bank-pat.html) | 银行与保险数据 | 1985 至 2024 | 未标注 | 年度 | P2 | direct_sale |
| [中国商业银行线上招聘数据](https://www.cnopendata.com/data/m/bank/bank-recruitment.html) | 银行与保险数据 | 2014-05 至 2025 | 未标注 | 年度 | P2 | direct_sale |
| [中国保险中介机构网点信息数据](https://www.cnopendata.com/data/m/bank/insurance-agc.html) | 银行与保险数据 | 未标注 | 未标注 | 年度 | P3 | open |
| [中国保险机构网点全集数据](https://www.cnopendata.com/data/m/bank/insurance.html) | 银行与保险数据 | 截止2020-05 | 未标注 | 年度 | P3 | direct_sale |
| [中国全部银行业务经营数据](https://www.cnopendata.com/data/m/bank/bank-ywjy.html) | 银行与保险数据 | 2018 至 2023-08 | 数百家银行 | 不支持更新 | P3 | direct_sale |
| [中国全部银行供应商客户数据](https://www.cnopendata.com/data/m/bank/bank-sx.html) | 银行与保险数据 | 截至2023-08-31 | 一千多家银行 | 不支持更新 | P3 | direct_sale |
| [中国全部银行分支机构数据](https://www.cnopendata.com/data/m/bank/bank-fzjg.html) | 银行与保险数据 | 截至2023-08-31 | 三千五百多家银行 | 不支持更新 | P3 | direct_sale |
| [中国全部银行贷款结构数据](https://www.cnopendata.com/data/m/bank/bank-dkjg.html) | 银行与保险数据 | 2017 至 2023-08 | 数百家银行 | 不支持更新 | P3 | direct_sale |
| [中国全部银行逾期贷款数据](https://www.cnopendata.com/data/m/bank/bank-overdueloan.html) | 银行与保险数据 | 未标注 | 数百家银行 | 不支持更新 | P3 | direct_sale |
| [中国邮政储蓄银行网点信息数据](https://www.cnopendata.com/data/m/bank/chinese-postal-savings-bank.html) | 银行与保险数据 | 未标注 | 逾 5 万条 | 年度 | P3 | direct_sale |
| [中国券商线上招聘数据](https://www.cnopendata.com/data/m/fund%26broker/qsacct.html) | 券商与基金数据 | 2014-05 至 2024 | 未标注 | 年度 | P3 | direct_sale |
| [金融业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/fund%26broker/finance-basic.html) `别名` | 券商与基金数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国全部银行专利及引用被引用数据](https://www.cnopendata.com/data/m/bank/bank-patyyby.html) | 银行与保险数据 | 未标注 | 未标注 | 年度 | P3 | direct_sale |
| [基金公司及其从业人员信息数据](https://www.cnopendata.com/data/m/fund%26broker/fund-and-practitioners-information.html) | 券商与基金数据 | 未标注 | 未标注 | 年度 | P3 | direct_sale |
| [证券公司从业人员信息数据](https://www.cnopendata.com/data/m/fund%26broker/securities-firm-employee.html) `别名` | 券商与基金数据 | 2003 至今 | 未标注 | 年度 | P3 | direct_sale |
| [证券另类子公司从业人员信息数据](https://www.cnopendata.com/data/m/fund%26broker/sub%26cor-employee.html) `别名` | 券商与基金数据 | 2003 至今 | 未标注 | 年度 | P3 | direct_sale |
| [证券市场资信评级机构从业人员信息数据](https://www.cnopendata.com/data/m/fund%26broker/credit-rating%26age-employee.html) `别名` | 券商与基金数据 | 2003 至今 | 未标注 | 年度 | P3 | direct_sale |
| [证券投资咨询机构从业人员信息数据](https://www.cnopendata.com/data/m/fund%26broker/MSIC-employee.html) `别名` | 券商与基金数据 | 2003 至今 | 未标注 | 年度 | P3 | direct_sale |
| [证券资产管理公司从业人员信息数据](https://www.cnopendata.com/data/m/fund%26broker/asset-management%26cor-employee.html) `别名` | 券商与基金数据 | 2003 至今 | 未标注 | 年度 | P3 | direct_sale |
| [金融业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/bank/finance-basic.html) | 银行与保险数据 | 1950 至 2024-10 | 未标注 | 年度 | P3 | direct_sale |

### 因子与事件数据（176 条）

| 名称 | 分类 | 覆盖 | 量级 | 更新 | 优先级 | 获取 |
|---|---|---|---|---|---|---|
| [A股上市公司环保处罚数据](https://www.cnopendata.com/data/m/ESG/listedco-envlpenalty.html) | ESG数据 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司社会责任评价数据](https://www.cnopendata.com/data/m/ESG/CSR-Report.html) | ESG数据 | 2010—2021年 | 未标注 | 年度 | P2 | open |
| [A股上市公司违规处罚数据](https://www.cnopendata.com/data/m/ESG/listedco-violation.html) | ESG数据 | 1994年4月—2026年5月 | 未标注 | 年度 | P2 | direct_sale |
| [ESG环保处罚信息数据](https://www.cnopendata.com/data/m/ESG/esg_env_punish.html) | ESG数据 | 2007年至今 | 未标注 | 按需 | P2 | direct_sale |
| [ESG舆情标签信息数据](https://www.cnopendata.com/data/m/ESG/esg_news.html) | ESG数据 | 2018年1月—2023年10月 | 未标注 | 按需 | P2 | direct_sale |
| [中国企业环保行政处罚数据](https://www.cnopendata.com/data/m/ESG/envl-penalty.html) | ESG数据 | 未标注（全国各级生态环境部门） | 未标注 | 年度 | P2 | direct_sale |
| [中国企业税务行政处罚数据](https://www.cnopendata.com/data/m/ESG/tax-penalty.html) | ESG数据 | 2000—2023年 | 未标注 | 年度 | P2 | direct_sale |
| [中国绿色制造名单数据](https://www.cnopendata.com/data/m/ESG/green-manuf.html) | ESG数据 | 未标注（工信部历批名单） | 未标注 | 年度 | P2 | direct_sale |
| [公司层面气候变化暴露指数](https://www.cnopendata.com/data/m/ESG/firm-level-climate-change-exposure.html) | ESG数据 | 2001—2020年；34国、1万+企业 | 未标注 | 不定期 | P2 | open |
| [市场监管总局产品质量监督抽查数据](https://www.cnopendata.com/data/m/ESG/product-quality-supervision.html) | ESG数据 | 2017年9月—2024年12月；32省市+2特区 | 未标注 | 年度 | P2 | direct_sale |
| [市场监管总局食品安全抽检数据](https://www.cnopendata.com/data/m/ESG/food-safety.html) | ESG数据 | 2014年—2025年3月 | 超500万条 | 年度 | P2 | direct_sale |
| [缺陷产品召回数据](https://www.cnopendata.com/data/m/ESG/recall-of-defective-products.html) | ESG数据 | 2004年至今 | 未标注 | 年度 | P2 | direct_sale |
| [中国专利分行业统计数据](https://www.cnopendata.com/data/m/patent/patent-stbyindustry.html) | 中国专利数据 | 1985—2024年 | 未标注（统计口径） | 年度 | P2 | direct_sale |
| [中国专利及引用被引用数据（世界版）](https://www.cnopendata.com/data/m/patent/patent-citation-global.html) | 中国专利数据 | 1985—2025年 | 未标注 | 年度 | P2 | direct_sale |
| [中国专利无效宣告数据](https://www.cnopendata.com/data/m/patent/china-invalidated-patent.html) | 中国专利数据 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [中国专利申请权和专利权转移数据](https://www.cnopendata.com/data/m/patent/Transfer-patent-right.html) | 中国专利数据 | 未标注（页面仅称时间跨度长） | 未标注 | 年度 | P2 | direct_sale |
| [中国人工智能专利申请与授权数据](https://www.cnopendata.com/data/m/patent/Key-figures-Application-rgzn.html) | 中国专利数据 | 1985—2024年 | 未标注 | 年度 | P2 | direct_sale |
| [中国全部专利申请与授权数据](https://www.cnopendata.com/data/m/patent/patent-innovation.html) | 中国专利数据 | 1985—2025年 | 未标注（全样本） | 年度 | P2 | direct_sale |
| [中国关键数字技术专利申请与授权数据](https://www.cnopendata.com/data/m/patent/Key-figures-Application.html) | 中国专利数据 | 1985—2024年 | 未标注 | 年度 | P2 | direct_sale |
| [中国绿色专利申请与授权数据](https://www.cnopendata.com/data/m/patent/green-patent-innovation.html) | 中国专利数据 | 1985—2024年 | 未标注 | 年度 | P2 | direct_sale |
| [中国高新技术企业专利统计数据](https://www.cnopendata.com/data/m/patent/patent-hte.html) | 中国专利数据 | 1985—2024年；31省近37万家高企 | 未标注专利条数 | 年度 | P2 | direct_sale |
| [AI岗位线上招聘数据](https://www.cnopendata.com/data/m/labour%26recruit/AI-recruitment.html) | 中国劳动招聘与就业数据 | 2014年5月—2024年 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司AI岗位线上招聘数据](https://www.cnopendata.com/data/m/labour%26recruit/listedco-AI-recruitment.html) | 中国劳动招聘与就业数据 | 2014年5月—2024年 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司员工评价数据](https://www.cnopendata.com/data/m/labour%26recruit/listedco-ygpj.html) | 中国劳动招聘与就业数据 | 2014—2023年 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司招聘数据](https://www.cnopendata.com/data/m/labour%26recruit/chinese-listed-companies-onlinerect.html) | 中国劳动招聘与就业数据 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [中国企业劳动争议行政处罚数据](https://www.cnopendata.com/data/m/labour%26recruit/lw-penalty.html) | 中国劳动招聘与就业数据 | 2014—2025年 | 未标注 | 年度 | P2 | direct_sale |
| [中国零工经济线上招聘数据](https://www.cnopendata.com/data/m/labour%26recruit/lgjjacct.html) | 中国劳动招聘与就业数据 | 2014年至今 | 超过1700万条 | 年度 | P2 | direct_sale |
| [线上招聘网站信息数据](https://www.cnopendata.com/data/m/labour%26recruit/recruitment.html) | 中国劳动招聘与就业数据 | 未标注（页面称时间跨度长） | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司及子公司基本信息扩展数据](https://www.cnopendata.com/data/m/industry_commerce/listed-companies-and-subsidiaries-data.html) | 中国工商注册企业全信息 | 2000—2024年 | 未标注 | 年度 | P2 | direct_sale |
| [中国产业链上下游行业映射数据](https://www.cnopendata.com/data/m/industry_commerce/industry-chain-mapping.html) | 中国工商注册企业全信息 | 未标注（按三级行业分类） | 未标注 | 不定期 | P2 | direct_sale |
| [中国国有企业名单数据](https://www.cnopendata.com/data/m/industry_commerce/SOE.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [中国工商注册企业财务报表数据](https://www.cnopendata.com/data/m/industry_commerce/ind-cfe.html) | 中国工商注册企业全信息 | 2013—2024年 | 数千万条/年（2022年约3600万条） | 年度 | P2 | direct_sale |
| [失信企业基本信息扩展数据](https://www.cnopendata.com/data/m/industry_commerce/untrustworthy-data.html) | 中国工商注册企业全信息 | 2013—2020年 | 未标注 | 年度 | P2 | direct_sale |
| [中国二手房交易数据](https://www.cnopendata.com/data/m/china-real-estate/chinese-second-hand-housing-transaction.html) | 中国房地产数据 | 28个省级行政区、90多个重点城市 | 未标注 | 年度 | P2 | direct_sale |
| [中国重点城市新房供应与成交详细数据](https://www.cnopendata.com/data/m/china-real-estate/housing-tx.html) | 中国房地产数据 | 20余个重点城市 | 未标注 | 月 | P2 | direct_sale |
| [外国在华专利及引用被引用数据](https://www.cnopendata.com/data/m/global-patent/patent-wg.html) | 全球专利数据 | 1985—2025年 | 未标注 | 年度 | P2 | direct_sale |
| [法拍房数据](https://www.cnopendata.com/data/m/judicial-auction/fapaifang.html) | 司法拍卖数据 | 2012—2025年 | 未标注 | 年度 | P2 | direct_sale |
| [股权司法拍卖数据](https://www.cnopendata.com/data/m/judicial-auction/equity-auction.html) | 司法拍卖数据 | 2016年4月—2025年6月30日 | 未标注（四张表） | 年度 | P2 | direct_sale |
| [A股上市公司土地交易数据](https://www.cnopendata.com/data/m/land/list-company-land-transaction.html) | 土地数据 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [中国全部土地交易数据](https://www.cnopendata.com/data/m/land/land-transaction-data.html) | 土地数据 | 2000年至今 | 未标注 | 年度 | P2 | direct_sale |
| [中国重点城市土地出让与受让详细数据表](https://www.cnopendata.com/data/m/land/housing-tx%2Ftd.html) | 土地数据 | 约90个重点城市 | 未标注 | 日 | P2 | direct_sale |
| [A股上市公司招投标数据](https://www.cnopendata.com/data/m/tender%26bid/chinese-listed-companies-ztb.html) | 招投标数据 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [政府采购公告数据](https://www.cnopendata.com/data/m/tender%26bid/gov-pro-gg.html) | 招投标数据 | 2013年至今 | 基础表1700万条+13张明细表 | 年度 | P2 | direct_sale |
| [政府采购意向公告数据](https://www.cnopendata.com/data/m/tender%26bid/gov-cgyx.html) | 招投标数据 | 2020—2024年 | 未标注 | 年度 | P2 | direct_sale |
| [政府采购数据](https://www.cnopendata.com/data/m/tender%26bid/government-procurement-data.html) | 招投标数据 | 2004年至今 | 未标注 | 年度 | P2 | direct_sale |
| [A股上市公司海关进出口交易明细数据](https://www.cnopendata.com/data/m/customs/A-hg.html) | 海关与国际贸易数据 | 2007—2016年 | 未标注 | 不支持更新 | P2 | direct_sale |
| [中国对外贸易指数数据](https://www.cnopendata.com/data/m/customs/china-indexes-of-foreign-trade.html) | 海关与国际贸易数据 | 2018年1月—2024年11月 | 未标注 | 年度 | P2 | direct_sale |
| [中国海关统计数据](https://www.cnopendata.com/data/m/customs/china-customs-statistics.html) | 海关与国际贸易数据 | 1981年至今 | 未标注（统计口径） | 年度 | P2 | direct_sale |
| [中国海关进出口交易明细数据](https://www.cnopendata.com/data/m/customs/CN-hg.html) | 海关与国际贸易数据 | 2000—2016年 | 未标注 | 不支持更新 | P2 | direct_sale |
| [境外投资企业（机构）名录数据](https://www.cnopendata.com/data/m/customs/overseas-investment-enterprise-list.html) | 海关与国际贸易数据 | 未标注 | 未标注 | 年度 | P2 | direct_sale |
| [中国工业企业绿色专利及引用被引用数据](https://www.cnopendata.com/data/m/ESG/chinese-industrial-enterprise-green-patent-citation.html) | ESG数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国污水处理厂数据](https://www.cnopendata.com/data/m/ESG/wwtp.html) | ESG数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国汽车能源消耗量数据](https://www.cnopendata.com/data/m/ESG/AMEC.html) | ESG数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国绿色专利及引用被引用数据](https://www.cnopendata.com/data/m/ESG/green-patyyby.html) | ESG数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国绿色低碳专利分地区统计数据](https://www.cnopendata.com/data/m/ESG/low-carbon-patent-stats.html) | ESG数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国绿色低碳专利及引用被引用数据](https://www.cnopendata.com/data/m/ESG/low-carbon-patent-citation.html) | ESG数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国绿色低碳专利申请与授权数据](https://www.cnopendata.com/data/m/ESG/low-carbon-patent-innovation.html) | ESG数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [全球紧急事件数据](https://www.cnopendata.com/data/m/ESG/emergency.html) | ESG数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [全球风电可持续发展能源项目信息数据](https://www.cnopendata.com/data/m/ESG/Windfarm.html) | ESG数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [再生资源回收企业信息数据](https://www.cnopendata.com/data/m/ESG/zszyhs.html) | ESG数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [工业产品生产许可证数据](https://www.cnopendata.com/data/m/ESG/licence-of-industrial-products.html) | ESG数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [微博微公益平台数据](https://www.cnopendata.com/data/m/ESG/wb-benefit.html) | ESG数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [生活垃圾焚烧厂数据](https://www.cnopendata.com/data/m/ESG/msw-incineration-plant.html) | ESG数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [电器电子产品有害物质声明数据](https://www.cnopendata.com/data/m/ESG/dzcpyhwzsm.html) | ESG数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [轻松筹公益捐赠项目数据](https://www.cnopendata.com/data/m/ESG/donation.html) | ESG数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国AI人工智能专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/ai-patentyyby.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国AI人工智能专利申请与授权数据](https://www.cnopendata.com/data/m/patent/ai-patentbasic.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国专利权人工商股东信息数据](https://www.cnopendata.com/data/m/patent/Patent-shareholder.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国人工智能专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/Key-figures-Quoted-rgzn.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国元宇宙专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/Key-figures-Quoted-yyz.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国元宇宙专利申请与授权数据](https://www.cnopendata.com/data/m/patent/Key-figures-Application-yyz.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国全部专利详细地址数据](https://www.cnopendata.com/data/m/patent/china-patent-address.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国关键数字技术专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/Key-figures-Quoted.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国区块链专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/Key-figures-Quoted-qkl.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国区块链专利申请与授权数据](https://www.cnopendata.com/data/m/patent/Key-figures-Application-qkl.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国国家著录项目专利数据](https://www.cnopendata.com/data/m/patent/chgjzlxmzl.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国工业互联网专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/Key-figures-Quoted-gyhlw.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国工业互联网专利申请与授权数据](https://www.cnopendata.com/data/m/patent/Key-figures-Application-gyhlw.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国工业企业专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/industrial-patent.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国应急管理专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/pt-ct-yjgl.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国应急管理专利申请与授权数据](https://www.cnopendata.com/data/m/patent/pt-yjgl.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国数字经济专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/dig-eco-patyyby.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国数字经济专利申请与授权数据](https://www.cnopendata.com/data/m/patent/dig-eco-patjbsw.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国物联网专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/Key-figures-Quoted-wlw.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国物联网专利申请与授权数据](https://www.cnopendata.com/data/m/patent/Key-figures-Application-wlw.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国科研院所专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/researchinstt-patentyyby.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国科研院所专利申请与授权数据](https://www.cnopendata.com/data/m/patent/researchinstt-patentbasic.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国绿色专利文本数据](https://www.cnopendata.com/data/m/patent/Chinese-green-patent-text-data.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国量子信息专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/Key-figures-Quoted-lzxx.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国量子信息专利申请与授权数据](https://www.cnopendata.com/data/m/patent/Key-figures-Application-lzxx.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国金融科技专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/fintech-patentyyby.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国金融科技专利申请与授权数据](https://www.cnopendata.com/data/m/patent/fintech-patentbasic.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国高校数字经济专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/gx-dig-eco-patyyby.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国高校数字经济专利申请与授权数据](https://www.cnopendata.com/data/m/patent/gx-dig-eco-pat.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国高端芯片专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/Key-figures-Quoted-gdxp.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国高端芯片专利申请与授权数据](https://www.cnopendata.com/data/m/patent/Key-figures-Application-gdxp.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [新三板公司专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/xsb-co-patent-citation.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [自动化专利引用与被引数据](https://www.cnopendata.com/data/m/patent/zdh-patentyy.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [自动化专利申请与授权数据](https://www.cnopendata.com/data/m/patent/zdh-patentbasic.html) | 中国专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国会计与财务类岗位线上招聘数据](https://www.cnopendata.com/data/m/labour%26recruit/re-acct.html) | 中国劳动招聘与就业数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国党政类岗位线上招聘数据](https://www.cnopendata.com/data/m/labour%26recruit/dz.html) | 中国劳动招聘与就业数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国家政服务信息数据](https://www.cnopendata.com/data/m/labour%26recruit/jzfw.html) | 中国劳动招聘与就业数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国零工经济线上招聘公司基本信息扩展数据](https://www.cnopendata.com/data/m/labour%26recruit/lgjjjbxx.html) | 中国劳动招聘与就业数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [会计师事务所线上招聘数据](https://www.cnopendata.com/data/m/labour%26recruit/ABA-recruitment.html) | 中国劳动招聘与就业数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [医疗行业线上招聘数据](https://www.cnopendata.com/data/m/labour%26recruit/hspt-recruitment.html) | 中国劳动招聘与就业数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [零工经济搜索指数数据](https://www.cnopendata.com/data/m/labour%26recruit/lgjj.html) | 中国劳动招聘与就业数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [5G行业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/5G-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [专精特新“小巨人”企业财务报表数据](https://www.cnopendata.com/data/m/industry_commerce/little-giant-freport.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国专利权人基本信息扩展数据](https://www.cnopendata.com/data/m/industry_commerce/patent-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国工业企业基本信息扩展数据](https://www.cnopendata.com/data/m/industry_commerce/tic-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国高新技术企业财务报表数据](https://www.cnopendata.com/data/m/industry_commerce/hte-freport.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中征应收款融资服务平台数据](https://www.cnopendata.com/data/m/industry_commerce/zzysk.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [交通运输、仓储和邮政业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/traffic-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [住宿和餐饮业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/accommodation%26catering-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [信息传输、软件和信息技术服务业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/technology-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [农业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/agricul-company-reg-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [制造业单项冠军企业工商注册基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/manufacturing-individual-champion.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [制造业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/manufacture-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [劳务分包企业工商注册基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/laborsub-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [劳务外包企业工商注册基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/laborOS-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [医院医疗机构工商注册基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/hospital-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [卫生和社会工作业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/health%26social-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [居民服务、修理和其他服务业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/residential-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [建筑业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/construction-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [批发和零售业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/wholesale%26retail-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [教育业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/education-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [文化、体育和娱乐业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/cul%26spo%26ent-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [机器人行业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/robot-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [核酸检测机构工商注册数据](https://www.cnopendata.com/data/m/industry_commerce/NAT-ICR.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [水利、环境和公共设施管理业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/water%26env-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [电力、热力、燃气及水生产和供应业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/electricity-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [直播行业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/live-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [科学研究和技术服务业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/scientific-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [租赁和商务服务业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/leasing%26bu-service-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [线上招聘公司基本信息扩展数据](https://www.cnopendata.com/data/m/industry_commerce/rcrt-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [网络安全企业工商注册基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/cyber-security-gs.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [采矿业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/mining-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [食品业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/food-industry-basic.html) | 中国工商注册企业全信息 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [欧洲专利及引用被引用数据](https://www.cnopendata.com/data/m/global-patent/eupatent-citation-global.html) | 全球专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [英国专利及引用被引用数据](https://www.cnopendata.com/data/m/global-patent/gbpatent-citation-global.html) | 全球专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [韩国专利及引用被引用数据](https://www.cnopendata.com/data/m/global-patent/krpatent-citation-global.html) | 全球专利数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国专利权人海关进出口信用数据](https://www.cnopendata.com/data/m/customs/customs-pt.html) | 海关与国际贸易数据 | 截至2021年 | 未标注 | 不支持更新 | P3 | direct_sale |
| [中国口岸收费清单数据](https://www.cnopendata.com/data/m/customs/kasf.html) | 海关与国际贸易数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国工业企业海关进出口交易明细数据](https://www.cnopendata.com/data/m/customs/gq-hg.html) | 海关与国际贸易数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [国际货运代理信息数据](https://www.cnopendata.com/data/m/customs/intl-freight-forwarding.html) | 海关与国际贸易数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [外贸供应商名录数据](https://www.cnopendata.com/data/m/customs/Manufacturer-Directory.html) | 海关与国际贸易数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [大陆进口境外食品生产企业](https://www.cnopendata.com/data/m/customs/jkjwsp.html) | 海关与国际贸易数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [鼓励进口技术和产品目录数据](https://www.cnopendata.com/data/m/customs/gljk.html) | 海关与国际贸易数据 | 未标注 | 未标注 | 未标注 | P3 | direct_sale |
| [中国排污许可证信息数据](https://www.cnopendata.com/data/m/ESG/permit-mee.html) | ESG数据 | 未标注 | 未标注（页面称数据量大） | 年度 | P3 | direct_sale |
| [中国高校专利统计数据](https://www.cnopendata.com/data/m/patent/patent-statistics-for-chinese-universities.html) | 中国专利数据 | 未标注 | 未标注 | 年度 | P3 | direct_sale |
| [小巨人和单项冠军企业专利及引用被引用数据](https://www.cnopendata.com/data/m/patent/xjr%26dxgj-patent-citation.html) | 中国专利数据 | 1985—2025年 | 未标注 | 年度 | P3 | direct_sale |
| [新三板公司专利申请与授权数据](https://www.cnopendata.com/data/m/patent/xsb-co-patent-innovation.html) | 中国专利数据 | 1990—2024年 | 未标注 | 年度 | P3 | direct_sale |
| [中国券商线上招聘数据](https://www.cnopendata.com/data/m/labour%26recruit/qsacct.html) `别名` | 中国劳动招聘与就业数据 | 2014年5月—2024年 | 未标注 | 年度 | P3 | direct_sale |
| [AI人工智能企业工商注册基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/aibasic.html) | 中国工商注册企业全信息 | 1950年—2024年10月 | 未标注 | 年度 | P3 | direct_sale |
| [A股上市公司工商登记分支机构数据](https://www.cnopendata.com/data/m/industry_commerce/listedco-gsdj.html) | 中国工商注册企业全信息 | 截至2024年12月31日 | 未标注（三张表） | 年度 | P3 | direct_sale |
| [专精特新“小巨人”企业工商注册基本信息数据](https://www.cnopendata.com/data/m/industry_commerce/little-giant%26ent-basic.html) | 中国工商注册企业全信息 | 未标注（历年认定批次） | 未标注 | 年度 | P3 | direct_sale |
| [中国工商注册企业全信息](https://www.cnopendata.com/data/m/industry_commerce/chinese-industrial-and-commercial-enterprises-registered-information%2Fchinese-industrial-enterprise-patent-citation.html) | 中国工商注册企业全信息 | 未标注（全国工商注册企业） | 超1.7亿条 | 年度 | P3 | direct_sale |
| [中国工商注册企业全生命周期统计数据](https://www.cnopendata.com/data/m/industry_commerce/backtracking-ind%26com.html) | 中国工商注册企业全信息 | 1990—2024年；31省份 | 3.5亿条 | 年度 | P3 | direct_sale |
| [中国高新技术企业基本信息扩展数据](https://www.cnopendata.com/data/m/industry_commerce/hte-basic.html) | 中国工商注册企业全信息 | 截至2023年 | 未标注 | 年度 | P3 | direct_sale |
| [中国各城市新房信息数据](https://www.cnopendata.com/data/m/china-real-estate/newhousing.html) | 中国房地产数据 | 2010年—2023年6月 | 未标注 | 年度 | P3 | direct_sale |
| [中国小区信息数据](https://www.cnopendata.com/data/m/china-real-estate/chinese-community-information.html) `别名` | 中国房地产数据 | 28个省级行政区、102个重点城市 | 未标注 | 年度 | P3 | direct_sale |
| [房地产业工商注册企业基本信息数据](https://www.cnopendata.com/data/m/china-real-estate/realestate-basic.html) | 中国房地产数据 | 1950年—2024年10月 | 未标注 | 年度 | P3 | direct_sale |
| [全球专利及引用被引用数据](https://www.cnopendata.com/data/m/global-patent/global-patent.html) | 全球专利数据 | 截至2024年 | 未标注 | 年度 | P3 | direct_sale |
| [日本专利及引用被引用数据](https://www.cnopendata.com/data/m/global-patent/jppatent-citation-global.html) | 全球专利数据 | 1985—2024年12月31日 | 未标注 | 年度 | P3 | direct_sale |
| [欧洲专利局专利数据](https://www.cnopendata.com/data/m/global-patent/patent-epo.html) | 全球专利数据 | 未标注 | 未标注 | 年度 | P3 | direct_sale |
| [美国专利及引用被引用数据](https://www.cnopendata.com/data/m/global-patent/uspatent-citation-global.html) | 全球专利数据 | 1985—2024年12月31日 | 未标注 | 年度 | P3 | direct_sale |
| [交通工具司法拍卖数据](https://www.cnopendata.com/data/m/judicial-auction/vehicle-auction.html) | 司法拍卖数据 | 2012年7月—2025年6月30日 | 未标注 | 年度 | P3 | direct_sale |
| [中国土地权人基本信息扩展数据](https://www.cnopendata.com/data/m/land/land-basic.html) | 土地数据 | 1990—2024年土地交易 | 未标注 | 年度 | P3 | direct_sale |
| [全球土地与农资数据](https://www.cnopendata.com/data/m/land/land%26agri.html) | 土地数据 | 200多个国家 | 未标注 | 年度 | P3 | direct_sale |
| [中国各地区招投标单位基本信息扩展数据](https://www.cnopendata.com/data/m/tender%26bid/tender%26bidd-unit.html) | 招投标数据 | 截至2021年 | 未标注 | 年度 | P3 | direct_sale |
| [中国工业企业招投标数据](https://www.cnopendata.com/data/m/tender%26bid/in-bidding.html) | 招投标数据 | 未标注 | 未标注 | 年度 | P3 | direct_sale |
| [中国电缆招投标数据](https://www.cnopendata.com/data/m/tender%26bid/dlztb.html) | 招投标数据 | 2010—2022年 | 未标注 | 年度 | P3 | direct_sale |
| [政府采购中标单位基本信息扩展数据](https://www.cnopendata.com/data/m/tender%26bid/govprocurement-zbdw.html) | 招投标数据 | 未标注 | 未标注 | 年度 | P3 | direct_sale |
| [政府采购公告中标单位基本信息扩展数据](https://www.cnopendata.com/data/m/tender%26bid/govprocurement-ann-zbdw.html) | 招投标数据 | 未标注 | 未标注 | 年度 | P3 | direct_sale |
| [中外双边贸易数据](https://www.cnopendata.com/data/m/customs/sino-foreign-bilateral-trade.html) | 海关与国际贸易数据 | 2005—2019年；59国+2地区 | 未标注 | 年度 | P3 | direct_sale |
| [国际货物贸易数据](https://www.cnopendata.com/data/m/customs/international-trade-in-goods.html) | 海关与国际贸易数据 | 未标注；59国+2地区，含月度 | 未标注 | 年度 | P3 | direct_sale |

### 宏观、区域经济与财政税收（72 条）

| 名称 | 分类 | 覆盖 | 量级 | 更新 | 优先级 | 获取 |
|---|---|---|---|---|---|---|
| [中国专利分地区统计数据](https://www.cnopendata.com/data/m/regional/patent-stats.html) | 中国分地区数据 | 1985-2024（月度） | 省/市/区县 × 三类专利 | 年度 | P2 | direct_sale |
| [中国各区县最低工资数据](https://www.cnopendata.com/data/m/regional/minimum-wage-by-region.html) | 中国分地区数据 | 1999-2023 | 区县级 | 年度 | P2 | direct_sale |
| [中国各地区招投标数据](https://www.cnopendata.com/data/m/regional/regional-bidding-data-across-china.html) | 中国分地区数据 | 未标注 | 1.2亿条 | 年度 | P2 | direct_sale |
| [中国各地区碳交易数据](https://www.cnopendata.com/data/m/regional/chinese-carbon-trading-data-by-region.html) | 中国分地区数据 | 2013-2026（日频） | 两万多条 | 年度 | P2 | direct_sale |
| [中国工商企业进入退出信息统计数据](https://www.cnopendata.com/data/m/regional/stats-enterprises.html) | 中国分地区数据 | 1980-2025 | 32省 × 行业 × 年 | 年度 | P2 | direct_sale |
| [中国彩票销售数据](https://www.cnopendata.com/data/m/regional/chinese-lottery-sales.html) | 中国分地区数据 | 2008-2025 | 全国/分类型/分地区 | 年度 | P2 | direct_sale |
| [农产品批发价格数据](https://www.cnopendata.com/data/m/regional/ncpjg.html) | 中国分地区数据 | 未标注 | 45种/326种农产品 × 31省 | 年度 | P2 | direct_sale |
| [中国城市层面电力短缺指数](https://www.cnopendata.com/data/m/macro/PSI.html) | 中国宏观经济数据 | 2001-2017 | 218个城市 | 不定期 | P2 | open |
| [中国省市县夜间灯光栅格数据](https://www.cnopendata.com/data/m/macro/qgyjdg.html) | 中国宏观经济数据 | 1992-2023 | 未标注 | 年度 | P2 | direct_sale |
| [中国省级层面经济政策不确定性指数](https://www.cnopendata.com/data/m/macro/EPU.html) | 中国宏观经济数据 | 2000-2017 | 31个省份 | 不定期 | P2 | open |
| [中国高质量夜间灯光数据](https://www.cnopendata.com/data/m/macro/gzlyjdg.html) | 中国宏观经济数据 | 未标注 | Excel统计表 + Shp矢量 | 年度 | P2 | direct_sale |
| [企业的经济政策不确定性感知指数](https://www.cnopendata.com/data/m/macro/FEPU.html) | 中国宏观经济数据 | 未标注 | 未标注 | 不定期 | P2 | open |
| [全球宏观经济数据](https://www.cnopendata.com/data/m/global-macro/global-economy.html) | 全球宏观经济数据 | 1960-2023 | 200+国家/地区 × 500+指标 | 未标注 | P2 | direct_sale |
| [中国各地区财政发展指数](https://www.cnopendata.com/data/m/finance-tax/czfzzs.html) | 财政税务数据 | 未标注 | 未标注 | 不定期 | P2 | open |
| [中国各省及省本级预算偏离数据](https://www.cnopendata.com/data/m/finance-tax/yspl.html) | 财政税务数据 | 1994-2017（省级）/2008-2017（地级市） | 31省 + 252市 | 不定期 | P2 | open |
| [中国城投公司上下三层企业族谱数据](https://www.cnopendata.com/data/m/finance-tax/ctgszp.html) | 财政税务数据 | 未标注 | 3600+城投公司 / 31省 | 年度 | P2 | direct_sale |
| [中国各城市栅格层面人口支付力数据](https://www.cnopendata.com/data/m/regional/rkzfl.html) | 中国分地区数据 | 近十年 | 全国城市栅格 | 实时 | P2 | direct_sale |
| [全国店铺点评网站全信息数据](https://www.cnopendata.com/data/m/regional/Store.html) | 中国分地区数据 | 截至2024 | 4000多万家门店 | 年度 | P2 | direct_sale |
| [全国超市便利店数据](https://www.cnopendata.com/data/m/regional/POI-supermarket.html) | 中国分地区数据 | 未标注 | 34省 / 190多万家 | 年度 | P2 | direct_sale |
| [上海市工商注册企业基本信息数据](https://www.cnopendata.com/data/m/regional/shcity-basic.html) | 中国分地区数据 | 1950-2023 | 未标注 | 年度 | P3 | direct_sale |
| [中国各地区方言信息数据](https://www.cnopendata.com/data/m/regional/zgfyxx.html) | 中国分地区数据 | 1986/2012两版 | 省/市/区县/乡镇/街道 | 不定期 | P3 | direct_sale |
| [中国各地区观鸟数量统计数据](https://www.cnopendata.com/data/m/regional/birdsts2025.html) | 中国分地区数据 | 2002-2024 | 34省/344市/2672区县 | 年度 | P3 | direct_sale |
| [中国各城市栅格层面人口分布数据](https://www.cnopendata.com/data/m/regional/rkfb.html) | 中国分地区数据 | 未标注 | 全国城市250米栅格 | 实时 | P3 | direct_sale |
| [中国各城市游客来源国统计数据](https://www.cnopendata.com/data/m/regional/yklyg.html) | 中国分地区数据 | 2001-2017 | 60个城市 | 不支持更新 | P3 | direct_sale |
| [中国合伙企业进入退出信息统计数据](https://www.cnopendata.com/data/m/regional/statspts-enterprises.html) | 中国分地区数据 | 1950-2021 | 未标注 | 年度 | P3 | direct_sale |
| [中国商标分地区统计数据](https://www.cnopendata.com/data/m/regional/trademark-sts.html) | 中国分地区数据 | 1950-2024 | 申请7700万条/核准5400万条 | 年度 | P3 | direct_sale |
| [中国开发区行政区划数据](https://www.cnopendata.com/data/m/regional/zgkfq.html) | 中国分地区数据 | 未标注 | 未标注 | 年度 | P3 | direct_sale |
| [中国核酸检测机构及采样点数据](https://www.cnopendata.com/data/m/regional/China-testing-agency%26point-of-COVID-19.html) | 中国分地区数据 | 截至2022-10 | 31省机构/采样点 | 不定期 | P3 | direct_sale |
| [中国省市县日间热成像数据](https://www.cnopendata.com/data/m/regional/qgrcx.html) | 中国分地区数据 | 2018-2023 | 省市县三级 | 年度 | P3 | direct_sale |
| [中国省市县碳排放量栅格数据](https://www.cnopendata.com/data/m/regional/qgtpf.html) | 中国分地区数据 | 1970-2023 | 省市县乡镇四级栅格 | 年度 | P3 | direct_sale |
| [中国省市县网格人口数据](https://www.cnopendata.com/data/m/regional/qgwgrk.html) | 中国分地区数据 | 1990-2022 | 1km栅格 × 省市县 | 年度 | P3 | direct_sale |
| [中国社会组织名录数据](https://www.cnopendata.com/data/m/regional/shzz.html) | 中国分地区数据 | 未标注 | 31省 / 111万条 | 年度 | P3 | direct_sale |
| [中国绿色专利分地区统计数据](https://www.cnopendata.com/data/m/regional/chinese-green-patent-volume-data-by-region.html) | 中国分地区数据 | 1985-2025 | 省/市 × 7大主分类 | 年度 | P3 | direct_sale |
| [中国能源年鉴地区能源平衡表数据](https://www.cnopendata.com/data/m/regional/hjtjnj.html) | 中国分地区数据 | 1997-至今 | 30省 × 30余指标 | 年度 | P3 | direct_sale |
| [中国行政区划shp数据](https://www.cnopendata.com/data/m/regional/chinese-administrative-divisions-shp.html) | 中国分地区数据 | 2013-2023 | 省市县多级要素 | 年度 | P3 | open |
| [中国行政区划数据](https://www.cnopendata.com/data/m/regional/chinese-administrative-divisions.html) | 中国分地区数据 | 未标注 | 未标注 | 年度 | P3 | open |
| [中国酒店数据](https://www.cnopendata.com/data/m/regional/hotel.html) | 中国分地区数据 | 未标注 | 32省市+2特别行政区 | 年度 | P3 | direct_sale |
| [中国高考录取分数线数据](https://www.cnopendata.com/data/m/regional/gaokao.html) | 中国分地区数据 | 2014-2026 | 批次×学校×专业 | 年度 | P3 | direct_sale |
| [全国Spa门店数据](https://www.cnopendata.com/data/m/regional/POI-SPA.html) | 中国分地区数据 | 未标注 | 34省 / 83万家 | 年度 | P3 | direct_sale |
| [全国中式快餐店分布数据](https://www.cnopendata.com/data/m/regional/POI-zskc.html) | 中国分地区数据 | 未标注 | 34省 / 10万+条 | 年度 | P3 | direct_sale |
| [全国停车场分布数据](https://www.cnopendata.com/data/m/regional/POI-tcc.html) | 中国分地区数据 | 未标注 | 34省 / 164万条 | 年度 | P3 | direct_sale |
| [全国健身房分布数据](https://www.cnopendata.com/data/m/regional/POI-jsf.html) | 中国分地区数据 | 未标注 | 34省 / 11万个 | 年度 | P3 | direct_sale |
| [全国充电站分布数据](https://www.cnopendata.com/data/m/regional/POI-cdz.html) | 中国分地区数据 | 未标注 | 34省 / 约3万条 | 年度 | P3 | direct_sale |
| [全国兴趣点分地区分类型统计数据](https://www.cnopendata.com/data/m/regional/CNpoi-stat.html) | 中国分地区数据 | 未标注 | 省/市/区县 × 多级分类 | 年度 | P3 | direct_sale |
| [全国兴趣点（POI）数据](https://www.cnopendata.com/data/m/regional/POI.html) | 中国分地区数据 | 未标注 | 17大类 | 年度 | P3 | direct_sale |
| [全国加油站分布数据](https://www.cnopendata.com/data/m/regional/POI-jyz.html) | 中国分地区数据 | 截至2024 | 34省 / 14万条 | 年度 | P3 | direct_sale |
| [全国彩票店数据](https://www.cnopendata.com/data/m/regional/POI-lottery.html) | 中国分地区数据 | 未标注 | 34省 / 17万家 | 年度 | P3 | direct_sale |
| [全国景区数据](https://www.cnopendata.com/data/m/regional/scenic-spot.html) | 中国分地区数据 | 未标注 | 31省 A级及以上景区 | 不定期 | P3 | direct_sale |
| [全国汽车经销商数据](https://www.cnopendata.com/data/m/regional/car-dealer.html) | 中国分地区数据 | 未标注 | 四万余家 | 不定期 | P3 | direct_sale |
| [全国火锅店分布数据](https://www.cnopendata.com/data/m/regional/POI-hgd.html) | 中国分地区数据 | 2024 | 34省 / 约36万家 | 年度 | P3 | direct_sale |
| [全国西式快餐店分布数据](https://www.cnopendata.com/data/m/regional/POI-xskc.html) | 中国分地区数据 | 未标注 | 34省 / 5万+家 | 年度 | P3 | direct_sale |
| [农业工商注册企业数量统计数据](https://www.cnopendata.com/data/m/regional/agricul-company-reg-statistical.html) | 中国分地区数据 | 1980-2025 | 31省 × 行业 | 年度 | P3 | direct_sale |
| [制造业工商注册企业数量统计数据](https://www.cnopendata.com/data/m/regional/data-on-companies-registered-in-the-manufacturing-industry.html) | 中国分地区数据 | 1950-至今 | 31省/363市/2933区县 | 年度 | P3 | direct_sale |
| [北京市工商注册企业基本信息数据](https://www.cnopendata.com/data/m/regional/bjcity-basic.html) | 中国分地区数据 | 截至2024-10 | 未标注 | 年度 | P3 | direct_sale |
| [广州市工商注册企业基本信息数据](https://www.cnopendata.com/data/m/regional/gzcity-basic.html) | 中国分地区数据 | 1950-2023 | 未标注 | 年度 | P3 | direct_sale |
| [法院名录数据](https://www.cnopendata.com/data/m/regional/court.html) | 中国分地区数据 | 未标注 | 31省法院名录 | 年度 | P3 | direct_sale |
| [淘宝村淘宝镇名单数据](https://www.cnopendata.com/data/m/regional/taobao-township.html) | 中国分地区数据 | 2017-2022（缺2018） | 7780村/2429镇 | 年度 | P3 | direct_sale |
| [深圳市工商注册企业基本信息数据](https://www.cnopendata.com/data/m/regional/szcity-basic.html) | 中国分地区数据 | 未标注 | 未标注 | 年度 | P3 | direct_sale |
| [中国开发区基础统计指标数据](https://www.cnopendata.com/data/m/macro/zgkfq-tj.html) | 中国宏观经济数据 | 2017-2023 | 未标注 | 不支持更新 | P3 | direct_sale |
| [中国县域统计年鉴数据](https://www.cnopendata.com/data/m/yearbooks/xyjj.html) | 年鉴数据 | 1997-2018 | 2000多个县域 | 年度 | P3 | direct_sale |
| [中国审计年鉴数据](https://www.cnopendata.com/data/m/yearbooks/audit.html) | 年鉴数据 | 1999-2017 | 全国+地方审计机关 | 年度 | P3 | direct_sale |
| [中国环境统计年鉴数据](https://www.cnopendata.com/data/m/yearbooks/environmental-statistics.html) | 年鉴数据 | 2004-2023 | 31省 × 十二方面 | 年度 | P3 | direct_sale |
| [中国各级政府税收分成数据](https://www.cnopendata.com/data/m/finance-tax/gjzfssfc.html) | 财政税务数据 | 1994-2014 | 中央/省/县等多级 | 不定期 | P3 | open |
| [中国涉税专业服务机构名单](https://www.cnopendata.com/data/m/finance-tax/ssjg.html) | 财政税务数据 | 截至2025-05 | 7.8万TSC5 + 19万监管机构 | 年度 | P3 | direct_sale |
| [中国税收调查企业数据](https://www.cnopendata.com/data/m/finance-tax/Tax-Survey.html) | 财政税务数据 | 2008-2020 | 每年60-70万条样本 | 不定期 | P3 | direct_sale |
| [中国财政运行数据](https://www.cnopendata.com/data/m/finance-tax/czyx.html) | 财政税务数据 | 2019/2020报告期 | 六大指数 | 不支持更新 | P3 | open |
| [税收调查企业专利及引用被引用数据](https://www.cnopendata.com/data/m/finance-tax/tax-patent.html) | 财政税务数据 | 1985-2023 | 未标注 | 年度 | P3 | direct_sale |
| [税收调查企业基本信息扩展数据](https://www.cnopendata.com/data/m/finance-tax/tax-basic.html) | 财政税务数据 | 2008-2016 | 未标注 | 年度 | P3 | direct_sale |
| [税收调查企业工商变更数据](https://www.cnopendata.com/data/m/finance-tax/tax-idtchange.html) | 财政税务数据 | 2008-2016 | 未标注 | 年度 | P3 | direct_sale |
| [税收调查企业海关进出口交易明细数据](https://www.cnopendata.com/data/m/finance-tax/tax-hg.html) | 财政税务数据 | 2007-2016 | 未标注 | 不支持更新 | P3 | direct_sale |
| [税收调查企业股东信息数据](https://www.cnopendata.com/data/m/finance-tax/tax-stockholder.html) | 财政税务数据 | 未标注 | 未标注 | 年度 | P3 | direct_sale |
| [税收调查企业裁判文书数据](https://www.cnopendata.com/data/m/finance-tax/tax-jddm.html) | 财政税务数据 | 2008-2016 | 未标注 | 年度 | P3 | direct_sale |

## 构建提示

- 新鲜度证据已挂 38 条
- 频率证据已挂 103 条（来源 _freq_audit.json，其中与审计结论差额 0 条）
