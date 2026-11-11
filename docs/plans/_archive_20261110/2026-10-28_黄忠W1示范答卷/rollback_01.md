# 回滚点：L2 模型变更（ZHAOYUN flash→v4-pro）
> STATUS: frozen（黄忠验收夹具/样例文件，非计划；2026-10-31 W2 收口）



## 回滚步骤

```powershell
# 备份/恢复或 revert：
Copy-Item C:\项目\_vault\backups\agent_ide_bundle.before-zy-model.json portal\agent_ide_bundle.json -Force
# 或：git revert 813a120a
# 重启：
New-Item -ItemType File -Path C:\Temp\LEGION_8796_RESTART.flag -Force
Start-ScheduledTask -TaskName LegionOps
# 验证：
# GET /api/zhaoyun/model → 恢复 deepseek/deepseek-flash
```
