# JEV 能力账本（2026-11-10 初版）

> **⬇ 已被取代（2026-11-10）**：权威版本升级为
> `docs/plans/2026-11-10_统一真相源_能力账本+执行计划_v2.md` 上半「JEV 能力账本 v2」。
> 本文保留作原始证据底稿；后续状态变更只在 v2 改。

> 用途：把「JEV 到底能干嘛」从未知变成可度量的账。proven / unknown / out-of-scope 三栏，
> 每栏含证据与待接维度；末尾是接数据 / 写回测的 checklist。
> 配套验证器：`tools/jev_validator.py`；首批回测报告：`quant_backtest/reports/jev_validator_20261110*.json|md`

## 〇、定位（先钉死）

- **JEV = 真伪 / 可持续性判定平台**，不是预测器、不是荐股、不是爬虫。
- 已知 OK：**新闻 / 事实 / 披露的结构化解读（语义层）** —— 这是"阅读理解"，不是"明天涨跌"。
- 待测：三维度三角定位能否作为"涨停后 N 日回落"的**前向**预测器（即"判伪"是否真的准）。

## 一、PROVEN（已验证可用）

| 能力 | 证据 | 备注 |
|---|---|---|
| 语义 / 事实解读层（方向口径） | **已跑后验硬证据**（见下）`tools/jev_posterior_poc.py`，140741 已判新闻 / 28502 可评估；`jev_flow.load_disclosures` 已落地研报/公告解析（ts_kind 固定 research/notice，不参与 Δmin） | **弱成立**：组合层方向 sign 正确，但单点不可靠、**conf 反相关已弃用**（见「语义层后验结果」与「conf 校准重训」小节） |
| 资金流读取（量价之"量"） | `jev_flow.load_flow` 接 `ts_moneyflow`，divergence 定义清晰（主力净买 `main_net>0` 但全档净流出 `net_mf<0`） | 全市场 5000+ 只/日；快照缓存 12h；725/725 历史覆盖率 ✓ |
| 社交热度读取（股吧/雪球） | `jev_social.load_social` 接 `social_YYYYMMDD.json`（股吧人气 + 雪球四榜） | 仅 2 日有快照（20261106 / 20261109） |
| 板块联动网出图 | `jev_graph.py` 三栏联动 + 点板块 drill-down，已挂 8091（`/jev-graph`） | 渲染层，非判定本身 |

### 语义层后验结果（jev_posterior_poc.py，2026-11-10）

口径：信号日 = 新闻 ts 日期，持有 = 其后首个交易日（T+1 收盘）；标的收益 = 关联 A 股 T+1 pct_chg 等权均值；超额 = 标的 − 沪深300 同日；方向命中 = sign(超额) 与 bias 一致；中性命中 = |超额| ≤ ±1%。
样本：140741 已判新闻 → 29910 带标的 → **28502 可评估**（方向类 14102 / 中性 14400），覆盖 19 个交易日。报告：`quant_backtest/reports/jev_posterior_20261110.json|md`。

| 指标 | 值 | 对照 |
|---|---|---|
| 方向类准确率（bull/bear 方向对） | **52.5%**（n=14102，95%CI 51.7–53.3%） | 全判bull 49.2% / 全判bear 50.7% → 仅 +1.8~3.3%，显著但微弱 |
| 平均超额（bull / bear / neutral） | **+0.203% / −0.408% / +0.099%** | **sign 正确**：bull 跑赢、bear 跑输，组合层方向 tilt 真实存在 |
| 中性带命中（\|超额\|≤1%） | 30.3% | 中性类≈无信息量（均值超额≈0） |
| conf 校准 | **反相关（比随机还差）**：见下「conf 校准重训」小节，`tools/jev_calibrate.py` 跑 28506 样本 / 时间切分 70:30 → 原始 AUC **0.414**（高 conf 反而更可能错），最佳单调反转 AUC 也仅 0.586 | **conf 已证实无决策价值 → 正式弃用**（不修不换，直接从下游摘除） |

按事件类型（n≥20）：credit 64.7%、trade 62.9%、risk 58.1%、policy 54.6%、equity 53.8%、earnings 53.9% 有真实边；price 48.8%、supply 49.1%、other 51.5% 无/反向。

**结论（修正原「proven」口径）**：
- 新闻 bias 的**方向**在组合层是真实弱信号（bull 平均跑赢 0.2%、bear 跑输 0.4%）——「阅读理解方向对」成立，但仅 0.2~0.4% 量级。
- 单点**命中率仅 52.5%**，接近抛硬币，不可作为单只标的买卖信号。
- **conf 反相关（比随机还差）**：`jev_calibrate.py` 后验证实原始 AUC=0.414，高 conf 反而更可能错；即便反转也仅 0.586。JEV 自报 `conf` 不仅未校准，**还反向**，已无决策价值。
- → 把该项从「proven/可用」降为「**弱成立（方向口径）**」：可作组合层微弱方向因子，不可作单点信号，**且正式弃用 conf**（摘除下游过滤/排序）。

### conf 校准重训（tools/jev_calibrate.py，2026-11-10）

口径：复用 jev_posterior 标注（right=1 当且仅当 bias 判读正确），28506 样本按 ts 时间切分 70:30（训练 19954 / 测试 8552）。拟合 Temperature / Platt / Isotonic 三种校准器 + 10 分位可靠性查表。报告：`quant_backtest/reports/jev_calibrate_20261110.json|md`，拟合参数：`jev_calib_params.json`。

| 方法 | AUC | ECE | Brier |
|---|---|---|---|
| raw_conf | **0.414** | 0.431 | 0.447 |
| temperature（T=20，保序） | 0.414 | 0.096 | 0.255 |
| platt（反转，保序） | 0.586 | 0.030 | 0.240 |
| isotonic | 0.499 | 0.022 | 0.245 |

分位可靠性查表（测试集）：conf [0,0.75)→右率 0.557；[0.90,0.95)→0.352；[0.95,1.00)→0.375。**高 conf 段右率反而最低** → 确认反相关。

**结论**：
- 单调校准器保序，**AUC 不变**——Temperature 把 ECE 从 0.43 降到 0.10（修准了绝对概率），但**零判别增益**；Platt 因学到反转斜率把 AUC 拉到 0.586，仍是弱信号、不可用。
- **conf 救不回来**，结论从"重训校准"升级为"**弃用 conf**"。权威实现 `calibrate_conf()` 仅存于 `tools/jev_calibrate.py`（Temperature 缩放，仅向后兼容占位、未接任何决策路径）；`tools/jev_signal.py` 的同名 stub 已于 2026-11-10 删除（消除双定义歧义，grep 确认无调用方）。
- **下游摘除清单**（须改）：`api_common_stock.py:57` 的 `min_conf=0.75` 过滤、`api_backtest_pages_a2.py` 的按 `conf` 排序/Top-N、各处 `conf>=0.7/0.85` 阈值与颜色编码 —— 改用**事件类型分层**（credit/trade/risk/policy/equity/earnings 有边；price/supply 无）+ 组合层 sign。另 `portal/m_score.py` 的 `jev_conf` **5 核心评分指标**（反相关）亦于 2026-11-10 续替换为 `jev_signal`（分层加权方向分，注册表+代码+测试同步，活端点验证）。

## 二、UNKNOWN（未证明 / 当前证据偏弱甚至反向）

| 能力 | 当前证据 | 待接维度 / 下一步 |
|---|---|---|
| 三维度三角定位 → 判伪 | `jev_validator` 跑 725 只涨停 / 9 日（20261020–20261030）：triangulation 模式下**结构维度仅 52/725 有值**（`limit_list_d` 的 `open_times/limit_times/first_time` 字段稀疏，且全挤在 10-30 一日），方向也可疑（`limit_times≥3` 连板多在 A 股是动量延续而非弱）→ 实际只在 10-30 一日生效，无法正常下结论 | 结构维度须换定义，或直接改用 stk_mins 的 **Δmin（时间维度）**；社交须历史回补快照 |
| 资金背离单维 → 涨停后回落 | **money_only 模式 725 只**：判伪 3 日回落 **61.3%** vs 全涨停基线 **60.2%**，lift **+1.1%**（≈噪声）；1/5 日 lift 亦近 0。判伪 455 / 支撑 270，两子集回落率都与基线无差 | **当前公式无前向预测力**。需：①拆分 divergence（`main_net>0&net_mf<0`）与 `main_net<0` 分别回测；②换预测 horizon；③限定题材/板块后再测 |
| 时间维度（Δmin：价格先行 vs 消息追认） | 仅 11-06 单案例叙事，**未量化回测**。判例 #25③ 明令：拿不存在的时间戳算领先分钟会复现坑 | 需 stk_mins 分钟数据 + 新闻时间戳，建 Δmin 判读并回测 |
| 社交维度历史覆盖 | **0**（仅 2 日快照）。故历史回测无法用第三维交叉 | 历史回补 `social_YYYYMMDD.json`（jev_social_fetch 批量） |
| 跨市场状态泛化 | 仅 1 样本（11-06） | 牛/熊/震荡多 regime 回测 |

### 关于"11-06 新型电力"判例的再定性
11-06 那次（股吧散户追捧 + 雪球机构无视 + 主力接而全档逃 = 散户接盘、涨得不实）是**事后叙事**：回头看"涨得不实"成立，但本批 725 只前向回测显示，把它当成"涨停后 N 日回落"的**前瞻信号**不成立（资金背离 lift ≈ 0）。
**结论：JEV 目前是"事后解释器"，不是"前瞻预测器"——这正是用户说的"未知"区。**

## 三、OUT OF SCOPE（明确不做）

- 爬虫 / 数据采集（红线：采集是下属工位，JEV 只读快照）
- 荐股 / 买卖点 / 组合管理
- 实时盯盘告警（属 RiskGuard / 盯盘机器人，非 JEV）

## 四、验证器（tools/jev_validator.py）

- **干什么**：输入 N 个历史涨停日 → 跑三维度三角定位 → 统计"判伪"集合 N 日实际回落比例 → 对照全涨停基线（lift）。
- **模式**：`--mode triangulation`（三维度多数票）/ `money_only`（仅资金背离，隔离测试 JEV 在 11-06 真正用过的维度）。
- **数据来源**：涨停宇宙 = `limit_list_d`（优先，带结构字段）+ `daily`（近期补）；前向收益 = `daily.parquet`；资金 = `flow_*.json` 快照（`--fetch-flow` 经 `jev_flow` 现拉并缓存）；社交 = `social_*.json`。
- **护栏**：前向窗口最后一日超出 `daily` 最大交易日（20261109）的日自动剔除，杜绝窗口塌缩成 T+1 单日。
- **已知限制**：结构维度字段稀疏、社交历史 0 覆盖、近期日无前向窗口 → **三维度完整回测暂不可行**，须先补数据。
- **首批报告**：`jev_validator_20261110.json|md`（triangulation）、`jev_validator_20261110_money_only.json|md`（money_only）。

## 五、下一步 checklist（接数据 / 写回测）

- [ ] **批量回补 `social_YYYYMMDD.json`**（历史日）→ 解锁社交维度历史覆盖，凑齐三维度
- [ ] **Δmin 时间维度**：用 stk_mins 分钟数据 + 新闻时间戳实现真正的"价格先行 vs 消息追认"，替换 `limit_list` 结构代理
- [ ] **重检 money fake 定义**：拆分 divergence 与 `main_net<0`，分别回测；试不同 horizon（1/3/5/10 日）与板块/题材分层
- [x] **跑 `jev_posterior_poc.py`** 得出新闻 bias 校准的公开结论（语义层 proven 的硬证据）→ **已完成 2026-11-10**：方向准确率 52.5%（微弱有效），但 conf 未校准、中性类无信息；语义层降级为「弱成立（方向口径）」
- [x] **重训 conf 校准**（`tools/jev_calibrate.py`）→ **已完成 2026-11-10**：原始 AUC 0.414（**反相关**，高 conf 更可能错），单调校准器保序、AUC 不变，最佳反转也仅 0.586 → 结论从"重训"升级为"**弃用 conf**"；权威 `calibrate_conf()` 留在 jev_calibrate.py（未接决策），jev_signal 的 stub 已删
- [x] **摘除下游 conf 依赖** → **已完成 2026-11-10**：新增 `tools/jev_signal.py`（`STRONG_TYPES` + `event_type_tier` + `jev_signal_score`），改写 4 个**决策驱动**点——`api_common_stock.py:_pool_bear_news`（去 `min_conf=0.75` 过滤、改事件类型分层门：显式弱类型 price/supply 不进避雷池）、`api_jev_wordcloud.py`（去 conf 加权、改强弱类型权重 1.0/0.4）、`api_backtest_pages_a2.py`（强信号要闻榜 / 事件类型分层分布替代 conf 排序与直方图）、`api_news_intel.py:read_verdicts`（判读区 conf 次级排序 → 事件类型分层 `(tier_rank, -|signal|)`）；其余 `conf` 仅留作**显示字段**（避雷池/词云/要闻/日报分布）。5 文件 `py_compile` 通过、4 模块 import 冒烟通过；已落 `C:\Temp\PORTAL_API_RESTART.flag` 触发 8091 换血（冷启动守卫 `import portal_api` 校验）。**后续 2026-11-10 续补**：`portal/m_score.py` 的 `jev_conf` 5 核心评分指标亦替换为 `jev_signal`（强类型±1/弱±0.4 加权方向分），注册表 JSON/`score_guard.DEFAULT_CORE`/builder/两测试桩同步改，活 `/api/m/score/core` 验证 5 核心已含 `jev_signal`（值 -0.0064）
- [ ] 在更多交易日（含牛/熊/震荡 regime）跑 validator，扩大样本、算置信区间
- [ ] 若前向预测力始终 ≈ 0，则正式把 JEV 定位收敛为"事后解释器 + 语义层"，不在"预判"上投入
