# GOV-MIN-004｜Portal 新增路由独立审阅（2026-10-18）

**结论：BLOCKED / HUMAN_REVIEW_REQUIRED。** 正式基线保持 72 条；封存检查显示 72 vs 106，列出 34 条新增字面量。下表是来源和用途的**静态代码证据清单**，不是运行可达性验证、owner 认领、变更审批或回滚授权。34 条全部 **owner = UNVERIFIED、审批 = 未批准**。不生成候选/正式基线，不切换或回滚生产配置。

## 证据边界和口径

- 封存差异：`docs/governance/rebaseline-001-20261018T002306+0800/route-baseline-check.stdout.txt:1-36`。此文件唯一确定本次 34 条清单及排序；没有重跑检查或探针。
- 人工决定：`docs/governance/rebaseline-001-decision-record-2026-10-18.md:11-19,68-89`，明确 72 条基线、历史 23 条与封存 34 条未对账，以及逐条审批前禁止重建/切换基线。账本与路由审批独立；本次没有读取或解析账本。
- 静态定位：`portal/route_baseline.py:35-56,87-108` 表明检查通过扫描 **`portal/portal_api.py` 中符合 `/api/...` 形式的字符串字面量**并与基线集合比较；该静态集包含代码中出现的字符串，不自动证明 HTTP 方法、服务当前运行版本、可达性、实际消费者、业务授权或删除安全性。下表结合 `portal/portal_api.py` 的分发代码/紧邻注释辨认用途；代码位置是审阅时的源码位置，不能替代封存时源码哈希核对。没有运行 nav 导出、刷新导航生成物或基线，也没有读取账本。
- 本报告的“回滚建议”均为**待 owner 确认的应急方案**，不是执行指令；先核对真实依赖、写操作和保护措施，取得独立审批后由授权执行人安排窗口并验证。尤其不能通过直接删除 34 条字面量、重建 106 条基线或改 nginx 来替代审阅。

## 封存新增 34 条逐条审阅

| # | 路由 | 静态来源位置和用途证据 | Owner | 审批 | 待审批回滚建议（不执行） |
|---:|---|---|---|---|---|
| 1 | `/api/ai_duty` | `portal/portal_api.py:2495-2498`，POST 分发调用 `_api_ai_duty`；`portal/api_misc.py:2143-2148` 会调用 `_audit_chat(..., timeout=90)` 模型链。`portal/portal_api.py:199-238,2450-2464` 显示本路径不在 `_MUTATION_POSTS`，因此**不进入该集合专属的 X-Portal-Token 检查及写端点频控**；不据此推断其他上游认证是否存在。 | UNVERIFIED | 未批准 | 优先核对真实上游认证/暴露面、模型调用成本与频控、paper 消费者；经授权先限制该入口的模型调用，再在独立审批后回退入口，保留审计。 |
| 2 | `/api/bt2_job` | `portal/portal_api.py:3226-3230`，GET 按 `id` 返回单任务回测结果。 | UNVERIFIED | 未批准 | 先核对任务详情页依赖和历史查询需求；审批后撤回详情入口/版本，保留已有任务记录。 |
| 3 | `/api/bt2_jobs` | `portal/portal_api.py:3220-3222`，GET `_bt2_status()` 任务清单投影。 | UNVERIFIED | 未批准 | 先核对回测进度消费者；审批后撤回列表入口，不能删除队列或任务状态。 |
| 4 | `/api/bt2_nl` | `portal/portal_api.py:2875-2883`，POST 自然语言回测解析，注释说明只解析不执行。 | UNVERIFIED | 未批准 | 核对输入/LLM 回退及表单依赖，获批后禁用解析入口并回退前端调用。 |
| 5 | `/api/bt2_run` | `portal/portal_api.py:2866-2874`，POST 参数化回测，daemon 线程运行引擎。 | UNVERIFIED | 未批准 | 先冻结新任务入口并确认在途任务处置；批准后撤回提交能力，不直接清理结果/状态。 |
| 6 | `/api/bt_daily_ledger` | `portal/portal_api.py:3231-3247`，GET 读取回测逐日结果文件并返回给回测中心；这里的“回测账本”不等于本治理工单禁止读取的持续漂移账本。 | UNVERIFIED | 未批准 | 核对 `bt_view.html` 展示依赖；批准后撤回读入口，保留原始回测产物，不触碰治理账本。 |
| 7 | `/api/capability/board` | `portal/portal_api.py:2951-2954` → `portal/api_capability.py:444`，能力看板只读聚合。 | UNVERIFIED | 未批准 | 核对看板依赖，获批后隐藏读面并回退调用，保留产物登记数据。 |
| 8 | `/api/combo_backtest` | `portal/portal_api.py:2856-2865`，POST 组合回测校验后落盘排队。 | UNVERIFIED | 未批准 | 先停新任务提交并核对已排队任务；获批后回退提交入口，不删队列产物。 |
| 9 | `/api/combo_backtest_jobs` | `portal/portal_api.py:3209-3211`，GET 组合回测队列/进度只读投影。 | UNVERIFIED | 未批准 | 核对队列展示依赖；获批后撤回查询入口，保留排队记录。 |
| 10 | `/api/ctl/alert` | `portal/portal_api.py:236,2523-2526`，POST Hermes 告警 ack/ignore/ack_all 写面，调用 `_api_ctl_alert`。 | UNVERIFIED | 未批准 | 优先冻结写入口并确认告警操作责任人与未处理操作；获批后回退接口，保全操作审计。 |
| 11 | `/api/ctl/alerts` | `portal/portal_api.py:3103-3111`，GET 从 `feishu_control.alert_state()` 读 OPEN 告警。 | UNVERIFIED | 未批准 | 核对飞书控制卡消费；批准后撤回只读面，保留告警源及状态。 |
| 12 | `/api/duty_brief` | `portal/portal_api.py:3112-3115`，GET 盘前简报，注释称 `?snapshot=1` 可触发 9:15 出件快照落盘，**不可当纯只读**。 | UNVERIFIED | 未批准 | 先核对定时调用及快照写入；获批后禁用出件参数/调用与入口，保留既有快照。 |
| 13 | `/api/flow/artifact` | `portal/portal_api.py:3033-3040` → `portal/api_flow.py:599`，GET 动作流产物预览。 | UNVERIFIED | 未批准 | 核对预览鉴权与消费者；获批后撤回预览入口，不删产物。 |
| 14 | `/api/flow/events` | `portal/portal_api.py:3033-3038` → `portal/api_flow.py:574`，GET 动作流事件聚合。 | UNVERIFIED | 未批准 | 核对事件页依赖；获批后撤回读面，保留事件源。 |
| 15 | `/api/flow/rerun` | `portal/portal_api.py:237,2487-2493` → `portal/api_flow.py:659`，POST 白名单产线任务重跑，注释涉及 `schtasks /Run`。 | UNVERIFIED | 未批准 | 高优先级核对授权/任务白名单及在途重跑；先冻结触发能力，再经审批回退入口，勿自行操作计划任务。 |
| 16 | `/api/flow/stats` | `portal/portal_api.py:3033-3036` → `portal/api_flow.py:560`，GET 动作流统计聚合。 | UNVERIFIED | 未批准 | 核对看板消费者；批准后撤回统计入口，保留原始事件。 |
| 17 | `/api/health` | `portal/portal_api.py:3083-3085`，GET 七口探活和五守护任务监测，非 `/health`。 | UNVERIFIED | 未批准 | 先核对健康页/告警对该路径的依赖；批准后回退页面调用，不能误删 `/health` 探针。 |
| 18 | `/api/jev-stack` | `portal/portal_api.py:3086-3088`，GET 四件套 metrics、换血时间轴、死信聚合观测。 | UNVERIFIED | 未批准 | 核对观测页依赖；批准后撤回观测入口，保留监控源。 |
| 19 | `/api/kg/lineage` | `portal/portal_api.py:2911-2914` → `portal/api_lineage.py:98`，GET 情报板块血缘只读端点。 | UNVERIFIED | 未批准 | 核对 `/lineage.html` 数据面；批准后撤回展示入口，不删图谱/血缘数据。 |
| 20 | `/api/my_strategies` | `portal/portal_api.py:208,2884-2888,3223-3225`，同一路径 GET 列表、POST 保存/删除个人策略。 | UNVERIFIED | 未批准 | 先冻结 POST 修改并确认用户保存数据；获批后同时回退 GET/POST 消费者，保全策略文件。 |
| 21 | `/api/ops_today` | `portal/portal_api.py:3212-3219`，GET 调用 `ops_signal_today.compute()`；`quant_backtest/ops_signal_today.py:87-104,139-169,186` 显示缓存命中直接返回，但缓存未命中时创建状态目录、推进 seen-ever 信号状态并写入 `signal_state.json` 和 `ops_today_cache.json`，**不可当纯只读**。 | UNVERIFIED | 未批准 | 先核对页面调用、状态写入来源及未命中缓存时的副作用并保全状态文件；取得审批后再冻结调用/回退接口，不清理或重写缓存与状态。 |
| 22 | `/api/os` | `portal/portal_api.py:3505-3508`，GET 个人 OS 采集、Agent、量化、入库四域聚合。 | UNVERIFIED | 未批准 | 核对个人 OS 页面依赖；批准后撤回聚合读面，保留各源系统。 |
| 23 | `/api/perf/board` | `portal/portal_api.py:2915-2918` → `portal/api_perf.py:148`，GET 投研绩效看板。 | UNVERIFIED | 未批准 | 核对绩效页依赖；批准后撤回读面，保留绩效来源数据。 |
| 24 | `/api/perf/replay` | `portal/portal_api.py:2919-2922` → `portal/api_perf.py:162`，GET 影子→Paper→成交→绩效回放。 | UNVERIFIED | 未批准 | 核对回放消费者；批准后撤回回放入口，保留链路原始证据。 |
| 25 | `/api/platform/approvals` | `portal/portal_api.py:3004-3009` → `portal/api_approvals.py:37`，GET 平台审批注册表读面。 | UNVERIFIED | 未批准 | 核对审批页依赖；批准后撤回查询接口，不能删除或改写审批记录。 |
| 26 | `/api/platform/replay` | `portal/portal_api.py:3004-3011` → `portal/api_replay.py:27`，GET 平台回放。 | UNVERIFIED | 未批准 | 核对回放页依赖；批准后撤回读面，保留原始链路证据。 |
| 27 | `/api/platform/trace` | `portal/portal_api.py:3004-3013`，GET **别名**，与 `/api/platform/replay` 均调用 `_platform_replay(qs)`；未见独立 trace 处理。 | UNVERIFIED | 未批准 | 先确认兼容调用方及别名契约；批准后仅撤回别名或同步回退消费者，不误伤 replay 主路径。 |
| 28 | `/api/unified/evidence` | `portal/portal_api.py:3015-3022` → `portal/api_unified.py:96`，GET 统一证据投影。 | UNVERIFIED | 未批准 | 核对证据展示消费者；批准后撤回投影接口，保留源证据。 |
| 29 | `/api/unified/knowledge` | `portal/portal_api.py:3015-3024` → `portal/api_unified.py:120`，GET 知识统一投影。 | UNVERIFIED | 未批准 | 核对知识页消费者；批准后撤回读面，不清理知识源。 |
| 30 | `/api/unified/replay` | `portal/portal_api.py:3015-3020` → `portal/api_unified.py:64`，GET 跨账本串线回放；此处只读源码，不读取任何账本实例。 | UNVERIFIED | 未批准 | 核对回放引用方及敏感信息暴露；批准后撤回查询入口，不对账本作任何改动。 |
| 31 | `/api/unified/skills` | `portal/portal_api.py:3015-3026` → `portal/api_unified.py:130`，GET Skill 统一投影。 | UNVERIFIED | 未批准 | 核对 Skill 展示消费者；批准后撤回读面，保留登记源。 |
| 32 | `/api/unified/usage` | `portal/portal_api.py:3015-3028` → `portal/api_unified.py:137`，GET 使用量统一投影。 | UNVERIFIED | 未批准 | 核对使用量展示消费者；批准后撤回读面，不改源记录。 |
| 33 | `/api/watch-tower/snapshot` | `portal/portal_api.py:3068-3082`，GET Watch Tower 快照，可带 `replay_at` 参数；调用 `_WATCH_TOWER_SNAPSHOT.snapshot`。 | UNVERIFIED | 未批准 | 核对快照与回放消费者；批准后回退入口，保留源状态和历史证据。 |
| 34 | `/api/watchtower/overview` | `portal/portal_api.py:3068-3082`，GET 与 `/api/watch-tower/snapshot` 共用分支，注释明确为只读兼容别名。 | UNVERIFIED | 未批准 | 先核对旧调用方；批准后优先逐步迁移别名消费者，避免直接移除导致兼容中断。 |

## 补充代码审计：既有模型入口（不计入封存新增 34 条）

- `POST /api/audit_chat` 已在正式路由基线中，**不是**上表新增第 35 条。`portal/portal_api.py:2483-2485` 分发至 `portal/api_misc.py:1936-1968`；它与新增 `/api/ai_duty` 均不在 `portal/portal_api.py:199-238` 的 `_MUTATION_POSTS` 内，因此均未经过该集合专属 token 校验和每 IP 12 次/分钟频控。只读 AST 探针确认两者在 `do_POST` 分发而不在该集合；没有发送 HTTP 请求或调用模型。这不证明不存在其他上游认证。
- `/api/audit_chat` 的 `preset=last_commit` 可在服务端读取最近提交 diff 并交给模型回退链；`/api/ai_duty` 同样调用模型。两者风险为资源占用与可能的资料外送，需进一步核对上游暴露面、模型配置、调用配额和输入体积。现有审计页与 `paper.html` 均未显式发送 portal token；部分页面由静态 nginx 提供，不能假设动态 `_html` 的本机 token 注入覆盖所有客户端。**不得未经客户端兼容验证直接加入 `_MUTATION_POSTS`**，应先设计独立的认证/限流/正文上限与离线负面测试，走单项审批后实施。
- `GET /api/ops_today` 仍为带状态写入的计算入口；`ops_signal_today.py:98-105` 的缓存命中条件仅比较池文件名，不能证明同名池或行情更新后结果新鲜，`:140-169,186` 的状态与缓存写入亦需并发/原子性审查。无 owner、冻结窗口和隔离测试之前不改其生产路径或数据状态。

## 未决、下一步放行门槛

1. **历史 23 vs 封存 34 未对账。** 决策记录同时保留历史“23 条”及本封存“34 条”口径，当前既没有历史 23 条的逐项对照表，也没有可审计解释去证明差额 11 条的来源/时序/去重关系。不能把两者相减后的 11 条自行标记为“后来新增”，更不能以 34 条替换历史口径。需另行提供原始历史 23 清单、取样时间、扫描规则、源码版本并人工逐条比对。
2. **34 条 owner 全部 UNVERIFIED、审批全部未批准。** 代码注释中的项目编号、模块名和用途并不是人名/团队认领、上线审批或回滚授权。逐项补齐负责人、风险/依赖、变更缘由、审批记录、可演练回滚步骤及独立 QA；POST 重跑、回测提交、告警写面、`/api/ai_duty` 模型调用与专属 token/限流闸门缺口，以及 GET 的 `?snapshot=1` 和 `/api/ops_today` 状态/缓存写入等副作用优先审查；该闸门缺口不等于所有上游认证均缺失。
3. **静态/封存一致性尚未证明。** 当前源码行号仅提供定位线索。后续在明确授权与冻结条件下，再核对封存源码指纹、部署版本、导航消费、方法/鉴权和真实依赖；如果研究导航导出，只能静态阅读现有源码和既有产物，不能运行导出或覆盖生成物。任何运行态验证、回滚和基线切换都需另行授权。
4. **独立门禁。** 按决策记录，在账本稳定快照与 owner/写入窗口核验、路由逐条审批、独立 QA 和正式基线切换单独人工批准完成前，维持 `DRIFT_DETECTED / HUMAN_REVIEW_REQUIRED / BLOCKED`；本报告不改变这些状态。
