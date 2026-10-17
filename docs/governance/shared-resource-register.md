# 共享资源登记表

> 状态：[CURRENT] + [GOVERNANCE]
> 启用日期：2026-10-17
> 规则：默认只读；涉及写入、迁移、清理、重启、换根或调度变更，必须先登记窗口；完成后补结果。

## 登记字段

| 字段 | 说明 |
|---|---|
| resource | 资源名称/路径/端口 |
| ticket | 工单号 |
| owner | 责任人/Agent |
| window | 开始-结束时间 |
| mode | `read-only` / `write` / `restart` / `migration` |
| scope | 明确允许触碰的文件、表或端点 |
| rollback | 回滚方式 |
| result | 完成后填写结果与证据路径 |

## 资源清单

| resource | 当前事实源 | 默认模式 | 特别纪律 |
|---|---|---|---|
| `quant_backtest/data` | `KG_DATA_ROOT`/`quant_backtest/data` | read-only | 结构操作前读写 `C:/Temp/kg_sync_task.log` |
| Neo4j `neo4j-kg` / 7687 | Docker volume + env 凭据 | read-only | 写面互斥、valid 时间边界、先备份 |
| `portal/nginx.conf` / `ai-portal` | nginx 配置与容器 | read-only | `nginx -t` 后 reload |
| portal 8091 | `portal_api.py` + 计划任务 | read-only | 重启用 flag + 计划任务，禁止裸杀 |
| org registry 8093 | registry service | read-only | API 数据面变更需契约测试 |
| Ollama 11434 | `OllamaWatchdog` | read-only | 不新增启动源；NUM_PARALLEL=1 |
| 计划任务注册表 | Windows Task Scheduler | read-only | 任务→脚本→解释器→产物→告警五段核验 |
| `agent_platform/data` | platform JSON/JSONL/SQLite | read-only | 审批/evidence/idempotency append-only |
| Hermes state | `Hermes/webui-state*` | read-only | 生产与验证 state 分离，切换先备份 |
| 统一导航源 | `portal/api_common.py` | read-only | 仅源文件修改后重新 export_nav |

## 当前登记窗口

| resource | ticket | owner | window | mode | scope | rollback | result |
|---|---|---|---|---|---|---|---|
| 项目治理启动 | GOV-20261017 | WorkBuddy | 2026-10-17 | read-only | 基线、文档、端口、HTTP 探测 | 删除本次新增治理文档（不动业务） | 进行中 |

## 变更窗口规则

1. 同一资源同一时间最多一个 `write`/`migration`/`restart` 窗口。
2. `read-only` 可以并行，但不能在读取期间假设数据不会变化；报告必须带时间戳。
3. 窗口超时自动视为失效，需重新登记。
4. 发现未登记写入，立即停止相关任务，保留证据并升级审查。
5. 资源登记不是权限授权；生产写面仍需要人工批准。
