# K-STOCK-125 Task 1 报告

## 变更

- 新增 `AI/111111/rag_web_backend.py`，提供 `RagWebService` 与 `RagUnavailable`。
- 新增 `AI/111111/test_rag_web_backend.py`，覆盖索引缓存、缓存失效、目录错误、参数校验、提示词、无命中短路和流式事件。
- 适配现有 `KnowledgeBase` 与 `OllamaClient`，只读取 `.txt`、`.md`、`.markdown`，来源输出转换为知识目录相对路径。

## TDD 过程

先运行 `python -m unittest AI.111111.test_rag_web_backend -v`，测试因 `rag_web_backend` 尚不存在而导入失败；随后实现适配层并修正 fake/client 两种 `embed` 签名兼容。

## 验证

- `python -m unittest AI.111111.test_rag_web_backend AI.111111.test_knowledge_base -v`：12 项通过。
- `python -m py_compile AI/111111/rag_web_backend.py AI/111111/test_rag_web_backend.py`：通过。

## 风险

- 上游 Ollama 的网络或协议错误统一包装为 `RagUnavailable`，由 HTTP 层决定状态码和对外文案。
- 索引仅保存在进程内，依赖文档相对路径、`st_mtime_ns`、`st_size` 变化触发重建。

## 提交

`e49daa6`（amend 后以最终哈希为准）。

