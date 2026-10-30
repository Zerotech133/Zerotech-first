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
  - `base_url=http://127.0.0.1:8796/v1`
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
| IDE OH + 工单 worktree（Windows 主机） | ⚠ 卡在 FileEditorAction，无 Observation |
| 新建文件（git worktree，Windows 主机） | ⚠ 同样卡住 |
| Docker sandbox + 工单 worktree（新建文件） | ✅ 成功 |
| IDE OH + Docker sandbox + 工单 worktree | ✅ A0 · 12.5s，工具事件可见 |
| 8796 换血 + 退役 8797 后最终验证 | ✅ A0 · 11.6s，8796 → sandbox → 文件写回 |

## Windows 主机的工具阻塞

Windows 直接运行 OpenHands 时：

- `FileEditorAction` 的 `create` / `str_replace` 会卡住
- 事件停在 `ActionEvent`，没有 `ObservationEvent`
- 直接调用 FileEditor 也复现
- 不是路径乱码（ASCII 路径同样）
- 不是模型（deepseek/glm 都复现）

**已通过 Docker sandbox 绕过：文件工具在 Linux 容器内执行。**

## 4. 8796 换血、退役 8797

已完成：

- 旧 8796（Session0）退出后，新 8796 由 `C:\项目\py312\python.exe` 启动
- nginx `/openhands/` 从 8797 切回 8796
- 8797 已停止并从端口注册表移除
- 最终实测：A0 · OpenHands · ✅ 11.6s

### 5. Docker sandbox / 资源限制

已完成：

```text
容器：legion-openhands-sandbox
镜像：ghcr.io/openhands/agent-server:latest-python
端口：127.0.0.1:8771 -> 8000
挂载：C:\legion-worktrees -> /workspaces
限制：--memory 4g --cpus 2
```

- 适配器默认连 8771
- task/worktree 映射为容器内 `/workspaces/<TID>`
- LLM 走容器内 `host.docker.internal:8796/v1`
- 启动/停止脚本：

```text
C:\项目\legion-openhands\start-sandbox.ps1
```

实测：

```text
Create OH2_UI_SANDBOX.txt
ActionEvent FileEditorAction: create
ObservationEvent: File created successfully at /workspaces/...
宿主 worktree 实际出现 OH2_UI_SANDBOX.txt
```

## 结论

5 项全部完成：

- 工单 worktree 映射
- 事件流展示
- IDE 模型/角色 → OpenHands 模型映射
- 8796 换血、8797 退役
- Docker sandbox + 资源限制

最终链路：8734 `/ide` → `/openhands/` → 8796 → 8771 sandbox；sandbox 的模型请求回 8796 `/v1` → DeepSeek。  
实测：A0 · OpenHands · ✅ 11.6s，FileEditor + Observation + 宿主 worktree 写回。
