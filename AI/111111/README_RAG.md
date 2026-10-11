# 轻量级 RAG 知识库（K-STOCK-119）

实现位于 `AI/111111/knowledge_base.py`，只使用 Python 标准库；Ollama 通过注入的 client 调用 `bge-m3` 向量模型和 `qwen3.5:9b` 对话模型。默认每 500 个字符切块，记录来源与块序号，检索按余弦相似度返回 Top-4，索引可 JSON 保存。

```python
import sys
sys.path.insert(0, "AI/111111")
from knowledge_base import KnowledgeBase

kb = KnowledgeBase()
docs = kb.read_documents("knowledge_docs")
kb.ingest(docs)
print(kb.answer("问题"))
kb.save("rag_index.json")
```

`read_documents` 只读 `knowledge_docs` 下的 `.md`、`.markdown` 和 `.txt` 文件，不会修改原始文档。生产环境可传入实现 `embed(model, input)` 与 `chat(model, messages)` 的 fake/client 以便测试；未传 client 时使用本机 `http://127.0.0.1:11434` 的 Ollama HTTP API。

运行测试：

```powershell
python -m unittest AI/111111/test_knowledge_base.py
python -m py_compile AI/111111/knowledge_base.py
```
