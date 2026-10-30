# OHLOOP：工单闭环 + sandbox 自愈

- 日期：2026-10-30
- 工单：K-QUANT-20261030-OHLOOP
- 状态：**4 项全部实现并实测**

## 1. 自动 worktree

新增 `agent_platform/legion_command/ide_worktree.py`：

- 只接受 `doing` / `review` 工单
- `C:\legion-worktrees\<TID>` 已存在则直接使用
- 不存在时自动 `git worktree add`：
  - 有 `task/<TID>` 分支 → 签出该分支
  - 没有 → 从 master 建 `task/<TID>`
- 适配器执行 OH 前自动调用

## 2. diff

新增接口：

```text
POST /api/ide/openhands/diff
```

返回：

- 分支、worktree
- 变更文件列表
- `git diff`
- 未跟踪新文件内容（synthetic diff）
- `has_changes`

## 3. 确认提交

新增接口：

```text
POST /api/ide/openhands/commit
```

- 只在 `task/<TID>` worktree 内 `git add -A && git commit`
- 不自动 push / 不自动 merge
- 返回 commit hash
- 前端 diff 面板出现「确认提交到工单」按钮

## 4. sandbox 自愈

`openhands_engine.ensure_sandbox()`：

- 先探测 8771 `/health`
- 不通则：
  - 容器已存在 → `docker start`
  - 不存在 → `docker run`（4GB / 2 CPU，挂载 worktrees）
- 等待 health 恢复后再执行任务

实测：

```text
docker stop legion-openhands-sandbox
→ ensure_sandbox() → started=True
→ /health 200
```

## E2E 实测

浏览器 8734：

```text
OH ON + 工单 K-QUANT-20261030-OHLOOP-TEST
→ Create OHLOOP_E2E.md
→ OH 14.8s
→ Action/str_replace + Observation
→ 出现 diff 面板：OHLOOP_E2E.md
→ 点「确认提交到工单」
→ ✅ 已提交 a45265d08
```

worktree 分支：

```text
task/K-QUANT-20261030-OHLOOP-TEST
commit a45265d08 "OpenHands 执行结果 · K-QUANT-20261030-OHLOOP-TEST"
```

## 仍未收口：8796

- 8796 被一个 **Session0 旧进程**（pid 29228，当前会话无权限停止）占用，加载的是旧代码，没有 diff/commit 路由。
- 为完成闭环验证，OH 适配 API 暂时走 **8799** 临时实例：
  - nginx `/openhands/` → 8799
  - 8799 已登记为过渡端口
- 8797 已退役。
- 待 8796 旧进程可终止后：
  1. 停 8796
  2. 启动新 8796
  3. nginx `/openhands/` 改回 8796
  4. 退役 8799

## 验收

```text
自动 worktree        ✅
diff（含新文件）      ✅
确认提交             ✅ commit a45265d08
sandbox 自愈         ✅ stop → auto start
浏览器 E2E           ✅
```
