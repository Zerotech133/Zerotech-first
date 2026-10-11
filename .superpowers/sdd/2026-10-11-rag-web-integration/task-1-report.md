# K-STOCK-126 Task 1 后端适配层实现报告

## 变更

`9701c83` 新增 `AI/111111/rag_web_backend.py`，提供 `RagWebService` 和 `RagUnavailable`：

- 适配 `KnowledgeBase` 与 `OllamaClient` 的 `embed`、`chat_stream` 调用签名，并规范化 embedding 返回值。
- 根据支持文档的相对路径、`st_mtime_ns` 和文件大小建立索引指纹；知识目录缺失或没有 `.txt`、`.md`、`.markdown` 文档时抛出 `RagUnavailable`。
- 校验 `top_k` 为 1 到 8 的整数，提供检索、上下文消息构建、来源路径归一化、流式 `message`/`sources`/`done` 事件和无命中回答。
- 新增 5 个初始后端契约测试，覆盖流式输出、目录不可用、无命中和参数校验等行为。

`c540512` 为修复轮：

- 用 `threading.RLock` 串行保护索引建立及检索期间的临时 `top_k` 切换，避免并发检索互相覆盖。
- 增加索引复用与文档变更后的重建断言，并增加并发检索分别返回 1 条和 2 条结果的回归测试；后端测试总数由 5 个增至 7 个。

## 测试

测试文件中的方法数量为：`AI/111111/test_rag_web_backend.py` 7 个，`AI/111111/test_knowledge_base.py` 5 个，合计 12 个。

执行命令：

```powershell
python -m unittest AI.111111.test_rag_web_backend AI.111111.test_knowledge_base -v
```

实际输出：

```text
test_concurrent_search_respects_each_top_k (AI.111111.test_rag_web_backend.RagWebServiceTests.test_concurrent_search_respects_each_top_k) ... ok
test_empty_directory_raises_unavailable (AI.111111.test_rag_web_backend.RagWebServiceTests.test_empty_directory_raises_unavailable) ... ok
test_index_is_rebuilt_when_supported_document_changes (AI.111111.test_rag_web_backend.RagWebServiceTests.test_index_is_rebuilt_when_supported_document_changes) ... ok
test_missing_directory_raises_unavailable (AI.111111.test_rag_web_backend.RagWebServiceTests.test_missing_directory_raises_unavailable) ... ok
test_no_matches_emits_unknown_without_chat_call (AI.111111.test_rag_web_backend.RagWebServiceTests.test_no_matches_emits_unknown_without_chat_call) ... ok
test_stream_emits_answer_sources_and_done (AI.111111.test_rag_web_backend.RagWebServiceTests.test_stream_emits_answer_sources_and_done) ... ok
test_validation_rejects_blank_question_and_invalid_top_k (AI.111111.test_rag_web_backend.RagWebServiceTests.test_validation_rejects_blank_question_and_invalid_top_k) ... ok
test_answer_prompt_contains_only_context_and_admits_unknown (AI.111111.test_knowledge_base.KnowledgeBaseTests.test_answer_prompt_contains_only_context_and_admits_unknown) ... ok
test_empty_and_no_hit_are_safe (AI.111111.test_knowledge_base.KnowledgeBaseTests.test_empty_and_no_hit_are_safe) ... ok
test_ingest_chunks_and_preserves_source_and_sequence (AI.111111.test_knowledge_base.KnowledgeBaseTests.test_ingest_chunks_and_preserves_source_and_sequence) ... ok
test_json_round_trip_and_read_documents (AI.111111.test_knowledge_base.KnowledgeBaseTests.test_json_round_trip_and_read_documents) ... ok
test_search_returns_top_k_by_cosine_similarity (AI.111111.test_knowledge_base.KnowledgeBaseTests.test_search_returns_top_k_by_cosine_similarity) ... ok

----------------------------------------------------------------------
Ran 12 tests in 0.094s

OK
```

## 自审

- 默认模型为 `bge-m3` 和 `qwen3.5:9b`；本任务未修改 `web_chat.py`、`web_ui.py`、`tools/check_ports.py` 或 portal。
- 流事件使用 `message`、`sources`、`done` 三种结构；无命中时不调用 chat 模型。
- 服务只读取 `.txt`、`.md`、`.markdown` 文档，不启动监听端口。
- 索引指纹依赖文件路径、`st_mtime_ns` 和大小；内容变化但三项指纹均不变时不会触发重建。

## 遗留风险

- 真实 Ollama 服务不可用时，底层客户端异常会向调用方传播；本任务未增加重试或降级。

## 提交记录

- `9701c83`：新增 RAG-Web 后端适配层及 5 个初始契约测试。
- `c540512`：增加 `RLock` 并发保护、索引重建断言及并发回归测试（fix round）。
