# 零界平台框架 v1（ZBF v1）· 整体复盘与唯一开发框架

- 版本：v1.0（Proposed，随 ADR-017 待批）
- 日期：2026-11-02
- 性质：**平台唯一框架总纲**。本文件回答：整体复盘、社区对标、只留哪一套、未来所有开发怎么做
- 上游决策：ADR-015（内核契约与组合模型）、ADR-016（唯一宿主整合，Accepted）、ADR-017（黄金路径，Proposed）
- 配套：`compositions/`（组合清单）、`platform/kernel_v1.yaml`（内核冻结清单）、`deliverables/2026-11-02_平台内核P0_立法回执_v1.md`

## 〇、一句话结论

**ZBF v1 = 一套宿主 + 一层内核契约 + 一个组合模型 + 一条黄金路径 + 一道一致性门禁。**

- 一套宿主：零界（8734 唯一入口；单身份、单库、单调度、单组合账本），智策收敛为 `quant-domain` 组合（ADR-016）。
- 一层内核：K0–K8（组合层/组织席位/任务/运行/接口/数据/呈现/扩展/治理），只冻结契约、schema、校验器、门禁，不冻结实现。
- 一个组合模型：C1–C8（军团/产品/策略/页面/数据/工作流/通道/平台），组合 = 只引用不内嵌的 `manifest.yaml`。
- 一条黄金路径：未来任何开发只有 **三种合法形态**——①加/减组合；②替换实现（adapter）；③改内核（必须 ADR）。临时脚本/第二入口/第二套认证属于非法形态。
- 一道门禁：`composition validate` + `kernel_guard` + devtask 现有九道门 + 质量分级 Q1–Q4。

**只保留这一套；不再有第二套技术栈、第二入口、第二数据库、第二认证、第二调度。**

---

## 一、整体复盘（2026-10-08 → 11-02）

### 1.1 时间线（关键节点）

| 日期 | 事件 | 结果 |
|---|---|---|
| 10-08 | 组织架构 Agent 平台设计（K-ORG-001） | 立"加部门/Agent=加配置，零代码"与"新模态接入四步" |
| 10-16~19 | 数字军团收编/入职框架；AgentSpec v1（K-ORG-190） | 席位/技能/模型注册表、skill_registry、model_tiers、指挥台 API |
| 10-27 | ADR-014 Agent 运行契约（run/event/conversation） | Accepted；P1 只读派生落地，P2 部分落地 |
| 10-30 | 零界×智策"联邦架构"三方案 | 不合并代码/DB、只读 BFF、入口跳转（**已被 11-02 推翻**） |
| 10-31~11-01 | ACAD1 生产页、30 卡桥接、项目规划 v2、组织章程 | 研究治理成形；同时暴露双池/寄居/声明不接线 |
| 11-02 | 三份只读审计 + 固化可行性研究 + 框架决策 | 21 处硬耦合；ADR-015/016；P0 立法 |
| 11-02 | 老田拍板：唯一宿主=零界、L1 30 天、SaaS 归档、批准 ADR-016 | P0 产物提交 `6dbd9c9c9` |

### 1.2 做对了什么

1. **入口立宪**：8734 唯一人类入口，避免多入口分裂（K-STOCK-113）。
2. **注册表 + 回滚**：org/team/skill/model 注册表、改绑备份、org_command_api 单测，是平台里工程质量最高的一段。
3. **质量门真实存在**：devtask 九道门（语法/导入边界/元数据/路由清单/budget/Mixin/导航/端口/凭据/test），不是纸面规范。
4. **判例文化**：ADR + `docs/audit` + `docs/plans` 让决策可追溯；本轮也靠判例快速定位矛盾。
5. **只读审计有效**：三份并行审计在两小时内定位 21 处硬耦合、ADR-014 状态漂移、工作流无插件位。
6. **P0 立法成功**：ADR-015/016 + 组合 schema + 6 影子清单 + 内核冻结清单，已提交保护。

### 1.3 问题（复盘结论，按严重度）

| # | 问题 | 后果 |
|---|---|---|
| 1 | **两套技术栈并行**（零界 stdlib/vanilla vs 智策 FastAPI/React/PG） | 两入口/两认证/两调度/两池，运维与口径分裂 |
| 2 | **同一事实多源**（模型绑定 5 处、技能 2 套、席位 4 处、池配额 3 套） | 改一处不生效，审计无法判真 |
| 3 | **声明层不接执行点**（tool_matrix 29/67 且无人执行、agent_policy 不接线、skill_registry 双向漂移） | "能力 6/6"与"无产出"同时成立（ACAD1 复盘 5.5/10） |
| 4 | **没有组合层** | 增/删军团、产品、页面、工作流无法证明"内核零改动" |
| 5 | **没有黄金路径** | 每个新能力各写各的：改主链、复制模板、手工登记 |
| 6 | **没有质量分级** | 新组合与核心组合同一门槛，要么都不做、要么都放行 |
| 7 | **调度碎片化**（412 计划任务 + APScheduler + LegionScheduler） | 工作流不可声明、失败不可见、跑批口径不一 |
| 8 | **临时文件当契约**（`C:\Temp\*.json`） | 无 schema/版本/归属，删除即断链 |
| 9 | **页面体系无清单**（104 HTML + 150 nginx location + 双注册） | 漏登记即 404/401，组合无法声明"我有哪些页面" |
| 10 | **并行会话无清理纪律** | 11-02 03:2x `reset+clean` 清掉未跟踪的 P0 文件一次（已重建提交） |

### 1.4 五条教训

1. **契约先于功能**：先定接口/schema/口径，再写实现；否则每加一个功能就多一处耦合。
2. **单一真源与单入口不是口号**：必须由门禁强制（核验执行点、路由基线、端口注册）。
3. **声明必须被消费**：没有执行点的声明就是第二本坏账（组合清单也一样）。
4. **先质量分级再扩张**：Q1 能登记、Q2 能运行、Q3 能治理、Q4 能进化；不同级别不同门槛。
5. **文档 ≠ 实现，交付 ≠ 履职**：能力卡/任务 rc=0 不等于产出；组合 validate 通过 ≠ 引用可解析（P1 必须补 resolver）。

---

## 二、社区/开源对标（我们去看了什么，采纳什么）

> 原则：只借"模式"，不搬"运行时"。单机 Windows + 单人是硬约束，社区方案的分布式部分一律不引入。

| # | 社区实践 | 代表项目/来源 | 核心做法 | 我们的采纳 | 不采纳/边界 |
|---|---|---|---|---|---|
| 1 | **平台工程黄金路径（Golden Path）** | [Backstage 开发者自助](https://developers.redhat.com/articles/2025/06/25/how-implement-developer-self-service-backstage)、[AWS APEX Golden Paths](https://aws-samples.github.io/sample-apex-skills/docs/skills/eks/eks-platform-engineering/references/golden-paths/) | 平台给"铺好的路"：模板 + 自助 + 默认安全；不是强制，但有 escape hatch | **黄金路径成为唯一开发路径**：组合模板 + `composition` 校验 + 例外走 ADR | 不引入 Backstage/开发者门户；不搞第二套 UI |
| 2 | **插件/集成 Manifest** | [Dify 插件 Manifest](https://docs.dify.ai/en/develop-plugin/features-and-specs/plugin-types/plugin-info-by-manifest)、[Home Assistant Integration Manifest/Quality Scale](https://contributors.home-assistant.io/core/quality-scale/) | YAML 声明 name/version/权限/资源/生命周期；清单非法则加载失败 | Composition v1 + 扩展 manifest 扩类型；权限矩阵（Dify `resource.permission` 思路）进 `permissions` | 不引入插件运行时/插件市场（ADR-013 已否） |
| 3 | **质量分级** | [Home Assistant Quality Scale](https://contributors.home-assistant.io/core/quality-scale/) | Bronze/Silver/Gold/Platinum 逐条规则，逐项可查 | **Q1–Q4 组合质量分级**（登记/可运行/可治理/可进化），门禁按级别放行 | 不照搬其规则集，只借"分级 + 逐条可查"模式 |
| 4 | **声明式组合/控制器** | [CNCF kro composition](https://www.cncf.io/blog/2025/12/15/building-platforms-using-kro-for-composition/)、[Crossplane 2.0](https://blog.crossplane.io/announcing-crossplane-2-0/) | 声明期望态 + reconciliation；组合由声明拼装 | `compositions/*.yaml` 为声明；P1 `composition.py apply/status` 做 reconcile（幂等、只投影） | **不引入 K8s/CRD/Operator/控制器**，单机用 CLI + 文件 |
| 5 | **契约优先数据** | [dbt contracts](https://docs.getdbt.com/reference/resource-configs/contract) | 数据表声明 contract + tests + 版本，CI 强制 | dataset registry 增 `contract/version/tests`；数据契约进组合 `data.contract` | 不引入 dbt/数据仓库新栈 |
| 6 | **工具与 Agent 协议** | [MCP Tools 规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools.md)、[OTel GenAI Agent Spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/7976d915f01d65230cb4ac8cfc3a4742c6235a49/docs/gen-ai/gen-ai-agent-spans.md) | 工具必须有 input/output schema、错误分类、人审/审计；Agent run/span 统一语义 | K4 信封补 `outputSchema`；工具默认只读 + 人审；ADR-014 映射 `gen_ai.*` | 不引入 A2A 网络/新消息总线；MCP 只走本地 stdio/HTTP |
| 7 | **模块化单体/平台即产品** | [AWS APEX IDP Architecture](https://aws-samples.github.io/sample-apex-skills/docs/skills/eks/eks-platform-engineering/references/idp-architecture/) | 单团队用模块化单体；平台当产品，内部客户是业务团队 | 明确**单机模块化单体**；军团/产品是内部客户；平台团队=ARCH1+A0 | 不微服务化、不上 K8s/MQ/新数据库 |
| 8 | **可审计决策/文档体系** | ADR（Nygard 模式）+ Diátaxis（我们已用 ADR-004） | 一决策一文件、四类文档、状态可废弃 | 本框架 + ADR-017；季度 Kernel Review | 不新增文档体系，沿用现有 |

**社区给我们的三个"反直觉"结论**：
1. 平台不是"功能集合"，是**铺好的路 + 默认安全 + 自助模板**；我们过去缺的正是"路"（黄金路径），不是功能。
2. 成熟的插件生态都靠**manifest + 生命周期 + 质量分级**，没有靠"文档约定"的；我们的组合清单必须机器可校验。
3. 声明式组合不需要 K8s；**CLI + 文件 + 幂等投影**同样能实现 reconcile，且适合单机。

---

## 三、唯一框架 ZBF v1

### 3.1 分层

```text
┌──────────────────────────────────────────────────────────────┐
│ 唯一宿主：零界（8734 nginx）                                   │
│ 单入口 · 单身份 · 单业务库 PG 5446 · 单调度 · 单组合账本         │
│ 内部服务：portal-api 8091 / quant-api 8105 / Hermes 8787 /      │
│ legion-ide 8103 / DSH（均仅内部，不经第二入口）                 │
└───────────────▲──────────────────────────────────────────────┘
                │ 实现/宿主
┌───────────────┴──────────────────────────────────────────────┐
│ 组合层 K0：compositions/<id>/manifest.yaml + registry + CLI    │
│ 只引用不内嵌；C1–C8；生命周期 draft→…→retired                  │
└───────────────▲──────────────────────────────────────────────┘
                │ 引用
┌───────────────┴──────────────────────────────────────────────┐
│ 内核 K1–K8：组织席位 / 任务 / 运行 / 接口 / 数据 / 呈现 / 扩展 / 治理 │
│ 冻结：契约 + schema + 校验器 + 注册表格式 + 门禁               │
└───────────────▲──────────────────────────────────────────────┘
                │ 适配
┌───────────────┴──────────────────────────────────────────────┐
│ 运行适配器（可替换）：LLM / Agent 运行器 / 数据源 / 通道       │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 组合质量分级 Q1–Q4（社区质量分级模式的本土化）

| 级别 | 名称 | 必达条件 | 允许状态 |
|---|---|---|---|
| Q1 | 已登记 | schema PASS + owner + 版本 + 生命周期 | `draft`（影子，不进生产） |
| Q2 | 可运行 | Q1 + 引用可解析 + 健康探针 + SLO + 回滚步骤 + 至少 1 个测试 | `active`（内部可用） |
| Q3 | 可治理 | Q2 + 数据契约 + 诊断信息 + 审计留痕 + 替换说明 | 对外/关键业务组合 |
| Q4 | 可进化 | Q3 + 技术替换演练通过 + 退役路径 + 季度评审 | 平台核心/长期组合 |

门禁：`active` 至少 Q2；对外/关键至少 Q3；平台核心/长期 Q4。**新组合从 Q1 起步，不允许一次跳级**。

### 3.3 内核/组合/实现/非法：四种形态

| 形态 | 判定 | 入口 | 内核改动 | 审批 |
|---|---|---|---|---|
| ① 组合变更 | 新增/修改军团、产品、策略、页面、数据包、工作流、通道 | `compositions/<id>/` + registry | **0** | `composition validate` + devtask |
| ② 实现替换 | 换模型、数据源、推送通道、运行器、前端壳 | adapter/manifest 引用 | 0 | 契约测试 |
| ③ 内核变更 | 改 schema/状态机/信封/口径/门禁 | `platform/kernel_v1.yaml` 清单内 | 有 | **ADR + A0** |
| ✗ 非法 | 改主链加分支、新入口/认证/DB/调度、无 manifest 上线、临时脚本 | — | — | 一律拒 |

### 3.4 五条硬规则（框架宪法）

1. **单宿主**：只有零界 8734 一个入口；5175/8102/8080 不得作为第二入口（L1 截止 2026-12-02）。
2. **单真源**：模型/席位/技能/数据口径/池，各自只有一个真源，其余为生成或只读投影。
3. **声明必须被消费**：组合清单每个字段必须登记"谁读它、在哪生效"；否则删除该字段。
4. **新能力只能是组合**：不允许"直接在 portal/zhice 里加一个分支/页面/脚本"作为交付。
5. **内核改动必须 ADR**：`kernel_guard` 在 merge 时强制；紧急修复 24h 内补 ADR。

---

## 四、未来所有开发：黄金路径（唯一路径）

### 4.1 六步法

```text
Step 0 分类：内核 / 组合 / 实现替换 / （非法）临时脚本
Step 1 开单：python tools/devtask.py new <TID> "<一句话>" --acceptance ... --boundaries ...
Step 2 写清单：从 compositions/_templates/<tier>.yaml 复制，填 refs（不内嵌实现）
Step 3 校验：python tools/composition.py validate compositions/<id>
              + 引用解析 + 冲突检查 + 质量级别判定（Q1→Q2）
Step 4 接线：页面/任务/数据/席位引用；新实现走 adapter；不改主链
Step 5 合并：devtask merge（九道门 + composition gate + kernel guard + secret scan）
Step 6 上线/观测：健康探针 + SLO + 组合账本登记；退役走 retired（不物理删除）
```

### 4.2 模板矩阵（P1 提供 `compositions/_templates/`）

| 要新增什么 | tier | 最少字段 | 典型引用 |
|---|---|---|---|
| 军团/组织 | `legion` | seats + tiers + owner + SLO | `agent/*`、`skill/*` |
| 产品/业务 | `product` | strategies + pages + data + jobs | `strategy/*`、`page/*`、`job/*` |
| 策略包 | `strategy` | strategy manifest + 回测/影子/晋级 | `strategy/*` |
| 页面包 | `page` | route + shell 三件套 + BFF | `page/*`、`interface/*` |
| 数据包 | `data` | dataset refs + contract + freshness | `dataset/*` |
| 工作流 | `workflow` | 节点/触发/超时/失败策略 | `job/*` |
| 通道 | `channel` | 通道实现 + 重试/降级 | `service/*` |

### 4.3 禁止清单（写进 AGENTS.md 与评审门）

1. 新增第二入口/第二认证/第二数据库/第二调度；
2. 无 `manifest` 上线的新能力；
3. 声明了但执行点不读的字段；
4. 复制 `domains/*_wiring.py` 再加一个新工作流；
5. 把 `C:\Temp\*.json` 当跨模块契约；
6. 在共享仓执行 `git clean -fdx` / `reset --hard`（11-02 事故判例）；
7. 绕过 kernel guard/composition gate（`DEVTOOL_MERGE_GATE=0` 仅回滚/紧急）。

### 4.4 例外流程（框架不适用时）

1. 写 ADR（问题/为何框架不适用/替代方案/回滚）；
2. A0 批准；
3. 实现为临时 adapter（不进内核）；
4. 90 天内回归框架或正式扩 schema；
5. 例外清单进季度 Kernel Review。

---

## 五、目前框架问题 → 闭环映射

| # | 问题 | 解决 | 阶段 | 验收 |
|---|---|---|---|---|
| 1 | 两套技术栈/两入口 | ADR-016 唯一宿主；L1 关 5175/8102 | L1 | 8734 唯一；`Get-NetTCPConnection` 无 5175/8102 |
| 2 | 双池口径（19 vs 30） | PG `v2_acad1_pool` 为真源，JSON 降投影 | P2/L1 | 池数一致；对账脚本 |
| 3 | 多运行器无统一 Run | ADR-014 P2 双写 + OTel GenAI 映射 | P2 | run/event schema + 2 入口双写 |
| 4 | 声明不接执行点 | tool_matrix/agent_policy 进执行入口；Seat 描述符单一真源 | P2 | default deny 真生效；声明→执行登记表 |
| 5 | 同一事实多源 | 单一真源 + 生成（模型/席位/技能/口径） | P2 | 新登记处出现即违规 |
| 6 | 无组合层 | ADR-015 + K0（schema/registry/CLI/guard） | P0 done/P1 | 6 影子 validate；P1 引用可解析 |
| 7 | 页面无清单/双注册 | 页面 manifest + 区块注册 + 分派表化 | P2 | 页面 100% 在册；无 404/401 漏登记 |
| 8 | 调度碎片 412 任务 | job manifest + 单 runner；Windows 任务仅触发器 | P2 | job manifest 覆盖日管线 |
| 9 | Temp JSON 契约 | 契约目录 + schema/版本/保留期；dataset registry 登记 | P2 | 0 个未登记 Temp 契约 |
| 10 | 无质量分级/黄金路径 | Q1–Q4 + 本框架 §四 | P1 | 组合分级可查；新功能走黄金路径 |
| 11 | 5175 引用面 14 处/静态 build 脏 | L1 清零清单 + clean build/pin | L1 | 引用清零；build 可复现 |
| 12 | 认证未统一/autoSession | 单认证 + 安全默认值先修 | L1 | 三态探针通过；autoSession 关 |
| 13 | 双计费体系冲突 | 定商业化 Of Record（零界 commercial vs 智策 billing） | L1 决策 | 归档范围确认 |
| 14 | 单库范围过大 | L2 只统一共享业务数据，运行态文件保留 | L2 决策 | 范围写入 ADR-016 附录 |
| 15 | P1 resolver 缺口 | `tools/resolver.py`（复用 composition 注册表；agent/skill/strategy/dataset/page/job） | **已完成 2026-11-03** | `--all` 6 组合 131 refs / 0 error / 0 warning（QA 11-04 复核；126→131 为 lusu 合法 +5） |
| 16 | kernel guard bootstrap | 手动门禁开关 → 自动 | P1 | 无 ADR 改内核被拒 |
| 17 | 组合文件触发测试门 | devtask/test_gate 加组合跳过/专用 gate | P1 | 组合改动只跑组合门 |
| 18 | 并发清理事故 | AGENTS.md 判例 + 写域/锁 + 未跟踪交付物保护 | 立即 | 判例入库；无再次发生 |
| 19 | 文档漂移（ADR-009 重号/014 状态/007 路径） | 一条修订提交 | 立即 | doc-drift 检查通过 |
| 20 | 排期冲突（11-04/11-08/12-02） | 排期表 + 互斥窗口 | 立即 | 无重叠变更窗口 |

---

## 六、90 天路线与验收

| 阶段 | 内容 | 期限 | 验收 |
|---|---|---|---|
| P0 立法 | ADR-015/016 + schema + 6 影子 + kernel_v1 | **已完成**（commit `6dbd9c9c9`） | 6/6 schema PASS；索引同步 |
| P1 框架可执行 | composition.py + kernel_guard + 模板 + resolver + merge 门 | 1–2 周 | 试点组合零内核 diff；引用可解析；门禁生效 |
| L1 运行态单栈 | 单入口/单认证/关 5175/8102/单调度台账 | 至 **2026-12-02** | ADR-016 L1 验收全绿 |
| P2 关键解耦 | 双池/声明接线/Seat 描述符/WorkflowSpec/契约入库/页面 manifest/job manifest | 2–6 周 | 问题表 1–10 清零过半 |
| P3 单栈收口 | 单库/单 UI 壳/单调度/智策降为 services/quant + 页面包 | 2–3 月 | 组合账本 100%；Kernel v1.0 |
| 稳态 | 季度 Kernel Review + 组合审计 + 技术替换演练 | 每季度 | T1–T5 ≥4/5；新增组合 ≤3 天 |

**框架成功判据（T1–T5）**：新增/删除组合内核 diff=0、实现替换零调用方改动、组合间只走契约、组合可治理、质量分级可查。

---

## 七、边界与待拍板

**不做**：微服务/K8s/MQ/新数据库；不重写 portal/zhice；不做插件市场/微前端；不引入第二入口/认证/调度。

**待拍板**：
1. ZBF v1 + ADR-017 是否批准（黄金路径作为唯一开发路径）；
2. 质量分级门槛：`active`=Q2、对外=Q3、核心=Q4 是否认可；
3. 例外流程审批人（建议 A0 + ARCH1 双签）；
4. 内核冻结范围（K1–K8 全冻 vs 先冻 K1/K2/K4/K7/K8）；
5. 双计费体系 Of Record（零界 commercial vs 智策 billing）；
6. L2"单库"范围（共享业务数据 vs 全部数据）；
7. 模板目录与命名（`compositions/_templates/`）。

## 八、社区来源（本次对标）

- Backstage 自助/黄金路径：[Red Hat 2025-06](https://developers.redhat.com/articles/2025/06/25/how-implement-developer-self-service-backstage)、[AWS APEX Golden Paths](https://aws-samples.github.io/sample-apex-skills/docs/skills/eks/eks-platform-engineering/references/golden-paths/)、[IDP Architecture](https://aws-samples.github.io/sample-apex-skills/docs/skills/eks/eks-platform-engineering/references/idp-architecture/)
- 插件 Manifest：[Dify Plugin Manifest](https://docs.dify.ai/en/develop-plugin/features-and-specs/plugin-types/plugin-info-by-manifest)、[Home Assistant Quality Scale](https://contributors.home-assistant.io/core/quality-scale/)
- 声明式组合：[CNCF kro composition](https://www.cncf.io/blog/2025/12/15/building-platforms-using-kro-for-composition/)、[Crossplane 2.0](https://blog.crossplane.io/announcing-crossplane-2-0/)
- 数据契约：[dbt contracts](https://docs.getdbt.com/reference/resource-configs/contract)
- 工具/Agent 协议：[MCP Tools 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools.md)、[OTel GenAI Agent Spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/7976d915f01d65230cb4ac8cfc3a4742c6235a49/docs/gen-ai/gen-ai-agent-spans.md)、[Agent Registry JSON Schema](https://docs.cloud.google.com/agent-registry/json-schemas)
- 迁移/模块化：Strangler Fig（既有 10-30 方案引用）；模块化单体（APEX IDP 参考）
