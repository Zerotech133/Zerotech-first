# StrongEod 时点拍板备忘（2026-10-19，WS-R2 只读研究）

> 性质：只读研究。全程未改任何脚本/计划任务/数据，未连真实 PG，未跑任何写同步；仅新建本文件。守卫判据逐字对照 strong_scan.py:52-95 与 strong_guard_precheck.py:31-34（GUARD_SAMPLE/GUARD_BLOCK/GUARD_MIN_CN 三常量一致）。

## 〇、结论摘要

1. **两时点消费的数据完全相同**：无论 06:30 还是 15:45，守卫与扫描消费的都是 T-1 收盘行情（板块锚=D-1 由昨日 17:30 TdxSync 供给，个股锚=D-1 由当日凌晨 01:30~05:30 zhice 补数供给）。时点差异不在新鲜度，在**失败处置窗口、盘前可用性与运行窗口宽裕度**。
2. **守卫拦的是 scan，拦不住 card**：10-19 15:46 实测 strong_card --send 把 10-13 旧名单 119 只「板块领涨」全部重推（strong_state.json 全部条目 ts=2026-10-19 15:46）——这是比「名单缺席」更危险的静默错误，与时点拍板正交，须先修。
3. 建议**恢复 06:30**（有 PACT 先例、盘前可用、失败当日可补救、兼容 --full 全量评估窗口）；若担心凌晨补数贴边，用**双跑 06:30+15:45**（守卫+当日熔断天然幂等）。前提：远端板块日线采集恢复（本机改时点无效）。

## 一、时序还原（数据水位何时推进）

### 1.1 供给侧：两仓独立推进，节奏不同

| 仓 | 存储 | 定时供给 | 推进时刻 |
|---|---|---|---|
| 板块仓 tdx_sector_daily | 本地 parquet（quant_backtest/data/tdx/，30 分片实测） | TdxSync 计划任务 **17:30**（Get-ScheduledTask 实测 trigger=2026-10-16T17:30:00；tdx_sync_task.log 多日 `exit=0 17:30:45/17:30:53` 等实证） | 远端源头每日 17:00 更新（tdx_sync.py:4），本机 17:30 拉到 D 日行情 → **17:30 后板块锚=D** |
| 个股仓 v2_bar_daily（zhice-db，外部 PG） | zhice-db | zhice 凌晨补数（非本仓任务） | **01:30~05:30 浮动补齐 D-1 日全量**（strong_scan.py:60-63 口径注记；审批包实测 10-16 全量 5569 行 ≥5000 阈）；傍晚仅零星成功 |

当前板块仓停更实证：881019 最新 trade_date=**20261012**（parquet 全 30 分片逐片 max 实测，10-19 晚）；10-19 14:51 手动补跑与 17:30 定时跑均撞 DNS NameResolutionError（C:/Temp/tdx_sync_out.log 实测多条 ERROR），且以 `ALL DONE` fail-open 收尾（旧代码）。根因闭合见 M0 计划 §8.11-8.12 与 deliverables/strong数据链修复审批包_20261019.md：**10-13 起远端源头板块日线采集停更**（远端最后写入 2026-10-12 17:00:14），本机 DNS 间歇故障是叠加因素。

### 1.2 StrongEod 两时点各自消费哪天的锚

设运行日为 D：

| 时点 | 板块锚 | 个股锚 | 守卫判定 | 名单语义 |
|---|---|---|---|---|
| **06:30 跑 D 日** | D-1（昨日 17:30 TdxSync 供给） | D-1（当日凌晨补数，若完成） | 要求相等 → **通过**（凌晨补数完成为前提） | 基于 D-1 收盘 |
| **15:45 跑 D 日** | D-1（昨日 17:30 供给，当日 17:30 尚未到） | D-1（今晨补数；当日收盘数据 17:00 后才进远端、次日凌晨才进 zhice-db） | 要求相等 → **通过** | 基于 D-1 收盘 |
| 15:45 跑 D 日（若昨日 17:30 TdxSync 失败） | D-2 | D-1 | 不等 → **拦截**（10-16~10-19 实况） | 无名单 |

**即：两时点的数据语义完全等价，均为 T-1。** D 日 15:45 时当日收盘数据尚未入任何一仓（远端 17:00 更新 → 本机 17:30 同步 → 次日凌晨进个股仓），不存在「15:45 更新鲜」的窗口。

### 1.3 历史运行时点实证（产物 mtime + 任务日志）

| 日期 | 时点 | 证据 |
|---|---|---|
| 10-08 | 15:45（无守卫时代） | strong_scan.py:63-64 注记「10-08 14:55 StrongEod 15:45 错时同模式教训」——曾出 T-1 静默名单，守卫因此而生（10-08 加，strong_scan.py:25-27） |
| 10-09 ~ 10-13 | **06:30** | reports/strong_list_20261009~13.jsonl mtime 均 06:51-06:55；portal/data/strong_latest.json 内 items ts=06:30:13、汇总 ts=06:51:59（实测）；strong_card 快照 mtime 同步 06:5x |
| 10-14 06:30 | 06:30，守卫首次正确拦截 | docs/changes/20261014_alert_loop_changes.md:2-3「判例：今晨 StrongEod 06:30 freshness_guard 正确拦截」；guard_alert.log「sent 2026-10-14 06:30:00」 |
| 10-16 ~ 今 | **15:45**（任务重建，无留痕） | Get-ScheduledTask StrongEod trigger StartBoundary=2026-10-16T15:45:00（10-16 与全部任务同日重建）；LastRun 10-19 15:45:46 rc=0（rc 假象：守卫 exit 3 被 strong_card 第二动作 rc=0 掩盖，任务双动作实测 strong_scan.py → strong_card.py --send） |
| 杂时点 04:07/04:34/09:01/16:08/20:43 | 不明主体 | guard_alert.log 各日 dedup skip 实测；无对应计划任务注册，疑手动/watchdog，建议老田确认是否存在第二个注册点 |

## 二、下游消费时序（强名单被谁、在几点消费）

| 消费方 | 运行时点 | 消费内容 | 时效保护 | 证据 |
|---|---|---|---|---|
| strong_card.py --send（StrongEod 任务第二动作） | 紧随 scan（15:45 任务内 / 06:30 时代 06:5x） | reports/strong_latest.json 全量 items，板块领涨组真推飞书 | **无 stale 闸门**——10-19 15:46 实测重推 10-13 旧名单 119 只（strong_state.json pushed 全条目 2026-10-19 15:46；outbox/strong_card_2026-10-13.json mtime Oct 19 15:45；news_collector/outbox/pending_alert_strong_card.json mtime Oct 19 15:45） | strong_card.py:150-190 主流程只按当日 pushed 去重，不校验 date/basis |
| TodaysPicks 15:40（build_todays_picks.py） | **15:40，排在 StrongEod 15:45 之前** | strong_latest.json 的 basis/list | 有闸门：日扫源落后行情湖 >4 天整源不入（build_todays_picks.py:26-28、load_strong :109-124 优先用 basis）；当前 basis=10-12 已被排除——**被保护但缺席** | 时序错位：15:40 永远消费昨日 scan 产物；若 StrongEod 改 06:30，TodaysPicks 反而拿到当日早晨名单（新鲜度 +1 天） |
| portal/strong.html + api_flow.py:55 / api_misc.py:303 | 实时读 | webroot 副本 portal/data/strong_latest.json（strong_scan.py:363-372 同步落盘） | 无时效闸门，静态展示；当前挂 10-13 数据 | grep 实测 |
| shadow_weekly_report.py:66 | 周批 | reports/strong_latest.json 快照 | — | grep 实测 |
| strong_review.py | 人工 | strong_list_*.jsonl 归档 | — | strong_review.py:34 |

## 三、两时点利弊矩阵

| 维度 | 06:30 | 15:45 |
|---|---|---|
| 数据新鲜度 | T-1 | T-1（**等价**，见 §1.2） |
| 守卫通过率 | 依赖凌晨补数完成（浮动到 05:30 后的边缘日会被拦，strong_scan.py:60-61 自述）；板块仓须昨日 17:30 成功 | 凌晨补数几乎必然已就绪；板块仓依赖同条件（昨日 17:30 成功）——**该维度 15:45 略稳** |
| 与 TdxSync 先后依赖 | 依赖「昨日 17:30 已成功」；失败时昨晚即可发现处置 | 同依赖；但 17:30 在 15:45 之后，**当日 15:45 被拦时已无当日板块数据可等** |
| 失败后当日补救窗口 | **有**：拦截后人工补数 + `--force` 重扫，当日 15:45 前可出名单（10-13 判例：手动 catchup 后次日晨恢复） | **无**：拦截即全天无名单，补救延至次日 06:30/15:45 |
| 对次日盘前消费保障 | **名单 06:55 前落盘**（10-09~10-13 实测），盘前/盘中全天可读；TodaysPicks 次日 15:40 拿到 basis=D-1 的新名单 | 名单傍晚才出；若恢复失败，次日 15:40 的 TodaysPicks 只能拿 basis=D-2 的旧名单（被 >4 天闸门放行、真实新鲜度下降），盘前无新名单 |
| 运行窗口宽裕度 | 非交易时段，--full 全市场评估不受 15:45 窗口约束（PER_BLOCK_CAP=20 防超时即为 15:45 设的枷锁，strong_scan.py:14-17；W1-2 --full 方向下 15:45 更不可行） | 默认 20 只/板块截断为此妥协 |
| 治理 | AGENT-PACT 10-08 拍板先例 | 10-16 重建后现状，**无留痕**，与 PACT 冲突（审批包 §四已指出） |

## 四、建议

1. **首选：恢复 06:30**。理由：PACT 先例、名单盘前可用、失败当日有补救窗口、兼容 --full 全量评估方向；数据新鲜度与 15:45 完全等价，无代价。
2. **折中：双跑 06:30 + 15:45**（若担心凌晨补数贴边）。幂等性有双保险：strong_scan 当日熔断（strong_scan.py:22-23，06:30 成功则 15:45 跳过零成本）；strong_card 按 pushed state 当日去重（strong_card.py:60-66，不会重复推卡）。06:30 被拦时 15:45 提供二次机会。成本≈每日多一次守卫检查与潜在重复告警（guard_alert 12h 去重窗口可吸收）。
3. **无论选哪个，先修两个 P0（与时点正交）**：
   - **strong_card 补 stale 闸门**（date 或 basis 落后即不推/只落盘）：10-19 已实证静默重推 6 天前旧名单 119 只，为当前链路最危险缺陷；
   - **rc 可观测性**（审批包③，已批复实施中）+ **guard_alert 推送通道修复**：guard_alert.log 实测 10-14 起 push fail 连续 5 次未恢复——即便时点改对，拦截仍可能无人知晓。
4. **前置硬依赖**：远端（/home/xundao/TDX-Trading/）板块日线采集恢复（10-13 起停更，本机侧任何时点调整都无效）；恢复后本机 17:30 自动追平，次日所选时点守卫即放行。

## 五、需要老田补充的决策信息

1. 强名单是否存在**盘前（<09:30）消费场景**（人工看卡/自动化读取）？这是 06:30 的核心价值所在。
2. --full 全量评估是否会常态化（W1-2 方向）？常态化则 15:45 窗口基本出局。
3. 凌晨 zhice 补数近期的 SLA：浮动到 05:30 之后的频率？若贴边频繁，建议双跑而非单 06:30。
4. 15:45 是 10-16 重建时谁定的、有无未留痕的考虑（如刻意避开清晨机器占用）？
5. 04:07/04:34/09:01/16:08/20:43 等杂时 strong_scan 触发主体（guard_alert.log 实证存在）是否有第二个计划任务/watchdog 注册点？
6. 双跑方案（成本≈每日多一次守卫检查）是否接受？

## 六、证据文件索引

- 代码：quant_backtest/strong_scan.py:14-17（15:45 窗口）、:22-27（熔断+守卫）、:52-57（锚常量）、:57-95（freshness_guard）、:60-63（凌晨补数口径）、:363-372（webroot 副本）；strong_card.py:60-66（state 按日重置）、:150-190（无 stale 闸门推送）；strong_guard_precheck.py:31-34（判据复刻）；quant_backtest/tdx_sync.py:4（远端 17:00 更新）
- 任务：Get-ScheduledTask 实测 StrongEod 15:45（双动作 strong_scan→strong_card --send）、TdxSync 17:30、TodaysPicks 15:40、RpsDaily 16:05
- 日志：C:/Temp/tdx_sync_task.log（17:3x exit 记录、10-13/14 失败、LOCK-CLEAR）、C:/Temp/tdx_sync_out.log（10-19 14:51 与 17:30 两轮 DNS ERROR + ALL DONE fail-open）、quant_backtest/logs/guard_alert.log（10-14 06:30 sent、后续 push fail ×5）、C:/Temp/strong_full_timing.log（拦截文本实测）
- 产物：reports/strong_list_20261009~13.jsonl（mtime 06:5x）、portal/data/strong_latest.json（date=10-13, basis=10-12, ts=06:51:59）、strong_state.json（119 条 pushed @2026-10-19 15:46）、outbox/strong_card_2026-10-13.json（mtime Oct 19 15:45）
- 文档：docs/changes/20261014_alert_loop_changes.md:2-3（06:30 判例）、docs/plans/2026-10-19_选股策略独立化与选股Agent_M0计划.md §8.11-8.13、deliverables/strong数据链修复审批包_20261019.md §四
- 本机实测：tdx_sector_daily parquet 30 分片 881019 max trade_date=20261012（10-19 晚 pandas 只读逐片核验）
