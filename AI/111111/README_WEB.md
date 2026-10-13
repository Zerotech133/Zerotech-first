# AI 聊天网页

运行 `python AI/111111/web_chat.py`，服务监听 `127.0.0.1:8899`。页面可在“普通聊天”和“知识库问答”之间切换：普通聊天调用 `/api/chat`，知识库问答调用只读 `/api/rag`。

页面角色下拉框来自 `chat_profiles.json`。普通聊天会按共享的字符预算保留最近上下文，再连同当前角色的 system prompt 一起发送给 Ollama，并将角色写入浏览器本地历史；服务端拒绝伪造 `system` 消息、空消息和超长历史。普通聊天默认关闭“深度思考”，打开开关后会发送 `think=true`；RAG 始终关闭思考模式。会话首次发送后记录当前模型，切换模型会在页面提示冷启动。RAG 请求也携带角色，响应的 `sources` 事件会附带 `profile` 与实际 `model`，便于审计。

推荐先执行启动前自检：

```powershell
python -m ollama_service.launcher --check-only
```

检查通过后可直接启动 Web 服务；若 8899 已在线，启动器会复用现有服务，避免重复拉起：

```powershell
python -m ollama_service.launcher
```

`/api/chat` 接受 `{"message":"问题", "model":"qwen3.5:9b", "profile":"general", "history":[]}`，`history` 最多 40 条。服务端固定发送 `think=false` 和 `keep_alive=30m`，普通聊天默认模型为 `qwen3.5:9b`；35B 只在模型选择器中手动使用。`/api/rag` 接受 `{"message":"问题", "top_k":4, "profile":"research"}`，其中 `top_k` 为 1 到 8 的整数。响应为 NDJSON：先返回 `message` 片段，再返回 `sources`（来源相对路径、分数、索引版本，以及角色和模型），最后返回 `done`。知识库只读取 `knowledge_docs/` 下的 `.txt`、`.md` 和 `.markdown` 文件，索引保存在内存中，文档目录不存在或没有支持文件时返回 503；Ollama 不可用时返回 502。页面不包含密钥，历史仅保存在浏览器本地。

RAG 和普通聊天都默认关闭模型思考模式以降低首问延迟；深度思考开关将在交互层单独接入。

发布前可运行只读 smoke 检查，结果不包含回答正文或密钥：

```powershell
python AI/111111/smoke_local.py --json > work/ai_smoke_kstock228.json
```
