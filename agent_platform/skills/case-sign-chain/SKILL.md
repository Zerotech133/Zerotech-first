---
name: case-sign-chain
description: "判例草稿签字链：AI draft→待签 payload→本人签→公钥验签→入 Z4"
license: internal
compatibility: Windows, Python 3.12, local-only, no-network-verify
metadata:
  owner: "ZHAOYUN"
  version: "1.0.0"
  risk: "read-only"
  legacy_capability: "case-sign-chain"
  verify: "scripts/verify.py"
allowed-tools: [read-file, run-python]
---

# case-sign-chain

## 是什么
判例草稿签字链：AI draft→待签 payload→本人签→公钥验签→入 Z4

## 怎么跑
```
python tools/gov_ai_case_bridge.py --emit / --apply（私钥由老田持有，Agent 只验签）
```

## 边界
未签/篡改/未配公钥一律拒；strict 模式 verified=True 才入库

## 验证
```
python agent_platform/skills/case-sign-chain/scripts/verify.py
```

