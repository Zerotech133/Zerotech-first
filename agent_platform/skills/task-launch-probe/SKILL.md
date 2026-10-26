---
name: task-launch-probe
description: "S-0 能力正测试（一次性任务 rc 断言）"
license: internal
compatibility: Windows, Python 3.12, local-only, no-network-verify
metadata:
  owner: "ZHAOYUN"
  version: "1.0.0"
  risk: "read-only"
  legacy_capability: "task-launch-probe"
  verify: "scripts/verify.py"
allowed-tools: [read-file, run-python]
---

# task-launch-probe

## 是什么
S-0 能力正测试（一次性任务 rc 断言）

## 怎么跑
```
ProbeP1 模式：建临时任务→/run→断言 rc∈{0,3}→删
```

## 边界
探针用后即清

## 验证
```
python agent_platform/skills/task-launch-probe/scripts/verify.py
```

## 备注
探针本身有建删任务副作用，verify 用 schtasks 只读查询替代

