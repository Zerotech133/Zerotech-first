# P1 执行回执 · RAGAS-lite 评测 + Phase3 网络探测（2026-10-30 20:10）

## ① RAGAS 风格评测（免安装，qwen3.5 judge，15 条 golden）

| 指标 | 值 | 解读 |
|---|---|---|
| context_precision | **0.933** | 检索片段与问题相关度很高 |
| context_recall（关键词近似） | **1.0** | 期望关键词都在检索上下文中 |
| answer_relevancy | 0.617 | 中等 |
| faithfulness（非弃答 6 条） | 0.417 | judge 未校准，仅参考 |
| **弃答率** | **9/15 = 60%** | 回答「资料未覆盖」 |

### 关键结论（比指标更重要）
1. **检索不是瓶颈**：precision 0.933、recall 1.0 → 相关文档都找到了；
2. **内容深度是瓶颈**：60% 的问题因「标题/描述/评论只有浅层信息」而弃答 —— 这正是 **Phase 3 字幕转写**要补的；
3. 因此：**不应继续投入 RAG 调参/reranker**（已实测 -2.4pp），应把资源放在 Phase 3（等网络）与内容补强；
4. faithfulness 指标本身不可靠：明显有据的答案（如夏普比率）被判 0，初版与 v2 三档 rubric 都偏低 → 不作为质量结论，仅记录口径缺陷。

## ② Phase 3 网络探测（已自动化）
- 新脚本 `tools/kb_corpus/p3_probe.py`：每日探测 YouTube；可达则自动跑 `p3_fetch_subs.py --limit 100` + `p3_distill.py --limit 100`；不可达写 `C:/Temp/kb_p3_probe.json`。
- 已接入 **BackupDaily 第 1g 段**（PowerShell 语法 SYNTAX_OK）；
- 手工实测（19:50）：`youtube_reachable=false, action=blocked`。

## ③ 未执行项
- **8091 服务化（NSSM）**：需管理员权限 + 系统配置变更 → 等你批准；
- ragas 官方包：阿里云 pip 两次卡死 → 用本地 judge 等价实现替代（已交付）；如需官方包，建议直接下 wheel。

## 交付
- 脚本：`tools/kb_corpus/phase5_ragas_lite.py`、`phase5_faithfulness_v2.py`、`p3_probe.py`
- 集成：`jev-eval/backup_daily.ps1` 第 1g 段
- 报告：`90_报告/phase5_ragas_lite.{json,md}`、`phase5_faithfulness_v2.json`