# 诸葛亮 P1-2：LlamaIndex RAG 方案与 v0 工单（2026-10-27）
> STATUS: pending_approval（v0 待实施；工单 WO-LEGION-ZHUGE-P1RAG-20261027 已 done=立单；owner: ZHAOYUN；2026-11-07 前确认）



- 工单：`WO-LEGION-ZHUGE-P1RAG-20261027`
- 前置：P1-1 已把 grounding（关键词检索 docs + `[S#]`）接入 L5，引用率 8/8=100%；本单升级检索质量，目标不是「有引用」而是「引对、答对」。
- 状态：**方案已立单；v0 待实施**。
- 单源口径：本计划属诸葛亮角色系列，遵循 `2026-10-26_诸葛亮角色总卡_v0.1_待签.md`。

## 1. 为什么不能停在前一版

P1-1 的 grounding 是**关键词扫描**（按行打分、取前 3 条）：
- 优点：零依赖、可解释、已能出引用；
- 缺点：同义/语义召回弱；长文档只看单行；跨文档综合差；无索引/缓存；docs 600+ 篇时噪声大。

## 2. 目标与验收

| 项 | 目标 |
|---|---|
| 检索召回 | factual 8 题，**recall@3 ≥ 80%**（相关文档进入前 3） |
| 引用正确 | 引用来源确实支撑答案（人工抽检 10 条，错误率 ≤10%） |
| 答案命中 | factual 关键事实命中率 ≥70%（对比当前人工抽检） |
| 延迟 | 单次检索 ≤300ms（本地，不含模型调用） |
| 兼容 | 与原 grounding 接口一致：`search(query,k)->[{id,path,line,title,snippet}]`；L5 可开关切换 |
| 环境 | **主环境不装重型依赖**；LlamaIndex 在隔离 venv 跑，BM25 兜底默认可用 |

## 3. 架构（v0 → v1）

```
tools/zhuge_rag.py
  ├─ build_index(paths, out) → work/p1/rag/index.json（可重建；不入库）
  ├─ search(query, k) → sources[{id,path,line,title,snippet,score}]
  ├─ backends:
  │   ├─ bm25      （默认，纯 Python，零依赖；v0）
  │   └─ llama_index（可选；隔离 venv；v1）
  └─ 与 grounding 对接：zhuge_grounding.retrieve 优先 ZHUGE_RAG=1 时调 rag.search
```

- **语料**：`docs/**/*.md`（主）；`deliverables/*.md`（次）；`.worktrees/tasks.json`（任务，可选）；工作记忆/判例摘要（后续）。
- **切块**：Markdown 标题切块，单块 ≤1200 字，保留 `path:section` 元数据；行号用于引用。
- **索引**：BM25（k1=1.5,b=0.75）；LlamaIndex 用 `VectorStoreIndex` + 本地 embedding（优先 Ollama `bge-m3`；不可用则 `HuggingFaceEmbedding` 小型模型或 BM25-only）。
- **引用**：沿用 P1-1 的 `[S#] path:line — snippet`；`ensure_citations` 不变。
- **新鲜度**：索引带 `built_at`/文件 mtime；docs 变更后重建；提供 `--check` 提示过期。

## 4. 分阶段

### M1 BM25 v0（1–2 天）
- `tools/zhuge_rag.py` + 索引构建/查询；`tools/test_zhuge_rag.py`（临时 docs，纯离线）；
- `ZHUGE_RAG=1` 开关接 grounding；保持旧关键词路径可回退；
- 指标：recall@3（人工标注 8 factual 相关文档）+ 延迟。

### M2 LlamaIndex 后端（2–3 天，隔离环境）
- 新建 Python 3.12+ venv（主环境不装）；
- `pip install llama-index-core llama-index-embeddings-ollama`（或 huggingface）；
- 实现 `LlamaIndexBackend.build/search`；与 BM25 同接口；
- 评测对比两次 recall/引用正确率；不达标则不切换。

### M3 切换与验收（1 天）
- L5 默认后端切到 RAG（env 可回退 BM25/关键词）；
- 跑 factual 8 + 注入/越权回归（复用 P0-4 promptfoo）；
- 更新能力卡/P1 文档；观察 1 周引用正确率与延迟。

## 5. 风险与边界

- **依赖污染**：LlamaIndex 装主环境曾导致 Phoenix/依赖冲突 → 一律隔离 venv；主环境只用 BM25 v0。
- **索引陈旧**：docs 每日变化；索引带时间戳，过期自动回退关键词并在 warnings 标注。
- **检索幻觉**：检索到不相关片段也会「有引用」；因此评测必须同时看 recall/引用正确性，不只看 citation 率。
- **prompt 长度**：OpenClaw CLI 单行限制；chunk 控制在 3 条 × 300 字内，超长截断。
- **零执行权**：RAG 只读 docs；不写、不执行。

## 6. 复跑/评测命令（计划）

```powershell
python tools/zhuge_rag.py --build
python tools/zhuge_rag.py --query "诸葛亮当前载体是什么？"
python tools/p1_citation_eval.py --binding ollama/qwen3.5:9b   # 引用率
# 另加 recall@3 标注集（8 factual × 相关文档）
```

## 7. 下一步
先做 **M1 BM25 v0**（不改 L5 默认行为，可回退），再决定是否值得上 LlamaIndex；若 BM25 已满足 recall/延迟，则 LlamaIndex 仅作可选后端，避免为框架而框架。
