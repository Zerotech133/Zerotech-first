# LDE-NATIVE：Hermes 原生施工台 v1

- 日期：2026-10-30
- 工单：`K-QUANT-20261030-LDE-NATIVE`
- 状态：**P0 已完成并实测**

## 1. 目标

把 8103 `/rw` 的 LDE 施工能力原生化进 Hermes 扩展，不再跳旧页面/iframe：

- 作用域选择
- 工单锁 acquire / release
- 文件读取
- 写预览 → diff → 确认写
- 白名单 exec
- code_action / code_observation 审计

## 2. 接入

```text
8734 /lde/  → nginx 同源代理 → 127.0.0.1:8103
```

- `GET /lde/rw` 负责种 `ide_session` HttpOnly cookie
- Hermes 扩展新增原生 nav「LDE 施工台」
- 面板调用 `/lde/api/ide/*`，带 `credentials: include`

## 3. 实测

```text
scope: 20 个可写工单
lock:  已持有
read:  agent_platform/legion_command/ide_worktree.py · 5581B
write: agent_platform/legion_command/LDE_NATIVE_OK.txt · 14B
exec:  git status → ✅ exit=0 · 90ms
audit: code_action lock/read + code_observation exec
```

写入验证：

```text
.worktrees/K-QUANT-20261030-OHLOOP-TEST/agent_platform/legion_command/LDE_NATIVE_OK.txt
→ lde-native-ok
```

截图：

`C:\项目\work\_dsh_role_v3\hermes_lde_native_final.png`

## 4. 顺手修掉启动坑

8103 通过 WMI 启动时没有 `PYTHONPATH`，`serve.py` 的
`from tools.safe_urlopen import safe_urlopen` 会被 zhice 的常规
`tools` 包遮蔽，导致启动即退。

已在 `serve.py` 加按文件路径加载仓库 `safe_urlopen` 的兜底；
`ops/start_legion_ide.ps1 -Action start -EnableWrite` 现可直接拉起：

```text
owner=51516
rw_enabled=True
```

## 5. 命令白名单注意

`exec` 是白名单 + 参数限制；例如：

```text
✅ git status
❌ git status -s LDE_NATIVE_OK.txt（当前版本参数校验拒绝）
```

后续可扩白名单，但不在本次 P0 放宽。

## 6. 待办

1. 逐文件 Git diff / 提交
2. 文件树（当前仍需填路径）
3. Agent prompt/技能编辑
4. LDE 面板 8787 直连兼容（当前主入口 8734）
