---
name: governance-ai
description: "治理 AI：异常归纳/优先级建议/判例草稿（DeepSeek flash）"
license: internal
compatibility: Windows, Python 3.12, local-only, no-network-verify
metadata:
  owner: "ZHAOYUN"
  version: "1.0.0"
  risk: "read-only"
  legacy_capability: "governance-ai"
  verify: "scripts/verify.py"
allowed-tools: [read-file, run-python]
---

# governance-ai

## 是什么
治理 AI：异常归纳/优先级建议/判例草稿（DeepSeek flash）

## 怎么跑
```
python agent_platform/tools/gov_ai.py --action all --json（draft 落 data/governance_ai；健康卡 governance.ai_advice 判活）
```

## 边界
只产 draft，不写卡/经验库/工单；失败黄不阻塞治理链

## 验证
```
python agent_platform/skills/governance-ai/scripts/verify.py
```

