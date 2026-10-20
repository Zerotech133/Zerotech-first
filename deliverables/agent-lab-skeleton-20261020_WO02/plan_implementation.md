# 实施计划与风险矩阵（骨架配套）

> 生成物为工程骨架，业务逻辑需按领域实现；本计划中的工作量均为待确认的估算，
> 阶段门启用须老田批准。

## 一、阶段划分

| 阶段 | 内容 | 完成标志 | 依赖 | 责任人 |
|---|---|---|---|---|
| S0 骨架评审 | 三席分工、守门边界、总线层级契约评审定稿 | 老田确认角色表与红线 | 无 | 老田 |
| S1 总线与基类 | message_bus 层级路由/署名校验/账本 + base_agent 汇报重试 | 环回测试：越级消息被拦、死信可查 | S0 | 下游开发 |
| S2 中台三席 | 任务分解、简报汇编、督办回执三个最小闭环 | 一张目标单可走完 分解→执行→回执→简报（2026-10-20 端到端 7 用例贯通 + compile_briefing v0 已交付；简报尚未接总线视图，接线挂 S3 前清障） | S1 | 下游开发+老田验收 |
| S3 平台接入 | platform_agent 模板接首个平台（建议量化），接 events/tasks 既有家底 | 平台日报按时上行、专业账本只读投影（2026-10-20 量化试点交付：模拟推演引擎+平台日报+模拟账本快照，tasks_adapter 只读适配器就绪；真实数据源/真实后端接线与定时上行为下一波，须另批） | S2 | 平台负责人 |
| S4 守门上线 | risk_guard 规则标定、approval_gate 身份方案与账本落盘 | 预警按标定阈值触发；签准全走人工账本 | S2 | 风控+老田 |
| S5 扩编 | 其余 5 平台实例化、监控接入 OS 单屏驾驶舱 | 六平台日报齐、驾驶舱只读投影同源（2026-10-20 离线部分交付：PLATFORM_REGISTRY 六平台注册表+make_platform 工厂、OsCommander.digest 六平台汇聚快照、部署/监控席位端口对齐经离线校验；领域推演引擎替换、真实数据源、K8s 全席位清单扩编挂账） | S3 | 各平台负责人 |

**串行纪律**：S0 是硬前置；S4 的身份方案未经老田与安全席裁定前，approval_gate 只允许"登记+提醒"，不实现任何认证放行。

## 二、风险矩阵

| 风险 | 概率 | 影响 | 触发条件 | 应对 | 责任人 |
|---|---|---|---|---|---|
| 三席分工与老田设想不一致，返工 | 中 | 高 | S0 评审否决角色表 | 骨架按席位文件切分，重划不动总线 | 老田 |
| 中台过度调度越过审批红线 | 中 | 高 | 任何 Agent 自行放行签准/交易 | approval_gate 独立进程+无自动放行路径；risk_guard 监控越权企图 | 风控 |
| 总线单点故障丢汇报 | 中 | 中 | bus 容器重启/网络分区 | 汇报重试+席位本地队列（S1 待实现）；死信可人工补投 | 下游开发 |
| 阈值占位值直接投产误报/漏报 | 高 | 中 | 未标定即启用 alerts.yml | 告警规则注释强制标注占位；S4 标定为上线前置 | 风控 |
| 平台账本与 OS 投影不一致（第二账本） | 中 | 高 | 平台私改汇总口径 | 只读投影、不造第二账；偏差进 risk_guard 事件 | 各平台负责人 |
| 骨架被误当完整系统直接部署 | 低 | 高 | 跳过 TODO 实现直接 compose up | README/产物清单显式标注骨架与待补项；审计非 0 不交付 | 下游开发 |

## 三、待补项落人（摘要）

- 任务分解策略、冲突仲裁逻辑 → 下游开发（S2，依据 S0 定稿）
- 汇报重试与本地队列 → 已实现（S1，31 用例覆盖）；剩余跨进程文件锁 → 下游开发
- 本人身份核验方式 → 老田+安全席（S4 前裁定；principal_proof 仅记录不构成认证）
- 告警阈值标定 → 风控+老田（S4）
- 各平台专业执行器（推演引擎） → 各平台负责人（S3/S5）
- **P5 复核观察项（2026-10-20）**：① ~~zhuge_liang.escalate 直发 principal 会被总线白名单拦截入死信~~——**已裁定消解**：老田 2026-10-20「方案B 白名单扩员」（PRINCIPAL_WHITELIST=(risk_guard, approval_gate, tian_feng)），升级请示经归口席以 escalation_request 类直发本人，分级判定在归口席；总线校验「白名单 + 消息类别」两道：白名单 PRINCIPAL_WHITELIST=(risk_guard, approval_gate, tian_feng)、类别词表 PRINCIPAL_MESSAGE_CLASSES（remediation_ticket/escalation_request/alert/approval，类别以 subject 前缀承载并在账本派生 message_class 字段可审计）；② ~~entrypoint.py 的 message_bus 导入降级占位路径待移除~~（已移除，导入失败 closed on failure，S2 收口波次工位H）。
- **S2 收口波次遗留（2026-10-20 09:18 批，三工位交付）**：田丰工单生命周期归口 API 待补~~（现借用诸葛 transition）~~（已补齐 transition_dev_task，S3 前置波次工位I）；工单状态是否经总线留痕待老田裁定；~~compile_briefing 接入总线视图与实例方法挂 S3 前清障~~（已交付 briefing_from_bus，工位J）；停滞阈值（STAGNATION_MIN_SPAN_S=3600 等）为占位待真实基线标定；字符串 ts 的停滞跨度量化方式待裁定。
- **S3 前置清障波次新增（2026-10-20 10:08 批）**：os_commander 已由占位升级为归集驾驶舱最小实现（collect 只读投影，UI 另立）；诸葛 issues 台账落位（briefing_from_bus 现按空清单读取）；S3 正式波次=量化平台首个接入试点（对接 events/tasks 既有家底）+ 各平台真实推演引擎。
- **S7 裁定落地波次新增（2026-10-20 11:32 批，三工位交付）**：方案B 分级判定与直发通道测试补齐 16 用例（工位V）——grade_issue 复核改写 severity 无幂等/留痕快照、escalate_issue 重复投递无去重，是否加幂等待老田裁定；总线 route 只记账无实际投递（message_bus.py TODO，实际送达接线挂领域实现）；赵云 check_overdue 督办节奏落地（工位W，窗口 3600s/次数 2 占位待标定，轮次状态内存态重启归零、跨进程持久化待评估，升级动作由调用方决定未接总线）；BaseAgent file_lock 跨进程锁落地（工位X，重入显式拒绝、子进程探针验证互斥）——既有账本写路径（pending_reports.jsonl 等）接锁待编排者统一评估，msvcrt.LK_LOCK 阻塞式如需超时语义待定。
- **S8 接线收尾波次新增（2026-10-20 11:49 批，三工位交付）**：总线 inbox 实际投递已接入（工位Y，落账后投递 expected_target、record 增 delivered、DLQ 不投递、回捞后投递；跨进程投递与重试策略仍挂领域实现）；pending 队列写路径已接 file_lock 边车锁（工位Z，直锁实测 msvcrt EOF 字节撞写 PermissionError 故选边车；新挂账：send_pending 读-改-写 TOCTOU、file_lock 重入登记非线程原子、bus_ledger 等其他账本写路径待评估）；督办轮次状态持久化已接入（工位AA，state_path 可选注入、重启计数延续、损坏重置+data_gaps）并测通赵云草稿→田丰登记→分级消费 E2E。
- **S9 工程评估收口波次新增（2026-10-20 12:16 批，三工位交付）**：send_pending TOCTOU 已修复（工位AB，三阶段时序：锁内快照→锁外发送→锁内重读合并重写，期间新增条目不丢失、混合压力零丢失零重复；新挂账：跨进程并发 send_pending 整周期互斥——当前仅写路径互斥，跨进程双发理论窗口未扩大）；file_lock 重入登记表已加守卫锁原子化（8 线程压力无竞态，重入拒绝语义不变）；总线账本写路径已接边车锁（工位AC，ledger/dlq 两路径，file_lock 从 base_agent 导入实测无循环导入）；board 状态点过期判定已接入（STATUS_STALE_TTL_S=300 占位待真实心跳基线标定，stale_reason 条件性增量字段，断连席位剔除策略挂领域实现）；requirements.txt 已锁定版本转可运行（工位AD，单机实测来源注释，pydantic 为 fastapi 传递依赖白名单放行）+ tests/test_requirements.py 双向一致性校验。
- **S10 契约守护波次新增（2026-10-20 13:33 批，三工位交付）**：席位 API 路由补齐 HTTP 层测试 13 用例（工位AE，含"零真实网络"自检；观察项：BaseAgent 实际路由为 POST /task 非 /receive，create_app 拒绝语义一律 200+body.ok=false，调用方须读 body.ok 不能只判状态码）；未投递消息挂起与注册补投已接入（工位AF，PENDING_DELIVERY_MAX=1000 占位、超限丢最旧+warning+账本留痕、redelivered 追加行不改原 record；挂起队列进程内内存态、跨进程持久化挂领域实现）；文档产物一致性机器守卫已建立（工位AG，10 用例：artifacts 文件存在性/计数三方一致/README 表路径与计数/角色四要素/端口三处一致/骨架必含 todo/status 枚举与 verified 标志/名称唯一）。**AG 查账发现并已收口**：并行会话 11:29 落的 roles/rectification_line.py 未登记（已补登 artifacts 与 README）；README 陈旧计数（28 文件/240 用例）已刷新；README 核验命令 lab_audit 路径已改为技能目录引用。
- **S11-S12 安全与持久化加固波次（2026-10-20 午后批，六工位交付，登记见 deliverables 各 S 工单件）**：归口幂等（grade_issue 复核留快照/escalation 去重 12 例）、离线签准验签（Ed25519 + durable nonce 19 例）、阈值标定拒绝造数（10 例+工具）、田丰主状态原子持久化（18 例）、签准账本跨进程锁与 LedgerCorrupt fail-closed（20 例）、board 契约版本信封（15 例）、pending 跨进程互斥与 TTL 租约（17 例，CB2 闭合 Windows os.replace 竞态）、总线席位鉴权 65 例、entrypoint 接线（LEGION_SEAT_TOKENS + uvicorn + **bind 与鉴权联动**：未配凭证只监听 127.0.0.1，无 --bind 逃生参数）、WAL 送达预写日志（14 例，漏报比重复危险口径）。
- **S13 回归收口（2026-10-20 17:20 批）**：全量复跑 **推翻 S12「全绿」结论**——实测 514 passed / 1 failed，`test_cross_process_append_keeps_ledger_parseable` 独立重跑 11 次 3 红（≈27%）。根因（WO-LEGION-S13-01 已闭合）：Windows CreateFile 在多进程同时首次创建同一 .lock 文件的窄窗口返回 EACCES，`file_lock` 的 `open("a")` 命中，异常上抛表现为随机丢单。四组对照实验排除区间锁冲突/TEMP 目录/纯高频三假设。处置：`_open_lock_handle` 有限退避重试（只重试 PermissionError、耗尽抛原异常**不降级无锁**、重试成功也留痕）；双向变异 + 20/20 收敛 + 全量 518 passed。残余：窄窗口在 CreateFile 层的确切触发条件未定位，升级判据=真实并发出现重试告警。
- **S14 心跳对账波次（2026-10-20 19:40 批）**：WO-LEGION-S14-01 已闭合——`BaseAgent.on_heartbeat()` 周期钩子（默认空实现、异常留痕不崩）+ `TianFeng.on_heartbeat()` 覆写（`_last_reconcile_ts=None` 首个心跳立即对账清重启遗留悬挂，之后按 `WAL_RECONCILE_MIN_INTERVAL_S=600s` **占位**节流；补投打 warning、失败进 data_gaps）。闭合 reconcile_wal docstring 的 TODO「补投依赖调用方」：长期无新升级时未闭环 intent 不再悬挂（漏报比重复危险口径）。新 7 例 + 双向变异（M1 恒跳过 4 红/M2 恒对账 e 红），全量 **525 passed / 0 failed**。挂账：600s 为占位待真实基线标定；WAL 双失败（WAL 写失败+主状态落盘失败）不可判定与 state_path 多进程写不合并仍挂领域实现。
- **S15 降级留痕波次（2026-10-20 21:15 批）**：WO-LEGION-S15-01 已闭合——WAL 双失败（intent 写失败降级送达 + 主状态落盘失败）从「零痕不可判定」收窄为「有痕可审计」：新增第三条持久化通道 `<state名>.degraded.jsonl`（append-only、独立锁，与 WAL/主状态互不依赖），**送达前**写入（先落盘后发送硬约束）；重启加载进 data_gaps（`degraded_send_unresolved` + 人工核账指引），坏行保留原文。三层兜底：WAL → 降级账本 → data_gaps（内存）。降级条目**不自动补投**（降级场景磁盘正在故障，补投走 WAL 是伪解，显式披露）。新 6 例 + 双向变异（M1 禁送达前留痕 a/b/c 红、M2 禁重启加载 b/d 红），全量 **531 passed / 0 failed**。挂账：三层兜底最后一层仍为内存态（WAL+降级账本同时失败且进程死亡 = 无标准库手段可落盘，如实披露）；「WAL 持续故障下的补投通道」另立单。
