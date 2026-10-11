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

`read_documents` 只读 `knowledge_docs` 下的 `.md`、`.markdown` 和 `.txt` 文件，不会修改原始文档。生产环境可传入实现 `embed(model, input)` 与 `chat(model, messages)` 的 fake/client 以便测试；未传 client 时使用本机 `http://127.0.0.1:11434` 的 Ollama HTTP API。训练、LoRA、模拟成交和交易门禁不由此模块开启。

## 只读基线与资格清单

`rag_baseline.py` 会为每份支持文档计算 SHA256，生成 `training-eligible-manifest-v0.1`，并运行标题检索命中率评测。没有许可证批准、权限快照、时间切分、`split_group`、样本哈希和 `offline_review_receipt`（且回执必须 `verified=true`、带签名与 proof 哈希）的记录一律标记为 `evaluation_only`。

```powershell
python -m AI.111111.rag_baseline `
  --docs-dir E:\老田的AI助手\knowledge_docs `
  --manifest-out work\training-eligible-manifest-v0.1.json `
  --report-out work\rag-baseline-report-v0.1.json
```

该命令只读取源文档，索引保存在内存中；输出中的 `training_eligible`、`lora_review_ready` 和 `financial_execution_enabled` 默认均为 `false`。

运行测试：

```powershell
python -m unittest AI/111111/test_knowledge_base.py AI/111111/test_rag_baseline.py
python -m py_compile AI/111111/knowledge_base.py AI/111111/rag_baseline.py
```
