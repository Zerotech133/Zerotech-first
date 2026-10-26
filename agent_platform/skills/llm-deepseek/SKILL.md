---
name: llm-deepseek
description: "DeepSeek 真模型推理（异常归纳/建议生成/判例抽取）"
license: internal
compatibility: Windows, Python 3.12, local-only, no-network-verify
metadata:
  owner: "ZHAOYUN"
  version: "1.0.0"
  risk: "read-only"
  legacy_capability: "llm-deepseek"
  verify: "scripts/verify.py"
allowed-tools: [read-file, run-python]
---

# llm-deepseek

## 是什么
DeepSeek 真模型推理（异常归纳/建议生成/判例抽取）

## 怎么跑
```
python agent_platform/engine/deepseek.py --selftest（默认 deepseek-flash；key 读 _vault/secrets/deepseek_api_key.txt 或 DEEPSEEK_API_KEY）
```

## 边界
key 私有区不落仓；异族互镜的一极

## 验证
```
python agent_platform/skills/llm-deepseek/scripts/verify.py
```

