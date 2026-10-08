# 0x8007042B 五任务失败根因诊断报告（K268·掉电击杀链闭环）

- **日期**: 2026-10-08
- **结论**: 五任务 0x8007042B **全部同根因**——AC 掉电瞬间 Windows 计划任务默认电池策略批量终止运行中任务；非脚本故障、非病毒软件误杀
- **处置**: 对症调整已落地（电池策略关闭 + 告警掉电标注）；供电硬件排查待用户拍板

---

## 一、结论与因果链（实锤）

```
09:01:59  Kernel-Power EID=105  AcOnline=false        ← AC 掉电，切电池
09:01:59  TaskScheduler EID=201 ×3                    ← 同秒！CryptoSync /
          rc=2147943691 (0x8007050B)                    ChanlunScanUS /
          ERROR_PROCESS_ABORTED                         JEVBackfill-Hourly 被终止
09:02:52  Kernel-Power EID=566  Reason=15             ← 会话挂起
09:02:54  Kernel-Power EID=42   TargetState=4         ← 系统进入 S4 休眠
09:03:07  Kernel-Power EID=107  WakeFromState=4       ← 唤醒恢复（时钟回拨 4 秒校正）
```

**机制**: Windows 计划任务导出 XML 时默认 `DisallowStartIfOnBatteries=true` + `StopIfGoingOnBatteries=true`——掉电瞬间任务计划程序按策略终止**所有正在运行**的任务，与运行时长无关（CryptoSync 已跑 52 分钟 vs ChanlunScanUS 仅 1m58s，同秒被杀即证）。

**交叉验证（此前误判"除名"的两任务实为同案受害者）**:
| 任务 | 上次运行 | 结果 | 命中的掉电事件 |
|---|---|---|---|
| RtdxRtMinPull | 10-05 18:45:00 | 0x8007042B | **10-05 18:45:27 EID=105**（启动后 27 秒） |
| _sentinelEOD | 10-05 14:16:19 | 0x8007042B | **10-05 14:21:13 EID=105** → 14:21:28 休眠 |

**环境背景（重磅）**: 近 7 天 Kernel-Power 掉电事件**数十次**，其中 10-06 凌晨 04:46–05:51 一小时内掉电 10+ 次（每次 2–4 秒恢复，典型接触不良/供电不稳），S4 休眠-唤醒循环 15+ 次。**所有长跑任务（回测/拉数/同步）随时可能被打断，这是供电硬件问题的软件层投影。**

## 二、排凶记录（逐项状态）

| 嫌疑 | 状态 | 证据 |
|---|---|---|
| 脚本自身退出 | ❌ 排除 | err 日志 0 字节（启动即被外力终止非脚本退出）；三任务同秒被杀 |
| zhice watchdog | ❌ 排除 | 08:58 轮日志静默；Get-Procs 杀面（zhice[-_]os/jev_gate/...）不含目标进程 |
| DockerWatchdog v2.3 | ❌ 排除 | 源码（注册表解出）核验为 Enable-only，不杀进程 |
| **火绒** | ❌ **排除**（本事件） | 隔离库(applog/log.db/QuantineEx 只读副本)核验：09:01:59 时点**零记录**（最近为 08:34:25 与 09:40:06） |
| **Kernel-Power 掉电** | ✅ **实锤** | EID=105 与三任务 EID=201 同秒；电池策略机制吻合；历史掉电精确命中另两任务 |

## 三、Ollama 0xFFFFFFFF（独立旧账，已另案处理）

- 任务「AI工作站Ollama服务」LastRun=10-07 07:01 结果 0xFFFFFFFF——**与本次掉电事件无关**
- `serve_wrapper.py` 头注释显示 **K267（同日）已有专文**（docs/ops/2026-10-08_Ollama任务根因排查.md）并补日志落盘
- 当前实测：11434 LISTENING（ollama.exe PID 42732 以 **Services 会话**常驻，3 条 ESTABLISHED），任务电池策略已是 False，且带 RestartCount=3 自愈
- **状态: 无需重复处置**

## 四、已落地的调整

1. **五个受害任务电池策略关闭**（`Set-ScheduledTask -InputObject`，保留原 Principal/触发器，运行中实例不受影响；改前改后核验通过）:
   - CryptoSync / ChanlunScanUS / JEVBackfill-Hourly / RtdxRtMinPull / _sentinelEOD
   - `DisallowStartIfOnBatteries: True→False`，`StopIfGoingOnBatteries: True→False`
   - 语义: 掉电不再杀任务（机器电池常满 96–100%，纯策略误伤；真正掉电时任务随系统休眠挂起、唤醒后继续）
2. **alert_fail.py K268 掉电标注**: 发送前查 System 日志近 10 分钟 EID=105(AcOnline=false)，命中则在告警头加「[疑似掉电击杀·电池策略]」+ 佐证条数——不抑制告警只归类语义；查询失败兜底返回 0（诊断不反噬告警）。自测 PASS（600min 窗口命中今日 2 次掉电/10min=0）+ 既有 18 测试全 OK
3. **CryptoSync 环境复跑验证**: State=Running（09:57:22 触发）——当前环境可正常拉起并运行任务

## 五、待用户拍板

1. **⚠️ 供电硬件排查（根本解）**: 7 天数十次掉电+休眠循环，软件层只能止损——建议查电源线/插座/UPS；10-06 凌晨的密集 2–4 秒抖动是接触不良的典型形态
2. **休眠触发源未定位**: 睡眠/休眠超时均为「从不」(0x0)，但 09:01:59 掉电后 55 秒即进 S4（EID=42 Reason=0）——疑电源键配置/厂商工具/BIOS「AC 恢复」行为，建议核对电源按钮动作与 BIOS AC Power Loss Recovery 设置
3. **火绒反复隔离 backup_daily.ps1（独立活跃问题）**: 今日隔离库新增 14 条（00:23–09:42），全部是 `E:\老田的AI助手\jev-eval\backup_daily.ps1` 及其 C:\Temp 变体（bd_syntax_check.ps1 / backup_daily_v8.ps1 / backup_v6_decode.ps1）——**jev-eval 备份链今天很可能一次都没跑成**。选项: 火绒加白该路径 / 换非 PowerShell 实现 / 与火绒申诉误报
4. 孤儿/退役任务清理（前次审计衍生: 6 孤儿候选 + 2 脚本缺失任务）仍待逐个拍板

## 六、取证工件

- `C:\Temp\hr_copy\`（火绒库只读副本×6）、`C:\Temp\hr_dig_out.txt`（隔离/主日志/托盘消息全量）
- `C:\Temp\kill_evt.txt`（击杀时点 System + TaskScheduler 事件）、`C:\Temp\power_history.txt`（近 7 天掉电史）
- `C:\Temp\battery_check.txt` / `battery_fix.txt`（电池策略改前改后）、`C:\Temp\ollama_check.txt`
- `C:\Temp\power_deep.txt` / `power_deep2.txt`（深钻补遗：电源配置/唤醒源/掉电统计/电池详情）、`C:\Temp\battery_report.html`

## 七、深钻补遗（同日第二轮取证）

### 7.1 两处修正

1. **S4→S3**：`powercfg /a` 证实本机**休眠未启用**（仅 S3 待机可用）。SYSTEM_POWER_STATE 枚举中 4=Sleeping3，此前 TargetState=4 读作「S4 休眠」有误——实为 **S3 挂起内存**。结论不受影响（任务在掉电秒已被电池策略终止，睡眠只是后续）。
2. **休眠触发源已定位**：EID=42 文本描述明确「睡眠原因: **Button or Lid**」（电源键或盖子）。10-07 22:48 同为 Button or Lid。**排除**联想电源管家自动休眠（本机仅 LenovoFnAndFunctionKeys 服务）与系统空闲（超时均为「从不」）。

### 7.2 掉电模式统计（230 次配对事件，窗口 03-27 ~ 10-08）

- **两类事件混存**：
  - **A 类·微抖动**：47 次 <10 秒（含 9-09~9-14 一批 1-5 秒抖动）——电源口/线材**接触不良**特征
  - **B 类·人为模式**：掉电后 55 秒~10 分钟内 Button or Lid 睡眠（10-07 22:38 掉电→22:48 合盖；10-08 09:01 掉电→09:02 合盖）——拔电移动/收工场景
- **模式突变点 9 月初**：3-8 月为偶发长断电（关机/搬动级别）；**9-01 起出现秒级微抖动且密度持续上升**（9-30 12 次、近三天每天 5-9 次）——渐进恶化轨迹
- 按小时分布全天分散（9 时最高 25 次，但凌晨亦有）——排除「定时开关机」类
- 电池 L24B4PC0（17.3V）状态 OK 96%，掉电时满电——排除电池耗尽

### 7.3 硬件排查指导（按嫌疑度排序，须人工执行）

| # | 检查点 | 依据 | 动作 |
|---|--------|------|------|
| 1 | **DC 电源口（机身侧）** | 秒级微抖动 47 次=插头接触瞬断 | 目视氧化/松动，插紧后轻摇线材看充电图标是否闪断；氧化用电子清洁剂 |
| 2 | **适配器线缆两端接头** | 弯折疲劳高发区 | 弯折移动线身各段，复现掉电即坏线；备件替换验证 |
| 3 | **排插/墙插** | 多机共插时劣化常见 | 换独立墙面插座对照一周（看 105 事件是否归零） |
| 4 | **Button or Lid 事件是否本人** | 10-08 09:02 / 10-07 22:48 两次 | 若非您合盖/按键：查盖子磁传感器（屏轴附近）与电源键是否卡滞 |
| 5 | BIOS「AC Recovery」设置 | 09:28:08 插回电源即自动唤醒 | 若需插电即自启保持开；反之关（行为正常，仅确认口径） |
| 6 | 电池健康深查 | `C:\Temp\battery_report.html` 已生成 | 核对 DESIGN_CAPACITY vs FULL_CHARGE_CAPACITY 衰减度 |

**验证口径**：处置后观察 System 日志 EID=105 频次——目标是 A 类微抖动归零；B 类人为模式属正常作息。

## 八、孤儿/退役任务深度核实结论（同日第三轮）

浅扫 6 孤儿候选**经 git 史/代码引用/README 核实后全部为 false positive**（浅扫只查计划任务注册表，未查代码引用与提交史）：

| 脚本 | 浅扫定性 | 深度核实定性 | 处置 |
|------|----------|--------------|------|
| portal/register_feishu_watchdog_task.py | 孤儿 | **保留**——10-07 c623abb 刚落地（飞书 SYSTEM 看门狗注册器），注册器本就一次性运行 | 无需动作 |
| portal/start_portal_nginx.ps1 | 疑旧启动残留 | **保留**——10-07 2fcd047 刚版本化的 ai-portal 容器启动脚本（非旧方式） | 无需动作 |
| portal/sync_publish.py | 孤儿 | **保留**——10-08 19c4e47 刚交付（X5 快照刷新，580 文件 0.6s 幂等实测） | 无需动作 |
| quant_backtest/k266_clip_watch.py | K266 已缓 | **保留**——K266 M2 三入口之一，工具链完整（缓行≠废弃） | 无需动作 |
| quant_backtest/test_weak_watch.py | test_ 前缀 | **保留**——测试资产（项目测试纪律），不调度属正常形态 | 无需动作 |
| quant_backtest/weak_track_watch.py | 孤儿 | **保留**——README:69 文档化的手动运维工具 | 无需动作 |

**真需处置的仅 2 个脚本缺失任务条目**（注册表指向已不存在的文件，均 Disabled）：

| 任务 | 缺失证据 | 现状 | 建议 |
|------|----------|------|------|
| OpenClaw Gateway | gateway.cmd 不存在（.openclaw 目录尚在）；无进程、8787 无监听 | Disabled | 导出 XML 留档 deliverables 后删除条目；OpenClaw 若重部署会注册新任务，残条目无复用价值 |
| 量化前戏-FastAPI-Server | 整个老工作目录 WorkBuddy\20260324114946 已不存在（quant-foreplay 已清理） | Disabled | 同上 |

> 注册表条目删除不可逆（无回收站），故以「XML 导出留档 → 确认 → 删除」等效回收站流程执行，**待用户拍板**。

### 8.1 拍板记录（2026-10-08）

- **两个缺失任务条目：用户拍板「两个都删」** → 已执行：XML 导出留档 `deliverables/removed-tasks-backup-2026-10-08/`（openclaw-gateway.xml / quant-foreplay-fastapi-server.xml）→ Unregister → 逐个复核 GONE ✅
- **六个孤儿脚本：用户拍板「确认全部保留、零删除」** → 审计浅扫定性就此修正，后续不再列为清理候选
