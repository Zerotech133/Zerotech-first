# 方案 A：OpenHands 作为执行引擎

- 日期：2026-10-30
- 状态：**MVP 已跑通**；IDE 入口已加，待 8796 换血后启用
- 范围：OpenHands Agent Server + 军团网关 + IDE 入口

## 架构

```text
浏览器 8734 /ide
   │  OH 模式
   ▼
军团网关 8796 /api/ide/openhands/chat
   │  HTTP + session key（本机 loopback）
   ▼
OpenHands Agent Server 127.0.0.1:8770
   │  原生 Agent runtime：event stream / tools / workspace
   ▼
OpenHands LLM profile: openhands_legion_gateway
   │  OpenAI 兼容
   ▼
军团网关 8796 /v1/chat/completions
   │  模型/Key/路由
   ▼
DeepSeek / 其他 provider
```

职责划分：

- OpenHands：Agent 循环、工具调用、工作区读写、事件状态
- 军团：模型与 Key、组织/角色/工单、统一入口、审计
- 本机 loopback；OpenHands 不直接持有云端 Key

## 已落地

1. OpenHands 安装

```text
C:\项目\legion-openhands\.venv312
Python 3.12 + openhands-sdk/tools/workspace/agent-server
```

启动器：

```text
C:\项目\legion-openhands\start-openhands.ps1
```

2. Agent Server

```text
http://127.0.0.1:8770
health: {"status":"ok"}
```

3. LLM Profile

```text
name: legion_gateway
model: openhands_legion_gateway
LLM: openai/deepseek-flash
base_url: http://127.0.0.1:8796/v1
```

4. 军团网关

新增：

```text
GET  /api/ide/openhands/health
POST /api/ide/openhands/chat
GET  /api/ide/openhands/conversation?id=
```

同时补了 OpenAI function calling：

- 请求透传 `tools` / `tool_choice`
- 响应返回 `tool_calls` / `finish_reason`
- SSE 流式回放 `tool_calls`

5. IDE 入口

`portal/ide.html` 输入框旁新增 **OH** 按钮：

- OH OFF：原有聊天/只读工具
- OH ON：走 OpenHands 执行引擎
- 自动保存 conversation_id，支持连续任务

## 已实测

```text
1) 网关直接 tool_calls：200，get_weather 返回正确
2) OpenHands Agent Server /health：ok
3) OpenHands 任务：Create NOTES.md
   → 文件实际写入 workspace/workspace/project/NOTES.md
4) 适配器 POST /api/ide/openhands/chat
   → {"ok":true,"reply":"done","duration_ms":33707,"engine":"openhands"}
   → 文件 ROUTE_OK.txt 实际写入
```

## 已知限制

1. **8796 需要重启换血**：当前 8796 是 Session0 旧进程，无法从当前会话停止；需在管理员终端重启，或由系统计划任务重启。
2. OpenHands 当前工作区是独立目录：

```text
C:\项目\legion-openhands\workspace\workspace\project
```

尚未自动映射到军团工单 worktree；下一步应“一工单一会话一 worktree”。

3. OpenHands 的 tool 活动暂未在 IDE 过程面板逐条展示；当前只显示最终结果。后续通过 `/api/ide/openhands/conversation?id=` 拉事件流。
4. 模型固定 `deepseek-flash` 回退；尚未把 IDE 选中模型传给 OpenHands profile。
5. OpenHands 使用本地进程而非 Docker 沙箱；下一步接 Docker sandbox 或 Windows Job Object。

## 启用步骤

```powershell
# 1) 起 OpenHands（已在跑可跳过）
powershell -ExecutionPolicy Bypass -File C:\项目\legion-openhands\start-openhands.ps1 -Action start

# 2) 重启 8796（管理员终端）
#    先停旧进程，再运行 start_legion_ops.ps1
powershell -ExecutionPolicy Bypass -File C:\aios\ops\start_legion_ops.ps1

# 3) 验证
curl http://127.0.0.1:8770/health
curl http://127.0.0.1:8796/api/ide/openhands/health

# 4) 浏览器打开 8734/ide，点 OH → 输入任务
```

## 下一步

1. OpenHands 工作目录映射到 `task/<TID>` worktree
2. 工具事件流显示在过程面板
3. 把 IDE 选中模型/角色映射到 OpenHands profile
4. Docker sandbox / 资源限制
5. 将 `start-openhands.ps1` 纳入受控启动（不做计划任务，需人工批准）
