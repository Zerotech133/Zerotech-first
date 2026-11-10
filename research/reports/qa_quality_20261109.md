# 问答质量日报（2026-11-09）

> 真实 HTTP multi 抽检 2 条（cache=0，LLM 2 次）· rolling 7d · 阈值 可答率≥80% / guard≥80% / fallback≤20%。

## 当日结论

**⚠️ 漂移告警**：可答率 0%、guard 0%、fallback 0%、平均引用 0.0、可解析率 0%、推断 0.0、平均耗时 0.0s、完整度 0.0/5

### 告警明细

- [ANSWERABLE_LOW] 可答率 0% < 80% 阈值
- [GUARD_LOW] guard 通过率 0% < 80% 阈值
- [TREND_DOWN] 可答率较 rolling 7d 均值 93% 下滑 93%（>15%）

## rolling 7d 趋势

- 2026-11-03 ~ 2026-11-09（7 天）：日均可答率 79%、guard 79%、fallback 7%、平均引用 1.8571

## 告警文本（--send 时发送）

```
【问答质量告警 2026-11-09】发现 3 项漂移：
- [ANSWERABLE_LOW] 可答率 0% < 80% 阈值
- [GUARD_LOW] guard 通过率 0% < 80% 阈值
- [TREND_DOWN] 可答率较 rolling 7d 均值 93% 下滑 93%（>15%）
当日：可答率 0%、guard 0%、fallback 0%、样本 2 条
rolling 7d：日均可答率 79%、guard 79%、fallback 7%（7 天）
```

- 发送结果：sent=True error=None

## 抽检样本

| # | 问题 | 状态 | 引用 | 可解析 | 推断 | guard | 耗时 | 完整度 |
|---|---|---|---|---|---|---|---|---|
| 1 | 电气化率的关键参数或阈值有哪些？ | no_evidence | 0 | 0% | 0.0 | False | 4.78s | 1/5 |
| 2 | 计算机行业板块表现的验证方式与观测窗口是什么？ | no_evidence | 0 | 0% | 0.0 | False | 0.08s | 1/5 |

- 历史文件：`C:\项目\老田的AI助手\research\reports\qa_quality_history.json`（累计 8 天）

- 每日质量抽检仅供运维监控参考，不构成投资建议。
- 总耗时 6.51s
