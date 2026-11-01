# ADR-015：平台内核契约与组合模型 v1

- **状态**：Proposed（内核冻结范围待 A0 拍板；组合层 v0.1 先行）
- **日期**：2026-11-02
- **关联**：ADR-008（席位契约）、ADR-014（Agent 运行契约）、ADR-016（唯一宿主整合）、O3 扩展契约、S21 对外契约、K-ORG-001 §11.3、K-ORG-190、K-STOCK-137、I5 回写信封
- **性质**：**本 ADR 只定义契约与冻结边界，不改变任何运行时行为**
- **配套产物**：`compositions/_schema/composition.schema.json`、`compositions/README.md`、`compositions/registry.json`、`platform/kernel_v1.yaml`
- **上游研究**：`deliverables/2026-11-02_松耦合技术框架平台_固化可行性研究_v1.md`（v1.2）

## 背景

平台经过 40+ 天演进，已有可复用底座：AgentSpec/席位注册表、TaskStore、S21 统一信封、O3 扩展 manifest、ADR-014 运行契约、策略收编 S1–S5、数据登记、路由基线、devtask 九道门。K-ORG-001 早已立"加部门/Agent = 注册表加配置，零代码改动"，K-ORG-190 也落地了"加岗不改平台"。

但 2026-11-02 实测审计暴露同一组问题（详见研究报告 §二/§三）：

1. **扩展点仍是"多处登记 + 复制模板"**：新增席位 = 2 文件自动 + 4 处人工；新增工具 = 4 处登记；新增一类工作流要写 300–760 行 Python（无插件位）；
2. **声明层普遍没接执行点**：`tool_matrix` 只覆盖 29/67 席且无人执行；`agent_policy` 自述"不接线"；`skill_registry` 与实现双向漂移；
3. **同一事实多源**：模型绑定 5 处、技能 2 套、席位 4 处、池配额 3 套；
4. **没有组合层**：谁在用哪个组合、依赖谁、健康如何，没有总表；增/删一个军团或产品无法证明"内核零改动"。

若不立法，每次新增能力都要改主链或复制模板，"未来只是增加减少组合"无法成立。

## 决策

### 1. 固定的是契约与边界，不是技术实现

冻结对象是 **K0 组合层 + K1–K8 内核契约**（schema / 状态机 / 信封 / 口径 / 注册表格式 / 治理门）。
**不冻结**：语言、框架、数据库、UI 库、模型型号、运行器、Windows 计划任务等实现。
判据：实现可替换而调用方零改动；契约变更才需要 ADR。

### 2. 建立 K0 组合层（本次新增）

1. **Composition Manifest v0.1**：`compositions/<id>/manifest.yaml`，只引用不内嵌，schema 见 `compositions/_schema/composition.schema.json`；
2. **组合账本**：`compositions/registry.json`（id/版本/owner/生命周期/宿主/依赖/健康）；
3. **组合运行时**（P1 实现）：`tools/composition.py validate|plan|status|diff|audit|retire`——只读校验 + 投影 + 登记，不做第二真源；
4. **内核守卫**（P1 接入 devtask merge）：`tools/kernel_guard.py`，内核契约文件改动必须带 ADR 号；
5. **一致性测试**：T1–T5 判据自动化（新增组合零内核 diff、删除零残留、替换零调用方改动）。

### 3. 内核分层（K0–K8）

| 编号 | 内核层 | 冻结物（契约/schema/门禁，不含实现） | 现状 |
|---|---|---|---|
| K0 | 组合层 | composition schema、registry 格式、组合 CLI、kernel guard | 新建（v0.1 draft） |
| K1 | 组织/席位 | 席位描述符 schema（收编 org/team/hr/tool_matrix）、skill/model 目录 schema | 部分（待统一 Seat 描述符） |
| K2 | 任务/协作 | TaskStore 状态机与 RBAC、task schema、approval 凭据 | 已有行为契约，schema 待补 |
| K3 | 运行/可观测 | Run/Event/Conversation schema（ADR-014）、contract_version 协商 | 契约文档有，schema/校验器待补 |
| K4 | 接口/信封 | S21 信封 schema、reuse 契约、I5 回写信封、错误码表 | 已有 schema/文档 |
| K5 | 数据/证据 | dataset registry 契约（含 pg_table）、数据契约、证据包 schema、血缘 | 部分（文件级；PG/Temp 待补） |
| K6 | 呈现 | tokens/nav/page_shell/shortscreen、路由基线、页面 manifest/区块注册 | 部分（页面 manifest 待建） |
| K7 | 扩展 | extension manifest v1 schema + lint + 生命周期 | 已有（镜像态，待转真源） |
| K8 | 治理 | devtask 九道门、ADR 流程、L2、版本策略、kernel guard | 已有，待补 kernel/composition/doc-drift 门 |

具体冻结文件与 owner 见 `platform/kernel_v1.yaml`（v0.9 draft）。

### 4. 五条可测判据（T1–T5）

1. 新增组合：只新增 `compositions/<id>/` + 注册表引用 + 测试，内核文件 diff = 0；
2. 删除/停用组合：内核零改动，页面/任务/席位/数据可回收，残留 = 0；
3. 替换实现（模型/数据源/通道/运行器/前端壳）：只改 adapter/manifest 引用，调用方零改动；
4. 组合间只走契约：不 import 内部实现、不直读对方私有表、不共享可变状态；
5. 组合可治理：owner/版本/健康探针/数据口径/SLO/回滚/账本齐全。

### 5. 同一事实单一真源

内核 v1 必须"单一真源 + 生成"：模型以 `model_tiers.json` 为唯一来源；席位以统一 Seat 描述符为唯一来源；技能以 skill registry 为唯一登记；池/数据口径以数据契约为唯一来源。其余全部派生或只读投影，新登记处出现即视为违规。

### 6. 声明必须被执行点消费

组合清单/注册表的每个字段必须登记"谁读它、在哪生效"。没有执行点消费的字段视为无效声明——这是对"声明层假耦合"的根治口径，避免组合清单变成第二本不生效的账。

### 7. 变更纪律

- 内核 semver；组合 manifest 用 `kernel: ">=x.y <z"` 锁兼容区间；
- minor 只增字段/事件类型；major 删改字段必须双版本并存 + ≥90 天 deprecation（对齐 ADR-014）；
- 内核契约文件改动必须带 ADR 号（kernel guard 拒收无 ADR 改动）；紧急修复允许 24h 内补 ADR；
- 每季度 Kernel Review（版本、组合兼容矩阵、技术替换演练、硬耦合残留）。

### 8. 落地节奏（详见研究报告 §七）

P0 立法（ADR-015/016 + schema + 影子清单 + 冻结清单）→ P1 组合运行时 MVP + 运行态单栈（L1）→ P2 关键解耦（声明接线/Seat 描述符/WorkflowSpec/契约入库/双池收口）→ P3 单栈收口 + 组合化演练 + 发布 Kernel v1.0。

## 后果

- **正面**：新增/删除军团、产品、策略、页面、数据源、工作流 = 声明式操作；内核改动可审计；技术实现可替换；"一套宿主跑 N 个组合"可证明。
- **代价**：需要一次性的契约整理（Seat 描述符、WorkflowSpec、schema 入库）；组合层带来少量元数据维护成本；门禁会拦"顺手改内核"。
- **不做**：不丢现有注册表；不做大爆炸重构；不改变在途运行时；不把实现细节写进内核。

## 待拍板

1. K1–K8 全部冻结，还是先冻 K1/K2/K4/K7/K8、K3/K5/K6 写"过渡期"？
2. 是否批准 kernel guard 进 devtask merge（内核契约文件改动必须带 ADR 号）？
3. Seat 描述符、WorkflowSpec、契约入库三项的优先级与节奏（建议 P2 全做）？
4. Composition schema v0.1 是否按本稿冻结（后续只增不改）？

## 相关

- 研究报告：`deliverables/2026-11-02_松耦合技术框架平台_固化可行性研究_v1.md`
- 组合层：`compositions/README.md`、`compositions/_schema/composition.schema.json`
- 内核冻结清单：`platform/kernel_v1.yaml`
- 唯一宿主：`docs/adr/ADR-016-single-host-integration.md`
- 既有判例：ADR-008、ADR-014、`docs/plans/2026-10-19_Agent指挥平台迭代方案_K-ORG-190.md`、`docs/plans/2026-10-11_模拟交易松耦合架构蓝图_K-STOCK-137.md`、`docs/specs/2026-10-27_扩展契约_v1.md`、`docs/plans/2026-10-21_军团对外接口契约_v1.md`
