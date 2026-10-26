---
name: task-exit-audit
description: "治理扫描主工具：46 任务退出码+窗口三分类"
license: internal
compatibility: Windows, Python 3.12, local-only, no-network-verify
metadata:
  owner: "ZHAOYUN"
  version: "1.0.0"
  risk: "read-only"
  legacy_capability: "task-exit-audit"
  verify: "scripts/verify.py"
allowed-tools: [read-file, run-python]
---

# task-exit-audit

## 是什么
治理扫描主工具：46 任务退出码+窗口三分类

## 怎么跑
```
python agent_platform/tools/task_exit_audit.py --scan --window 24
```

## 边界
只读；Disabled/267011 不计败

## 验证
```
python agent_platform/skills/task-exit-audit/scripts/verify.py
```

