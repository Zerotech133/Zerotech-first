# 执行卡 20261107：P0 cap 方案②（连续水位+游标）实施与切换程序

> 依据：`docs/changes/20261107_jev_cap_data_loss_p0_rfc.md`（D1 已批选方案②、
> D2 ①止血已落）。本卡 = 方案②代码落地 + D3 历史补数执行程序。
> 状态：**代码+单测已全部落地（env 门控默认关）；切换与补数回放待触发条件。**

## 1. 已落地内容（2026-11-07）

| 层 | 文件 | 内容 |
|---|---|---|
| R1/R2 SQL | `quant_backtest/tdx_read.py` `read_info` | 加 `until`（含）与 `after` 游标参数；after 模式升序三键 `date, COALESCE(_fetched_at,''), rowid` + WHERE 严格大于（rowid 防同秒并列组跨页丢行）；**缺省 None 逐字保持旧行为**（notice_lookup/premarket/datamart 零影响） |
| R2 读层 | `quant_backtest/datamart.py` `read` | 加 `after` 参数：sqlite 布局 SQL 下推；其余布局客户端严格大于过滤（降级语义，`_after_filter_rows`，**sqlite 源绝不重放谓词**——重放会误删并列组，实测丢 49/1225 行后修正）；end→until 透传（信息三表现无 end 调用方） |
| R2/R3 feed | `news_collector/jev_feed.py` | `feed_source_v2`（分页循环/耗尽推进/安全阀续走/失败不推进）、`_wm_norm`（v1 str↔v2 dict 兼容）、`_zero_cursor`；`JEV_WM_V2=1` 门控，**缺省 v1 路径逐字不变**；v1 对 v2 dict 水位降级读 `.get("date")`（RFC §7 回滚口径）；v2 下 cap_hit 信号退役，安全阀走 `page_limit` 留痕 |
| D3 工具 | `news_collector/jev_backfill_gap.py` | 缺口报表（缺省）+ `--apply` 幂等回放（复用 `feed_source_v2` 零游标单日口径，插入路径与生产同源）；夜窗 21:45–07:05 拒跑（错峰） |

关键设计决策：
- **v2 路径永不传 `after=None`**——那会回落 v1 的 `DESC LIMIT` 截断语义；
  游标缺失（首跑/耗尽重叠日）一律落零游标（start 前一日，升序整走）。
- 耗尽推进后下轮重叠重读该日：`_fetched_at` 新到的迟到行落在新位次必被
  捕获；旧行幂等去重（`_xd_id` 主键）。
- 安全阀 `WM_V2_MAX_PAGES=40`（cap=500 → 单源单轮至多 2 万行），打满时
  游标随水位保存、下轮续走，`runs.detail.src.<kind>.page_limit=true`。

## 2. 验证证据

- 单测三套 **19 项全过**：
  `test_read_info_cursor.py` 5（默认路径回归/until/并列组 15/15 无重无漏/NULL 兜底/真库回归）；
  `test_jev_feed_wm_v2.py` 7（v1 门控/dict 降级读/耗尽推进/幂等/安全阀续走/失败不推进/cap_hit 退役）；
  `test_jev_backfill_gap.py` 7（窗口守卫/夜窗拒跑/缺口计算/回放幂等/失败中止）。
- 回归：cap_hit + notice_scope + backfill_contend + v2 + gap 合跑 **56 passed**；
  datamart 家族（test_datamart/integrity/date_regressions/warehouse_read_guard/
  read_info_cursor）**70 passed**（2 失败为 warehouse_read_guard 跨进程 spawn
  存量 flaky，HEAD 复测同样失败，与本改动无关）。
- 真库端到端：游标分页 200/页 vs 全量基准 1225 行**集合相等、无重无漏、
  全程 (date,_fetched_at,rowid) 单调**；临时库集成 `feed_source_v2` 真实源
  分页耗尽 + 二轮幂等（1010 映射行 → 150 唯一条）。
- py_compile 全过；锚点断言流程（count==1 → 替换 → 新串存在）全程。

## 3. D3 历史缺口实测（2026-11-07 dry 报表，窗口 10-28..11-06）

**缺口合计 28,630 条**（RFC 原估 6,000–9,000 系按近期窗口，实际 3–5×）：
- news 缺口 ~8,648（含 11-02=794 / 11-03=729 两天零入库实锤）；
- notice 缺口 ~19,982（11-02=618 / 11-03=1,516 零入库）；
- 判读队列影响：notice 大头走 prefilter 白名单（零算力 neutral），news
  ~8.6k 进 Ollama 夜批（1,300/h ≈ 6.6h），一次性排水可接受。

## 4. 切换程序（两个触发门，均未到）

**门 A（切换 v2）**：RFC §6 顺序——公告过滤真实轮次生效验证通过后：
1. 今晚 21:00 JevFeed 首轮（粗筛已在位）→ 明早 09:00 快照 `notice_skip>0`
   首次出现；
2. 通过后 `start_jev_feed.ps1` 加 `$env:JEV_WM_V2 = '1'`，当日轮观察
   runs.detail.wm 变 v2 dict、`src.*.page_limit`/ADV 行为正常。

**门 B（D3 回放）**：门 A 通过当晚起算（避免污染首轮观测），白窗执行：
```
cd news_collector && python jev_backfill_gap.py --start 20261028 --apply
```
- 与夜批错峰（21:45–07:05 自动拒跑）；先跑一轮 dry 复核缺口无漂移；
- 回放后复核：`SELECT COUNT(*) FROM news WHERE source LIKE 'xd_%'`
  增量 ≈ 缺口数；resonance/词云侧 schema 零改动（只增行）。
- 回放行 jev=NULL，由 JevNight 按 jev IS NULL 全量承接判读。

## 5. 回滚

- **切回 v1**：撤 `JEV_WM_V2` env 即回；v1 代码已内置对 v2 dict 水位的
  降级读（`_wm_norm` 反向：`.get("date")`），无数据迁移。
- **read_info/datamart**：新参数全部缺省 None = 旧行为，函数签名向后兼容；
  如需彻底还原按 git revert 两文件（消费方仅 jev_feed v2 使用新参数）。
- **补数回滚**：xd id 幂等主键，误补行按 `source='xd_*' AND created_at=<回放日>`
  精确 DELETE（证据在 runs.detail）。

## 6. 风险与边界

- 入队量抬升 1.2–6.8×（RFC §5-1 已知代价，判读压力由 prefilter + 夜批承接）；
- 非 sqlite fallback 源（anns_d）客户端游标为严格大于，同秒并列组跨页
  理论有丢行——该源仅在真源不可用时触达，cap_hit→page_limit 可观测；
- 方案 B/A 收益重算（D4）：cap 修复上线 7 天后用全量样本重跑。
