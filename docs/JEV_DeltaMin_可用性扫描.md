# JEV · Δmin 时间维度可用性扫描（2026-11-10）

> 用途：钉死"新闻↔价格"领先/追认判定的数据源边界，绕开判例 #25③"拿不存在的时间戳算领先分钟"的坑。
> 方法：只做可用性实证，不打补丁、不写推演代码。三问——哪些新闻有分钟戳？价格侧分钟数据能拉到哪些票？社交维度能进 Δmin 吗？
> 工具：Python 3.11 + sqlite3 + pyarrow；数据源 `news_collector/news.db`、`quant_backtest/data/tushare_curated/stk_mins.parquet`、`quant_backtest/data/tushare/stk_mins/`、`portal/social_*.json`。

## 一、核心结论（先给答案）

**新闻→价格 Δmin 完全可行，且数据边界已清晰：**

1. **新闻侧有真实分钟戳的源足够多**——实时快讯类(sina/eastmoney/wallstreetcn/chinanews/world/xd_news)≈99–100% 带精确到分钟的时间；总覆盖 81%（115,228 / 141,319 条）。**之前"新闻时间戳缺失"的疑虑被推翻**——缺失的只是公告/研报类（它们天然只有日期，属"追认"信号，本就不该强求分钟）。
2. **价格侧必须用工位实际分钟源 `quant_backtest/data/tushare/stk_mins/`**，不是那份 `tushare_curated/stk_mins.parquet` 快照（后者只到 2026-10-23，含不了 2026-11 的近期案例）。活源是 34,227 个 `{code}_{ex}_{YYYYMM}.parquet`，每日 15:39 更新，覆盖到 2026-11。
3. **社交维度暂不进分钟级 Δmin**——`social_*.json` 是日级快照，内部条目无逐条时间戳；社交只做日级 co-signal。

## 二、逐源证据

### 2.1 news.ts 时间戳精度（分层）
- 总行 141,319；时间截断到 `00:00` 的 26,091 条（18%）；真实分钟级 115,228（81%）。
- 按 source 分层：

| source | 总条 | 真实分钟 | 占比 | 性质 |
|---|---:|---:|---:|---|
| sina | 55,639 | 55,581 | 99% | 实时快讯 ✅ |
| eastmoney | 22,828 | 22,817 | 99% | 实时快讯 ✅ |
| wallstreetcn | 14,635 | 14,611 | 99% | 实时快讯 ✅ |
| chinanews | 10,170 | 10,170 | 100% | 实时快讯 ✅ |
| world | 8,603 | 8,561 | 99% | 实时快讯 ✅ |
| xd_news | 3,258 | 3,242 | 99% | 实时快讯 ✅ |
| wechat_kol | 143 | 143 | 100% | 社交/KOL ✅ |
| govpolicy | 26 | 26 | 100% | 政策 ✅ |
| xq_kol | 12 | 12 | 100% | 社交/KOL ✅ |
| **announcement** | 14,008 | 0 | 0% | 公告（天然日级，=追认信号） |
| xd_notice | 8,015 | 0 | 0% | 通知（天然日级） |
| report | 3,440 | 0 | 0% | 研报（天然日级） |
| survey | 542 | 65 | 11% | 调研（部分补零） |

- **判读**：做 Δmin 时，**只用带真实分钟戳的源**（sina/eastmoney/wallstreetcn/chinanews/world/xd_news/wechat_kol/govpolicy/xq_kol）。announcement/xd_notice/report 作为"日级追认"信号单独处理（价格先动、T 日盘后公告追认 → 粗粒度 Δmin 仍可用）。

### 2.2 价格侧：两份分钟数据，只一份能用
- `quant_backtest/data/tushare_curated/stk_mins.parquet`：147,881,133 行、5,037 只票，**范围 2025-09-01 ~ 2026-10-23**。→ 历史快照，**不含 2026-11**，不能用于近期 Δmin。
- `quant_backtest/data/tushare/stk_mins/`（JEV 实际分钟源 / `jev_mins_fetch.py` 输出）：**34,227 个按票×月 parquet**，样例 `689009_SH_202611.parquet`、`688981_SH_202610.parquet`，mtime 最新 **2026-11-09 15:39**。→ 活源，**覆盖到 2026-11，每日更新**。
- 涨停时刻可从活源切片提取：`pq.read_table(..., filters=[("ts_code","=",code), ("trade_time",">=",day+" 09:00:00"), ("trade_time","<=",day+" 15:00:00")])` 轻量可取（无需读 1.47 亿行全量）。

### 2.3 社交侧：日级快照，无逐条时间
- `social_20261106.json` / `social_20261109.json`：顶层有 `day` + `fetched_at`（分钟级），但 `xq`(hot/discuss/trade/follow)、`guba`(rank/posts)、`by_code`(299–305 只) 内部条目**只有 code/name/value/pct/rank，无时间戳**。
- 结论：社交维度只能提供"某日某票热度排名/帖子数"的**日级**信号，不能参与"谁先动"的分钟级 Δmin。

## 三、对判例 #25③ 的修订

- 旧坑：有人用**不存在的时间戳**算领先分钟 → 复现错误。
- 新口径（已实证）：**只用 news.ts 中真实分钟级源**（§2.1 标 ✅ 的）做 Δmin；公告/研报类走日级追认通道；社交走日级 co-signal。价格侧统一走活源目录 `stk_mins/`。

## 四、Δmin 定义提案（下一步，待老田拍板）

> 本扫描只完成"先验可用性"。定义与权重在 P1 阶段落地。

- **价格先行（lead）**：涨停时刻 `T_limit`（从活源 stk_mins 提取首次触板分钟）早于 新闻分钟戳 `T_news` → `Δmin = T_news − T_limit > 0`（价格先动、消息后追认）。
- **消息追认（confirm）**：`T_news` 早于 `T_limit` → `Δmin < 0`（消息先出、价格后响应；或盘后公告对次日涨停的追认，走日级）。
- **权重**：在组合 pattern 里，Δmin 的符号（lead/confirm）与量级共同决定因果方向；与资金背离、散户热度、机构沉默三维度交叉时，lead 型（价格先行+消息追认）才是"有人提前知道"的强信号。

## 五、钉死的边界（给 P1 的硬约束）

1. Δmin 价格源 = `quant_backtest/data/tushare/stk_mins/`（活源），**禁止**用 `tushare_curated/stk_mins.parquet`。
2. Δmin 新闻源白名单 = sina/eastmoney/wallstreetcn/chinanews/world/xd_news/wechat_kol/govpolicy/xq_kol（真实分钟级）；公告/研报单列日级追认。
3. 社交维度不参与分钟级 Δmin（日级 co-signal 另算）。
4. 切片用 pyarrow `filters` 按票+日轻量读，避免读 1.47 亿行全量。
