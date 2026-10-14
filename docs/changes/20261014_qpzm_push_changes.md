# 20261014 qpzm 推送卡片加"分位数"口径（W3-4 dashboard 泳道）

执行：qpzm-push-dev · 日期：2026-10-14 · 解释器：`"C:/Program Files/Python311/python.exe"`

## 背景

qpzm 前研究（W3-4 前研究）结论：共振打分为强右偏分布（当前真实归档实证：
190 个 live 历史分，中位 75.5、min 26、max 87，分数堆在高段），**原始分高低
直接看会误导**（分高≠能买，要看分位）。本泳道把分位数送进推送卡片。

## 注入点清单

| # | 位置 | 改动 |
|---|------|------|
| 1 | `rps_resonance.py` 新增 `load_hist_scores()` | 读日批归档 `reports/rps_daily_batches.jsonl`，只取 `source=live` 行，按日期升序取**滚动 60 交易日**（不足全部可得）的候选分集合作为历史分布基准；`exclude_date` 双保险排除当日 |
| 2 | `rps_resonance.py` 新增 `score_percentile()` | 单票分 → P0~P100 一位小数；定义 = 严格低于该分的历史占比×100（并列计高位，同分不抬轿）；空基准返回 None |
| 3 | `rps_resonance.py` 新增 `annotate_percentile()` | 开关 `config.json qpzm.score_percentile`（缺省 true）；无历史 fail-open 跳过，不炸主链 |
| 4 | `run_daily()` 内 `resonance_watchlist(...)` 之后、`write_pages(...)` 之前 | 逐候选注入 `percentile` 字段 → 随 res 落 `rps_latest.json`，推卡/日批归档读同一份快照（口径单一来源），异常 try/except 隔离 |
| 5 | `push_daily_card()` 共振块 | 每行"分"旁挂 `(Pxx.x)`（无字段不挂） |
| 6 | `push_daily_card()` 卡片头部 | 新增首个 notation 灰字说明行（仅当本次带分位时出现） |
| 7 | `config.json` 新增 `qpzm` 节 | `{"score_percentile": true}` 最小增量 |

## 盘前作战卡（runner.py morning_push）核查结论

通读 `runner.py morning_push()`（runner.py:474）：卡片数据流 = 纸面台账
ledger（pending/open 持仓）+ 九维警报池（nine_dim_alert_check），**无任何
qpzm 共振分引用**。按任务约定：只改 qpzm 自身卡片，runner.py 未改动。

## 字段格式示例

打分产物（`rps_latest.json` res 行 / 归档 picks 同源）新增字段：

```json
{"name": "Palantir", "score": 86, "percentile": 95.8, "action": "...", ...}
```

- `percentile`：P0~P100，一位小数；口径=「当日全市场分位」，分布基准为
  滚动 60 交易日 live 归档分（不足 60 日全部可得，零历史不加字段）。

## 卡片效果示例（文本模拟，20261013 真实快照）

> 📊 RPS 强度榜 × 共振（四市场）｜环境闸门 0/4 开启 · 20261013
> *<font color='grey'>分数为原始共振分（右偏分布），括号内为当日全市场分位</font>*
> **自选池共振 ≥50**
> · Palantir 86分(P95.8) 回避（环境闸门）［tech+jev+rps+env］
> · 先导基电 85分(P89.5) 回避（环境闸门）［tech+rps+env］
> · 闽东电力 85分(P89.5) 回避（环境闸门）［tech+rps+env］
> · 台积电 80分(P72.6) 回避（环境闸门）［tech+jev+rps+env］

## 口径说明文案（卡片头部原句）

> 分数为原始共振分（右偏分布），括号内为当日全市场分位

## 单测

`test_qpzm_percentile.py`（合成数据，不触网不推卡），四条路径全过：

1. 分位计算正确性：边界 P0/P100、并列同分计高位、空基准 None
2. 60 日滚动窗口：>60 日裁早窗、replay 行不入基准、exclude_date 排当日
3. 开关关闭：不加字段、卡片回退旧版（无口径行/无 P 后缀）；缺省 true
4. 历史不足回退：<60 日全部可得；零历史 fail-open 不加字段

运行：`"C:/Program Files/Python311/python.exe" test_qpzm_percentile.py`

## 回退方法

- 快回退（不回滚代码）：`config.json` 置 `"qpzm": {"score_percentile": false}`
  → 不算分位、卡片完全回退旧版（口径提示行同步消失）
- 全量回退：
  `cp rps_resonance.py.bak_20261014 rps_resonance.py`
  `cp config.json.bak_20261014_qpzm config.json`
- 注：`percentile` 为纯增量字段，历史归档/页面不依赖它，回退无副作用。

## 备份与纪律留痕

- 备份：`rps_resonance.py.bak_20261014`、`config.json.bak_20261014_qpzm`
- 未推真飞书（卡片验证全部 monkeypatch 截获）；未注册计划任务；未动生产数据
  （仅读归档/快照，未写 reports 下任何文件）

## 遗留风险

1. **历史基准薄**：当前 live 归档仅 5 个交易日（190 分），分位粒度粗；
   随日批自然累积，约 60 个交易日后基准才满窗。期间分位偏"小样本经验分位"，
   交付语义不受影响（字段口径不变）。
2. **基准口径=自选池候选分**（含买入/观察/回避全档），非 A 股全市场逐票分
   ——共振分只对池内计算，历史无全市场分可归档；"全市场"指候选含四市场。
   若未来要全市场口径，需在日批新增全池打分归档（另行立项）。
3. **回看页面无分位**：历史 HTML 页面（write_pages 渲染）未加 P 后缀，
   仅推送卡片与 `rps_latest.json` 带分位（最小改动边界）。
4. replay 归档行 score 恒 None，永远不入基准（设计如此，避免三维复算口径
   混入四维 live 分布）。
