# RC-1 第一批收编档案（BATCH1_DOSIER）

- 工位：batch1-manifest（S1 步骤）
- 日期：2026-10-18
- 计划依据：`C:/项目/老田的AI助手/量化板块策略收编与四域贯通研发计划_2026-10-18.md`（RC-1 第一批 6 策略）
- 字段口径来源：`agent_platform/engine/strategy_config.py`（严格 6 字段）+ `agent_platform/engine/strategy_registry.py`（身份/版本/能力/生命周期）+ `agent_platform/engine/strategy_discovery.py`（module/class/strategy_id 装配三件套）
- 本目录文件：6 份 `<strategy_id>.manifest.json`（本档案）

## 与 agent_platform 字段口径的对齐说明

1. **严格 6 字段**：`strategy_config.py::_manifest` 拒绝任何未声明字段。本批 manifest 全部只含 6 个业务字段 + 1 个 `_provenance` 登记字段。**差异记录**：`_provenance` 不在 `_REQUIRED_FIELDS` 白名单内，聚合进 `strategy_config.json` 供 `load_config` 消费时**必须先剥离**（各 manifest 的 `_provenance.note` 已内嵌同一声明）。若后续把 `_provenance` 提升为一等字段，属 agent_platform 校验器改动，不在本工位权限内。
2. **permissions 恒等集**：校验器要求 `permissions` 必须且只能为 `["propose", "read_context"]`，本批 6 份一致，无裁量空间。
3. **resource_budget 恰好三键**：`timeout_s`（有限正数）/ `max_concurrency`（正整数）/ `max_context_bytes`（正整数），多一键少一键都拒。
4. **运行模式**：`ALLOWED_MODES = {BACKTEST, REPLAY, SHADOW, SIM_MARKET}`。本批一律只申请 `BACKTEST+REPLAY+SHADOW`，**不含 SIM_MARKET**——未过 G3 门不得申请（计划文档 W4 G3 授权链节点）。
5. **allowed_data 无枚举约束**：校验器只要求非空、无重复的字符串数组。本批采用命名空间小写令牌（`bars.daily` / `bars.1m` 等 / `artifacts.chanlun_pool`），比既有示例（`bars`/`closes`/`features`）更细粒度，属向下兼容的约定升级，不与任何既有机制冲突；若与模板工位（pipeline-fw 的 manifest_template.json）口径撞车，以 agent_platform 机制为准。
6. **发现的问题**：`strategy_discovery.load_manifest` 的装配清单（module/class/strategy_id）与 `strategy_config.py` 的运行清单（6 字段）是**两套清单**，S2 Agent 化后需要分别维护装配条目与运行条目；本批 manifest 只覆盖运行清单口径，装配三件套留待 S2 与 Agent 类名一起定。

---

## 1. ma_cross（均线交叉）

- **本体现状**：`quant_backtest/strategies.py::ma_cross`，fast MA 上穿 slow MA 做多、下穿空仓，逐帧 0/1 序列，无状态。参数默认 `{fast:5, slow:20}`（PARAM_DEFAULTS）。
- **回测基线**：`quant_backtest/runner.py` backtest 引擎（信号 shift(1) 次日开盘生效、仅做多）；参数行取 `CFG["strategies"]["ma_cross"]`，缺省回落 PARAM_DEFAULTS。
- **数据依赖**：日线 close（最小授权 `bars.daily`）。
- **S2 Agent 化预估**：工作量小（约半天）。逐帧纯函数 → propose(context) 直接映射单标的 Verdict；坑点仅在把"逐帧仓位序列"语义裁成"单标的一次立场产出"，需约定取序列末帧。
- **S3 对账**：基线文件 = runner.py 回测输出；预期一致率风险点低——ma_cross 逐帧无状态、无 ffill，是 6 个里最易对平的。
- **S4 影子期**：10 交易日起步。

## 2. rsi_reversal（RSI 反转）

- **本体现状**：`quant_backtest/strategies.py::rsi_reversal`，事件帧语义：RSI<oversold 触发做多、>overbought 触发空仓，中间 `ffill().fillna(0)` 持有至反向事件。参数默认 `{period:14, oversold:30, overbought:70}`。
- **回测基线**：runner.py 同上；参数回落 PARAM_DEFAULTS。
- **数据依赖**：日线 close（`bars.daily`）。
- **S2 Agent 化预估**：工作量小（约半天）**但有坑**：单点 propose 拿到的是截断上下文，事件帧语义要求"距上次反向事件以来的状态"可从 context 内历史 K 线重算——context 窗口必须足够长（覆盖最近一次反向触发），否则 Verdict 与逐帧序列不一致。这决定 `max_context_bytes` 下限。
- **S3 对账**：预期一致率风险点 = context 窗口长度不足导致 ffill 状态重算起点不同；对账脚本须用与回测等长的预热窗。
- **S4 影子期**：10 交易日起步。

## 3. boll_breakout（布林突破）

- **本体现状**：`quant_backtest/strategies.py::boll_breakout`，收盘破上轨做多（`replace(0, NA).ffill()` 持有），跌破中轨空仓。参数默认 `{period:20, k:2}`。
- **回测基线**：runner.py 同上。
- **数据依赖**：日线 close（`bars.daily`）。
- **S2 Agent 化预估**：工作量小（约半天），与 rsi_reversal 同款事件帧坑（突破日之后一直持有的状态需从历史重算）。
- **S3 对账**：风险点同 rsi_reversal（ffill 状态重算 + 布林带预热窗 ≥ period）。
- **S4 影子期**：10 交易日起步。

## 4. trend_follow（趋势跟随）

- **本体现状**：`quant_backtest/strategies.py::trend_follow`，close 在 N 日均线上且均线斜率（`diff(slope_lookback)`）为正 → 做多，逐帧无状态。参数默认 `{n:20, slope_lookback:5}`。
- **回测基线**：runner.py 同上。
- **数据依赖**：日线 close（`bars.daily`）。
- **S2 Agent 化预估**：工作量小（约半天），逐帧纯函数，最省事的之一；预热窗 ≥ n + slope_lookback。
- **S3 对账**：无 ffill，风险点低。
- **S4 影子期**：10 交易日起步。

## 5. chanlun_li122（缠论多级别联立，D2 二梯队）

- **本体现状**：`quant_backtest/chanlun_li122.py`，纯函数层：`judge(upper_signal, mid_bars, low_bars, ruleset_key)`，上级买向信号 + 中级别底分型成立 + 低级别不新低，三查全过才放行。RULESETS 四档（A 默认 / B 简化 / A1 仅一买 / A2 宽松）。复用 `chanlun_fractal` 的包含合并与分型识别，不重写缠论结构。无 IO，输入序列内部截断拷贝，只用 dt ≤ 信号 dt 数据（重放口径无未来函数）。
- **回测基线**：主 = `quant_backtest/backtest_li122_vs_single.py`（D2 联立 V1 对照：达标闸门=假阳率降 ≥30% 且平均净收益不降；信号窗 2026-06-01~2026-09-15）；辅 = `chanlun_rotation_v6c_result.json`（校验上级信号源 chanlun_signal 口径未漂移）。
- **数据依赖**：多级别 Bar 并集 `bars.1m / bars.5m / bars.30m / bars.daily`（档 A 需 1m 评估、档 B 需日线上级）；manifest 按最小授权取 RULESETS 全集并集。
- **S2 Agent 化预估**：**工作量最大（2~3 天），本批主要坑位**：
  1. 输入形态是"上级信号 + 两路 Bar 序列"三元组，不是单标的开门见山的快照——context 装配层要为它专门造多级别上下文（这正是 `max_context_bytes` 给到 1MB 的原因）；
  2. 上级信号来自 chanlun_signal 扫描产出，S2 需决定信号源进 context 的注入方式（复用信号池 or 现算），复用信号池则数据权限还要加 `artifacts.chanlun_pool`（本次未申请，留 S2 决策时补登）。
- **S3 对账**：联立判定**是纯函数可复现**（同输入必同输出，全部判定只用 dt ≤ 信号 dt 数据）——这是好消息；风险点在**输入侧**：分钟 Bar 的取样起点（回测脚本 DATA_START=20260401 预热）与 `_dt_index` 找不到 dt 时"取末根保守"的容错语义，重放环境与回测环境的数据切片必须逐字节一致，否则 checks 的 detail 会对不齐。
- **S4 影子期**：10 交易日起步，建议影子期同时跑 A/A1 双档对照（延续回测的组合对照纪律）。

## 6. chan_monitor（缠论信号池监控，既有在册，已过 S5）

- **本体现状**：`quant_backtest/strategy_layer/adapters/chan_monitor.py::ChanMonitorAdapter`，只读 chanlun_scan 产出的信号池 JSONL（`data/chanlun_pool{,_us,_hk}.jsonl`），分市场选日（QA-20261017 修正后的组内最大 dt 口径），类型分位映射 + 共振加成 → Signal。降级语义：池缺失/损坏/无信号一律返回空列表不 raise。在 `strategy_layer/strategy_registry.json` enabled 列表内，权重 0.5。
- **回测基线**：本身是聚合层在册策略（已过 S5），无独立回测基线；信号质量上游锚 = `chanlun_rotation_v6c_result.json`。
- **数据依赖**：按 adapter 实际依赖最小授权 = `artifacts.chanlun_pool`（三池 JSONL），不申请原始 K 线。
- **S2 Agent 化预估**：豁免（已收编）；本批仅补 manifest 档案。若后续按 AP StrategyAgent 契约再包一层 propose→Verdict，属增量工作（约 1 天），风险点是把 adapter 的"买点→buy / 卖点→sell"映射到五档 recommendation 枚举（建议 sell→"sell"、buy→"buy"，strong 档按共振加成分档）。
- **S3 对账**：豁免；若补做，对账对象 = adapter 单测与信号池样例（QA-20261017 多市场选日修正已有判例）。
- **S4 影子期**：**豁免**（已过 S5，在册运行中）。

---

## 批次汇总

| strategy_id | allowed_data | budget (timeout_s / conc / ctx_bytes) | modes | S2 预估 | S4 |
|---|---|---|---|---|---|
| ma_cross | bars.daily | 5 / 1 / 256KB | BT+REPLAY+SHADOW | ~0.5d，低险 | 10 交易日 |
| rsi_reversal | bars.daily | 5 / 1 / 256KB | BT+REPLAY+SHADOW | ~0.5d，ffill 状态坑 | 10 交易日 |
| boll_breakout | bars.daily | 5 / 1 / 256KB | BT+REPLAY+SHADOW | ~0.5d，ffill 状态坑 | 10 交易日 |
| trend_follow | bars.daily | 5 / 1 / 256KB | BT+REPLAY+SHADOW | ~0.5d，低险 | 10 交易日 |
| chanlun_li122 | bars.1m/5m/30m/daily | 10 / 1 / 1MB | BT+REPLAY+SHADOW | 2~3d，多级别 context 坑 | 10 交易日（A/A1 双档） |
| chan_monitor | artifacts.chanlun_pool | 5 / 1 / 512KB | BT+REPLAY+SHADOW | 豁免（已过 S5） | 豁免 |

## 治理记录

- 本工位写权仅 `quant_backtest/strategies/manifests/`（6 JSON + 本 MD），未触碰 strategies.py / chanlun_li122.py / agent_platform / strategy_layer / execution_engine.py / xq_classify.py。
- commit 显式 pathspec 提交，结束时 diff=0 自证。
