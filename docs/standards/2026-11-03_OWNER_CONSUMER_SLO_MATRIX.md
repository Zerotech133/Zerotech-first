# 接口 owner / consumer / SLO 矩阵 v1（IFS-02/IFS-08/IFS-10 配套）

- **编号**：2026-11-03_OWNER_CONSUMER_SLO_MATRIX
- **机器可读真源**：`docs/standards/INTERFACE_REGISTRY_v1.yaml`（`interfaces[*]` = owner/consumer/SLO；`owners` = 席位目录；`slo_classes` = SLO 档位）
- **配套**：`docs/standards/2026-11-03_接口对接规范_IFS_v1.md`（IFS-01~14）、`docs/standards/2026-11-03_ID_CHAIN_v1.md`
- **性质**：规范层矩阵（本单只登记，不改代码/不启动服务）
- **验收口径**：38 接口 × owner × consumer × SLO **无空洞**；owner 必须在册；consumer ≥1；SLO 必须命中档位；write/paper_only 与 IFS-05 一致。

---

## 一、SLO 档位定义（与 registry `slo_classes` 一致）

| 档位 | p95 延迟 | 错误率上限 | 可用性 | 适用 |
|---|---|---|---|---|
| `read_fast` | 300 ms | 1% | 99.5% | 轻量只读（列表/状态/健康） |
| `read_heavy` | 1500 ms | 2% | 99.0% | 聚合/数据仓查询 |
| `write_ledger` | 500 ms | 0.5% | 99.9% | 账本落盘（decision/handoff/trade） |
| `write_gate` | 800 ms | 0.5% | 99.9% | 人工门/确认/会议写 |
| `internal_contract` | 100 ms | 0.1% | 100% | 进程内契约（ID 链中间件） |
| `mcp_tool` | 3000 ms | 2% | 99.0% | Hermes MCP 工具 |
| `manifest` | 100 ms | 0.1% | 100% | 组合 manifest 静态登记 |

> SLO 为 F0 定稿基线；实施后由实测校准（每季度 kernel review 复核）。缺实测数据时不得宣称达标（fail-visible）。

---

## 二、接口矩阵（38 接口）

| # | interface_id | 方法 | 路径 | owner | consumer | SLO 档位（p95/err） | write | paper_only | status |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `acad1.pool.get` | GET | `/api/acad1/pool` | ACAD1 | D1、门户、DATA1 | read_fast（300ms/1%） | false | true | draft |
| 2 | `dw.query.get` | GET | `/api/dw/` | DATA1 | 全平台 Agent、门户、组合 | read_heavy（1500ms/2%） | false | true | v1 |
| 3 | `dw.query.post` | POST | `/api/dw/` | DATA1 | 全平台 Agent、门户 | read_heavy（1500ms/2%） | false | true | v1 |
| 4 | `strategy.list` | GET | `/api/strategies` | STRAT1 | D1、门户、组合、WATCH_TOWER | read_fast（300ms/1%） | false | true | v1 |
| 5 | `d1.research.get` | GET | `/api/d1/research` | D1 | 门户、HERMES | read_heavy（1500ms/2%） | false | true | draft |
| 6 | `d1.case.upsert` | INTERNAL | `internal:d1/case` | D1 | D1 投研、会议 | internal_contract（100ms/0.1%） | true | true | draft |
| 7 | `d1.decision.create` | POST | `/api/d1/decision` | D1 | 门户、会议、WA1 | write_ledger（500ms/0.5%） | true | true | draft |
| 8 | `d1.decision.list` | GET | `/api/d1/decisions` | D1 | 门户、会议 | read_fast（300ms/1%） | false | true | draft |
| 9 | `d1.meeting.get` | GET | `/api/d1/meeting` | D1 | 会议、门户 | read_fast（300ms/1%） | false | true | draft |
| 10 | `d1.meeting.create` | POST | `/api/d1/meeting` | D1 | 会议、门户 | write_gate（800ms/0.5%） | true | true | draft |
| 11 | `d1.handoff.create` | POST | `/api/d1/handoff` | D1 | WA1、WA2、WA3、TR1、WATCH_TOWER、门户 | write_ledger（500ms/0.5%） | true | true | draft |
| 12 | `d1.handoff.list` | GET | `/api/d1/handoffs` | D1 | 门户、WA1、TR1 | read_fast（300ms/1%） | false | true | draft |
| 12b | `d1.watch_plans.list` | GET | `/api/d1/watch-plans` | D1 | 门户、WA1、WA2、WA3、WATCH_TOWER | read_fast（300ms/1%） | false | true | draft |
| 13 | `watch.plan.create` | INTERNAL | `internal:watch/plan` | WA1 | WA2、WA3、WATCH_TOWER、确认门 | internal_contract（100ms/0.1%） | true | true | draft |
| 14 | `watch.trigger.create` | INTERNAL | `internal:watch/trigger` | WA1 | 确认门、TR1 | internal_contract（100ms/0.1%） | true | true | draft |
| 15 | `confirm.create` | POST | `/api/sim/confirm` | TR1 | HUMAN、TR1、paper | write_gate（800ms/0.5%） | true | true | draft |
| 16 | `confirm.list` | GET | `/api/sim/confirmations` | TR1 | 门户、HUMAN | read_fast（300ms/1%） | false | true | draft |
| 17 | `paper.execution.record` | INTERNAL | `internal:paper/execution` | TR1 | paper 账本、outcome、门户 | write_ledger（500ms/0.5%） | true | true | draft |
| 18 | `paper.order` | POST | `/api/paper_order` | TR1 | paper 账本、门户 | write_gate（800ms/0.5%） | true | true | deprecated |
| 19 | `paper.direct` | POST | `/api/paper_direct_open` | TR1 | paper 账本 | write_gate（800ms/0.5%） | true | true | deprecated |
| 20 | `paper.stop` | POST | `/api/paper_stop` | TR1 | paper 账本 | write_gate（800ms/0.5%） | true | true | deprecated |
| 21 | `paper.status` | GET | `/api/quant/paper` | TR1 | 门户、D1 | read_fast（300ms/1%） | false | true | draft |
| 22 | `paper.exchange.health` | GET | `/paper_exchange/health` | TR1 | 门户、WATCH_TOWER | read_fast（300ms/1%） | false | true | v1 |
| 23 | `paper.exchange.snapshot` | GET | `/paper_exchange/snapshot` | TR1 | 门户、WATCH_TOWER | read_fast（300ms/1%） | false | true | v1 |
| 24 | `outcome.get` | INTERNAL | `internal:d1/outcome` | D1 | 训练、数据仓、校准 | internal_contract（100ms/0.1%） | true | true | draft |
| 25 | `training.episode.get` | GET | `/api/d1/training/episodes` | D1 | 训练、数据仓 | read_fast（300ms/1%） | false | true | draft |
| 26 | `mcp.d1-research` | MCP | `mcp:d1_research` | D1 | HERMES、军团 Agent | mcp_tool（3000ms/2%） | false | true | draft |
| 27 | `composition.interface.list` | MANIFEST | `manifest:compositions/*/manifest.yaml` | A0 | ARCH1、DEV、平台 | manifest（100ms/0.1%） | false | true | draft |
| 28 | `watchtower.membership.get` | GET | `/api/watch-tower/strategy-pool/membership` | WATCH_TOWER | WA1、门户 | read_fast（300ms/1%） | false | true | draft |
| 29 | `health.probe.get` | GET | `/api/health` | A0 | 门户、运维、守护器 | read_fast（300ms/1%） | false | true | draft |
| 30 | `health.landing.get` | GET | `/health` | A0 | 监控、nginx | read_fast（300ms/1%） | false | true | draft |
| 31 | `war_room.snapshot.get` | GET | `/api/war-room/snapshot` | WATCH_TOWER | 门户、WATCH_TOWER、D1 | read_heavy（1500ms/2%） | false | true | draft |
| 32 | `acad1.research.pool` | GET | `/quant-api/api/v1/acad1-research/pool` | ACAD1 | ACAD1 研究台、门户 | read_heavy（1500ms/2%） | false | true | draft |
| 33 | `acad1.research.stats` | GET | `/quant-api/api/v1/acad1-research/stats` | ACAD1 | ACAD1 研究台、门户 | read_heavy（1500ms/2%） | false | true | draft |
| 34 | `acad1.research.reviews` | GET | `/quant-api/api/v1/acad1-research/reviews` | ACAD1 | ACAD1 研究台、门户 | read_heavy（1500ms/2%） | false | true | draft |
| 35 | `acad1.research.decisions` | GET | `/quant-api/api/v1/acad1-research/decisions` | ACAD1 | ACAD1 研究台、门户 | read_heavy（1500ms/2%） | false | true | draft |
| 36 | `acad1.research.evidence` | GET | `/quant-api/api/v1/acad1-research/evidence` | ACAD1 | ACAD1 研究台、门户 | read_heavy（1500ms/2%） | false | true | draft |
| 37 | `acad1.research.calendar` | GET | `/quant-api/api/v1/acad1-research/calendar` | ACAD1 | ACAD1 研究台、门户 | read_heavy（1500ms/2%） | false | true | draft |

> 统计：draft 30 / v1 5 / deprecated 3 / retired 0；write=true 12；read-only 26；**paper_only 全 38 = true（平台 paper-only 铁律）**。

---

## 三、owner 覆盖（无空洞校验）

| owner | 席位角色 | 接口数 | 接口清单 |
|---|---|---|---|
| ACAD1 | academy_director | 7 | acad1.pool.get、acad1.research.pool、acad1.research.stats、acad1.research.reviews、acad1.research.decisions、acad1.research.evidence、acad1.research.calendar |
| DATA1 | data_team_lead | 2 | dw.query.get、dw.query.post |
| STRAT1 | strategy_researcher | 1 | strategy.list |
| D1 | d1_decision | 11 | d1.research.get、d1.case.upsert、d1.decision.create、d1.decision.list、d1.meeting.get、d1.meeting.create、d1.handoff.create、d1.handoff.list、outcome.get、training.episode.get、mcp.d1-research |
| TR1 | trade_executor | 9 | confirm.create、confirm.list、paper.execution.record、paper.order、paper.direct、paper.stop、paper.status、paper.exchange.health、paper.exchange.snapshot |
| WA1 | watch_monitor | 2 | watch.plan.create、watch.trigger.create |
| WATCH_TOWER | watchtower_arbiter | 2 | watchtower.membership.get、war_room.snapshot.get |
| A0 | product_rd_chief | 3 | composition.interface.list、health.probe.get、health.landing.get |

校验结果：**38 接口全部有 owner；全部 owner 在 registry `owners` 目录内（8 席）**；无 owner 空洞。

---

## 四、consumer 覆盖（关键链路）

| consumer | 消费接口 | 说明 |
|---|---|---|
| D1 | acad1.pool.get、strategy.list、paper.status | 投研/裁决输入 |
| WA1/WA2/WA3/WATCH_TOWER | d1.handoff.create、d1.handoff.list、confirm.create、confirm.list、watchtower.membership.get | 盯盘/确认 |
| TR1 | d1.handoff.create、confirm.create、confirm.list、paper.*、outcome.get | 模拟交易所 |
| 门户（8734） | 全部只读接口（含 war_room.snapshot.get 作战台聚合） | 人类入口唯一 8734 |
| WATCH_TOWER / D1 | war_room.snapshot.get | 作战台实时只读聚合（D1 可消费） |
| ACAD1 研究台 / 门户 | acad1.research.*（6） | 司马徽研究台只读消费（8105 原生 JWT / 8734 反代 auth_request） |
| HERMES | d1.research.get、mcp.d1-research | MCP 只读消费 |
| 数据仓/训练 | outcome.get、training.episode.get、dw.query.* | 回放/训练 |
| 组合/ARCH1/平台 | composition.interface.list、strategy.list、dw.query.* | 组合登记/治理 |

校验结果：**38 接口全部 consumer ≥1**；关键链路（池→D1→WA→确认→paper→outcome）每跳至少 1 个消费方（缺 watch_plan/trigger 的生产接口为内部契约，见 ID chain 空洞表）。

---

## 五、SLO 与状态分布

| 档位 | 接口数 | 接口 |
|---|---|---|
| read_fast | 13 | acad1.pool.get、strategy.list、d1.decision.list、d1.meeting.get、d1.handoff.list、confirm.list、paper.status、paper.exchange.health、paper.exchange.snapshot、training.episode.get、watchtower.membership.get、health.probe.get、health.landing.get |
| read_heavy | 10 | dw.query.get、dw.query.post、d1.research.get、war_room.snapshot.get、acad1.research.pool、acad1.research.stats、acad1.research.reviews、acad1.research.decisions、acad1.research.evidence、acad1.research.calendar |
| write_ledger | 2 | d1.decision.create、d1.handoff.create |
| write_gate | 5 | d1.meeting.create、confirm.create、paper.order、paper.direct、paper.stop |
| internal_contract | 5 | d1.case.upsert、watch.plan.create、watch.trigger.create、paper.execution.record、outcome.get |
| mcp_tool | 1 | mcp.d1-research |
| manifest | 1 | composition.interface.list |

> 精确数量以 registry 为准（契约测试用 counts 锚点校验：interfaces=38、write=12、read=26）。

状态说明：

- `v1`（5）：dw.query.get/post、strategy.list、paper.exchange.health/snapshot —— 已带 schema_version/只读/真源；仍待按 IFS-03 补齐 request_id 与 source 对象后由 registry 复核（F1）。
- `draft`（30）：已存在或规划中，未满足 v1 全部条件（缺 envelope/ID/契约测试/登记）；其中 war_room.snapshot.get 已完成 F1d envelope 接线，待独立 schema 与 Q2 证据后升 v1；acad1.research.*（6）为本地契约 `acad1_research/v1`（无独立 JSON Schema/schema_file、无 envelope），升 v1 需补 schema/envelope/证据。
- `deprecated`（3）：paper.order / paper.direct / paper.stop —— 多入口收敛（F4），迁移期内只兼容不扩展。
- `retired`（0）。

---

## 六、空洞与整改

| 缺口 | 影响接口 | 整改 |
|---|---|---|
| confirmation 已承接四 ID（WIRE 收口） | confirm.create/list | F4 已完成，保留 envelope 整改 |
| paper 多入口 + 无 trade_id | paper.* | F4 `WO-IFS-PAPER-20261103` |
| outcome 无 outcome_id | outcome.get | F5 `WO-IFS-OUTCOME-20261103` |
| training episode 缺 | training.episode.get | F5 `WO-IFS-TRAIN-20261103` |
| MCP 未登记 servers.json | mcp.d1-research | F6 `WO-IFS-MCP-20261103` |
| composition 未暴露全部接口 | composition.interface.list | F6 `WO-IFS-COMPOSITION-20261103` |
| war_room 无独立 JSON Schema/schema_file | war_room.snapshot.get | F1 后续补 schema（F1d 仅完成 envelope 接线；本单只登记） |
| ACAD1 research 6 接口无独立 JSON Schema/envelope（本地契约） | acad1.research.*（6） | F1 后续补 `acad1_research/v1` schema/envelope；evidence 依赖 C:\Temp 文件（404/503 已诚实）；401 由 8105 JWT/8734 auth_request 承担 |
| envelope 未统一 | 除 D1（F1b）/作战台（F1d）外的 draft 接口 | F1 `WO-IFS-ENVELOPE-20261103` |

---

## 七、维护规则

1. 新增接口必须先在本矩阵/YAML 登记（IFS-02），owner/consumer/SLO 缺一不接单；
2. owner 变更（席位调动）需同批更新 registry `owners` 与本矩阵（IFS-08）；
3. SLO 调整需 ADR 或季度 kernel review 记录，并刷新 `slo_classes`；
4. 契约测试（`tools/tests/test_interface_registry_contract.py`）校验：接口数、owner 在册、consumer 非空、SLO 命中档位、paper_only 恒真、status 合法。

## 八、变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-11-03 | 首版：31 接口矩阵、8 席位 owner、7 档 SLO、空洞与整改映射 |
| v1.1 | 2026-11-04 | WO-IFS-REGISTRY-WARROOM-20261104：登记 F1d `war_room.snapshot.get`（GET `/api/war-room/snapshot`，owner WATCH_TOWER，consumer 门户/WATCH_TOWER/D1，schema war_room/v1，source_kind=aggregate，read-only，paper_only，read_heavy，status draft，ids 空）→ **接口数 31→32**（draft 23→24、read 19→20、WATCH_TOWER 1→2）；registry_version 1.0.0→1.1.0。回滚：`git revert <本单 commit>`（3 文件：registry YAML / 本矩阵 / tools/tests 契约测试），再跑 `pytest tools/tests/test_interface_registry_contract.py tools/tests/test_interface_schema_contract.py -q` 与 `composition.py validate --all` 复核。 |
| v1.2 | 2026-11-04 | WO-ACAD1-INTERFACES-20261104：登记司马徽军团（ACAD1）6 个 research 接口 `acad1.research.{pool,stats,reviews,decisions,evidence,calendar}`（GET `/quant-api/api/v1/acad1-research/*`，8105 原生 `/api/v1/acad1-research/*`；owner ACAD1，consumer ACAD1 研究台/门户，schema `acad1_research/v1` 本地契约，auth portal-token（8105 JWT/SSO、8734 auth_request），read-only，paper_only，SLO read_heavy，status draft，ids 空）→ **接口数 32→38**（draft 24→30、read 20→26、ACAD1 1→7、read_heavy 4→10）；registry_version 1.1.0→1.2.0。回滚：`git revert <本单 commit>`（4 文件：registry YAML / 本矩阵 / acad1-legion manifest / tools/tests 契约测试），再跑契约测试与 `composition.py validate --all` 复核。 |
