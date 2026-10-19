---
name: windows-portproxy-troubleshoot
description: Troubleshoot and restore Windows netsh portproxy forwarding when a local port does not listen. Use when the user asks why a port is not listening, cannot connect to a port, or explicitly mentions portproxy. Do not use for Linux iptables or WSL-only forwarding.
---

# Windows Portproxy Troubleshoot

**Scope** — this skill covers **Windows `netsh interface portproxy` v4tov4` 转发**（由 IP Helper 服务驱动的本地端口转发）。典型场景：正式入口端口（如 8734）不监听，需要挂到后端服务（如 nginx 8080）。不覆盖 WSL / Docker / iptables / 其他平台。

**不做** — 不重启后端服务，不修改 nginx.conf / 应用代码，不把 portproxy 规则写入 git（存于注册表，非文件），不自行执行破坏性 stop-process。

## 三步收敛

每一步都必须有**独立的客观证据**，才能进入下一步。禁止把"没输出"直接等同于"空表"——netsh 空表会返回表头+空行，而 iphlpsvc 未启动时会报"请求的操作已成功完成"但后续 connect 失败；必须读回规则表才确认。

### Step 1 — 拿到证据

依次跑下面三条，输出**原样抄回**给用户，不要只给结论。

```powershell
# 1. IP Helper 服务状态（portproxy 的驱动，挂掉规则也不会生效）
Get-Service iphlpsvc | Select-Object Name,Status,StartType

# 2. portproxy 规则表（空表会打印表头但无规则行；iphlpsvc 未启动会直接报错）
netsh interface portproxy show all

# 3. 目标端口和后端端口的真实监听（排除 TIME_WAIT 和 OwningProcess=0 的假阳性）
Get-NetTCPConnection -LocalPort 8734,8080 -State Listen |
    Select-Object LocalAddress,LocalPort,State,OwningProcess
```

### Step 2 — 归类

| 观察 | 根因 | 修复 |
|------|------|------|
| iphlpsvc `Stopped` 或 `Manual` | 服务未启动 | `Start-Service iphlpsvc` + `Set-Service iphlpsvc -StartupType Automatic` |
| 规则表空、目标端口无监听、后端端口正常监听 | 规则未建立 | 见 Step 3 |
| 规则存在、目标端口有监听、HTTP 仍失败 | 后端服务挂了 | 走后端排障，不要碰 portproxy |
| 规则存在、目标端口监听 PID 异常（0 或空） | 僵尸进程 | 先确认是否 TIME_WAIT 后 `State=Listen` 再决定 |
| `netsh` 直接报 "请求的操作需要提升" | 当前终端非管理员 | 用 `Start-Process -Verb RunAs` 拉起管理员 PowerShell，把命令发过去执行 |

### Step 3 — 建立 / 更新规则（必须在管理员 PowerShell）

**只读非管理员终端**：先 `Start-Process powershell -Verb RunAs`，让用户确认 UAC。把下面命令原样丢到管理员窗口：

```powershell
# 建立/覆盖规则（先删后加保证幂等）
netsh interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=<LISTEN_PORT>
netsh interface portproxy add    v4tov4 listenaddress=127.0.0.1 listenport=<LISTEN_PORT> connectaddress=127.0.0.1 connectport=<CONNECT_PORT>
```

`<LISTEN_PORT>` 是正式入口端口（如 8734），`<CONNECT_PORT>` 是后端服务端口（如 8080）。**只绑定 127.0.0.1**，不要用 `0.0.0.0` 暴露给外网。

### 三步验证（每步都要客观输出）

```powershell
# V1 规则在注册表
netsh interface portproxy show all

# V2 端口监听 + PID 非 0
Get-NetTCPConnection -LocalPort <LISTEN_PORT> -State Listen |
    Select-Object LocalAddress,LocalPort,State,OwningProcess

# V3 HTTP 可达
try {
    $r = Invoke-WebRequest "http://127.0.0.1:<LISTEN_PORT>/<HEALTH_PATH>" -UseBasicParsing -TimeoutSec 5
    "HTTP $([int]$r.StatusCode)"
} catch {
    "HTTP FAIL: $($_.Exception.Message)"
}
```

## 持久化说明

portproxy 规则存于 `HKLM\SYSTEM\CurrentControlSet\Services\PortProxy\v4tov4\tcp`，**系统重启自动保留**。前提：

- iphlpsvc 启动类型必须是 `Automatic`（默认）
- 不要用 `netsh interface portproxy reset` 清表

## 反模式

- ❌ 直接宣称"端口已打开"却不附 `Get-NetTCPConnection` 输出
- ❌ 把 POWERHELL `curl` 别名（Invoke-WebRequest）当 curl 用，参数不兼容会静默失败
- ❌ Stop-Process 时 OwningProcess=0 就直接 kill，这是 Idle/ TIME_WAIT 不是真进程
- ❌ 一次命令里同时查询 + kill + 重启，耦合太深失败面大；拆开
- ❌ 非管理员终端直接跑 `netsh portproxy add`，必然触发 UAC 拒绝
- ❌ 把端口问题归因为后端服务（如 nginx）却不先验证 portproxy 规则表
- ❌ 只给 HTTP 200 就宣称"正式入口验收通过"——还得验页面动线
- ❌ **PS 5 .ps1 文件 BOM 管理**：Trae Write 工具会剥 UTF-8 BOM，VS Code/IDE 的重新保存又可能把 BOM 叠回去，最终出现 `EF BB BF EF BB BF`（双 BOM）。PS 5 解析时第二个 BOM 字节污染 `param(` 起始，报 `assignment expression is not valid`。**任何生成或修改的 .ps1 必须先 `powershell -File xxx.ps1` 跑一遍确认 EXIT=0**，不要盲信文件写成功。若触发叠 BOM：读出字节 → strip 多余 BOM → `[IO.File]::WriteAllText($path, $content, [Text.UTF8Encoding]::new($true))` 重写单 BOM

## 参考命令（scripts/portproxy_probe.ps1）

一个打包好的探针脚本，只读，不触发 UAC。输出分四块：iphlpsvc / portproxy / 监听 / HTTP。运行：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/portproxy_probe.ps1 -Listen 8734 -Connect 8080
```

如果脚本不存在或路径不对，手工跑上面的 Step 1 三条命令即可。
