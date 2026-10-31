# 回滚点：OTel 开关回滚
> STATUS: frozen（黄忠验收夹具/样例文件，非计划；2026-10-31 W2 收口）



## 回滚步骤

```powershell
# 关闭上报（用户级 env）：
[Environment]::SetEnvironmentVariable("ZHAOYUN_OTEL", "0", "User")
# 重启：
New-Item -ItemType File -Path C:\Temp\LEGION_8796_RESTART.flag -Force
Start-ScheduledTask -TaskName LegionOps
# 验证：
# record() → otel.skipped=otel_off；Phoenix 不再收到新 span
# health 检查 http://127.0.0.1:6006/health 仍 200（UI 不受影响）
```
