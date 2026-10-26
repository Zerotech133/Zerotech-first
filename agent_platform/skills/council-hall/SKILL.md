---
name: council-hall
description: "议会厅治理面发言"
license: internal
compatibility: Windows, Python 3.12, local-only, no-network-verify
metadata:
  owner: "ZHAOYUN"
  version: "1.0.0"
  risk: "read-only"
  legacy_capability: "council-hall"
  verify: "scripts/verify.py"
allowed-tools: [read-file, run-python]
---

# council-hall

## 是什么
议会厅治理面发言

## 怎么跑
```
落盘评审/回执文书
```

## 边界
只产建议与证据（V3.1）

## 验证
```
python agent_platform/skills/council-hall/scripts/verify.py
```

## 备注
只产建议/证据，verify 检查议会材料目录存在

