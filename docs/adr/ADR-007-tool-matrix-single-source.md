# ADR-007：工具矩阵（tool_matrix）作为工具授权单一源

- **状态**：Accepted（历史回填，2026-10-27；原实现 2026-10-27 WO-LEGION-DEVEXEC3 及 tool_matrix_check）
- **关联**：P4；`tools/tool_matrix_check.py`；`agent_platform/rules/tool_matrix.json`

## 背景

席位/Agent 的工具授权分散在各 profile/代码里，容易出现"某席位悄悄多出写权限"或"文档与实际不一致"。

## 决定

1. `tool_matrix` 是**工具授权的单一登记源**：席位 × 工具 × 执行/只读；
2. 工具默认 read-only；写权限必须显式登记并走 L2 审批；
3. `tool_matrix_check` 校验矩阵与 org/profile 一致性，合并门/blind 检查；
4. 新增工具先登记矩阵，再写代码。

## 后果

- 正面：权限可审计、可自动检查；与 O2 质量分级/ O3 扩展契约衔接。
- 代价：新工具多一步登记；矩阵需随 org 变更更新。
- 不做：不做细粒度 ACL（单机个人平台，矩阵粒度足够）。
