# S3-08 终验收一键检查（2026-11-02T15:56:22+08:00）

> 工具：`tools/datawarehouse/final_acceptance.py v1` ｜ 定义：deliverables/2026-11-02_S3-08终验收_检查清单_v1.md §1（G1–G10） ｜ 只读（不触发任务/不改数据/不重启）

## 总判定：**FAIL** 🔴

- 验收日：2026-11-02 ｜ 今晚（T 窗）：2026-11-02 ｜ 采集：2026-11-02T15:56:22+08:00
- 计数：pass=4 / fail=4 / expected-risk=1 / unknown=1（共 10 Gate）

| Gate | 内容 | 状态 | 判定依据/证据 |
|---|---|---|---|
| G1 | 今晚 TdxSync 四批（任务+数据级） | 🔴 fail | last_result_nonzero:\ZeroBound\TdxSync_A_高频=267011; not_run_in_night:\ZeroBound\TdxSync_A_高频(1999-11-30T00:00:00+08:00); last_result_nonzero:\ZeroBound\TdxSync_B_财务=267011; not_run_in_night:\ZeroBound\TdxSync_B_财务(1999-1；tasks=4；rows_delta_total=None；fresh_tables=0 |
| G2 | TdxSyncProbe 严格判定（19:20+） | ❔ unknown | 无 19:20 后 probe 严格判定记录（not_found）；ts=None；code=None；fail=None；lock_hits=None |
| G3 | qualify 分布 / K8 复判 | ✅ pass | unknown=15 < 40；unknown=15 / threshold=40；dist={'deprecated': 8, 'evidence_ok': 170, 'evidence_partial': 54, 'planned': 9, 'unknown': 15} |
| G4 | XmapSync 明早首跑 | ✅ pass | rc=0 且今日已跑；last_run=2026-11-02T14:37:47+08:00；last_result=0；state=Ready |
| G5 | KgDailySync 链 | ✅ pass | KgDailySync rc=0 + catalog 当日更新 + registry sha 对齐；last_run=2026-11-02T06:20:01+08:00；last_result=0；state=Ready |
| G6 | DataWarehouseSnapshot 首跑（K3） | 🔴 fail | catalog_version_mismatch(snapshot=28/catalog=32)；last_run=2026-11-02T10:06:47+08:00；snapshot=2026-11-02T14:05:55+08:00；log_ok=True；catalog=28 |
| G7 | NoticeBackfill（PT8H） | 🔴 fail | last_result=267014；last_run=2026-11-02T14:46:43+08:00；last_result=267014；state=Ready |
| G8 | A3RotationCanary（accepted-risk） | 🟡 expected-risk | A3 canary rc=1 属 accepted-risk（裁定 B；8 处旧值未轮换不变）；last_run=2026-11-02T07:00:01+08:00；last_result=1；state=Ready |
| G9 | SentimentBT 首跑 | 🔴 fail | not_run_today(last_run=1999-11-30T00:00:00+08:00)；last_run=1999-11-30T00:00:00+08:00；last_result=267011；state=Ready |
| G10 | KPI 复跑（kpi_report） | ✅ pass | ok=9>=9、unknown=0、K3=ok；kpi={'ok': 9, 'warn': 1, 'unknown': 0, 'total': 10}；K3=ok |

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

