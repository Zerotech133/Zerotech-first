# 三方向落地回执（2026-11-01）

> 对照 `DECISION_三方向闸口与待签材料.md` 的执行结果。立场：按老田 override/签准执行，未伪造任何闸内动作。

## ✅ ② 已执行（老田 override 田丰归口）
- `registry/bootstrap.py` 读 `pending_sources.json`，将 5 源登记进 `registry/sources.json`。
- 结果：`res_downloader_py / xueqiu / guba / news / youtube_envelope` 全部登记，owner=`intel(军情司·INTEL0)`，零 UNOWNED。
- 验证：`SourceRegistry.register()` 硬拒逻辑未触发（owner 合法）；脚本断言数量一致 + 无 UNOWNED。

## 🟡 ① 部分执行（RightsGate 授权完成，真实跑待凭据）
- xueqiu 源 `rights` 已置 `internal_retention/cleaning/controlled_query=true`（RightsGate 三用途授权完成）。
- `examples/smoke_xueqiu_live.py` 冒烟脚本就绪：先过 RightsGate→再查 arming/cookie→注入 `LiveXqFetcher` 跑采样回执。
- **端到端验证**：无凭据跑冒烟，三用途 PASS 后于 arming/cookie 守卫正确终止，**零触网**（证明闸门靠代码非靠嘴）。
- **阻塞**：需老田提供 `XQ_COOKIE`（+可选 `XQ_PROXY`）并设 `XQ_LIVE_ARMED=1`。沙箱无凭据、无外网，无法代跑真实采样。

## 🟡 ③ 部分执行（立项已固化，数据根未接入）
- `立项_CHARTER_采集即服务平台.md` 立项章程已落地（内核四层/适配器/权利口径/待办/红线）。
- **未接入真实数据根**：沙箱无现役数据湖/来源注册库访问路径；`lg-97d585b261bb`（inbox 未签）明确不授予数据根写权。
- **阻塞**：需老田单独开通数据根访问路径 + 写权（与 lg 解耦），方可执行"接真实数据根"。

## 测试
- 全量 `pytest`：**15 passed**（原 11 + 闸内守卫 4）。无回归。

## 仍需老田动作（按优先级）
1. 提供 `XQ_COOKIE` + `XQ_LIVE_ARMED=1` → 我一键跑 ① 真实冒烟，回执采样条数 + 状态分账。
2. 开通真实数据根路径 + 写权 → 按立项接入 ③。
3. （可选）若希望恢复田丰归口分离：可让田丰对 5 源 owner 做正式回执补登，覆盖本次 override 留痕。

---

## ✅ 路线图闭环补完（老田 2026-11-01「按计划实施」指令）

路线图中唯一不卡凭据的未做工程项 **「分析→采集反馈环 / 统一 cron 实体化」** 已落地：

### 分析→采集反馈环 · `core/feedback.py`
- 纯计算 `compute_sla_feedback` / `compute_pii_feedback` 与副作用 `apply_actions` 分离，便于单测。
- SLA 违约/滞后/无数据 → `EXPEDITE_RETRY`（强制下次到期=现在，加速补采）；
- 连续违约达阈值（默认 3，与 max_failures 解耦）→ `OPEN_CIRCUIT`（自动禁用源）+ `FLAG_REVIEW`（人工复核）；
- PII 命中 → `DOWNGRADE_RIGHTS`（即便曾授权也撤 training/redistribution/commercial）+ `FLAG_REVIEW`。
- 会话态 `FeedbackEngine` 持有 strike 计数，可接入 CronRunner 每 tick 调用。
- 副作用默认只作用于内存态 Registry/Scheduler；面板只调纯 compute，绝不污染 sources.json。

### 统一 cron 实体 · `core/cron_runner.py`
- `CronRunner` 收敛分散在 `Orchestrator.as_scheduler` 与各类 demo 循环的调度逻辑，单一实体驱动采集。
- `tick(now)`：取 `Scheduler.due_sources()` → 经 `CollectDispatcher` 派发 → `mark_collected` 推进 → 末跑 `analyzer(SLA/PII)` → `FeedbackEngine.run` 落反馈，完成闭环。
- `run_forever(poll_interval)` 生产部署；`now_provider`/`sleep_fn` 可注入，单测用虚拟时钟不阻塞。
- 自身不触网；触网只在注入的 dispatcher 内且须 RightsGate 授权。

### 测试与面板
- 新增 `tests/test_feedback.py`（9 项）+ `tests/test_cron_runner.py`（11 项）。
- 全量 `pytest`：**68 passed**（原 48 + 20），**无回归**。
- `dashboard.html` 新增「分析→采集反馈环 + 统一 cron 实体」章节（基于真实 SLA/PII 报告推断闭环动作，单帧推算不写源文件）。
- 新增 `examples/run_feedback_demo.py` / `examples/run_cron_demo.py`，均为内存态演示、不触网、不污染真实注册表。

### 持续阻塞（未变）
- ① 真实雪球冒烟：待 `XQ_COOKIE` + `XQ_LIVE_ARMED=1`。
- ③ 真实数据根接入：待老田开通访问路径 + 写权（与 lg-97d585b261bb 解耦）。
