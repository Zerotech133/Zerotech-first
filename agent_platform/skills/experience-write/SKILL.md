---
name: experience-write
description: "Z4 supervision 域经验写入（赵云写田丰读）"
license: internal
compatibility: Windows, Python 3.12, local-only, no-network-verify
metadata:
  owner: "ZHAOYUN"
  version: "1.0.0"
  risk: "read-only"
  legacy_capability: "experience-write"
  verify: "scripts/verify.py"
allowed-tools: [read-file, run-python]
---

# experience-write

## 是什么
Z4 supervision 域经验写入（赵云写田丰读）

## 怎么跑
```
~/.hermes/profiles/zhaoyun/experience/supervision/ 四字段（cue/lesson/evidence/confidence）
```

## 边界
登记即去重键；采纳需老田签

## 验证
```
python agent_platform/skills/experience-write/scripts/verify.py
```

