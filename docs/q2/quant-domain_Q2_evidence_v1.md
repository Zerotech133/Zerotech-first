# quant-domain · Q2 证据包

- owner：A0｜自动状态：**AUTO-EVIDENCE-COLLECTED（待 owner 验收/签署）**
- 生成：2026-11-03T05:50:52+0800｜只读自动证据，**不替代验收**

## 自动探针

| 端口 | 路径 | 期望 | 实际 | ms | 结果 |
|---:|---|---:|---:|---:|---|
| 8105 | /api/v1/health | [200] | 200 | 5.6 | PASS |
| 8734 | /quant-drill | [200, 302, 401] | 200 | 26.4 | PASS |

## 测试文件

- `agent_platform/tests/test_strategy_config.py`：存在
- `agent_platform/tests/test_quant_double_knife.py`：存在

## 人工门（未完成即不得 Q2 PASS）

- Q2 评审 + SLO/回滚证据

## 缺口 / 结论

- 自动证据：齐；Q2 结论仍须 owner 验收签字。
- 回滚预案/SLO 人工项：本证据包不代填。

## 签署（待 owner）

- owner：____｜日期：____｜结论：____
