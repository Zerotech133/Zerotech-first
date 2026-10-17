# 数字军团管理端代码审计、研发进度与缺失项 v1.0

## 1. 审计范围

审计对象：

```text
C:/项目/deliverables/legion-admin-prototype/index.html
C:/项目/deliverables/legion-admin-prototype/style.css
C:/项目/deliverables/legion-admin-prototype/app.js
```

真实组织依据：

```text
C:/aios/agent_platform/registry/org.json
C:/aios/agent_platform/hr/WATCH_TOWER/resume.yaml
C:/aios/agent_platform/hr/WATCH_TOWER/appointment.yaml
```

审计目标：检查管理端原型是否与 WATCH_TOWER 真实编制一致，检查交互边界、资源引用、状态管理、数据真实性、可维护性及后续接入缺口。

## 2. 当前研发进度

| 模块 | 状态 | 证据/说明 |
|---|---|---|
| 管理端视觉原型 | 已完成 | 平台浅色卡片、蓝/绿状态体系已统一 |
| 组织树 | 已完成 | A0、A0A、WATCH_TOWER、AgentWatchdog、ops_runtime 等节点 |
| Agent 详情 | 已完成 | ID、职务、E2、主管、部门、上级、Endpoint 等字段 |
| 运行状态展示 | 原型完成 | 可用性、心跳、请求量、响应时间为演示数据 |
| 市场分片 | 原型完成 | CN-A/CN-B/GLOBAL 为演示数据，尚未接真实分片服务 |
| 方法插件 | 原型完成 | 展示型列表，尚未接真实插件注册表 |
| 权限矩阵 | 原型完成 | 展示型矩阵，尚未接真实权限策略 |
| 暂停/恢复/重启 | 原型完成 | 当前为本地状态和 Toast，不调用守护服务 |
| 审计入口 | 原型完成 | 当前为提示，不读取审计日志 |
| 新增适配/注册 Agent | 入口完成 | 当前弹窗为原型提示，尚未创建注册工单 |
| 数据接口 | P0 已开始 | 已新增只读 BFF：registry/HR 读取、Agent 详情和 800ms TCP 健康探测；认证/RBAC 尚未接入 |
| 持久化 | 缺失 | 操作不会保存到组织注册表或数据库 |
| 浏览器自动化验收 | 阻塞 | 当前环境没有可用 Chromium/Playwright/jsdom |

## 3. 已发现并修复的问题

### BUG-001：节点切换后异步重启结果可能污染当前节点

**严重度：P1，已修复**

原问题：重启 WATCH_TOWER 后，如果用户在 1.6 秒内切换到其他节点，旧定时器仍可能把当前页面状态改为“运行中”，造成状态串台。

修复：在重启时捕获 `restartAgentId`，回调执行前检查：

```javascript
if (currentAgentId !== restartAgentId) return;
```

### BUG-002：管理操作提示固定写死 WATCH_TOWER

**严重度：P1，已修复**

修复：暂停、恢复、重启和审计提示统一使用：

```javascript
agents[currentAgentId].title
```

### BUG-003：演示时间与真实注册时间不一致

**严重度：P1，已修复**

原型曾展示旧的演示日期和时间；已改为与当前审计上下文一致的注册日期 `2026.10.17`，并增加实时上下文字段 ID。

### BUG-004：资源文件缺失

**严重度：P0，已修复**

HTML 曾先于 CSS/JS 生成，导致页面暂时无法完整加载。当前三文件均已存在，HTML 本地引用有效。

## 4. 当前代码审计结论

### 4.1 已通过项

- `node --check app.js` 通过；
- HTML 的本地 `style.css`、`app.js` 引用存在；
- DOM ID 无重复；
- 组织节点均使用 `data-target`，由 `selectAgent()` 统一切换；
- 适配弹窗具备打开、关闭、遮罩关闭和确认反馈；
- 暂停/恢复、重启、审计操作已绑定；
- 节点级操作名称跟随 `currentAgentId`；
- 真实 WATCH_TOWER 关键字段已进入页面；
- 没有使用 `innerHTML` 拼接用户输入的高风险路径；
- 当前仅为本地原型，无真实凭据和交易执行权限。

### 4.2 尚未完成项

1. **数据真实性**：可用性、请求量、响应时间、市场分片、插件版本、毕业权重均为静态演示数据；
2. **身份字段模型化**：详情页仍需从 HR/registry API 读取，不应长期复制静态文字；
3. **组织关系**：部门、直属守护、最高上级需要由组织注册表计算并返回 lineage；
4. **权限校验**：按钮当前任何访问者都可点击，缺少 RBAC、二次确认和操作审计；
5. **运行控制**：暂停/重启没有调用 AgentWatchdog 或运行控制 API；
6. **审计查询**：没有 request_id、actor、时间、结果、失败原因和可导出记录；
7. **适配注册**：没有 schema、审批流、工具白名单校验、入编写入和回滚；
8. **错误与离线态**：没有 API 超时、服务离线、部分数据缺失、权限不足和重试界面；
9. **安全边界**：Google Fonts 为外链；生产环境应自托管字体或允许 CSP 白名单；
10. **可访问性**：需要补充键盘焦点、ARIA 关系、弹窗焦点陷阱、按钮状态语义和移动端验证；
11. **测试**：当前只有静态检查，尚无浏览器 E2E、接口契约、权限和状态机测试；
12. **状态机**：运行状态应从 `registered/starting/healthy/degraded/paused/restarting/failed` 统一建模，不能只改文字。

## 5. 推荐研发计划

### P0：从原型到可运行管理端

**目标：只读真实数据 + 安全操作闭环。**

1. 建立管理端 API：组织树、Agent 详情、心跳、分片、插件、权限、告警、毕业状态；
2. 后端读取 `org.json`、HR appointment/resume 和运行时健康数据，形成统一 DTO；
3. 接入 AgentWatchdog 健康探测，明确 `supervisor` 是守护组件，`A0A` 是部门归口；
4. 全部管理操作接入 RBAC、二次确认、幂等键、request_id 和审计事件；
5. 前端增加 loading、empty、offline、403、500、retry 状态；
6. 禁止使用演示指标冒充实时指标，增加 `source`、`observed_at`、`quality` 标签。

**验收：** WATCH_TOWER 页面每个核心字段均能回溯到来源；暂停/重启操作有权限校验、审计记录和结果回写。

### P1：Agent 适配与注册中心

**目标：让管理端可以安全增加运行体。**

1. 定义 Agent manifest/schema：agent_id、name、title、kind、dept、supervisor、endpoint、tools、danger、healthcheck；
2. 注册草稿→校验→审批→入编→灰度→启用状态机；
3. 工具白名单和 `danger: deny` 默认策略；
4. 组织关系校验：部门存在、主管存在或标记为平台守护组件、最高上级合法；
5. 版本化、冲突检测、回滚和审计；
6. 将正式注册写入唯一受控入口，避免直接散改 `org.json`。

**验收：** 新增 Agent 不能绕过权限、工具和主管关系校验；失败注册可解释、可重试、不会污染正式组织表。

### P2：运行运营和可观测性

**目标：从静态管理升级为运营控制台。**

1. 心跳时序、请求量、延迟、错误率和容量趋势；
2. 分片调整、插件启停、版本对比和灰度发布；
3. 告警中心、事件确认、升级和通知渠道；
4. 审计检索、导出、lineage 回放；
5. 毕业权重、T+5/T+10/T+20 归因和回测证据关联；
6. 组织级健康评分和跨 Agent 故障拓扑。

## 6. 管理端最小后端契约建议

```text
GET  /api/admin/org/tree
GET  /api/admin/agents/{agent_id}
GET  /api/admin/agents/{agent_id}/health
GET  /api/admin/agents/{agent_id}/permissions
GET  /api/admin/agents/{agent_id}/audit
POST /api/admin/agents/{agent_id}/pause
POST /api/admin/agents/{agent_id}/resume
POST /api/admin/agents/{agent_id}/restart
POST /api/admin/agent-adapters/drafts
POST /api/admin/agent-adapters/{draft_id}/validate
POST /api/admin/agent-adapters/{draft_id}/submit
```

统一响应：

```json
{
  "code": "OK",
  "data": {},
  "message": "",
  "request_id": "...",
  "observed_at": "2026-10-17T06:55:23+08:00",
  "source": "agent_platform"
}
```

## 7. 当前最重要的缺失

```text
缺后端数据契约
缺认证/RBAC
缺真实健康探测
缺安全操作 API
缺审计持久化
缺 Agent manifest/schema
缺注册审批状态机
缺真实插件/分片服务
缺错误态和离线态
缺浏览器 E2E
```

因此当前版本可以称为：

> **管理端信息架构与视觉交互原型 v0.2**

不能称为生产管理端，也不能把页面上的演示指标当成真实运行指标。

## 8. P0 本轮新增实现

新增目录：

```text
C:/项目/deliverables/legion-admin-prototype/backend/
├── __init__.py
├── repository.py
├── api.py
└── test_repository.py
```

已实现：

- 只读 `GET /api/admin/org/tree`；
- 只读 `GET /api/admin/agents/{agent_id}`；
- 只读 `GET /api/admin/agents/{agent_id}/health`；
- 兼容 `/api/v1/...` 版本化路径；
- 从真实 `org.json`、HR resume/appointment 读取；
- 返回 `request_id`、`observed_at`、`source`、`source_mtime`；
- endpoint 800ms 非侵入 TCP 探测；
- 探测失败返回 `offline/probe_failed`；
- 写方法统一返回 `405 READ_ONLY`；
- YAML/registry 错误统一返回明确的 503 数据源错误；
- 测试结果：`2 passed`。

已知真实数据源问题：当前全量组织树读取会遇到部分历史 HR YAML 格式错误，接口明确返回 `503 API_UNAVAILABLE`，没有静默伪造数据；单个 `WATCH_TOWER` 详情与健康接口可读取，当前 `127.0.0.1:9106` 探测为 offline（连接拒绝），这表示守护端点当前不可达，不等于 Agent 编制注销。

## 9. 下一步定盘

优先做 P0，而不是继续堆页面：

```text
统一管理端 API/DTO
→ 接真实 registry + HR + AgentWatchdog
→ 接 RBAC 和审计
→ 改造前端为真实数据驱动
→ 增加浏览器 E2E
→ 再进入 Agent 注册适配 P1
```
