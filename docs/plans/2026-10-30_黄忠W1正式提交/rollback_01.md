# 回滚点 01：ZHAOYUN 会议绑定覆盖回滚（MEETING_BINDING_OVERRIDES）
> STATUS: frozen（黄忠验收夹具/样例文件，非计划；2026-10-31 W2 收口）



> 回滚对象：`agent_platform/legion_command/serve.py` 的 `MEETING_BINDING_OVERRIDES = {"ZHAOYUN": "ollama/qwen3.5:9b"}` 及配套 `resolve_meeting_binding()`、`/api/chat` 的 `purpose=meeting` 覆盖分支
> 引入提交：289c742a0（WO-HQ-P2B-20261029，2026-10-29 07:33；合并 bb6e617f3）；代码位置：`serve.py:118-128`、`:2049-2052`、`:2163-2166`
> 校验：`git merge-base --is-ancestor 289c742a0 HEAD` → 退出码 0（2026-10-30 实测）；`test_hq_p2.py:54-60` 覆盖该行为（5 passed）

## 第一步：备份（或 git revert）

```powershell
# 1) 文件级备份（推荐：只回滚 override，不动 P2 看板）
Copy-Item C:\项目\老田的AI助手\agent_platform\legion_command\serve.py `
  C:\项目\_vault\backups\legion_serve.py.before-meeting-binding-rollback-20261030 -Force
# 备份存在性证据（回滚前必查）
Test-Path C:\项目\_vault\backups\legion_serve.py.before-meeting-binding-rollback-20261030   # 期望 True
# 2) 记录回滚前提交，便于事后对照
git -C C:\项目\老田的AI助手 rev-parse HEAD

# 备选：git revert 全量回滚 P2（不推荐——会连带删除 hq_stats 会议看板/meeting.html 面板）
# git -C C:\项目\老田的AI助手 revert --no-edit 289c742a0
```

## 第二步：删除 override（定点补丁）

```powershell
cd C:\项目\老田的AI助手
# 2.1 删除覆盖表与解析函数（serve.py:118-128 整块：MEETING_BINDING_OVERRIDES + resolve_meeting_binding）
# 2.2 /api/chat（serve.py:2049-2052）把三行覆盖逻辑改回单行：
#     purpose = str(req.get("purpose", "")).strip()
#     binding_orig = resolve_binding(agent)
#     meeting_override = purpose == "meeting" and agent in MEETING_BINDING_OVERRIDES
#     binding = (resolve_meeting_binding(agent) if meeting_override else binding_orig)
#     改回 ->
#     binding = resolve_binding(agent)
# 2.3 删除响应里的覆盖标记（serve.py:2163-2166）：
#     if meeting_override:
#         env["meeting_override"] = True
#         env["binding_original"] = binding_orig

# 断言：符号已消失（命中数必须为 0）
Select-String -Path agent_platform\legion_command\serve.py -Pattern "MEETING_BINDING_OVERRIDES|resolve_meeting_binding" | Measure-Object | Select-Object -ExpandProperty Count
# 期望输出：0
git diff --stat -- agent_platform/legion_command/serve.py   # 期望仅该文件、行数约 -14/+1
```

## 第三步：重启 8796 使新代码生效

> 红线：8796 重启属 L2 动作，**执行前须按五要素取得老田明确批准**（命令全文/影响面/不可逆点/替代方案/回滚）；本文件只是回滚操作单，本单未预执行。
> 机制证据：`ops/start_legion_ops.ps1:37-47` 消费 `C:\Temp\LEGION_8796_RESTART.flag`（杀旧 pid → 2s 后 `Ensure 8796 agent_platform\legion_command\serve.py` 重新拉起）；任务名 `LegionOps`（`Start-ScheduledTask -TaskName LegionOps`）。

```powershell
# 3.1 记录旧 pid（只信端口判活：S4U/Session0 进程枚举不可靠）
$oldPid = (Get-NetTCPConnection -LocalPort 8796 -State Listen -ErrorAction SilentlyContinue |
           Select-Object -First 1).OwningProcess
# 2026-10-30 实测监听在 127.0.0.1:8796，GET /v1/health → 200 {"ok": true, "service": "dsh-llm-gateway"}

# 3.2 一次性 sentinel 重启
New-Item -ItemType File -Path C:\Temp\LEGION_8796_RESTART.flag -Force | Out-Null
Start-ScheduledTask -TaskName LegionOps

# 3.3 等 health 200 且新 pid 出现（窗口 5–20s）
for ($i = 0; $i -lt 30; $i++) {
  try { $r = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8796/v1/health' -TimeoutSec 3
        if ($r.StatusCode -eq 200) { break } } catch {}
  Start-Sleep -Seconds 1
}
$newPid = (Get-NetTCPConnection -LocalPort 8796 -State Listen -ErrorAction SilentlyContinue |
           Select-Object -First 1).OwningProcess
# 通过线：health 200 且 $newPid 非空；日志 C:\Temp\legion_command_8796.log 有新进程特征行
```

## 第四步：验证（预演已实测，线上步骤同口径）

```powershell
# 4.1 线上验证：purpose=meeting 的 binding 必须等于 resolve_binding 原值，且覆盖标记消失
$body = '{"agent":"ZHAOYUN","purpose":"meeting","user":"rollback-verify"}'
$resp = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8796/api/chat' `
  -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 180
# 通过线（三条全部满足才判回滚成功）：
#   resp.binding           == 原 resolve_binding("ZHAOYUN") 的值
#   $null -eq resp.meeting_override   （字段必须不存在）
#   $null -eq resp.binding_original   （字段必须不存在）
# 对照组：agent=A0、purpose=meeting → binding=glm/glm-5.3，且无 meeting_override（覆盖表外席位不受影响）

# 4.2 单测回归（覆盖用例随功能移除）
C:\项目\py312\python.exe -X utf8 -m pytest -q -p no:cacheprovider -o addopts= `
  agent_platform/tests/test_hq_p2.py -k "not meeting_binding_override"
# 通过线：4 passed / 0 failed（改动前 5 passed，含被移除的覆盖用例）
```

**2026-10-30 预演实测（本地 `build_server(port=0)` + LLM 打桩，未动线上 8796）**：现状 `purpose=meeting` 返回 `binding=ollama/qwen3.5:9b`、`meeting_override=True`、`binding_original=ollama/qwen3.5:9b`；把覆盖表置空模拟「已删 override」后返回 `binding=ollama/qwen3.5:9b`、`meeting_override` 缺省、`binding_original` 缺省，`binding == resolve_binding("ZHAOYUN")` 断言 True；对照组 A0 `binding=glm/glm-5.3` 且无覆盖标记。原始输出：`C:\项目\deliverables\2026-10-30_黄忠W1正式提交_证据\rollback01_drill.txt`。

## 不可逆点与已知风险

- 代码层可逆：补丁可用备份恢复或再 `git revert` 回滚提交；无数据变更，8796 重启窗口 5–20 秒内 `/api/chat`、`/api/zhaoyun*` 短暂不可用。
- 口径风险（实测发现）：ZHAOYUN 不在 `org.json` 的 62 席 `agent_profiles` 内，`resolve_binding("ZHAOYUN")` 回落到平台默认 `ollama/qwen3.5:9b`，与 `serve.py:118` 注释所称「正式绑定=DSH 会话载体 deepseek-v4-pro」存在落差；因此**删 override 后 binding 值本身不变**，验证必须以 `meeting_override`/`binding_original` 字段消失 + 单测口径为准，不能只看 binding 字符串。
