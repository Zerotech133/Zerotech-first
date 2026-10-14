# 20261014 linkage-dev 变更：三线联动影子样本收割落地 + 历史命中率回测

执行人：linkage-dev（滚动泳道）｜日期：2026-10-14｜产线：linkage.py / linkage_signals.py / linkage_plot.py

## 一、任务背景与目标

linkage 产线现状：个股×板块×大盘归一化对比，五信号自动标注（①抗跌 ②滞涨 ③共振底 ④孤军深入 ⑤三共振），但个股级产物是"未挂账组合"——信号只展示、从不验证。实施计划判据：**影子样本命中率 ≥55% 再议挂账组合**。

本次交付：
1. 影子 collector（每日五信号命中落 jsonl 档案，只记不推）
2. 历史命中率回放（五信号 × T+5/10/20 × 双口径），给出挂账与否的数字依据

## 二、产线现状盘点（任务1 结论）

### 五信号精确判定规则（linkage_signals.judge_series，v1 原样）

| 信号 | 名称 | 判定条件（1d/30m/5m 各周期独立判定） |
|---|---|---|
| ① | 抗跌 | 窗口收益（大盘<0 或 板块<0）且 超额(个股−板块) ≥ +5pp |
| ② | 滞涨 | 板块窗口收益>0 且 超额 ≤ −5pp |
| ③ | 共振底 | 三线收盘同破各自近 60 根前低（不含最新一根） |
| ④ | 孤军深入 | 个股收盘破近 120 根前高而板块/大盘未全破（与⑤互斥 elif） |
| ⑤ | 三共振 | 三线收盘同破各自近 120 根前高 |

阈值：`GAP=5.0pp`、`LO_WIN=60`、`HI_WIN=120`；窗口收益=归一化序列（公共首根=0%，1d 默认 250 根）末值；关键位 close 口径。

### 数据入口（linkage.py）

个股 `chanlun_data.load_kline`（CN 走 zhice-db v2_bar_daily，TDX ts_daily 兜底）；大盘 `load_kline` 指数（默认 000001.SH）；板块 TDX 880xxx（tdx_sector_daily / 分钟尾巴仓）；个股→板块走 `ts_stock_basic.industry` 匹配；三线按个股时间轴 ffill 对齐。

### 历史留痕排查

- `reports/linkage_png/*.json` 侧车仅 **21 份**（7 股×3 周期），且为覆盖式快照（每次生成覆写），**无逐日留痕**
- 无任何计划任务/每日批次调用 linkage_*（task_*.xml、*.ps1、runner.py 均无引用）
- **结论：可捞历史样本≈0，历史命中率只能靠只读回放补**

## 三、影子 collector（任务2 交付）

### 新文件：`quant_backtest/linkage_collector.py`

落档文件：`quant_backtest/data/linkage_shadow.jsonl`，每行一条：

```json
{"ts": "采集时刻", "code": "301216.SZ", "name": null, "period": "1d",
 "signal": "2", "signal_name": "滞涨(弱于板块)", "note": "触发说明原文",
 "block": {"code","name","seg","how"},
 "snapshot": {"last_pct": {...}, "excess_vs_block": -17.24,
              "prev_low": {...}, "prev_high": {...},
              "break_low": {...}, "break_high": {...}},
 "ref_date": "2026-10-13", "source": "linkage_signals|linkage_plot|linkage_collector"}
```

- **ref_date** = 个股线末根 dt 前 10 位（信号参考交易日，非采集时刻）
- **幂等**：键 = `ref_date|code|period|signal`，进程内缓存 + 落盘前锁内复核，同键不重复落（任务书"同日同股同信号"细化到周期——1d 与 30m 同日同信号是两条独立观察）
- **原子追加**：`.lock` 独占文件锁（120s 陈旧回收）→ 复核 → `O_APPEND` 单次 write + fsync
- **独立入口**：`python linkage_collector.py <code> [--periods 1d,30m,5m]` 现判现收
- 长跑日志：`C:/Temp/linkage_collector.log`

### 挂载点（注记式，均 try/except 包裹，采集失败不影响产线）

排查结论：**linkage 无计划任务驱动的每日批次**，每日产出入口就是两条代码链路，均已挂：

1. `linkage_signals.py` `main()`（linkage_signals.py:154-160）：判定报告输出后 `collect(code, res)`；另在 `judge()`（linkage_signals.py:110-112）附加 `stock_last_dt` 字段（纯增量，供 ref_date 用，不改判定逻辑）
2. `linkage_plot.py` `gen_all()`（linkage_plot.py:162-168）：每周期侧车产出后按侧车内容落档

未注册/修改任何计划任务（纪律遵守）。**每日采集生效方式：每日对观察池跑 `linkage_signals.py <code>` 或 `linkage_plot.py <code>` 即自动落档**——若后续要无人值守收割，需另一条泳道把观察池循环挂进现有每日任务，本次未越权代办。

### 冒烟验证

- 301216.SZ 1d 判定：信号②触发（板块+4.1%、超额−17.2pp）→ +1 落档，ref_date=2026-10-13 ✓
- 同参数二次运行：+0（幂等）✓
- linkage_plot 同日同股同信号：+0（幂等拦截）✓，PNG/侧车产出不受影响 ✓

## 四、历史命中率回放（任务3 交付）

### 新文件：`quant_backtest/linkage_hitrate_backtest.py`（只读）

- 数据源（全本地仓）：`ts_daily`（个股）/ `ts_index_daily` 000001.SH（大盘）/ `tdx_sector_daily`（板块）/ `ts_stock_basic`（universe≈5000 只 + industry→板块归属，linkage.resolve_industry_block 同源）
- 数据范围：个股 2023-07-31 起、板块 2021 起、指数至 2026-10-09 → 可评估区间 **约 2024-08 ～ 2026-09**（250 根归一化窗口预热 + T+20 前向截断）
- **与产线口径逐条对齐**：①② 用 250 根窗口收益（要求三线窗口全有数据，此时归一化基=窗口首根，与产线一致）；③④⑤ 破位判定利用"窗口内归一化基恒定 → pct 比较等价原始 close 比较"，直接用原始 close rolling 极值（shift(1) 排除最新一根），严格等价
- **样本去重**：连续触发段只取首日（episode 去重），防同段行情重复计数
- 命中：口径A = T+k 绝对收益>0；口径B = 跑赢所属板块
- 明细：`data/linkage_hitrate_result.json`

### 命中率表（全量回放，episode 去重）

| 信号 | T+k | 口径A n | 口径A 命中 | 口径A 均收 | 口径A PF | 口径B n | 口径B 命中 | 口径B 超额均 | 口径B PF | 判据结论 |
|---|---|---|---|---|---|---|---|---|---|---|
| ① 抗跌 | T+5 | 208,936 | 49.6% | +1.09% | 1.46 | 208,936 | 46.0% | +0.17% | 1.075 | 继续攒样本 |
| ① 抗跌 | T+10 | 202,777 | 51.9% | +2.43% | 1.793 | 202,777 | 45.0% | +0.42% | 1.134 | 继续攒样本 |
| ① 抗跌 | **T+20** | 193,537 | **56.8%** | +6.06% | 2.623 | 193,537 | 44.1% | +1.30% | 1.291 | **够格挂账(仅A/T+20)** |
| ② 滞涨 | T+5 | 1,102,931 | 49.7% | +0.37% | 1.176 | 1,102,931 | 45.9% | +0.04% | 1.019 | 继续攒样本 |
| ② 滞涨 | T+10 | 1,095,991 | 50.9% | +0.77% | 1.264 | 1,095,991 | 45.6% | +0.05% | 1.017 | 继续攒样本 |
| ② 滞涨 | T+20 | 1,080,952 | 51.4% | +1.57% | 1.39 | 1,080,952 | 45.5% | +0.05% | 1.013 | 继续攒样本 |
| ③ 共振底 | T+5 | 10,932 | 66.6% | +2.49% | 2.637 | 10,932 | 53.3% | +0.61% | 1.393 | **够格挂账** |
| ③ 共振底 | T+10 | 10,930 | **74.7%** | +6.41% | 6.013 | 10,930 | 54.9% | +1.22% | 1.614 | **够格挂账** |
| ③ 共振底 | T+20 | 10,926 | **87.6%** | +12.61% | 19.859 | 10,926 | 55.4% | +2.25% | 1.904 | **够格挂账** |
| ④ 孤军深入 | T+5 | 82,731 | 42.5% | +0.01% | 1.003 | 82,731 | 41.2% | −0.11% | 0.971 | **建议废弃** |
| ④ 孤军深入 | T+10 | 82,432 | 42.4% | +0.13% | 1.023 | 82,432 | 40.4% | −0.29% | 0.945 | **建议废弃** |
| ④ 孤军深入 | T+20 | 82,103 | 41.1% | −0.18% | 0.976 | 82,103 | 38.6% | −0.72% | 0.896 | **建议废弃** |
| ⑤ 三共振 | T+5 | 10,541 | 37.6% | −1.72% | 0.647 | 10,541 | 39.7% | −0.35% | 0.893 | **建议废弃** |
| ⑤ 三共振 | T+10 | 10,541 | 41.5% | +0.60% | 1.131 | 10,541 | 39.8% | −0.15% | 0.965 | **建议废弃** |
| ⑤ 三共振 | T+20 | 10,541 | 45.7% | +2.73% | 1.565 | 10,541 | 38.5% | −0.44% | 0.932 | 继续攒样本(贴线) |

三档标准（对照 55% 判据，按口径A）：够格挂账 = n≥30 且 ≥55%；建议废弃 = n≥30 且 <45%；其余（含 n<30）= 继续攒样本。

### 55% 判据对照结论

- **③ 共振底：三个周期全部够格挂账**（66.6%/74.7%/87.6%，PF 2.6→19.9，超额口径也 ≥53%）。三线同破前低后的超跌反弹是五信号里唯一"绝对+相对双口径都过线"的信号，**建议优先对信号③启动挂账组合论证**
- **① 抗跌：仅 T+20 口径A 过线（56.8%）**，且绝对均收/PF 漂亮（+6.06%/2.62）——但口径B 跑赢板块只有 44.1%，超额均 +1.30pp 很薄：绝对收益主要吃的是 2024-09 起的市场 beta 而非选股 alpha。**建议对"抗跌"改用超额口径重新定义命中后再议**，直接按口径A 挂账有 beta 冒充 alpha 的风险
- **② 滞涨：全部 45%～51%，贴不过线**。样本量极大（110 万 episode）仍只到 50% 上下，说明不是样本不够而是信号本身无前向区分度；但超额均 >0（弱正），**继续攒影子样本，观察正式影子档案（去重口径更严）是否一致**
- **④ 孤军深入：建议废弃**。n=8.2 万、三个周期全在 38%～42%，超额 PF<1——"个股独破前高"在当前市况下是假突破形态，与模板直觉相反
- **⑤ 三共振：T+5/T+10 建议废弃，T+20 贴线继续观察**。短期显著负（T+5 均收 −1.72%），唯一价值是 20 日窗口的动量延续，不达标

## 五、遗留风险

1. **样本非独立**：episode 去重只合并连续日，跨股同日、间隔复燃的样本高度相关（尤其③的 87.6% 部分来自同几轮大跌的反弹潮、②的百万样本是面板堆积）。命中率数字可用于信号间相对排序，**挂账组合论证时须做按时间分块的稳健性检验**
2. **区间偏置**：可评估区间约 2024-08～2026-09，覆盖一轮急跌（2024H2 前）与一轮强修复；②"滞涨"与④的结论对市况敏感，换 regime 可能反转
3. **窗口基近似**：回放要求三线 250 窗口全有数据才判定（严格掩码）；产线在数据不足窗口会用更晚的公共首基，新股/长停牌股的边界样本与回放存在轻微口径差（对结论影响可忽略，但影子档案与回放数字不会逐一相同）
4. **数据尾巴**：ts_index_daily 只到 2026-10-09、tdx_sector_daily 到 10-12、ts_daily 到 10-13，三线不同步；回放已在评估端截断，但**影子 collector 每日判定会拿 ffill 的陈旧大盘值**（产线 load_kline 走 zhice-db 不受影响，仅历史回放受此限制）
5. **collector 无人值守缺口**：当前挂的是代码入口，没有注册每日批处理（纪律禁止本次代办）；若观察池每日没人跑 linkage CLI，影子档案只进手工触发样本
6. **④/⑤ 废弃建议未执行**：本次只给数字依据，未动 linkage_signals 的信号逻辑（注记式纪律）；是否下线④⑤由产线 owner 决策

## 六、文件清单

| 文件 | 动作 |
|---|---|
| `quant_backtest/linkage_collector.py` | 新增（影子采集器） |
| `quant_backtest/linkage_hitrate_backtest.py` | 新增（只读回放） |
| `quant_backtest/linkage_signals.py` | 注记式最小改动（.bak_20261014 已备份）：judge() 附加 stock_last_dt；main() 挂 collector |
| `quant_backtest/linkage_plot.py` | 注记式最小改动（.bak_20261014 已备份）：gen_all() 挂 collector |
| `quant_backtest/data/linkage_shadow.jsonl` | 新增（影子档案，首条已落：301216.SZ 信号②） |
| `quant_backtest/data/linkage_hitrate_result.json` | 新增（回放明细） |
| `C:/Temp/linkage_collector.log`、`C:/Temp/linkage_backtest_full.log` | 运行日志 |

未推送飞书、未动 positions.json、未注册/修改计划任务。

## 七、20261014 落地（linkage-prod-dev，owner 三项拍板执行）

### 7.1 信号③ 共振底挂账组合论证 → 判定【暂缓，改道择时】

`linkage_resonance_audit.py`（新增，只读）+ `reports/linkage_resonance_audit.json`。三判据仅一项过：

| 判据 | 实测 | 结论 |
|---|---|---|
| 独立反弹潮 ≥5 | 5 个（间隔>10 交易日合并） | ✓ |
| 块间不塌方 | T+5/T+10 塌方：2026H2 块 T+5 命中仅 27.9%/PF 0.32；**T+20 四块全过（81.7~95.6%）** | ✗（短持有期） |
| 规模可控 | 单日 P95=**1605 只**，21 个活跃日 19 天 >50 只 | ✗ |

- 集中度坐实：前 3 潮贡献 **83.8%** 总收益（遗留风险#1 成立）
- 定性：③ 本质是**普跌反弹的市场级信号**而非选股信号——信号有效性未被推翻（T+20 稳），但不能按选股组合挂账；建议改道"大盘择时口径"另立项论证。组合规则草案已备案在 audit JSON

### 7.2 ④⑤ 下线（owner 拍板）

- `linkage_config.json`（新增）：`sig4_disabled: true`（④ 判定层掐断：不判定/不展示/不落档）、`sig5_display_only: false`（⑤ 判定与 collector 留档照旧、产线展示/推送/页面隐藏）
- `linkage_signals.py` / `linkage_plot.py` 改动（`.bak_20261014` 备份）；判定一次、侧车展示与 collector 留档分离；下游防御式无字段强依赖
- **回退：删除 linkage_config.json 即恢复旧行为（全开）**
- 测试：新单测 5/5（④关闭零输出/⑤判定在不展示/collector 照收⑤/缺省旧行为）+ 既有 linkage 测试 5/5 回归 + CLI 冒烟

### 7.3 collector 观察池自动化（owner 批准）

- `linkage_collect_daily.py`（新增）：观察池 = positions CN(pending+open) ∪ watchlist CN，去重 25 只；空池静默退出、幂等、单票失败跳过（cross TopN 无落盘产物本版跳过，列遗留）
- `start_linkage_collect.ps1`（新增，ASCII 校验过）；日志 `C:/Temp/linkage_collect_daily.log`
- 计划任务 **LinkageCollect** 已注册：每日 16:20（收盘湖落定后、strong 15:45 后错峰），Administrator，PT1H；未动任何既有任务
- 手动触发验证：LastTaskResult=0，`pool=25 ok=25 fail=0 new_records=24`，影子档案正常落
- 手工注册命令（如需重建）：`Register-ScheduledTask -TaskName 'LinkageCollect' -Trigger (New-ScheduledTaskTrigger -Daily -At 16:20) -Action (New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\aios\quant_backtest\start_linkage_collect.ps1') -User 'Administrator' -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1))`

### 7.4 本节遗留事项

1. ③ 改道"大盘择时口径"立项待 owner 点头
2. cross TopN 纳入观察池：待 cross 产线有每日落盘产物后接入
3. ⑤ T+20 影子样本半年后（2027-04）复议
4. linkage_collector 每日判定在历史回放口径下吃 ffill 陈旧大盘值（产线 zhice-db 不受影响）
