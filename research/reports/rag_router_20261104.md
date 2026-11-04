# RAG 三级降级 + 调用成本监控（W23，20261104）

> 任务：task-183｜py312｜只写 4 文件｜**不 commit**｜测试全 mock、离线不联网、不打印密钥。

## 一、降级链路

`tools/kb_corpus/llm_router.py::generate()` 按顺序调用，前级失败自动降级：

| 级 | Provider | 模型（env 可覆盖） | 端点 | 超时（env） |
|---|---|---|---|---|
| T1 | Bailian v4d（DashScope OpenAI 兼容） | `RAG_MODEL_V4D`（默认 `qwen2.5-14b-instruct-a57a3521e4e0`） | `{dashscope_base_url}/chat/completions` | `RAG_TIER_TIMEOUT=60s` |
| T2 | Bailian v2 | `RAG_MODEL_V2`（默认 `qwen2.5-14b-instruct-e383f32d42f2`） | 同上 | 60s |
| T3 | 本地 Ollama | `RAG_OLLAMA_MODEL`（默认 `qwen3.5:9b`） | `{OLLAMA_HOST}/api/chat`（默认 `127.0.0.1:11434`） | `RAG_OLLAMA_TIMEOUT=120s` |

**降级触发**：超时 / HTTP 429 / HTTP 5xx / 网络错误（URLError、ConnectionError、OSError）/ 响应 JSON 或空内容 / 其余 4xx（可用性优先）。
每级记录：`tier / provider / model / status / elapsed_ms / error(脱敏)`；全部失败返回 `ok=False, error="all_tiers_failed"` + 三级 attempts（调用方可兜底话术）。

## 二、成本监控

- 每次**成功**调用追加一行到 `research/reports/llm_cost_20261104.jsonl`（`LLM_COST_LOG` 可覆盖）：
  `{ts, provider, model, tier, prompt_tokens, completion_tokens, total_tokens, cost_cny, purpose}`；
- 估算价（env 可覆盖）：`LLM_PRICE_IN_PER_1K=0.002` 元/1k prompt tokens、`LLM_PRICE_OUT_PER_1K=0.006` 元/1k completion tokens；Ollama 成本 0；
- 日预算：`LLM_DAILY_BUDGET_CNY=20`（默认）；日累计（按 ts 日期求和）**≥预算 → `budget_warning=True`**，并返回 `daily_cost_cny/budget_cny` 供上层告警；
- CLI：`python tools/kb_corpus/llm_router.py cost` 查看当日累计/预算/日志路径/条数。

## 三、rag_lite 集成

- `rag_lite.ask(question, use_router=None)`：`retrieve()` → **strict prompt 默认**（保留 `RAG_PROMPT_PROFILE`；`=base` 回滚旧 prompt）→ `llm_router.generate(purpose="rag_lite")`；
- 返回结构新增 `router` 元数据：`ok/tier/provider/model/cost_cny/daily_cost_cny/budget_cny/budget_warning/attempts`；
- **回滚开关**：`RAG_ROUTER_DISABLE=1` → 退回旧本地 `chat()`（provider=`ollama_legacy`），其余行为不变；
- T3 即本地 Ollama，与旧实现同模型，故断网时仍可答。

## 四、密钥纪律

- 仅读 `C:/项目/_vault/secrets/dashscope_api_key.txt` 与 `dashscope_base_url.txt`（只读、不写、不在日志/返回值/报告出现）；
- 支持 `DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL` 环境变量覆盖；
- 异常统一 `redact()`：`sk-****` 脱敏（测试覆盖）。

## 五、测试（tools/tests/test_llm_router.py，全 mock 离线）

**8 passed**：

| # | 用例 | 断言 |
|---|---|---|
| 1 | T1 正常 + 成本落账 | tier=1/provider/cost>0/日志 1 行/tokens=150 |
| 2 | T1 超时 → T2 | tier=2，attempts[0] TimeoutError |
| 3 | 429+500 → T3 | 状态序列 [429,500,200]，tier=3，cost=0 |
| 4 | 三级全失败 | ok=False、all_tiers_failed、3 attempts |
| 5 | 预算告警 | daily_cost>budget → budget_warning=True |
| 6 | 密钥脱敏 | 错误信息含 `sk-****`，不含明文密钥 |
| 7 | rag_lite ask 集成 + 回滚 | strict prompt 生效、router 元数据、`RAG_ROUTER_DISABLE=1` 走旧 chat |
| 8 | env 模型覆盖 | `RAG_MODEL_V4D=custom-model-x` 生效 |

回归：`tools/tests/test_rag_answer_gen.py` **6 passed**（rag_lite 既有行为无回归）。

## 六、命令

```powershell
# 查看降级链/当日成本（离线可用）
C:\项目\py312\python.exe -X utf8 tools/kb_corpus/llm_router.py tiers
C:\项目\py312\python.exe -X utf8 tools/kb_corpus/llm_router.py cost
# 直接调用（需真实网络/密钥；本任务未做联网冒烟）
C:\项目\py312\python.exe -X utf8 tools/kb_corpus/llm_router.py ask --prompt "测试问题"
# rag_lite 问答（默认走 router；回滚旧链路/旧 prompt）
$env:RAG_ROUTER_DISABLE='1'; C:\项目\py312\python.exe -X utf8 tools/kb_corpus/rag_lite.py ask "问题"
$env:RAG_PROMPT_PROFILE='base'; C:\项目\py312\python.exe -X utf8 tools/kb_corpus/rag_lite.py ask "问题"
# 测试
C:\项目\py312\python.exe -X utf8 -m pytest -q tools/tests/test_llm_router.py
```

## 七、验收 / 风险

- [x] 三级降级链路（T1→T2→T3）+ 每级耗时/结果记录
- [x] 成本 JSONL 落账 + 日预算告警
- [x] rag_lite ask 接入 router，strict prompt 默认，`RAG_PROMPT_PROFILE=base` 回滚
- [x] 测试 8 passed（全 mock，离线）
- [x] 密钥仅读不打印、错误脱敏
- 风险：本任务按约束**未做联网冒烟**（Bailian 真实 200 响应字段需上线前用真实 key 验证一次）；建议上线前另立 L2 冒烟任务（1 次调用）核对 usage 字段与计费口径；预算价目为默认估值，需财务口径确认后调 `LLM_PRICE_*`。
