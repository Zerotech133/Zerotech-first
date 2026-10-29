# 方案 A 二期：worktree / 事件流 / 模型映射

- 日期：2026-10-30
- 状态：**代码已接，主链路部分验证；Windows 主机工具执行阻塞**
- 关联：`K-QUANT-20261030-OH2`

## 已完成

### 1. 工单 worktree 映射

- 适配器改用 OpenHands **原生会话 API**（不是 OpenAI gateway）
- 创建会话时传 `workspace.working_dir`
- `task` 参数映射 `.worktrees/<TID>`
- 增加 ASCII junction `C:\legion-worktrees -> ...\.worktrees`，避免 Windows 中文路径进入工具参数后乱码
- IDE OH 模式增加工单 ID 输入框，可指定 worktree

### 2. 事件流

- 适配器读取 `/api/conversations/{id}/events/search`
- 提取 `ActionEvent` / `ObservationEvent` / `MessageEvent`
- 返回 `events` 数组
- IDE 过程面板显示最近 12 条工具动作/观察 + 最终答复

### 3. 模型映射

- IDE 选中模型传入适配器
- 适配器写入会话 `agent_settings.llm`：
  - `model`
  - `api_key=legion-local`
  - `base_url=http://127.0.0.1:8797/v1`
- OpenHands 会话 stats 已验证显示：
  - `openai/deepseek-flash`
  - `openai/glm-5.3`

## 验证结果

| 项目 | 结果 |
|---|---|
| 原生会话指定 `workspace.working_dir` | ✅ 成功 |
| ASCII junction worktree | ✅ 路径正确 |
| 事件检索 Action/Observation | ✅ 有数据 |
| 修改已有文件（plain workspace） | ✅ `hello.py` 改成功 |
| IDE OH 默认任务 | ✅ 12.1s 写文件 |
| IDE OH + 工单 worktree | ❌ 卡在 FileEditorAction，无 Observation |
| 新建文件（git worktree） | ❌ 同样卡住 |

## 硬阻塞

OpenHands 当前 Windows 主机模式下：

- `FileEditorAction` 的 `create` / `str_replace` 会卡住
- 事件停在 `ActionEvent`，没有 `ObservationEvent`
- 直接调用 FileEditor 也复现
- 不是路径乱码（ASCII 路径同样）
- 不是模型（deepseek/glm 都复现）

判断：OpenHands 本地文件编辑工具与当前 Windows 进程/工作区环境不兼容。

## 未完成

### 4. 8796 换血、退役 8797

- 8796 是 Session0 旧进程，当前会话无权限停止
- 现用 8797 临时适配网关 + nginx `/openhands/` 同源代理
- 需要管理员终端或系统重启后把前端地址切回 8796，并退役 8797

### 5. Docker sandbox / 资源限制

未实现。当前仍是宿主机进程。

## 建议下一步

**先做 Docker sandbox（第 5 项）**，而不是继续在 Windows 主机工具上打补丁：

- `DockerWorkspace` + 官方 `ghcr.io/openhands/agent-server:latest-python`
- 每个工单 worktree 以 volume/bind mount 进容器
- 文件编辑在 Linux 容器内执行，绕开 Windows FileEditor 阻塞
- 同时获得资源限制/隔离

在 Docker sandbox 跑通前，军团 IDE 的 OH 模式只能用于“读/分析/默认工作区简单写文件”，不能承诺真实工单改码。
