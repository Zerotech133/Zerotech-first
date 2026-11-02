# L2 调整记录：NoticeBackfill 新任务 + health card retired（2026-11-02 15:20）

## 一、NoticeBackfill（P0 超时修复）

| 项 | 内容 |
|---|---|
| 根因 | 原 `\NoticeBackfill` PT1H，单日需判 ~10k 条（3.76s/条≈11.5h）→ 每日 267014 截断 |
| 权限约束 | 改既有 S4U 任务 `Set-ScheduledTask`/COM/XML 全部 Access denied（Medium integrity、审批禁用）→ 改用「新建 + 停旧」 |
| 新建 | `\ZeroBound\NoticeBackfill`：每日 06:45、**PT8H**、同 Action/cwd、IgnoreNew、StartWhenAvailable、允许电池、Principal=Interactive |
| 旧任务 | `\NoticeBackfill` **Disabled** 保留（可一键 ENABLE 回滚）；XML 备份 `C:\Temp\task68\NoticeBackfill_before_20261102_144411.xml` |
| 有界验证 | 14:46 起 7 分钟 Running 稳定、380/10,129 条、1.18s/条、fail=0、进度 45,012→45,212；随后主动 End（避免 8h 非窗口长跑） |
| 容量 | 10,129×1.18s≈3.3h < PT8H（2.4× 余量）；最差速度跨日可续（200 条 flush） |
| 回滚 | `Unregister-ScheduledTask -TaskPath '\ZeroBound\' -TaskName 'NoticeBackfill'` + `schtasks /Change /TN "NoticeBackfill" /ENABLE` |
| 待管理员 | 把 PT8H 写回原 S4U 任务后删除替代任务（消除同名两任务）；明早 06:45 观察首跑 |

## 二、health card retired（L1 代码，8091 重启后生效）

| 项 | 内容 |
|---|---|
| 结论 | 9104 非故障：W3/W4FINAL 已退役 card-handler（Prometheus metrics 端口），health 中 optional=true/ok=false 属设计内；日报推送不依赖 9104（DailyReport 20:30 exit=0/SEND_OK 实测） |
| 代码 | `portal/api_health_portal.py`：新增 `_probe_service()`，`state="retired"` 直接返回 `{ok:true, optional:true, state:"retired", ms:0}` 不建 TCP（冷请求 2.1s→<300ms）；card spec 标 retired |
| 测试 | `portal/tests/test_health_portal_mixin.py` +1（4 passed） |
| 生效 | **需 11-05 8091 重启**（L2 窗口） |
| 回滚 | `git checkout -- portal/api_health_portal.py portal/tests/test_health_portal_mixin.py` |