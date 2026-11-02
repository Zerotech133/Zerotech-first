# data-warehouse · Q2 证据包

- owner：DATA1｜自动状态：**AUTO-EVIDENCE-COLLECTED（待 owner 验收/签署）**
- 生成：2026-11-03T05:50:52+0800｜只读自动证据，**不替代验收**

## 自动探针

| 端口 | 路径 | 期望 | 实际 | ms | 结果 |
|---:|---|---:|---:|---:|---|
| 8091 | /api/dw/stats | [200] | 200 | 12.8 | PASS |
| 8734 | /dw | [200, 302, 401] | 302 | 19.9 | PASS |
| 8734 | /dataops | [200, 302, 401] | 302 | 4.7 | PASS |

## 测试文件

- `tools/datawarehouse/tests/`：存在
- `portal/test_warehouse_dashboard.py`：存在

## 人工门（未完成即不得 Q2 PASS）

- 登录后页面验收 + SLO + 回滚

## 缺口 / 结论

- 自动证据：齐；Q2 结论仍须 owner 验收签字。
- 回滚预案/SLO 人工项：本证据包不代填。

## 签署（待 owner）

- owner：____｜日期：____｜结论：____
