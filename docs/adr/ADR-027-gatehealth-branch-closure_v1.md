# ADR-027 · GATEHEALTH 分支结案归档（不申请 H5 钉扎例外）

- 状态：**Proposed（待老田批准）**
- 决策人：老田（待签）；执行：DSH-Lead
- 日期：2026-11-10
- 关联：ADR-006（worktree 隔离）、ADR-013（范围与文档纪律）、ADR-015（内核/组合）、ADR-025（治理嵌入）；`tools/merge_gated.py` H5-a 钉扎；用户 2026-11-08「方案 A」批准
- 依据：`C:\Temp\gatehealth_decision_20261110.md`（只读评估）、`deliverables/2026-11-10_GATEHEALTH结案回执_v1.md`

## 背景

1. 分支 `task/K-STOCK-ANALYSIS-GATEHEALTH-20261107`（tip `f64af59fa`，2026-11-07 22:11）起因是补齐 fast 门覆盖：analysis/commercial 两套目录此前从不进门，同时附带 closeout 动态基线、冻结文件拆分与旧基线重生成。
2. 该分支 merge-base `8f1667860`，至 2026-11-10 已落后 master **355** 提交、独有 **9** 提交；其修改 `tools/test_gate.ps1` 属 H5-a 钉扎文件，按现行规则分支不得自行合入。
3. 核心能力已由 master `5bf84007f`（2026-11-09 16:17，`feat(gate): fast 增 analysis/commercial（7→9 套件）+ 冻结守卫同步（H5 钉扎，Lead 手动合并；用户 2026-11-08「方案 A」批准）`）落地：fast 门现为 9 套件并同步冻结守卫。
4. 分支其余内容已过时：`DEVELOPER_GUIDE.md` 分支版 31,791B vs master 76,157B；nav.js / `portal_route_baseline.json` / `mixin_baseline.json` 为 11-07 旧重生成；分支 `test_gate.ps1` 落后 master 114 行（缺后续守卫）。

## 决策

1. **GATEHEALTH 分支结案归档**：不再 rebase、不进合并队列、不申请 H5 钉扎例外（gate 变更已由 `5bf84007f` 完成，无需例外）。
2. **分支指针保留**（`task/K-STOCK-ANALYSIS-GATEHEALTH-20261107` → `f64af59fa`）作为历史存档；worktree `C:\项目\老田的AI助手\.worktrees\K-STOCK-ANALYSIS-GATEHEALTH-20261107` 在 11-11 窗口外清理，清理前保持只读。
3. 若「closeout 动态基线」仍有 master 未覆盖的独有语义，另起小单在最新 master 上按正常流程重做（不背 355 提交历史）。
4. 本 ADR 不改变 H5-a 钉扎规则：后续任何 `tools/test_gate.ps1` / `tools/test_baseline.json` 变更仍走「独立审批 + Lead 手动合并」。

## 影响

- 正面：移除一个 355 提交的陈旧分支对队列与评审的干扰；避免为已落地能力重复申请例外；R3 遗留项闭环。
- 代价：无代码变更；仅保留分支指针与结案文档。
- 回滚：恢复分支进入队列即可（指针未删除），无需数据回滚。

## 审批签名

| 角色 | 结论 | 姓名 / 日期 |
|---|---|---|
| 老田 | 待签 | |
| A0 | 不适用（非产品口径） | |
| ARCH1 | 不适用（非架构变更） | |
