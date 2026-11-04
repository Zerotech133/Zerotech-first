# 执行回执：W27 生产 kb_qa 接入 llm_router（L2，2026-11-04）

## 结论
✅ 已完成：生产 `GET /api/kb/qa` 生成端已从本地 Ollama 切到 **llm_router 三级降级**
（百炼 v4d → 百炼 v2 → 本地 ollama）；检索/prompt/引用校验/质量守卫不变。
用户 20:36 批准，受控重启 8091 完成；新进程 **pid 15772**，`/api/health` 200；
真实问答命中 **bailian_v4d**（成本流水 20:37:27）；失败自动降级、最终规则摘录兜底。

## 变更内容
`portal/start_portal_api.ps1`（`$env:ZHUGE_RAG='1'` 之后新增 4 行）：
```powershell
# W27 (2026-11-04): 生产 kb_qa 生成端走 llm_router（v4d→v2→ollama）；
# 回滚：删除本行并重启，或改为 'ollama'。L2 审批见 docs/ops/2026-11-04_W27_kb_qa_router_L2审批单.md
$env:KB_QA_LLM_PROVIDER = 'router'
```
代码：`tools/kb_corpus/kb_qa.py`（commit 5919ec782）
- `KB_QA_LLM_PROVIDER=ollama|router`（默认 ollama，旧口径不变）；
- `call_router()` 调 `llm_router.generate(purpose="kb_qa")`，三层全败抛 `KbQaError`，
  由 `ask()` 走规则摘录降级（fallback=true）；
- `_ask_single` / `_ask_multi` 统一走 `chat()` 分派；payload `llm.endpoint` 标注链路；
- CLI 新增 `--llm-provider`；7 个新测试。

## 执行时间线
| 时间 | 事件 |
|---|---|
| 20:36 | 用户批准「现在重启并切 router」 |
| 20:36:14 | 创建 `C:\Temp\PORTAL_API_RESTART.flag`；`schtasks /run /tn "AI工作站门户API"` |
| 20:36:18 | 脚本消费 flag：`restart-flag: killed [32600,36832,37160,39268]` |
| 20:36:36 | `cold-start guard OK: portal_api import smoke passed` |
| 20:36:37 | `starting`（新进程启动） |
| 20:37:07 | `/api/health` 200，portal api pid **15772**；停机约 **49s**（含导入冒烟 18s） |
| 20:37:27 | 首问命中 v4d：`tokens=1438 cost=0.003852 purpose=kb_qa` |

## 验收证据
```
GET /api/kb/qa?q=...&topk=3&mode=single  → HTTP 200
data.answer: 2 条可核验引用（[41692ee59293]/[28e0346286a2]），confidence=high
成本流水: {"ts":"2026-11-04T20:37:27+08:00","provider":"bailian_v4d",
          "model":"qwen2.5-14b-instruct-a57a3521e4e0","total_tokens":1438,
          "cost_cny":0.003852,"purpose":"kb_qa"}
/api/health: ok=true，portal api pid=15772，ms=9
```
- 两次实测时延 **16s / 5s**（含检索；v4d 生成段约 5s，较本地 9b 明显下降）。
- API 响应做了字段裁剪（`data` 不含 `llm`/`fallback`），故以成本流水 provider 与答案
  引用质量作为「实际走 v4d」的判据；服务端日志 `portal_api_err.log` 无异常。

## 风险与回滚
- 成本：约 ¥0.0026–0.004/问；日预算 `LLM_DAILY_BUDGET_CNY=20`，超限仅告警。
- 降级：云端 429/5xx/超时 → v2 → ollama；三层全败 → 规则摘录（不编造）。
- 回滚（约 1 分钟，含重启）：
  1) 删除/注释 `portal/start_portal_api.ps1` 中 `$env:KB_QA_LLM_PROVIDER = 'router'`；
  2) `New-Item C:\Temp\PORTAL_API_RESTART.flag` + `schtasks /run /tn "AI工作站门户API"`；
  3) 验证成本流水不再新增、答案恢复本地 9b 口径。
- 本次重启停机 49s，略长于上次 20s：因 `cold-start guard` 导入冒烟耗时 18s，属正常。

## 观察项（24h）
- `/api/kb/qa` 的 fallback 比例（API 层看不到，需查 `kb_qa` 内层日志/后续加字段）；
- 成本流水每日总额 ≤ ¥20；延迟 p95；未出现 `all_tiers_failed`。
