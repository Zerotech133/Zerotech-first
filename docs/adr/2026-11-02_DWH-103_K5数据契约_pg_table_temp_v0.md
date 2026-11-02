# ADR（Proposed）：DWH-103 · K5 数据契约补齐——dataset_registry 增 `pg_table` 与 `C:\Temp` 契约产物

- **状态**：**Proposed（2026-11-02 草案；未生效）**——待 ARCH1 预审 + **A0 拍板**；本 ADR **不实现、不改运行时**，不写 `tools/`、`platform/`、registry/schema 文件
- **建议编号**：ADR-019（待 A0 分配；本文件按 task-13 任务单命名 `2026-11-02_DWH-103_K5数据契约_pg_table_temp_v0.md`，避免与在途 ADR 编号冲突）
- **日期**：2026-11-02
- **关联**：ADR-015（K5 数据/证据层；§5 单一真源、§6 声明被消费、§7 变更纪律）、ADR-017（§6 禁止把 `C:\Temp\*.json` 当契约）、`platform/kernel_v1.yaml` K5（owner DATA1，status partial）、ADR-010（凭据）、ADR-012（L2 五要素）、ADGS 试点 DWH-103（治理道）、前置 DWH-101/102/104（已交付证据）、ACL-103 ADR（ACAD1 池契约，同批依赖）
- **性质**：治理道**预审材料**；定义 K5 数据契约补齐的 schema/登记/口径，不写实现、不写 registry/schema 文件

## 一、背景与问题

`data-warehouse` 组合的 K5"数据/证据"层当前只覆盖**文件级**登记，两个关键载体在登记之外，导致"声明可解析、事实不可治理"：

1. **无 `pg_table` 载体**：`tools/datawarehouse/dataset_registry.py` 从 inventory（`research/reports/dataset_inventory_*.json`）生成 314 条登记，`by_kind` 实测 `dir 258 / sqlite_table 42 / corpus_layer 14`；**kind 里没有 `pg_table`**。智策 PG 5446 `zhice_os_v2` 的 **83 张 `v2_*` 业务表**全部在登记外（含 ACAD1 池 `v2_acad1_pool`、`v2_selection_pool`、`v2_stock_research` 等被组合 manifest 引用的表）。`platform/kernel_v1.yaml:156` 明记：**"文件级；缺 pg_table 与 C:\Temp 覆盖（P2）"**。
2. **`C:\Temp` 契约产物 0 覆盖**：`C:\Temp\acad1_evidence_bundle.json`、`C:\Temp\zhice_job_status.json` 等 **10+ 契约文件**被跨系统裸读，无 schema/版本/归属/保留期；`dataset_registry.py` 的 `ALLOWED_ROOTS` **不含 `C:\Temp`**，无法登记/采样；同时 `composition.py` 的 ref 解析对 `dataset/file/*` **直接回退探测 `C:\Temp\{token}.json`**（`tools/composition.py:222`）——"临时目录当契约"的链路已隐式存在，与 ADR-017 §6 明令冲突。
3. **声明与登记不一致（软校验掩盖缺口）**：`data-warehouse/manifest.yaml` 已声明 GAP ref `dataset/pg/v2_zhice_tables`（智策 83 张 `v2_*` 表）与 `dataset/file/temp_contract`（`C:\Temp` 契约产物），并写整改期 2026-11-12（DWH-103）；但组合门对 `dataset/pg/*` 的判定是"`v2_*` 命名即通过"（`composition.py:228-229`），对 `dataset/file/*` 回退探 Temp 路径——**缺口被软校验吞掉**，`validate --all` 仍可 6/6 通过。
4. **无 dataset schema 可冻结**：`platform/kernel_v1.yaml:160-163` 把 `platform/schemas/dataset.schema.json` 列为 planned（"P2：含 pg_table/文件/契约产物三类"），但 `platform/schemas/` 目录**当前不存在**（2026-11-02 只读实测）。没有 schema 就没有 fail-visible 的登记契约，`dataset_registry.py`（kernel contract）的每次改动只能靠人眼。
5. **不决策的代价**：登记率 84.4%（265/314）的口径里**不包含** PG/Temp，报"登记 95%/覆盖 80%"的 SLA 永远无法真实达成；ACAD1/D1 等组合的 `dataset/pg/*` 引用无法被机器验证；Temp 文件被清理/改写无感知（`rc=0` 假绿）。

## 二、决策（Proposed，D1–D8）

### D1 扩展 dataset 载体 kind：`pg_table` + `contract_artifact`

- 在现有 `kind` 枚举上**只加两类**：`pg_table`（PG 5446 业务表）、`contract_artifact`（`C:\Temp` 等处的进程间契约产物）。
- 不新增第三种容器（不把 JSON 契约硬塞成 `dir`/`sqlite_table`）；不改现有三类语义。
- 两类共用 `platform/schemas/dataset.schema.json`（D7）的 `oneOf` 判别。

### D2 🔴 `pg_table` 登记模型（字段与口径）

| 字段 | 值/口径 | 说明 |
|---|---|---|
| `dataset_id` | `pg_<schema>_<table>`（如 `pg_public_v2_acad1_pool`） | 与 manifest ref `dataset/pg/v2_acad1_pool` 的 token 对齐 |
| `kind` | `pg_table` | 新增 |
| `connector` | `pg@127.0.0.1:5446/zhice_os_v2`（只读） | 只读 DSN 走环境变量 `ZHICE_V2_DSN`，**不入库、不落盘** |
| `physical` | `{schema, table}` | `information_schema.tables` 只读探测 |
| `owner` | 按表归属（ACAD1/D1/DATA1/STRAT1…） | 现由 `OWNER_HINTS` 路径推断，PG 表需显式声明，禁止猜 |
| `registered`/`exposed` | 登记/是否有业务取数入口（API/MCP/CLI/SQL） | 与 DWH-104 口径一致 |
| `access` | `read_only` | 连接层强约束（只读账号/SELECT only） |
| `freshness` | **v0 不声明**（null） | PG 表无统一"数据日"列；不允许用 `pg_stat_user_tables.last_autoanalyze` 冒充业务新鲜度；新鲜度须逐表在 v1.1 由 owner 声明口径 |
| `row_estimate` | 可空，`pg_class.reltuples` 只读 | 仅参考值，不参与 SLA |

- **连接器安全**：只读、超时 5s、失败 **fail-visible**（`exit 2`/显式 `connector_unavailable`，不静默跳过）；缺失 DSN → 报 `missing_dsn` 而不是空登记（与 parked 探针 `WO-ZHICE-V2-PROBE-20261101` 的 `env ZHICE_V2_DSN 缺失 exit2` 约定一致）。
- **默认 schema 白名单**：`zhice_os_v2` 的 `public`；跨库/其他 schema 需 A0 加白。

### D3 🔴 PG 登记范围与波次（避免一次 83 张全量爆炸）

| 波次 | 范围 | 验收 |
|---|---|---|
| **wave1（试点内，DWH-103）** | 组合 manifest 已引用 + ACAD1/D1 核心表：`v2_acad1_pool`、`v2_selection_pool`、`v2_stock_research`、`v2_base_signal`、`v2_selection_report` 等（**建议 ≤15 张，以清单为准**） | 每张可 `get/sample`（只读）；登记条目进 registry；组合 ref 从"命名软过"变"登记命中" |
| **wave2（试点后）** | 其余 `v2_*` 表（83 张全量） | 83/83 登记；`dataset/pg/*` ref 解析改为严格模式 |

- 全量清单必须由**只读 probe 生成**（不写 PG、不启停服务），清单作为 wave 分工依据；wave1 未覆盖表在 registry 中显式标 `planned`，不得留空。

### D4 🔴 `C:\Temp` 契约产物登记模型（白名单，不扩根目录）

- **不把 `C:\Temp` 加入 `ALLOWED_ROOTS`**（世界可写、无归属、可被任意进程覆盖，扩根=把采样器变成任意文件读入口）。
- 改为**显式白名单清单**：`contract_artifact` 条目必须来自一份受控清单（建议 `agent_platform/catalogs/contracts/temp_contract_manifest_v1.yaml` 或等价物，**实现单创建，不在本 ADR**），每条含：`artifact_id`、`path`（绝对路径，仅 `C:\Temp\<固定名>` 形式）、`owner`、`produced_by_job`、`schema_ref`（如 `acad1-evidence/v1`）、`retention_days`、`sha256`（可空，首次登记时计算）、`access: read_only`、`status: active|retired|missing`。
- 采样/校验只对白名单条目生效，**禁止递归扫描 `C:\Temp`**；文件缺失 → `status=missing` **fail-visible**（告警），不静默丢弃。
- 过渡口径：v0 允许 Temp 原地登记（不迁移）；**迁移到契约目录**（建议 `agent_platform/catalogs/contract_artifacts/` 或统一契约目录，P2 拍板）后 `path` 改指新位置，`contract_artifact` 类型不变。与 ADR-017 §6 的"Temp 不作契约"目标一致，迁移完成前以"明示白名单 + fail-visible"兜底。

### D5 dataset_id ↔ manifest ref 命名规范（让声明可机器解析）

- **ref 权威格式**（沿用现有 manifest 写法）：
  - `dataset/pg/<token>` ↔ registry `kind=pg_table`，`token = <table>`（例：`dataset/pg/v2_acad1_pool`）；
  - `dataset/file/<token>` ↔ registry `kind=contract_artifact`（`token = artifact_id`，例：`dataset/file/temp_contract` 为**聚合 GAP ref**，登记后拆成每条 `contract_artifact` 的具体 artifact_id）；
  - `dataset/dir/<token>`、`dataset/sqlite_table/<token>`、`dataset/registry/<token>`、`dataset/corpus_layer/<token>` 沿用现状。
- registry 每条登记增加 `ref_aliases: [..]` 字段，承载 manifest 引用 token（如 `v2_acad1_pool`、`dataset_registry`、`temp_contract`）——**ref 解析从"硬编码猜测"收敛为"registry 权威查表"**。
- 目标态（wave2 后）：`composition.py` 删除 `kind=pg → 任意 v2_* 通过` 与 `kind=file → 探 C:\Temp\{token}.json` 两处启发式，改为严格登记命中；**过渡期保持软校验+warning**，避免在途 manifest 被误杀（P5 拍板 staged 时机）。

### D6 🔴 stats 口径版本化（保护 314/265/72 基线不被静默改变）

- 现有 `DatasetRegistry.stats()` 输出 `total/registered/exposed/by_domain/by_kind`，被 `/api/dw/stats`、DWH-104 报告、DWH-102 manifest baseline、ACL-103 ADR 引用；**v0 314/265/72 不得无版本地改变**。
- 方案：保留 `stats_v1`（文件级 314 口径，字段不变），新增 `stats_v2`：
  - `file_inventory: {total, registered, exposed, by_kind}`（=v1 原样）
  - `pg: {total, registered, exposed, connector_status}`
  - `contract_artifacts: {total, active, missing}`
  - `all: {total, registered, exposed}`（合并口径）
  - `denominator_version: "v2"` + `generated_at` + `connector_status`
- `/api/dw/stats` 只**新增**字段、不改既有字段（向后兼容）；SLA"登记 95%/覆盖 80%"的分子分母是否切到 v2 口径需 A0 确认（P3），切换前继续按 v1 口径报基线并在周报注明。

### D7 🔴 `platform/schemas/dataset.schema.json` v0.1 与登记 YAML v2

- 新建（实现单）`platform/schemas/dataset.schema.json`，`oneOf` 判别 `kind`：
  - 公共必填：`dataset_id`、`kind`、`domain`、`owner`、`registered`、`exposed`、`access`、`ref_aliases`、`status`；
  - `pg_table` 额外：`connector`、`physical.{schema,table}`；
  - `contract_artifact` 额外：`path`、`produced_by_job`、`schema_ref`、`retention_days`、`sha256`（可空）；
  - `dir/sqlite_table/corpus_layer` 沿用现状字段。
- 登记 YAML（`agent_platform/catalogs/dataset_registry_v1.yaml`）升 **v2**：新增 `pg_tables`、`contract_artifacts` 两节 + `stats_v1/stats_v2`；v1 文件保留可回滚。
- schema 冻结进 K5（`status: planned → existing`）后，`kernel_guard` 对 `dataset_registry.py`/`dataset.schema.json` 变更要求 ADR 号（ADR-015 §7）。

### D8 门禁与执行边界

- `dataset_registry.py` 属 K5 `kind: contract`（`kernel_v1.yaml:153-156`）→ 改动须带 ADR 号（kernel_guard；ADR-015:75）。
- `metadata_consistency.py` 当前检查 catalog×registry×lifecycle×物理 store（`tools/datawarehouse/metadata_consistency.py:1-11`），**不检查 dataset 级登记**；v0 只做"新增只读校验 + 定向测试"，**不扩张阻断门禁**（门禁强度 P5 拍板，避免未批 fail-closed 影响在途 merge）。
- 安全边界：只读连接/只读文件；不写 PG、不写 `C:\Temp`、不迁移文件（迁移属 P2/L2）；PG 凭据走 env（ADR-010 凭据区，L2 五要素另批）；数据集登记 YAML 由生成器 `emit_yaml` 重建（`dataset_registry.py:227-247`），不改手工数据。

## 三、数据源与口径（只读证据，详见交付证据文件）

| 事实 | 口径/数值 | 来源（file:line） |
|---|---|---|
| 数据集总量/登记/入口 | **314 / 265（84.4%）/ 72（22.9%）**；四域 241/19/29/25；四象限 52/213/20/29 | `deliverables/2026-11-02_数据仓暴露面盘点_v1.md:11-22,70-100`；`2026-11-02_DWH-101-104_证据_v1.md:88-90` |
| 载体 kind | `dir 258 / sqlite_table 42 / corpus_layer 14`（**无 pg_table**） | 同上 report:85-90；只读实测 `DatasetRegistry().stats()`（2026-11-02 09:2x） |
| PG 缺口 | 智策 **83 张 `v2_*` 表**在登记外；`dataset_registry.py` 无 PG 连接器 | `deliverables/2026-11-02_松耦合技术框架平台_固化可行性研究_v1.md:64`；`compositions/data-warehouse/manifest.yaml:33-34` |
| Temp 缺口 | `C:\Temp` 契约文件 **10+**、0 覆盖；`ALLOWED_ROOTS` 无 `C:\Temp` | 研究:85；`tools/datawarehouse/dataset_registry.py:18-29` |
| 隐式 Temp 依赖 | 组合门 ref 解析对 `dataset/file/*` **回退探测 `C:\Temp\{token}.json`**；`dataset/pg/*` 任意 `v2_*` 软过 | `tools/composition.py:210-231`（:222、:228-229） |
| K5 缺口记录 | "文件级；缺 pg_table 与 `C:\Temp` 覆盖（P2）"；`dataset.schema.json` planned（pg/文件/契约产物三类） | `platform/kernel_v1.yaml:148-166`（:156、:160-163） |
| schema 现状 | `platform/schemas/` **不存在**（Test-Path=False） | 2026-11-02 只读实测（证据文件） |
| 组合声明 | GAP ref `dataset/pg/v2_zhice_tables` + `dataset/file/temp_contract`，整改期 **2026-11-12（DWH-103）** | `compositions/data-warehouse/manifest.yaml:33-36,52-55`；`2026-11-02_DWH-102_证据_v1.md` §一#2 |
| 登记口径真源 | `dataset_inventory_20261102.json`（SHA256 `6D162E1E…B3C1A`）+ `dataset_registry_v1.yaml`（`generated_at 02:48:20`）+ `/api/dw/stats` 四源对账 | DWH-104 report:58-64；DWH-102 证据开头 |
| 隐含契约链路 | 智策只读探针约定：`ZHICE_V2_DSN` 缺失 `exit 2`（不连库不假绿） | parked 任务 `WO-ZHICE-V2-PROBE-20261101`（`deliverables/2026-11-02_W0_parked定性清单_v1.csv` 第 8 行） |
| 契约 lint 缺口 | `datacontract-cli` 未安装（属 L2 工具引入），ODCS lint 当前 `pending_wiring` | `tools/seat_capability_inject.py:924-925` |

## 四、与 ADR-015 / K5 的关系

- **K5 定义**（ADR-015:48）："数据/证据：dataset registry 契约（含 pg_table）、数据契约、证据包 schema、血缘"——本 ADR 补的正是 K5 明列而当前缺失的"含 pg_table"与"证据包/契约产物"。
- **kernel_v1 K5**（:148-166，owner DATA1）：`dataset_registry.py`=contract（existing）、`dataset.schema.json`=schema（planned）、`evidence_bundle.schema.json`=schema（planned）；本 ADR 的 D7 是这两条 planned 的落地定义；D8 是 kernel_guard 的适用范围确认。
- **ADR-015 §5 单一真源**（:65）：登记以 dataset_registry 为唯一来源，禁止再出现旁路登记处（例如散落脚本里硬编码 `C:\Temp` 路径、`v2_*` 命名约定推断）。
- **ADR-015 §6 声明被执行点消费**（:67）：`dataset/pg/*`、`dataset/file/*` 的 manifest 声明目前由启发式"消费"，本 ADR 改为 registry 权威解析（D5）。
- **ADR-015 §7 变更纪律**（:75）：内核契约文件改动带 ADR；emergency 24h 补 ADR——本 ADR 即 `dataset_registry.py` 后续改动的 ADR 依据。
- **ADR-017 §6**（:26）：禁止 `C:\Temp` 当契约——D4 的白名单+fail-visible 是过渡，迁移契约目录是目标。
- **与 ACL-103 ADR（建议同批）**：ACAD1 池契约声明了 `dataset/pg/v2_acad1_pool`、`dataset/file/acad1_evidence_bundle` 等 ref；本 ADR 提供解析基础。两份 ADR 同批预审/拍板可避免"契约 Accept 了但 ref 解析不了"的中间态（P8）。

## 五、影响面

| 面 | 影响 | 是否改变运行时 |
|---|---|---|
| 代码 | `tools/datawarehouse/dataset_registry.py`（新 kind/连接器/白名单/ref_aliases/stats_v2）、`emit_yaml`、定向测试；**实现单另开** | 否（本 ADR 不写） |
| API | `/api/dw/stats` 只增字段；`/api/dw/list|get|sample` 对新 kind 支持；DWH-105 的 `data_date` 字段口径协同 | 否 |
| 契约/schema | 新增 `platform/schemas/dataset.schema.json` v0.1；`dataset_registry_v1.yaml` v2；K5 状态 planned→existing；`contract_artifact` 白名单清单 | 否 |
| 门禁 | `kernel_guard` 覆盖 `dataset_registry.py`/schema；`composition.py` 的 dataset ref 判定 staged 严格化（先 warning 后 block） | 否（门禁生效在实现后） |
| 消费者 | 数据仓页面/`/api/dw/*`、DWH-104 口径与周报、`data-warehouse` manifest SLA、ACAD1/D1 组合 dataset refs、ACL-103 契约 | 否 |
| 安全 | 只读 PG 连接（env 凭据，L2 边界）；`C:\Temp` 白名单（不扩根目录、不递归扫描）；sha256 校验 | 否 |
| 统计口径 | 314/265/72（v1）与含 PG/Temp 的 v2 并存；SLA 分子分母切换需 A0 拍板 | 否 |

## 六、迁移与回滚

**迁移（Proposed→实现，分四段）**：
1. **本 ADR**：ARCH1 预审 + A0 拍板 §七 P1–P8 → Accepted；不动代码/数据。
2. **schema + registry v2 草案**（实现单，独立 worktree）：`dataset.schema.json` v0.1 + registry YAML v2（pg/temp 两节、stats_v1/v2）+ 定向测试（schema 校验、空/缺 DSN、白名单缺失 fail-visible）。
3. **wave1 登记**：≤15 张核心 PG 表 + `C:\Temp` 白名单首批（含 ACAD1 证据包等），只读 probe 生成清单；`metadata_consistency` 与组合 `validate --all` 复核。
4. **wave2 + 门禁收紧**：83/83 PG + Temp 迁移契约目录（P2）；`composition.py` 启发式切换严格模式；K5 状态更新。

**回滚**：
- registry：保留 `dataset_registry_v1.yaml`（v1）不覆盖，v2 生成失败/评审未过 → 回退读 v1；`emit_yaml` 重建可逆。
- schema：`platform/schemas/dataset.schema.json` 回退删除（新增文件）；K5 条目回 `planned`。
- 连接器：`ZHICE_V2_DSN` 缺失/失败 → fail-visible 且不参与统计（不产生空登记假绿）；可整体停用 PG/Temp 两节回到 v1 口径。
- 全程不写 PG/不迁移/不删文件；不做 `git reset --hard`/`clean -fdx`；回滚以 revert 提交。

## 七、未决项 / 需 A0 拍板点

| # | 拍板项 | 选项 | 建议 | 影响 |
|---|---|---|---|---|
| P1 | PG 登记范围与波次 | A. wave1 核心 ≤15 张、wave2 全 83（D3）｜B. 一次全量｜C. 只登记 manifest 引用的 6 张 | **A** | 决定试点内可交付性与 ref 严格化时点 |
| P2 | `C:\Temp` 策略 | A. 白名单登记+fail-visible、不扩根（D4）｜B. 把 `C:\Temp` 加进 `ALLOWED_ROOTS`｜C. 先迁移契约目录再登记（放弃试点内登记） | **A**（迁移作为 P2 目标） | 安全 vs 进度；B 有任意文件读风险 |
| P3 | stats 口径切换 | A. v1 保留 + v2 并存，SLA 暂按 v1 报（D6）｜B. 立即切 v2 重算 SLA | **A** | 影响 314/265/72 基线与 DWH-102/ACL-103 引用一致性 |
| P4 | `dataset.schema.json` v0.1 是否按 D7 冻结 | A. 冻结（后续只增不改，与 ADR-015 Composition schema 同纪律）｜B. 先 draft 不冻结 | **A** | 决定 kernel_guard 覆盖与后续演进成本 |
| P5 | dataset ref 严格化时机与门禁强度 | A. 过渡期 warning、wave2 后 block｜B. 立即 strict block | **A** | 立即 block 会误杀在途 manifest/组合 |
| P6 | PG 只读 DSN/账号来源 | A. 环境变量 `ZHICE_V2_DSN`（不落盘）｜B. 凭据区统一管理（L2 单） | **A** + L2 另批 | 安全边界；B 需 L2 五要素 |
| P7 | PG 表 owner/新鲜度声明真源 | A. v0 仅登记 owner，新鲜度 null（D2）｜B. 逐表声明 freshness（工作量大） | **A**（v1.1 再逐表） | 防止"假新鲜度"污染 SLA |
| P8 | 与 ACL-103 ADR 是否同批拍板 | A. 同批（推荐）｜B. 分开 | **A** | 避免 ref 无法解析中间态 |

## 八、本 ADR 被接受后的验收口径（不实现，仅定义）

1. `platform/schemas/dataset.schema.json` 存在且可校验（YAML/JSON schema 解析 + 合法/非法样例测试）；
2. registry v2：`stats_v2` 输出 `file_inventory/pg/contract_artifacts/all` + `denominator_version`；`stats_v1` 与 314/265/72 完全一致；
3. wave1：核心 ≤15 张 `pg_table` 可 `get/sample`（只读），`C:\Temp` 白名单条目可登记且 missing 时 fail-visible；
4. `python -X utf8 tools/datawarehouse/metadata_consistency.py` rc=0；`python -X utf8 tools/composition.py validate --all` 6/6 errors=0；
5. 定向测试：空/缺 DSN、白名单外文件拒绝、`ref_aliases` 解析、v1/v2 stats 兼容，全绿（≥8 例）；
6. `kernel_guard --list` 可读到 K5 新条目（D7/P4 后）。

## 九、边界声明

- 本 ADR **只写决策与口径**：未写实现、未改 `tools/datawarehouse/**`、未改 `platform/**`、未新增 schema/registry 文件、未 git commit/merge、未启停服务、未连接数据库。
- 所有引用均来自只读读取（2026-11-02，仓库 HEAD `e8787d9c3`，并发 WIP 22 项非本单产生）；证据清单见 `deliverables/2026-11-02_ACL-103_DWH-103_ADR草案_证据_v1.md`。
- 实现归 DWH-103 实现单（DATA1/ARCH1）；PG/RDS 凭据属 L2（ADR-010/012），本 ADR 不代签。
