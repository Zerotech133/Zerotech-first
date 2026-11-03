# ADGS 试点 11-03 开盘检查回执 v1

- 执行：2026-11-03 08:30（计划：`pilot_open_1103.md`）
- 性质：只读检查；未启停服务、未 git 运行时变更、未改任务/DB
- 结论：**开盘检查 PASS，WIP=0，可进入首日试点**

## 一、5 分钟检查结果

| 检查 | 结果 |
|---|---|
| L1 | **18/18，FAIL=0，PENDING=0** |
| 8734 / 8091 / 8105 / 1411 | LISTEN ✅ |
| 5175 / 8102 | OFF ✅ |
| DB alembic_version | **0049** ✅ |
| `zhice_migration_preflight.py --db` | **PREFLIGHT PASS，rc=0** |
| `/api/dw/stats` | **401**（鉴权生效）✅ |
| `/acad1/pool` | **200** ✅ |
| `/quant/` | **302** ✅ |
| `/probe/zhice` | **200** ✅ |
| 任务板 in_progress | **0**（无并行 WIP）✅ |
| task-5 / task-6 | pending，保留至 11-16 复审 ✅ |

## 二、WIP（≤6）

- 同时 DOING：**0**
- 本日计划 6 项：ACL-105 24h 观察（11:30）、11-04 黄忠准备、UF-17 复核、DWH-103 wave3、ACL-103 运行时（W3，暂不启动）、18:00 首日对账。
- 当前未新开任务单；11-16 前 task-5/6 不收口。

## 三、时间门

- **11:30 ACL-105 24h 观察**：已有 schedule，重点 `v2_score_component` 写入/ACL 一致性/8105 错误率；异常回滚 `C:\项目\backups\zhice_pre_0049_20261102.dump`。
- **18:00 ADGS 首日对账**：已有 schedule，产出 `reconcile_2026-11-03.md`。
- **11-04 09:15/09:30 黄忠验收**：已有 schedule；④满窗后 review，当前 NO-GO 属设计内。

## 四、边界

- L2/ACL-105 已于 11-02 提前执行；本开盘不重复跑窗口；
- 未触碰 5175/8102（off）、未改 nginx/凭据；
- 无门禁绕过。
