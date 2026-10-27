# ADR-003 · 研发单路由与 ARCH1 工具矩阵授权（A0 L1 批）

- **日期**：2026-10-27
- **登记岗**：ARCH1
- **关联工单**：WO-LEGION-DEVRULE-20261027 / WO-LEGION-ARCH1-TOOLS-20261027 / WO-LEGION-MATRIX4-20261027
- **状态**：accepted（复核期 2026-11-10 到期回访，QA1 按此复核）

## 背景
2026-10-27 前研发需求常直接落到 DSH/治理席，归属漂移；同时 ARCH1 缺代码库智能/隔离验证/评测/观测四类工具，架构评审只能人工读码；A0 当天以 L1 批出两条治理口径，需收敛成可审计架构决策。

## 决策
①研发单路由：新研发需求优先派 ARCH1/组长（G1 DEV1 / G2 DEV5），不得直接落 DSH/治理席；例外仅 A0/老田显式指派、跨部门治理、研发部请求支援且 ARCH1 确认（AGENTS §4 + docs/plans/2026-10-27_研发单路由规则_ARCH1优先_v1.md）。②工具授权：ARCH1 org tools 增 codebase_intel/sandbox_verify/eval_harness/trace_log，并同步 tool_matrix execute 白名单（org/team/bundle 一致）。③流程与工具均 L1，不做代码强制。

## 代价（明确接受的坏处/限制）
路由靠流程遵守而非代码门禁，DSH/治理误接研发单需人工退回；四工具为只读/隔离验证用途，不扩生产权限；ARCH1 周巡检工作量增加（ARCH1ArchPatrol 周一 09:50）。

## 复核期
2026-11-10——到期由 QA1 按本 ADR 回访；若推翻，新 ADR 引用本条编号并置本条 status=superseded。
