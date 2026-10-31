# 回滚点：L2 模型变更
> STATUS: unspecified（owner: TBD；2026-11-07 前补状态或标 superseded）



## 回滚步骤

```powershell
# 备份/恢复或 revert：
Copy-Item 备份 → portal/agent_ide_bundle.json；或 git revert <sha>
# 重启：
New-Item -ItemType File -Path C:\Temp\LEGION_8796_RESTART.flag -Force
Start-ScheduledTask -TaskName LegionOps
# 验证：
GET /api/zhaoyun/model 恢复 flash
```
