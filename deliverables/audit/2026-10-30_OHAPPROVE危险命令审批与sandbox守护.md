# OH 危险命令审批 + sandbox 守护

- 日期：2026-10-30
- 关联：K-QUANT-20261030-OHLIVE（后续）
- 状态：**已完成并实测**

## 1. 危险命令逐条审批

采用 OpenHands 原生：

```text
confirmation_policy = AlwaysConfirm
```

适配器 `status()` 分流：

| 动作 | 策略 |
|---|---|
| `file_editor` / `task_tracker` | 自动批准 |
| 只读 shell（cat/ls/pwd/git status…） | 自动批准 |
| 危险 shell（rm/mv/curl/git commit…） | 停在 `waiting_for_confirmation` |

前端出现：

```text
⚠ 等待人工确认：TerminalAction: rm -rf /tmp/...
[批准执行] [拒绝]
```

接口：

```text
POST /api/ide/openhands/respond
{ conversation_id, accept, reason }
```

## 2. sandbox 自启/健康守护

- 容器重启策略：`unless-stopped`
- `start-sandbox.ps1` 新建容器时带 `--restart unless-stopped`
- 网关内 watchdog：
  - 60s 检查一次 `/health`
  - 容器掉线自动 `docker start` / `docker run`
  - 首次 health/start 自动启动线程

## 3. 实测

### 审批

```text
文件编辑 Create APPROVAL_SAFE_OK.txt
→ 自动批准 → 完成

rm -rf /tmp/oh-danger-approval-test
→ waiting_for_confirmation
→ POST /respond accept=true
→ 完成

浏览器：
等待确认 True / 批准按钮 1
点批准 → ✅ OpenHands 完成 · 13.7s
```

### 自愈

```text
docker stop legion-openhands-sandbox
→ ensure_sandbox(started=True)
→ /health 200
```

## 4. 仍未收口：8796

8796 仍被 Session0 旧进程占用（pid 29228），当前 OH API 继续走 8799 过渡网关。

需要在管理员终端执行：

```powershell
Stop-Process -Id 29228 -Force
powershell -ExecutionPolicy Bypass -File C:\aios\ops\start_legion_ops.ps1
```

然后：

```text
nginx /openhands/ 从 8799 改回 8796 → 退役 8799
```

## 验收

| 项目 | 状态 |
|---|---|
| 文件编辑自动批准 | ✅ |
| 只读 shell 自动批准 | ✅ |
| 危险 shell 等待确认 | ✅ |
| 前端批准按钮 | ✅ |
| sandbox 掉线自愈 | ✅ |
| 容器自启策略 | ✅ unless-stopped |
