# 零界 AI 助手 P0 稳定性收口实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复本地 AI 助手的 Windows 端口检查崩溃、8899 跨端口资源依赖、模型路由提示和 Ollama 参数不一致问题，并建立可重复的本地 smoke 验证。

**Architecture:** 保持现有 `AI/111111` 分层，不做大规模重构。`web_chat.py` 继续作为 8899 本地服务，但同源提供 `tokens.css` 与 `nav.js`；Ollama 客户端、RAG CLI 和健康检查统一读取模型路由和聊天参数。每项修复先写失败测试，再做最小修改，最后运行 AI 测试和三道评审门。

**Tech Stack:** Python 3.11 标准库、`unittest`、现有 `tools/check_ports.py`、本地 Ollama HTTP API、Windows PowerShell。

**Spec:** `docs/superpowers/specs/2026-10-13-ai-platform-stabilization-design.md`

## Global Constraints

- 人类统一入口仍为 8734；8899 仅为本地 Ollama 聊天服务。
- 不把智谱密钥写入源码、JSON、浏览器 localStorage、事件日志或错误消息。
- 普通聊天默认 `think=false`、`keep_alive=30m`、`qwen3.5:9b`；35B 只作为手动高级模型。
- RAG 默认使用本地 `bge-m3` 嵌入和 `qwen3.5:9b` 生成，不默认向云端发送知识库内容。
- 每个修改必须在独立 worktree、有任务号、提交信息包含工单号；禁止全量 `git add`。
- 不覆盖主工作区无关未提交文件，不修改交易、训练、LoRA 和真实执行开关。

## Review Focus

- GBK 控制台无法编码状态符号时，端口检查必须降级为 ASCII 且返回正确 rc；由 Task 1 的编码回归测试覆盖。
- 8899 页面在 8734 不可用时仍必须加载关键 CSS/JS；由 Task 2 的同源静态资源测试覆盖。
- 模型路由拒绝未知模型时，错误信息必须列出 4B、9B、35B；由 Task 3 的路由提示测试覆盖。
- RAG CLI 与 Web 必须发送相同的 `think=false` 和 `keep_alive=30m`；由 Task 3 的请求体测试覆盖。
- 健康检查默认模型必须与交互默认模型一致，不能悄悄把 35B 当作默认聊天模型；由 Task 3 的健康配置测试覆盖。

### Task 1: Windows 端口检查输出兼容

**Files:**
- Modify: `tools/check_ports.py:129-136`
- Test: `tools/test_check_ports_json.py`

**Interfaces:**
- Consumes: `sys.stdout.encoding`、端口在线布尔值。
- Produces: `_status_label(up, stream=None) -> str`；UTF-8 支持时返回现有中文状态符号，不支持时返回 ASCII `UP`/`DOWN`。

- [ ] **Step 1: Write the failing test**

在 `tools/test_check_ports_json.py` 增加：

```python
class GbkStream(io.StringIO):
    encoding = "gbk"

def test_gbk_console_falls_back_to_ascii_without_unicode_error():
    argv = ["check_ports.py"]
    with patch.object(sys, "argv", argv), \
         patch.object(check_ports, "tcp_up", return_value=True), \
         patch.object(check_ports, "listening_ports", return_value={}):
        output = GbkStream()
        with redirect_stdout(output):
            rc = check_ports.main()
    assert rc == 0
    assert "UP" in output.getvalue()
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run:

```powershell
C:\Program Files\Python311\python.exe -m pytest tools\test_check_ports_json.py -q
```

Expected: the new test fails because the current `print("✓在线")` path does not inspect the stream encoding.

- [ ] **Step 3: Implement the minimal encoding fallback**

在 `tools/check_ports.py` 添加：

```python
def _status_label(up, stream=None):
    stream = stream or sys.stdout
    label = "✓在线" if up else "✗离线"
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        label.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return "UP" if up else "DOWN"
    return label
```

把端口状态输出改为 `_status_label(up)`，保留 `--json` 输出结构和严格模式 rc 语义。

- [ ] **Step 4: Run focused and real console checks**

```powershell
C:\Program Files\Python311\python.exe -m pytest tools\test_check_ports_json.py -q
$env:PYTHONUTF8=""
C:\Program Files\Python311\python.exe tools\check_ports.py --strict --json
```

Expected: 测试通过；JSON 可解析；默认 GBK 控制台不再抛 `UnicodeEncodeError`。严格模式仍如实报告离线和未登记端口。

- [ ] **Step 5: Commit**

```powershell
git add tools/check_ports.py tools/test_check_ports_json.py
git commit -m "K-STOCK-228: 修复端口检查的 Windows 编码兼容"
```

### Task 2: 8899 同源静态资源与页面契约

**Files:**
- Modify: `AI/111111/web_ui.py` 中 tokens/nav 引用；`AI/111111/web_chat.py` 的 `ChatHandler.do_GET`
- Test: `AI/111111/test_web_ui.py`、`AI/111111/test_web_chat.py`

**Interfaces:**
- Consumes: `portal/static/tokens.css`、`portal/static/nav.js`。
- Produces: `GET /static/tokens.css` 和 `GET /static/nav.js`，返回 200、正确 `Content-Type` 和文件内容；页面引用改为 `/static/tokens.css`、`/static/nav.js`。

- [ ] **Step 1: Write the failing tests**

在 `test_web_ui.py` 增加：

```python
def test_page_uses_same_origin_static_assets():
    page = web_ui.render_chat_page(("qwen3.5:9b",))
    assert 'href="/static/tokens.css"' in page
    assert 'src="/static/nav.js"' in page
    assert "127.0.0.1:8734/static" not in page
```

在 `test_web_chat.py` 增加：

```python
def test_static_assets_are_served_from_8899():
    response, data = self.request("GET", "/static/tokens.css")
    self.assertEqual(response.status, 200)
    self.assertIn("text/css", response.getheader("Content-Type"))
    self.assertIn(b"--bg-page", data)
```

- [ ] **Step 2: Run the focused tests to verify they fail**

```powershell
C:\Program Files\Python311\python.exe -m pytest AI\111111\test_web_ui.py AI\111111\test_web_chat.py -q
```

Expected: 页面同源断言失败，`/static/tokens.css` 当前返回 404。

- [ ] **Step 3: Implement safe static asset serving**

在 `web_chat.py` 定义固定映射，不接受任意用户路径：

```python
STATIC_ASSETS = {
    "/static/tokens.css": (STATIC_ROOT / "tokens.css", "text/css; charset=utf-8"),
    "/static/nav.js": (STATIC_ROOT / "nav.js", "application/javascript; charset=utf-8"),
}
```

`do_GET` 先查该映射，文件不存在返回 404；存在时读取 UTF-8 内容并设置 `Content-Length`。页面只使用相对同源 URL。

- [ ] **Step 4: Run focused tests and an HTTP smoke**

```powershell
C:\Program Files\Python311\python.exe -m pytest AI\111111\test_web_ui.py AI\111111\test_web_chat.py -q
foreach($u in "http://127.0.0.1:8899/","http://127.0.0.1:8899/static/tokens.css","http://127.0.0.1:8899/static/nav.js") { Invoke-WebRequest -UseBasicParsing $u | Select-Object StatusCode,ContentType }
```

Expected: 页面、CSS、JS 均 200；关键资源不再依赖 8734 在线。

- [ ] **Step 5: Commit**

```powershell
git add AI/111111/web_chat.py AI/111111/web_ui.py AI/111111/test_web_chat.py AI/111111/test_web_ui.py
git commit -m "K-STOCK-228: 让本地聊天页面使用同源静态资源"
```

### Task 3: 统一 Ollama 参数与默认模型契约

**Files:**
- Modify: `AI/111111/ollama_client.py`、`AI/111111/knowledge_base.py`、`AI/111111/model_routing.py`（若当前分支缺少该文件，先从已审阅的 K-STOCK-227 变更恢复，不重写无关文件）、`ollama_service/health.py`
- Test: `AI/111111/tests/test_ollama_client.py`、`AI/111111/test_knowledge_base.py`、`AI/111111/test_model_routing.py`、`ollama_service/test_health.py`（缺失时创建最小测试文件）

**Interfaces:**
- Consumes: `DEFAULT_CHAT_MODEL`、`RAG_CHAT_MODEL`、`EMBED_MODEL`。
- Produces: `OllamaClient.chat_stream(..., think=False, keep_alive="30m")`、`OllamaClient.chat(..., think=False, keep_alive="30m")`；RAG CLI 使用相同请求字段；健康检查默认聊天模型为 `qwen3.5:9b`。

- [ ] **Step 1: Write failing contract tests**

补充断言：

```python
def test_client_sends_fast_chat_contract():
    list(self.client.chat_stream([], "demo"))
    self.assertFalse(Handler.last_chat_payload["think"])
    self.assertEqual(Handler.last_chat_payload["keep_alive"], "30m")
```

为 `KnowledgeBase` 的 `_OllamaClient` 增加可注入 opener 或请求捕获测试，断言 `/api/chat` JSON 同样含 `think: false` 和 `keep_alive: "30m"`。为模型路由错误增加：

```python
with self.assertRaisesRegex(ModelRoutingError, "qwen3.5:35b"):
    select_model("chat", "unknown-model")
```

为健康配置增加：

```python
self.assertEqual(HealthCheckConfig().chat_model, "qwen3.5:9b")
```

- [ ] **Step 2: Run focused tests to verify the contract fails**

```powershell
C:\Program Files\Python311\python.exe -m pytest AI\111111\tests\test_ollama_client.py AI\111111\test_knowledge_base.py ollama_service -q
```

Expected: 当前客户端没有 `keep_alive` 默认字段，RAG 旧客户端没有该字段，健康默认值仍为 35B。

- [ ] **Step 3: Implement the smallest shared contract**

将 Web 已采用的参数下沉到 `OllamaClient`；旧 `_OllamaClient.chat` 保持兼容签名但发送：

```python
{"model": model, "messages": messages, "stream": False,
 "think": False, "keep_alive": "30m"}
```

把路由拒绝消息改为动态列出 `CHAT_MODELS`，将健康默认模型导入 `model_routing.DEFAULT_CHAT_MODEL`，避免另写 35B 常量。

- [ ] **Step 4: Run focused tests and full AI regression**

```powershell
C:\Program Files\Python311\python.exe -m pytest AI\111111 ollama_service -q
C:\Program Files\Python311\python.exe -m compileall -q AI\111111 ollama_service
```

Expected: 现有测试与新增契约全部通过，且没有第三方依赖变化。

- [ ] **Step 5: Commit**

```powershell
git add AI/111111/ollama_client.py AI/111111/knowledge_base.py AI/111111/model_routing.py AI/111111/test_model_routing.py AI/111111/tests/test_ollama_client.py AI/111111/test_knowledge_base.py ollama_service/health.py ollama_service/test_health.py
git commit -m "K-STOCK-228: 统一本地模型参数与健康检查默认值"
```

### Task 4: P0 回归包与发布证据

**Files:**
- Create: `AI/111111/smoke_local.py`
- Modify: `AI/111111/README_WEB.md`
- Test: `AI/111111/test_smoke_local.py`

**Interfaces:**
- Consumes: 11434 `/api/tags`、8899 `/`、`/static/*`、`/api/chat`、`/api/rag`。
- Produces: 机器可读 JSON smoke 结果；模型、页面、静态资源和 API 的失败项带分类，不输出密钥或完整回答内容。

- [ ] **Step 1: Write the failing smoke contract test**

```python
def test_smoke_result_has_required_checks():
    result = run_smoke(base_url="http://127.0.0.1:11434", web_url="http://127.0.0.1:8899")
    assert {"ollama_tags", "web_page", "static_assets", "chat", "rag"} <= set(result["checks"])
```

- [ ] **Step 2: Run the focused test to verify it fails**

```powershell
C:\Program Files\Python311\python.exe -m pytest AI\111111\test_smoke_local.py -q
```

Expected: `smoke_local.py` 尚不存在，测试无法导入 `run_smoke`。

- [ ] **Step 3: Implement read-only smoke runner**

使用标准库 `urllib.request`，每项请求设置独立超时；只记录状态码、Content-Type、首 token 是否出现、RAG 是否有 `sources/done`，不记录回答正文。`main --json` 输出 JSON，任何必检项失败返回 1。

- [ ] **Step 4: Run real smoke and publish evidence**

```powershell
C:\Program Files\Python311\python.exe AI\111111\smoke_local.py --json > work\ai_smoke_kstock228.json
Get-Content work\ai_smoke_kstock228.json | ConvertFrom-Json | Select-Object -ExpandProperty checks
```

Expected: 当前 Ollama、8899 和 RAG 索引在线时全部通过；若模型冷启动超时，报告 `warming/timeout`，不伪造成功。

- [ ] **Step 5: Run all review gates**

```powershell
C:\Program Files\Python311\python.exe -m pytest AI\111111 ollama_service tools\test_check_ports_json.py -q
C:\Program Files\Python311\python.exe -m compileall -q AI\111111 ollama_service tools
C:\Program Files\Python311\python.exe tools\_mixin_integrity.py --check
C:\Program Files\Python311\python.exe tools\unify_nav.py --check
C:\Program Files\Python311\python.exe tools\check_ports.py --strict --json
git diff --check
```

- [ ] **Step 6: Commit documentation and evidence manifest**

```powershell
git add AI/111111/smoke_local.py AI/111111/test_smoke_local.py AI/111111/README_WEB.md work/ai_smoke_kstock228.json
git commit -m "K-STOCK-228: 增加本地 AI 发布 smoke 与运行说明"
```

## Execution Order

按 Task 1 → Task 2 → Task 3 → Task 4 顺序执行。Task 2 和 Task 3 都会影响 8899 运行实例，完成 Task 3 后统一重启 8899，再执行 Task 4 的真实 smoke。P1 智谱 Provider、P2 RAG 质量评测和 P3 平台整合另立计划，不在本 P0 计划中混入。
