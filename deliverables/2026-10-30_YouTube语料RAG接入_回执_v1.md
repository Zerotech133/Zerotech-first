# RAG 接入（AI/111111）· 完成回执

## 成果
- 语料 RAG 索引：**TOP 800 core_quant 视频 → 800 篇 markdown → 4,926 chunks / 117.0 MB**（AI/111111 KnowledgeBase 原生 schema）
- 索引：`C:\项目\知识库\50_RAG\rag_index_corpus.json`；文档：`50_RAG/docs/*.md`
- 模型契约：bge-m3（embedding）+ qwen3.5:9b（RAG chat），与 AI/111111 既有配置一致

## 验证问答（修复后）

| 问题 | 耗时 | 回答质量 |
|---|---|---|
| 如何避免回测过拟合？ | 35.8s | 有据回答 |
| walk-forward 验证怎么做？ | 8.1s | 有据回答 |
| 交易成本与滑点如何影响策略？ | 6.8s | 有据回答 |

### 示例（交易成本与滑点）

根据提供的上下文，交易成本（如佣金、手续费）和滑点对交易策略的影响如下：

1.  **导致回测结果失真**：许多在 TradingView 上表现良好的策略，如果不加入费用和滑点，其结果会具有误导性（very misleading）。
2.  **降低实际收益**：如果不在策略测试器结果中包含佣金和滑点，策略的实际盈利能力会被高估。
3.  **增加风险**：忽略这些成本会使策略看起来比实际情况更优，从而在实盘中面临更大的风险。

文中特别提到：“你绝对必须将费用和佣金以及滑点包含在策略测试结果中……如果不包括这些，策略会非常具有误导性。”

## 顺带修复的两个真实 BUG（已合并）
| # | 工单 | 文件 | 问题 | 修复 |
|---|---|---|---|---|
| 1 | WO-KB-RAG-20261030 | `AI/111111/knowledge_base.py` | `_embed` 把全部 chunk 一次性发 `/api/embed`，批量过大 → **HTTP 400**（语料建索引必崩） | 分批 32 条调用 + 回归单测（2 passed） |
| 2 | WO-KB-RAG2-20261030 | 同上 `_OllamaClient.chat` | qwen3.5 为 thinking 模型，未关思考 + 超时 120s → **答案超时/空返回** | `think=false` + `num_predict=500` + 超时 300s |

## 待办
- **门户知识页**：需新增 `portal/api_kb_corpus.py`（检索/问答接口）+ `portal/kb_corpus.html` + 8091 重启激活（L2，需老田批准）；
- **全量索引**：62,401 docs 已续跑（进度约 30%，ETA 1.5h）；完成后可把 RAG 索引扩到全量语料；
- P3 字幕转写：yt-dlp 已装、YouTube 网络仍超时。

## 交付物
| 项 | 路径 |
|---|---|
| RAG 索引 | `C:\项目\知识库\50_RAG\rag_index_corpus.json` |
| RAG 文档 | `C:\项目\知识库\50_RAG\docs\`（800 篇） |
| 问答验证 | `C:\项目\知识库\90_报告\rag_integration_qa.json` |
| 构建脚本 | `tools/kb_corpus/build_rag_index.py`（已合并） |