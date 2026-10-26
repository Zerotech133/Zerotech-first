---
name: docs-archive
description: "一话题一归档（S4）：根目录 md >24h 迁移"
license: internal
compatibility: Windows, Python 3.12, local-only, no-network-verify
metadata:
  owner: "ZHAOYUN"
  version: "1.0.0"
  risk: "read-only"
  legacy_capability: "docs-archive"
  verify: "scripts/verify.py"
allowed-tools: [read-file, run-python]
---

# docs-archive

## 是什么
一话题一归档（S4）：根目录 md >24h 迁移

## 怎么跑
```
移 docs/audit/ 或 _archive/
```

## 边界
回收站纪律

## 验证
```
python agent_platform/skills/docs-archive/scripts/verify.py
```

