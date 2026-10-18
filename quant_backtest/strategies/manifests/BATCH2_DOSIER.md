# RC-3 第二批收编档案（BATCH2_DOSIER）

- 工位：batch2-manifest（S1 提前起草）
- 日期：2026-10-18
- 计划依据：`量化板块策略收编与四域贯通研发计划_2026-10-18.md`（RC-3 第二批 4 策略）
- 字段口径来源：同第一批（`agent_platform/engine/strategy_config.py` 严格 6 字段 + `_provenance` 剥离约定）
- 本目录文件：4 份 `<strategy_id>.manifest.json` + 本档案
- 范式对齐：完全沿用 BATCH1_DOSIER 口径——permissions 恒等集、resource_budget 恰三键、模式一律 `BACKTEST+REPLAY+SHADOW` 不含 SIM_MARKET、allowed_data 用命名空间小写令牌（在第一批 `bars.*`/`artifacts.*` 基础上按需扩 `factors.*`/`config.*`，仍为向下兼容的令牌约定，校验器对 allowed_data 无枚举约束）

---

## 1. nine_dim（九维量化选股）

- **本体现状**：`quant_backtest/nine_dim.py`，两段式评分引擎：①零 API 本地粗筛（RPS50>80 + 额>2亿 + 量能/布林动能 + 剔ST，候选池 ≤400）；②候选池补 API 因子 → 九维声明式评分表（FACTORS 9 组 + PENALTIES 7 惩罚项，"None 不计分不扣分"铁律 + structural-void 豁免，cov≥0.75 才出完整评分）。**K33 重定位：总分语义反转——高分=拥挤警报（回避位，≥75 入警报池）非买入信号**。
- **输出形态**：打分截面（rows: code/score/cov/band）+ 警报池 JSONL（pool_type=alert）+ r5/r20 战绩缓存。不是 0/1 信号，是带覆盖度标注的分档打分。
- **数据依赖**：本批最宽——14 令牌。本地全市场日线（TDX/tushare 双通道）+ 指数日线（科创50）+ tushare 十类因子接口 + news.db JEV + 知识图谱特征（kg_features.parquet + Neo4j，全防御降级）。RPS50 由 `rps_resonance.batch_rps` 本地复算，不另授权。
- **S2 Agent 化预估**：**工作量最大（3~4 天），本批主要坑位**：
  1. 上下文装配：单票 propose 需要截面级 ctx（行业分位 gm_p70/pe_med/ev_mean、ind_top3、市场环境 env、动态调权 wdelta），不是单标的历史 K 线——要么整截面注入（1MB 预算按 rows≤400×detail 估的），要么改造成"粗筛+评分"两段调用；
  2. 不可判语义：FACTORS 各 j() 返回 True/False/None 三态且 None 影响 cov 分母，Verdict 契约必须能表达"不可判"（第一批 5 个纯 0/1 策略无此问题）；
  3. 慢接口缓存（fina 5 日 TTL / report_rc 当日缓存）在 Agent 侧的归属——建议留在 context 装配层，Agent 只做纯评分。
- **S3 对账**：基线 = `nine_dim_backtest.py` P2 回放（锚点 as-of 截断纪律完整，纯函数可复现）+ K33 规格。风险点：API 因子源的 as-of 快照依赖 `nine_dim_source_snapshot`，重放环境拿不到逐日历史回执的因子（news.db 2026-09-25 起、大宗快照缺失即 fail-closed）会整项 None——对账时"None 率"本身要作为对齐指标，不是异常。
- **S4 影子期**：10 交易日起步（本就是影子评分引擎，只写门户不推送）。

## 2. rps_resonance（RPS 相对强度多维共振）

- **本体现状**：`quant_backtest/rps_resonance.py`，四市场全市场 RPS（A股 tushare 50/120/250 复合 0.4/0.3/0.3；美/港/加密 zhice-db）+ 宽度法环境闸门（breadth≥0.55 偏暖放行 / <0.40 或 med20<-3% 一票否决 / 数据缺失 safe=None 缺维降权，S6 三态）+ 自选池四维共振（tech.35/jev.25/rps.25/env.15，缺维按比例降权，买入≥75/观察≥50；A股次新<250根不产生买入）。
- **输出形态**：截面榜单（RPS Top）+ 自选池逐票四维分与三档 action + 策略信号矩阵。建议枚举与 chan_monitor 的 buy/sell 语义不同（买入/观察/回避三档）。
- **数据依赖**：跨截面全市场如实申请——A 股全市场日线 + 三海外市场全市场收盘（zhice-db）+ news.db JEV + watchlist 配置。`--shadow-tech` 预研入口另消费 chanlun_pool，未申请（留 S2 决策补登）。
- **S2 Agent 化预估**：2~3 天。难点与 nine_dim 同族（截面上下文 + 三态闸门），但维度少（4 维 vs 9 维）、无 API 因子长尾；技术维复用第一批 4 策略 REGISTRY（宽表复刻已有数学等价判例），签名归一可行路径：单票 propose 拿"该票历史 close + 全市场环境摘要"，RPS 分位由装配层以全市场截面预算，Agent 只做四维合成。**海外市场数据（zhice-db 直连）在 REPLAY/SHADOW 模式下的可用性要先验证**——DB 拉取窗口仅 200/300 自然日，new_flag 无次新语义，回放侧需明示口径降级。
- **S3 对账**：基线 = 产线 `rps_latest.json` 快照 + `_strat_pos_wide` 复刻判例 + `test_rps_shadow_tech.py` 纯打分核。风险点：RPS 是全市场 rank，截面完整性（DB 缺符号、tushare 断供）直接改变分位数值——对账必须锁定同一份截面快照，不能用"重跑当天数据"。
- **S4 影子期**：10 交易日起步。

## 3. vol_target（波动率目标仓位缩放，仓位/风控型）

- **本体现状**：核心 = `backtest.py::_vol_scale`：scale=clip(target_vol/rv20, floor, cap)，默认 {target_vol:0.15, lookback:20, floor:0.25, cap:1.0}，乘在引擎持仓上（只降仓不加杠杆），无未来函数（scale(t) 只由截至 t 的窗口决定，有单测判例）。`voltarget_ab.py` 是 A/B 对照采集状态机（waiting→tracking，预注册判据 20261015 冻结 sha），纯纸面记录。
- **输出形态**：**仓位缩放系数 ∈ [0.25, 1.0]，不是选股列表/买卖信号**。叠加在任何上游信号之上。
- **数据依赖**：单标的日线 close（rv20），最小授权 `bars.daily`，本批最窄。
- **S2 Agent 化预估**：本体工作量小（0.5~1 天，纯函数+单测全覆盖）。**真正的坑在契约层而非 manifest 层**：6 字段模型（allowed_data/permissions/modes/budget）**表达无障碍，本清单未做任何字段扩展**——permissions 仍是 propose+read_context（它确实只 propose、只读上下文），modes 也无需特殊档位。差异落在 S2 的 Verdict 语义：现有五档 recommendation（buy/sell/…）是"选股立场"，vol_target 要表达"仓位缩放系数"，建议 S2 约定其一：① recommendation=hold + confidence/scale 字段承载系数；② context 协议新增 position_scale 产出位。**无论哪种都是 Agent 契约扩展，不属于 manifest 校验器 6 字段模型**——这是本批要向聚合层 adapter 设计者明确提出的一个问题（第一批 6 个全是选股立场型，第二批引入了首个非选股型）。
- **S3 对账**：基线最扎实 = `test_voltarget.py`（与 `backtest.py.bak_20261014_vt` 字节级等价 + 无未来函数判例）+ `_voltarget_ab_result_20261014.json` A/B 采集。风险点低；唯一注意 rv20 的窗口起点对齐（lookback=20，不足窗口时 scale 语义要在重放侧复现）。
- **S4 影子期**：10 交易日起步（与 A/B 采集位 60 交易日评估期对齐推进）。

## 4. qpzm（青萍之末）

- **本体现状**：`qpzm_backtest.py`（全量参数网格锚：wide 价矩阵 → RPS 复合分 + 宽度闸门 + tech 分 → combine_score 买入/观察双阈值）+ `qpzm_daily_batch.py`（K238 逐日批次回测：T 收盘信号→T+1 开盘买入→持有 N∈{5,10,20} 日收盘卖出，成本 0.15%，基准=同日全体可交易等权，K254 后复权+一字板剔除，K251 逐日盯市）。live/replay 双口径判例成熟（rps_daily_batches.jsonl，live 永不覆盖）。
- **输出形态**：截面信号（买入/观察/回避三档）+ 批次绩效报告。与 rps_resonance 同族但独立实现（自算 RPS/闸门，口径一致）。
- **数据依赖**：A 股全市场 wide 价矩阵（bars.daily）+ 日批归档（分位注入基准+批次回测输入）+ watchlist（backfill 复算池限定）。
- **S2 Agent 化预估**：2 天。纯计算层与产线已分离（单测合成数据全覆盖、不依赖本地 parquet），是四者中签名最接近"截面信号流（chanlun_pool 式）"的：combine_score 直接产出 (date, code, action) 流。坑点：①W3-4 分位注入消费滚动 60 日 live 历史——Agent 侧要么把基准注入 context，要么豁免分位字段（口径要在 S2 定死，否则 live/replay 分位对不齐）；②K254 后复权因子链（own-bar pre_close）是装配层职责，Agent 拿到的应是后复权价，否则与批次回测基线漂移。
- **S3 对账**：基线 = `qpzm_backtest` 全量网格锚 + `rps_batch_backtest.json` 批次回测 + `test_qpzm_backtest.py`/`test_qpzm_percentile.py` 判例。四者中对账基建最全，风险最低。
- **S4 影子期**：10 交易日起步（live/replay 并行已有判例）。

---

## 批次汇总

| strategy_id | allowed_data 令牌数 | budget (timeout_s / conc / ctx_bytes) | modes | S2 预估 | 类型 |
|---|---|---|---|---|---|
| nine_dim | 14 | 10 / 1 / 1MB | BT+REPLAY+SHADOW | 3~4d，截面 ctx+三态不可判坑 | 选股打分（警报语义） |
| rps_resonance | 6 | 10 / 1 / 1MB | BT+REPLAY+SHADOW | 2~3d，全市场截面 rank 坑 | 截面共振选股 |
| vol_target | 1 | 5 / 1 / 256KB | BT+REPLAY+SHADOW | 0.5~1d，Verdict 契约坑 | **仓位/风控型（非选股）** |
| qpzm | 3 | 10 / 1 / 1MB | BT+REPLAY+SHADOW | 2d，后复权+分位基准坑 | 截面信号流 |

## 建议收编顺序与理由

**qpzm → rps_resonance → vol_target → nine_dim**

1. **qpzm 最先**：纯计算层与产线口径已分离（合成数据单测、live/replay 双判例）、输出是标准截面信号流（最接近聚合层 adapter 已有形态）、对账基建最全——S1→S5 全链路风险最低，适合作为第二批打样。
2. **rps_resonance 次之**：与 qpzm 同族（RPS/闸门/共振口径一致），可复用 qpzm 立起的截面上下文装配与三档 action 契约；增量坑只有海外三市场数据可用性，在 qpzm 契约定型后处理成本最低。
3. **vol_target 第三**：本体最小且有字节级等价单测，但它引入**首个非选股型策略**——宜在 qpzm/rps 把选股型 Verdict 契约跑通后，单独做"仓位缩放语义"的契约扩展样本，避免开局就碰契约变更。
4. **nine_dim 最后**：数据源最宽（14 令牌）、上下文最大（截面+逐票 detail）、三态不可判语义最复杂、对账依赖 as-of 因子快照——等前两者把截面装配与三态表达（vol_target 的契约扩展可先探路）沉淀成公共能力后再收编，边际成本最低。

## 治理记录

- 本工位写权仅 `quant_backtest/strategies/manifests/`（4 JSON + 本 MD），未触碰 strategies.py / nine_dim.py / rps_resonance.py / voltarget_ab.py / qpzm_*.py / backtest.py / agent_platform / strategy_layer / execution_engine.py。
- qpzm_backtest.py 与计划文档原文在本工位读取时被审批超时拦截，未强读；qpzm 形态经 `qpzm_daily_batch.py` / `test_qpzm_backtest.py` / `qpzm.manifest.json` 引用的公开 API（load_wide/cross_rps/env_gate/tech_score/combine_score/TH_CUR/RPS_W_CUR/PERIODS）交叉确认，不影响 manifest 口径正确性；若 S2 需要其内部实现细节，由有权限工位补读。
- commit 显式 pathspec 提交（`*.manifest.json` 受 .gitignore:65 全局忽略，add -f 显式纳入），结束时 diff=0 自证。
