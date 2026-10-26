---
name: health-card
description: "健康卡运行与红黄绿解读"
license: internal
compatibility: Windows, Python 3.12, local-only, no-network-verify
metadata:
  owner: "ZHAOYUN"
  version: "1.0.0"
  risk: "read-only"
  legacy_capability: "health-card"
  verify: "scripts/verify.py"
allowed-tools: [read-file, run-python]
---

# health-card

## 是什么
健康卡运行与红黄绿解读

## 怎么跑
```
python tools/gen_health_card.py
```

## 边界
检查器先自证（判例）

## 验证
```
python agent_platform/skills/health-card/scripts/verify.py
```

