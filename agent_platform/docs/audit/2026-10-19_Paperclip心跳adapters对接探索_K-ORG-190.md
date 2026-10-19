# Paperclip 心跳 adapters 对接探索报告（K-ORG-190 · B 路 · 只读探索）

- **日期**：2026-10-19
- **执行**：drill-w6（任务 #110，只读探索，未改任何代码、未 git、未碰运行中服务；schtasks 查询被安全中心黑名单拦，改用仓内文档口径佐证）
- **结论速览**：心跳总线不是 TCP 端口而是**文件账本**。B 路对接点已在主仓 `agent_platform/adapters/heartbeat.py` 落了第一刀（探针抽象 + hb.v1 账本，半成品），军侧 Paperclip 仅 `/healthz` 一个心跳面且**部署未发生**（8790 未监听）。建议 A 案（拉式 HttpProbe + hb.v1），军侧零改动。

---

## 一、两侧现状实测

### 1.1 军团侧（digital-legion @ 57a1af6）

| 项 | 实测 |
| --- | --- |
| Paperclip 心跳面 | 仅 `paperclip/app.py` 的 `GET /healthz`（返回 status/accounts/balance_default，`app.py:261-269`）。**无独立 keepalive/心跳推送机制，无心跳数据外发** |
| 部署形态 | docker-compose 绑 `127.0.0.1:8790`（compose 注释：2026-10-18 netstat 核实空闲）；**2026-10-19 netstat 实测 8790 无监听 → 容器未运行，部署未发生** |
| M1 心跳（另一回事，勿混） | `heartbeat/runner.py`（361 行）：每日情报采集心跳，方案 B 计划任务 `AgentLegionHeartbeat` 每日 08:30（README 明文），直连 Ollama 11434 产 brief.json + 运行记录 run-*.json + 降级 degraded.json，全部**文件落盘型**，与 Paperclip WO/审计零耦合 |
| 军侧心跳数据流 | 四协议/影子/演练均为文件产物；无任何向主仓端口推送的机制 |

### 1.2 主仓侧（老田的AI助手）

**真正的"心跳总线" = `agent_platform/data/llm_tool_audit.jsonl`（文件账本）**，不是任何端口：

| 消费/生产面 | 位置 | 形态 |
| --- | --- | --- |
| 生产：军团动作统一落账 | `tools/legion_pulse.py`（K-ORG-121 L1）：`heartbeat(caller, action, detail, extra)` append 行 `{ts, caller, action, detail}`，fail-open | 写 |
| 生产：入职/派工/调度 | `portal/onboard_agent.py:144,311`、`portal/api_legion_ops_v2.py:159,205,236-239` | 写 |
| 消费：大屏近 N 分钟活动 | `legion_pulse.recent(minutes)`，`api_legion_ops_v2.py:21` 引入 | 读 |
| 消费：事件回放适配 | `agent_platform/adapters/event_adapter.py:148` event_source=`"llm_tool_audit/heartbeat"`（心跳行已收编为事件源） | 读 |
| 消费：**K-ORG-190 心跳三态** | `portal/org_command_api.py:184-231`：契约 `agent.heartbeat{expect, probe, stale_after}` → `heartbeat_state ∈ green/stale/no_ledger/None(灰)`；**probe=文件 mtime 探针**；chief A0 内置默认（probe=`agent_platform/data/shadow/chief_last_run.log`，stale_after=26h）；portal_api.py:1290-1295 同源复用 | 读 |
| **B 路半成品** | `agent_platform/adapters/heartbeat.py`（241 行，文件头自述"Paperclip 心跳对接第一刀，B 路"）：`HeartbeatProbe` 协议 + `NullProbe`（恒 not_configured）+ `FileProbe`（读状态 JSON）；样本契约 `{source, alive, detail, checked_at(分钟粒度)}`；幂等键 sha1(checked_at\|source)[:16]；落 `data/heartbeat/heartbeat.jsonl`（schema hb.v1）；安全阀：无显式 root 拒写 / 先校验后写 / SHADOW_DATA_ROOT 隔离继承 / CLI 无 --persist-root 即 dry-run | 未投产 |

**半成品缺口**：①只有 Null/File 探针，无真实 HTTP/Docker 探针（红线自述"部署未发生，不造不可验的探针"）；②`data/heartbeat/` 目录尚不存在（从未落账）；③无任何消费方读 hb.v1 账本（门3 巡检/证据仓接线仅为注释预留）；④org.json 中无任何 agent 配置 heartbeat（grep 零命中，仅 chief 代码内置默认）。

### 1.3 端口实测（2026-10-19 netstat，只读）

| 端口 | 实测 | 归属判读 |
| --- | --- | --- |
| 8091 | LISTENING pid=25792 (python.exe, Services 会话) | portal_api 门户 API（service_catalog：看门狗守护、health 探活） |
| 8094 | LISTENING pid=41904 (python.exe, Console 会话) | **旧 home_server 端口**，K-STOCK-167 已收编退役（portal_api/api_misc.py 移植五投影，注释明文"原服务退役"），但仍有 python 进程占监听——残余 listener，与 escort 巡检发现一致，处置归 OPS（非本任务） |
| 9899 | **无监听** | CowAgent console 端口，`ops/keepalive_9899.py` 计划任务「AI工作站CowAgent看门狗」每 5 分钟 TCP 探活拉起（同脚本并守护 8095/8097/8099）；无监听属看门狗窗口期/未拉起的既有现状 |
| 8790 | 无监听 | Paperclip 容器端口，部署未发生 |

**判定：8091/8094/9899 均非心跳总线**。心跳在主仓是文件账本（llm_tool_audit.jsonl 生产消费闭环 + hb.v1 账本预留），Paperclip 心跳端点是 8790 /healthz（未上线）。

## 二、对接设计要点

### 2.1 字段对齐

| 主仓消费侧字段（K-ORG-190 三态） | B 路 hb.v1 账本行 | 军侧供给 |
| --- | --- | --- |
| `agent_id`（org.json agent id） | `source`（如 `paperclip_org_shell`） | Paperclip 服务本体（需 agent_id↔source 映射一行约定） |
| `heartbeat_state` green/stale/no_ledger | `alive` bool + `checked_at`；三态可由 **hb.jsonl 尾行 mtime/checked_at 时效**推出（与现有 mtime 探针同形，probe 文件直接指向 heartbeat.jsonl 即可复用 org_command_api 零改动） | `/healthz` 200=alive |
| `timestamp`（probe 文件 mtime） | `checked_at`（分钟粒度）+ `recorded_at` | `/healthz` 响应即时刻 |

### 2.2 对接点

- **唯一对接层：`agent_platform/adapters/`**（heartbeat.py 已占位，B 路第一刀在此，续刀同层）。
- 消费接线点两选一：`org_command_api._heartbeat_state`（probe 指向 hb.jsonl，零代码改）或门3 巡检直接读 hb.jsonl。
- 军侧对接点：**无**（/healthz 现成；M1 runner 与此无关，不得混线）。

## 三、方案对比（A/B 案）

**A 案 · 拉式探针（推荐）**
主仓 `adapters/heartbeat.py` 增补第三种探针 `HttpProbe`（GET http://127.0.0.1:8790/healthz，短超时，连接失败=alive False 落账，不抛异常破坏 fail-visible）；计划任务/巡检周期跑现有 CLI：`python adapters/heartbeat.py --probe http --persist-root <agent_platform data root>`。
- 优：复用全部既有安全阀/幂等/测试形态；**军侧零改动**（尊重 digital-legion M0 契约层零依赖铁律）；hb.jsonl 兼作 K-ORG-190 probe 文件，三态消费零新增代码。
- 缺：前置条件是 Paperclip 容器真实部署（8790 上线）；部署前只能 NullProbe/FileProbe 演示形态。

**B 案 · 推式心跳**
Paperclip 或军团心跳脚本周期调主仓 `legion_pulse.heartbeat("PAPERCLIP", "alive", ...)` 直写 llm_tool_audit.jsonl。
- 优：直接进现有大屏 recent() 与事件回放，零新增消费面。
- 缺：军团仓反向 import 主仓模块，**击穿 M0 零依赖原则**（README 明文）；存活探针与动作审计两种性质混入同一账本；Paperclip 容器侧还要持主仓路径依赖（跨机/换机即死风险，K-STOCK-414 同型教训）。

**建议**：A 案为主体；如需上大屏，加一个**可选桥接小刀**（主仓侧独立脚本读 hb.jsonl 尾行→legion_pulse.heartbeat 转写），把跨仓依赖锁在主仓一侧，桥接器等 8790 上线后再启用。

## 四、遗留待拍板项

1. **Paperclip 容器部署时点**：8790 未监听是 A 案 HttpProbe 的硬前置。部署前 B 路只能以 Null/FileProbe 维持"诚实未接线"形态。
2. **hb.v1 是否纳入 K-ORG-190 消费**：org.json 各 agent 是否配 `heartbeat.probe` 指向 hb.jsonl（mtime 探针可零改动复用），还是门3 巡检独立读。
3. **source 命名统一**：FileProbe 默认 `paper_engine`（语义是 Paper 交易引擎）vs 本次对接对象 `paperclip_org_shell`（组织壳）——两者是不同实体，需 lead 定名并明确是否都要接。
4. **8094 残余监听（pid=41904）处置**：旧 home_server 已收编退役但仍占端口，建议转 OPS/escort 裁决，不在本任务范围。
5. **桥接器是否立项**：B 案弱化版（hb.jsonl→llm_tool_audit 转写）是否需要，取决于大屏是否要展示 Paperclip 心跳灯。

## 五、探索过程证据清单

- 军侧：`digital-legion/paperclip/app.py:261-269`（healthz）、`docker-compose.yml:8`（8790）、`heartbeat/README.md` + `heartbeat/runner.py`（M1 方案 B）、README M1 节（AgentLegionHeartbeat 08:30）
- 主仓：`tools/legion_pulse.py`（总线账本）、`agent_platform/adapters/heartbeat.py`（B 路第一刀全文）、`agent_platform/adapters/event_adapter.py:148`、`portal/org_command_api.py:184-231`（三态契约+chief 默认）、`portal/portal_api.py:1290-1295`、`portal/api_legion_ops_v2.py:21,159-239`、`portal/onboard_agent.py:144,311`、`portal/api_common.py:259` + `ops/keepalive_9899.py`（9899 归属）、`portal/api_misc.py`（8094 收编退役判词）
- 实测：netstat 8091/8094/9899/8790 四端口监听状态；`agent_platform/data/` 下 heartbeat 目录不存在（未投产佐证）；org.json 无 heartbeat 配置（grep 零命中）
