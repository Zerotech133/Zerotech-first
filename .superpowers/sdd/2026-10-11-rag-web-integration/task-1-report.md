# K-STOCK-126 后端适配层实现报告

## 变更

- 新增 `AI/111111/rag_web_backend.py`，实现 `RagWebService` 与 `RagUnavailable`。
- 适配 `KnowledgeBase` 和 `OllamaClient` 的 embed/chat_stream 调用签名，并规范化向量列表返回值。
- 实现 `knowledge_docs` 支持文件的 `(relative path, st_mtime_ns, st_size)` 索引指纹缓存；目录不存在时抛出 `RagUnavailable`。
- 限制 `top_k` 为 1 到 8；知识目录存在但没有支持文档时抛出 `RagUnavailable`。
- 实现问题检索、上下文消息构建、来源路径归一化、流式消息/来源/done 事件及无命中回答。
- 新增 `AI/111111/test_rag_web_backend.py`，覆盖流式契约、缺失目录、无命中、缓存失效和参数校验。

## 测试

命令：

```powershell
python -m unittest AI.111111.test_rag_web_backend -v
```

结果：6 个测试全部通过（OK）。

命令：

```powershell
python -m unittest AI.111111.test_rag_web_backend AI.111111.test_knowledge_base -v
```

完整结果：11 个测试全部通过（OK）。

提交前 `git diff --check` 通过。

## 自审

- 默认模型为 `bge-m3` 和 `qwen3.5:9b`，未修改既有 `web_chat.py`、`web_ui.py`、`tools/check_ports.py` 或 portal。
- 流事件仅使用 `message`、`sources`、`done` 三种规定结构；无命中不会调用 chat 模型。
- 只读 `.txt`、`.md`、`.markdown` 文档，服务本身未启动监听端口。
- `top_k` 在构造服务和单次检索时都限制为 1 到 8；空知识目录明确报告为不可用。

## 遗留风险

- 真实 Ollama 服务不可用时，底层客户端异常会向调用方传播；本任务未要求增加重试或降级。
- 索引指纹依赖文件系统的 `st_mtime_ns` 与大小；若文件内容变化但两者均未变化，需等待下一次指纹变化才会重建。

## Fix Round 1

- 为 `_search()` 增加 `threading.RLock`，覆盖索引确保、`top_k` 临时切换和检索，避免并发请求互相污染。
- 新增线程池并发回归测试，验证 `top_k=1` 与 `top_k=8` 结果长度稳定。
- `python -m unittest AI.111111.test_rag_web_backend AI.111111.test_knowledge_base -v`：13 项通过。
- `python -m py_compile AI/111111/rag_web_backend.py AI/111111/test_rag_web_backend.py`：通过。
