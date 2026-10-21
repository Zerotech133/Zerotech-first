# GOV-MIN 增量证据索引｜2026-10-18

> 性质：仅依据既有治理文档的增量定位；不是运行态检查、账本快照或审批记录。原 `evidence-index-2026-10-17.md` 是其生成时的历史时间切片，**不覆盖、不原位改写**。本索引不读取或解析账本，不重跑 route check，不执行服务/测试。状态 `DELIVERED` 只表示指定文档存在且可审阅；`STATIC_QA` 只表示静态复核，不等于功能测试通过或生产批准。

## 1. 工单交付与验收（以本时间切片为准）

| 工单 | 新增证据（相对 `docs/governance/`） | 交付与验证范围 | 当前验收/门禁 |
|---|---|---|---|
| GOV-MIN-001 | `evidence-index-2026-10-17.md` | 10/17 初版索引已交付；其 MISSING 只适用于当时。 | 历史文档 QA 已完成；新增文档须另建增量记录，不能覆盖初版。 |
| GOV-MIN-002 | `kg-root-selector-offline-test-plan-2026-10-18.md` | 五场景验收矩阵已交付；选择器源码访问审批超时、契约未提供，pytest 不可用，未执行测试。 | 设计已交付，**功能验收 BLOCKED / NOT_EXECUTED**。待授权契约和隔离环境。 |
| GOV-MIN-003 | `path-migration-matrix-2026-10-18.md` | E 盘旧路径静态矩阵已交付，并完成独立静态 QA；124 为非穷尽审阅条目数，非全仓字面量数。 | 文档静态审阅完成；路径迁移、目标等价性及功能回归均 **BLOCKED**。 |
| GOV-MIN-004 | `portal-route-review-2026-10-18.md`；历史执行记录 `portal-qa-2026-10-17.txt` | 封存的 72→106 / 34 字面量逐条静态审阅并独立复核；10/17 的 72→94 / 22 为另一历史切片。 | 文档静态审阅完成；34 条 owner 均未签认、均未批准，23/34 未对账；不证明生产可达、导航消费、鉴权或运行回归。 |
| GOV-MIN-005 | 无候选投影交付；依据 `rebaseline-001-decision-record-2026-10-18.md` | 原计划投影被后续保全决策禁止。 | **BLOCKED / 不得追补投影**，不得读取、解析、校验或修改冻结账本。 |
| GOV-MIN-006 | `service-runbooks-draft-2026-10-18.md` | 8787/8788/8791/8093 四端口历史证据草稿已交付并独立静态 QA。 | 文档草稿完成；当前健康、真实启动命令、隔离及 8093 服务身份仍 UNVERIFIED；非可执行启停手册。 |
| GOV-MIN-007 | 尚无已交付环境报告或契约测试矩阵 | 独立 Agent Platform 树的 Python/pytest/依赖/权限门槛未获核验。 | **BLOCKED / NOT_TESTED**；不得宣称契约测试通过。 |

## 2. 事实口径及过时工单风险

1. `next-minimal-workorders-2026-10-17.md` 的“可立即执行”是旧派单状态；其 GOV-MIN-004 `94/72/22` 为 10/17 切片，不能替换 10/18 封存的 `72/106/34`。人工决策仍保留历史“23 条新增”，23 与 34 尚未对账；**正式路由基线仍为 72 条**，不生成 candidate 或新基线。
2. 同一旧工单将 GOV-MIN-005 描述为可执行只读投影，已被 `rebaseline-001-decision-record-2026-10-18.md` 的禁止投影/读取账本决定覆盖；禁止以旧卡片获取执行权限。
3. 10/17 索引把当时不存在的 003/004 专项报告/006 标成 MISSING，不能推断上述 10/18 新文件仍缺失；007 当前仍缺交付。文件名由实际 10/18 派单确定，不冒充旧文件名。旧索引保持原样，后续变更只追加新版本及证据链接。
4. `rebaseline-001-freeze-gate-2026-10-18.md` 为控制面待签门禁，自检/独立文档 QA 不等于写入来源已冻结。REBASELINE-001 维持 `DRIFT_DETECTED / HUMAN_REVIEW_REQUIRED / CONTINUOUS_LEDGER_DRIFT / BLOCKED`。只有 owner/writer、批准窗口、冻结证明、路由口径人工复核与独立 QA 全部满足后，才能讨论控制面 `READY_FOR_APPROVAL`；该状态本身不授予执行权。

## 3. 后续待批安全工单索引

`portal-safety-change-requests-2026-10-18.md` 提供两项**独立**待批变更：模型 POST `/api/audit_chat` 与 `/api/ai_duty` 的鉴权/频控/body 上限；有状态 GET `/api/ops_today` 的状态与缓存契约。仅设计、不得直接修改代码、发布、运行探针或扩大路由正式基线。

## 4. 增量索引自检边界

本索引创建时仅静态核对工单编号、文档路径、状态词及旧/新时间切片；以下并行测试为后续新增证据，不追改原始封存。未发生生产 HTTP 请求、账本 I/O、来源冻结证明、生产可达性验证或变更批准。若证据发生冲突，新增带时间戳的审阅记录并维持 `BLOCKED`，不覆盖旧索引或封存包。

## 5. 16:xx 并行开发增量证据（非生产验收）

| 工作线 | 新增文件 | 隔离执行结果 | 解释与状态 |
|---|---|---|---|
| SEC-PORTAL-01 模型入口离线安全回归 | `portal/test_model_endpoint_guard_offline.py` | 受管 Python + pytest 9.1.1 定向执行 `6 passed, 8 xfailed`；两路径各有鉴权、独立限流、体积/负数 Content-Length 四类 strict xfail | `TEST_DESIGN_EXECUTED / PRODUCT_BUG_NOT_FIXED`；AST 抽取门户分发、请求桩，不连接真实模型/HTTP。既有客户端契约与共享写端点集合隔离保持，待 owner/审批。 |
| SEC-PORTAL-02 运营状态离线回归 | `quant_backtest/tests/test_ops_today_isolated.py` | 受管 Python + pytest 9.1.1 定向执行 `2 passed, 3 xfailed`；GET 带写与同名池缓存的重读次数/asof 三判据 strict xfail | `TEST_DESIGN_EXECUTED / PRODUCT_BUG_NOT_FIXED`；AST 提取计算函数仅注入临时路径与 mock，未访问 E 盘数据。写状态拆分与缓存修复待 owner/审批。 |

两测试文件均为新建未跟踪文件，不代表已提交、合并或部署。上述 xfailed 是**明确证明当前实现尚未满足目标契约**，不得作为功能测试通过或安全变更放行；测试期间保持正式 72 条路由基线及 REBASELINE-001 `BLOCKED`。
