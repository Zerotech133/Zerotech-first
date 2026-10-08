# K-STOCK 模拟仓（paper trading）v0.1

AI 决策与人为测试共用的纸面交易账本。不碰真钱；SQLite 全留痕；`source=ai|manual` 区分订单来源。

## 文件

- `engine.py` 核心引擎（纯标准库）：账本四表（account/orders/positions/ledger）、费率、风控、T+1、涨跌停、撮合
- `market.py` 行情接口：`ParquetMarket`（真实特征湖 `quant_backtest/data/ashare/*.parquet`，需系统 Python311+pandas）/ `FakeMarket`（测试注入）
- `cli.py` 命令行入口（人为测试与 automation 共用）
- 账本：`k_stock/data/paper.db`（`--db` 可换）

## 口径（v0.1 定稿）

| 项 | 口径 |
|---|---|
| AI 信号单 | `exec` 盘前落 pending → `settle` 按**决策当日开盘价**撮合（防未来函数；湖盘后更新，账面次日晨确认） |
| 手动单 | 按**湖内最近收盘价**（或 `--price` 指定价，须在该 bar [low,high] 内）即时成交 |
| 整手 | 100 股整数手 |
| T+1 | 湖内最后交易日买入的部分当日不可卖（湖更新后自动解锁） |
| 涨跌停 | 撮合日开盘触及涨停拒买 / 跌停拒卖（主板 ±10%，创业板/科创 ±20%，北交所 ±30%；ST ±5% 无法从代码识别，挂账） |
| 费率 | 佣金 max(5, 0.025%) 双向 + 卖出印花税 0.05%；零滑点（挂账 v0.2） |
| 风控 | 单票 ≤20% 总资产；总仓位 ≤95%（`--force` 跳过，人为测试用）。v0.1 无独立单笔限（与一手最低额自相矛盾，已论证删除） |
| AI 仓位公式 | 目标仓位 = 20% × min(1, conf/0.75)；差额不足一手则放弃并留痕 cancelled |
| 湖滞后 | 决策日超前湖内时 plan_date 按工作日顺延预测，settle 取 ≥plan_date 第一个真实交易日（节假日自动纠偏） |

## 命令

```bash
P=C:/Program Files/Python311/python.exe   # 真实行情须 py311（pandas）
python k_stock/paper/cli.py status                 # 账户/持仓/盈亏/对账
python k_stock/paper/cli.py buy  --stock 600519.SH --qty 100 [--price x] [--force]
python k_stock/paper/cli.py sell --stock 600519.SH [--all | --qty 100] [--price x] [--force]
python k_stock/paper/cli.py exec --newest [--dry-run]   # AI 决策→pending 单（默认最新 poc_*.json）
python k_stock/paper/cli.py settle [--dry-run]          # 撮合 pending
python k_stock/paper/cli.py mark                        # 当日权益快照（同日覆盖）
python k_stock/paper/cli.py curve                       # 权益曲线（历史快照）
python k_stock/paper/cli.py pending | history | verify
python k_stock/paper/cli.py reset --cash 1000000 --yes  # 清账重开（危险，须 --yes）
```

## automation 每日闭环（07:05）

```
settle        # 撮昨日 pending（按当日开盘价，湖已更新）
exec --newest # 落今日新决策 pending（按决策当日开盘，湖明日才有该 bar）
mark          # 记当日权益快照（S1 判据链证据，curve 可看）
```

幂等：AI 单 ref=`{run文件名}#{股票}` 唯一索引，同 run 重跑零重复。
注意：cancelled（不足一手/无可卖）不复活——次日产新 run 文件自然重判。

## 挂账（v0.2+）

- 滑点模型（开盘价 VWAP 近似）
- ST ±5% 识别（需股票名称/风险警示数据）
- 过户费（沪市 0.001%）
- Hermes/CowAgent 会话交易工具（AI 对话下单）
- 门户可视化页（8080）

（equity 日快照已落地 v0.1.1：`mark`/`curve`，存 paper.db equity_marks 表）
