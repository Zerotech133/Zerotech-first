# L2 审批单：W27 生产 kb_qa 接入 llm_router（2026-11-04）

## 0. 变更摘要
把生产知识库问答 `GET /api/kb/qa` 的**生成端**从「本地 Ollama qwen3.5:9b」
切到 `llm_router` 三级降级：**百炼 v4d → 百炼 v2 → 本地 ollama**。
检索、prompt、引用/拒答/质量守卫逻辑**全部不变**；仅换生成模型与故障降级链。
代码已就绪并以环境变量开关控制（默认仍 `ollama`，不改变现状）：
`KB_QA_LLM_PROVIDER=router`。

## 1. 命令全文（获准后执行）
```powershell
# ① 启动脚本新增一行（portal/start_portal_api.ps1，$env:ZHUGE_RAG='1' 之后）
$env:KB_QA_LLM_PROVIDER = 'router'
# （DASHSCOPE_API_KEY 走 C:/项目/_vault/secrets/dashscope_api_key.txt，只读，不回显）

# ② 重启 8091（现有受控流程）
New-Item -ItemType File -Path 'C:\Temp\PORTAL_API_RESTART.flag' -Force | Out-Null
schtasks /run /tn "AI工作站门户API"      # 任务脚本自读 flag，冷启动保护后替换进程

# ③ 验收
Invoke-RestMethod 'http://127.0.0.1:8091/api/health' | ConvertTo-Json -Compress
Invoke-RestMethod 'http://127.0.0.1:8091/api/kb/qa?q=M9%20%E8%A6%86%E9%93%9C%E6%9D%BF%E5%AF%B9%20PCB%20%E4%BB%B7%E5%80%BC%E9%87%8F&topk=3&mode=single' |
  ConvertTo-Json -Depth 4        # 期望 .llm.endpoint 以 router( 开头、answer 非空
Get-Content 'C:\项目\老田的AI助手\research\reports\llm_cost_*.jsonl' -Tail 1
```

## 2. 影响面
- **功能**：`/api/kb/qa`、`/api/kb/qa/` 及门户「知识库问答」入口的答案生成换模型；
  检索命中、引用 ID 校验（`[unit_id]`）、拒答文案、多单元聚合/守卫均保持原样。
- **时延**：v4d 实测单问 **5.5s**（本地 ollama 约 10–40s，取决于机器负载）；
  云端超时 60s/层，最坏情况会依次降级，最终回本地 ollama（总时延可能 >60s）。
- **成本**：实测 1 问 = 1,009 tokens ≈ **¥0.00258**；日预算 `LLM_DAILY_BUDGET_CNY=20`，
  超预算只告警不硬停；成本流水 `research/reports/llm_cost_*.jsonl`（含 purpose=kb_qa）。
- **可用性**：云端 429/5xx/超时 → 自动降级 v2 → ollama；三层全失败 → kb_qa 规则摘录
  降级（fallback=true，不编造），与旧行为一致。
- **密钥**：仅服务进程读取 `C:/项目/_vault/secrets/dashscope_api_key.txt`，日志脱敏 `sk-****`。

## 3. 不可逆点
1. 重启瞬间 `/api/*` **约 20s 不可用**（门户与自动任务短时失败重试可恢复）。
2. 切换后新产生的问答会调用百炼并**产生费用**（按量，约 ¥0.0026/问）；
   已产生的成本流水不可撤回。
3. 生成内容由 v4d 产出，可能与此前本地 9b 的措辞/引用习惯不同；结果已带
   `llm.endpoint` 字段可区分，答案不落库、无数据迁移。

## 4. 替代方案
- **A（不重启，仅研究）**：保持现状，W27 代码+开关留在仓库，等下一次计划内重启；
  缺点：生产仍用本地 9b，v4d 引用优势不上线。
- **B（旁路灰度，不接生产）**：仅 `rag_lite`（视频知识库）已接 router；生产 kb_qa 不动。
- **C（只切 v2 不切 v4d）**：改 `RAG_MODEL_V4D` 指向 v2；引用质量低于 v4d，不推荐。
- **D（全量 A/B）**：先接 router 但按 10% 流量；当前无按请求灰度基础设施，成本更高。

## 5. 回滚
```powershell
# ① 删除/注释启动脚本中的 $env:KB_QA_LLM_PROVIDER = 'router'
# ② 再走一次受控重启（同上 flag + schtasks /run）
# ③ 验证 .llm.endpoint 变回 http://127.0.0.1:11434/api/chat
# ④ 代码级回滚：KB_QA_LLM_PROVIDER=ollama（默认值）即为旧口径；无需回滚代码
```
回滚耗时约 1 分钟（含 20s 停机），无数据变更、无 schema 迁移。

## 6. 验证与观察
- 重启后 `/api/health` 200；`/api/kb/qa?...&mode=single` 的 `llm.endpoint` 含 `router(`；
- `llm_cost_*.jsonl` 出现 `purpose=kb_qa`、`provider=bailian_v4d` 行；
- 连续 3 问无 fallback、答案含 `[unit_id]` 引用；观察 24h 成本与延迟；
- 失败信号：三层全失败使 `fallback=true` 比例升高 → 检查密钥/百炼服务/网络。

---
申请：Lead（W27）｜待批：老田｜建议窗口：低峰期（无自动任务排队时）

## 7. 审批
- 审批人：老田
- 审批时间：2026-11-04 20:36
- 结论：**批准**（选择「批准：现在重启并切 router」）

## 8. 实际执行记录
- 20:36:14 创建 flag + `schtasks /run`；20:36:18 脚本 killed `[32600,36832,37160,39268]`；
- 20:36:36 cold-start guard OK；20:36:37 启动新进程；20:37:07 `/api/health` 200（pid 15772）；
- 停机 **49s**（含导入冒烟 18s，正常）；首问 20:37:27 命中 `bailian_v4d`，
  `tokens=1438 cost=0.003852 purpose=kb_qa`；API 问答 2 条可核验引用、confidence=high。
- 详见 `deliverables/2026-11-04_W27_kb_qa_router_执行回执.md`。
- 回滚：删除启动脚本中 `KB_QA_LLM_PROVIDER='router'` 行并再次受控重启。

## 9. 第二次重启（2026-11-05，修 multi 分支绕过）
- 背景：2026-11-05 代码审计发现 `portal/api_kb_qa.py` 的 `mode=multi` 分支调用
  `multi.answer_multi` 时未传 `chat_fn`，实际仍走本地 Ollama（不产生 v4d 成本行），
  首次验收只测了 `mode=single`，属验收口径漏洞。修复见 commit **701c023fd**。
- 审批：老田 2026-11-05 00:23 批准「执行 L2 重启让 W27 multi 修复在生产生效」。
- 执行：00:24:38 flag 消费 killed `[12868,15772,37168]`；00:24:54 cold-start guard OK；
  00:24:56 starting；00:26:03 `/api/health` 200，portal api pid **52780**；停机约 **85s**。
- 验收：`mode=multi` 200/10.3s，`llm_cost_20261105.jsonl` 首行
  `provider=bailian_v4d total_tokens=1201 cost=0.002962 purpose=kb_qa`；
  `mode=single` 200，连续 4 行 v4d 成本记录；health 200。
- 详见 `deliverables/2026-11-05_W27_multi_router_重启执行回执.md`。
- 回滚：同第 5 节（删除启动脚本行 + 再次受控重启）。

