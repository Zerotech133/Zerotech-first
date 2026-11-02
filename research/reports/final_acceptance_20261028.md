# S3-08 终验收一键检查（2026-11-03T06:43:28+08:00）

> 工具：`tools/datawarehouse/final_acceptance.py v1` ｜ 定义：deliverables/2026-11-02_S3-08终验收_检查清单_v1.md §1（G1–G10） ｜ 只读（不触发任务/不改数据/不重启）

## 总判定：**FAIL** 🔴

- 验收日：2026-10-28 ｜ 今晚（T 窗）：2026-10-27 ｜ 采集：2026-11-03T06:43:28+08:00
- 计数：pass=1 / fail=8 / expected-risk=0 / unknown=1（共 10 Gate）

| Gate | 内容 | 状态 | 判定依据/证据 |
|---|---|---|---|
| G1 | 今晚 TdxSync 四批（任务+数据级） | 🔴 fail | not_run_in_night:\ZeroBound\TdxSync_A_高频(2026-11-02T17:35:00+08:00); not_run_in_night:\ZeroBound\TdxSync_B_财务(2026-11-02T17:45:00+08:00); not_run_in_night:\ZeroBound\TdxSync_C_低频(2026-11-02T17:55:00+08:00); not_run_in_ni；tasks=4；rows_delta_total=None；fresh_tables=7 |
| G2 | TdxSyncProbe 严格判定（19:20+） | ❔ unknown | 无 19:20 后 probe 严格判定记录（not_found）；ts=None；code=None；fail=None；lock_hits=None |
| G3 | qualify 分布 / K8 复判 | ✅ pass | unknown=14 < 40；unknown=14 / threshold=40；dist={'deprecated': 8, 'evidence_ok': 187, 'evidence_partial': 38, 'planned': 9, 'unknown': 14} |
| G4 | XmapSync 明早首跑 | 🔴 fail | not_run_today(last_run=2026-11-03T06:00:01+08:00)；last_run=2026-11-03T06:00:01+08:00；last_result=0；state=Ready |
| G5 | KgDailySync 链 | 🔴 fail | task_last_result=267009; not_run_today(last_run=2026-11-03T06:20:01+08:00); catalog_not_updated_today; registry_sha_mismatch；last_run=2026-11-03T06:20:01+08:00；last_result=267009；state=Running |
| G6 | DataWarehouseSnapshot 首跑（K3） | 🔴 fail | not_run_after_0640(last_run=2026-11-03T06:40:01+08:00); snapshot_not_today(generated_at=2026-11-03T06:40:32+08:00); dw_log_not_today；last_run=2026-11-03T06:40:01+08:00；snapshot=2026-11-03T06:40:32+08:00；log_ok=True；catalog=35 |
| G7 | NoticeBackfill（PT8H） | 🔴 fail | not_run_today(last_run=2026-11-02T14:46:43+08:00)；last_run=2026-11-02T14:46:43+08:00；last_result=267014；state=Ready |
| G8 | A3RotationCanary（accepted-risk） | 🔴 fail | not_run_today(last_run=2026-11-02T07:00:01+08:00)；last_run=2026-11-02T07:00:01+08:00；last_result=1；state=Ready |
| G9 | SentimentBT 首跑 | 🔴 fail | not_run_today(last_run=1999-11-30T00:00:00+08:00)；last_run=1999-11-30T00:00:00+08:00；last_result=267011；state=Ready |
| G10 | KPI 复跑（kpi_report） | 🔴 fail | ok=8 < 9; K3=warn（期 K3 转 ok：dw_snapshot 每日自动）；kpi={'ok': 8, 'warn': 2, 'unknown': 0, 'total': 10}；K3=warn |

## 说明

- G1 数据级 rows 增量与 G2 同源（TdxSyncProbe 严格窗口记录）；probe 未装/未跑时 G1/G2=unknown（不硬编码判过）
- G4–G9 以「LastRunTime.date == 验收日」判明早窗口是否跑过；跨日长任务（NoticeBackfill PT8H）以 LastResult 为准
- G10 导入 kpi_report.build_report()（只读，不写文件）；K10 的 warn 不计失败（清单口径）
- expected-risk 仅 G8（A3 canary rc=1，裁定 B）计入；其它 Gate 不接受 expected-risk

## 明早 09:00 复跑

```powershell
cd C:\项目\老田的AI助手
& 'C:\项目\py312\python.exe' -X utf8 tools\datawarehouse\final_acceptance.py --date 20261103
# 退出码 0=PASS / 1=FAIL / 2=UNKNOWN；JSON=research/reports/final_acceptance_20261103.json
```

