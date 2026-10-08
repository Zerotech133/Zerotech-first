# K-STOCK 线 · 多团队选股策略 Agent（S1 PoC）

> 立项：K-STOCK-002 蓝图 v2（2026-10-08 13:50，以菜鸟 thunder 体系为蓝本，v1 作废）
> 上游文档：`docs/plans/2026-10-08_132000_多团队选股Agent立项蓝图.md`、研发计划 v6（deliverables）
> 定位：工作站第二增长曲线——量化决策层的「多 AI 投研团队」生产化

## 蓝本资产（全部源码级确认，位于 `E:\老田的AI助手\菜鸟\`）

| 资产 | 位置 | 用途 |
|---|---|---|
| 团队编排数据模型 | `thunder_model/all_ddl.sql` L595 `ai_team_role`（node_key/parent_node_keys/is_parallel/debate_partner_key/max_debate_rounds） | 本线 `config/team_poc.json` 的 schema 依据 |
| AI 团队编排引擎参照 | `thunder-model-admin` TeamTaskServiceImpl（851 行 Java） | `kstock/orchestrator.py` 的 Python 重实现蓝本 |
| 六维 24 因子 | `thunder_model/thunder_model.sql` `stock_weight_factor`（WEIGHT/PENALTY，阈值在 factor_name） | S2 评分引擎输入（`kstock/scoring.py` 已落解析器） |
| 人设库 | `ai_chat_role` 1036 角色 system_message | S2 提取保守/激进人设替换 S1 简化人设 |
| TDX 因子引擎 | `TDX-Trading/backtest_engine.py`（Alpha101/Qlib158/GTJA191+资金面/情绪面） | S2 评分算子库 |

## S1 PoC 架构（本目录）

```
run_poc.py                    # 日终 runner（单股/--batch 批量）
config/team_poc.json          # 五节点 DSL（ai_team_role 字段对齐）
kstock/
  llm.py                      # GLM 8098(预留) → Ollama 11434 qwen3.5:9b(现役) → mock 降级三态
  orchestrator.py             # 拓扑分波/并行屏障/辩论链(相似度>0.8 收敛)/节点降级
  nodes.py                    # 五节点人设+JSON schema 契约
  scoring.py                  # 24 因子表加载器（S2 打分引擎挂点）
tests/test_orchestrator.py    # 5 用例（全绿 0.18s）
```

流程：**T1 激进多头 ⇄ T2 保守空头**（辩论≤2 轮，词面相似度>0.8 提前收敛）∥ **N1 新闻情绪** ∥ **F1 基本面** → **D1 最终决策**（五档+置信度+目标价区间+风险等级）。

## S1 判据（蓝图 §三）

1. 5 个交易日**无人干预**跑通（runner 挂计划任务/automation）
2. 打分-次日收益**秩相关>0**（D1 输出 vs 次日行情，对照脚本 S2 挂账）
3. **lineage 可追溯**（data/poc_*.json 含 trace+raw 全留痕，已满足）

## 运行

```powershell
$PY = "C:\Program Files\Python311\python.exe"
& $PY -X utf8 k_stock\run_poc.py "600519.SH 贵州茅台"            # 单股真跑
& $PY -X utf8 k_stock\run_poc.py --batch k_stock\data\watchlist.txt  # 批量
& $PY -X utf8 -m pytest k_stock\tests -q                          # 自测
```

## 已知限制（S1 范围外）

- N1/F1 输入 context 为手工文本（S2 接 jev 新闻流+Tushare 财务）
- 六维评分未接入（S2，scoring.py 已备解析器）
- A1 投资建议节点并入 D1 前置（S2 拆出）
- GLM 8098 当前拒连（挂账 K275-F4；Ollama 路径已可用）
- 相似度用词面 Jaccard（S2 换 bge-m3 向量）

## 状态

- [x] D0 破土（2026-10-08）：骨架+5 测试绿+mock 全链+Ollama 真跑验证
- [ ] D0 正式：数据接入（N1=jev 流/F1=Tushare）
- [ ] D1-D5：无人干预连续跑+秩相关对照
