# K-STOCK-125 Task 1 后端适配层实现报告

## 变更

- 新增 `AI/111111/rag_web_backend.py`，提供 `RagWebService` 与 `RagUnavailable`。
- 适配包内导入和直接脚本导入，读取 `knowledge_docs/` 下的 `.txt`、`.md`、`.markdown`。
- 使用 `(relative path, st_mtime_ns, st_size)` 做内存索引缓存，文档变化后重建。
- 校验问题和 `top_k`（1 到 8 的整数，拒绝布尔值），输出消息、来源和 done 事件；无命中时返回“我不知道。”且不调用 chat。
- 用 `RLock` 保护共享索引和请求级 `top_k` 临时切换，避免线程化 HTTP 请求互相污染。
- `RagUnavailable.status_code` 区分知识库不可用（503）和 Ollama 不可用（502）。

## TDD 与验证

- RED：模块不存在时，`python -m unittest AI.111111.test_rag_web_backend -v` 按预期导入失败。
- GREEN：`python -m unittest AI.111111.test_rag_web_backend AI.111111.test_knowledge_base -v`：12 项通过；并发修复后包含 13 项后端+知识库回归通过。
- `python -m py_compile AI/111111/rag_web_backend.py AI/111111/test_rag_web_backend.py`：通过。
- `git diff --check`：通过。

## 审查

任务级复核确认 spec compliance；并发 `top_k` 竞态经 Fix Round 1 加锁和线程池回归测试后通过，无 Critical/Important 遗留。

## 提交

- `2ef85f9`：RAG-Web 后端和基础测试。
- `5158ab0`：并发锁和回归测试。
- `5776434`：任务报告校正。
