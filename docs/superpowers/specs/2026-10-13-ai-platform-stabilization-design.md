# 零界 AI 助手稳定性与云边模型演进设计

> 日期：2026-10-13
> 状态：待评审
> 范围：本地 Ollama 聊天、Web/RAG、云端模型路由、运行治理

## 1. 背景与目标

零界当前已经具备门户、量化情报、Agent 组织和本地 AI 助手四条能力线。本设计聚焦 AI 助手及其运行边界，先解决可复现的稳定性问题，再引入云端模型，最后把 RAG 和观测能力接入统一平台。

目标是：

1. 普通聊天、RAG、CLI 和 Web 共享同一模型与超时契约。
2. 8899 本地聊天服务在 Ollama 冷启动、模型切换、RAG 不可用时给出可解释状态。
3. Ollama 与智谱使用统一的 provider 接口，密钥只来自环境变量，不落盘、不进日志、不进入浏览器。
4. RAG 保持本地嵌入和来源可追溯，云模型只作为可选生成 provider。
5. 8734 仍是人类统一入口；8899 是本地 Ollama 聊天服务，不能形成第二套门户入口。

## 2. 当前基线与约束

- `AI/111111` 当前 79 项测试通过，Python 编译通过。
- Ollama 11434 在线，已安装 `qwen3.5:4b`、`qwen3.5:9b`、`qwen3.5:35b` 和 `bge-m3`。
- 8899 Web、8734 门户、8091 API 当前可访问。
- `tools/check_ports.py` 在 GBK 控制台输出 Unicode 状态符号会崩溃；UTF-8 模式仍因硬离线端口和两个未登记第三方监听而严格失败。
- 主工作区存在大量与 AI 无关的未提交改动；本设计不允许全量 `git add` 或覆盖这些改动。
- 智谱密钥当前未在进程、用户/机器环境变量或项目配置中发现；接入只能读取 `GLM_API_KEY` 或 `ZHIPU_API_KEY`。
- 前端必须遵循 `tokens.css`、`nav.js`、短屏化和 8734 入口规范。

## 3. 方案比较

### 方案 A：稳定性优先（推荐）

先修复端口检查、入口资源、模型切换状态和统一 keep-alive，再抽象 provider，最后做 RAG 质量与平台整合。优点是风险最小、每一步都能回归；缺点是云模型要延后一个小版本。

### 方案 B：云模型优先

直接先做智谱 provider 和模型选择器，再回头补稳定性。优点是尽快获得云端模型选择；缺点是会把错误处理、超时、流式协议和密钥治理一起放大，容易在基础链路不稳时引入新变量。

### 方案 C：入口平台优先

先把 8899、8734、8091 和门户反代完全收敛，再做 provider 与 RAG。优点是入口清晰；缺点是用户最直接感受到的模型性能和云端选择会延后，且涉及 nginx 与门户大文件，变更面最大。

本设计采用方案 A。

## 4. 目标架构

```text
Web/CLI
  -> ChatService（会话、上下文、思考模式、超时、状态事件）
  -> ProviderRouter（按 provider/model/capability 选择）
      -> OllamaProvider（NDJSON，think，keep_alive）
      -> ZhipuProvider（SSE 转统一事件）
  -> RAGService（本地 bge-m3 检索、来源和索引版本）
  -> Event/Health 层（首 token、完成、错误、冷启动、索引状态）
```

### 4.1 Provider 接口

统一接口只暴露以下能力：

- `list_models()`：返回 provider、模型名、能力和可用状态；
- `stream_chat(messages, model, think, keep_alive)`：返回统一事件流；
- `embed(texts, model)`：仅本地 Ollama 默认实现；
- `health()`：返回连接、鉴权和模型可用性，不返回密钥。

统一事件至少包含 `message`、`thinking`、`sources`、`status`、`error`、`done`，前端不再分别解析 Ollama NDJSON 和智谱 SSE。

### 4.2 会话与性能

- 新会话默认 `qwen3.5:9b`、`think=false`、`keep_alive=30m`。
- 35B 只在高级模型选项中出现，切换时必须提示冷启动。
- 会话记录 provider 和 model；切换模型立即显示“可能冷启动”，即使当前还没有历史消息。
- 上下文按字符预算裁剪，保留最新完整消息；服务端和前端使用同一预算常量。
- 记录请求开始、首 token、完成、取消、超时和模型切换状态。

### 4.3 RAG

- 默认嵌入固定为 `bge-m3`，默认 RAG 生成模型为 `qwen3.5:9b`。
- 文档按内容哈希和来源路径增量更新；索引持久化后校验版本、记录数和来源。
- 每个回答带来源路径、片段、分数、文档哈希和索引版本。
- 无命中返回明确拒答；索引不可用返回 503；上游模型不可用返回 502。
- 云端 provider 第一阶段只用于普通聊天，RAG 继续使用本地嵌入，避免云端数据外发成为隐式行为。

### 4.4 智谱密钥治理

- 只读取进程环境变量 `GLM_API_KEY`，兼容 `ZHIPU_API_KEY`。
- 启动健康检查只显示“已配置/未配置/鉴权失败”，禁止输出密钥或完整请求头。
- 不把密钥写入源码、JSON、浏览器 localStorage、事件日志或错误消息。
- 云端请求失败默认显示明确错误；是否回落本地由配置开关控制，不能静默改变 provider。

## 5. 分阶段实施

### P0：稳定性收口

1. 让 `check_ports.py` 在 GBK、UTF-8 和 `--json` 三种模式都可运行。
2. 将 8899 页面资源改为同源可用路径，或由本地服务安全提供静态资源；不再依赖页面内硬编码 8734 地址。
3. 修正模型路由错误提示，补充 35B。
4. 统一 CLI、RAG 和 Web 的 `think`、`keep_alive`、超时和错误分类。
5. 增加启动、聊天、RAG、取消和冷启动 smoke；只提交 AI 相关文件。

### P1：云边模型路由

1. 新增 provider 抽象和统一事件适配。
2. 接入智谱流式聊天，增加鉴权健康检查。
3. Web 模型目录显示 provider、能力、默认状态和冷启动提示。
4. 增加显式回落策略与回归测试。

### P2：RAG 质量与可观测性

1. 增加索引状态、增量更新和重建 CLI。
2. 固定离线评测集，评估来源召回、引用完整性、拒答率和时序泄漏。
3. 记录首 token、完成耗时、生成速度、模型加载提示和失败原因。

### P3：平台整合

1. 将 AI 健康状态接入 8734 运维页和事件层。
2. 收敛 8734、8899、8091 的文档和反代说明。
3. 按域拆分 `portal_api.py`，AI 新端点不得继续扩展巨石文件。

## 6. 错误与降级规则

| 场景 | HTTP/事件 | 用户可见行为 |
|---|---|---|
| Ollama 未启动 | 502 / `provider_unavailable` | 提示启动 Ollama，并保留重试 |
| 智谱未配置 | 503 / `provider_not_configured` | 显示配置状态，不泄露密钥 |
| 智谱鉴权失败 | 502 / `provider_auth_failed` | 提示检查环境变量，可手动切回本地 |
| 模型冷启动 | `status=warming` | 显示当前模型和可能等待时间 |
| 请求超时/取消 | `timeout`/`cancelled` | 保留已生成内容，允许重试 |
| RAG 索引缺失 | 503 / `rag_unavailable` | 明确知识库不可用，不伪装成普通聊天 |
| 无检索来源 | 200 / `sources=[]` | 明确回答“没有足够依据” |

## 7. 测试与验收

### 单元与契约测试

- provider 请求体、流式事件和错误映射；
- think、keep_alive、上下文预算和会话模型锁定；
- 智谱密钥缺失、鉴权失败和回落策略；
- RAG 索引增量、版本、来源和拒答；
- GBK/UTF-8/JSON 端口检查。

### 端到端 smoke

1. `11434 /api/tags` 返回四个模型；
2. 8899 `/` 返回 HTML 且资源可加载；
3. `/api/chat` 返回首 token 和 `done`；
4. `/api/rag` 返回消息、来源和索引版本；
5. 模型切换显示冷启动状态；
6. Ollama 停止后返回可解释 502；
7. 智谱未配置时不影响本地聊天。

### 发布门

每个任务必须在独立 worktree、有任务单、提交信息带工单号，并通过：

```powershell
C:\Program Files\Python311\python.exe -m pytest AI\111111 -q
C:\Program Files\Python311\python.exe -m compileall -q AI\111111
C:\Program Files\Python311\python.exe tools\_mixin_integrity.py --check
C:\Program Files\Python311\python.exe tools\unify_nav.py --check
C:\Program Files\Python311\python.exe tools\check_ports.py --strict --json
```

端口守卫中的外部第三方监听必须有明确证据后再登记或豁免；不能为让 CI 变绿而静默修改注册表。

## 8. 不在本轮范围

- 不读取或猜测用户的明文智谱密钥；
- 不启用训练、LoRA、模拟成交或真实交易；
- 不把 RAG 文档默认上传到云端；
- 不整体重写 `portal_api.py`；
- 不清理或覆盖主工作区无关未提交文件；
- 不把 8899 变成人类统一入口，8734 仍是门户入口。

## 9. 完成定义

P0 完成的标准是：端口检查在默认 Windows 控制台不崩溃，8899 资源不依赖硬编码跨端口地址，AI 相关测试和 smoke 全绿，Git diff 只包含任务范围文件；P1/P2/P3 必须在前一阶段证据稳定后分别立单实施。
