# 当前运行态矩阵与启动手册

> 状态：[AUDITED]（配置/代码/HTTP 运行态交叉核验）
> 日期：2026-10-17
> 说明：端口可达不等于业务链路完整；“当前状态”以最后一次实测和最新审计为准。

## 1. 端口矩阵

| 端口 | 服务 | 绑定/入口 | 启动或守护 | 当前说明 |
|---:|---|---|---|---|
| 8734 | 人类统一入口 | portproxy → nginx | Windows portproxy + Docker nginx | 唯一对外/人类入口；文档和页面不写 8080 |
| 8080 | Docker nginx `ai-portal` | `0.0.0.0:8080` | `portal/start_portal_nginx.ps1`，容器 `unless-stopped` | 容器/局域网反代链路；源码目录只读挂载 |
| 8091 | `portal_api.py` | `127.0.0.1` | 计划任务“AI工作站门户API” + `start_portal_api.ps1` | 门户动态 API；`/health` 存活探针 |
| 8092 | KG Canvas | `127.0.0.1` | `zerobound-kg/kg_canvas.py` | 独立只读图谱画布，当前 nginx 未收编 |
| 8093 | org registry | 本机服务 | org registry 启动脚本/计划任务 | `/api/org`、`/api/agent/knowledge` 的数据面 |
| 8094 | 菜鸟/知识桥 | 本机服务 | 对应守护任务 | nginx `/cai/api/` |
| 8096 | Legion API | 本机服务 | 对应守护任务 | nginx `/legion-api/` |
| 8097 | 推送管理 | 本机服务 | 对应守护任务 | nginx `/push/` |
| 8100 | datamart API | `127.0.0.1` | zerobound-kg 对应服务 | 数据中台 API，非门户主入口 |
| 8787 | Hermes WebUI | `127.0.0.1` | `Hermes/start_hermes_bg.py` | 当前主链为 `hermes-webui-next/server.py` |
| 8788 | Hermes next 验证实例 | `127.0.0.1` | `start_hermes_next.py` | 验证/并行端口，不是生产入口 |
| 8791 | KG 数据大屏 | 本机服务 | 计划任务 `KgDataDashboard` | Hermes MCP 与门户相关数据读取 |
| 11434 | Ollama | 本机服务 | `OllamaWatchdog` | 本地模型与 Agent 推理主通道 |
| 7687 | Neo4j | Docker `neo4j-kg` | Docker | 共享图谱数据库，写面需协调 |

## 2. 数据根矩阵

| 数据域 | 唯一/主数据根 | 备注 |
|---|---|---|
| 量化与 KG warehouse | `C:/项目/老田的AI助手/quant_backtest/data` | Zerobound、门户和任务共用；禁止新建第二 live 根 |
| Agent Platform | `C:/项目/老田的AI助手/agent_platform/data` | 任务、幂等、审批、证据、memory 等独立账本 |
| Portal 代码与页面 | `C:/项目/老田的AI助手/portal` | nginx 只读挂载，动态数据主要读 QBT_DIR |
| Hermes state | `C:/项目/老田的AI助手/Hermes/webui-state` | 切换 WebUI 时必须保持 state 目录 |
| Hermes next state | `C:/项目/老田的AI助手/Hermes/webui-state-next` | 8788 验证实例 |
| Neo4j | Docker volume `neo4j-kg` | 不在仓库内；恢复先启动容器 |

`C:/项目/老田的AI助手/KGDataRoot` 不是新的 live 数据根；历史文档中的 `E:\老田的AI助手` 和 `C:\aios` 可能是 junction/旧环境路径，不能直接复制为新命令。

## 3. 统一入口与路由

- `/`：新版门户首页。
- `/org`：新版静态组织架构页；数据经 8093。
- `/approvals`：审批注册表页，数据经 `/api/platform/approvals`。
- `/hermes/`：反代 8787。
- `/kg/*`：portal_api 的 KG 查询页，不等于 8092 KG Canvas。
- `/cowagent/`：反代 9899；上游离线时 502 属上游状态。
- `/api/*`：默认 8091；`/api/org` 和 `/api/agent/knowledge` 特殊转 8093。

## 4. 安全与重启纪律

1. nginx 改动前执行 `docker exec ai-portal nginx -t`，通过后再 reload。
2. 不得使用 `Stop-Process -Name python`；门户、Hermes、采集任务共用 Python，必须按端口/命令行精确识别。
3. 8091 受控重启使用 `C:/Temp/PORTAL_API_RESTART.flag` + 计划任务“AI工作站门户API”，不要裸杀服务。
4. 变更类门户 POST 依赖 `X-Portal-Token`；nginx 注入 `X-Portal-Proxy` 和 `X-Real-IP`。
5. nginx webroot 禁止暴露 `.py/.pyc/.ps1/.md/.conf/.env/.bak` 等后缀；新增静态资源后应做暴露面回归。
6. Hermes 默认只绑定回环；如需局域网暴露，必须补密码和访问控制。
7. KG Canvas 是只读服务，也不得直接暴露公网。

## 5. 常见故障

| 症状 | 首查 | 处理 |
|---|---|---|
| 8080 全部 502 | Docker DNS/resolver、8091 health | 检查 `ai-portal`、`/health`、`nginx -t` |
| `/org` 页面 200 但无数据 | 8093、`/api/org`、`/api/agent/knowledge` | 先直测数据面，不要改回旧 SSR |
| `/approvals` 404 | nginx `/approvals` 精确 location、8091 页面路由 | 检查配置并 reload |
| Hermes 页面不可用 | 8787、当前 server.py、state 目录 | 使用 `start_hermes_bg.py`，勿启动旧副本覆盖 state |
| KG 数据为空 | `KG_DATA_ROOT`、Neo4j、KgDailySync | 区分数据根错误与调度未运行 |
| 门户修改不生效 | 8091 旧进程、nginx 缓存 | 使用受控重启哨兵；静态页检查 no-cache |

## 6. 事实边界

- 本矩阵记录的是入口、代码配置和已知运行态，不替代具体服务的业务验收。
- 计划任务“已注册”不代表当天成功运行；必须同时核对 LastResult、产物 mtime 和日志。
- 设计文档中的端口/路径若与本文件、代码或 HTTP 实测冲突，以运行态和代码为准。
