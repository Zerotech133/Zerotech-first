---
name: harness-runner
description: "DeepSeek Harness 执行框架（cron/重试/隔离）"
license: internal
compatibility: Windows, Python 3.12, local-only, no-network-verify
metadata:
  owner: "ZHAOYUN"
  version: "1.0.0"
  risk: "read-only"
  legacy_capability: "harness-runner"
  verify: "scripts/verify.py"
allowed-tools: [read-file, run-python]
---

# harness-runner

## 是什么
DeepSeek Harness 执行框架（cron/重试/隔离）

## 怎么跑
```
执行体=DSH 会话（DeepSeek Harness 桌面版）；定时/重试由 governance_clock 承担；Harness² 本体不在位，按需再立项
```

## 边界
执行体=DSH 会话；不依赖 Harness² 本体

## 验证
```
python agent_platform/skills/harness-runner/scripts/verify.py
```

## 备注
执行体=DSH 会话；verify 检查调度器与命令台存在

