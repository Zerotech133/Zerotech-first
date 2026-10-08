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
