# 席位评测（Promptfoo）

> 2026-10-27 起：席位 prompt / SKILL.md 的能力回归与红队。
> 工具：[Promptfoo](https://www.promptfoo.dev/docs/intro/)（开源、本地跑、可断言、可对比模型）。

## 用法

```powershell
npm install -g promptfoo
$env:DEEPSEEK_API_KEY = (Get-Content C:\项目\_vault\secrets\deepseek_api_key.txt -Raw).Trim()
cd C:\项目\老田的AI助手
promptfoo eval -c agent_platform/evals/promptfooconfig.yaml
```

## 评测分层

| 层 | 断言类型 | 目的 |
|---|---|---|
| 契约层 | `contains` / `not-contains` / `regex` | 输出契约（回执结构/L2 待批/回滚命令）不许退化 |
| 安全层 | `not-contains`（凭据形态）、`llm-rubric`（越权） | 不泄密、不越权 |
| 质量层 | `llm-rubric` / 人工抽检 | 审查结论是否有证据、误报率 |

## 纪律

- 改 prompt / SKILL.md 前先跑基线，改完对比；**分数下降不得合并**；
- 新增用例优先覆盖"今天踩过的坑"（如 test_gate 的 rc 未校验 → 用"异常退出必须判失败"用例锁住）；
- 评测只读、不写生产；模型 key 走环境变量，禁止入仓。
