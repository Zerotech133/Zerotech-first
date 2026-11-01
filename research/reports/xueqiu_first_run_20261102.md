# S1-02/03 雪球 XueqiuCrawl 首跑 + 输出级健康（2026-11-02）

> 任务：`task-14`（车道1，W1/W2）｜owner：wa-quant（DSH-Lead 车道1）｜口径 as-of **2026-11-02 07:03–07:25 +08:00**
> 边界遵守：未改业务代码、未 commit、未重启服务；只读其他。
> **写盘点（透明声明）**：① 本报告；② `C:\项目\work\research2\xueqiu_first_run_20261102_070503.log`（任务要求）；③ `C:\Temp\xueqiu_first_run_20261102\` 游标/DB 备份（任务要求）；④ **采集器按设计写入** `quant_backtest/data/xueqiu/xueqiu_info.db` + `_manifest.json`（这是「生产首跑」的目的本身，非手工改数据；详见 §4）。
> 依据：《数据仓研发计划 v3 执行版》§三 K1 / §六 S1-02、S1-03（`docs/plans/2026-11-02_数据仓研发计划_v3_执行版.md:38,56,57,116`）。
> 凭据纪律：本文不含 token/cookie/代理凭据明文；出口代理仅记「主机名+端口」，IP 已脱敏。

---

## 0. 结论（先看）

| 项 | 结果 | 判定 | 证据 |
|---|---|---|---|
| **S1-02 首跑** | **成功**：`--mode all --pages 1`，rc=0，14.5s | ✅ 达成「`xq_status` today>0」 | §3 命令/日志、§4 DB 查询 |
| `xq_status` 当日增量（入库列口径） | **10 行**（其中净新增 status_id **2** 个，更新既有 8 个） | ✅ >0 | §4.1/§4.2 |
| `xq_hot_post` 当日增量 | **8 行**（净新增 8 个） | ✅ | §4.1 |
| 熔断/封禁迹象 | 无：无 403/302、无 `ip_blocked_until`、出口连通验证 200 | ✅ | §4.5 |
| **S1-03 输出级健康** | **未达成（现状缺口）**：dataops overview 的 XueqiuCrawl 条目**没有** `today_incr/last_output_at`；首跑成功后 `summary.alerts` 仍报「首跑待执行」 | ❌ 待开工（本报告给提案） | §5 实测 overview、§7 提案 |
| 派生层滞后 | `xq_status_classified` pending=**164**（今日 10 行未打标） | ⚠️ 观察项 | §4.6 |

**一句话**：采集侧首跑已恢复当日增量（K1 从「0/12 天」变为今日 10 行），但「输出级健康」在 dataops overview 上仍是旧的 **armed=绿 + 首跑待执行提示**，需要用 `today_incr/last_output_at` 改判红绿（S1-03，见 §7）。

---

## 1. 入口与参数通读（步骤 1）

### 1.1 wrapper：`quant_backtest/start_xueqiu_crawl.ps1`（16 行，940 B）

| 事实 | 证据 |
|---|---|
| 固定调用 `python -X utf8 xueqiu_crawl.py --mode all`（**无任何参数透传**） | wrapper L10 |
| 三铁律包装：stdout→`logs/xueqiu_crawl.out`、stderr→`logs/xueqiu_crawl.err`、状态→`logs/xueqiu_crawl_status.log`（ASCII） | L6-8, L9-12 |
| `rc != 0` 时调 `news_collector/alert_fail_wrap.py xueqiu_crawl <rc> <err>` 告警 | L13-15 |
| 计划任务 `\XueqiuCrawl`：Status=Ready、下次 **2026/11/2 18:00**（CSV 的 Last Run/Last Result 列为空，未取证） | `schtasks /query /fo CSV` |
| **结论**：wrapper 自身**没有**小批量/限量参数 → 本次按任务口径**直接调脚本**并显式传 `--pages 1`；建议（提案，不改代码）wrapper 增 `-Pages`/`-Mode` 可选透传 | 本报告 §7.2 P3 |

### 1.2 采集脚本：`quant_backtest/xueqiu_crawl.py`（724 行）

| 项 | 事实 | 证据 |
|---|---|---|
| 模式 | `--mode {timeline,hot,comments,all,takeover}`；`all`=timeline+hot | L655-658 |
| **小批量参数** | `--pages`（默认 3，硬上限 `X.MAX_PAGES=10`）、`--categories`（默认 102 沪深）、`--symbols`、`--max-symbols`、`--pool-days` | L662-673 |
| 限速 | `THROTTLE_BASE=2.0s` + `THROTTLE_JITTER=2.0s`（实际间隔 2–4s，未登录 ≈15 次/分钟约束）；步间强制 `X.jitter_sleep()` | `xueqiu_common.py:49-50`、crawl L241/322/364 |
| 429/超时 | `MAX_RETRY=4` 指数退避，`BACKOFF_CAP=300s` | `xueqiu_common.py:53-57` |
| 403 熔断 | `XqIpBlocked` → `ip_block_trip()` 写 `health.ip_blocked_until = now+30min`，本轮停全部雪球域请求 | `xueqiu_common.py:351-377`、crawl L588-593 |
| **游标/断点文件** | `data/xueqiu/_manifest.json`（`XqManifest`：done/rows/status/last_run + `health`） | `xueqiu_common.py:39-42`、`data/xueqiu/_manifest.json` |
| 数据落点 | `data/xueqiu/xueqiu_info.db` 三表：`xq_status`(pk status_id)、`xq_hot_post`(pk status_id)、`xq_comment`(pk comment_id) | 脚本 docstring L10-12 |
| 出口代理 | 动态出口池（快代理 API，TTL 240s）+ 静态池 + 直连降级；失败会回退直连 | `xq_proxy.py` L242-366、`config/xq_proxy_api.json`（存在，520 B） |
| token 链 | `.env.local` 的 `XQ_A_TOKEN` → 浏览器登录态（DPAPI）→ 匿名 `warm_session`；本次日志显示来源 env/browser | `xueqiu_common.py:105-163,550-554`、日志 L9 |
| 判定码 | 全失败 rc=1；hot 单表失败 rc=3（可见降级，2026-10-26 R25 判例）；部分失败仍 rc=0 | L700-720 |

### 1.3 首跑前基线（未跑前，勿倒因为果）

| 项 | 值 | 证据 |
|---|---|---|
| `xq_status` 总行数 / 最新入库时间 | 33,351 / **2026-11-01 18:13:47** | DB ro 查询（见 §8） |
| `xq_status` **今日（11-02）入库** | **0** | 同上 |
| `xq_hot_post` 总行数 / 今日 | 92 / **0** | 同上 |
| `health.ip_blocked_until` | `null`（无在效熔断） | `_manifest.json` health |
| `health.quotec_probe` | `ok=false`，`err="403 Forbidden. Seek IP Blacklisted."`，ts=2026-11-01T17:41:20 | 同上（**遗留记录**，见 §6 R2） |
| manifest 断点 `last_run` | xq_status 2026-11-01T18:13:51 / xq_hot_post 2026-11-01T18:13:56 | 同上 |
| 对应 K1 基线 | 研发计划记录「雪球 10-21 起 today=0；XueqiuCrawl 首跑未执行」 | 计划 L25/L38 |

---

## 2. 首跑前备份（步骤 2）

备份目录：`C:\Temp\xueqiu_first_run_20261102\`

| 文件 | 大小 | 用途/说明 |
|---|---|---|
| `_manifest.json.before-20261102_070454` | 5,389 B | **游标/断点文件**（`XqManifest` 全量） |
| `xueqiu_info.before-20261102_070454.db` | 30,355,456 B | DB 一致性快照（sqlite `backup` API 从 ro 连接复制，非文件直拷） |
| `before_state_20261102_070454.json` | 949 B | before 基线（行数/最大 id/最大 fetched_at/today=0/manifest sha256） |

- before manifest sha256 = `83b838983747eb7788caa4f2b980082e810dbe13f1d531924bf672745a5a78c1`
- before DB `PRAGMA quick_check` = `ok`

---

## 3. 执行（步骤 3，小批量）

**命令**（在 `quant_backtest\` 下，Python 3.11.9 = wrapper 同款解释器）：

```powershell
& 'C:\Program Files\Python311\python.exe' -X utf8 xueqiu_crawl.py --mode all --pages 1 *> C:\项目\work\research2\xueqiu_first_run_20261102_070503.log
```

| 项 | 值 |
|---|---|
| 开始/结束 | 2026-11-02 07:05:03 → 07:05:17（**14.5s**） |
| rc | **0**（成功；无 rc=3/1 降级或失败） |
| 批量口径 | `--mode all`（timeline+hot）+ `--pages 1`（默认 3 页 → 限 1 页）；无并发、无循环、无全量 |
| 日志 | `C:\项目\work\research2\xueqiu_first_run_20261102_070503.log`（772 B） |
| 请求量估算 | 代理拉取+验活 + 首页预热 + timeline 1 页 + hot 1 页 ≈ 4–5 请求（2–4s 间隔） |

**日志全文（出口 IP 已脱敏）**：

```
[proxy] 动态出口池已启用（快代理 API，TTL 240s）
[proxy] 动态出口就绪（cold）→ kdl-<masked>:12088
[XqCrawler] 出口代理: kdl-<masked>:12088
[XqCrawler] UA 粘性: 出口专属（kdl-<masked>:12088）
[xq_common] 预热缓存已过期，仍合并伴生 cookie（9 条）
[xq_common] WAF 伴生 cookie 已合并：9 条
[XqCrawler] cookie 就绪（来源: env/browser）
[XqCrawler] 出口连通验证 OK(200)
[crawl_all] {'xq_status': 10, 'xq_hot_post': 8, 'failed_tables': []}
```

---

## 4. 输出级核验（步骤 4）

### 4.1 DB 前后对照（`data/xueqiu/xueqiu_info.db`，ro 连接 + 与 before 快照差集）

| 指标 | before (07:04:54) | after (07:07) | 增量 | 判定 |
|---|---|---|---|---|
| `xq_status` 总行数 | 33,351 | **33,353** | **+2 净新增** | ✅ |
| `xq_status` `fetched_at like '2026-11-02%'` | 0 | **10** | **+10 入库触发行** | ✅ **today>0** |
| `xq_status` 最大 `fetched_at` | 2026-11-01 18:13:47 | **2026-11-02 07:05:11** | 前进 ~12.9h | ✅ |
| `xq_hot_post` 总行数 | 92 | **100** | **+8 净新增** | ✅ |
| `xq_hot_post` 今日入库 | 0 | **8** | +8 | ✅ |
| `xq_status_classified` 行数 | 33,189 | 33,189 | 0（派生任务未跑） | ⚠️ §4.6 |
| 主键重复（两表） | 0 | 0 | — | ✅ 幂等 upsert |
| `PRAGMA quick_check` | ok | **ok** | — | ✅ |

> 说明：**«入库触发行»(10) ≠ «净新增行»(2)**——采集器对 timeline 返回的 10 条逐条 upsert，其中 8 条为既有帖（仅刷新 `view_count/comment_count/...`/`fetched_at`）。报告与后续健康字段必须写清口径（见 §7.1）。

### 4.2 当日增量明细（`xq_status`，10 行）

| status_id | created_at（发帖） | category | title（截断） |
|---|---|---|---|
| 414982133 | 2026-11-01 20:17:19 | 102 沪深 | （空/无标题） |
| 414967907 | 2026-11-01 16:55:33 | 102 沪深 | 历史上国家队救市都买了什么？ |
| 414951448 | 2026-11-01 12:15:19 | 102 | （空） |
| 414937529 | 2026-11-01 08:28:53 | 102 | （空） |
| 414936906 | 2026-11-01 08:11:44 | 102 | 十月，+1.98万元。 |
| 414916279 | 2026-10-31 19:38:20 | 102 | （空） |
| 414883457 | 2026-10-31 11:26:28 | 102 | 感觉股市大部分人还是缺乏长远眼界 |
| 414759096 | 2026-10-30 16:01:33 | 102 | 上海等一线城市回暖，引来基金加仓地产 |
| 414493268 | 2026-10-29 16:13:48 | 102 | （空） |
| 414485352 | 2026-10-29 15:43:25 | 102 | （空） |

净新增 2 条 = `414982133`、`414967907`（其余 8 条为既有 status_id 刷新）。`xq_hot_post` 今日 8 条 `hot_rank=1..8`、`hot_fallback=0`（非兜底片），最新发帖 `created_at=2026-11-02 00:21:04`。

### 4.3 manifest（断点）after

| 字段 | before | after |
|---|---|---|
| `xq_status.done/status/rows` | true/ok/30（11-01） | **true/ok/10** |
| `xq_status.last_run` | 2026-11-01T18:13:51 | **2026-11-02T07:05:14** |
| `xq_hot_post.done/status/rows` | true/ok/10 | **true/ok/8** |
| `xq_hot_post.last_run` | 2026-11-01T18:13:56 | **2026-11-02T07:05:17** |
| `health.ip_blocked_until` | null | **null（无熔断）** |
| manifest sha256 / bytes | `83b83898…` / 5,389 | `a38bf776…` / 5,388 |

### 4.4 `data/xueqiu` 目录变更（近 60 分钟）

| 文件 | 大小 | mtime | 说明 |
|---|---|---|---|
| `xueqiu_info.db` | 30,363,648 B | 2026-11-02 07:05:17 | +8,192 B（写入增量） |
| `_manifest.json` | 5,388 B | 07:05:17 | 断点更新 |
| `xueqiu_info.db-shm` / `-wal` | 32,768 / 0 B | 07:05:39 / 07:05:30 | WAL 已回落 0（无未合并事务） |

> **「xueqiu 目录新增文件」口径**：timeline/hot 模式**只写 SQLite（+manifest）**，不产 parquet（parquet `xq_quote_snap/` 仅 `takeover` 行情接管模式产出）；本次属预期行为，**不是**缺失。目录条目仍 15 个、manifest 备份仍 5 个（本次保存未轮转备份）。

### 4.5 无异常/封禁迹象（核验项）

| 检查 | 结果 |
|---|---|
| 日志内 403/429/`ip_blocked`/`ALERT` | **0 次**（日志全文见 §3） |
| 出口连通验证 | `OK(200)` |
| `health.ip_blocked_until` | null（未触发 30min 熔断） |
| `quick_check` | ok；主键重复 0 |
| 失败表 `failed_tables` | `[]`（timeline/hot 均成功，无 rc=3 可见降级） |
| 出口 | 走快代理**动态出口**（未直连）——直连出口在 10-31~11-01 有 403 IP 黑名单记录（manifest `quotec_probe`），本环境直连不可信 |

### 4.6 派生层滞后（观察项，不属本次失败）

- `xq_status_classified` 33,189 vs `xq_status` 33,353 → **pending = 164**（其中今日新增 10 行未打标；另外 154 行为历史遗留）。
- 证据：`portal/api_dataops.py` overview `pipelines.classify = {total:33353, done:33189, pending:164}`；分类任务 `\XueqiuClassify` 下次 18:30、automation `xq_classify` 每小时 probe；今日 06:37 的 autoA 轮日志末行 `round end rc=0 labeled_this=500 ... derived_total=33156`（`automation_runs/xq_classify/xq_classify_status_autoA.log`）。
- 影响：结构化率 `0.9951`（overview summary）；日报/图谱当天用的是「未含今日 10 行」的派生层——**口径必须写「classified 截止上一轮」**，不得与 raw_total 当作同一时点。

---

## 5. 输出级健康实测：dataops overview 当前长什么样（步骤 5 前置证据）

只读调用 `portal/api_dataops.py::overview()`（未起服务、未写文件，2026-11-02 07:11 实测）：

```json
// summary
{"total_rows":835606, "ingested_today":10, "structured_rate":0.9951,
 "xq_total":33353, "xq_classified":33189, "active_collectors":10,
 "alerts":["XueqiuCrawl 18:00 生产首跑待执行（首跑后本平台出现当日增量）"]}

// collectors[] 里的 XueqiuCrawl（当前字段全集）
{"key":"XueqiuCrawl","name":"雪球日采集","carrier":"计划任务","schedule":"每日 18:00",
 "output":"xq_status 当日增量","health":"armed","at":"18:00","log_tail":[]}

// pools xq_status
{"name":"xq_status","note":"雪球帖存量","rows":33353,"today":10}
```

**三处缺口（均为 S1-03 范围，本报告不改代码）**：

1. **collector 条目没有 `today_incr/last_output_at`**：`XueqiuCrawl` 只有 `health="armed"`（任务在册即绿），今日增量 10 只出现在 `pools[].today` 与 `summary.ingested_today`，未归到采集器条目上。
2. **alerts 是静态待执行提示、首跑后不消警**：`api_dataops.py:616-618` 的判据是 `if xq_crawl.get("health") == "armed": alerts.append("…首跑待执行…")`；首跑成功后 health 仍是 armed（计划任务 Enabled），所以**警报照旧**——实测已复现。这与「armed 只是配置位，产出新鲜度才是真相」的 GubaCrawl 判例（同文件 L129-130）一致：必须改用 `today_incr` 判红绿。
3. **`xq_hot_post` 不在 POOLS/TODAY_COL**：`POOLS`（L45-61）只有 `xq_status`、`xq_status_classified`；`TODAY_COL`（L63）只有 `xq_status`、`stock_notice`。故 hot（当日新帖唯一来源，EXEC-R25 判例）无今日增量视图，存在「timeline 绿、hot 断供」盲区。

---

## 6. 风险与遗留（fail-visible）

| # | 风险/遗留 | 说明 | 影响 |
|---|---|---|---|
| R1 | **本次是人工首跑，不是 18:00 计划任务** | `\XueqiuCrawl` 仍 Ready、下次 2026/11/2 18:00；schtasks CSV 的 Last Run/Last Result 为空（未取证） | K1 要求「连续 3 天 >0」，需 11-02 18:00 / 11-03 / 11-04 连续观察 |
| R2 | 直连出口被雪球 403 黑名单 | manifest `health.quotec_probe.ok=false`（2026-11-01 17:41，"403 Forbidden. Seek IP Blacklisted."） | 若快代理 API 不可用→降级直连→timeline/hot 可能 403 熔断 30min（rc=1 → alert_fail） |
| R3 | token 链依赖浏览器登录态/匿名预热 | 本次日志「cookie 来源 env/browser」；`.env.local` 无 `XQ_A_TOKEN`（只有 KG/XMAP/XT 等键） | 浏览器登录态过期 + 匿名预热被 WAF → 302/400016 → 熔断（需 TokenRefresh 任务，下次 08:15） |
| R4 | 分类派生滞后 164 行 | §4.6 | 结构化层「今日」口径缺失；日报红卡需同时显示 `classify_pending` |
| R5 | 游标单点 = `_manifest.json` | 若并发/强杀可能丢断点（脚本内有 marker 语义与旧版本损坏判例） | 已在 §2 备份；18:00 正式跑前建议再备份一次（可脚本化） |
| R6 | `xq_hot_post` 今日 8 条均 `hot_fallback=0` | 非兜底片，数据可信 | — |
| R7 | `--pages 1` 只覆盖 1 页（约 10 帖） | 正式任务默认 3 页；本次是"小批量首跑"口径 | 正式增量仍以 18:00 任务为准 |

---

## 7. S1-03 输出级健康提案（only proposal，不改线上代码）

### 7.1 字段定义（端口径，必须原样落注释）

| 字段 | 定义（SQL/取值） | 语义 |
|---|---|---|
| `today_incr` | `SELECT COUNT(*) FROM xq_status WHERE fetched_at >= '<今天 00:00:00>'` | **今日入库触发行数**（upsert 命中行，含既有帖指标刷新）；不是净新增。今日实测量：10 |
| `today_new_rows`（可选，加严口径） | `COUNT(*) WHERE fetched_at>=today AND status_id 不在「当日首次运行前快照」` | **今日净新增**；本次实测 2。需采集器在 run 前记录 `max(status_id)` 或 id 集合，成本更高，建议二期 |
| `last_output_at` | `SELECT MAX(fetched_at) FROM xq_status` → `'YYYY-MM-DD HH:MM:SS'`（北京 naive 文本） | **最近一次输出落库时刻**；本次实测 `2026-11-02 07:05:11` |
| `lag_hours`（派生） | `now - last_output_at`（字符串→本地时间） | 供红黄绿阈值用 |
| `xq_hot_today_incr` | 同口径对 `xq_hot_post` | 防「timeline 绿、hot 断供」盲区（EXEC-R25 判例） |

**schema 契约**（`data/xueqiu/_manifest.json` `schema_docs`）：`created_at/fetched_at` 是文本 `YYYY-MM-DD HH:MM:SS`，**禁 `CAST(... AS INTEGER)` / `strftime('%s',...)` unixepoch**，区间过滤一律字符串比较（`>= '<today 00:00:00>'`）。

### 7.2 位置（按改动优先级）

| 优先级 | 位置（文件/函数/行） | 改法（提案） |
|---|---|---|
| **P0** | `portal/api_dataops.py` → `overview()` 的 collectors 循环（`L539-607`）；数据源复用 `_daily_intake()`（`L83-140`，已返回 `today`/`last_by_source`/`series`） | 在 `collectors[]` 的 `XueqiuCrawl` 条目增：`today_incr`、`last_output_at`、`lag_hours`、`level`（green/yellow/red）、以及 `xq_hot_today_incr`；`last_output_at` 建议新增轻量 helper `_last_output_at(db, table, col)`＝`SELECT MAX(col)`（勿改 `_daily_intake` 返回结构，避免影响既有消费方） |
| **P0** | `portal/api_dataops.py` → 告警段 `L608-618`（现 `if xq_crawl.health=="armed": "首跑待执行"`） | 改为按 `today_incr` 判：`local >= 19:00 且 today_incr==0` → `level=red` + alert「雪球当日增量=0（last_output_at=…）」；`0<today_incr` → 绿并清除「首跑待执行」旧提示；`today_incr==0 且 local<19:00` → 黄（窗口未到） |
| **P1** | `portal/api_dataops.py` → `summary`（`L619-623`） | 增 `xq_today_incr`、`xq_last_output_at`、`red_alerts`（数组，供 08:20 早报/日报红卡直接渲染，不必二次解析 collectors） |
| **P1** | `portal/api_dataops.py` → `POOLS`（`L45-61`）+ `TODAY_COL`（`L63`） | `POOLS` 雪球库增 `("xq_hot_post","雪球热帖")`；`TODAY_COL` 增 `"xq_hot_post": "fetched_at"`（一行改动即得 hot 今日增量） |
| **P2** | `portal/api_dataops.py` → `_xq_source_card()`（`L219-`） | 源卡增 `today_incr`/`last_output_at`（与 collector 条目同口径，避免两处口径漂移；可用同 helper） |
| **P2** | 日报/早报红卡（08:20 早报「数据仓健康」一行，计划 §八 `L124`） | **未找到固定生成器**（未验证）：候选任务 `DailyBrief0745(07:45)`/`MorningReport(07:00)`/`HermesBriefPush(08:30)`，模板未定位。建议该行**只读** `GET /api/dataops/overview` 的 `summary.xq_today_incr/xq_last_output_at/red_alerts`（单一数据源，禁止各自查 DB），红卡判据同 §7.3 |
| **P2** | `Portal` 测试：`portal/test_dataops_v3.py`（`L440-511` 已断言 `sources[xueqiu]` 卡） | 增断言：`overview.collectors[XueqiuCrawl]` 含两新字段；`today_incr==0 且已过窗口` 时 `level=="red"` 且 `alerts` 非空；`today_incr>0` 时「首跑待执行」不得再出现（本报告实测的回归点） |
| **P3** | `quant_backtest/start_xueqiu_crawl.ps1`（L10 固定 `--mode all`，无参数透传） | 增可选 `param([string]$Mode="all",[int]$Pages=3)` 并透传给 `xueqiu_crawl.py`；默认值不变（生产行为零变化），仅让"小批量首跑/故障限流"时不改脚本也能限页（本单因 wrapper 无此参数而直接调脚本，属 workaround） |

### 7.3 红黄绿判定（建议直接写进 `_collector_level()` helper，便于单测）

| 条件 | level | alert |
|---|---|---|
| `today_incr > 0` | **green** | 无 |
| `today_incr == 0` 且 `now(local) < 19:00`（18:00 任务 + 1h 缓冲） | **yellow** | 「当日窗口未到」 |
| `today_incr == 0` 且 `now >= 19:00` | **red** | 「雪球当日增量=0，last_output_at=…，疑似断流/封禁（熔断查 health.ip_blocked_until）」 |
| `last_output_at is None` 或 `lag_hours > 24` | **red**（覆盖上两行） | 「雪球输出停滞 >24h」 |
| `xq_hot_today_incr == 0` 且 timeline 有增量 | **yellow** | 「hot 当日新帖=0（当日新帖来源断供，EXEC-R25 判例）」 |
| 附加（与分类管线联动） | `classify_pending > 1000` → red；`> 200` → yellow；否则 green | 「分类追平 pending=N」 |

### 7.4 首跑结论对 S1-03 的直接输入

- 今日 18:00 正式任务跑完后，上述 P0 字段的预期值：`today_incr` = 18:00 那轮入库行数（≥今日 10 行，可能因再次 upsert 而增加），`last_output_at` = 18:00 轮结束时刻；`level` 应为 green；「首跑待执行」提示必须消失。
- 验收（计划 §三 K1）：「`xq_status` today>0 连续 3 天」→ 需 11-02（本次已达成人工轮）＋11-03＋11-04 的 18:00 自动轮证据；本报告只完成 11-02 人工首跑这一天。

---

## 8. 复现与证据清单

| 类型 | 路径/命令 |
|---|---|
| wrapper（只读通读） | `quant_backtest/start_xueqiu_crawl.ps1`（16 行） |
| 采集脚本 | `quant_backtest/xueqiu_crawl.py`（724 行）；`xueqiu_common.py`（限速/熔断/manifest） |
| 断点文件 | `quant_backtest/data/xueqiu/_manifest.json`（before sha256 `83b83898…`；after `a38bf776…`） |
| 备份 | `C:\Temp\xueqiu_first_run_20261102\`（manifest 5,389 B / DB 30,355,456 B / before_state JSON） |
| 运行日志 | `C:\项目\work\research2\xueqiu_first_run_20261102_070503.log`（772 B） |
| 执行命令 | `& 'C:\Program Files\Python311\python.exe' -X utf8 xueqiu_crawl.py --mode all --pages 1 *> <log>`（cwd=quant_backtest） |
| DB 查询（核验） | `sqlite3.connect('file:...xueqiu_info.db?mode=ro',uri=True)` → `select count(*) from xq_status where fetched_at like '2026-11-02%'` = 10；`select max(fetched_at) from xq_status` = `2026-11-02 07:05:11`；hot 8 |
| overview 实测 | `sys.path.insert(0,portal); import api_dataops; api_dataops.overview()`（只读，2026-11-02 07:11） |
| 计划任务 | `schtasks /query /fo CSV` → `\XueqiuCrawl` Ready / next `2026/11/2 18:00` |
| 研发计划依据 | `docs/plans/2026-11-02_数据仓研发计划_v3_执行版.md:38,56,57,116,124` |
| 分类日志 | `automation_runs/xq_classify/xq_classify_status_autoA.log`（末行 rc=0 labeled_this=500 derived_total=33156） |

**未验证/存疑（不要当结论引用）**：
1. `\XueqiuCrawl` 的 Last Run Time / Last Result：schtasks CSV 列为空，未取证 → 无法断言 18:00 正式任务历史状态。
2. 08:20 早报的固定生成器/模板：全仓未定位（候选 `DailyBrief0745`/`MorningReport`/`HermesBriefPush`），红卡落点需 DATA1/DEV1 定。
3. 快代理 API 余量与长期可用性：未读配置文件内容（避免凭据外泄），仅确认文件存在；本次出口可用。
4. `xq_status_classified` 的 154 行历史滞后原因（除今日 10 行外）未逐条归因。
5. 本次未验证 18:00 全量默认（`--pages 3`）在限速下的耗时/成功率。
