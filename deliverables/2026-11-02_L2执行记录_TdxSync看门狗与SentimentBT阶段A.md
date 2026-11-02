# L2 执行记录：TdxSync 看门狗 + SentimentBT 阶段 A（2026-11-02 11:33）

> 依据：老田「按计划实施」+ 两份 L2 申请单五要素；执行：DSH-Lead

## 一、TdxSync 看门狗（已安装并验证）

| 项 | 结果 |
|---|---|
| 任务 | `\ZeroBound\TdxSyncProbe`（每小时 :20，PT10M，IgnoreNew，StartWhenAvailable，允许电池） |
| Action | powershell → `py312 -X utf8 tools/datawarehouse/tdx_sync_probe.py --json *>> C:\Temp\tdx_sync_probe_task.log; exit $LASTEXITCODE` |
| 注册/首次运行 | ✅ REGISTER_OK；11:31:44 触发 **LastTaskResult=0**；NextRun 12:20 |
| 修 bug | 修 `--help` argparse `%TEMP%` 转义崩溃（`%TEMP%`→`%%TEMP%%`），修复后 help 正常、预窗口实跑 rc=0 |
| 判定逻辑 | 首夜前只记录（awaiting_first_run）；今晚 19:20 首次严格判定，期望 code=0 且 rows_delta_total>0 |
| 回滚 | `Unregister-ScheduledTask -TaskName TdxSyncProbe -TaskPath \ZeroBound\` |

## 二、SentimentBT 阶段 A（零外发，已安装并首跑）

| 项 | 结果 |
|---|---|
| 首跑（人工） | `py311 news_collector/jev_impact.py`（无 --push）rc=0；输出 n=7632 impact 分层正常 |
| 产物 | `sentiment_bt_result.json` / `jev_impact.json` **age=0.00h**（11:32 新写）；`sentiment_bt_pushed.json` 仍 10-14 20:35（**零推送**，水位未动） |
| daily_report | 数据行恢复：n=7632 / corr=0.032 / 高置信 n=6901 / impact 分层正常（L1 止血告警行消失） |
| 任务 | `\ZeroBound\SentimentBT` 每日 **07:20**（py311 jev_impact.py，cwd=news_collector，PT20M，IgnoreNew，允许电池）→ NextRun 11-03 07:20 |
| 阶段 B（--push） | **未启用**；需另批授权 + 水位 seed（level=1300/n=1334 → 新 milestone 预计 3000+）并验证 `PUSH skip` |
| 回滚 | `Unregister-ScheduledTask -TaskName SentimentBT -TaskPath \ZeroBound\` + 恢复 C:\Temp\sentiment_bt_backup_20261102\ 产物/水位 |

## 三、验证命令（可复跑）

```powershell
schtasks /Query /TN "\ZeroBound\TdxSyncProbe" /FO LIST
schtasks /Query /TN "\ZeroBound\SentimentBT" /FO LIST
Get-Content C:\Temp\tdx_sync_probe_task.log -Tail 2
C:\项目\py312\python.exe -X utf8 -c "import sys;sys.path.insert(0,'news_collector');import daily_report;print(daily_report._data_line())"
```

## 四、注意

- 今晚 17:35–18:15 四批 + GlobalFutSync 首夜，**19:20 看门狗首次严格判定**（数据级）；
- SentimentBT 阶段 A 连续 age<48h 达 3 天后，再另批评估阶段 B（--push）；
- 11-05 06:00 窗口：8091 重启 + nginx 接线（提醒已登记）。