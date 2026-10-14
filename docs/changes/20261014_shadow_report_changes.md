# 20261014 · W5 影子积累期：影子对账周报生成器（shadow-report-dev）

## 概要
- 新增 `quant_backtest/shadow_weekly_report.py`（只读，零产线行为改动）。
- 产物：`quant_backtest/reports/shadow_weekly_report.md`（人读周报）+ `shadow_weekly_report_data.json`（机器读）。
- 回退：删除上述三个文件即完全无痕（未注册计划任务、未推飞书、未改任何产线文件/数据）。

## 五线数据源路径（全部只读）
| 线 | 数据源 | 说明 |
|---|---|---|
| L1 双刀注记 | `portal/qd_scan_cache.json`（qd_scanner 每日 14:40 覆写） | 注记开关 `portal/qd_scanner_config.json`（qd_breadth_note=true，W3-5b） |
| | `quant_backtest/reports/qd_breadth_series.json` | 全市场宽度序列（20230103 起，湖口径） |
| | 约定目录 `quant_backtest/reports/qd_scan_daily/` | bucket×前向收益回算用逐日存档，**尚不存在**（fail-open 标注） |
| L2 chanlun P5 | `quant_backtest/reports/chanlun_beichi_grid.json` | W3-4 网格结果（mtime 2026-10-14 11:18，D_END=20261012），复用其前向收益口径（T+1 开盘买→持有 5/10/20 收盘卖、hfq、CN 双边 15bps），不重算回测 |
| | `quant_backtest/_v7_stream_cache.json` buy_ev | 本周新 3 买信号计数（bei 标注 vs 无标注） |
| L3 linkage | `quant_backtest/data/linkage_shadow.jsonl` | collector 落档；观察池约定 25 只（linkage_collect_daily.py） |
| L4 jev | `jev-eval/logs/review.log` | 只读正则提取最新"回填覆盖率"行，不跑 backfill |
| | `jev-eval/review_data/triples.jsonl` | FOCUS 判定与 outcome/ret_nd 前向收益 |
| L5 三档对照 | `quant_backtest/data/gate_shadow_3tier.jsonl` | gate_shadow_collect.py 产物，**尚不存在 → 标"待首采"，不报错** |

## 红灯/黄灯/绿灯判定规则
- **通用**：数据源缺失=🔴 红灯（fail-open，不炸报告）；关键样本不足或注记未生效=🟡 黄灯；正常积累=🟢 绿灯；约定产物未建=⚪ 待首采。
- **双刀**：cache 缺失→红；`qd_breadth_note` 开关关→黄；开关开但最新产物无 breadth 注记→黄；任一 bucket 样本≥30（重点 >=50 档）→ 触发 θ50 门控假说中期检验提醒。
- **chanlun P5**：grid json 缺失→红；任一 H∈{5,10,20} 的 diff_pp≤-1pp（标注 3 买更差）→黄（反向观察在位，固定字段跟踪）；全部>-1pp→绿（信号减弱）。
- **linkage**：jsonl 空→红；本周零增量→黄；信号②累计<30→黄。
- **jev**：覆盖率<50%→红；n≥300 且 FOCUS 前向均值>+1pp→绿（毕业）；否则黄（冲刺中）。
- **三档**：文件不存在→待首采；存在但本周无记录→黄。

## CLI
```
"C:/Program Files/Python311/python.exe" shadow_weekly_report.py [--week YYYY-MM-DD] [--json-only]
```
`--week` 取该日所在周的周一为起点；`--json-only` 不打印 MD 全文。输出固定两个文件，覆盖写、幂等可重跑。

## 挂载建议（注记式，未执行）
产线无"周五盘后"周汇总入口；最接近的周级任务为 **WeakWeeklyReport**（`deploy_weak_weekly.ps1` 注册，北京周一 15:50 调 `weak_track_weekly.py --send`）。建议（未改动）：在 `weak_track_weekly.py` 主流程末尾以 subprocess 追加调用本脚本 `--json-only`，随时可删、零副作用。不注册新计划任务。

## 首跑周报要点（2026-W42，2026-10-12 ~ 10-14）
- **双刀 🟡**：本周扫描 21 信号（10-12 14:46 产物），`qd_breadth_note` 已开但该产物无 breadth 注记（W3-5b 上线晚于该次扫描），待 14:40 下次扫描落档；宽度序列本周 2 日：10-12=29.6%、10-13=32.6%，均在 "<40" 档（若按 40% 档门控假设，两日新单均会 gate_would_block=True）；无逐日产物存档，bucket×前向收益回算待存档建立。
- **chanlun P5 🟡**：反向观察在位——ratio=1.0 口径 H5=-1.90pp / H10=-0.92pp / **H20=-3.20pp**（verdict"反向显著"），与 W3-4 登记的 -3.2pp（H20）一致；本周新 3 买 0 个；网格缓存 D_END=20261012 未过期。
- **linkage 🟡**：首采 25 行（ref_date 2026-10-13），观察池覆盖 25/25；信号①抗跌 22、信号②滞涨 3（累计 3，<30 继续攒）。
- **jev 🟡**：FOCUS 累计 425，有 outcome 336（n≥300 已达标），前向均值 **-1.04pp 未过 +1pp**，毕业冲刺未达成；回填覆盖率 76.8%（747/973，2026-10-13，阈值 50%）；本周新增 FOCUS 35 条。
- **三档 ⚪**：gate_shadow_3tier.jsonl 待首采。

## 发现的问题
1. 双刀注记链路尚未真正落过带注记的产物（开关 10-12 后才生效），下次 14:40 扫描后需复核 breadth/breadth_bucket/gate_would_block 字段确实写入。
2. qd_scanner 产物为单文件覆写，历史信号无逐日留存 → bucket×前向收益对照无法回溯，只能从存档建立日起算（建议后续给 qd_scan_cache.json 加每日快照，本任务未做）。
3. jev FOCUS 前向均值为负且样本已达标，按当前口径毕业无望，建议下周周报关注均值走势是否收敛。

## 遗留风险
- gate_shadow_3tier.jsonl 由另一代理并行在建，格式尚未对齐，首采后本脚本按 `strategy/tier`（含 `gate/bucket` 回退键）尽力解析，必要时微调。
- review.log 若轮转，jev 覆盖率历史行会丢失（周报只取最新一行，影响有限）。
- 本报告的 jev 口径只统计 triples.jsonl 中 `decision=FOCUS` 且 outcome 已回填样本，与 jev-eval 内部评估口径若有出入，以 jev-eval 为准。

## 回退
删除以下三个文件即无痕（无其他任何改动）：
- `quant_backtest/shadow_weekly_report.py`
- `quant_backtest/reports/shadow_weekly_report.md`
- `quant_backtest/reports/shadow_weekly_report_data.json`
