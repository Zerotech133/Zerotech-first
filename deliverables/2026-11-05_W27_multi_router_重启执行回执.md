# 执行回执：W27 multi 分支 router 修复生产生效（L2，2026-11-05）

## 结论
✅ 完成。用户 2026-11-05 00:23 批准后，8091 受控重启（L2），
`/api/kb/qa` 的 **`mode=multi` 与 `mode=single` 生成端均已走 llm_router**（百炼 v4d）；
`llm_cost_20261105.jsonl` 出现 `provider=bailian_v4d`、`purpose=kb_qa` 成本行。
检索、prompt、引用校验、质量守卫逻辑未变。

## 背景（审计发现的验收漏洞）
- W27 首次上线（2026-11-04 20:36）只验证了 `mode=single`。
- 2026-11-05 代码审计发现 `portal/api_kb_qa.py` 的 multi 分支：
  `fn = multi_fn or multi.answer_multi`，**未传 `chat_fn`**；
  `tools/kb_corpus/kb_qa_multi.py` 的 `answer_multi` 默认 `call_ollama`
  → 生产 `mode=multi`（生成默认）实际仍走本地 Ollama，不产生 v4d 成本。
- 修复（commit **701c023fd**）：multi 真实链路传 `chat_fn=qa.chat`；
  `test_api_kb_qa.py` 增加断言真实调用签名，25 passed。

## 变更内容
- 代码：commit `701c023fd`（`portal/api_kb_qa.py`、`tools/tests/test_api_kb_qa.py`）。
- 启动脚本：`portal/start_portal_api.ps1` 保持
  `$env:KB_QA_LLM_PROVIDER = 'router'`（2026-11-04 已加，本次无需改）。
- 本次仅重启进程使修复代码加载：flag + `schtasks /run /tn "AI工作站门户API"`。

## 执行时间线
| 时间（2026-11-05） | 事件 |
|---|---|
| 00:23 | 用户批准「执行 L2 重启让 W27 multi 修复在生产生效」 |
| 00:24:38 | 脚本消费 flag：`restart-flag: killed [12868,15772,37168]` |
| 00:24:54 | `cold-start guard OK: portal_api import smoke passed` |
| 00:24:56 | `starting`（新进程启动） |
| 00:25:01 | 计划任务 Last Run；状态 Running（任务宿主运行 uvicorn） |
| 00:26:03 | `/api/health` 200，portal api pid **52780**（旧 15772 已退出）；停机约 **85s** |

日志：`C:\Temp\portal_api_task.log`。
注：`C:\Temp\portal_api_restart.log` 为 2026-10-10 旧日志（含 pandas warmup 失败），与本轮无关。

## 验收证据
重启前基线：
- `GET /api/kb/qa?...&mode=multi&cache=0&guard=0` → 200、16.1s、llm_called=True；
- `research/reports/llm_cost_20261105.jsonl` **不存在** → 确认 multi 未走 v4d。

重启后：
```
/api/health → 200，services.portal api ok=true pid=52780 ms=4

GET /api/kb/qa?q=M9覆铜板对PCB价值量影响&topk=6&mode=multi&cache=0&guard=0
→ 200，elapsed 10.3s，answer 174 字，citations 2，
  meta: mode=multi llm_called=true clusters=3 citation_retry.used=false

GET /api/kb/qa?q=布林线中轨的突破信号是否有效&topk=6&mode=single&cache=0&guard=1
→ 200，7.3s；引用守卫回退（citation_missing），但生成段已调用 v4d
```
成本流水（`research/reports/llm_cost_20261105.jsonl`）：
```json
{"ts":"2026-11-05T00:26:10+08:00","provider":"bailian_v4d","model":"qwen2.5-14b-instruct-a57a3521e4e0","tier":1,"prompt_tokens":1061,"completion_tokens":140,"total_tokens":1201,"cost_cny":0.002962,"purpose":"kb_qa"}
{"ts":"2026-11-05T00:26:32+08:00", ... "total_tokens":1168,"cost_cny":0.002764}
{"ts":"2026-11-05T00:26:34+08:00", ... "total_tokens":1202,"cost_cny":0.002576}
{"ts":"2026-11-05T00:26:52+08:00", ... "total_tokens":1167,"cost_cny":0.002794}
{"ts":"2026-11-05T00:26:55+08:00", ... "total_tokens":1175,"cost_cny":0.002450}
```
全部 `provider=bailian_v4d`、`purpose=kb_qa`，证明 multi/single 均走 router。

## 风险与回滚
- 成本：约 ¥0.0025–0.003/问；日预算 `LLM_DAILY_BUDGET_CNY=20`，超限仅告警。
- 降级：云端 429/5xx/超时 → v2 → 本地 ollama；三层全败 → 规则摘录（不编造）。
- 质量守卫回退仍可能发生（引用不足时摘录知识单元），与模型切换前逻辑一致。
- 回滚（约 1–2 分钟，含停机）：
  1) 删除/注释 `portal/start_portal_api.ps1` 中 `$env:KB_QA_LLM_PROVIDER = 'router'`；
  2) `New-Item C:\Temp\PORTAL_API_RESTART.flag` + `schtasks /run /tn "AI工作站门户API"`；
  3) 验证成本流水不再新增、答案恢复本地 9b 口径。

## 观察项（24h）
- 成本流水每日总额 ≤ ¥20；multi/single 的 provider 是否始终 v4d；
- `/api/kb/qa` 延迟 p95（v4d 生成段约 5–7s；multi 总时延 10–16s）；
- 质量守卫回退比例、`all_tiers_failed` 是否出现。
