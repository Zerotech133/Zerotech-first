# RAG-Web 联动 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 8899 Web 聊天服务中增加基于 `knowledge_docs/` 的 RAG 问答模式，并以 NDJSON 返回回答和来源。

**Architecture:** 新增 `rag_web_backend.py` 作为 RAG-Web 适配层，负责索引缓存、检索、提示词和流式回答；新增 `web_ui.py` 负责页面 HTML，避免后端和页面任务同时编辑同一文件。最后由 `web_chat.py` 把两者接到 `/api/rag` 和首页，保留 `/api/chat` 原有行为。

**Tech Stack:** Python 3 标准库、`http.server`、现有 `KnowledgeBase`、现有 `OllamaClient`、`unittest`。

**Spec:** `docs/superpowers/specs/2026-10-11-rag-web-integration-design.md`

## Global Constraints

- 服务只能绑定 `127.0.0.1`，端口固定为 `8899`，并保留当前端口注册。
- 不引入第三方 Python 依赖；所有测试使用标准库 `unittest` 和 fake client。
- 普通聊天接口 `POST /api/chat` 的请求、响应和错误行为保持兼容。
- RAG 默认使用 `bge-m3` 嵌入模型、`qwen3.5:9b` 对话模型，默认 `top_k=4`，允许范围为 1 到 8。
- RAG 只读 `knowledge_docs/` 下的 `.txt`、`.md`、`.markdown` 文件，索引保存在内存中。
- 底层异常不直接返回客户端；上游不可用返回 502，知识目录不可用返回 503。

## Review Focus

- 空白问题、非整数 `top_k`、布尔值 `top_k` 和越界 `top_k` 必须返回 400；由 Task 3 的请求测试覆盖。
- 知识目录不存在或没有支持文档时必须返回 503；由 Task 1 的索引测试和 Task 3 的 HTTP 测试覆盖。
- 文档修改后缓存必须失效并重新索引；由 Task 1 的缓存测试覆盖。
- 没有命中时回答必须是“我不知道”，来源数组为空；由 Task 1 的无命中测试覆盖。
- 上游在 HTTP 头发送后中断时不能发送第二个响应；由 Task 3 的流中断回归测试覆盖。

### Task 1: RAG-Web 后端适配层

**Files:**
- Create: `AI/111111/rag_web_backend.py`
- Test: `AI/111111/test_rag_web_backend.py`

**Interfaces:**
- Consumes: `KnowledgeBase.read_documents()`, `KnowledgeBase.ingest()`, `KnowledgeBase.search()`；`OllamaClient.embed()` 和 `OllamaClient.chat_stream()`。
- Produces: `RagWebService(docs_dir="knowledge_docs", top_k=4, embed_model="bge-m3", chat_model="qwen3.5:9b", client=None)`；`ensure_index() -> int`；`search(question, top_k=None) -> list[dict]`；`build_messages(question, matches) -> list[dict]`；`stream(question, top_k=None) -> Iterator[dict]`。每个流字典只能是 `{"message":{"content": str}}`、`{"sources": list[dict]}` 或 `{"done": True}`。

- [ ] **Step 1: Write the failing tests for the service contract**

```python
def test_stream_emits_answer_sources_and_done(self):
    service = RagWebService(client=FakeRagClient(), docs_dir=self.docs)
    events = list(service.stream("太阳从哪里升起？"))
    self.assertEqual(events[-1], {"done": True})
    self.assertEqual(events[-2]["sources"][0]["source"], "facts.md")
    self.assertIn("东方", "".join(e["message"]["content"] for e in events if "message" in e))

def test_missing_directory_raises_unavailable(self):
    with self.assertRaises(RagUnavailable):
        RagWebService(docs_dir=self.temp / "missing", client=FakeRagClient()).ensure_index()
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m unittest AI.111111.test_rag_web_backend -v`

Expected: FAIL because `rag_web_backend.py` and its service contract do not exist.

- [ ] **Step 3: Implement the minimal service and client adapter**

Implement `RagWebService` with these rules:

```python
class RagUnavailable(RuntimeError):
    pass

class RagWebService:
    def ensure_index(self) -> int: pass
    def search(self, question: str, top_k: int | None = None) -> list[dict]: pass
    def build_messages(self, question: str, matches: list[dict]) -> list[dict]: pass
    def stream(self, question: str, top_k: int | None = None): pass
```

`ensure_index()` computes a tuple of `(relative path, st_mtime_ns, st_size)` for supported files. It rebuilds `KnowledgeBase` only when that tuple changes. `search()` validates `question.strip()` and `top_k`; `stream()` calls `OllamaClient.chat_stream()` through the adapter, yields message chunks, then a sources event containing only `source` and rounded `score`, and finally `{"done": True}`. With no matches it yields `{"message":{"content":"我不知道。"}}`, an empty sources event, and done without calling the chat model.

- [ ] **Step 4: Run focused tests and the existing RAG suite**

Run: `python -m unittest AI.111111.test_rag_web_backend AI.111111.test_knowledge_base -v`

Expected: all tests PASS, including cache invalidation, no-hit behavior, and fake streaming.

- [ ] **Step 5: Commit the backend task**

```powershell
git add -f AI/111111/rag_web_backend.py AI/111111/test_rag_web_backend.py
git commit -m "feat: add RAG web backend adapter K-STOCK-125"
```

### Task 2: 独立 Web 页面交互层

**Files:**
- Create: `AI/111111/web_ui.py`
- Test: `AI/111111/test_web_ui.py`

**Interfaces:**
- Consumes: `render_chat_page(models: Sequence[str]) -> str`.
- Produces: 页面包含 `mode` 选择框、普通聊天和知识库问答请求路径、来源渲染函数；不依赖 HTTP 服务或 RAG 后端。

- [ ] **Step 1: Write the failing rendering tests**

```python
def test_page_has_chat_and_rag_modes(self):
    page = render_chat_page(("qwen3.5:9b", "qwen3.5:4b"))
    self.assertIn('value="chat"', page)
    self.assertIn('value="rag"', page)
    self.assertIn('/api/rag', page)
    self.assertIn('sources', page)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m unittest AI.111111.test_web_ui -v`

Expected: FAIL because `web_ui.py` does not exist.

- [ ] **Step 3: Implement the page renderer**

Copy the current inline page behavior into `render_chat_page()` without changing `web_chat.py` yet. Keep the current model selector and “思考中” state. Add a mode selector; choose `/api/chat` for `chat` and `/api/rag` for `rag`. Parse `message`, `sources`, and `done` NDJSON records separately and render source names and scores below the answer. Escape model labels with `html.escape` before insertion. Task 3 will replace the old inline page with this renderer.

- [ ] **Step 4: Run focused tests and compile the UI module**

Run: `python -m unittest AI.111111.test_web_ui -v; python -m py_compile AI/111111/web_ui.py`

Expected: all rendering tests PASS and compilation exits 0.

- [ ] **Step 5: Commit the UI task**

```powershell
git add -f AI/111111/web_ui.py AI/111111/test_web_ui.py
git commit -m "feat: add RAG-aware web UI renderer K-STOCK-125"
```

### Task 3: HTTP 接线与回归

**Files:**
- Modify: `AI/111111/web_chat.py`
- Modify: `AI/111111/test_web_chat.py`
- Modify: `AI/111111/README_WEB.md`

**Interfaces:**
- Consumes: `RagWebService.stream()` from Task 1 and `render_chat_page()` from Task 2.
- Produces: `POST /api/rag` with the contract from the spec; `GET /` renders the new page; `POST /api/chat` remains compatible.

- [ ] **Step 1: Add failing HTTP tests**

```python
def test_rag_endpoint_returns_sources_and_done(self):
    with patch.object(web_chat, "RAG_SERVICE", FakeService()):
        response, data = self.request("POST", "/api/rag", {"message": "问题", "top_k": 2})
    self.assertEqual(response.status, 200)
    self.assertIn(b'"sources"', data)
    self.assertIn(b'"done": true', data)

def test_invalid_top_k_is_400(self):
    response, _ = self.request("POST", "/api/rag", {"message": "问题", "top_k": True})
    self.assertEqual(response.status, 400)
```

- [ ] **Step 2: Run Web tests and verify RED**

Run: `python -m unittest AI.111111.test_web_chat -v`

Expected: the new `/api/rag` tests FAIL while existing ordinary chat tests remain green.

- [ ] **Step 3: Add the route without changing ordinary chat**

Add `_rag_payload()` validation, a module-level lazy `RAG_SERVICE`, and a `do_POST()` branch for `/api/rag`. Send `application/x-ndjson`, flush every event, and use 400/502/503 for validation, Ollama, and document errors. Track `response_started` so an upstream interruption after headers only ends the stream. Change `do_GET()` to call `render_chat_page()`.

- [ ] **Step 4: Run the complete validation set**

Run:

```powershell
python -m unittest discover -s AI/111111/tests -v
python -m unittest discover -s AI/111111 -p 'test_*.py' -v
python -m py_compile AI/111111/*.py AI/111111/tests/*.py
```

Expected: all tests PASS and compilation exits 0.

- [ ] **Step 5: Run a real local Ollama smoke test**

Start the service on `127.0.0.1:8899`, POST `{"model":"qwen3.5:4b","message":"只回复 OK"}` to `/api/chat`, then POST `{"message":"问题","top_k":2}` to `/api/rag`. Confirm both return status 200, RAG emits `sources` and `done`, and no source document is modified.

- [ ] **Step 6: Update documentation and commit**

Document `/api/rag`, mode selection, source records, and the startup command in `AI/111111/README_WEB.md`, then run:

```powershell
git add -f AI/111111/web_chat.py AI/111111/test_web_chat.py AI/111111/README_WEB.md
git commit -m "feat: connect RAG mode to Ollama web chat K-STOCK-125"
```

### Integration Review

- [ ] Confirm Tasks 1 and 2 touched disjoint production files and their tests pass independently.
- [ ] Merge both task commits into the K-STOCK-125 integration branch before Task 3.
- [ ] Run all three test commands and the real smoke test from Task 3.
- [ ] Run `python tools/_mixin_integrity.py --check`, `python tools/unify_nav.py --check`, and `python tools/check_ports.py` with UTF-8 output before merge.
