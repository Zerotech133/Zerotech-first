# 多 Agent 卖点研判平台 · MVP 计划

## Context（为什么做）

用户愿景：将「SellPoint AI 卖点研判」从**单一 agent** 演进为**多 agent 平台**——多个独立 agent 各用不同卖法研判同一批 A 股卖出信号，对照真实走势找最优解，为后续模型训练积累数据地基。

组织范式（用户原话）：
**投研机构（多 agent 出研判） → 下发指令 → 操盘手（各自择时择价） → 找到最优解 → 便于训练模型**

本仓已有同构先例可直接复用（探索已确认）：
- `quant_backtest/stock_council.py`：六视角 agent 注册表 + 主持人聚合 + 分歧度 `dissent_hat`
- `quant_backtest/multi_agent.py`：specialist 注册表 + router/synthesizer 范式
- `quant_backtest/agent_tools.py`：(ok/data/err) 统一只读工具契约
- `tools/triples_outcome_backfill.py`：tushare 真实收益回填能力
- 现有 `sellpoint_ai.py`（67 张卡已落盘）为演进基底

## MVP 目标（影子先行 · 不动引擎 · 信息层铁律）

1. **多 agent 分歧采集**：把 `sellpoint_ai.py` 改造为「N 个投研卖点 agent」并行研判同一批卖出信号，各自独立出「卖点强度 + 动作建议 + 理由」。
2. **操盘手择时对照**：每个 agent 建议对接模拟账本，差异化择时规则记录实际出场 → 同源信号多出口对照。
3. **训练数据地基**：为每个 (信号, agent) 回填后续 N 日真实收益 → 攒 `(signal, agent_param, action, real_ret)` 四元组 → 锦标赛式统计哪个 agent/哪个 regime 最优。
4. **门户/任务**：/sellpoint 按 agent 分组展示分歧；复用 SellPointAI 15:52 任务。

**本期明确不做**（避免过度设计）：不动缠论核心引擎参数（`BEICHI_RATIO`/`BI_MIN_GAP` 变体 → 二期「真引擎分歧」）；不做 synthesizer 融合决策（先锦标赛对照，融合二期）；不引入独立持仓假设（全员共享持仓池）。卖法差异本期载体 = **agent persona（prompt）+ 卖点类型侧重 + 动作偏置 + 择时规则**，不改核心引擎，风险最小。

## 组件设计

### 1. Agent 注册表（改 `sellpoint_ai.py`，学 `stock_council.py` L62-L120）
新增内置 agent 定义 `AGENTS`，每个含：`agent_name/method/persona_prompt/侧重卖点类型/动作白名单偏置/出彩风格/择时规则`。MVP 首批 2 agent：
- `cons_1sell` 保守趋势背驰派：主抓 **1卖/tbe**，强度偏保守，动作偏 `清仓/止盈`，择时=检出日收盘一次性清仓。
- `aggr_3sell` 激进盘整派：主抓 **3卖/2卖**，强度偏谨慎，偏 `减仓/观望`，择时=持有至信号确认+分批止盈（复用 `chanlun_paper` 天数规则）。
- 各 agent 独立调一次 LLM（qwen3:8b-8k），每信号产出独立卡片。

### 2. 研判引擎参数化（改 `sellpoint_ai.py` L143-L199 / L210-L244 / L63-L77）
- `_facts()` 特征模板按 agent 注入侧重（是否强调 tbe/npv/浮盈）。
- LLM prompt 按 agent persona 生成（学 L69-L77），动作白名单、强度判定偏置按 agent。
- 幂等 key 从 `(code,type,dt)` 扩展为 `(agent_name,code,type,dt)`。

### 3. 择时对照（对接 `data/chanlun_paper.json`）
- 每 agent 的卡片携带 `timing`（择时规则快照）+ 关联模拟账本，落盘出场日期/出场价。

### 4. 训练数据地基（改 `tools/triples_outcome_backfill.py` → 复用作 sellpoint_bias）
- 为已落盘的 (agent, code, type, dt) 回填 `signal_dt` 后 N 日真实收益（tushare daily），写 `data/sellpoint_bias.jsonl`（锦标赛对照流）。
- N 与 COLLATE_WAIT：默认 5 交易日后回填首判，后续滚动。

### 5. 数据 schema（升迁兼容）
`data/sellpoint_cards.jsonl` 原行保留，新增行加 `agent_name/method/stance/timing` 列；已 67 卡标 `agent_name=legacy_cons` 兼容。

### 6. 门户（改 `portal/portal_api.py` /sellpoint）
统计行加「agent 分布」；卡片表加 agent 列，按 agent 分组/过滤；分歧视图（同信号多 agent 并排）。

### 7. 任务（复用）
复用 `SellPointAI` SYSTEM 任务每日 15:52，跑多 agent；`sellpoint_bias` 回填挂 15:35 链或独立每日任务。

## 文件改动清单
- `quant_backtest/sellpoint_ai.py`（核心：AGENTS 注册表 + 参数化 + 择时 + schema 扩展）
- `quant_backtest/tools/triples_outcome_backfill.py` 或新增 `tools/sellpoint_bias_backfill.py`（复用回填能力/独立脚本）
- `portal/portal_api.py`（/sellpoint 按 agent 展示）；`quant_backtest/config.json`（sellpoint 区加 agents 开关）
- `quant_backtest/data/sellpoint_cards.jsonl`（升迁）、新增 `data/sellpoint_bias.jsonl`
- 记档 `INTEL_RD_PLAN.md` 新 K 节（验号）+ 资产册

## 验证
1. `py_compile` sellpoint_ai + 回填脚本 rc=0。
2. 影子首跑：2 agent × 同一批 fresh 信号 → 卡片带独立 agent_name/action（抽查 2-3 行）。
3. `--status` 显示按 agent 分布；`/sellpoint` 浏览器视觉验证（分歧并排、无 JS 报错）。
4. `sellpoint_bias` 回填首判 rc=0，行含真实收益。
5. SellPointAI 任务手动触发 rc=0；结论控 usls。
6. 门户回归（/fut、/bt 无损）。

## 二期预留（影子达标后）
- 引擎真分歧（BEICHI_RATIO/BI_MIN_GAP 参数变体 → 不同 agent 看不同信号集）
- agent 独立持仓假设；synthesizer 融合决策；按 regime 最优 agent 画像训练。