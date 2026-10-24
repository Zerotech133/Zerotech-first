> A1 仓内归位 · 冻结稿（2026-10-24）· 来源交付包 `deliverables/2026-10-24_OS首版单事项闭环_研发计划与方案/`；A0 裁定 D1-D8 已获老田批准（见同包 17_A0裁定记录_2026-10-24.md）。文中相对链接以交付包为路径基准；本文件随任务 OS-CLOSE-A1 评审后合并。

# 03b · 事项合同 v1.1（事业目标—事项—成果 三级关系 · 增量合同）

> 合同版本：`os-item/1.1`（在 v1.0 单事项合同上**增量**扩展）· 2026-10-24 · 状态：**已冻结（A1）**；v1.0 合同继续有效
> 上游：`09_产品基线_v0.1` §5 三级关系；`11_研发方案_v2` §4/§5/§8
> 机器可校验（原型）：`tools/os_goal_check.py` —— **13 项测试通过**
> 落地（A1/B0）：`docs/specs/OS_GOAL_ITEM_OUTCOME_v1_1.md` + `portal/os_goal_store.py`；
> **v1.0 合同、schema、判据器继续有效**，v1.1 只加不改。

---

## 1. 版本与兼容

| 项 | 规则 |
|---|---|
| `schema_version` | 新事项写 `os-item/1.1`；v1.0 事件流仍可读、仍按 v1.0 判 |
| 新增必填 | 仅对 `os-item/1.1` 新事项生效：`goal_ref`、`outcome_kind`、`citations`（显式声明） |
| 兼容映射 | 软件面可继续用 `artifact.produced` 承载成果（等价 `outcome.produced`，kind=software）；正式事件化在 A1 完成，过渡期用映射表 |
| 不变量 | v1.0 的 10 条不变量全部继承；新增 I11–I15（见 §6） |

## 2. Goal（事业目标）与 Assumption（假设）

**`goal.created` / `goal.updated` 事件**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `goal_id` | string `GOL-YYYYMMDD-NNNN` | ✅ | 全局唯一；不被 item 复用 |
| `statement` | string | ✅ | 我在建立什么事业、为谁 |
| `target_user` | string | ✅ | 服务谁（首个样本/首批/扩展） |
| `assumptions[]` | array | ✅（≥1） | `{id, text, status: hypothesis\|validated\|falsified, evidence_refs[], updated_at}` |
| `success_signals[]` | array | ✅（≥1） | 怎样算离目标更近 |

**不变量**
- I11：Goal 只能由本人（actor.kind=user）建立与更新；
- I12：假设状态只可迁移并留痕（hypothesis→validated/falsified），不删除历史；
- I13：Goal 不设"完成/关闭"终态，只通过假设状态与成功信号表达进展（允许长期、反复）。

## 3. Item 扩展（本事项属于哪个目标）

在 v1.0 `item.created` 基础上新增必填：

| 字段 | 位置 | 必填 | 说明 |
|---|---|---|---|
| `goal_ref` | `refs.goal_id` | ✅ | 无目标不开事项 |
| `outcome_kind` | `payload.outcome_kind` | ✅ | `software \| business_plan \| content \| research` |
| `why_now` | `payload.why_now` | ✅ | 为什么现在做（一句话；防止"技术上能做"占主线） |
| `attempt_no` | `payload.attempt_no` | ✅ | 同一目标下第几次尝试（从账本读取，不是推断） |
| `related_item_refs[]` | `payload.related_item_refs` | — | 上次尝试/依赖事项，用于"为什么重来、上次学到什么" |

## 4. Outcome（成果）四类规范

| 成果类型 | 必填内容 | 必备证据 | 预览/试用 | 回滚 |
|---|---|---|---|---|
| `software` | 产物版本、路径、sha256、运行/预览方式 | 构建日志 + 测试回执（`test.recorded`） | 本人打开隔离预览 | 删除目录/撤回配方 |
| `business_plan` | BP 版本、修订理由、假设台账快照、**citations[]** | 假设证据引用 + 上一版差异 | 只读文档预览（不导出） | 保留旧版本（版本链） |
| `content` | 选题依据、草稿版本、审核记录、发布计划、**citations[]** | 来源引用 + 审核意见 | 只读预览；发布=BLOCKED | 旧草稿保留 |
| `research` | 数据来源与快照、方法、回测/模拟结果、风险结论、**citations[]** | 数据指纹 + 结果文件 + 风控分层 | 只读报告 | 结果文件保留 |

- `business_plan/content/research` 的 `citations` **不得为空**；`software` 可显式 `[]`。
- Outcome 版本链：`supersedes` 指向上一版；**不改旧版本**。
- 每件 Outcome 必须能回答六问：服务于哪个目标 / 依据什么证据 / 谁授权了什么 / 做到什么 / 谁验收 / 第几版。

## 5. Citation（跨面引用）与三值规则

| 字段 | 说明 |
|---|---|
| `source_surface` | `software \| business_plan \| content \| research \| external` |
| `source_ref` | 可复读来源标识（文件/commit/文档/数据快照/会话） |
| `source_kind` | `data \| document \| commit \| conversation \| inference` |
| `source_sha256` | 来源指纹（可追溯） |
| `claim` | 引用支撑的具体主张（一句话） |
| `confidence` | `fact \| hypothesis` |


> 三值由 `source_kind`（data/document/commit/conversation/inference）与 `confidence`（fact/hypothesis）组合表达：`inference` 只能配 `hypothesis`；不单独存在 confidence=inference。
**六条规则（判据器实现）**
1. `inference + fact` → `INFERENCE_AS_FACT` FAIL（推断不得直接当事实）。
2. 同一 `source_ref` 先 hypothesis 后 fact → `CITATION_UPGRADE` FAIL（重复转述不得升级）。
3. 引用必须带来源面、来源类型、来源指纹；缺一即 `CITATION_SHAPE` FAIL。
4. BP/内容/研究类成果空引用 → `CITATION_REQUIRED` FAIL。
5. 两来源矛盾 → 记 `conflict`（双方并列），不自动合并成结论。
6. 同一来源在账本内只登记一次来源属性；后续引用只能降级或持平。

## 6. 新增不变量与门禁

| 编号 | 不变量 | 违反 |
|---|---|---|
| I14 | 无 Goal 不开 Item；Item 的 `goal_ref` 必须等于当前 Goal | `ITEM_ANCHOR` / `GOAL_ANCHOR`（BLOCKED/FAIL） |
| I15 | 成果类型必须与事项声明一致；引用必须可复读且不升级 | `OUTCOME_MISMATCH` / `CITATION_*` FAIL |

**新增门禁**
- **G-1 目标锚定**：Goal 存在且本人创建、假设带状态；Item 挂对 Goal、声明合法 outcome_kind。
- **G8 成果与引用完整性**：Outcome 类型一致、四类必填证据齐全、引用规则全过。

**各面附加门（首版）**
| 面 | 附加门 |
|---|---|
| software | 沿用 v1 G6/G7（产物+测试+本人试用验收） |
| business_plan | 修订必须写理由；假设台账不得凭空转 validated；导出/外发=BLOCKED |
| content | 发布计划存在但**未授权不得外发**（`external.send`=BLOCKED） |
| research | 数据来源+快照+风控分层；**真实交易=BLOCKED** |

**总判定与退出码**：内层单事项判据器沿用 v1.0 机器退出码（0/1/2/3）；B 阶段对外结论按判据 v3.1 两轴输出（四件成果 × 真实受控执行），不得把退出码 3 直接写成「未通过」。

## 7. 实现状态（诚实）

| 项 | 状态 |
|---|---|
| `tools/os_goal_ref/`（随附参考实现，G0 迁入 portal） | ✅ 已入仓为 `tools/os_goal_ref/`；13 项测试通过（四类成果 PASS；缺目标 BLOCKED；无引用/推断升事实/同源升级/目标非本人/类型不符 均拒绝） |
| 事件正式化（`outcome.produced` / `evidence.recorded`） | ⏳ A1/B0 完成；过渡期用 `artifact.produced` + `outcome_kind/citations` 承载（与原型一致） |
| JSON Schema v1.1（增 Goal/Outcome/Citation 三个 `$defs`） | ⏳ A1 在 `item_contract_v1.schema.json` 上增量 |
| 仓库落地（`docs/specs/…` + `portal/os_goal_store.py`） | ⏳ 拍板后按 `10_研发计划_v2` A1/B0 |


> **范围界定（A1 复核修订）**：A1 只冻结本合同语义与判据文本；`tools/os_goal_ref/` 为随附参考实现（非生产代码）。事件正式化（outcome.produced/evidence.recorded）、JSON Schema v1.1 增量、`portal/os_goal_store.py` 落地均为 **G0 范围**（见 `deliverables/…/18_B阶段执行组织与所有权.md`）。
## 8. 冻结与演进

1. 本增量合同随产品基线 v1.0 一并冻结；两天窗口内不改变量语义。
2. 任何语义变化 → `os-item/1.2` + 新判据版本 + 老田复核；旧事件按原版本语义回放。
3. 判据器、合同正文、schema 三者必须同批提交；改一改二由评审门拒绝。

---
*本文件为冻结稿（A1），随 OS-CLOSE-A1 合并入仓；v1.0 合同与 schema 继续有效。*
