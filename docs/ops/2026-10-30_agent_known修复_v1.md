# agent known 失败横向修复 v1（2026-10-30 · 关羽 P79）

> 背景：P78 修复 agent 套件收集后，4 条 master 复现失败登记为 known。本单横向支援修复其中 2 条真问题，其余 2 条按规则重分类。

## 一、修复项

### 1. `org_audit` 空部门误判（代码 bug，已修）

- 现象：`test_org_audit::test_audit_detects_depts_without_profile` 报「审计结果与实际零档案部门不一致」：审计多出 `trading`，独立口径为空。
- 根因：`engine/org_audit.py` 的 DEPT_NO_PROFILE 判定 `if not profs and not matrix_ids` **漏了 `ids` 条件**——
  `agents=[]` 的空壳部门（trading 迁移后为空）被误报为「整部门零档案」。
- 修复：改为 `if ids and not profs and not matrix_ids`（仅名单有席位才算零档案部门）。
- 验证：`test_org_audit.py` 全文件通过（与 `test_audit_real_summary` 等 12 项兼容）。

### 2. `test_d1_json_contract` 契约测试漂移（测试 stale，已对齐）

- 现象：非决策节点断言 user prompt 含「3 句以内」失败。
- 根因：现行契约已分双分支（菜鸟团队分析移植后）：有 `analysis_type` JSON Schema 指令的节点追加「先正文后 ```json```」；
  无 schema 的类型才保留「3 句以内」尾句；两种都不得出现决策节点专用的「严格只输出一个 JSON」。
- 修复：测试改为按双分支断言（T1=market_technical → schema 指令；unknown_type_xyz → brief tail）。
- 验证：`test_d1_json_contract.py` 全文件通过（10 tests）。

## 二、重分类

| 用例 | 原分类 | 新分类 | 依据 |
|---|---|---|---|
| `test_zhaoyun_view::test_build_view_full` | known | **flaky** | 全量 3 轮 1 失败/2 通过，隔离 3/3 通过（顺序/数据状态） |
| `test_invest_staff_audit::test_audit_all_green` | known（保留） | known（保留） | org invest 现有 **9 席（含 TR1）** vs 审计器 `SEATS=8`；属 agent 线编制迁移（TR1 周瑜调入）待确认，due 11-06 |

## 三、效果

- agent 全量：**8 failed → 5 failed**（4 条 env worktree 数据缺失 + 1 条 known invest；2449 passed）；
- d1 / org_audit 两条从 known 移除；flaky 11→12（zhaoyun）；known 4→1（invest）；
- 探针 1 轮复核（结果见日志）用于确认无未登记失败。

## 四、后续

1. agent 线确认 TR1 归属：若 invest 应为 9 席 → 更新 `tools/invest_staff_audit.py` SEATS/检查项与 4 个测试；若不应在 invest → 修正 org.json（owner 决策后再动）。
2. 12 条 flaky 按 7 轮规则继续观察（含 zhaoyun / lock 两条新登记）。
