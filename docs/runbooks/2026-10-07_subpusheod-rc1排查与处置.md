# SubPushEod rc=1 根因排查与处置（P0-6）

**日期**：2026-10-07
**排查人**：WS3 排障手
**结论速览**：subscribe.py 代码与飞书推送通道**均健康**；rc=1 是**任务动作层（cmd.exe 拉起阶段）的间歇性失败**，叠加 **10-06 停机错过调度**。代码无需修改；给出用户侧处置动作。

---

## 1. 任务与链路

- 任务：`\SubPushEod`，由 `quant_backtest/deploy_subscribe.ps1` 注册（SYSTEM，每日 15:30 北京 / 07:30Z，StartWhenAvailable，RestartOnFailure 10min×2，ExecutionTimeLimit 30min）
- 动作：`cmd.exe /c ""C:\Program Files\Python311\python.exe" -X utf8 "C:\aios\quant_backtest\subscribe.py" --scan eod >> "C:\aios\quant_backtest\logs\subscribe_task.out" 2>&1"`（工作目录 `C:\aios\quant_backtest`，C:\aios 为指向 `E:\老田的AI助手` 的 junction）
- 兄弟任务 `\SubPushIntraday`：同一动作模式（cmd + python + 同一日志文件），每 30 分钟盘中共 12 次

## 2. 证据链（计划任务事件日志 + 文件系统，只读取证）

| 时间（本地） | 事件 | 结果 |
|---|---|---|
| 10-05 20:33 | 部署注册（StartBoundary 已过 → 注册即自动补跑） | **rc=0x80070001**（cmd.exe 返回 2147942401），日志无输出 |
| 10-05 20:35 | 手动重跑 | **rc=0x80070001**，日志无输出 |
| 10-05 20:36:53 | 重新注册（XML 有改动） | — |
| 10-05 20:37 / 20:40 | 手动运行 | **rc=0，推送成功**（日志 `SUBSCRIBE_OK mode=eod subs=2 ok=2`，状态文件有当日推送记录） |
| 10-06 15:30 | 计划 EOD 时点 | **机器关机，任务从未触发**（事件日志零记录） |
| 10-07 01:37 | 机器开机（C:\Temp 目录时间戳） | — |
| 10-07 01:47:07 | 事件 114：错过的计划 → StartWhenAvailable 立即补跑 | — |
| 10-07 01:47:09 | **rc=0x80070001**，subscribe_task.out **该次零输出**；**同一秒** SubPushIntraday 同模式动作 rc=0 正常完成 | health_latest.json（03:00 快照）记录 `SubPushEod rc=1` |
| 10-07 09:30 | `\公众号选题撰文` 任务 | **同样 rc=0x80070001 / LastTaskResult=1**（非开机时段，机器已运行 8 小时） |

旁证：

- `python -X utf8 C:\aios\quant_backtest\subscribe.py --list` 本机直跑 **rc=0**，5 条订阅清单正常
- `C:\aios` junction 存在且解析正常；Python311 路径存在；col_audit 等 C:\aios 路径任务 10-07 正常运行
- 失败运行均**无 Python traceback** 落日志 → 进程级死在 cmd 拉起/最早期，**不是 subscribe.py 业务异常**（业务异常会打栈且以 rc=1 退出，但伴随可见 traceback）

## 3. 根因判定

1. **直接原因（确定）**：10-06 15:30 EOD 未执行是因为整机关机；开机后 01:47 的补跑在 cmd.exe 拉起阶段失败（0x80070001 ERROR_INVALID_FUNCTION / LastTaskResult=1），Python 根本没跑起来，EOD 汇总当天未送达。
2. **失败层级（确定）**：任务动作环境层，非 subscribe.py 代码、非飞书通道（10-05 两次 rc=0 实证通道正常；01:47 同秒同模式 Intraday 成功实证环境大体验正常）。
3. **触发条件（高度疑似，未实锤）**：间歇性——同一注册在手动启动时成功、自动/补跑启动时失败，且 `公众号选题撰文` 在机器正常运行时段 09:30 复现同码。最常见的已知诱因是**启动瞬间工作目录/重定向目标路径暂不可解析**（C:\aios junction → E: 盘在唤醒/高负载瞬间）或安全软件对 cmd→python 派生的瞬时拦截。精确诱因需一次受控复现（见 §4 动作 1）。

> rc=1 与 0x80070001 的关系：LastTaskResult 记 1、事件 201 记 2147942401，两例任务（SubPushEod、公众号选题撰文）均呈此配对——即健康快照里的 rc=1 就是这次动作拉起失败的结果码，**不要误读为 Python 退出码 1**。

## 4. 需要用户执行的动作（按序）

1. **受控复现定诱因**（5 分钟）：挑非交易时段执行 `schtasks /Run /TN SubPushEod`（会真实补发一次 EOD 汇总，单订阅受每日 3 次熔断保护，最多重复 1 条）。
   - rc=0 → 间歇性成立，重点查 §4.2；
   - rc 仍=1/0x80070001 → 稳定复现，`Get-ScheduledTask -TaskName SubPushEod | Export-ScheduledTask` 导出现网 XML 与 `deploy_subscribe.ps1` 比对（10-05 20:36 的注册与仓库脚本可能不同版）。
2. **加固动作定义（改 `deploy_subscribe.ps1` 后以管理员重跑注册）**——注意该文件在 quant_backtest/（WS2 文件集边缘，本次未代改，建议 WS2/用户落地）：
   - 动作串改为「失败自动重试一次」：`cmd /c ""...python.exe" -X utf8 "...subscribe.py" --scan eod >> log 2>&1 || "python.exe" ... --scan eod >> log 2>&1"`（重复推送风险由 subscribe_state 每日 3 次熔断兜底）；
   - RestartOnFailure 调为 PT5M × 3；
   - 可选：日志与工作目录直用 E: 真实路径替代 junction，排除 junction 瞬时解析因素（需接受路径含中文的既有权衡，deploy 脚本注释表明当初为 ASCII 才走 C:\aios——若维持 junction，则保留重试方案）。
3. **健康检查盲区**：`ops/health_check.py` 只读 LastTaskResult，10-06「整天没跑」与「跑了失败」无法区分。建议后续给 check_tasks 增加 LastRunTime 陈旧度检查（EOD 任务 >26h 未跑即告警）——已列入遗留项。
4. **开机可达性**：10-06 全天停机是漏推的另一半原因。若该站需无人值守，考虑 BIOS 来电自启/不关机策略；否则接受「停机日 EOD 顺延至开机补发」并在 runbook 层面知悉。

## 5. 附：本次顺带排查的两项（原 P2-8）

- **sentinel 族（_sentinelEOD / _sentinelKgTdx / ChanlunRefill / RtdxRtMinPull / Git Updater）rc=0x8007042B**：= 0x42B(1067) ERROR_PROCESS_ABORTED「进程意外终止」。`C:\Temp\sentinel_eod.py` 为一次性哨兵（内部 sleep 等到 15:06/15:31 再验收），这类长时等待进程在**关机/重启时被系统杀掉**即留下该码——10-06 停机与全部此码任务时间吻合。**语义=「上次是被动终止」，非业务故障**；属可接受噪音，建议后续加入 health_check 的 _BENIGN_RC 或按「开机首查豁免」处理。
- **公众号选题撰文 rc=1**：事件日志实证 10-07 09:30 该任务与 SubPushEod 同码失败（0x80070001/LastTaskResult=1）——与 SubPushEod 同一动作层间歇性问题，处置随 §4 一并观察。
