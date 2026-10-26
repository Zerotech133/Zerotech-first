---
name: lookahead-guard
description: "Z5 防穿越断言（数据时点校验）"
license: internal
compatibility: Windows, Python 3.12, local-only, no-network-verify
metadata:
  owner: "ZHAOYUN"
  version: "1.0.0"
  risk: "read-only"
  legacy_capability: "lookahead-guard"
  verify: "scripts/verify.py"
allowed-tools: [read-file, run-python]
---

# lookahead-guard

## 是什么
Z5 防穿越断言（数据时点校验）

## 怎么跑
```
python agent_platform/engine/lookahead_guard.py --selftest
```

## 边界
规则精确优先+pass-through 反证

## 验证
```
python agent_platform/skills/lookahead-guard/scripts/verify.py
```

