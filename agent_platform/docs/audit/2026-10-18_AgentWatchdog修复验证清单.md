# AgentWatchdog 修复验证清单（管理员跑完 fix_agentwatchdog.ps1 后照单验收）

- 日期：2026-10-18
- 作者：dev-watchdog-verify（Q 路开发代理）
- 性质：**验收用只读文档**。本文所有路径 / 任务名 / 日志落点均从代码与系统实况取证（取证方式见 §5），未凭记忆书写。
- fix 脚本位置（实际取证）：
  `C:\项目\zhice-os-v2-windows-20260920\code\src\zhice_os_v2\agent_platform\deploy\fix_agentwatchdog.ps1`
- 用法（fix 脚本头注释原文）：管理员 PowerShell 里
  `powershell -ExecutionPolicy Bypass -File fix_agentwatchdog.ps1`

---

## ① 缺陷链与修复三步对照表

| # | 根因 | 表现 | 修复动作（已就绪，待管理员执行） | 对应验证项 |
|---|------|------|----------------------------------|-----------|
| R1 | 旧任务 Arguments 被 `schtasks /TR` 引号嵌套截断为 `-File \`，且 WorkingDirectory 指向不存在的 `%LOCALAPPDATA%\Programs\agent_platform\` 目录 | 计划任务每 5min 触发即报 **0x8007010B**（目录名无效），监控自身已死 | fix 脚本用 `agentwatchdog_fixed.xml` 重注册（Arguments 全路径 `-File` + WorkingDirectory 指到真实目录），并前置自检补拷 `start_agent_platform.ps1` 到该目录（fix 脚本 L25-33） | V1 / V2 / V3 |
| R2 | 旧任务 `AgentWatchdog` ACL=t 只读（管理员提升上下文所建），非管理员无法同名覆盖 | 只能先以新名 `AgentWatchdog2` 注册同 XML 应急（2026-10-18 已落地，LastTaskResult=0x0） | 管理员跑 fix 脚本后**同名覆盖旧 AgentWatchdog**（`Register-ScheduledTask -Force`，fix 脚本 L33），覆盖后与 2 号并存、幂等互不冲突（fix 脚本头注释 L13-14） | V6 |
| R3 | 宿主/父进程注入小写 `http_proxy`，PS5.1 `Start-Process` 内部环境字典按大写规范名预置后加小写键 → 「已添加项。字典中的关键字:HTTP_PROXY」异常 | daemon 拉起全灭，且脚本 exit 0 掩盖（假绿） | `start_agent_platform.ps1` 开头**代理变量归一**：删掉 http_proxy/HTTP_PROXY/https_proxy/HTTPS_PROXY 全部大小写变体，以大写规范名设回（start 脚本 L8-19） | V4（watch.err 不再出现该异常） |
| R4 | 拉起失败后 `exit 0`，LastTaskResult 恒 0x0，失败不可见 | 任务"成功"但 daemon 全灭 | `Start-Process -PassThru` 判定，failed 非空时写 `FAILED` 行到 aw-stack.log 并 **exit 1**（start 脚本 L36-38、L50-54） | V3 / V4 |

补充背景：R1 的历史证据链——`-File \` 截断 + CWD 指到文件（非目录）曾致每 5min 0x8007010B。

---

## ② 验证步骤清单（管理员跑完 fix 脚本后逐条执行）

| 项 | 验证内容 | 通过判据 |
|----|---------|---------|
| V1 | fix 脚本自身输出 | 输出含 `Task State: Ready`、`LastTaskResult: 0x0`、`Arguments` 为全路径、`WorkingDir` 为 `C:\Users\t\AppData\Local\Programs\agent_platform`、`daemon 进程: 2 个` |
| V2 | 任务注册存在性 + 触发器 | `AgentWatchdog` 存在且 XML 参数为修复版；Repetition `<Interval>PT5M</Interval>` |
| V3 | LastTaskResult = 0x0 | `Get-ScheduledTaskInfo` 两个任务均 0x0；等待 ≥10min（两个调度周期）后仍 0x0 |
| V4 | 留痕日志字节增长 | `code\logs\aw-stack.log` 在调度周期内新增 `pulled up:` 行（daemon 已在跑时无新行也正常，判据改看 watch.err 持续追加、无 FAILED 行）；`aw-proc.watch.err` 字节持续增长 |
| V5 | aw_decision PG 写入恢复 | PG `aw_decision` 表 `count(*)` 增长且 `max(created_at)` 为最近时间；watch.err 无 `aw_decision 写库失败，降级 JSONL` |
| V6 | 新旧任务关系 | 旧 `AgentWatchdog` 已被同名覆盖为修复版 XML（与 `AgentWatchdog2` 同参数并存，两者幂等互不冲突：任一触发都只补缺不重启已在跑的 daemon） |
| V7 | daemon 进程 | `watch-daemon` + `ack-serve` 两个 python 进程在跑（ack-serve 监听 :9105） |

---

## ③ 每步具体命令（一条龙，非管理员可直接跑）

### V1 fix 脚本自检输出
管理员跑完脚本时输出即验收凭证；若需重看关键项：

```powershell
Get-ScheduledTask -TaskName AgentWatchdog | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName AgentWatchdog |
  Select-Object LastRunTime, LastTaskResult
(Get-ScheduledTask -TaskName AgentWatchdog).Actions |
  Select-Object Execute, Arguments, WorkingDirectory
```

### V2 任务注册存在性 + 触发器（python 直读 Tasks XML，encoding='utf-16'）

```python
import os
for name in ("AgentWatchdog", "AgentWatchdog2"):
    p = os.path.join(r"C:\Windows\System32\Tasks", name)
    with open(p, encoding="utf-16") as f:
        xml = f.read()
    print(name, "存在" if xml else "空")
    print("PT5M 触发器:", "<Interval>PT5M</Interval>" in xml)
    print("全路径参数:", 'start_agent_platform.ps1' in xml and '-File "C:' in xml)
```

注：`C:\Windows\System32\Tasks` 目录本身枚举需管理员，但**已知文件名直读**普通权限即可（本次取证即此法成功）。Tasks XML 不存 LastTaskResult，该值必须走 V3 的运行时查询。

### V3 LastTaskResult（等待 ≥10min 两个调度周期后复查）

```powershell
schtasks /Query /TN AgentWatchdog  /V /FO LIST | findstr /C:"结果" /C:"Last Result"
schtasks /Query /TN AgentWatchdog2 /V /FO LIST | findstr /C:"结果" /C:"Last Result"
```

### V4 留痕日志字节增长

```bash
# 日志落点（fix 链实际路径）：C:\项目\zhice-os-v2-windows-20260920\code\logs\
ls -l "/c/项目/zhice-os-v2-windows-20260920/code/logs/" | grep -i aw
# 基线记下 aw-stack.log / aw-proc.watch.err 字节数，等 ≥10min 再查一次
tail -5 "/c/项目/zhice-os-v2-windows-20260920/code/logs/aw-stack.log"
# 失败信号排查（应无输出）：
grep -c "FAILED" "/c/项目/zhice-os-v2-windows-20260920/code/logs/aw-stack.log"
grep -c "字典中的关键字" "/c/项目/zhice-os-v2-windows-20260920/code/logs/aw-proc.watch.err"
```

### V5 aw_decision PG 写入恢复（程序化取连接串，不落密码）

```python
# 在 code 目录下跑（C:\项目\zhice-os-v2-windows-20260920\code），
# PYTHONPATH 指向 src，连接串经 AgentPlatformConfig 读取（localhost:5446，本次取证实见）
import subprocess, sys
code = r"""
from sqlalchemy import create_engine, text
from zhice_os_v2.agent_platform.config import AgentPlatformConfig
e = create_engine(AgentPlatformConfig().db_url)
with e.connect() as c:
    n, latest = c.execute(text(
        "SELECT count(*), max(created_at) FROM aw_decision")).one()
    print("aw_decision rows =", n, "| latest =", latest)
"""
subprocess.run([r"C:\项目\py312\python.exe", "-c", code],
               cwd=r"C:\项目\zhice-os-v2-windows-20260920\code",
               env={"PYTHONPATH": r"C:\项目\zhice-os-v2-windows-20260920\code\src",
                    "PATH": r"C:\项目\py312;C:\Windows\System32"})
# 判据：rows 较上次增长 且 latest 为最近几分钟；
# 同时 watch.err 无 "aw_decision 写库失败，降级 JSONL"（daemon 端 batch_size=1，
# 写库失败仅 logger.warning 到 aw-proc.watch.err，不落 JSONL 文件）
```

### V7 daemon 进程

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'agent_platform\.cli (watch-daemon|ack-serve)' } |
  Select-Object ProcessId, CommandLine
# 预期 2 个：watch-daemon --market CRYPTO + ack-serve（:9105）
```

---

## ④ 回滚 / 二次失败处置

fix 脚本无退出码语义（`$ErrorActionPreference='Continue'`，最后一段输出 daemon 计数但不 exit 1）——**判定失败以输出与 V1-V7 为准，勿以脚本退出码为准**。

| 信号 | 处置 |
|------|------|
| fix 输出 `!! daemon 未齐` | 按提示手动跑 `%LOCALAPPDATA%\Programs\agent_platform\start_agent_platform.ps1`；仍失败看 `%TEMP%` 下输出与 `code\logs\aw-proc.*.err` |
| LastTaskResult 非 0x0 | 0x8007010B=WorkingDirectory 又坏（查 V2 XML）；0x80070005=ACL 权限；其他码对照 `schtasks` 文档；先看 `Get-ScheduledTaskInfo` 的 LastRunTime 是否在跑 |
| aw-stack.log 出现 `FAILED:` 行 | start 脚本已 exit 1（R4 生效），LastTaskResult 会非 0；看 aw-proc.watch.err 首屏报错（PG 连不上 → 查 5446 端口 postgres；代理字典炸 → 查 R3 归一段是否被改动） |
| `too many clients already` | PG 连接池耗尽（本次取证 14:02 实见），非本修复范畴；处置：重启 PG 或杀多余 python 客户端后观察 aw_decision 写入恢复 |
| 需回滚 | 停进程即可：`Get-AwProcs` 同款过滤后 `Stop-Process`；`schtasks /Change /TN AgentWatchdog /DISABLE`（需管理员）。aw_* 表留档不删（db.py 头注释约定：回滚=停进程） |

---

## ⑤ 当前状态基线（2026-10-18 取证实况快照）

1. **文件**：`deploy\` 下三件套齐全——`fix_agentwatchdog.ps1`、`agentwatchdog_fixed.xml`、`start_agent_platform.ps1`（zhice-os-v2 仓，只读取证）。
2. **计划任务**（python utf-16 直读 Tasks XML 成功）：
   - `AgentWatchdog` **存在**，XML 参数已是修复版（全路径 `-File "C:\Users\t\AppData\Local\Programs\agent_platform\start_agent_platform.ps1"` + WorkingDirectory 同目录 + PT5M）；
   - `AgentWatchdog2` **存在**，同参数（URI `\AgentWatchdog2`）；
   - 两者 Principal 均 `S-1-5-21-1304855347-670822282-137719513-1001`，InteractiveToken。
   - ⚠️ 与工作记忆「旧任务 Arguments 截断为 `-File \`」存在出入：**磁盘现状旧任务参数也已为修复版**（可能管理员已覆盖或曾被修复）。管理员的 fix 重跑为幂等覆盖，无副作用；LastTaskResult 运行时值留待 V3 复核。
3. **脚本副本**：`C:\Users\t\AppData\Local\Programs\agent_platform\start_agent_platform.ps1` 在位（fix 前置自检 L26 的依赖已满足）。
4. **留痕日志**（`code\logs\`）：`aw-stack.log` 2 条 `pulled up: watch-daemon,ack-serve`（07:23:37 / 07:24:05，与工作记忆 [2026-10-18 07:24:05] 留痕一致）；`aw-proc.watch.err` 20,873,193 字节且持续增长（13:59 仍在写）→ watch-daemon 活；`aw-proc.ack.err` 显示 ack server listening on :9105（07:24:07）。
5. **异常实见**：`aw-proc.watch.err` 尾部（14:02:39）`data_provider 取数失败 ZEC/USDT: connection to server at "localhost" (::1), port 5446 failed: FATAL: sorry, too many clients already`——PG 连接耗尽在身，**可能拖累 V5 判据**，验收时先处置再判。
6. **落库链路**：daemon `DecisionRecorder(batch_size=1)` 直写 PG `aw_decision`（models.py:78）；写库失败仅 logger.warning 降级（daemon 未传 jsonl_fallback，无 JSONL 文件可查），故 V5 以 PG count + watch.err 无 warning 双判。

---

> **本清单为验收用只读文档**。执行修复仍待老田以管理员身份运行 `fix_agentwatchdog.ps1`；本文档自身不含任何写操作，验证命令亦全部为只读（Tasks XML 直读 / 日志 tail / PG SELECT / 进程枚举）。
