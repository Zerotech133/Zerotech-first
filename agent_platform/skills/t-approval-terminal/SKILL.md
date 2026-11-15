---
name: t-approval-terminal
description: "做T信号→iPhone 审批执行终端（赵云独有）：T-Engine 出审批卡片，iPhone 端（OpenClaw/Pinky）批准或拒绝，执行回执回流项目一入纸交易账；含 L1 零审批直入队、L2 超时默认执行、L3 超时默认取消、通道失败降级链"
license: internal
compatibility: Windows, Python 3.12, local-only, no-network-verify
metadata:
  owner: "ZHAOYUN"
  version: "1.0.0"
  risk: "read-only"
  legacy_capability: "t-approval-terminal"
  verify: "scripts/verify.py"
allowed-tools: [read-file, run-python]
---

# t-approval-terminal

## 是什么
做T信号→iPhone 审批执行终端（赵云独有）：T-Engine 出审批卡片，iPhone 端（OpenClaw/Pinky）批准或拒绝，执行回执回流项目一入纸交易账；含 L1 零审批直入队、L2 超时默认执行、L3 超时默认取消、通道失败降级链

## 怎么跑
```
python agent_platform/skills/t-approval-terminal/scripts/exclusive_check.py（只读核验独占性+焊点三件套）；真实系统在两个兄弟仓：terminal/gateway/app.py（Gateway：POST /api/v1/cards、/cards/{id}/decision、/cards/{id}/receipt、超时 worker、Bearer+HMAC-SHA256 签名）+ integration/test_seam.py（焊点回归）
```

## 边界
只做通道与审批，不做下单；真机 iPhone 端未接（现为 CLI 模拟器 gateway/simulate.py），无主动回调、降级链仅纯逻辑；赵云只核验契约与证据，不代签、不代替老田批准资金动作

## 验证
```
python agent_platform/skills/t-approval-terminal/scripts/verify.py
```

## 备注
赵云独有技能：做T 审批卡片→iPhone 端批准/拒绝→执行回执全链路；真实系统在 ../terminal（Gateway）+ ../integration（焊点测试），本仓只登记能力与边界

