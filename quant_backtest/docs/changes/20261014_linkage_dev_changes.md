# 20261014 linkage-dev 变更留痕

> 注：本文件在 20261014 落地会话开始时不存在（原定"恢复上下文"失败），
> 按任务简报恢复以下骨架后追加落地小节。后续变更请继续追加。

## 背景上下文（恢复自任务简报，供后读者）

### 五信号历史命中率表（linkage_hitrate_backtest.py，episode 去重，口径A=绝对收益>0 / 口径B=跑赢板块）

| 信号 | T+5 | T+10 | T+20 | 回放结论 |
|---|---|---|---|---|
| ① 抗跌 | — | — | 过线（T+20 够格） | 继续攒/挂账边缘 |
| ② 滞涨 | 未过线 | 未过线 | 未过线 | 继续攒样本 |
| ③ 共振底 | 66.6% (PF 2.64) | 74.7% (PF 6.01) | 87.6% (PF 19.86) | 三档全"够格挂账" |
| ④ 孤军深入 | <45% | <45% | <45% | 建议废弃 |
| ⑤ 三共振 | <45% | <45% | 边缘 | 建议/继续攒 |

明细：`data/linkage_hitrate_result.json`。双口径中口径B（超额）普遍弱于口径A。

### collector 落档格式（linkage_collector.py → data/linkage_shadow.jsonl）

每行一个 JSON：`ts / code / name / period / signal / signal_name / note /
block / snapshot / ref_date / source`；幂等键 = ref_date+code+period+signal；
原子追加 + .lock 防并发。挂载点：linkage_signals.main()、linkage_plot.gen_all()。

### 遗留风险

1. **样本相关性**：③ 的 87.6%（T+20）部分来自同几轮大跌反弹潮，episode
   高度相关 → 本次任务A 用时间分块 + 独立反弹潮合并正面回应。
2. ⑤ 影子样本不足（回放建议废弃/边缘）→ 本次拍板：展示下线、collector
   留档攒 T+20 实盘影子样本，半年后复议。

---

# 20261014 落地（三任务，产线 owner 老田已批准）

执行环境：解释器仅 `C:/Program Files/Python311/python.exe`；改动前备份
`.bak_20261014`（linkage_signals.py / linkage_plot.py 均已有当日早前备份，
**未覆盖**，回退即还原该备份）；未推送飞书。

## A. 信号③ 共振底挂账组合论证（含时间分块稳健性）

新文件 `linkage_resonance_audit.py`（只读，判定与数据复用
linkage_hitrate_backtest，零新口径），产出
`reports/linkage_resonance_audit.json`。

### 半年度分块稳健性（口径A 绝对收益）

| 块 | T+5 | T+10 | T+20 |
|---|---|---|---|
| 2024H2 | n=4418 命中62.3% PF2.43 | 命中75.3% PF12.13 | 命中81.7% PF15.51 |
| 2025H1 | n=2478 命中96.0% PF106.9 | 命中92.6% PF38.30 | 命中95.6% PF63.79 |
| 2025H2 | **无样本**（该半年全市场无共振底信号日） | — | — |
| 2026H1 | n=1602 命中91.8% PF40.39 | 命中78.8% PF8.03 | 命中90.8% PF23.81 |
| 2026H2 | n=2434 **命中27.9% PF0.32** | 命中52.7% PF1.08 | 命中88.0% PF15.49 |

- 块间判定（≥4/5 块命中≥55% 且无单块 PF<0.5）：T+20 **过**（4/4）；
  T+10 **不过**（3/4，2026H2 52.7%）；T+5 **不过**（3/4 且 2026H2 PF 0.32
  塌方——最近一轮潮 2026-07-13~17 的 T+5 短窗反弹已走完）。

### 独立反弹潮（全市场信号日按相邻间隔>10 交易日切潮）

潮数=5（判据≥5：过）。**前 3 潮贡献 83.8% 总收益 ⚠ 集中度风险（>60%）**：

| 潮 | 区间 | n | T+20 命中 | 收益贡献 |
|---|---|---|---|---|
| 1 | 20240812~0913 | 4414 | 81.7% | 46.6% |
| 2 | 20250110~0113 | 853 | 94.0% | 6.1% |
| 3 | 20250407 | 1623 | 96.4% | 19.1% |
| 4 | 20260323 | 1602 | 90.8% | 10.1% |
| 5 | 20260713~0717 | 2434 | 88.0% | 18.1% |

### 规模现实性（活跃日 21 天）

单日只数 P50=310 / **P95=1605** / 最大=1625；>50 只日 19/21 天。**不过**
（P95 远超 20 只上限）——③ 本质是**普跌反弹的市场级信号**，不是选股信号。

### 组合规则草案（备案，暂缓期间不启用）

- 触发→T+1 开盘买；持有期基准 **T+20**（T+5/T+10 在最近块塌方，短窗不采）；
- 去重口径：episode 首日 + **同潮只开一轮仓**（潮内不重复加仓）；
- 普跌反弹日（单日信号>50 只）当日不开个股新仓；若启用，倾向以指数/仓位
  层面表达而非个股篮子（单票权重上限、同时在手上限等选股型条款对③不适用，
  留待改走择时口径后另议）。

### 判定：**暂缓**

三判据（块间不塌方 + 独立潮≥5 + 单日P95≤20只）仅独立潮一项全过，
T+10/T+5 块间塌方、规模失控（P95=1605 只），且前3潮集中度 83.8%。
信号本身有效性未被推翻（T+20 各块全过），但**不能按选股组合挂账**；
建议后续按"大盘择时/仓位信号"重新立项论证。影子 collector 继续留档。

## B. ④⑤ 信号下线（owner 拍板）

### 开关机制

- 新文件 `linkage_config.json`（仓库原无 linkage 专用配置，故新建）：
  - `"sig4_disabled": true` —— ④ 孤军深入**完全不判定、不展示、不落档**；
  - `"sig5_display_only": false` —— ⑤ 三共振**判定保留、collector 照落**，
    产线展示/报告/侧车隐藏（注意语义：键存在且为 `false` 才隐藏；
    `true` 或缺省 = 照常展示）。
- **缺省回退**：缺文件/缺键/坏 JSON = 旧行为全开（删除
  linkage_config.json 即整体回退）。

### 改动点（备份 .bak_20261014 均为当日早前原始版，未覆盖）

- `linkage_signals.py`：新增 `load_config()/sig4_off()/sig5_hidden()`；
  `judge_series(..., cfg=None)` 在 sig4_disabled 时**不产出 tag4**
  （判定层掐断 → 展示/落档天然全无）；`report(..., cfg=None)` 隐藏⑤
  展示并附操作者提示行；判定结果中⑤保留（collector 照收）。
- `linkage_plot.py`：`plot_one(..., sig=None)` 支持外部传入完整判定；
  侧车 `.json` 的 `signals` 按开关过滤⑤（门户展示隐藏）；`gen_all()`
  判定一次、侧车展示与 collector 留档**分离**——collector 收完整
  signals，不受展示开关影响（下游读不到④⑤字段不炸：全部按列表
  过滤/缺省处理，无字段强依赖）。
- `linkage_collector.py`：零改动（幂等/格式不变，⑤照落）。

### 单测（test_linkage_toggle.py，5/5 通过；既有 test_linkage_volume_integrity.py 5/5 回归通过）

1. ④关闭零输出（judge/报告文本均无 tag4）✅
2. ⑤判定在但不展示（判定保留、报告隐藏+提示行）✅
3. collector 仍收⑤（临时档文件验证落档+幂等）✅
4. 开关缺省旧行为（无配置 → ④⑤全开）✅
   附加：真实 linkage_config.json 生效路径验证 ✅

### 回退方法

`copy linkage_signals.py.bak_20261014 linkage_signals.py` +
`copy linkage_plot.py.bak_20261014 linkage_plot.py` + 删除
`linkage_config.json`（若只想去开关保留采集：仅删 json 即可，代码侧
缺省=全开）。

## C. collector 观察池自动化（owner 已点头）

### 新文件

- `linkage_collect_daily.py`：观察池 = positions.json（pending+open 的
  CN 票）∪ ../news_collector/news_config.json watchlist（CN 归一化与
  runner.resolve_watchlist 同款）∪ cross 每日 TopN（**现无落盘产物，
  本版跳过**，见遗留事项）。tushare 代码精确去重、保序（持仓优先），
  单次上限 `--max 60`（默认），逐票 1d 周期调
  linkage_collector（幂等，单票失败跳过）；**观察池为空静默退出 0**。
- `start_linkage_collect.ps1`：ASCII only（校验通过），循
  start_weak_track.ps1 风格走 `C:\aios\quant_backtest` junction 路径；
  日志 `C:/Temp/linkage_collect_daily.log`（exit 行）+
  `_out/_err` 原始输出。

### 计划任务：已注册 ✅

- 任务名 `LinkageCollect`（未动任何既有任务），每日 **16:20**（收盘数据
  落湖后、strong 15:45 之后错峰），runas Administrator（Interactive，
  RunLevel Highest），执行时限 **PT1H**，StartWhenAvailable。
- 注册命令（已执行成功；如需重建可手工执行）：

```powershell
$action = New-ScheduledTaskAction -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\aios\quant_backtest\start_linkage_collect.ps1'
$trigger = New-ScheduledTaskTrigger -Daily -At 16:20
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) -StartWhenAvailable
Register-ScheduledTask -TaskName 'LinkageCollect' -Action $action -Trigger $trigger -Settings $settings -User 'Administrator' -RunLevel Highest -Force
```

### 手动触发验证（2026-10-14 10:59）：跑通 ✅

`Start-ScheduledTask` → LastTaskResult=0；日志
`DONE pool=25 ok=25 fail=0 new_records=24`（观察池 25 只 CN 票，
24 条新信号落 `data/linkage_shadow.jsonl`，幂等键正常）。
回退：`Unregister-ScheduledTask -TaskName 'LinkageCollect' -Confirm:$false`。

## 7.5 择时口径论证（③共振底 → 大盘择时/风控联动，20261014 timing-dev 只读审计）

新文件 `linkage_timing_audit.py`（只读，判定复用 linkage_hitrate_backtest.
judge_matrix，零新口径、零产线改动），产出 `reports/linkage_timing_audit.json`，
日志 `C:/Temp/linkage_timing.log`。样本 20210104~20261013（指数收益自
20230731 起）；宽度 = 全市场 close>MA20 占比（闸门参考线 40/45）。
qpzm/cross 闸门内信号逐 episode 明细未落盘 → 策略层后验按预案用
指数+宽度代理。触发潮定义：日触发数 ≥ 阈值（P90活跃日=1155 只 /
固定 500 只两档），相邻间隔 ≤10 交易日合并。

### 触发潮清单（固定 500 只档，6 潮）

| 潮 | 区间 | 触发日数 | 峰值只数 | 潮内指数 | 宽度@潮首 | 潮尾后 T+5 | T+10 | T+20 | T+60 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 20240822~0827 | 2 | 1115 | -0.0% | 14.2% | -1.61% | -3.67% | +5.34% | +15.69% |
| 2 | 20240913 | 1 | 558 | 0.0% | 24.9% | +5.88% | +29.06% | +21.51% | +24.31% |
| 3 | 20250110 | 1 | 672 | 0.0% | 9.3% | +2.31% | +2.65% | +5.91% | +3.13% |
| 4 | 20250407 | 1 | 1625 | 0.0% | 5.1% | +5.37% | +6.29% | +8.25% | +12.13% |
| 5 | 20260323 | 1 | 1605 | 0.0% | 7.4% | +2.88% | +2.02% | +7.13% | +9.17% |
| 6 | 20260713~0717 | 2 | 1155 | -3.82% | 17.5% | +1.33% | +1.81% | +4.33% | 窗口未满 |

- **潮后 20 日均值 +8.75%、相对全样本基线（+0.62%）超额 +8.13pp**；
  P90 档（1155 只，3 潮：20250407/20260323/20260717）潮后 20 日均值
  +6.57%、超额 +5.95pp。反弹择时方向证据强（T+20 六潮全正）。
- 触发期当下：触发日指数当日均收 **-2.3%**（P90 档 -4.67%），但潮长仅
  1~2 交易日，潮内累计收益 0~-3.8% → "当下确实弱"成立，但**可避损的
  时间窗极小**。

### 与宽度闸门的互补性（关键负结论）

- 触发日宽度分布（500 只档 8 个触发日）：**<40% 区间 8/8 = 100%**，
  40~50 / 50~60 / ≥60 均为 0；P90 档同样 3/3 全在 <40%（潮首宽度
  5.1%~24.9%，深度低位）。触发潮全部发生在宽度 <40%、med20 深负区
  ——与 qpzm/cross 现闸门（breadth<40% 拦截）**信息完全重叠，不正交**。
- 事件×状态正交假设被证伪：③ 是"宽度深度低位时的破位事件"，不产生
  闸门维度的新信息（闸门在触发日早已全关）。

### 两段式后验（触发日次日起空仓，宽度回升>45% 或 20 交易日解除）

| 口径 | 策略全期 | 买入持有 | 策略MDD | B&H MDD | 空仓日数 |
|---|---|---|---|---|---|
| 500 只档 | **-1.89%** | +15.88% | -17.89% | -17.89% | 54 |
| P90 档 | +2.06% | +15.88% | -17.89% | -17.89% | 33 |

逐潮空仓窗收益（被"避开"的部分）：+5.88 / +2.39 / +6.45 / +4.77 /
-0.23 / -2.08 → **4/6 个空仓窗避开的全是反弹起点**（202409 潮错过
+5.88%、202504 潮错过 +6.45%）。宽度从 <40 回升穿 45% 滞后于 V 底
5~14 天，"触发日减仓→宽度回升回补"是**反生产力**的：③ 不能当
RiskGuard 式离场器用，MDD 也未改善（最大回撤发生在无触发潮时段）。

### 立项判定：**不值得立项**（按任务书判据）

| 判据 | 门槛 | 实测 | 结果 |
|---|---|---|---|
| 触发后 20 日指数超额 | ≥2pp | +8.13pp | ✅ 过 |
| 与宽度闸门信息重叠 | <70% | **100%** | ❌ 不过 |

超额判据大幅过线、重叠判据完全不过 → 择时状态变量/闸门联动口径
**不立项**。③ 对 ①qpzm/cross 闸门、②盘前作战卡 regime 的增量
信息为零（触发时宽度必然已 <40%）。

### 规则草案（备案，不启用）

1. **不并入任何环境闸门**：③ 触发与 breadth<40% 完全共线，叠加
   无收紧效果（闸门最优点 45~50% 的收紧应由宽度自身完成）。
2. **不做离场器**：两段式跑输 B&H 13.8pp（500 只档），离场触发
   交给宽度回升节奏，③ 不参与。
3. 唯一有数字支持的方向（如 owner 日后复议）：**潮尾回补确认器**——
   触发潮尾日 T+1 起恢复/加快回补（六潮 T+20 全正、均值 +8.75%），
   表达为"深度低位区内的事件确认"，不改变仓位闸门本身。与 A 节
   "以指数/仓位层面表达"的备案一致，样本仅 6 潮且前 3 潮集中度
   问题依旧存在，需影子攒样本后再议。

### 产物与回退

- 新文件：`linkage_timing_audit.py`（只读）、
  `reports/linkage_timing_audit.json`（本节全部数字来源）、
  `C:/Temp/linkage_timing.log`。
- 产线零改动，无回退需求；删除上述三文件即完全清除。

## 遗留事项

1. **③ 改道择时口径**：③ 实为普跌反弹市场信号（单日 P95=1605 只、
   21 个活跃日），按选股组合挂账已判暂缓；建议另立项按指数/仓位
   择时论证（T+20 各块全过是正面证据）。
2. **⑤ 影子样本**：collector 留档持续攒 T+20 实盘样本，半年后
   （2027-04 前后）复议是否恢复展示。
3. **cross 每日 TopN**：无现成落盘产物，观察池暂只含持仓+watchlist；
   若后续 cross 侧出 TopN json，在 linkage_collect_daily.build_pool()
   加第三路即可。
4. **③ 最近块 T+5 塌方**（2026H2 命中27.9%/PF0.32）：若未来启用，
   持有期勿短于 T+10；短窗反弹速度依赖行情。
5. sig5_display_only 语义有反直觉处（false=隐藏），已在配置
   _comment 与本文档双处注明；owner 复议恢复展示时改回 `true` 或删键。
