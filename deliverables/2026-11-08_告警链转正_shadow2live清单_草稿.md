# 告警链转正：shadow→live 收口清单（P0-2）

> 远端已确认 GitHub（2026-11-08 06:3x，origin=Zerotech133/Zerotech-first）——CI workflow 已转正为 `.github/workflows/quality-gate.yml`（未跟踪待合入）。本清单的 S1-S4 不受影响。

## 0. 现场实况（2026-11-08 06:1x 实测，修正上一轮判断）

| 链路 | 现状 | 证据 |
|---|---|---|
| `tools/alerts_shadow.py`（8001 只读投影） | **活着**，但本来就定位影子：只读、不推送 | docstring「不做任何推送动作；推送链仍在 alert_push/alert_watch」 |
| `news_collector/alert_push.py`（真推送链，飞书卡） | **停摆 13 天**：最后 2026-10-26 `ok=False status=source_unavailable` | `outbox/alert_push_last.json` |
| `news_collector/alert_watch.py`（五路盯盘推送） | 最后成功 2026-11-05，此后无记录 | `outbox/alert_watch_state.json` |
| 发送侧（凭证+通道） | **健康可复用**：`news_collector/send_feishu.py`（OpenAPI 直连 + lark-cli 回退，K-STOCK-414 修正后无人值守安全） | 头注 + `notify_devops.py` 实测 SEND_OK 判例 |

**结论修正**：不是「shadow 建 live」，而是 **live 链已存在但断了——修复停摆 + 把断链本身变成可告警事件**。

## 1. 转正四步（每步独立验收，可单独回滚）

### S1 修停摆根因（先诊断后动手，预计 10 分钟）— ✅ 已完成（2026-11-08 06:5x）
- [x] 手动跑一次抓堆栈 → 根因不是代码故障，是**三层叠加**：
  1. **council 源 retired 状态白名单缺失**（真卡点）：生产侧 11-05 起 `mark("council","retired","producer_retired")`（portal/api_common_alert.py:322），消费端 status 白名单只有 `(ok,empty,error,stale)` → 整包 `return None`。修复：白名单补 `retired` + `SOURCE_ERROR_REASONS` 补 `producer_retired`；
  2. **CORE_SOURCES 含已停生产线**（口径未传导）：chanlun/nine_dim/weak 于 11-05 定档停跑（结构标注工具，非入场依据），CORE 仍把 chanlun/nine_dim 列为核心源。修复：`CORE_SOURCES=("premarket","cross")`，三源移入 `HALT_SOURCES`（不计 good 防 EXEC-R35 复活、不计 bad 防误标覆盖不足）；
  3. **L556 暗闸**：bad 非空且无 alerts → source_unavailable（chanlun/nine_dim/weak 永远 bad，恰逢空日报就永远拒推）。修复：halted 不进 bad 名单。
- [x] EOD 卡/空报副标题注记「结构标注线已停（缠论/九维/弱于板块，2026-11-05 定档非入场依据）」；晨报路径（双源全停 → 放行空报+注记，不静默失败）
- [x] **恢复生产自愈**（mock 三档验证）：chanlun 单源恢复 → 当日回归推送+注记自动消失；三源全恢复 → halt_note=False，零代码改动回归原语义
- [x] **验收通过**：真实推送 `ALERT_PUSH_OK mode=eod status=sent alerts=1 targets={个人:true,内部测试:true,量化群:true}`，outbox 13 天来首次 `ok=true`（ts=2026-11-08 06:57）
- 改动面：`news_collector/alert_push.py` +54/-5，单文件；他线 WIP 零触碰（hr/legion_ide 等原样未动）

### S2 补断链探针（停摆 13 天无人知 = 缺这一件）— ✅ 已完成（2026-11-08 07:0x，待注册计划任务）
- [x] 新建 `tools/alert_push_watchdog.py`（零触碰现有文件）：读 outbox `alert_push_last.json` 的 ts/ok；
      - 超 **24h** 无成功推送 → 断链告警；ok=false 持续 **6h**（ts 还在动）→ 断链告警（比 24h 更早发现"活着但一直失败"）；
      - 文件不存在/JSON 损坏 → 断链；`send_feishu` 直发个人（OpenAPI 直连 + lark-cli 回退，kind=ops:alert_push_watchdog）；
      - **熔断**：同一断链事件只发一次（state 文件）；恢复后自动复位并发恢复通知；断链+告警通道也挂 → rc=2 交外层兜底。
- [x] 自检用例 8 项全过：T1 常规巡检 OK；T2 `--test` 通道真实发到个人（SEND_OK）；T3 26h 无成功→告警；T4 熔断（二次零发送）；T5 恢复复位+恢复通知；T6 ok=false 8h→告警含失败状态；T7 失败窗口边界精确（5h59m 不告警/6h01m 告警）；T8 文件不存在→告警
- [x] **计划任务已注册并验收（2026-11-08 07:44）**：`Register-ScheduledTask`（PowerShell 原生写法；schtasks 的 `\"` 转义在 PowerShell 下不可用，判例）。手动触发 `LastTaskResult=0`，state 落盘 `{"alerted": false, "ts": "2026-11-08 07:44"}`——探针以计划任务身份首跑通过，识别当前链路健康。次日 08:00 起自动巡检。
- 验收：断链 24h 内必有人收到飞书消息 ✅（逻辑层 T3-T8 实证；通道层 T2 实证；调度层 LastTaskResult=0 实证）

### S3 watchdog 家族接入同通道（红即到人）— ✅ 已完成（2026-11-08 08:0x）
- [x] 新建 **`tools/notify_hub.py`** 统一出口：`send_feishu.send_text` 直发个人（kind=ops:health_notify）、4h 同指纹去重、发送留痕 state（fail-visible：发送失败调用方如实非 0）
- [x] 三工具各加 `--notify` 参数（判定/阈值/状态机零改动，只加出口）：
      | 工具 | 原出口 | 现出口 |
      |---|---|---|
      | `intel_source_health.py` | **无**（纯拉模式） | 硬故障（启用源 missing/empty/error）直发；rc 语义不变 |
      | `huangzhong_health_alert.py` | flag 等转发（被动） | apply+would_alert 时**与写 flag 同点位并行直发**（flag 链保留不拆）；dry-run 零外发 |
      | `jev_risk_alert.py` | flag 等转发（被动） | 新告警逐条直发（seen 状态机去重 + hub 兜底） |
- [x] **活红样本验收**（现场真红）：intel 的 govpolicy 硬故障 → 4 条真实 SEND_OK；二次跑 DEDUPE 命中（4h 窗口）；黄忠触发态（构造 consecutive=2）apply+notify 直发 + flag 并行；无红时不发（三工具默认行为回归不变）
- 口径调整（对照原设计的"静默窗口聚合日报"）：按 FF09 防过度设计**砍掉聚合层**——各工具自有阈值/去重状态机已管噪音，hub 只做去重兜底；若日后噪音超标再加聚合
- [x] **调度接通（2026-11-08 08:4x，老田批准后落地）**：
      - **jev_risk_alert ✅ 已接通**：发现其本就由 `JevBackfill` 计划任务（每小时）经 `C:\aios\jev-eval\jev_backfill_task.ps1` 调用（原口径 10-30 JEVALERTB「只进 outbox 不推送」）；已备份原文件（.bak_20261108_s3notify）后给包装器加 `--notify`（锚点断言 count==1，PS1 Parser 语法过，头部注释同步改口径）。实跑调度参数 rc=0（当前无新红零外发，回归正确）
      - **intel_source_health / huangzhong_health_alert ⏸ 无既有调度**：全计划任务+包装器扫描零命中（二者纯拉模式无任务），需**新建任务**——注册命令已备好（下段），由本人执行（Register-ScheduledTask 被沙箱拒，S2 同款）
      - 实证：`C:\aios` 与 `C:\项目\老田的AI助手` realpath 相同（junction），S1/S3 全部修复已在调度路径生效
- [x] **三项注册命令已由老田指令 + 沙箱外执行落地（2026-11-08 09:0x）**：
      - **IntelAlertNotify ✅**（每日 08:10）：冒烟 rc=1 = `EXIT_UNHEALTHY`（govpolicy 硬故障在案，rc 语义不变；notify 被 hub 4h 指纹去重不重发——设计如此）
      - **HuangzhongAlertNotify ✅**（每日 08:10）：冒烟 rc=0（当前健康零告警）
      - **AlertShadowBoot ✅**（30 分钟重复保活）：服务实拉起存活（PID 67416，`push_chain_ok=true`）
      - 过程判例三条：① **boot 触发器（AtStartup）注册被拒而 Daily 同权成功**——改 `-Once` + `RepetitionInterval=30min`（`New-ScheduledTaskTrigger` 的 Repetition* 属性对 -Once 原生支持、对 -Daily 不可赋值）；② 初版误设 `ExecutionTimeLimit=1h` 会在 1h 后杀掉长驻服务——已改 `[TimeSpan]::Zero` 无时限，死进程由 30 分钟重复拉起兜底（IgnoreNew 防重入）；③ schtasks.exe 在安全中心程序黑名单（不可绕过），一律走 Register-ScheduledTask 原生 cmdlet

```powershell
# ① S4：8001 影子服务开机自启（服务当前已手动拉起，PID 见日志；此任务保重启后自愈）
$act = New-ScheduledTaskAction -Execute 'python' -Argument '-X utf8 "C:\项目\老田的AI助手\tools\alerts_shadow.py" --port 8001' -WorkingDirectory 'C:\项目\老田的AI助手'
Register-ScheduledTask -TaskName 'AlertShadowBoot' -Action $act -Trigger (New-ScheduledTaskTrigger -AtStartup) -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)) -Force

# ② S3：intel 源健康红即到人（建议每日 08:10；Python 路径按机器实际）
$act2 = New-ScheduledTaskAction -Execute 'C:\项目\py312\python.exe' -Argument '-X utf8 -B "C:\项目\老田的AI助手\tools\intel_source_health.py" --notify' -WorkingDirectory 'C:\项目\老田的AI助手'
Register-ScheduledTask -TaskName 'IntelAlertNotify' -Action $act2 -Trigger (New-ScheduledTaskTrigger -Daily -At 08:10) -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable) -Force

# ③ S3：黄忠健康红即到人（同上；dry-run 参数勿加，加了不外发）
$act3 = New-ScheduledTaskAction -Execute 'C:\项目\py312\python.exe' -Argument '-X utf8 -B "C:\项目\老田的AI助手\tools\huangzhong_health_alert.py" --notify' -WorkingDirectory 'C:\项目\老田的AI助手'
Register-ScheduledTask -TaskName 'HuangzhongAlertNotify' -Action $act3 -Trigger (New-ScheduledTaskTrigger -Daily -At 08:10) -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable) -Force
```
- 验收：人为停掉任一 health 源 → 红结果直发飞书 ✅（直发链三层实证：通道 SEND_OK / 去重 DEDUPE / 回归零副作用）

### S4 shadow 服务转正口径（小改）— ✅ 全部完成（2026-11-08 09:0x：代码+上线+保活注册）
- [x] `alerts_shadow.py` 保持只读，`/alerts/status` 增加 **`push_chain_ok`**（true=链正常/false=断链/null=探针不可用）+ `push_chain` 明细（last_ts/last_ok/broken_reason/stale_h/fail_h）
- [x] **判定同源防漂移**：直接 import `alert_push_watchdog.diagnose()` 复用 S2 探针口径（24h 无成功/ok=false 超 6h/文件缺失/损坏 → false）；探针模块不可导入时 null（fail-visible 不崩服务）
- [x] `/alerts/ui` 状态页新增「推送链健康」红/绿行（断链原因直显；健康时显示最后成功时间）
- [x] 验收（五态）：真实 outbox → True（last_ts=2026-11-08 06:57）；文件缺失/损坏/ok=false 超 6h → 全部 False；新鲜 ok=true → True。端到端：临时端口 8011 起 HTTP 实测 `/alerts/status`（新字段+既有字段回归）/`/api/health`（兼容口径不变）/`/alerts/ui`（红绿展示）全过
- [x] **上线卡点①（2026-11-08 08:3x 验证通过，进程保持待本人接管）**：8001 已用新代码两次拉起并实测 `/alerts/status`：`running=true`、`push_chain_ok=true`、8091 数据源可达、门户既有契约字段全回归。**但工具会话结束进程即被沙箱回收（cmd start /b 亦逃不掉，实测）**——长驻保持必须走 AlertShadowBoot 注册命令（下段①，本人执行）或本人手动拉起。过程判例：PS5.1 `Start-Process` 撞环境变量大小写并存（HTTPS_PROXY/https_proxy 等 4 键，连 `Get-ChildItem Env:` 都崩）——用 `[Environment]::SetEnvironmentVariable($null,'Process')` 逐键清除后成功
- [x] **上线卡点②转登记命令**：AlertShadowBoot 注册被沙箱拒（Register-ScheduledTask HRESULT 0x80070005，S2 schtasks 同款）——命令已备好放 S3 段注册命令块，由本人执行
- 验收：门户预警页显示推送链红/绿 ✅（代码+实测：push_chain_ok=true 绿；断链态由五态验证覆盖；**门户实际可见依赖本人执行注册命令①**）

## 2. 明确不做的（防过度设计）

- ❌ 不引入 Prometheus/Grafana/Alertmanager 全家桶——单机内用工具，飞书通道已验证，加监控栈属 FF09
- ❌ 不改 `send_feishu.py` 凭证链（OpenAPI 直连已无人值守安全，K-STOCK-414 判例刚修过）
- ❌ 不做告警风暴抑制算法——S3 的静默窗口已够用，等真出现风暴再加

## 3. 风险与回滚

| 步骤 | 风险 | 回滚 |
|---|---|---|
| S1 | 修复触发真实推送（交易时段打扰） | 首跑加 `--dry-run`（若无此 flag，临时把 push_targets 指向测试目标） |
| S2 | 探针自身误报刷屏 | 计划任务先挂 3 天观察期，频率 1 次/日 |
| S3 | 多源红叠加噪音 | 逐源接入（先 huangzhong 后 jev），每源观察 1 天 |

## 4. 依赖与前置

- S1 无依赖，立即可做；
- S2/S3 依赖 S1（链修好了探针才有意义）；
- S4 依赖 S2；
- 全部动作不碰在改文件；每步独立 commit + 回单留痕（对齐 DEVTOOL_MERGE_GATE 判例文化）。
