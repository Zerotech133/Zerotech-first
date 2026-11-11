# 问答质量日报（2026-11-08）

> 真实 HTTP multi 抽检 2 条（cache=0，LLM 2 次）· rolling 7d · 阈值 可答率≥80% / guard≥80% / fallback≤20%。

## 当日结论

**⚠️ 漂移告警**：可答率 50%、guard 50%、fallback 50%、平均引用 1.0、可解析率 50%、推断 0.25、平均耗时 37.885s、完整度 3.0/5

### 告警明细

- [ANSWERABLE_LOW] 可答率 50% < 80% 阈值
- [GUARD_LOW] guard 通过率 50% < 80% 阈值
- [FALLBACK_HIGH] fallback 率 50% > 20% 阈值
- [TREND_DOWN] 可答率较 rolling 7d 均值 100% 下滑 50%（>15%）

## rolling 7d 趋势

- 2026-11-02 ~ 2026-11-08（7 天）：日均可答率 93%、guard 93%、fallback 7%、平均引用 2.2143

## 告警文本（--send 时发送）

```
【问答质量告警 2026-11-08】发现 4 项漂移：
- [ANSWERABLE_LOW] 可答率 50% < 80% 阈值
- [GUARD_LOW] guard 通过率 50% < 80% 阈值
- [FALLBACK_HIGH] fallback 率 50% > 20% 阈值
- [TREND_DOWN] 可答率较 rolling 7d 均值 100% 下滑 50%（>15%）
当日：可答率 50%、guard 50%、fallback 50%、样本 2 条
rolling 7d：日均可答率 93%、guard 93%、fallback 7%（7 天）
```

- 发送结果：sent=True error=None

## 抽检样本

| # | 问题 | 状态 | 引用 | 可解析 | 推断 | guard | 耗时 | 完整度 |
|---|---|---|---|---|---|---|---|---|
| 1 | 如何识别全球风电市场趋势相关的机会或信号？ | success | 2 | 100% | 0.5 | True | 23.8s | 5/5 |
| 2 | 区域收入分化的核心结论与主要依据是什么？ | fallback | 0 | 0% | 0.0 | False | 51.97s | 1/5 |

- 历史文件：`C:\项目\老田的AI助手\research\reports\qa_quality_history.json`（累计 7 天）

- 每日质量抽检仅供运维监控参考，不构成投资建议。
- 总耗时 81.42s
