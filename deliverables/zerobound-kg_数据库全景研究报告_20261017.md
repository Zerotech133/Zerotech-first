# zerobound-kg / 股票数据系统 · 数据库全景研究报告

> 日期：2026-10-17 · 方法：只读盘点（SQLite 直查 / dashboard API / Docker exec / manifest 解析）
> 范围：C:/项目/zerobound-kg（代码）+ C:/项目/KGDataRoot（数据快照）+ C:/aios（运行真源）+ 本机 Docker 服务

---

## TL;DR

系统共 **7 类存储、210 张表、1.27 亿行**，由 `catalog.json` 统一编目，读层 `datamart.py` 统一收口。核心图谱 Neo4j 155 万节点 / 438 万边。发现 **1 个高优先级口径问题**（news.db 数仓 vs 图内差 +9049，19%），1 个环境断链（E 盘 news.db 不可达），其余结构健康。

---

## 1. 存储全景（7 类）

| # | 存储 | 位置 | 形态 | 规模 | 角色 |
|---|------|------|------|------|------|
| 1 | **KGDataRoot（C 盘快照）** | `C:/项目/KGDataRoot` | parquet 树 + SQLite | 14.6 GB | 研究用数据快照 |
| 2 | **运行真源** | `C:/aios/quant_backtest/data`（= `C:/项目/老田的AI助手/quant_backtest/data` 同一物理目录） | 同上布局 | 同步中 | 生产数据根，catalog 版本 v2、2026-10-16 更新 |
| 3 | **quant.db** | `KGDataRoot/quant.db` | SQLite 6 表 | 191 MB | 缠论池(9720) + 九维数据(4413 股) |
| 4 | **tdx_info.db** | `KG:/DataRoot/tdx/` | SQLite 3 表 | 102 万行 | 资讯：新闻 50.7 万 / 公告 21.5 万 / 研报 29.5 万 |
| 5 | **Neo4j 图谱** | Docker `neo4j-kg` :7687/:7474 | 属性图 | 155 万节点 / 438 万边 | 知识图谱（MarketEvent 67.9 万、IntelItem 54.4 万、Person 19.2 万…） |
| 6 | **PostgreSQL zhice-db** | Docker :5446 | 67 表 | v2_bar_minute 1253 万行 | zhice-os v2 业务库 |
| 7 | **辅助库** | quant-pg（quant_meta/quant_system）、cainiao-mysql(thunder_model 233 表，Quantz+AI 对话+交易) | Docker | — | 边缘业务 |

**目录按来源分组**（catalog 210 表）：xtcloud 146 / tushare 50 / xueqiu 10 / crypto 1 / guba 1 / legacy 1 / pg 1。

## 2. 三大 SQLite 库明细

### quant.db（191 MB，6 表）
| 表 | 行数 | 说明 |
|---|---|---|
| chanlun_pool | 9,720 | 缠论信号池：CN/1d，2025-09-17 ~ 2026-09-30，19 列（bei/bi2/mbei/tbe 背驰类标记） |
| nine_dim_rc / fina / fc | 4,413 / 4,713 / 4,413 | 九维数据（rows_json 压缩存储），覆盖 4,413 只股票 |
| *_meta | 1+1 | 拉取水位 |

### tdx_info.db（3 表 102 万行）
`stock_news`(507,094) / `stock_notice`(215,486) / `stock_report`(294,565)，均带 `_fetched_at` 水位列，按 ts_code 关联。

### news.db（⚠️ 断链）
默认指针 `E:/老田的AI助手/news_collector/news.db` — **E 盘当前不可达**。KG_NEO4J_* / KG_NEWS_DB 环境变量均未设置（.env.local 仅有 4 行：XT_DATA_KEY / CAINIAO_USER/PASS）。

## 3. Parquet 数仓（KGDataRoot 14.6 GB）

| 区 | 大小 | 内容 |
|---|---|---|
| tushare/ | 9.29 GB | 50 数据集：daily 418 万行、daily_basic 415 万、moneyflow 398 万、adj_factor、三大报表、分钟线(idx/sw/etf/stk/hk)、us/hk 全套 |
| tdx/ | 5.14 GB | 161 表目录（154 done / 7 fail）：ts_daily 418 万、index_daily 625 万、sector_min 3,270 万行最大表、stk_auction 228 万 |
| us/ | 0.07 GB | 逐股单文件（AAPL 等，legacy，正被 us_daily 替代） |
| crypto/ | ~0 | 逐币种 _USDT parquet（BTC 等 10+ 币） |
| features/ | ~0 | kg_features.parquet、chanlun_sig_dataset.parquet |
| semantica/ | 0.09 GB | 语义层：decisions/events/facts jsonl + 向量 npy + notice_llm 评测集 |
| distill/ nlquery/ nlmulti/ | ~0 | 蒸馏池、NL 查询样本 |
| 顶层 | — | quant.db、youzi_registry.parquet、kg_communities.json（社区发现产物，2026-09-30） |

**失败表（7）**：ts_limit_list_d、ts_tdx_daily、ts_shibor(+quote)、ts_libor、ts_hibor、ts_stk_week_month_adj —— 多为权限/无数据。

**tushare manifest**：68 万行已登记；monetary_policy 无权限；4 个 auction probe 0 行。

## 4. 图谱与对账状态（dashboard :8791 实况）

- **图谱规模**：节点 1,551,843 / 边 4,378,988。Top 标签：MarketEvent 679,186 · EXP_IntelItem 493,595 · Person 192,031 · Event 69,119 · IntelItem 50,398 · Organization 24,838 · Product 6,700 · Company 6,265 · Security 5,911。
- **情报五源水位**（2026-10-16 09:47）：news.db 图内 37,810 / 数仓 46,859（**delta +9,049，19%，超 5% 告警线**）；guba 图内 10,297（数仓 0 计数，采集侧 jsonl 150 文件至 10-14）；雪球三表水位 null（token 缺失降级中，与记忆一致）。
- **ann_history.db**：图内 2,292 / 数仓 0（对账口径：数仓计数为 0 时 delta 为负，属指针缺失非数据丢失）。
- 仪表盘安全设计良好：Cypher 只读白名单、驱动层 READ_ACCESS 双保险、仅 127.0.0.1 绑定。

## 5. 关键发现与建议

| 优先级 | 发现 | 建议 |
|---|---|---|
| **P1** | news.db 指向 E 盘且不可达；雪球水位 null；仪表盘显示的"最新情报 2026-10-01"实际依赖断链库 | ① 迁移或挂载 news.db 至可达路径并设 `KG_NEWS_DB`；② 补 xq_a_token（记忆中的动作项仍开放） |
| **P1** | news.db 数仓 46,859 vs 图内 37,810（差 19%）| 排查 kg_intel_link 增量链路是否卡在 10-01；水位 last_rowid=43,465 落后 |
| P2 | E 盘整体不可达（config.json `data_root` 仍写 E 盘老路径）| 与"工作站迁移复盘_20261016"呼应：清理 config.json 遗留指针，统一 KG_DATA_ROOT 环境变量注入 |
| P2 | KGDataRoot 与 C:/aios 真源双份存储（14.6GB × 2）| 明确 KGDataRoot 定位（快照/研究副本），写清同步策略，防口径分裂 |
| P3 | ts_shibor/libor/hibor 等 7 表失败 | 权限类，可放弃或换源 |
| P3 | cainiao-mysql(thunder_model) 233 表与主链路解耦 | 确认归属（菜鸟量化交易业务），无需纳入中台 |

## 6. 架构评价（延续《数据中台架构设计.md》）

设计目标"一个入口取数、一张目录看全"已基本落地：catalog v2 + datamart 读层 + dashboard 可视化 + manifest 断点续传 + watermark 水位，链路完整。**当前短板不在结构，在数据新鲜度与对账闭环**：雪球源降级、news 增量疑似停滞、E 盘遗留指针。建议下一步做一次「全源水位巡检」任务，把五源 watermark 统一纳入 data_health 监控。

---
*盘点过程零写入：所有查询均为只读（SQLite ro 模式 / API GET / docker exec 只读 SQL）。*
