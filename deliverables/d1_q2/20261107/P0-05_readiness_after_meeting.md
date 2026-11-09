# D1 P0-05 运行时账本 readiness

- 检查时间：2026-11-08T21:14:22+08:00
- 结构：PASS；三类账齐备：NO
- 未齐备：training

## 账本

| 账本 | 行数 | 状态 |
| --- | --- | --- |
| d1_decisions | 4 | clean |
| meetings | 1 | clean |
| training | 0 | data_unavailable |

## 四 ID 链（现有数据）

- decision → handoff：4/4
- handoff → watch plan：4/4
- plan → trigger：4/4
- trigger → confirmation：4/4
- paper trade ids：0；positions=0

## 缺口

- 未齐备账本：training；P0-05 需真实 D1 会议与 episode，禁止造数
- trigger 已能追到 confirmation，但 paper 当前空仓、无 trade_id；decision→handoff→watch plan→trigger 已通，trade 段未有真实成交可追

## 下一步

1. 由真实 paper 成交/outcome 链生成 training_episode → training/episodes.jsonl
2. P0-05 验收前重跑本检查；不把空态或样例当真实事件

> 本工具只读，不创建会议/episode，不造数。
