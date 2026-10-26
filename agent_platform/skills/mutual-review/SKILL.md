---
name: mutual-review
description: "互审单：评审对家产出（MUTUAL-REVIEW 工单）"
license: internal
compatibility: Windows, Python 3.12, local-only, no-network-verify
metadata:
  owner: "ZHAOYUN"
  version: "1.0.0"
  risk: "read-only"
  legacy_capability: "mutual-review"
  verify: "scripts/verify.py"
allowed-tools: [read-file, run-python]
---

# mutual-review

## 是什么
互审单：评审对家产出（MUTUAL-REVIEW 工单）

## 怎么跑
```
读对家 deliverables/worktree diff → 评审批文书
```

## 边界
执行者不结案；分歧上议会厅

## 验证
```
python agent_platform/skills/mutual-review/scripts/verify.py
```

