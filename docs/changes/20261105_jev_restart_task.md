# JEV 重启通道（方案 A）落地留痕 — 2026-11-05

## 一、要解决的问题

JEV 三件套（9101 gate / 9102 review / 9103 qd-bridge）此前运行在 **session 0**，
用户态 `taskkill` 一律「拒绝访问」，导致：

- DSN 那类「必须换血」的改动无法落地（进程起得来、杀不掉）；
- 每次需要重启只能由不明身份的 session 0 拉起者或手工 ADMIN 会话完成，无留痕。

## 二、五要素

| 项 | 内容 |
|---|---|
| 命令全文 | `Register-ScheduledTask -TaskName JevRestart -Action (powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\aios\jev-eval\start_jev_stack.ps1" -Force) -Principal (用户 t / Interactive / Highest) -Settings (...)`；封装在 `tools/install_jev_restart_task.ps1`，支持 `-Verify` / `-Uninstall` / `-Elevate` |
| 影响面 | 新增一条**无触发器**计划任务；只在显式 `Start-ScheduledTask -TaskName JevRestart` 时执行，平时零开销、与 `JevStack`（10 分钟看护）职责不重叠 |
| 不可逆点 | 无数据不可逆。任务可由 `-Uninstall` 一条命令注销；`start_jev_stack.ps1` 的改动可用 `git revert` 撤销 |
| 替代方案 | B 提权取证找出 session 0 拉起者（依赖 4688 审计，系统级变更）；C 每次手工开 ADMIN 会话（人肉、无留痕） |
| 回滚预案 | `powershell -File tools\install_jev_restart_task.ps1 -Uninstall`（管理员）→ 任务注销，服务不受影响 |

## 三、设计取舍（为什么不是 SYSTEM）

| 选项 | 结论 |
|---|---|
| SYSTEM / Highest | **否**。`jev-gate.env.ps1` 走用户级 DPAPI，而明文回退文件 `jev-gate.env.plain.ps1` **当前不存在**；SYSTEM 下解密失败 → env 加载失败 → 重启必然 rc=1 |
| 用户 t / Interactive / Highest | **采用**。DPAPI 正常（实测无「回退明文」告警，`env_fallback.flag` 仍停在 10-16）；High IL 带 SeDebugPrivilege，可终止跨会话的同账号进程 |
| 挂触发器（定时） | 否。重启是显式动作，定时重启会与 JevStack 看护互相打架 |

## 四、配套代码修复（必须，否则方案 A 会打断飞书回调）

`jev-eval/start_jev_stack.ps1` 原第 175 行：

```powershell
if (($Force -or $StopRetired) -and (Test-Port 9104)) { ... }   # 旧
if ($StopRetired -and (Test-Port 9104)) { ... }                # 新
```

原逻辑意味着 `-Force` 会连带 `taskkill` 9104 的飞书卡片回调服务（文档却写着「-Force 不含 9104」）。
JevRestart 走的就是 `-Force`，不修就会每次重启都把「已处理/忽略」按钮回调打哑。已改为**只有显式 `-StopRetired` 才清 9104**。

## 五、落地实测（05:06:35，任务 Result=0）

| 端口 | 重启前 PID | 重启后 PID | 会话 |
|---|---|---|---|
| 9101 gate | 49568 | **60232** | 0 → **1** |
| 9102 review | 49384 | **54192** | 0 → **1** |
| 9103 qd-bridge | 48164 | **56460** | 0 → **1** |
| 9104 card-handler | 10708 | **10708（未动）** | 仍 0 |

日志链路（`jev-eval/logs/jev-stack.log`）：

```
[05:06:52] force-restart: killed 3 (49568,49384,48164)
[05:07:02] pulled up: jev-gate,review-gate,qd-bridge
[05:07:22] stack ok (3/3 listening)
```

新进程自检（`logs/gate.log`）：`DB 通道自检 channel=direct fallback_enabled=false healthy=true port=5446 pw_source=file`。
断档窗口约 10–30 秒（杀死→重新绑定端口），期间 JevStack 若恰好在跑会重复拉起，属幂等。

**副产物**：三件套已从 session 0 迁回 **session 1**，以后同账号提权会话即可直接管理，不必再依赖那个不明身份的拉起者。

## 六、日常用法

```powershell
# 重启三件套（普通用户即可发起，任务自身以 Highest 运行）
Start-ScheduledTask -TaskName 'JevRestart'
# 或（管理员 cmd）
schtasks /run /tn JevRestart

# 只查不跑
powershell -NoProfile -ExecutionPolicy Bypass -File C:\aios\tools\install_jev_restart_task.ps1 -Verify
```

## 七、已知风险

1. `AIOS-S4U-Guard`（SYSTEM，每 5 分钟）会尝试把 action 含 `aios` 的用户任务改成 S4U。
   若它成功改造 `JevRestart`，任务将落回 session 0 且 DPAPI 可能失败 —— `-Verify` 会打印
   `WARN 已被 AIOS-S4U-Guard 改成 S4U`，此时重跑安装脚本即可改回。
2. 任务为 `Interactive` 登录类型：**必须在用户 t 已登录时才能运行**（与 JevStack 一致）。
3. `-Force` 只重启三件套；9104 若也需要换血，须显式 `start_jev_stack.ps1 -StopRetired`。
