# R0–R7 全栈门表（草案 v0.9 · 待 ARCH1 拍板）

> 状态：**草案**（2026-10-30，DSH-Lead 起草）· 待 ARCH1 确认口径后转 v1.0
> 目的：把「立项→设计→接口→实现→测试→合并→验收→复评」的既有门禁固化成一张表，
> 供作战屏显示阶段状态/审计红点，并作为虚拟小队模板的骨架。
> 原则：不新增重复门禁；每道门必须指向**已有命令/证据**，否则标记"待建"。

## 一、门表总览

| 门 | 名称 | 主责 | 关键产物 | 已有命令/证据 | 状态 |
|---|---|---|---|---|---|
| R0 | 立项门 | ARCH1/A0 | 立项单+白名单+边界 | `docs/plans/*立项*`；任务单 | 已有（未统一模板） |
| R1 | 设计门 | ARCH1/组长 | 设计文档/ADR/数据契约 | `docs/plans/`、`docs/dev/*_v1.md` | 已有（口径散） |
| R2 | 接口门 | 接口人/DEV3/DEV4 | API/页面/字段契约 | `portal/tests/test_*_contract.py`、`_mixin_integrity.py` | 部分（契约测试覆盖不足） |
| R3 | 实现门 | worker | commit+静态检查 | ruff E9,F；py_compile；单测本地 | 已有 |
| R4 | 测试门 | worker/OPS1 | 测试报告 | `test_gate.ps1 -Suite fast/...`；新增失败=0 | 已有（tools 全量夜间化待批） |
| R5 | 合并门 | DSH/质量闸 | merge commit+门禁记录 | `devtask.py merge`（内联质量闸+merge 锁+pre-merge hook+凭据/L2/导航/端口） | 已有（2026-10-29 内联） |
| R6 | 验收门 | 黄忠/QA1/席位 | 验收单+证据 | 黄忠 `huangzhong_daily.py`；会议验收 evidence；服务探活 | 已有（模板待统一） |
| R7 | 复评门 | DSH/A0 | 复盘/回滚判据/观察指标 | 周报、复盘回执、观察台账（如 HK R1） | 部分（未强制） |

## 二、逐门定义（验收口径）

### R0 立项门
- **输入**：需求来源（A0/老田/组）、价值假设、影响面、数据/权限/预算边界。
- **产物**：立项单（含白名单路径、L2 风险登记）；`devtask new` 工单。
- **出口判据**：负责人确认 + 白名单明确 + 不做清单明确；跨部门需 ARCH1 批。
- **证据**：工单 note + `docs/plans/` 立项文档。

### R1 设计门
- **输入**：R0 立项单。
- **产物**：设计文档/ADR（接口、数据、失败模式、回滚、可观测）。
- **出口判据**：≥1 名非作者评审（组长/接口人）；关键取舍有记录。
- **证据**：设计文档 + 评审意见（工单 note/会议纪要）。

### R2 接口门
- **输入**：R1 设计。
- **产物**：API/页面/字段契约；契约测试（golden/结构快照）。
- **出口判据**：契约测试绿；Mixin 方法集零 diff；接口人确认（PM1/DSGN1/QA1 对应域）。
- **证据**：`test_*_contract.py`、`_mixin_integrity.py --check`、接口确认回执。

### R3 实现门
- **输入**：R2 契约。
- **产物**：可分 review 的 commit（一单一事、worktree）。
- **出口判据**：`ruff --select E9,F` 0；`py_compile` 通过；相关单测本地绿；无明文凭据。
- **证据**：commit + 本地测试输出。

### R4 测试门
- **输入**：R3 commit。
- **产物**：测试报告（新增/存量/flaky 分类）。
- **出口判据**：`test_gate -Suite fast` 新增失败=0；改动 tools 测试必跑；夜间全量（待建任务）无新增。
- **证据**：`C:\Temp\test_gate_*.log`；基线 `tools/test_baseline.json`。

### R5 合并门
- **输入**：R4 通过 + 分支 ahead>0。
- **产物**：merge commit（`devtask.py merge`）。
- **出口判据**：内联质量闸 PASS；merge 锁持有；pre-merge hook PASS；凭据/L2/导航/端口门全绿；HEAD 竞态自动重试后成功。
- **证据**：merge 日志、`devtask_merge_timing_*.json`、dev_events（merge_done/guard_fail）。

### R6 验收门
- **输入**：R5 merge + 部署/换血。
- **产物**：验收单（页面/API/服务/数据/回归证据）。
- **出口判据**：基线+红队通过（黄忠）；服务探活 200；页面关键路径可用；失败可回滚。
- **证据**：验收会纪要/evidence、探活记录、截图/日志。

### R7 复评门
- **输入**：R6 验收 + 观察期数据。
- **产物**：复盘（指标/问题/回滚或保留决定/后续单）。
- **出口判据**：观察指标达标或明确回滚；经验回写记忆/判例；下轮单已建。
- **证据**：复盘回执、周报指标、观察台账。

## 三、与现有门禁的映射（不改行为）

| 现有门禁 | 归属门 | 说明 |
|---|---|---|
| `devtask.py merge` 内联质量闸 | R4/R5 | fast 测试 + 变更 tools 测试 |
| merge 锁 / pre-merge hook | R5 | 串行化 + 二次测试 |
| `_mixin_integrity --check` | R2/R5 | 拆分防丢方法 |
| `scan_secrets`/gitleaks/L2 守卫 | R3/R5 | 凭据与危险操作 |
| `check_ports --strict`/`unify_nav --check` | R3/R5 | 端口/导航 |
| 黄忠验收 + 会议 acceptance | R6 | 只验不改；证据入纪要 |

## 四、待 ARCH1 拍板项

1. R0–R7 命名与主责是否采用（或并入 ARCH1 既有 R0–R7 定义）；
2. R6 验收是否强制"黄忠签字"（当前观察模式）；
3. R7 复评是否纳入每周五周报硬性栏目；
4. 门表落点：`docs/dev/fullstack_gates.md`（本文件）+ 作战屏接入（P2-12）。

## 五、变更记录

- v0.9 2026-10-30：首版草案（DSH-Lead），已有门禁映射，待 ARCH1 评审。
