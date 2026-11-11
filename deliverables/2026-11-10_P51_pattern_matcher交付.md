# P5.1 pattern matcher 交付（2026-11-10）

> **定位说明（2026-11-10 12:01 老田澄清）**：本系统（凌霄渡·量化策略工程师）是**独立系统**，与并行会话**不存在装车**——`portal/jev_pattern.py`（并行会话，8-pattern）与本件（10-pattern）是两套独立系统的平行实现，各自按各自的 P4 文档交付，互不覆盖、互不阻塞。本实现直读 moneyflow parquet（不依赖 _jev_snap 快照），可作为独立口径的对照实现。
>
> 依据：`docs/jev_patterns_v1.md`（P4 v1，10 候选）+ `docs/jev_deltamin_spec.md`（P1.4 数据契约）。
> 实现：`quant_backtest/_qpzm_rd_20261106/maintenance/_p51_matcher.py`（参数化阈值，v2 定版后只改 P 表）。
> 首跑：2026-11-09（`_p51_matcher_20261109.json`，候选 1803）。

## 一、实现要点

- **三维输入全部走 spec 契约**：moneyflow `d_{day}.parquet`（main_net=大单+特大买−卖，与 `portal/jev_flow.py` 权威口径一致）、daily（pre_close）、stk_mins 活源 + `portal/jev_graph.py::limit_state`、news.db + `parse_news_time` + `tools/jev_signal.py::event_type_tier`、`portal/social_{day}.json`。
- **判例 #25③ 纪律**：Δmin 双锚点缺任一即 unpriced，绝不硬算；unpriced 样本退出分钟 Δmin 类 pattern（P-01/02/05/06/09），保留在 P-04/07/10 候选池。
- **快过滤**：日高没摸到涨停价 → 跳过 stk_mins 读取（当日不可能有封板分钟）。
- **社交缺失 ≠ 双榜皆冷**：`social_missing=True` 的票不参与负向社交条件（P-01 的 xq==0、P-10 的双冷），防止把「没数据」当「机构无视」。
- **互斥组冲突只标记不丢弃**：P5.2 案例库需要重叠统计。
- **分钟数据补拉**：stk_mins 202611 目录只有 1029 票（tushare_sync 只覆盖中证800），首跑 seal 全空 → 用 `tools/jev_mins_fetch.py --day 20261109 --source tushare` 补齐 91 只「日高摸板」候选（87 merged，26s）。注意 xtcloud 源当日全超时（每票 12s×2 表），`--source tushare` 直连是正确路径。

## 二、11-09 首跑结果

| pattern | 命中 | 备注 |
|---|---|---|
| P-01 散户接盘 | **5** | 盘龙药业(Δmin+52)、祥明智能(+20)、龙建股份(+340)、日盈电子(+126)、世运电路(+195)；全部 div+股吧热+雪球零覆盖+价格先行，画像与 11-06 判例原型一致 |
| P-03 机构先手 | 53 | 平安/美的/潍柴/藏格/云南白药等白马：xq 榜覆盖+主力进+散户未醒 |
| P-04 价格先行无消息 | 49 | 涨停+主力进+强类型消息真空（n_minute 0~1） |
| P-08 弱事件涨停 | 11 | P-02 的对照面 |
| P-02/P-05/P-06/P-09 | 0 | 见下方数据缺口 |
| P-07 | 0 | announcement stocks 覆盖仅 1.9%（614 条中 22 条），无法映射个股 |
| P-10 | 0 | 社交快照只覆盖 60 票，「双榜皆冷」样本池太小 |

候选 1803（涨停 57 + 资金背离 1736 + mf_in_q80 有社交 133，交集去重），unpriced 1785。

## 三、数据缺口（P5.2 前要补的）

1. **announcement/report/survey 的 stocks 字段近乎为空**（1.9%/23%/2%）→ P-07 个股级判定被卡。出路：走 ts_anns_d 或 stock_notice 的票映射，或降级为板块级信号。
2. **社交快照只采焦点 60 票**（jev_social_fetch 默认选票逻辑）→ P-01/P-10 的负向条件与 P-10 全体受限。出路：扩票池（涨停+div 票全采）或接受「社交条件只在焦点票内判定」的口径。
3. **xd_notice 语义风险**：270 条 100% 带 stocks 且全部落在涨停票上（异动推送性质）→ 把它当「日级追认」存在同义反复（涨停票必然被它追认）。P5 校准时 xd_notice 的追认权重应单独评估（建议降权或排除 confirm 分支）。
4. **P-06 需要 T+1 moneyflow**：11-10 收盘后自动具备（automation 已在跑）。跑 P-06 需用 T-1 日作信号日。
5. **div 全市场发生率 31%（1736/5579）**：P4 文档预期「数十只」与实测差一个量级——P4 的样本预期是假设，实测以 P5.2 案例库为准；P-01 实际瓶颈是社交条件（5 只）而非 div 条件。

## 四、与 P4 评审的关系

matcher 按 **v1 条件实现，全部阈值参数化**（P 表：TH_LEAD/TH_DRIVE=5、MF_IN_Q=0.80、GUBA_TOP=100、GUBA_RISE=0.30、DLEAD_MIN=30、DFAST_MAX=60、PIPO_DAYS=3）。老田评审 patterns v1 → v2 定版后，改条件即改 `match()` 里对应分支 + P 表，无需动数据层。

## 五、复跑方式

```
cd quant_backtest/_qpzm_rd_20261106/maintenance
/c/Program\ Files/Python311/python.exe _p51_matcher.py --day YYYYMMDD
# 分钟缺口先补：python tools/jev_mins_fetch.py --day YYYYMMDD --source tushare --codes <清单>
```
产物：`_p51_matcher_{day}.json`（每票：patterns 命中 + 全维度原始值留痕）+ 运行日志 `_p51_run_{day}.log`。
