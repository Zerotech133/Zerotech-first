# S4 影子评估报告 · {STRATEGY_ID}（{REPORT_DATE}）

> 模板工位：pipeline-fw（RC-1 四件套 · S4 部分）。
> 统计数字由 `python -m strategy_layer.onboarding.shadow_collect --strategy {STRATEGY_ID} --report`
> 产出（口径对齐 `council_shadow.py`：r5/r20 前向收益、四分桶、Pearson r+t、
> N_MIN=30），按节填入本模板。报告落 `reports/onboarding_shadow/{STRATEGY_ID}_shadow_report_{N}.md`。

## 1. 策略与影子期概要

| 项 | 值 |
|---|---|
| strategy_id / version | {STRATEGY_ID} / {VERSION} |
| 收编批次 / S3 对账结论 | 第一批 / 一致率 {AGREE_RATE}%（阈值 99%，{PASS_FAIL}） |
| 影子期起止 | {SHADOW_START} ~ {SHADOW_END}（{TRADING_DAYS} 个交易日） |
| 运行模式 | SHADOW（manifest allowed_modes 内，无执行权） |
| 标的池 / 参数 | {SYMBOLS} / {PARAMS} |
| 影子档 | data/shadow_onboarding/{STRATEGY_ID}_shadow.jsonl（{N_RECORDS} 条） |

## 2. 分位表现（score 四分桶 × 前向收益）

| 桶 | n | r5 均值 | r5 胜率 | r20 均值 |
|---|---|---|---|---|
| 0-25 | {N} | {R5} | {WIN5} | {R20} |
| 25-50 | {N} | {R5} | {WIN5} | {R20} |
| 50-75 | {N} | {R5} | {WIN5} | {R20} |
| 75-100 | {N} | {R5} | {WIN5} | {R20} |

判读要点：高桶（75-100）r20 均值应系统性高于低桶——分位无单调性 = score
映射失效信号，即使相关性达标也要在评审会上说明。

## 3. 置信度与相关性

| 项 | 值 |
|---|---|
| confidence 取值 / 分布 | {CONF_DESC} |
| (score, r20) 样本对 n | {PAIRS_N}（门槛 30） |
| Pearson r / t | {R} / {T}（\|t\|≥2 近似 5% 显著） |
| gate 判定（脚本输出） | {GATE_STATUS} |

## 4. 三态判据（转正 / 延期 / 淘汰）

| 状态 | 判据（全部满足） | 后续动作 |
|---|---|---|
| **转正** | 影子 ≥10 个交易日 且 (score,r20) n≥30 且 t≥+2（显著正向） 且 分位桶单调 且 S3 一致率持续 ≥99% | 人工拍板评审 → manifest lifecycle_state: experimental→active，允许进聚合（weighted） |
| **延期** | n<30 或 \|t\|<2（不显著） 且 无反向证据 且 无降级异常 | 维持影子一个观察窗（≥10 交易日），补样本后重评；连续 2 次延期转淘汰评审 |
| **淘汰** | t≤-2（显著反向） 或 S3 一致率跌破 99% 且复跑不收敛 或 连续 5 个交易日 adapter 全量降级空信号 且 复跑正常 | 人工拍板评审 → manifest lifecycle_state: →deprecated，移出 allowed_modes 的 SHADOW |

红线（任何状态不变）：策略只产建议（Signal/Verdict），`permissions` 恒为
propose+read_context，execution_allowed 恒 False；转正≠拿到下单权。

## 5. 本期异常与降级记录

| 日期 | 事件 | 处置 |
|---|---|---|
| {DATE} | {EVENT，如 adapter 降级空信号 / 数据缺失} | {ACTION} |

（无则填「本期无降级事件」。）

## 6. 结论与签署

- 三态结论：{PROMOTE / EXTEND / RETIRE}
- 依据摘要：{一句话，引用 §3 gate 判定与 §4 判据}
- 人工拍板：{审批人 / 日期}（影子转正永远人工拍板，脚本只提供判据）
