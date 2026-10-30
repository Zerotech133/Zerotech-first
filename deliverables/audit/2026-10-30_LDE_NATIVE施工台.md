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

## 6. P1 增量：文件树 + 逐文件 Git

已补：

- 文件树 API：`GET /api/ide/tree?task=&path=`
  - 根目录只列受控目录
  - 目录下钻 / 根目录返回
  - 点文件直接加载到编辑器
- 逐文件 Git API：`POST /api/ide/git`
  - `diff` → diff
  - `add` → stage 单文件
  - `commit` → commit 单文件
  - 全部要求工单锁 + 受控路径
- UI：文件树、文件 diff/add/commit 按钮

实测：

```text
tree root → agent_platform → legion_command
→ ide_worktree.py 已加载 5270 字节
→ LDE_TREE_OK.txt 写入成功
→ git diff / add / commit 成功
→ commit 0b013fbf / a92088dfe
```

## 7. P2：Agent prompt / 技能

Agent 详情新增：

- 「在 LDE 编辑 prompt」
  - 取 bundle 的 `prompt_file`
  - 切换 LDE 施工台并自动填入路径
  - 可直接读取/编辑/预览/确认写
- 「复制技能」
  - 技能列表复制到剪贴板

实测：

```text
A0 田丰
prompt_file = agent_platform/prompts/a0_chief.md
在 LDE 打开 → 已读取 2712B
复制技能 → 产品定义与验收标准 / 架构/接口契约设计 / ...
```

## 8. 待办

1. LDE 面板 8787 直连兼容（当前主入口 8734）
2. exec 命令白名单按需扩容
3. Agent prompt 的版本/来源提示（当前写 worktree）
