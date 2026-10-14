# 2026-10-14 akshare 涨停/情绪接口探测（factor-probe）

## 变更范围
仅新增探测文件，未改动任何现有文件、未接产线。

## 新增文件
1. `quant_backtest/tools/probe_akshare_sentiment.py` — 可重复跑的 akshare 涨停/情绪接口探测脚本（内置 NO_PROXY 直连防护，结果落盘 `reports/akshare_sentiment_probe.json`）。
2. `quant_backtest/reports/akshare_sentiment_probe.md` — 探测报告（接口可用性表、字段清单、weak/strong/linkage 接入建议）。
3. `quant_backtest/reports/akshare_sentiment_probe.json` — 脚本运行的原始探测结果。

## 环境
- 在受管 venv `C:/Users/Administrator/.workbuddy/binaries/python/envs/default` 安装 akshare 1.19.1（pandas 3.0.6），未动系统 Python。

## 探测结论
- `stock_zt_pool_em`（涨停股池）：可用。20261014 当日 36 行 / 20261013 历史 67 行，~0.25s。
- `stock_zt_pool_zbgc_em`（炸板股池）：可用。当日 24 行 / 20261013 19 行；炸板率 20261013 = 22.09%。
- 连板高度：可由涨停池 `连板数` 列 max 推导（20261013 max=3，当日 max=4）；`stock_zt_pool_strong_em` 无连板数字段，不能替代。
- 附加可用：`stock_zt_pool_strong_em`（196 行）、`stock_zt_pool_previous_em`（69 行，含"昨日连板数"）。
- 无网络/代理故障，无需替代源。

## 建议（详见报告）
regime 第二输入首选 `max(连板数)`，辅以炸板率；更新节奏收盘后一次，盘中数据仅做快照信号不落历史。
