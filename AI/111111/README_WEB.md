# AI 聊天网页

运行 `python AI/111111/web_chat.py`，服务监听 `127.0.0.1:8899`。页面可在“普通聊天”和“知识库问答”之间切换：普通聊天调用 `/api/chat`，知识库问答调用只读 `/api/rag`。

推荐先执行启动前自检：

```powershell
python -m ollama_service.launcher --check-only
```

检查通过后可直接启动 Web 服务；若 8899 已在线，启动器会复用现有服务，避免重复拉起：

```powershell
python -m ollama_service.launcher
```

`/api/rag` 接受 `{"message":"问题", "top_k":4}`，其中 `top_k` 为 1 到 8 的整数。响应为 NDJSON：先返回 `message` 片段，再返回 `sources`（来源相对路径和分数），最后返回 `done`。知识库只读取 `knowledge_docs/` 下的 `.txt`、`.md` 和 `.markdown` 文件，索引保存在内存中，文档目录不存在或没有支持文件时返回 503；Ollama 不可用时返回 502。页面不保存历史、不包含密钥。

RAG 回答默认关闭模型思考模式以降低首问延迟；普通聊天保持 Ollama 默认行为。
