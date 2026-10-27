# 判例 · KGStockAnalysis → zerobound-kg 改名归一（2026-10-27）

> 依据：AGENTS.md §8「工程命名与别名注册表」+ 改名纪律（①查引用链 ②物理改名与引用归一同批 ③可选旧名 junction 标注过期 ④新代码引用物理名/注册别名）。
> 工单：`WO-GUANYU-KGRENAME-20261027`（关羽线·复审后续单）。

## 1. 事实核对

| 项 | 结果 |
|---|---|
| `C:\项目\KGStockAnalysis`（旧物理目录） | **不存在**（2026-10-17 已物理改名） |
| `C:\项目\zerobound-kg`（新物理目录） | 存在 |
| `C:\项目\老田的AI助手\zerobound-kg`（登记别名/junction） | 存在，链式 `C:\aios\zerobound-kg` |
| `E:\KGStockAnalysis`（旧机来源路径） | 本机无 E: 盘（2026-10-25 EXEC-R11 已核实），仅历史留档 |

## 2. 引用链盘点（全仓 377 处）

| 类别 | 数量 | 处置 |
|---|---:|---|
| 代码文件（`.py/.ps1`） | **18** | 逐条核对：**无活路径硬编码**（`start_kg_sync.ps1` 已用 `$kg_root` 变量桥接）；活跃文件注释补"旧名"标注（本单） |
| 配置文件（json/yaml/toml） | 187 | 其中 **176 处为生成数据** `work/memory_rag/index.json`(134)、`work/p1/rag/index.json`(42)——RAG 索引是历史语料，**不改**；其余 11 处属 `zerobound-kg` 项目自身/归档，**不由本仓修改** |
| 文档（md） | 172 | `.trae/` 13、`zerobound-kg/docs/planning` 41、`docs/plans` 27、`docs/ops` 12、`deliverables` 25、`tmp/` 5、`AGENTS.md` 自身登记表 2 等——**历史文档保留原貌**；AGENTS §8 已是权威映射 |

**结论：真实路径引用已归一（`$kg_root` 变量），剩余为历史文本/生成数据，不做批量替换**（批量替换会污染历史证据与生成索引，违反"判例循判例"与数据不动原则）。

## 3. 本单改动

- 活跃代码中的旧名注释/文档字符串 **7 文件 9 处**，统一标注为 `zerobound-kg（旧名 KGStockAnalysis，2026-10-17 已改名）`：
  - `portal/risk_feedback_ledger.py`、`quant_backtest/{kg_client,kg_schema,nl_query}.py`、`tools/riskguard_postmortem.py`、`quant_backtest/start_kg_sync.ps1`、`jev-eval/n1_check.ps1`
- **不改**：`zerobound-kg/` 项目内文件（独立项目线）、`.trae/` 第三方工具文档、`work/**/rag/index.json` 生成数据、`deliverables/` 历史交付物、`.txt` 日志。

## 4. 新代码纪律（生效口径）

1. 一律用物理名 `C:\项目\zerobound-kg` 或登记别名 `C:\aios\zerobound-kg` / `C:\项目\老田的AI助手\zerobound-kg`；
2. 禁止新增 `E:\KGStockAnalysis`、`C:\项目\KGStockAnalysis` 等旧路径；
3. 历史文档要提旧名时，写成 `zerobound-kg（旧名 KGStockAnalysis）`，便于检索与读者理解；
4. 若未来发现仍有**活路径**引用旧名，按本判例升级为 BUG 单处理（改路径 + 测试）。

## 5. 回滚

`git revert -m 1 <本单合并提交>`（仅注释/文档字符串，无行为变化）。
