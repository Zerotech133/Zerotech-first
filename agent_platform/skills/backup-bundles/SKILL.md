---
name: backup-bundles
description: "备份链：bundle+manifest+校验+轮转"
license: internal
compatibility: Windows, Python 3.12, local-only, no-network-verify
metadata:
  owner: "ZHAOYUN"
  version: "1.0.0"
  risk: "read-only"
  legacy_capability: "backup-bundles"
  verify: "scripts/verify.py"
allowed-tools: [read-file, run-python]
---

# backup-bundles

## 是什么
备份链：bundle+manifest+校验+轮转

## 怎么跑
```
powershell tools/backup_bundles.ps1 -DryRun|执行
```

## 边界
异地已落地（2026-10-26 回执 7/7 sha256）

## 验证
```
python agent_platform/skills/backup-bundles/scripts/verify.py
```

