# Ollama CLI

这是一个仅依赖 Python 标准库的 Ollama 多轮对话 CLI。默认连接 `http://127.0.0.1:11434`，历史保存在同目录 `history.json`。

```powershell
python chat.py
```

命令：`/model <名称>` 切换模型，`/clear` 清空历史，`/history` 查看条数，`/help` 帮助，`/exit` 退出。客户端 `ollama_client.py` 提供模型列表、流式/非流式聊天、向量嵌入与余弦相似度。
