# 设计：结构性 id 根因修复 + 重做冲突裁决(B)

- 日期：2026-08-26
- 目标系统：本地 `zhice-os-v2`
- 分支：`fix/remediation-p0`（A/C 已合并 main=`e5cc6b4`，B 已 rebase 摘除）
- 状态：待用户审阅
- 关联：2026-08-25-audit-remediation、上游 P0 修复 A(`a6dc7b0` 出场污染)/C(`e5cc6b4` 离群护栏)

## 1. 背景与结论

P0 审查判定 B(`483aba8` 冲突裁决"败者回退授权+盘中排除作废")**修错并已 revert**。根因不在 B 本身，而在其依赖的 **id 结构**——B 的两个机制都假设"plan_id / order_intent_id 一致且唯一"，但实盘盘前链（path#1）生成的 id 不带策略维度，导致同标的多策略 id 塌缩/双形态并存，B 无正确匹配锚。

**已坐实的根因链（子代理全链路测绘，file:line 为证）：**

```
workflow_service.py:554   candidate_id = f"cand-{ts}-{date}"        ← 不含策略维度(源头病灶)
        ↓
trade_plan_service.py:28  plan_id = f"plan-{candidate_id}"          ← base 跨策略共享同一 id
        ↓
workflow_service.py:580-588  落库瞬间打 -{st.lower()} 后缀 → 随即还原 base
        ↓
DB.plan_id = suffix'd   vs   approval.order_intent_id = base        ← 两形态并存
        ↓ 所有唯一性/join 消费点错配：
  · _query_entry_approvals: join(order_intent_id==plan_id) base↔suffix 匹配不上 → 排除失效
  · request_id = apr-{base} 跨策略共享 → save_approval 按 request_id upsert 塌成一行
  · conflict 回退按 base 比对 → no-op / 误伤赢家(赢家败者共享 base)
  · entry_counts 按 order_intent_id(base) 计数 → 跨策略合并、MAX_ENTRIES 误限
  · system_ext.py:404 candidate join(格式A带 type ≠ 格式B无前缀) → 恒空
```

**关键事实**：`strategy_version_id`(sv) 在 `TradePlanModel`/`ApprovalRequestModel` 两表俱在且已填真实值，是现成的唯一化锚——只是**没被编进 id 串**，join/回退全在比对 id 串而非 (ts_code, sv)。spec 信号路径 C(`workflow_service.py:886` `cand-{sv}-{ts}-{bucket}`)本就带 sv、天然唯一，**证明 sv 入串是正解**。

**当前 main 状态**：B 摘除后，冲突裁决对执行层**零防护**（回退授权与 entry-loop 排除均随 B 移除）。故本阶段必须：先修 id 结构 → 在唯一基线上重做 B。

## 2. 范围（用户 2026-08-26 确认口径）

| 决策 | 选择 |
|---|---|
| id 唯一化锚 | **strategy_version_id (sv)**，对齐 spec 信号路径口径 `cand-{sv}-{ts}-{...}` |
| 改造范围 | **全同根一次到位**：主链 + 同根的 save_approval 塌行 / candidate join 恒空一并修 |
| 历史脏数据 | **不迁移**，id 每日盘前重生、旧计划由 plan_expiry 自然 EXPIRED |

**不动**：spec 信号路径 C(已唯一)；缠论引擎；E1/E2/E3 语义；exit_policies 数值；取价口径。`candidate_service.py:111`(optimizer/回测非实盘路径)仅作口径参考、不在实盘链上，本阶段不强改（若零成本对齐则顺带）。

## 3. 详细设计

### 3.1 生成侧 — 把 sv 编进源头 id

| 位置 | 现状 | 变更 |
|---|---|---|
| `workflow_service.py:554` | `f"cand-{ts}-{date}"` | `f"cand-{sv}-{ts}-{date}"`（sv=该策略 sv_id，源头唯一） |
| `workflow_service.py:429` | `f"cand-{strategy_type.lower()}-{ts}-{date}"`（候选池落库） | 与 :554 **同口径** `f"cand-{sv}-{ts}-{date}"`，确保 plan.candidate_id == asset.candidate_id |
| `workflow_service.py:580-588` | 落库打 `-{st.lower()}` 后缀→还原 base | **整段删除**（源头唯一后后缀冗余，且它正是双形态之源） |
| `trade_plan_service.py:28` | `f"plan-{candidate_id}"` | 不改（自动继承唯一 candidate_id → `plan-cand-{sv}-{ts}-{date}`） |
| `workflow_service.py:637` | `order_intent_id=plan.plan_id` | 不改（plan.plan_id 已唯一，自动带 sv） |
| `approval_center.py:48` | `request_id=f"apr-{order_intent_id}"` | 不改（随 order_intent_id 唯一化，自动消除 request_id 撞车塌行） |

**校验点**：`candidate_id`/`plan_id` 列为 String64，加 sv 后需确认不溢出（实现时断言长度）。

### 3.2 消费侧 — 匹配逻辑对齐

| 位置 | 现状 | 变更 |
|---|---|---|
| `workflow_service.py:603-612` | premarket 手工重建 `{base}-{st.lower()}` 再比 cancelled_ids | 后缀删除后 plan_id 即 base 唯一，**简化为直接比 plan.plan_id ∈ cancelled_ids** |
| `workflow_service.py:1126` | `entry_counts.get(a.order_intent_id or a.request_id)` | 不改（order_intent_id 唯一后自动分策略计数，MAX_ENTRIES 正确按计划隔离） |
| `system_ext.py:404` | `CandidateAssetModel.candidate_id.in_([p.candidate_id ...])` | 不改逻辑（:429/:554 口径统一后 join 自然对上，恒空消除） |
| `sqlalchemy_db.py:388-417` save_approval | 按 request_id(unique) upsert | 不改逻辑（request_id 唯一后不再塌行）；**加测试**证同标的多策略授权各存一行 |

> 消费侧多为"源头唯一后自动对齐"，真正手动改动仅 :603-612 简化。这是"修对源头，下游自愈"的正解，避免在每个消费点打补丁。

### 3.3 重做 B — 冲突裁决在唯一基线上正确实现

**双保险纵深（与原 B 同目标，但建立在唯一 id 上）：**

1. **conflict_resolver_service.py `resolve_plan_conflicts`**：败者置 `plan_status='CANCELLED'` 时，一并回退其对应 approval。
   - 匹配键：`ApprovalRequestModel.order_intent_id == loser.plan_id`（现在 loser.plan_id 唯一带 sv，**只命中败者、不误伤赢家**）。
   - 动作：`status IN (PENDING, APPROVED) → CANCELLED`，写 `decided_at=utc_now()` 镜像 expire 语义。
   - 返回 `reverted_approvals` 计数入结果 dict + 日志。

2. **workflow_service.py 盘中入场**：重建 `_query_entry_approvals`，`outerjoin(TradePlanModel.plan_id == ApprovalRequestModel.order_intent_id)`（唯一后同形态、join 命中），排除 `plan_status IN (CANCELLED/EXPIRED/REJECTED)`；无对应计划的历史授权仍放行（保持既有行为）。两处互为纵深：即便授权漏回退，入场 join 也拦得住。

## 4. 测试（必须真跑到缺陷本体，不打桩被测逻辑）

吸取 czsc 假绿教训。每条测试播种**真实 base/后缀形态**，直连真逻辑：

- **id 唯一性**：盘前多策略同标的 → 断言各策略 candidate_id/plan_id/order_intent_id/request_id **两两不等且带 sv**。回归"塌缩"缺陷。
- **save_approval 不塌行**：同标的两策略 create_approval → 断言 `v2_approval_request` 存 **2 行**（非 upsert 覆盖成 1 行）。
- **candidate join 不恒空**：:429 落库 + :554 建计划同口径 → 断言 `system_ext` 的 candidate join 命中（非空）。
- **重做 B — 不误伤赢家**：同标的两策略(PROD 胜/DRAFT 败)播种计划+授权 → 真调 `resolve_plan_conflicts` → 断言**败者授权 CANCELLED、赢家授权仍 APPROVED**（回归"共享 base 误伤赢家"缺陷）。
- **重做 B — 不放行败者**：败者计划 CANCELLED → 真调 `_query_entry_approvals` → 断言**败者授权不被返回**、赢家被返回（回归"base↔suffix join 失效放行"缺陷）。
- **entry_counts 分策略**：同标的两策略各自入场计数独立、MAX_ENTRIES 不跨策略误限。

## 5. 风险与回滚

- **删后缀补丁改变新 plan_id 形态**：历史 DB 行仍是旧 suffix'd/base 混形态；决策为不迁移，新逻辑只认唯一形态，旧计划由 plan_expiry 自愈为 EXPIRED，不回读。风险：审计期若人工查旧计划需知形态切换点(本 commit)。
- **candidate_id 变长**：sv 入串后长度上升，String64 需实测不溢出（测试断言）。
- **回滚**：本阶段独立 commit，异常可 revert；上游 A/C 不受影响。备份分支 `backup/remediation-p0-pre-rebase` 保留原始三-commit 态。
