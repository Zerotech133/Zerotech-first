# ADGS 试点 11-03 开盘执行包 v1

- 编制：2026-11-02 15:20｜执行：2026-11-03 08:30
- 基线看板：`deliverables/2026-11-02_ADGS试点/pilot_board_v1.md`（T0 状态已过时，本文件为开盘实际态）
- 边界：只登记/对账，不启停服务、不 git 操作、不删除 worktree

## 一、10 项工作项实际状态（11-02 15:20）

| ID | 车道 | 实际状态 | 证据 |
|---|---|---|---|
| ACL-101 | 快速 | **DONE** | `deliverables/2026-11-02_ACL-101_证据_v1.md` |
| ACL-102 | 标准 | **DONE** | `deliverables/2026-11-02_ACL-102_证据_v1.md` |
| ACL-103 | 治理 | **DONE（wave1）** | `deliverables/2026-11-02_ACL-103_实现证据_v1.md`；契约 `agent_platform/catalogs/contracts/v2_acad1_pool.odcs.yaml` |
| ACL-104 | 紧急 | **DONE/CLOSED** | phase1/2 证据 + `docs/audit/2026-11-02_ACL-104_BUG-001_止血复盘_v1.md`（ACL-105 已补） |
| ACL-105 | L2 | **DONE（观察中）** | `docs/ops/2026-11-02_L2与ACL-105执行回执_v1.md`；2h 观察回执；24h 观察 11-03 11:30 |
| DWH-101 | 快速 | **DONE** | `deliverables/2026-11-02_DWH-101-104_证据_v1.md` |
| DWH-102 | 标准 | **DONE** | `deliverables/2026-11-02_DWH-102_证据_v1.md` |
| DWH-103 | 治理 | **DONE（wave1+wave2）** | `deliverables/2026-11-02_DWH-103_实现证据_v1.md`；wave2 证据；K5 登记 |
| DWH-104 | 快速 | **DONE** | `deliverables/2026-11-02_数据仓暴露面盘点_v1.md` |
| DWH-105 | 标准 | **DONE** | `deliverables/2026-11-02_DWH-105_证据_v1.md`；DWH-105 已随 8091 换血生效 |

**结论**：10/10 交付；剩 ACL-105 24h 观察（11-03 11:30）与 11-04 黄忠验收两条时间门。

## 二、开盘 WIP（同时 DOING ≤6）

| 优先级 | 事项 | owner | 说明 |
|---|---|---|---|
| P1 | ACL-105 24h 观察 | ARCH1/DATA1 | 11:30 执行，重点 `v2_score_component` 写入/ACL 一致性/错误率 |
| P2 | 11-04 黄忠验收准备 | ACAD1 | 07:47 后重跑 review（④满窗）；09:30/10:00 验收；L3 签字 |
| P3 | UF-17 收口复核 | DATA1 | dirty=0 已达成；复核 VERSION/扩展/`/quant` 重建 |
| P4 | DWH-103 后续（wave3/W3） | ARCH1 | registry YAML v2 / strict ref / 运行时强制（不阻塞开盘） |
| P5 | ACL-103 运行时强制点 | ARCH1 | W3，先不启动 |
| P6 | ADGS 首日对账 | Lead | 18:00 按模板出数 |

**WIP 计数**：DOING=0（开盘前）；开盘后按上表可同时开 ≤6。

## 三、08:30 开盘检查（5 分钟）

1. `python -X utf8 tools/l1_acceptance.py` → 期望 18/18；
2. 端口：8734/8091/8105/1411 LISTEN；5175/8102 off；
3. DB `alembic_version=0049`；`preflight --db` rc=0；
4. `/api/dw/stats`=401、`/acad1/pool`=200、`/quant/`=302、`/probe/zhice`=200；
5. 任务板：`team_task_list` 无 in_progress；task-5/6 保留到 11-16 复审（不收口）。

## 四、18:00 首日对账（模板 §十四 B）

产出：`deliverables/2026-11-02_ADGS试点/reconcile_2026-11-03.md`，含：
- 完成项：ACL-105 24h 观察结果；10/10 工作项状态复核；
- WIP：doing 数、阻塞项、异常；
- 证据新增/缺失；门禁绕过=0；
- Lead time 抽样（快速/标准/治理各 1 项）。

## 五、风险与边界

- 11-04 ④满窗前 Go/No-Go 仍是 NO-GO（设计内），不得提前写正式回执；
- 并行 W2 数据仓会话若仍在改 `portal/static`/`research/reports`，本线不触碰；
- L2/宿主机操作仍需 A0 批准；本开盘包不执行任何变更。
