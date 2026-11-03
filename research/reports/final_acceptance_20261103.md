# S3-08 终验收一键检查（2026-11-03T09:44:32+08:00）

> 工具：`tools/datawarehouse/final_acceptance.py v1` ｜ 定义：deliverables/2026-11-02_S3-08终验收_检查清单_v1.md §1（G1–G10） ｜ 只读（不触发任务/不改数据/不重启）

## 总判定：**PASS** ✅

- 验收日：2026-11-03 ｜ 今晚（T 窗）：2026-11-02 ｜ 采集：2026-11-03T09:44:32+08:00
- 计数：pass=9 / fail=0 / expected-risk=1 / unknown=0（共 10 Gate）

| Gate | 内容 | 状态 | 判定依据/证据 |
|---|---|---|---|
| G1 | 今晚 TdxSync 四批（任务+数据级） | ✅ pass | 四批 rc=0 + rows_delta_total>0 + 关键表 part 已刷新；tasks=4；rows_delta_total=22278；fresh_tables=3 |
| G2 | TdxSyncProbe 严格判定（19:20+） | ✅ pass | 严格窗口 rc=0 / fail=0 / lock_hits=0；ts=2026-11-02T23:20:03+08:00；code=0；fail=0；lock_hits=0 |
| G3 | qualify 分布 / K8 复判 | ✅ pass | unknown=14 < 40；unknown=14 / threshold=40；dist={'deprecated': 8, 'evidence_ok': 187, 'evidence_partial': 38, 'planned': 9, 'unknown': 14} |
| G4 | XmapSync 明早首跑 | ✅ pass | rc=0 且今日已跑；last_run=2026-11-03T06:00:01+08:00；last_result=0；state=Ready |
| G5 | KgDailySync 链 | ✅ pass | KgDailySync rc=0 + catalog 当日更新 + registry sha 对齐；last_run=2026-11-03T09:06:25+08:00；last_result=0；state=Ready |
| G6 | DataWarehouseSnapshot 首跑（K3） | ✅ pass | 06:40 后 rc=0 + snapshot 当日 + 日志 ok + catalog 版本一致；last_run=2026-11-03T06:40:01+08:00；snapshot=2026-11-03T06:40:32+08:00；log_ok=True；catalog=35 |
| G7 | NoticeBackfill（PT8H） | ✅ pass | rc=0 且今日已跑；last_run=2026-11-03T09:15:31+08:00；last_result=0；state=Ready |
| G8 | A3RotationCanary（accepted-risk） | 🟡 expected-risk | A3 canary rc=1 属 accepted-risk（裁定 B；8 处旧值未轮换不变）；last_run=2026-11-03T07:00:01+08:00；last_result=1；state=Ready |
| G9 | SentimentBT 首跑 | ✅ pass | rc=0 且两产物 <48h、无当日 push；last_run=None；last_result=None；state=None |
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

