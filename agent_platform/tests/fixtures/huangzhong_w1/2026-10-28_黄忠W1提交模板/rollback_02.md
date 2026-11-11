# 回滚点：OTel 开关回滚
> STATUS: frozen（黄忠验收夹具/样例文件，非计划；2026-10-31 W2 收口）



## 回滚步骤

```powershell
# 备份/恢复或 revert：
env ZHAOYUN_OTEL=0；或 git revert <sha>
# 重启：
New-Item -ItemType File -Path C:\Temp\LEGION_8796_RESTART.flag -Force
Start-ScheduledTask -TaskName LegionOps
# 验证：
record() → otel.skipped=otel_off
```
