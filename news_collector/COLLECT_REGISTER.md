# 采集模块确权台账（COLLECT_REGISTER，2026-11-10 首版）

> 结论：**不封存**。审计"未随 11-01 收口封存需确权"的前提经核实失真——
> 本目录实爬模块是 11-01 之后经 **C9A-KOL-001（2026-11-08 老田批）** 及
> 同线「标的驱动采集」编号（P1/P2/P4，11-09）新授权的线，不是漏网存量。
> 本台账 = rights 审批 + 调度清单的落地形态；增删模块须先改本表。
> 证据细节见 `docs/采集情报_板块手册.md` §确权（2026-11-10 修订）。

## 一、确权事实（2026-11-10 核查）

| # | 模块 | 数据源 | 方式 | 批文/编号 | 新增日 | 调度 | 输出 |
|---|---|---|---|---|---|---|---|
| 1 | `news_collector.py` | 新浪/东财/华尔街见闻 快讯 | 免费公开接口 | 长期在役（news.db 核心） | 早于 10 月 | ✅ 计划任务 `NewsCollect`（15min） | `news.db` |
| 2 | `xq_stock_timeline.py` | 雪球个股 timeline | Playwright 活登录 profile + 页内 fetch + WAF 自愈 + 限速 2-4s | 标的驱动采集（P 线） | 2026-11-09 | ✅ 计划任务 `XqTimelineDaily --queue all --pages 1` | `data/xq_timeline/{code}.jsonl` |
| 3 | `xq_kol.py` | 雪球专栏 KOL 20 号 | Playwright headed persistent + 页内 fetch | **C9A-KOL-001，2026-11-08 老田批** | 2026-11-08 | ❌ 手动 | jsonl |
| 4 | `wechat_kol.py` | 微信公众号 KOL（wechat2rss 公共实例 feed 池） | requests 公共 RSS | **C9A-KOL-001，2026-11-08 老田批** | 2026-11-08 | ❌ 手动（`fetch_wechat_kol` 管线） | news 表 |
| 5 | `guba_pw.py` | 东财股吧个股页 | Playwright 渲染（requests 已被"身份核实"拦截页挡死） | 标的驱动采集 | 2026-11-09 | ❌ 手动 | jsonl |
| 6 | `taoguba_collector.py` | 淘股吧个股页 | requests（SSR 直出，无需 PW） | 标的驱动采集 P2 | 2026-11-09 | ❌ 手动 | jsonl |
| 7 | `sse_collector.py` | 上证 e 互动 sns.sseinfo.com | requests（解析自研） | 标的驱动采集 P4 | 2026-11-09 | ❌ 手动 | `data/sse/{code6}.jsonl` |
| 8 | `irm_collector.py` | 深交所互动易 | requests（零风控官方接口） | 标的驱动采集 P1 | 2026-11-09 | ❌ 手动（`irm_backfill_answers.py` 配套） | jsonl |
| 9 | `quote_snapshot.py` | 行情快照 | 公开接口 | 长期在役 | 早于 10 月 | ✅ 计划任务 `QuoteSnapshot` | 快照 |
| 10 | `tmp_guba_probe.py` / `tmp_irm_capture.py` | 探针 | 临时 | — | 11-09 | ❌ | tmp 日志 |
| 11 | `run_kstock_intel.py` + `kstock_watchlist.json` | k_stock 持仓/近期出票标的（17只：A股8/港股3/美股6） | 标的驱动采集（irm 深交所互动易 / sse 上证 e 互动 / taoguba 淘股吧，均 requests 无头） | 老田 2026-11-10 指令（策略中台→OSINT1 桥） | 2026-11-10 | ✅ WorkBuddy 自动化 `KStockIntel 每日标的驱动采集`(07:30 ACTIVE) + 待管理员注册 Windows 任务 `KStockIntelDaily` | `data/{irm,sse,taoguba}/{code6}.jsonl` |
| 12 | `run_houfeng_intel.py` + `houfeng_watchlist.json` + `target_queue_houfeng.json` + `houfeng_cand164_replay.py` + `houfeng_candidates_164.json` | 侯风(HOUFENG) 选股策略产出（portal/houfeng/houfeng_pick_*.json 的 pool_off.picks，30只：SZ13/SH16/BJ1）**+ 原始候选池全量 164 只**（SZ70/SH93/BJ1，11-06 信号日冻结快照） | 标的驱动采集（irm 深交所.SZ / sse 上证.SH / taoguba 全A股含.BJ，均 requests 无头，分批≤40）；三源采集范围 = 选股∪候选池；候选池由 `_pick_20261106_report.py` 冻结参数**只读复现 + 双金标准校验**（候选数164、top30逐只逐序一致）产出，非策略研判；xq 经 `target_queue_houfeng.json` 并入 `xq_stock_timeline.load_queue` 并集（纯增量，不削减缠论465覆盖） | 老田 2026-11-10 指令（侯风平台→OSINT1 桥"把数据补齐"）+ 同日裁定"164只原始候选池纳入补齐" | 2026-11-10 | ✅ WorkBuddy 自动化 `HoufengIntel 每日标的驱动采集`(07:40 ACTIVE, id `1e336f25…`)；串 07:00 xq → 07:30 k_stock → 07:40 侯风 | `data/{irm,sse,taoguba}/{code6}.jsonl` + xq 并入 `target_queue_chanlun.json` 全队列 |
| 13 | `run_cohorts_intel.py` + `cohorts_watchlist.json` + `target_queue_cohorts.json` | 两池出票（portal/daily_report/cohorts/cohort_*_{main,auto}.json 最新 asof 批次：main 主选·三条全中 + auto S12 ZKG_AUTO_C20 生产影子池，逐日滚动，11-09 批 59 只） | 标的驱动采集（irm 深交所.SZ / sse 上证.SH / taoguba 全A股含.BJ，均 requests 无头，分批≤40）；xq 仅登记 target_queue_cohorts.json（开关 xq_include_cohorts 默认 false，未并入 07:00 有头管线） | 老田 2026-11-10 指令（"检查各个策略出票…方便组织数据获取"→两池桥） | 2026-11-10 | ✅ WorkBuddy 自动化 `CohortsIntel 每日两池标的驱动采集`(07:50 ACTIVE, id `d98f0056…`)；串 07:00 xq → 07:30 k_stock → 07:40 侯风 → 07:50 两池 | `data/{irm,sse,taoguba}/{code6}.jsonl` |
| 14 | `run_target_queue_intel.py` + `target_queue_chanlun.json` | 缠论 actionable 队列（65 只：SZ29/SH28/BJ8）**四源全跑**——irm(SZ)/sse(SH)/taoguba(全量 --pages 1)/**guba_pw.py 东财股吧（首座自动化股吧桥）** | 标的驱动采集（requests 无头 ×3 + Playwright 渲染 ×1，分批≤50/40/30）；股吧「身份核实」拦截为 IP 级间歇风控（11-11 实测：有头真实 Chromium 同样复现，数分钟后自愈），no_marker 优雅跳过不写库、不硬闯不换出口，全败连续 3 天日志 ALERT 供人工介入 | 老田 2026-11-11 指令（解决"四源手动、覆盖按需触发"缺口） | 2026-11-11 | ✅ WorkBuddy 自动化 `TargetQueueIntel 每日标的驱动采集`(09:30 ACTIVE, id `ecb410ca…`)；错开晨链（06:00-08:30），串 07:00 xq → 07:30 k_stock → 07:40 侯风 → 07:50 两池 → 09:30 缠论四源 | `data/{irm,sse,taoguba}/{code6}.jsonl` + `quant_backtest/data/guba/{code6}.jsonl` |

## 二、审计前提订正（对 2026-11-10 整合版 §1.2 的反核）

1. **"11-08/09 仍在提交"失真**：7 个实爬模块（#2-#8）git 状态全部为**未跟踪（??）**，从未提交过；
   11-08/09 的 commit 是管线/观测层（传播关系判定 30b115c16、L1 观测桥 fdb9476d6），不是爬虫本身。
2. **"未随 11-01 收口封存"失焦**：未检索到 11-01 收口令原文；且 #3/#4 明确带 11-08 批文，
   #2/#5-#8 属同批授权的标的驱动采集——性质是**新授权线**，不是收口漏网。
3. **真实缺口是治理而非封存**：批文只活在 docstring、模块未入 git、无台账（本表即补）。

## 三、待办（治理闭环）

- [x] **R1 模块入库**：#2-#8 连同批文号（C9A-KOL-001）+ #11 桥，已 `git add` + 提交（commit `a6d383bd9`，11 文件 / 1788 行，pre-commit 凭据扫描 0 命中；老田 2026-11-10 批）。推送 GitHub 受本环境网络限制（`github.com:443` 代理不可达），待另行批准走自建 git 远端（触 10.205.0.118，需单独点头）。
- [x] **R2 台账落地**：本文件（2026-11-10 建）
- [x] **R3 路径修复（2026-11-10 完成）**：`xq_kol.py:44` 写死 `C:\aios\quant_backtest` → `env AIOS_ROOT` 优先 + 原默认值兜底（对齐 `tools/intel_source_health.py:72` 同款约定）；验证脚本码点级断言默认解析值与修复前逐字符一致、env 覆盖生效、生产 profile 目录不受影响。验证过程撞既有判例：bash `-c` 通道把断言字面量 `\a` 串味成 `/a`，改落盘脚本 + `chr(92)` 构造通过。
- [x] **R4 文档订正**：整合版 §1.2/§2.2 + `docs/采集情报_板块手册.md` 同步改判（2026-11-10 已同步）
- [ ] **红线维持**：采集频率/源/反爬不进渲染链路；#3/#4 增删号只改 `news_config.json`，不动代码
- [ ] **R5 Windows 计划任务**：`KStockIntelDaily`(每日07:30) 注册脚本已备（`register_kstock_intel_task.ps1`）；当前非管理员会话注册被拒（"拒绝访问"），待管理员以管理员 PowerShell 执行；WorkBuddy 自动化已作兜底(07:30 ACTIVE，id `a4c50019…`)
- [x] **R6 侯风桥落地**：#12 桥完成（run_houfeng_intel.py + houfeng_watchlist.json + target_queue_houfeng.json）；首跑 2026-11-10 09:00 补齐 irm+162/sse+22/taoguba+373 条（失败0，合法空：10只SH无e互动、920599.BJ无淘股吧帖）；xq 并集合并验证 489=465+24 唯一代码全覆盖；WorkBuddy 自动化 `HoufengIntel`(07:40 ACTIVE, id `1e336f25…`)。已 git 入库（commit `347fb9570`），并经老田批准走自建 git 新仓 `aios.git` 推送（本地 remote `selfgit`）。
- [x] **R7 164 候选池补齐（2026-11-10）**：老田裁定"纳入补齐"。pick 文件只留计数、明细未落盘 → `houfeng_cand164_replay.py` 按 `_pick_20261106_report.py` 冻结参数只读复现 `M_off["BUY"][t_sig]`，双金标准校验（候选数=164、top30 逐只逐序一致）全 PASS → `houfeng_candidates_164.json`（SZ70/SH93/BJ1，含 30 只、净增 134）。runner 采集范围改为选股∪候选池（分批≤40），首跑 2026-11-10 10:42，覆盖增量以 `logs/houfeng_intel_full164.log` 的 BEFORE/AFTER 为准；合法空口径同 R6。**候选池为 20261106 冻结快照、非每日重算**；如需刷新须上级裁定后重跑复现脚本。
- [x] **R8 xq 雪球队列扩容（R22 已执行，2026-11-11）**：`target_queue_houfeng.json` 已登记 `candidate_pool`(164) + 开关 `xq_include_candidates`(默认 false→**true**)。开关 false → 07:00 `--queue all` = 489；置 true → 593（净增 104 只有头采集）。R22 已将 `selection_universe.json` 置 `houfeng_candidates164.xq_include=true` 同步生效，并入 07:00 有头管线。
- [x] **R9 服务端孤儿 pack 清理（R22 C 已清，2026-11-11）**：`10.205.0.118:/home/git/repos/aios.git/objects/pack/tmp_pack_i87Acd`（146M）经五要素批准删除，仓库 978M→832M，`git fsck --full` 零错误、无 tmp_pack 残留、HEAD 可达。
- [x] **R10 两池 xq 雪球覆盖（R22 已执行，2026-11-11）**：两池逐日滚动换仓，`target_queue_cohorts.json` 置 `xq_include_cohorts=true` 同步，并经 B 注册表重构 `xq_stock_timeline.load_queue` 并集合并，并入 07:00 有头管线（59 只）。负载 +33% 属运营裁定（已批）。
- [x] **R11 各策略出票统一汇总**：`strategy_picks_summary.py`（只读盘点 6 线：k_stock/侯风/两池/缠论/选股日更/策略库 S01-S12，含各线 OSINT 覆盖与缺口清单）→ `strategy_picks_summary.json`。k_stock 线 18 行持仓（ai_team 10 含港股3 / double_knife 2 / qingping_us 美股6）；缺口主线 = 两池 54/59 无覆盖（已由 #13 桥补齐）；selection_daily picks=0（S02 熔断暂停，无出票）。
- [x] **R12 OSINT 方法论落地（2026-11-10，老田批「按计划实施」）**：依据 `docs/OSINT方法论结合方案_2026-11-10.md`（范本=丁爸《美国陆军未来关键发展趋势深度研究》）。L1+L4：手册 §六 增补四组判据（三层信源结构/事实状态词法标注/传闻-澄清对账/输出纪律）；L3：**派生标注层** `fact_type_annotate.py`（只读四源 → `data/fact_type/{src}/{code6}.jsonl`，50815 行：tier1 1282 / tier3 49533，plan 3292/fact 2983/mixed 1484）——**不改采集器落盘 schema**（契约变更方案经评估不采用，留痕于手册 §6.5）；L2：对账底表 `rumor_verification.py`（→ `data/rumor_check/reconciliation_*.json`，632 code：两端齐备 102/仅官方 1/仅传闻 529，**核验结论栏一律留空待分析岗**）。两工具均只读源、零契约风险、可整体重跑；红线维持：不进渲染链路，标注≠事实裁定。
- [x] **R13 OSINT 收集方法落地 L1+L2（2026-11-11，老田批「执行L1+L2」）**：依据 `docs/OSINT收集方法学习_2026-11-11.md`（C1-C6：情报周期/PIR/NATO Admiralty Code/三角验证/SOCMINT）。**L1 零改动（手册 §6.6-6.9 即生效）**：独立性判据（转引≠独立源、结构性独立）、激励字段定义（六源倾向性基线）、回音室须知（单一伪影不支撑、样本偏差声明）、评级基线表（NATO Admiralty A-F×1-6 二维独立 + 六源可靠性基线）。**L2 小改 `rumor_verification.py`**：①核验栏加 `admiralty_code` 占位（NATO 格式如 `B2`，本席保持 null）；②code 级发帖量统计 `post_volume` + `rumor_by_source`/`official_by_source`（按源分列）；③官方端记录补 `src`（irm/sse 分离）。验收：全量重跑 summary 六项与基线完全一致（632/102/1/529/1282/37735），632 code 新字段断言 bad_count=0。**L3 待批（未执行）**：PIR 登记机制、xq 补 link 字段（溯源链断实证缺口 C6）、六源 track record 档案——均归上级裁定，本席不擅动。
- [x] **R14 OSINT 收集方法 L3 落地（2026-11-11，老田批「执行L3」）**：①**PIR 登记机制**：`docs/PIR_register.md`（PIR编号/需求/标的/源/指示器/覆盖状态/责任人，需求内容归上级填，本席只维护覆盖状态）；手册 §6.10。②**xq 补 link（C6 溯源缺口闭环）**：`xq_stock_timeline.py` API_JS 提取 permalink `https://xueqiu.com/{user_id}/{id}` + rec 落盘加 `link` 字段；存量 `xq_backfill_link.py` 幂等回填 **22768 行**（复跑 updated=0 确认幂等）；对账底表 rumor 端引用经 `rec.get("link")` 自动带溯源（C6 缺口从采集端到对账端全闭环）。③**六源 track record 档案**：`docs/source_track_record.md`（六源基线+校准口径，初期已验证列全 0 待分析岗回填 `admiralty_code`，本席不编造数字）；手册 §6.11。三项均只读/加成式 schema 变更（下游不消费 link 不受影响），红线维持：不替分析岗裁事实、不擅自扩源。
- [x] **R15 核验流转草案（2026-11-11，「下一步」自动推进 R12 治理闭环最后一公里）**：新增 `docs/核验流转草案.md`——定义 `rumor_verification.py` 产出的 `verification` 栏字段语义（status/by/note/admiralty_code）+ 填写主体建议（分析岗执笔、马超复核，归上级裁定）+ 流转流程 + 触发频率建议 + **已识别风险 R15-1（全量重跑新建带时间戳文件、已填 verification 不延续，建议改状态合并持久化，待裁定）**。边界重申：本席只生成底表与维护字段、不填任何结论。手册 §6.3 引用草案。
- [x] **R16 策略中台选股池读取 + 覆盖对照（2026-11-11，老田「去读取选股池」）**：直读 `k_stock/data/paper.db` 三表——**持仓池 17 只 / 信号池（orders BUY distinct）426 只 / 决策池 4 次**。关键发现：**信号池 426 只 OSINT 仅覆盖 129（30.3%）**，未覆盖 297（A股274 真实缺口 + 北交所13/港股3/美股7 源边界）。订正此前"k_stock A股100%对齐"= 持仓口径（17只），非选股池口径；根因 = `strategy_picks_summary.py` 与 `run_kstock_intel.py` 只桥接持仓+近14日BUY，未纳入 signal universe。落盘 `docs/策略中台选股池读取与OSINT覆盖对照_2026-11-11.md` + 数据 `data/strategy_pool_coverage.json`（逐只覆盖状态）。队列扩容（把 426 信号池纳入采集）属裁定项，归老田/马超，本席不动。
- [x] **R17 k_stock 选股池纳入 OSINT 采集队列（2026-11-11，老田裁定「有效信号 pending+filled 口径」）**：老田裁定"274 A股缺口是真实采集盲区、非源边界"，并选定**有效信号池口径**——`run_kstock_intel.py` 选股来源由 `positions ∪ orders(近14日 pending/filled)` 改为 `positions ∪ orders(side='BUY' AND status IN('pending','filled'))`，**排除 cancelled/rejected 已撤单/拒单**（全量 426 中 364=cancelled 占 85%、盲区 287 中 266=cancelled 占 93%，属策略已放弃的票，不拉入队列避免噪声放大）；删除失效的 `RECENT_DAYS` 常量，输出 JSON 字段 `recent_buy_valid→signal_universe` / 增 `scope` 等。验证：COMPILE_OK；新口径 **有效信号池 106 只（A股+BJ 97 + 港股3 + 美股6）**，A股采集由 8 → **97**，新增未覆盖盲区 39 只。07:30 自动化（KStockIntel）将按新口径运行；本次已手动触发一次补齐。代码 commit `4c6f687c7` + 台账；数据 commit `20943bf92`（irm+244/sse+92/taoguba+717 共 1053 条，watchlist 17→106，已推 selfgit master+main）。提交时以日志精确标的清单做交集筛选，排除他线并发独占文件（000719/002086/300133/300747 等），无误带。
- [x] **R18 持久累积入库机制（2026-11-11 08:08，老田「入库即每日自动查、无需按钮」）**：改造 `run_kstock_intel.py`——watchlist 由"每轮重算覆盖"改为"**持久累积并集**"：种子=`positions ∪ orders(BUY 全状态,含 cancelled)`，并与上一轮 `kstock_watchlist.json.watchlist` 做并集，已入库代码**永不掉出队列**（满足"入库有了的，未来就是每天增量自动查，无按钮"）。新增 `_load_prev_inventory()` + `chunked()` 分批调用（防个股数扩至数百后单进程超时）；timeout 维持 900s。验证：COMPILE_OK；首跑 `order_universe=426 / prev=106 / new_entered=320 / allset=426`，A股采集 97→**416**。**覆盖口径订正**：按新指令将 cancelled 235 只纳入订单宇宙，覆盖 R17"有意排除 235"口径——**274 真实盲区一次性闭环**。回退有效信号口径只需改 `build_watchlist` 种子 SQL 一行。采集器/落盘路径/契约零变更。代码+台账待提交；首跑全量 426 采集在后台执行。
- [x] **R19 蒋琬 S12 多因子共识篮子 OSINT 补齐（2026-11-11 09:53，老田「读取蒋琬因子挖掘的多因子选股，把里面的股票数据补齐」）**：定位 S12_ZKG_AUTO_C20 自动共识篮子（`research/shadow/s12/basket_20261106.json`，执行=FACTOR1 蒋琬、主管张松、复核司马微、老田批准；含 20 只 .SH，n=20/hold=40，asof 2026-11-06 影子基准）。补齐前覆盖率 6/20（13 缺口中 14 真实盲区——仅 3 只在 k_stock 426 宇宙内，S12 与 k_stock 是两套选股源）；对 20 只全量跑 sse(上证e互动)+taoguba(淘股吧 --pages 1)，幂等增量。结果：sse 新增 57 条、taoguba 新增 152 条（20 标的全 0 失败），覆盖 **20/20 缺口=0**。数据 commit `37dfbc15e`（30 文件 / sse 13 + taoguba 17，精准 staging 排除 163 他线并发文件），已推 selfgit master+main（0 0）。治理豁免 `003a56b43`（sse 600590 平台脱敏用户ID被 PHONE_CN 误报，同 603697/605136 性质）。**系统性治理待裁**：sse 互动易 `id` 字段 `sse:<code>:<digits>****<digits>` 是交易所公开抓取数据里的平台脱敏用户标识，会被 PHONE_CN 反复误报——建议要么扫描器加 `data/sse/*.jsonl:*:PHONE_CN` 目录级豁免、要么 `sse_collector.py` 归一化 `id` 去掉用户号段（属采集器 schema 变更，归老田裁定）。
- [x] **R20 四组统一总榜40只 OSINT 缺口补齐（2026-11-11 11:00，老田「看看数据采集了没有 → 补齐存挡」）**：老田贴"四组名单去重40只·统一口径总榜(2026-11-11盘前)"（v3 六因子模型统一分，40 只 A股/北交所）。覆盖核查：补齐前主源(irm/sse/taoguba)覆盖 24/40、雪球 xq 仅 4/40；16 只主源全缺且基本不在 k_stock/两池/S12 任何队列（仅 688213 在两池队列但从未跑过），属新名单盲区。补齐方式：对 16 只按市场分组跑 irm(SZ7)+sse(SH7)+taoguba(全16) 幂等增量（参照 R19 纪律：精准 staging、只交本批文件、排除他线并发改动）；雪球 36 缺口仍属 07:00 有头管线扩容裁定项(与 R10 同类)，不在本次。结果：主源覆盖 **24→37/40**，3 只缺口经诊断确认为合法空/源边界（688213.SH 科创板无帖合法空、920663.BJ/920504.BJ 北交所淘股吧源边界，均 rc=0 失败 0 非遗漏）。数据 commit `2d6050eb9`（20 文件：irm 6 + sse 1 + taoguba 13 / 244 增，精准 staging 排除他线并发文件），已推 selfgit master+main（0 0）。
- [x] **R21 缠论队列四源桥 + 股吧自动化 + 两项订正（2026-11-11 11:45，老田「解决 jev_impact 空产物 + 四源手动调度」）**：
  - **订正① jev_impact.json「0KB」是显示口径错误非故障**：`getsize//1024` 整除把 973 字节显示成 0KB。实测文件健康（07:28:21 由 `\ZeroBound\SentimentBT` 产出，LastTaskResult=0，n=24972，三层 CAR 齐全，applied={medium:True,high:True}）。教训：小文件报字节数，不用 KB 整除。
  - **订正②「四源手动」复核为「一股吧缺桥 + 三源已有桥」**：taoguba/irm/sse 已由 #11/#12/#13 三桥自动化且当日有产出；真实缺口 = 缠论 actionable 队列无桥 + **股吧 guba_pw 不在任何桥**（最后数据停在 11-10 17:50）。08:48 kstock 日志 taoguba 批次 `rc=3221226091` 秒挂为沙箱代理环境残留（`http_proxy=127.0.0.1:<port>` 被子进程继承）——`run_kstock_intel.py` `run_collector` 已补 `_clean_env()` 代理剔除（判例 #26 直连，生产计划任务无代理变量时幂等）。
  - **股吧风控判据**：「身份核实」拦截页为 IP 级间歇风控，有头真实 Chromium 同样复现（11:18 探针两连拦），11:30 集中采集自愈放行——不硬闯、不换出口（旧 403 逻辑禁复用），no_marker 优雅跳过，全败连续 3 天 ALERT。
  - **落地**：新增 `run_target_queue_intel.py`（#14 桥：65 只四源全跑，proxy 清理、分批、日志 `logs/target_queue_intel_daily.log`）；WorkBuddy 自动化 `TargetQueueIntel`(09:30 ACTIVE, id `ecb410ca…`)。首跑实测（11:24-11:41，17m46s）：irm +18 / sse +83 / taoguba +54（65 只失败 0）/ **guba ok=62/65 新增 +2287**（916+1192+179）。代码+台账 commit 见 git log；数据文件归属各线并发纪律，本次不代提交数据。

- [x] **R21 治理工具改造（2026-11-11 11:43，老田「按这个计划执行 A1①+A2」）**：①**A1① 扫描器目录级豁免**——`tools/scan_secrets.py` 的 `load_baseline()` 改为返回 `(exact_keys, glob_patterns)` 元组，新增 `_baseline_match()` 支持 `fnmatch` 目录级/通配豁免（`path:line:rule` 中 path 含 `*` 视为 glob、line/rule 用 `*` 表任意）；`scan_secrets_allowlist.tsv` 移除逐文件 sse 条目（603697/605136/600590），统一加 `./data/sse/*.jsonl:*:PHONE_CN` 目录级豁免。根因：sse 互动易 `id` 字段 `sse:<code>:<digits>****<digits>` 平台脱敏用户标识被 PHONE_CN 规则反复误报。②**A2 R15-1 verification 持久化**——`news_collector/rumor_verification.py` 新增 `_load_prev_verification()`：重跑时读上一轮 `reconciliation_*.json` 最新文件，按 code 合并已填 `verification`（status/by/note/admiralty_code），只沿用人工已填结论（status 非 pending 或 by/note/admiralty_code 非空），全 pending 不沿用；summary 加 `verification_carried_from_prev` 计数。本席只做数据层合并，不碰分析岗研判内容。验证：scan 单文件测试 `未豁免=0 基线豁免=1 SCAN_EXIT=0`（放行）；rumor 单码跑通 + 临时脚本无污染验证合并逻辑（已填沿用/未填不沿用/全pending不沿用全 PASS）。代码+治理 commit `5f9c9733f`（3 文件 +76/-12，pre-commit 凭据扫描 0 命中通过），已推 selfgit master+main(0 0)。A1②(sse id 归一化) 排期未做；R8/R9/R10 仍 pending 待裁定。

- [x] **R22 A1② sse id 归一化 + B 注册表架构 + R8/R10 雪球扩源（2026-11-11 %s，老田「执行 A1② / B / C / R8 / R10」）**：
  - **A1② sse id 归一化（根治每日重抓重复）**：`sse_collector.py` 旧 id 用 Python 内置 `hash()`（按进程加盐、跨进程不稳定）→ 同帖每次重抓 id 不同、按 id 去重失效，每日重跑把全部历史帖重复追加（实测 `600018.jsonl` 32 行仅 8 唯一）。改为 `_stable_sse_id()`：`hashlib.sha256` 内容指纹（前 16 位十六进制），**确定性 + 跨进程稳定 + 十六进制绝不可能是手机号形态（PHONE_CN 误报根治）**；采集端 `main()` 再加 `(ts,content)` 内容键兜底去重，历史文件未重写也能防重抓重复。一次性迁移 `data/sse/*.jsonl` 全量归一化（184 文件 / 171 tracked 变更 / 幂等）：**1774 行 → 672 唯一，去重 1102 行（62%% 膨胀）**。
  - **B 注册表架构 `selection_universe.json`**：声明式注册 6 选股源，各带 `xq_include` 开关——`chanlun`(基线465,恒并) / `houfeng_picks`(30,恒并) / `houfeng_candidates164`(164, R8) / `cohorts`(59, R10) / `s12`(蒋琬20 .SH, 关) / `unified40`(四组总榜40, 关)。`xq_stock_timeline.load_queue` 重构为**只读注册表并集**，移除 houfeng/cohorts 硬编码特判；源文件相对路径 + `../` 回退 aios 根（兼容 `research/`）。`unified40` 当前仅含 R20 确认 16 只盲区种子（13 已补 + 688213/920663/920504 合法空/源边界），全 40 只 v3 总榜待老田补登。
  - **R8 侯风164 雪球扩容（已批）**：`selection_universe.json` 置 `houfeng_candidates164.xq_include=true` + `target_queue_houfeng.json.xq_include_candidates=true` 同步。
  - **R10 两池 雪球扩容（已批）**：`selection_universe.json` 置 `cohorts.xq_include=true` + `target_queue_cohorts.json.xq_include_cohorts=true` 同步；`load_queue` 经 B 重构已并集合并。
  - **07:00 有头管线总并集 = 647**（原基线 489，净增 158 = ~104 侯风候选 + 59 两池 − ~5 交集）；`run_xq_daily.py` 用 `--queue all` 故即时生效。有头管线负载 +33%% 属运营裁定（已批）。
  - **C R9 孤儿 pack**：`10.205.0.118:/home/git/repos/aios.git/objects/pack/tmp_pack_i87Acd`(146M) 清理，五要素批准（见 C 执行记录）。

## 四、遗忘事件自检（2026-11-11 22:41 马良 · 应老田「检查是否有遗忘事件」）

### 已核实无遗忘（自纠）
- **B 注册表并集 = 647 实测确认**：`xq_stock_timeline.load_queue('all')` 实跑 = 647；按源拆分 chanlun=465 / houfeng_picks=30 / houfeng_candidates164=164 / cohorts=59 / s12=20 / unified40=16。注册表注释「缠论基线465」正确（load_queue 读 scope=all 得 465，此前疑 489 为误判）。
- **xq 07:00 有头管线确实在跑**：`data/xq_timeline/`(真实路径 `news_collector/data/xq_timeline`) 448 文件，最新 mtime 11-11 07:07；`run_xq_daily.py` 用 `--queue all --pages 1` 读注册表并集 → 扩源非死代码。

### 🔴 实质风险（归上级裁定，本席不擅动）
- **R22 扩源首次生效窗口 = 明日 07:00（11-12）**：commit `e72e68b0d` 时间 13:31 **晚于** 今日 07:07 那次 run；日志铁证 `109/489…130/489`，今日仍跑旧 489 并集。R8/R10 扩源（647）**尚未在任何实际 run 生效**，首次机会 = 11-12 07:00。
- **WAF 挑战导致 xq 每日覆盖不完整**：今日日志 `003021 p1 防御触发(WAF_CHALLENGE)——停轮防拉黑`，run 在 ~130/489 处 **rc=2 中途 halted**。扩源到 647 后，若 WAF 在 ~130 就停轮，排在并集后部的侯风164+两池59 大概率永远跑不到 → **扩源可能形同虚设**。建议上级裁定：WAF 重试/分批/错峰策略，或接受「有头管线每日只能覆盖前段队列」的既定约束。

### 🟡 台账卫生（本次已补）
- §三 R4/R8/R9/R10 原标 `[ ] 待裁定/待批`，实际 R22 已执行 → 已勾 `[x]` 并补注 R22 落地（R9 孤儿 pack 亦已清）。

### ⚪ 仍待老田输入/裁定（非遗忘，跨会话挂起）
- **unified40 全 40 只 v3 总榜**待老田粘贴原文补全 → 方可评估 `xq_include` 扩源（现 false 关闭）。
- **remote `main` 独立分叉**（main=8ef3f346 ≠ 我祖先），本次仅推 master；是否补推 main 待老田裁定（非快进，会改写 main 历史）。
- **R5 Windows 任务 `KStockIntelDaily`** 注册脚本已备(`register_kstock_intel_task.ps1`)但非管理员会话被拒；WorkBuddy 自动化 `KStockIntel`(a4c50019, 07:30 ACTIVE) 已兜底，公开缺口非紧急。

## 五、R24 WAF 优雅降级 + 扩源独立分片（2026-11-11 22:57 老田裁定执行）

**背景（R23 遗忘事件自检发现的真问题）**：原 `xq_stock_timeline.py` 主循环对单次 WAF_CHALLENGE 采取**全局停轮**（`halted` 标志 → 整轮 break，rc=2）。扩源（R8/R10 把 houfeng_candidates164+cohorts 223 只排在队列尾部）后，单轮时间变长、更可能中途撞 WAF，导致**队尾扩源代码永远跑不到**——扩源形同虚设。今日 07:07 run 实测 `003021 防御触发——停轮`、rc=2、仅 ~130/489。

**裁定参数（老田 22:57 批准）**：`WAF_COOLDOWN=300s` / `WAF_RETRY=3` / `SUSTAINED_ABORT_PCT=0.20`；扩源分片调度 **21:00 错峰**（避开晨链 07:00-09:30）。

**落地改动（commit 见 git log，已推 selfgit master）**：
1. **`xq_stock_timeline.py` 常量区**：新增 `WAF_COOLDOWN/WAF_RETRY/SUSTAINED_ABORT_PCT/MAX_DEFERRED_ATTEMPTS/DEFERRED_F` 五项。
2. **`load_queue` 加 `sources=` 参数**：分片模式只并集指定源 key（绕过 xq_include），日常 `--queue all` 行为不变。
3. **`main()` 重写为优雅降级 + 跨日自愈**：
   - 撞 WAF → 冷却 `WAF_COOLDOWN` 后重试同码同页 `WAF_RETRY` 次；重试成功续跑，耗尽则本码 `defer`（记 `_deferred.json`）**续跑下一码，不整轮停**。
   - 仅当 `waf_hits/已处理 > SUSTAINED_ABORT_PCT(20%)` 才置 `halted=SUSTAINED_WAF_BLOCK` 整轮停（真·持续封锁才防拉黑）。
   - 每轮结束写 `_deferred.json`；次轮启动把 deferred 码**前置重试**（单码最多 `MAX_DEFERRED_ATTEMPTS=5` 次后放弃，防冷却死循环）。
   - 日志新增 `deferred=N` 计数；统一经 `_append_items` 去重落盘。
4. **新增 `run_xq_daily_expanded.py`**：与 `run_xq_daily.py` 同构，调 `--sources houfeng_candidates164,cohorts --pages 1`，日志 `logs/xq_timeline_expanded.log`。
5. **注册 WorkBuddy 自动化 `XqExpandedShard 扩源分片每日补齐`**（id `7ec654ea…`，DAILY 21:00 ACTIVE）：独立跑扩源 222 只（164+59 去重 −1 交集），与 07:00 基线 run 解耦，保证扩源在 WAF 早停时仍生效。

**验证**：`py_compile` 通过；`load_queue('all')=647` 不变、`load_queue(sources=[houfeng_candidates164,cohorts])=222` 且 ⊆ 全集。首次扩源实际生效 = 11-12 07:00 基线 run（Layer1 让基线跑通）+ 11-12 21:00 分片 run（Layer2 保底）。

**边界重申**：WAF 风控姿态变更（"1 次抖动不整轮停"）属运营裁定，已按老田阈值落地；本席只采集落盘，不研判、不擅动扩源决策。

## 六、技术观察台账（外部工具/方案情报，2026-11-12 马良·应老田送达）

> 性质：老田贴送的第三方产品宣传材料（ego(lite) by Citro Labs），非内部需求。本席按 OSINT 治理纪律做证据分级 + 交叉核验 + 映射评估，**不擅自改动任何生产设施**。处置裁定：记入技术观察（不采纳、不试点、不替换现有管线）。

### T1 · ego(lite) 浏览器（Citro Labs，AI Agent 网页自动化）

**来源与证据分级**
| 维度 | 结论 | 证据等级 |
|---|---|---|
| 产品真实性 | 真实：`github.com/citrolabs/ego-lite`（MIT，含 skill/CLI/docs），浏览器本体免费单独下载；多源独立印证（腾讯云开发者 / CSDN / ai-bio.cn / trackmcp / agent 目录），~17.5k–22k stars | tier1 官方站 + 多二手源交叉 |
| "Real-World Bench" 基准 | 仓库真实（`citrolabs/ego-browser-benchmark-framework`）但 **harness/数据集/裁判均 Citro Labs 自有**；93.5% 成功率、快 47–63%、成本 $1.64 均为 **厂商自测、零第三方复现** | tier3 厂商营销，置信度低 |
| 平台支持 | **关键缺口**：原文称"macOS/Windows/Linux 都支持"，但较新来源（followagents，6 天前）明写 **"Windows is a closed beta"**，Linux 仅 roadmap；另一来源称"Windows 有安装包" → **来源内部冲突，以"Windows 非 GA、需本机实测"为准** | 口径冲突，须本机复核 |
| MCP 集成 | **无 MCP server**（agent-native 目录明确 "MCP Status: ⚠️ No MCP server"），仅经 ego-browser Skill/CLI 驱动 | 一手仓库事实 |
| "可复用技能" | 标注 **"(coming soon) 即将上线"** = 计划未建成（宣布≠已落地） | 厂商自述 |

**与原文逐条对照（中文编译偏差修正）**
- "永久免费、无需配置"：大体属实（skill MIT、本体免费、无 API key），但"免费"是厂商承诺，标注为待观察、不视为事实落地。
- "比 Playwright 快 47–63% / 成功率 93.5% / 成本 $1.64"：数字在厂商叙事内自洽（egolite.ai 与 lite.ego.app/compare 同引 31 任务 / gpt-5.6-sol / agent pi），但**无独立复核 → 规划时不得当事实用**。
- "支持任意 Agent（含 Hermes Agent、OpenClaw）"：接入列表属实，与"Hermes + OpenClaw"超级助理方向吻合（前提：跑 macOS 且接受登录态迁移）。
- "更少触发人机验证"：原理成立（继承真实登录态 → 登录后会话更少被风控），**正对 xq 雪球 WAF 痛点** → 见下方否决理由。

**映射我方架构（核心判断）**
| 我方现实 | ego(lite) 现实 | 结论 |
|---|---|---|
| xq 采集：**Windows 生产机**（C:/项目、Python311、常驻 Chrome profile）、**定时批量 647 只** | macOS 优先、**Windows 仅 closed beta**、**无 MCP**、**Agent 驱动交互式模型**（自然语言→Space→快照→JS 批处理） | **品类 + 平台双错配** |
| R24 已用"冷却重试 + 21:00 独立分片"结构性化解 WAF 早停 | "少触发验证码"是唯一对症点 | 已自愈，**换引擎收益≈0、回归风险高** |
| 超级助理（Hermes/OpenClaw）在 Windows 工作 | Skill/CLI 在非 GA 的 Windows 上未验证 | 方向可记，落地尚早 |

**决定性否决项（任一即足够）**
1. **Windows 非 GA**（closed beta）→ 跑不了老田的生产 Windows 机；
2. **无 MCP** → 无法直接接入 WorkBuddy 工具链，只能 CLI/Skill 包一层；
3. **基准未第三方复现** → 不得依 47–63%/93.5% 做采纳依据。

**唯一真正对口场景**：交互式、需登录态的**调研类**任务（读登录后后台 / 社媒监听），**非**我方定时批量抓取；即便如此也需 macOS 宿主 + 接受"把 Chrome 登录/Cookie/密码迁移进闭源浏览器本体"的新本地攻击面与外部依赖。

**复评触发条件（满足任一再议）**：① Windows GA 发布；② 出现批量抓取之外的"登录态交互调研"刚需且具备 macOS 宿主时开小范围试点；③ **不**用它替换 xq 的 Playwright 管线。

**局限性声明（OSINT 自省）**：样本 = 厂商官网 + 4 篇中/英二手 + 1 个 agent 目录，无一手实测；平台状态可能自最近来源（6 天前）起已变化；基准厂商可控，结论偏保守。

---

## 七、R26 xq Profile 升温/轮换核查（2026-11-12）

**指令**：核 `xq_auto_login_profile` 是否真·warm，并起草 Layer 1 轮换/升温方案。

**核查结论（只读核实，未改生产文件）**
- PRIMARY `tmp/xq_auto_login_profile` **缺失**（目录空，从未创建）；代码 `_launch_ctx` 中 `os.path.isdir` 判否→`continue`，**正确降级**到 FALLBACK，不会拿 cold 空 profile 顶上。
- FALLBACK `data/xueqiu/_headed_profile` **真·WARM**：含 `xq_a_token`+`xq_r_token`+`u` 登录三件套 + `device_id`/`acw_tc`/`cookiesu`/`smidV2`，主要 token 到期 2027-10~12；13 个 xueqiu cookie（5 持久未过期/7 会话/1 过期）。
- 所有 xueqiu cookie `last_access` 停在 11-11 23:21（今日 07:00 run 撞 WAF 早停未 flush 的**假象**，非真冷）。
- **WAF 根因修正**：鉴权层不冷 → WAF 是行为/频控型，非鉴权缺失型 → 印证 R24 冷却重试方向正确，Layer 1 轮换降频为补充杠杆。

**暴露的脆弱点**
1. 单点依赖：全部 xq 采集（xq_kol + xq_stock_timeline + R24 21:00 分片）100% 依赖唯一 `_headed_profile`，无冗余。
2. PRIMARY 名存实亡；并发 run 同 profile 持锁 → 一方降级到不存在 PRIMARY → `RuntimeError 无可用登录 profile`。

**Layer 1 方案（详见 `news_collector/PROFILE_WARMTH_L1_PLAN.md`）**
- L1.1 轮换池 `xq_profiles.json`（round_robin，降频+冗余，需先有 ≥2 个 warm profile）
- L1.2 只读巡检 `check_xq_warmth.py` + 06:55 预检自动化（告警不自动修）
- L1.3 run 前冷停闸（全冷→rc=3 `PROFILE_COLD`，省无效请求）
- L1.4 新 warm profile（**人工**：A 独立登录 或 B 克隆+验证，克隆有同指纹风险→优先 A）
- L1.5 30 天刷新 SOP

**待裁定**：①是否实现 L1.1–L1.3 自动化脚手架+注册预检；②新 profile 走 A 还是 B；③池规模 2 还是 3。

---

## §八 全策略选股线完整性审计（R27, 2026-11-12）

**方法**：逐线核 存在性/新鲜度/计数闭合/桥接接线/跨线并集/缺口；registry 活路径以 `selection_universe.json` 为准（非 target_queue 内冗余 flag）。

**完整性矩阵（7 线）**

| 策略线 | 源 mtime | 源计数 | 桥计数 | 活路径xq | 缺口 |
|---|---|---|---|---|---|
| 缠论 chanlun | 11-09 09:03 | 489 | 489 | ✅ | 无（最新=489，非旧记465） |
| 侯风 picks | 11-07 20:48 | 30 | 30 | ✅ | 无（源30=桥30） |
| 侯风164候选池 | 11-12 07:52 | 164 | 164 | ✅ | 无 |
| 两池 cohorts | 11-11 23:00 | 267(4 asof) | 83(最新20261110) | ✅ | **滞后**：最新源20261110，缺11-11/11-12 |
| k_stock | 11-12 07:59 | 426 | 426 | n/a | 无（今日刷新） |
| S12 | 11-08 07:45 | 20 | 20 | ❌(设计) | xq_include=false 待裁定 |
| unified40 | 11-11 13:27 | 16 | 16 | ❌(设计) | **partial 16/40，缺24只** |

- **xq 并集（开启源去重）= 562**（s12/unified40 按设计关闭）；此前 R22 记 647 为未去重粗加，实为 562。
- **权威标志核验**：`selection_universe.json` 中 `houfeng_candidates164=True` + `cohorts=True` → R8/R10 扩源在 xq 并集**确已生效**。

**待补齐项（3）**
- **G1 两池 cohorts 断档**：策略侧滚动结算管线停于 20261110。`cohorts/` 存 `health_check_20261111.py`/`register_20261111.log`/`retry_settle_20261111.sh`（11-11 曾尝试但**未产出** cohort_20261111_*.json）；11-12 未启动。当前 xq 并集的 cohorts 段基于 20261110 旧仓。**生成 cohort = 策略研判产出，越界 OSINT1 采集/治理角色，不臆造；交策略线/老田裁定是否由我触发重跑既有管线。**
- **G2 unified40 16/40**：缺 24 只，已留 `partial:true` 结构。**待老田粘贴 v3 统一总榜原文（全40只）后补登**，方能评估 `xq_include` 扩源。
- **G3 冗余 flag 不一致**：`target_queue_houfeng.json`/`target_queue_cohorts.json` 内 `xq_include_candidates=False`/`xq_include_cohorts=False`，与 `selection_universe.json` 权威 `True` 冲突——桥脚本每日重生成时写回 False。**不影响功能（活路径读 registry），属一致性瑕疵；建议清理（桥脚本携带标志 或 删冗余字段）。**

**结论**：7 线数据本身除 G1/G2 两处外均完整闭合；G1 为策略管线断档、G2 缺老田原始输入，均非采集侧可独立补齐——需上级裁定/输入。

---

## §九 R-01 id 稳定性治理 + R-02 注册表一致性守卫（2026-11-12 老田批准 P0 两项）

> 依据 `docs/2026-11-12_OSINT1采集治理研发方案_v1.md` §三 R-01/R-02。老田 23:41 指令：「执行 P0 两项，按 R-01 → R-02 顺序落地」。

### R-01 · 全库 id 稳定性治理 + 静态回归护栏

- **修复** `news_collector/obs_bridge.py`：`snapshot_id` 由 `abs(hash((source_id,now)))%10**8` 改为 `sha256("source_id|now")[:16]`（新函数 `_stable_snapshot_digest`）。
  - 实证：旧实现两次独立进程 `21574954` / `75500748`（**不一致**）；新实现恒 `878480759d67ef43`。
  - 定级：该 `snapshot_id` 当前不参与去重（每次 run 的 `now` 本就不同），故未造成数据膨胀，属「未爆技术债」；但它是观测回溯的唯一锚点，一旦将来用于幂等/对账即失效。
- **新建** `tools/id_stability_lint.py`：用 `tokenize` 精确判定（只认 NAME token 恰为 `hash` 且后接 `(`），天然排除 `hashlib.*` / 注释 / 字符串 / 属性调用 `xx.hash()`；支持行级 `# noqa: unstable-hash` 豁免；`audit`（默认）与 `--enforce` 双模式；`--all` 扫全仓、`--paths` 指定文件。
- **新建** `tools/git-hooks/pre-commit-id-guard.sh`，并在 `tools/git-hooks/install_guard.py` 的 `BLOCKS` 注册 `idguard` 块。默认 **audit（只告警）**，`WB_ID_GUARD_ENFORCE=1` 才阻断；python 缺失时 fail-open。
  - **当前状态：`--status` 显示 `idguard 块 : 未安装`**。理由：三目录尚有 5 处他线命中，装 audit 会给所有线每次提交打告警噪音、装 enforce 会直接阻断他线提交（越界）。安装时点归老田裁定。
- **审计报告** `docs/id_stability_audit.md`：10 模块普查（7 采集 + obs_bridge + 2 portal 快照）；5 处他线遗留命中分级——`tools/kb_corpus/phase1_dq.py:49`（P2，疑分片键）、`tools/tests/test_kb_qa_bank_v2.py:63/71`、`test_kb_qa_bank_v3.py:68`、`test_r1_daily_check.py:113`（P3，测试参数/临时名）。**均未代改**（归属线处理）。

**复现命令**
```
python tools/id_stability_lint.py                                  # 预期：命中 5 处（全为他线），本线 news_collector/ 0
python tools/id_stability_lint.py --paths <含hash的.py> --enforce   # 预期 rc=1
python tools/id_stability_lint.py --paths news_collector/obs_bridge.py --enforce  # 预期 rc=0
```

### R-02 · 注册表—桥文件一致性守卫

- **新建** `tools/registry_guard.py`：以 `selection_universe.json` 为权威，按各源声明的 `bridge_flag_field` 比对桥文件；漂移/缺字段/缺文件均报 ALERT + 处置建议。**只读、不改文件**——桥脚本每日重生成会再次覆盖，自动改等于每天打架，正解在桥生成器一侧（归归属线）。
- `selection_universe.json` 新增**声明式**字段 `bridge_flag_field`：`houfeng_candidates164 → xq_include_candidates`、`cohorts → xq_include_cohorts`。未来加源只需在注册表加字段，无需改守卫代码。
- `run_xq_daily.py` 启动自检接入（`try/except` 全包，守卫故障也只打印，**绝不阻断采集**）。
- 实测：`load_queue('all')` 仍 = **647**（加字段未破坏并集）。

**验收（实测）**
- `--selftest` 5/5 通过（真/假→DRIFT、真/真→OK、缺字段→ABSENT、缺文件→MISSING、无关卡→跳过）
- 影子根（副本）注入 `xq_include_candidates=false` → `[ALERT] houfeng_candidates164 DRIFT`，明确指出 `registry=True` vs `bridge=False`；`--enforce` **rc=1**；audit **rc=0**；生产文件零改动。
- 真实环境当前 **0 问题**（检查 2 源）：两处 flag 均为 `True`（mtime 11-12 16:41），与 registry 一致。

**重要订正（对本台账 §八）**：§八 记「xq 并集（开启源去重）= 562，R22 记 647 为未去重粗加」——**该结论有误**。本次以权威函数实测 `X.load_queue('all') = 647`，与 R22 一致；562 系 R27 探针口径误算。**以 647 为准**。
另：§八 的 **G3 漂移当前已消解**（两处 flag 均已 `True`），但**漂移机制仍在**（桥脚本每日重生成），故守卫的价值是持续监测而非一次性修复。

**复现命令**
```
python tools/registry_guard.py --selftest    # 预期 5/5 PASS
python tools/registry_guard.py               # 预期：检查 2 源，问题 0
python -c "import sys;sys.path.insert(0,'news_collector');import xq_stock_timeline as X;print(len(X.load_queue('all')))"   # 预期 647
```

### 待裁定（3 项）
1. `idguard` 钩子块**何时安装**（建议先清 5 处他线存量，再装 audit，观察一周转 enforce）。
2. 5 处他线命中**是否由本席代改**（默认不代改，等归属线）。
3. 全仓 `--all` 另 37 处是否纳入下阶段治理（须先划目录归属）。

### 边界声明
本席只做采集/治理工程：**未改任何他线代码、未裁任何阈值、未动扩源决策、未安装共享 git 钩子**（仅注册块代码，`--status` 确认未安装）。

---

## 十、R-03：三项裁定落地（idguard 安装 + 分域 enforce + 存量工单）· 2026-11-13

> **状态变更提示**：上节 R-02 边界声明的「未安装共享 git 钩子」，**自本节起已变更**——
> idguard 块已于 2026-11-13 安装。

### 10.1 三项裁定的处置

| 裁定项 | 处置 |
|---|---|
| ① idguard 何时装 | **已装**。不等"清完存量"，改用**基线豁免 + 分域 enforce** |
| ② 5 处是否代改 | **不代改**，改为出工单（`docs/id_stability_cleanup_worklist.md`），逐条给级别+补丁+归属 |
| ③ `--all` 是否纳入 | **纳入，但先剥第三方**：28 处 `video/pylibs/*` 属库内部实现，非治理对象 |

### 10.2 数字订正（两处，均系本席前次记录有误）

| 项 | 原记 | 实际 | 原因 |
|---|---|---|---|
| 全仓 `--all` | 37 处 | 40 原始 → **14 待治理** | 40 中 28 处在 `video/pylibs/*`（第三方）；原 37 为口径遗漏 |
| 三目录 | 5 处 | **7 处**（三目录）/ 14（全仓） | 漏检 2 处 f-string 内调用，见 10.3 |

### 10.3 判据缺陷修正：tokenize 的 f-string 盲区（重要）

原判据用 `tokenize`。**Python ≤3.11 把整个 f-string 当一个 STRING token** →
`f"u{abs(hash(question)) % 35 + 1:04d}"` 里的裸 `hash()` **检不出**；3.12+ 才拆开。
实测后果：手工用 **3.11** 扫出 12 处，钩子用 **3.13**（PATH 上 `python` 实为 3.13.14）跑出 14 处 →
同一文件被判「新增」，基线失效。

**修正**：判据改 **`ast` 为主**（`ast.Name(id='hash')` 作 `ast.Call` 的 func；`xx.hash()` /
`hashlib.*` 是 `ast.Attribute`，天然排除），`tokenize` 仅作语法错兜底。
修正后 **3.11 与 3.13 结果完全一致（14/14）**，并新发现 2 处真实漏检。

### 10.4 守卫当前策略（已生效）

| 暂存路径 | 模式 | 行为 |
|---|---|---|
| `news_collector/**` | **enforce** | 新增裸 hash → **阻断提交**（本线基线 0 命中，零误伤） |
| 其余目录 | audit | 新增裸 hash → 告警 + 留痕，不阻断 |
| 基线内 14 条 | 豁免 | 不告警（`tools/id_stability_baseline.json`，按「文件+源码行指纹」，行号漂移不影响） |
| `WB_ID_GUARD_ENFORCE=1` | 全量 enforce | 基线内仍豁免 |

### 10.5 验收证据（实测，非声明）

```
本线 news_collector/ 注入裸 hash   → hook rc=1 阻断 ✓
tools/ 注入裸 hash                 → hook rc=0 告警 ✓
基线内文件（加无关注释后暂存）      → hook 输出 0 行，完全静默 ✓
--all 双解释器一致性               → 3.11=14 / 3.13=14 ✓
--all 基线生效后                   → 0 新增 / 14 条已知 ✓
install_guard.py --status          → idguard 块 已安装；worktree/eol 保持 audit ✓
```

### 10.6 14 处定级（详见工单 `docs/id_stability_cleanup_worklist.md`）

- **P1 真 bug 6**：`quant_backtest/tools/liudu_trial_run.py:180`（回测种子→不可复现）、
  `quant_backtest/sector_state.py:303` + `sector_replay.py:102`（行情指纹→缓存恒失效）、
  `agent_platform/memory/store.py:136`（记忆 id→去重失效）、
  `tools/kb_corpus/phase1_dq.py:49`（MinHash→签名不可比）、
  `tools/kb_corpus/assertion_conflict_fixture.py:35`（断言 id）
- **P2 5**：`agent_platform/tools/data_lineage.py:104`（rid 语义双向可疑）+ `tools/tests/*` 4 处（测试不稳定）
- **P3 3**：`agent_platform/tools/agent_sandbox.py:81`（已含 time.time）、
  `deliverables/withdrawn-*` 临时文件、`tools/tests/test_r1_daily_check.py:113`（tmp_path 隔离）
- **采集主线 `news_collector/` = 0 命中**（这是能立刻转硬拦的前提）

### 10.7 转全量 enforce 的判据（替代"观察一周"）

按线清零、逐线纳入硬拦：`news_collector/`（**已完成**）→ `quant_backtest/`（3 处 P1，危害最大）
→ `tools/kb_corpus/`（2 处 P1）→ `tools/tests/`（5 处）→ `agent_platform/`（3 处）→
`deliverables/withdrawn-*`（确认废弃则归档）。全部清零 → 基线清空 → 全量 enforce。

### 10.8 复现命令
```
python tools/id_stability_lint.py --all                    # 全仓普查（已排第三方）
python tools/id_stability_lint.py --all --no-baseline      # 只看新增
python tools/id_stability_lint.py --all --show-known       # 列出基线内已知
python tools/id_stability_lint.py --all --write-baseline   # 收缩基线（清零后执行）
python tools/git-hooks/install_guard.py --status           # 守卫状态
python tools/git-hooks/install_guard.py --uninstall        # 回滚（仅卸块，保留原有内容）
```

### 10.9 边界声明（R-03）
- **未改任何他线代码**：14 处命中只定位、定级、给补丁，未落任何一行到他线文件。
- **未裁任何阈值/扩源/研判**：本项纯工程护栏。
- **已装共享钩子**（与 R-02 不同）：装的是 `install_guard.py` 生成的**托管块**（按标记增删），
  未手改 `.git/hooks/pre-commit`；回滚一条命令；分域设计保证他线提交不被阻断。

---

## 十一、采集新鲜度 SLA 视图（方案 R-03 / 现场发现 D7）· 2026-11-13

### 11.0 先说编号：执行批次号 ≠ 方案编号（口径订正）

此前提交信息里出现过「R-01 / R-02 / R-03」三批，与研发方案 v1 的 R-01~R-06 **不是一一对应**。
以本表为准，避免后续引用串号：

| 方案编号（`docs/2026-11-12_OSINT1采集治理研发方案_v1.md`） | 落地提交/批次 | 状态 |
|---|---|---|
| R-01 全库 id 稳定性治理 + 静态护栏 | 批次 R-01（lint+迁移）、批次 R-03（分域 enforce 上线） | ✅ 已完成（存量 14 处待归属线） |
| R-02 注册表—桥文件一致性守卫 | 批次 R-02 | ✅ 已完成 |
| **R-03 采集新鲜度 SLA 视图** | **本章 §十一** | ✅ 本次完成 |
| R-04 治理动作可复现化（ACPO） | — | ⬜ 未启动（需上级同意台账 schema 增补） |
| R-05 维度覆盖矩阵 | — | ⬜ 未启动（需研判线确认维度标准） |
| R-06 WAF 自适应节流 | — | ⬜ 未启动（需风控裁定） |

### 11.1 做了什么
新增只读监控 `news_collector/collect_sla.py`（+ 测试 `news_collector/tests/test_collect_sla.py`），
一屏回答 D7 的四个问题：**各源最后成功时间 / 滞后时长 / 队列覆盖缺口 / 积压与连续不达标**，
替代人工 `ls -lt`。产物：`data/collect_sla.json`（机读）、`data/collect_sla_state.json`
（跨轮记忆）、`logs/collect_sla.md`（人读表）。

### 11.2 首跑读数（2026-11-13 00:30，实跑非示例）
```
新闻流(自采)   GREEN  11-13 00:17   滞后0.2h   —                       新鲜
互动易         GREEN  11-12 16:41   滞后7.8h   28/65 (all 216/465)     新鲜
上证e互动      GREEN  11-12 16:41   滞后7.8h   15/28 (all 47/212)      新鲜
淘股吧         AMBER  11-11 11:30   滞后37.0h  56/65 (all 436/465)     内容未新增但运行日志 11-12 09:56（见 11.4）
东财股吧       GREEN  11-12 23:26   滞后1.1h   65/65 (all 109/465)     新鲜
雪球timeline   GREEN  11-12 21:13   滞后3.3h   624/647                新鲜
千股千评       GREEN  11-12 08:08   滞后16.4h  57/65                  新鲜
判定=AMBER（RED=0 / AMBER=1 / NODATA=0），阈值来源 DEFAULT，周末放宽 0h
```
三条**读数级**观察（不下结论、不裁阈值）：
1. **淘股吧内容 37h 未新增，但运行日志 11-12 09:56 有 `[tgb] 完成: 25 标的, 新增 0 条`**
   → 更像「跑了、源端无新帖」而非断供；真伪需人工确认，本表只降级解释不洗白。
2. **覆盖缺口集中在 irm 28/65（37 缺）与 sse 15/28（13 缺）**，二者都是当日 actionable 池；
   是「无该票互动页」还是「采集漏取」，需逐码复核（本轮未做）。
3. **千股千评 57/65（缺 8）**：`latest.json` 记录 `n_queue=57`，与 actionable 65 的差 8
   属源口径还是漏取，同样待复核。

### 11.3 判据与阈值
- `stale ≤ 26h+slack` → GREEN；`≤ 48h+slack` → AMBER；`> 48h+slack` → RED；
  `consecutive_bad ≥ 3` 时非 GREEN 一律提级 RED。
- `slack`：过去 3 天每含一个周末日 +24h（同 `sched_heartbeat._line_days` 判例），防周末必然误报。
- 阈值全部标 **DEFAULT 经验值**，`--thresholds <json>` 可覆盖；**正式调参归 11-15 微调日**，
  本席不定死、不裁标准。
- 覆盖分母取**采集器真实范围**：irm/sse/taoguba/guba/qianping = `actionable`（不是 465 只的 `all`）；
  sse 再按沪市 `60/68` 过滤（28 只）——用 65 只全市场当分母属分母错误，会虚报缺口。

### 11.4 口径限制（必须随读数一起看）
- **`last_ok` 是「内容最后新增」，不是「运行成功」**：四源均 append-on-new 写盘，当天 0 新帖则
  mtime 不动。故带 `runlog` 的源另报 `last_run_ts`，且**只允许把 RED 降级解释为 AMBER，不允许洗成 GREEN**。
- `runlog` 是**共享日志**（`logs/target_queue_intel_daily.log`，含 irm/sse/tgb 三家），
  日志行无日期、无法区分源级时刻，故只能给「该日志最近落笔时间」，**不可当源级心跳**。
- 覆盖比对是「文件是否存在」，**不校验内容是否为空/过旧**；空文件单列 `empty_files`
  （当前四源均 0）。
- 新闻流行的 `last_ok` 取 `news.db max(ts)`，是内容时间；子源 ok/got/new 读 `status.json`，
  显式 `skipped`（有裁定依据的降级）不计失败。

### 11.5 验收判据兑现
- [x] 停更源可被识别：cohorts 类停更在首跑中体现为 AMBER/RED 并给出 stale 小时（本表淘股吧即例）
- [x] 正常源呈 GREEN：k_stock/guba/新闻流/雪球 均 GREEN
- [x] 重跑幂等：连跑两次读数一致（除滞后小时随时间推移），JSON 结构稳定
- [x] 只读：`git status` 仅新增本脚本/测试/产物；不碰任何采集产物、不触网、不启浏览器
- [x] 7 条单测全过，**3.11 与 3.13 双解释器一致**（`7 passed`）

### 11.6 复现命令
```
python news_collector/collect_sla.py                        # 表格 + 落 json/state
python news_collector/collect_sla.py --json                  # 只打 JSON
python news_collector/collect_sla.py --md logs/collect_sla.md
python news_collector/collect_sla.py --no-state --no-write    # 纯快照（无跨轮记忆）
python news_collector/collect_sla.py --now "2026-11-13T09:00:00"   # 时点回放
python -m pytest news_collector/tests/test_collect_sla.py -q
```

### 11.7 边界声明（§十一）
- **只读**：不改任何采集产物；唯一写入是脚本自己的 json/state/md。
- **不裁阈值、不裁扩源、不做研判**：阈值为 DEFAULT，读数不含买卖/方向/结论。
- **未接入计划任务**：如后续注册，**必须**用固定 `exit 0` 的 wrapper——本脚本 rc=1 表示
  「有 RED」，属业务告警，直接注册会让任务计划程序把「正常有告警」记成失败（判例：
  退出码语义是任务跑完了吗，不是业务结论好不好）。
- **未接门户页**：`data/collect_sla.json` 已按机读设计，门户接线属下一阶段，本批不动 portal。

---

## 十二、覆盖缺口成因复核（A 项）· 2026-11-13 01:40

> **本条订正 §11.2 的缺口数字**（§11 保留原文不改，追加订正）：§11.2 写的
> 「irm 28/65、sse 15/28、qianping 57/65」中，irm 的分母是错的，qianping 的分母也是错的。
> 错因不是采集坏了，是**我第一版 SLA 用 65 只全市场当分母**。本节给出证据与订正。

### 12.1 分母错在哪（证据优先，先钉分母再谈缺口）

| 源 | §11 用的分母 | 真实分母 | 证据（一手） |
|---|---|---|---|
| irm 互动易 | 65 | **29（深市 00/30）** | `logs/target_queue_intel_daily.log:3` `→ irm_collector.py 29 只`；同轮 `:6` `完成: 29 标的, secid 解析 29, 失败 0` |
| sse 上证e互动 | 28（已按沪市） | 28（沪市 60/68） | 同日志 `:7` `→ sse_collector.py 28 只`；`sse_collector.py:_load_queue` 只收 60/68 |
| qianping 千股千评 | 65 | **57（非北交所）** | `data/qianping/latest.json` `n_queue=57` = 65 − 8(BJ)，设计上不采 92xxxx |
| taoguba 淘股吧 | 65 | 65（含 BJ，实测有尝试） | 同日志 `:11` 40 只 + `:15` 25 只，队列里含 920xxx |
| guba 东财股吧 | 65 | 65 | 同日志 `:19/:23/:27` 30+30+5 只 |

**结论一：分母一改，缺口立刻缩水。** 订正后：

```
互动易       28/29   缺 1
上证e互动    15/28   缺 13
淘股吧       56/65   缺 9   （8 只北交所 + 688475）
东财股吧     65/65   缺 0
千股千评     57/57   缺 0   ← §11 报的「缺 8」是假缺口
雪球timeline 624/647 缺 23  （全部 920xxx，见 12.3）
```

### 12.2 逐只复核：是「源端无内容」还是「我们漏取」

方法：新增只读探针 `news_collector/collect_gap_probe.py`（**不写盘、不动队列、不重建 uid 映射**，
只复用采集器自身的 `fetch_company`）。判定逻辑是「同批有内容票解析正常 ⇒ 解析器没坏；
目标票返回 0 ⇒ 源端无内容」。

| 源 | 缺口 | 探针结果 | 判定 |
|---|---|---|---|
| sse | 13 只（600930/688235/600055/601916/600998/601607/688475/601000/603288/600718/600699/600050/600085） | 13 只**逐个**「拉 0 条（答 0）」，且全部在 `uid_map` 中（页面存在）；同批 603348 拉 4 条、600587 拉 5 条解析正常 | **源端确无问答**，非漏取 |
| irm | 1 只（300984） | `secid=9900044779` 解析成功，`fetch_company(total=0)` 抓 0 条；同轮 29/29 解析成功、失败 0 | **源端确无问答**，非漏取 |
| taoguba | 9 只（8×920xxx + 688475） | 本轮实跑含 920xxx（40+25 两批），`[tgb] 920690 新增 0`、失败 0 | 实测空；**未能区分「无帖」与「源不支持 BJ」**，按空处理并要求到期复核 |
| qianping | 0（订正后） | `latest.json` 记为 57 只，与「非北交所」分母一致 | 无缺口 |
| guba | 0 | 65/65 全覆盖（含北交所） | 无缺口 |
| xq | 23 只，**全部 920xxx** | 见 12.3 | **代码缺陷（pending_fix）** |

### 12.3 真发现：雪球 23 只北交所票被静默丢弃（工单 OPS-1113-01）

- **现象**：xq 缺口 23 只，前缀 100% 是 `92`（920076/920087/920091/920145/920166/920207/920237/
  920374/920433/920436/920454/920455/920522/920527/920541/920556/920599/920690/920802/
  920870/920885/920895/920982）。
- **根因**（代码定位）：`news_collector/xq_stock_timeline.py:264`
  ```python
  symbol = ("SH" if code.startswith(("6", "9")) else "SZ") + code
  ```
  `92xxxx` 命中 `startswith("9")` → 拼成 `SH920870`（北交所应为 `BJ920870`）→ 雪球返回空列表
  且 **HTTP 200**，故既无文件、也不进 `_deferred.json`（该文件当前为 `{}` 即反证），
  属于**静默空**而非报错，SLA 之前完全看不见。
- **建议补丁（一行，未落代码）**：
  ```python
  def _symbol(code):
      """9 开头不全是沪市：92/43/83/87 为北交所/新三板 → BJ。"""
      c = str(code).zfill(6)
      if c[:2] in ("92", "43", "83", "87"):
          return "BJ" + c
      return ("SH" if c[0] in ("6", "9") else "SZ") + c
  ```
- **为什么只出工单不改**：把北交所纳入 xq 采集 = **扩源决策**（既有口径「BJ 仅入 taoguba」），
  归上级裁定；且 8 只 BJ 会进入 07:00 有头管线，涉及 WAF 负载，属风控姿态。
  补丁已备好，一句话批准即可落。
- **工单**：`OPS-1113-01 雪球北交所前缀缺陷`（待裁定；裁定前 23 只在白名单里标 `pending_fix`，
  **仍计入待查，不洗白**）。

### 12.4 白名单机制：让"缺口清单"能减下去，但减得有理有据

新增 `news_collector/collect_gap_whitelist.json`（`schema: collect_gap_whitelist/v1`）：

```json
{"source":"sse","codes":["600050","600085"],
 "kind":"no_content",              // no_content | structural | pending_fix
 "reason":"…探针 0 条，同批有内容票解析正常…",
 "method":"python news_collector/collect_gap_probe.py sse 600050 600085",
 "verified_at":"2026-11-13","expires":"2026-12-13","verified_by":"OSINT1 马良"}
```

四条纪律（已写进代码，不是口头约定）：
1. **只标注、不删缺口、不改采集行为**；`gap` 原样保留，另给 `gap_known`/`gap_open` 两截。
2. **`pending_fix` 不算合法缺口**：仍计入 `gap_open`，另给 `gap_pending_fix` 计数，防止用白名单把债洗掉。
3. **`expires` 到期即失效**（不设宽限：过期条目一律不生效）：过期的计入 `gap_whitelist_expired` 并在输出里显形，
   防白名单变成永久垃圾桶。
4. **每条必须带 `reason` + `method`**：换人可按 `method` 重跑复现；无证据的条目不收。

支持 `codes`（精确码）与 `prefixes`（前缀规则，如北交所 `92`），避免把 23 只逐个抄一遍。

### 12.5 复核后判定变化

```
判定=AMBER  RED=0 AMBER=1 NODATA=0
缺口合计=46（已核验合法 23 / 待查 23；白名单过期 0 条）
```
- 「已核验合法 23」= sse 13 + irm 1 + taoguba 9，全部有探针/实跑证据。
- 「待查 23」= xq 的 23 只北交所，全部指向 OPS-1113-01，**不是散落的神秘缺漏**。
- 唯一 AMBER 仍是淘股吧（内容 37.8h 未新增、运行日志 15.4h 前有记录）——结构性原因已定位，
  待 07:00 正常跑一轮即可判定是否真断供。

### 12.6 复现命令
```
python news_collector/collect_sla.py --no-state --no-write      # 看订正后读数（不落盘）
python news_collector/collect_sla.py --json                     # 含每源 missing 全量代码

# 探针（只读、不写盘；需 Python311，PATH 上的 3.13 无 requests）
"C:/Program Files/Python311/python.exe" news_collector/collect_gap_probe.py sse 600050 600085
"C:/Program Files/Python311/python.exe" news_collector/collect_gap_probe.py irm 300984

python -m pytest news_collector/tests/test_collect_sla.py -q     # 9 passed（3.11/3.13 一致）
```

### 12.7 边界声明（§十二）
- **未改任何采集器行为**：xq 前缀缺陷只定位、只出工单、只备补丁，**未落一行代码到 xq_stock_timeline.py**。
- **未裁扩源**：北交所是否进 xq 采集，归上级。
- **探针只读**：`collect_gap_probe.py` 不写数据文件、不改队列；单次请求量 13 只 × 1~2 页。
- **白名单不是免死金牌**：所有条目 30 天到期，`pending_fix` 不计入合法缺口。
- **未复核项**：taoguba 对北交所「无帖 vs 不支持」仍未区分（缺页级证据），到期须重测；
  雪球那 23 只**当时**只做了代码静态定位 + `_deferred.json` 为空的反证，未做实测——
  实测在 §13 补做（老田 2026-11-13 批准落补丁）。

## 十三、OPS-1113-01 落补丁与实测（2026-11-13 01:30–01:45）

### 13.1 改动
- `xq_stock_timeline.py` 新增 `to_symbol(code)`，替换原第 264 行一行式判据：
  `43/83/87/92 → BJ`、`6/9/5 → SH`、其余 `→ SZ`。保留 9→SH 覆盖沪 B 900xxx、
  5→SH 覆盖沪基金，避免修一个段打坏另一个段。
- **请求量不变**：注册表并集 647 只里 BJ 23 只（全部 920xxx）本来就在队列中被请求，
  只是 symbol 拼错打空靶。改动不增加 07:00 有头管线的请求条数，WAF 负载无增量。
- 新增 `tests/test_xq_symbol.py`（5 条）：BJ/SH/SZ 三段映射、后缀剥离与前导零、
  以及**反回退断言**——源码中不得再出现 `startswith(("6", "9")) else "SZ"`。

### 13.2 实测（活登录 profile，`--pages 1`）
| 批次 | 标的 | 新增条数 |
|---|---|---|
| 先验 1 | 920870 | +15 |
| 先验 2–4 | 920599 / 920076 / 920091 | +20 / +20 / +19 |
| 其余 19 只 | 920087…920982 | 379 |
| **合计** | **23/23** | **453** |

WAF 触发 0 次、deferred 0 次。落盘字段复核：`symbol=BJ920870`、`code=920870`、
`ts` 与 `text` 齐备（示例为恒进感应三季报转载）。

### 13.3 读数（只陈述，不研判）
- 原 23 个「缺口」**100% 是误判**（false negative），不是源端无内容。
- SLA 复跑：雪球 `647/647`、缺口 0；缺口合计 23 = sse 13 + irm 1 + taoguba 9，
  全部为已核验合法缺口（白名单 3 条，30 天到期）。
- 该缺陷的形态值得记住：**静默空比报错危险**——报错会进 `_deferred.json` 被看见，
  静默空只会在缺口清单里留下一个数字，而那个数字还可能被我自己的分母错淹没。

### 13.4 纪律补充（写进方法论）
1. **静态定位足以找到根因，但不足以证明修复有效**：必须实抓一次拿到内容才算闭环。
2. **缺口归零后要删对应白名单条目**：否则白名单会积累「历史合法」记录，掩盖新问题。
   本次已删 `xq/92 前缀 pending_fix` 条目，并在 `note` 里写明「今后再现 xq 缺口须按新证据重验」。
3. **分母、分母、分母**：本轮先错在分母（§12.1），后错在判据（§13），两次都不是采集器坏。

## 十四、2026-11-13 盘前巡检：淘股吧「伪成功」实证（11-13 06:30–06:50）

### 14.1 巡检读数（06:30）
聚合口径全 GREEN（RED=0 / AMBER=1 淘股吧 43h），但按**每票**算陈旧度后暴露两处：

| 源 | 分母 | 有 | 缺 | 中位时龄 | >24h | >48h |
|---|---|---|---|---|---|---|
| 互动易 | 29 | 28 | 1 | 46.2h | 18 | 13 |
| 上证e互动 | 28 | 15 | 13 | 41.1h | 13 | 0 |
| 淘股吧 | 65 | 56 | 9 | **83.7h** | 56 | 30 |
| 东财股吧 | 65 | 65 | 0 | 7.5h | 2 | 0 |
| 雪球 | 647 | 647 | 0 | 12h | 136 | 136 |

**结论一：聚合 SLA 会掩盖单票陈旧**。目录里「最大 mtime 新鲜」不等于「每只票都刷新过」——
雪球 647/647、淘股吧 56/65 都显示 GREEN，实际分别有 136 只、30 只超过 48h。
这不是读数错，是口径边界；已记入待办（见 14.4）。

### 14.2 淘股吧：11-12 那轮 65 只全部「新增 0」是伪成功
- **日志证据**：`logs/target_queue_intel_daily.log` 11-12 09:43/09:44 两批 40+25 只、
  `新增 0 条, 失败 0/1, 拦截页 -`；同一轮 guba 新增 457 条（134+283+40），同票不同源不可能全空。
- **探针证据**：新增只读探针 `collect_tgb_probe.py`，同 UA/Referer 复取，
  每票稳定解析 20 条；`000543` 本地最新 11-04 22:27 vs 页面 11-11 18:17、
  `002027` 11-10 16:21 vs 11-11 17:31、`600519` 11-10 20:50 vs 11-12 20:49。
- **机制**：HTTP 200 + 页面 >8KB + `parse_blocks` 返回 0 行时，采集器不报错、只计「新增 0」，
  与「真无新帖」在日志里完全同形 → 静默丢数据。同族于 §13 的「静默空」，区别是这次丢的是**已有内容**。
- **处置**：以 `--pages 1` 对 65 只补采，**新增 385 条、失败 0、拦截 0**，证明是当轮瞬时挑战而非源端无帖。
  补采后淘股吧转 GREEN（0.0h）。

### 14.3 雪球：136 只 >48h，尾部饥饿（未修，待批准）
- `XqTimelineDaily` 11-12 07:00 轮 rc=**1073807364**（0x40010004，进程被终止），
  日志尾部连续 `防御触发(HTTP 400)——冷却300秒后重试` → 单码被拖长；
  机器 11-12 08:50 关机（事件 ID 1074/6006/13），任务中途被杀。
- 21:00 扩源分片补了 221 只，但 21:21 又关机被杀 → 队列尾部（600780 起）连续 136 只未刷新。
- **结构性风险**：任务每次从 `sorted(codes)` 头部顺序跑，中途被杀则尾部永远饿着。
  建议改「断点游标 + 分片配额」（每轮从上次结束处继续），属采集行为变更，**待上级批准**。

### 14.4 待办（本轮只给读数与工单，不改采集行为）
1. **淘股吧伪成功护栏**：`parse_blocks==0 且 HTTP 200 且 len(html)>8000` 记为 `parse_empty`，
   按轮统计占比，>30% 直接判失败并写日志独立行——别让 0 新增同时代表两种含义。待批准。
2. **雪球断点游标**：见 14.3，待批准。
3. **SLA 增加「单票陈旧」维度**：现有 `last_ok` 是目录级最大 mtime，须补 `stale_codes_pct`
   （分母内 >48h 的占比），否则 GREEN 会被误读成「每只都新」。
4. **`XqTimelineDaily` 电池策略**：`DisallowStartIfOnBatteries=True`，
   若 07:00 时在市电之外则该轮直接不启动（今日实测 AC 供电，无影响）。是否放宽属系统级配置，待批准。


## 十五、2026-11-13 四项批准落地（15.1–15.4，同日提交）

老田当日批准 §14.4 全部四项。以下按「改了什么 / 判据 / 验证 / 未做什么」四段记，
不裁阈值、不改他线代码。

### 15.1 淘股吧 parse_empty 护栏（`taoguba_collector.py`）

**改了什么**：新增 `judge_parse_empty()` 纯函数 + 两个计数器。判据拆两条，互不替代：

| 判据 | 触发条件 | 含义 |
|---|---|---|
| `STRUCT_MISMATCH` | 首页 `headImg` 标记存在但解析 0 行 | 解析器失效（模板变更），与占比无关，直接失败 |
| `PARSE_EMPTY` | 首页 0 行占比 > 30% 且样本 ≥ 5 | 大面积空，疑似挑战页伪装成功 |

**为什么拆两条**：判例（§14.2）那一轮是「页面正常、解析 0 行」，若只按占比判，
少量结构失配会被大样本摊薄；若只按结构判，挑战页（无 headImg）会被漏掉。
两条都要有，才覆盖「我们瞎了」与「源端真空」的两种成因。

**容忍边界**（防误伤）：①阈值是「**超过**」才判，40 只里 12 只空（正好 30%）放行；
②样本 < 5 不判（手动跑 2–3 只不告警）；③真·空票（如北交所无帖）计入分子但通常不越线。
`--parse-empty-pct` 可调，`--no-parse-guard` 仅排障用。

**验证**：`tests/test_tgb_parse_guard.py` 9 条（含判例复现：65 只全空 → rc=2）。
退出码语义：rc=2 = 采集到但全解析不出（真故障）；驱动脚本 `run_target_queue_intel.py`
只记 `!! rc=2` 不中断后续源，故不影响同批其它源。

### 15.2 雪球断点游标 + 分片配额（`xq_stock_timeline.py`）

**改了什么**：新增 `_load_cursor/_save_cursor/rotate_from` + `--quota/--no-cursor/--reset-cursor/--plan-only`。
游标落 `data/xq_timeline/_cursor.json`（原子写：tmp + fsync + `os.replace`），
**每码写一次**，所以被关机/强杀也能从断点续跑，不再每轮从队头重来。

**分域**：游标按 `(scope, sources)` 分域。全量轮（`--queue all`）与 21:00 分片轮
（`--sources ...`）的断点互不串味——否则分片跑到哪里，全量轮就从哪里起，覆盖会错乱。

**验证**：单元 7 条（旋转无损、队尾 wrapped 进轮次、名单变更退回原序、原子写不留 tmp）；
真队列集成（`--plan-only`，不启浏览器、不与在跑任务争 profile）：

```
配额 40/647 起=000001…              ← 无游标：从队头
模拟上次止于第301只 301515 → 起=301516  ← 有游标：从紧邻下一只
```

**未做**：`--quota` 默认仍为 0（不限）——**不替你决定单轮跑多少**。理由：07:00 窗口
被关机打断时，配额挡不住中断，游标才是解；配额只在「想让多轮拼成一天全覆盖」时才需要设，
那是调度决策。跑完本次清理后 `_cursor.json` 已删除，下一轮由新代码自行建立。

**残留**：游标解决「不重复劳动」，但**一轮跑不完整个队列**这件事仍在。
647 只 × 1 页 ≈ 1 小时量级，若窗口不够，全队列周期仍 > 1 天。要「每天全覆盖」需改
按「最久未刷新优先」排序（比轮转更彻底），属新方案，未做。

### 15.3 SLA「单票陈旧」维度（`collect_sla.py`）

**改了什么**：新增 `_scan_stale()`，每源输出 `no_new_n` / `no_new_pct` /
`oldest_age_h` / `no_new_sample`，表加一列「48h无新增」，汇总加 `no_new_48h_total`。

**口径纪律（关键，别读错）**：四源全是 append-on-new，文件 mtime 停住只说明**期间没有新内容**，
不说明没去采。所以字段一律叫 `no_new_*`（无新增），不叫 stale/dead；
判「有没有去采」交叉 `last_run_ts` 或源日志。**该列不改判定，只做旁证**——
阈值裁定权不在本席。

**改判定的地方一处也没有**：`state` 仍只看源级 mtime；新增列与 summary 计数是纯增量。

**验证**：`test_single_ticker_no_new_dimension` 等 4 条（含「无单票粒度源须留空，不许编 0」）。
实测立刻显出旧工具看不见的东西：

```
互动易   GREEN  14.6h   13/28 (46.4%)  ← 源级绿，近半数两天无新增
雪球     GREEN   0.0h  136/647 (21.0%) ← 正是 §14.3 那批尾部饥饿的票
```

**边界**：`news`（sqlite）与 `qianping`（一天一文件）没有单票粒度，输出 `-`，不编数。

### 15.4 `XqTimelineDaily` 电池策略（系统级，已执行）

**五要素**（按规范先列后做）：
1. 命令全文：`$s=(Get-ScheduledTask XqTimelineDaily).Settings; $s.DisallowStartIfOnBatteries=$false; $s.StopIfGoingOnBatteries=$false; Set-ScheduledTask -TaskName XqTimelineDaily -Settings $s`
2. 影响面：只改该任务的电池两项；动作/触发器/时限（PT72H）/重启策略读回未变；
   任务本身仍在 Running（改的是下次生效的设置）。
3. 不可逆点：无（布尔值，可随时改回）。
4. 替代方案：不改（当前 AC 供电）；或在 wrapper 内做 AC 检测自旋——更复杂且无收益。
5. 回滚：同样命令把两个值设回 `$true`。

**读回核对**：`BEFORE True/True → AFTER False/False`，`ExecutionTimeLimit=PT72H` 保留，
`ACTION`/`TRIGGER` 未变。

**顺带读数（只报不改）**：全机仍有 **146** 个任务 `DisallowStartIfOnBatteries=True`，
其中与采集/日报相关的另有 `SentimentSnapshot`、`houfeng_paper_daily`、
`ZeroQ-KStock-Resume-Daily` 三个。是否一并放宽属系统级决策，本席不动。

### 15.5 本轮未做（明确列出，便于下轮追）

- 未改 09:30 / 21:00 任何调度参数；未新增计划任务。
- 未给 `--quota` 设生产值（见 15.2「未做」）。
- 未按「最久未刷新优先」重排队列（新方案，需批准）。
- 未调查互动易 13/28 的成因（本轮只出读数）。
- 未代改他线代码；未裁任何阈值。

---

## 十六、雪球 07:00 轮「会话级失效」事故（2026-11-13 09:30–11:20）

### 16.1 现象（07:00 生产轮）

| 项 | 读数 |
|---|---|
| 进度 | 前 **400/647** 只正常，逐只 +19/+20 条 |
| 断点 | 第 401 只 `600802` 起，**逐只 HTTP 400** |
| 空转规模 | 14 只 defer、45 次触发（3 次冷却 × 300s = **900 秒/只**） |
| 已耗 | 2.5 小时（07:15 起至 09:44 停止） |
| 代码版本 | 进程 07:00:01 启动，四项补丁 07:19:47 才提交 → **跑的是旧码，无游标** |

### 16.2 根因：会话级请求预算 ≈ 400 次，撞顶后退化为零星通过

证据链（三轮独立会话，逐条实测）：

| 轮次 | 启动 | 成功只数 | 撞墙位置 | 撞墙后表现 |
|---|---|---|---|---|
| A（07:00 生产） | 07:00:01 | 400/647 | 第 401 只 `600802` | **14 只 × 3×300s 全灭，零恢复** |
| B（09:47 重跑） | 09:47:20 | 400/666 | 第 401 只 `600926` | 冷却 300s 后 `600926` **+20 恢复**，`600928` 直接 +19，下一只再封 |
| C（10:23 续跑） | 10:23:04 | 从 `600929` 起 | — | **跑完全部 666/666，仅 1 次 WAF 且当场恢复** |

结论（按证据强弱排序）：
1. 撞墙是**请求量/速率限流**，不是时长：A 轮 15 分钟打满 400 只即封，B 轮 25 分钟
   打满 400 只同样封，**两轮都精确停在成功第 400 只之后的那一只**。
2. 撞顶后不是彻底失效，而是「冷却 300 秒偶尔放行 1–2 只」，不稳定（A 全灭 14 只、B 恢复 2 只）。
3. **新会话重置预算**：单只探针（09:46）、C 轮续跑均立即恢复；C 轮全程 666 只只撞 1 次
   `WAF_CHALLENGE`（JS 求值异常）并当场重试成功——说明**同样的请求量在不同时段结果不同**，
   限流不只由会话内计数决定（可能含时间窗/速率复合因子），**故不能把 400 当固定预算**。
4. 与鉴权无关：返回 **HTTP 400**（限流/风控），非 401/403。

补充：采集器全程只在启动时 `page.goto("https://xueqiu.com")` 预热一次，此后所有
`/query/v1/symbol/search/status` 复用同一 cookie 上下文，**轮内无会话重建**。

### 16.3 次生缺陷：比例阈值护栏对「慢速滴漏」失效

`SUSTAINED_ABORT_PCT=0.20` 用的是 `waf_hits / 已处理数`，**分母含成功样本**。
400 只成功垫底后，要再累积 86 只失败（约 15 小时）比例才到 20%——防拉黑护栏等于没有。
与 SLA 的 `consecutive_bad>=3` 判据不同源：**连续失败数**才反映「会话死了」，比例不反映。

### 16.4 补丁（`xq_stock_timeline.py`）

三个纯函数 + 五个常量，主循环三处接线：

- `window_bad_ratio(outcomes)`：最近窗口失败占比。
- `recent_fail_count(outcomes, k=3)`：最近 3 只里的失败只数 → 决定冷却档位。
- `retry_policy(recent_fail)` → `(重试次数, 冷却秒数)`：近 3 只**一只都没失败**时仍按
  `WAF_RETRY × WAF_COOLDOWN`（3×300s，给瞬时抖动留机会）；**只要近期出现过失败**就降级为
  `1 × CONSEC_FAIL_COOLDOWN`（60s）。理由：300 秒长冷却已被 A 轮 14 只实测证伪。
- `should_halt(outcomes)` → 停止原因字符串：
  - `SESSION_DEAD`：连续 3 只全灭（会话被彻底拒的早期快停）；
  - `SESSION_DEGRADED`：窗口满 10 只且坏率 ≥ 30%（限流态）。
- 停轮后保留游标（`_save_cursor` 每只都写），下次运行从断点续跑。

**关键设计点：坏率而非连续失败。** 2026-11-13 实测撞墙后的形态是「失败-成功-失败」交替，
最长连续失败只有 1 只——任何「连续 N 只失败」判据都会被成功不断清零，护栏永不触发。
单测 `test_halt_on_alternating_failures_not_missed` 专门锁死这条。

**停轮代价**：撞墙后首批失败仍按 900 秒走，之后每只 ≤60 秒，窗口满即停 —— 上界约
`900 + 9×60 ≈ 15 分钟`（原形态 246 只 × 900s ≈ **61 小时**）。

**原地不改**：不动 `WAF_RETRY`/`WAF_COOLDOWN` 全局默认（首只仍等 15 分钟，属有意保守）；
保留比例阈值 `SUSTAINED_ABORT_PCT` 作为「真·拉黑」第二道闸。

验收：`tests/test_xq_cursor.py` 扩到 17 条（新增 10 条覆盖上述语义），
`news_collector/tests` 全量 **52 passed**。

### 16.5 处置（五要素已过批：老田选「停 + 单只探针验证」）

1. 命令：`Stop-ScheduledTask -TaskName XqTimelineDaily`（读回 `State=Ready`、
   `LastTaskResult=0x41306` 用户终止）；核对无残留 pythonw / Playwright 浏览器占 profile。
2. 探针：单只 `600802` → `+20`（见 16.2-4）。
3. 全量重跑：三次 `Start-ScheduledTask` 接力 —— 09:45:39（旧码，发现 400 复现）→
   09:47:33（带首版护栏）→ **10:23:04（续跑，11:05:17 跑完 666/666，新增 13,264 条，
   deferred=0，rc=0）**。三次都在人工在场时执行，**不是**已固化的调度策略。
4. 不可逆点：无（append-on-new，重复不落盘、已采内容不动）。
5. 回滚：再 `Stop-ScheduledTask` 即止；明日 07:00 照常触发。

**收口读数（11:08 `collect_sla.py`）**：雪球 `666/666`、单票 48h 无新增 `0/666`、
缺口 `0`、判定 GREEN；七源全 GREEN，RED/AMBER/NODATA 均为 0。

### 16.6 尚未解决的判断：撞墙触发条件未定量（待裁定，本席不擅动）

| 读数 | 值 |
|---|---|
| 当前 xq 队列（`load_queue` 并集） | **666** 只 |
| 队列跑完耗时（C 轮实测） | 约 **38 分钟**（10:23:04 → 11:01 到 593 只，11:05 收尾） |
| 撞墙位置 | A 轮第 401 只、B 轮第 401 只、C 轮**未撞墙**（666 只仅 1 次 JS 异常且恢复） |
| 已知 | 新会话（新进程）能恢复；300 秒冷却只能偶尔放行 1–2 只 |
| 未知（本席不给结论） | 触发是「会话内请求数」还是「时间窗内速率」抑或两者复合——三轮数据不足以定量 |

因此「队列超预算」这一说法**不成立**（C 轮一轮跑完 666 只是反证）；真问题变成了：
**什么条件下会被限流，以及被限流后应当降速还是停轮**。

本席可提供的判据（不作裁定）：
- A/B 两轮速率分别约 26 只/分、16 只/分，均在 400 只处封；C 轮约 20 只/分却未封 →
  单纯「速率」与「总数」都解释不了，**时段（早盘前 vs 盘中）可能是混淆变量**。
- 若要把这条变成可运营规则，最低成本的做法是**每只之间加固定间隔**（如 5 秒）
  并观察是否仍撞墙；这属行为变更，需批准。
- **不建议**把「撞墙就换会话」固化成策略：本轮三次重启是人工补数据，常态化等于绕过
  对方限流，存在账号/IP 处罚升级风险。降速自律与换会话规避是两件事。


### 16.7 本轮未做（明确列出）

- 未改任何调度触发点、未给 `--quota` 设生产值（16.6 待裁定）。
- 未改 `WAF_RETRY` / `WAF_COOLDOWN` / `SUSTAINED_ABORT_PCT` 全局默认。
- 未处理 registry_guard 报的两条 DRIFT（`cohorts` / `houfeng_candidates164` 桥文件
  `xq_include=false` 与注册表 `true` 相反）——按告警文本归属「桥生成器线」，
  且 `load_queue` 只读注册表，不影响当日采集；仅记录，不代改他线。
- 未调查互动易 13/28 成因（仍挂起）。

---

## 十七、2026-11-13 三项指示落实（降速 / 桥文件同源 / 巡检待批）

### 17.1 限流响应：降速自律（不再把「换会话」当策略）

老田裁定原文：「不把『撞墙就换会话』固化成策略——今天三次重启是人工补数据，
常态化等于绕过对方限流，与降速自律是两件事。」

落地到 `xq_stock_timeline.py`，与既有停轮判据配合成一条完整策略链：

| 触发 | 响应 | 常量 |
|---|---|---|
| 撞一次限流 | 额外间隔从 5 秒起 | `RATE_FLOOR=5.0` |
| 再撞 | 翻倍 5→10→20→30 | `RATE_CAP=30.0` |
| 连续成功 | 每只回调 1 秒，归零即恢复原速 | `RATE_DECAY=1.0` |
| 窗口坏率 ≥30%（10 只样本） | 停轮，保留游标，换时段再跑 | `SESSION_BAD_RATIO` |

纯函数 `next_interval(cur, waf_hit)`，主循环两处 sleep 叠加 `rate_sleep`，
档位变化打日志（`降速档位 5 → 10 秒/只`），便于事后核算当日实际压力。

**边界说明（不作结论）**：降速是自律，**不保证**限流立即解除——C 轮实测表明同一速率
在不同时段结果不同。它的价值是「不再加重」而非「必然恢复」；恢复与否由停轮+换时段解决。

单测 5 条：首撞取 floor / 逐次翻倍到 cap / 成功回调归零 / 不出现负值 /
浪费量被停轮窗口封顶（`10 只 × 34 秒 ≈ 5.7 分钟`，不是按队列长度线性拖长）。

### 17.2 桥文件开关与注册表同源（DRIFT 两条收口）

原状：`run_cohorts_intel.py` / `run_houfeng_intel.py` 每日重生成时把
`xq_include_cohorts` / `xq_include_candidates` 写回 `false`，注册表 11-11 R22 已是 `true`
→ `registry_guard` 每天报 DRIFT。

改法（取守卫建议①，不取②删字段——保留字段可让人在桥文件里直接看到状态）：

- 新增 `news_collector/reg_flag.py`：唯一取值入口 `xq_include(source_key)` /
  `xq_note(source_key)`，只读注册表；缺文件/坏 JSON/缺字段**一律回退默认值不抛异常**
  （日更主链不能因为一个说明字段挂掉）。
- 两个生成器改为 `reg_flag.xq_include(...)` 写入，说明文本也随开关变化，不再有第二处说法。
- 一次性脚本把两个 `target_queue_*.json` 与两个 watchlist 的说明字段对齐（跑完即回收）。

**复核**：`tools/registry_guard.py` → `检查 2 源，问题 0（漂移 0 / 缺字段 0 / 缺文件 0），正常 2`。
单测 9 条，含**防回退**断言「生成器源码里不得再出现写死开关的字面量」与
「对真实文件跑一次守卫，drift 必须为空」。

**顺带核实**：`preopen_check_0530` / `DailyOpsCheck` 的任务参数经字节级复核，
路径 `C:\项目\老田的AI助手\...` **无异常空格**（先前 PowerShell 显示为「老田 的AI助手」
属本机已知显示串位，非参数损坏）。两任务均于 11-13 07:19 注册、`rc=267011`（尚未运行过），
首次触发在 11-14 05:30。

### 17.3 本轮未做

- 未注册任何新计划任务（巡检任务仍待五要素批准，见 17.4 提议）。
- 未改 `daily_check_dispatch.py`（05:30 派单管线属他线，只读核对）。
- 未调 `--quota`、未改 `WAF_RETRY` / `WAF_COOLDOWN`。


## §18 游标域隔离：从「读侧校验」补到「写侧隔离」（2026-11-14 04:50）

### 18.1 缺陷（11-13 遗留，次日自查发现）

11-13 上线的断点游标存在一个**只在特定组合下才发作**的缺陷：

| 轮次 | 触发方式 | 域键 `scope\|sources` | 队列 |
|---|---|---|---|
| 07:00 全量轮 | `run_xq_daily.py` → `--queue all`（无 `--sources`） | `all\|` | 666 |
| 21:00 分片轮 | 自动化 `XqExpandedShard` → `--sources houfeng_candidates164,cohorts` | `all\|houfeng_candidates164,cohorts` | 246 |

原实现把游标存成**单文件单记录**，读取时用 `scope`/`sources` 做匹配校验。
校验只解决「读错域」——**解决不了「写覆盖」**：21:00 分片轮跑完一写，
07:00 全量轮的断点即被物理抹掉。

发作路径（不是每天都触发）：
1. 07:00 轮被强杀或护栏停轮 → 游标停在第 N 只（本次要保住的东西）；
2. 当日 21:00 分片轮正常跑完 → 覆盖游标；
3. 次日 07:00 域不匹配 → 从头开始 → **N+1..666 的尾部连续跳过**。

这正是 11-12「尾部 136 只 48h 未刷新」要治的病，在最关键的场景下失效。

### 18.2 补丁

`xq_stock_timeline.py` 改动仅两函数 + 一处调用：

- 新增 `_cursor_domain(scope, sources)` → `"<scope>|<sources>"`，`None` 与 `""` 同域。
- `_load_cursor(scope, sources)`：v2 读 `domains` 字典按域取；读到 v1 单记录则原样返回，
  由调用方既有校验兜底（不猜）。
- `_save_cursor(...)`：读旧文件取 `domains` 后**按域合并写**；v1 旧记录按其自身
  `scope`/`sources` 归位，升级当天断点不丢。原子写（tmp + fsync + `os.replace`）不变。
- 调用点：`_load_cursor()` → `_load_cursor(scope, src_key)`。

存储格式：
```json
{"v": 2,
 "domains": {"all|": {"last_code": "...", "queue_len": 666, ...},
             "all|houfeng_candidates164,cohorts": {"last_code": "301218", ...}},
 "updated_at": "..."}
```

### 18.3 验证

- `tests/test_xq_cursor.py` 新增 3 条：两域互不覆盖、v1 升级不丢断点、域键对 `None`/`""` 稳定。
- `news_collector/tests` 全量 **95 passed**。
- 只读干跑 `--queue all --pages 1 --plan-only`：
  `PLAN scope=all sources=- quota=0 cursor=on round=1 queue_len=666 起=000001`。
  从头部起是**正确结果**——现存 v1 记录属分片轮域，全量轮本就没有可用断点（正是被覆盖的证据）。
- 未做：未改主循环、未改护栏、未动 `--quota`、未改任何调度参数。


## §19 单票陈旧成因复核：48h 无新增是业务事实，不是采集缺陷（2026-11-14 04:46）

### 19.1 待查读数（11-14 04:22 SLA）

| 源 | 分母内 48h 无新增 | 占比 | 最老无新增 |
|---|---|---|---|
| 互动易 | 16 / 28 | 57.1% | 111.9h |
| 上证e互动 | 11 / 15 | 73.3% | 63.2h |
| 淘股吧 | 1 / 56 | 1.8% | 69.0h |
| 东财股吧 | 2 / 65 | 3.1% | 65.2h |
| 雪球 | 0 / 666 | 0.0% | 18.4h |

互动类两源与股吧类两源差**一个数量级**，需判「平台本来冷」还是「解析漏取」。

### 19.2 方法（只读探针 `collect_stale_probe.py`）

对每只票同时取两组数：
1. **现场重拉**该票的问答接口 → 条数 + 最新时间戳 + 「晚于本地最新」的条数；
2. **读本地 jsonl** → 条数 + 最新时间戳。

判定：接口与本地最新时间一致 → 未漏取；接口明显更新而本地没有 → 漏取。不写盘、不动队列。

### 19.3 实测（8 只）

| 源 | 代码 | 接口条数 | 接口最新 | 本地条数 | 本地最新 | 晚于本地 | 判定 |
|---|---|---|---|---|---|---|---|
| irm | 000543 | 19 | 11-04 14:05:28 | 20 | 11-04 14:05:28 | 0 | SAME |
| irm | 002381 | 2 | 11-08 14:00:39 | 4 | 11-08 14:00:39 | 0 | SAME |
| irm | 002901 | 13 | 11-07 11:10:26 | 14 | 11-07 11:10:26 | 0 | SAME |
| irm | 300143 | 31 | 11-10 15:06:41 | 32 | 11-10 15:06:41 | 0 | SAME |
| irm | 002044 | 10 | 11-13 09:52:07 | 9 | 11-08 11:37:36 | 1 | DIFF（时间差） |
| sse | 600422 | 2 | 10-29 10:54 | 2 | 10-29 10:54 | 0 | SAME |
| sse | 600851 | 1 | 10-30 15:21 | 1 | 10-30 15:21 | 0 | SAME |
| sse | 605003 | 1 | 11-02 08:59 | 1 | 11-02 08:59 | 0 | SAME |

唯一 DIFF 的解释：002044 那条新帖发于 **11-13 09:52:07**，而 irm 采集器当轮 09:50 已跑完
（`last_ok` 读数 11-13 09:50）——**采集之后 2 分钟才出现的帖子**，属正常时间差，非漏取。

### 19.4 佐证（不依赖单源）

- `002381` 同时出现在互动易与东财股吧的「无新增」样本；`605003` 同时出现在上证e互动与股吧。
  **两条完全独立的采集路径**给出同一结论 → 指向「票本身冷」而非某个采集器坏了。
- 上证e互动样本的本地历史总量仅 1–2 条：这些票在该平台本就极少被提问。

### 19.5 结论与边界

结论：在本轮 8 只样本范围内，48h 无新增**属业务事实**，采集链路未发现缺漏。

边界（不扩大解释）：
- 样本 8 只，覆盖率：互动易懂 28 只的 5 只、上证e互动 15 只的 3 只，**非全量核验**。
- 未核「本地条数多于接口」的一侧（000543/002381/002901/300143 均本地多 1–2 条）——
  可能来自接口翻页上限或平台侧删除，不影响「未漏取」判定，但成因未查。
- 本席只出读数与判据，**「互动易 57% 是否属正常水位」不归我裁定**。


## §20 巡检任务五要素提议（未注册，待批）

原 §17.3 引用「17.4 提议」而该节缺失，此处补齐。目的：把「采集新鲜度」纳入每日自动巡检，
不再依赖人工在盘前问一句「数据是否正常」。

### 20.1 与既有管线的关系（不重复建）

| 既有管线 | 触发 | 覆盖 | 缺口 |
|---|---|---|---|
| `preopen_check_0530` | 05:30 | `audit_preopen.py` 判「数据到没到」（ok/pending/missed） | 不看采集覆盖、不看单票陈旧 |
| `DailyOpsCheck` | 07:30 | 每日例行检查 | 同上 |
| **拟建** `CollectSlaDaily` | 09:40 | `collect_sla.py` 判「采集是否新鲜/覆盖是否缺」 | 依赖 09:30 轮采集完成 |

三者互补，**不重叠**。

### 20.2 五要素

1. **命令全文**
   `"C:\Program Files\Python311\python.exe" -X utf8 "C:\项目\老田的AI助手\news_collector\collect_sla.py" --json`
   行为：默认原子写 `news_collector\data\collect_sla.json`（另有 `collect_sla_state.json`
   记跨轮连续坏次数），`--json` 把同一份读数打到 stdout 供任务日志留痕。
   无 `--out` 参数（本轮核过 CLI，只有 `--json/--md/--no-state/--no-write/--thresholds/--now`）。
2. **影响面**：新增一个每日 09:40 的只读任务。**不改任何采集器、不改调度时序、不写队列**。
   资源占用：单次 <10 秒、无浏览器、无网络重拉（除必要补采外）。
3. **不可逆点**：无。任务可随时 `Unregister-ScheduledTask` 移除，产物仅一个 JSON。
4. **替代方案**：① 挂到既有 `DailyOpsCheck` 尾部（省一个任务，但耦合他线脚本）；
   ② 不建任务，每日由本席人工跑（现状）；③ 只写 JSON 不做判定（降级为纯记录）。
5. **回滚**：`Unregister-ScheduledTask -TaskName CollectSlaDaily` 即止。

### 20.3 告警口径（只报读数，不裁结论）

- RED 源数 > 0 或 `stale_hours > 48` → 日志写 `ALERT`，退出码仍为 0（遵循「退出码=跑完了吗」）。
- `gap_open > 0`（白名单以外的新缺口）→ 单独一行 `GAP_OPEN=<n>`，附前 10 个代码。
- 业务判定写 JSON 字段，不靠退出码承载。

**状态：未注册。等老田批。**


## §21 三项治理债处置（2026-11-14 05:30，老田批准）

### 21.1 僵尸自动化清理：53 条已删

**口径**：`status=ACTIVE` 且 `scheduleType=once` 且 `scheduledAt < 2026-11-14 05:00`。

**清单**：`C:/Users/t/WorkBuddy/2026-11-04-18-40-43/zombie_automations_20261114.md`
（工作区文件，不入库；53 条逐条含 id/名称/计划时间）。

**结果**：53 / 53 删除成功，每条均返回 `deletedAutomationId`。
**保留**：8 条未来一次性（11-14T20:35 起至 2027-01-08）、52 条 recurring ACTIVE、
5 条 PAUSED（状态已如实，不属僵尸）。

**为什么不算误删**：once 型过了计划时间不会再触发，删除不影响任何未来调度；
代价是不可逆，故先落清单再删。清理前 122 条 → 清理后 69 条。

### 21.2 电池策略：147 命中 → 75 已放开，23 待提权

| 类型 | 数量 | 结果 |
|---|---|---|
| 根路径 Interactive | 75 | **已放开**（同用户可改） |
| 根路径 S4U 等 | 23 | **拒绝访问**，需管理员 |
| `\Microsoft\Windows\...` 系统任务 | 49 | 未动（系统管理，改了会被还原） |

**读回**：546 任务 → 仍受限 87（其中根路径用户任务 23，含本席相关 `GuojiaDaily`）。

**提权脚本**：`tools/fix_battery_limits.ps1` + `tools/fix_battery_limits.cmd`
（右键 .cmd → 以管理员身份运行；脚本内按「根路径 + 仍受限」动态筛选，不写死名单，跑完自动读回）。
**基线备份**：`C:\aios\_battery_baseline.csv`（147 条原值，可反向恢复）。

### 21.3 `--quota`：判断为「不设」，理由如下

- quota 是**前置截断**，不是撞墙保护。撞墙由「窗口坏率停轮 + 降速」承担，两者职责不同。
- 实测：11-13 C 轮 666 只全程跑完、仅 1 次瞬时异常。硬限 400 会让 266 只无谓滞后。
- 反算：设 quota=480 时 666/480 ≈ 1.39，单票刷新周期从 1 天变 1.39 天，**比不限更差**。
- 结论：07:00 全量轮保持 **0（不限）**。若撞墙复现且降速无效，再降 quota 并同步降速。

### 21.4 最久未刷新优先：已实现并在 07:00 轮启用

新增 `order_by_staleness(codes, data_dir)`：按 `data/xq_timeline/{code}.jsonl` 的 mtime 升序，
**无文件（从未采过）排最前**，稳定排序（同 mtime 保原序）。新增 `--order mtime`；
该模式自动 `cursor_on=False`——排序本身即断点（没采到的票 mtime 仍旧，下轮自动排前），
两套顺序不打架。

`run_xq_daily.py` 已传 `--order mtime`，今日 07:00 首次生产生效。
**回滚**：删掉该参数即恢复「固定顺序 + 游标」。

**干跑读数**（05:25）：
`顺序=最久未刷新优先（666 只）；cursor=off 起=600929,600930,600962… 止=301165,301188,301218`
——起点正是 21:00 分片轮未覆盖的票，刚采过的 `301218` 已自动排到队尾。
这是「尾部饥饿」的直接反证：不需要游标，排序自己会纠正。

**验证**：`news_collector/tests` 99 passed（新增 4 条：无文件优先 / 同 mtime 稳定 /
不丢不重 / 采完自动后移）。

### 21.5 本轮未做

- 未动 `\Microsoft\Windows\...` 系统任务 49 条（改不动也无意义）。
- 未启用 quota（判断见 21.3）。
- 未改 21:00 分片轮（队列固定 246、能跑完，暂无排序必要）。

## §22 07:00 首次生产轮就绪核查（2026-11-14 05:26，只读）

### 22.1 就绪读数

| 项 | 读数 | 判据 |
|---|---|---|
| 任务状态 | `XqTimelineDaily` Ready，`NextRun=11-14 07:00:00`，`ExecutionTimeLimit=PT72H` | 调度在位 |
| 生产参数 | `run_xq_daily.py` 实传 `--queue all --pages 1 --order mtime` | 排序已接 |
| 干跑（`--plan-only`） | `scope=all quota=0 cursor=off queue_len=666 起=600929,600930,600962… 止=301165,301188,301218` | 顺序符合「最久未刷新」 |
| 落盘 mtime 分布 | 714 个 jsonl，最老 **32.2h**、中位 18.5h、最新 7.7h；`>24h` 16 只（2.2%）、`>36h` **0**、`>48h` **0** | 排序依据当前健康 |
| 21:00 分片轮 | `run_xq_daily_expanded.py` 传 `--sources ...`（不传 `--order`、用游标 v2 分域） | 与 07:00 轮无游标互写 |
| 采集相关任务唯一性 | 全机仅 `XqTimelineDaily` 执行 `run_xq*.py` | 07:00 无同源并发 |

### 22.2 提权脚本执行结果（未完成，卡在权限）

- 本会话 `IsAdmin=False`，直接执行 `fix_battery_limits.ps1` → `NOT ELEVATED - run as administrator. Aborting.` exit=1（脚本自带前置断言，未做任何修改）。
- 账号本身在 `BUILTIN\Administrators`，令牌为 `Medium Mandatory Level` → **右键「以管理员身份运行」可行**，需 UAC 确认一次。
- 根路径仍受限 **24 条**（比 05:00 统计多 1，统计时点差异）：S4U 型 17 条（`GuojiaDaily`、`BootStackCheck`、`ChanlunLearnDaily`、`Legion*` 5 条、`ResticTrialDaily`、`TushareIncrementGuard`、`KgLeiden`、`CzscShadowDaily`、`CuratedDailyRefresh`、`A0Patrol`、`A3RotationCanary`、`datamart_api_keepalive`、`DbDumpDaily`(Interactive)、`AI工作站调优回测批次`、`PhoenixMaintenance`）+ Interactive 型 7 条（`AlertPushWatchdog`、`qengine_daily_0820/2100/2340`、`WpsUpdate*` 2 条、`DbDumpDaily`）。
- 已放开 459 条（`DisallowStart` 与 `StopIfGoing` 双关）；`\Microsoft\Windows\...` 系统任务 49 条未动。基线 `C:\aios\_battery_baseline.csv` 可反向恢复。

### 22.3 新发现：`--order mtime` 的排序依据不是「上次尝试时间」（B 级，未发作）

排序键取本地 jsonl 的 **mtime = 内容最后一次新增的时间**，而不是「最后一次采集尝试的时间」。雪球 collector 是 append-on-new（无新帖不写盘），因此：

- 若某票连续多日**确实没有新帖**，采过之后 mtime 不变 → 下次仍是「最老」→ **继续排队首**；
- 冷票累积后会在队首形成固定占位，每轮被白采、挤掉后面真正待刷新的标的——与 11-12「尾部饥饿」是同一枚硬币的反面（这次是**头部拥堵**）。
- 当前未发作：`>36h 未变 = 0`，714 个文件全在 32h 内更新过。

判据（达到任一即需改）：① `>36h 未变` 文件数连续 3 日 > 队列 5%；② 连续两轮队首 10 只完全相同。
治本方案：新增 attempt ledger（`_attempt.json`，记录每只最后一次尝试时间戳），排序键改读 ledger，缺记录回退 mtime。**不在今日 07:00 前改**（距触发 1.5 小时，新机制需测试，风险大于收益）。

### 22.4 依赖时序风险：08:00 情绪快照 vs 雪球完成时刻

`SentimentSnapshot` 固定 08:00 触发，读当日雪球产出。正常档（2–4 秒/只）666 只约 **37 分钟**，07:37 完成，余量 23 分钟；一旦触发降速档并维持（8 秒/只）→ 约 **89 分钟**，08:29 完成，**跨过 08:00**，快照将读到半截雪球数据。

本席不裁调度，仅列选项供裁定：
- A：雪球 07:00 提前至 06:30（周末/工作日一致）；
- B：`SentimentSnapshot` 后移至 09:00；
- C：接受低频不完整，快照侧标注「雪球未完成」标记；
- D：不动（昨日 C 轮 666 只仅 1 次瞬时异常，降速概率低）。

### 22.5 07:00 后要看的三个点（本席自查口径）

1. 日志首行起点是否为 `600929`（陈旧第一）；
2. 若撞限流，是否输出「降速档位」并在窗口坏率达 30% 时 `SESSION_DEGRADED/DEAD` 停轮，而非 900 秒/只空转；
3. 完成时刻是否早于 08:00（对照 22.4）。

## §23 07:00 首轮实战复盘 + 采集审批分级（2026-11-14 20:00）

### 23.1 生产读数（对照 §22.5 三点）

| 自查点 | 读数 | 判定 |
|---|---|---|
| 起点是否落在陈旧第一 | `600929`（干跑即此码） | ✅ 符合预期 |
| 撞限流后的行为 | 降速 `0→5→10→20` 秒档生效；停轮 `SESSION_DEAD`；**无 900 秒/只空转** | ✅ 护栏起作用 |
| 完成时刻 | 07:00:01 启动 → 07:32 结束 | ✅ 早于 08:00 |

但覆盖面不合格：

| 项 | 读数 |
|---|---|
| 实际处理 | **400/666（60.1%）**，未采 **266 只** |
| 新增 | 7,927 条 |
| 停轮依据 | **连续 3 只整只被拒**（`600713` / `600718` / `600724`），非窗口坏率 |
| defer | 3 只（`600718` / `600724` + 1） |
| 退出码 | `rc=2`（业务阻断） |
| 冷却代价 | 300×3 + 60 + 60 ≈ **17 分钟**（对比 11-13 的 15 小时） |

### 23.2 判读修正：降速挡不住「请求数预算」型限流

四次独立观测的撞墙位置：

| 轮次 | 起点时刻 | 撞墙位置 | 耗时到撞墙 |
|---|---|---|---|
| 11-13 A | 07:00:01 | 第 **401** 只（`600802`） | 约 15 分钟 |
| 11-13 B | 09:45:39 | 第 **401** 只（`600926`） | 约 25 分钟 |
| 11-14 | 07:00:01 | 第 **400** 只（`600713` 起） | 含 17 分钟冷却 |
| 11-13 C | 10:23:04 | **666 只全跑完，仅 1 次瞬时异常** | — |

撞墙点与**请求数**高度吻合、与**耗时无关**（15 分钟与 25 分钟都停在第 401 次）。这支持「单会话约 400 次请求配额」的假设；降速只把撞墙时刻往后退，**不增加预算**，所以它不是解药。

C 轮（666 全跑完）与该假设冲突，证据不足以解释——**并列记录，不作结论**。可能变量：时段、上游缓存、IP 池轮换。需再攒样本。

**结构结论（只陈述算术，不裁方案）**：队列 666 > 单会话预算 ~400 → 任何单轮都只能覆盖约 60%。要全队列当日覆盖，必须**一轮拆两段**（每段 ≤400）。

### 23.3 今日处置：分时接力（不是撞墙提速重冲）

- 19:52:30 手动触发第二轮（新会话）；按 mtime 排序自动从 `600738` 起（紧接 07:00 轮停下的 `600724` 之后），`deferred 3 只` 自动前置重试。
- 性质界定：这是把 §22 的「分时两轮」**手工执行一次**，用于当日补数据；与「撞墙立刻换会话按原速重冲」不同——后者是规避，前者是分时段接力。
- 未改任何调度参数。

**实测结果（第二轮 19:52:30 → 20:35:25）**：

| 项 | 读数 |
|---|---|
| 处理 | **427/666**，新增 **8,443** 条 |
| 撞墙点 | 第 425 只 `000815` 起（与 07:00 轮的 400 只同量级，再次支持「~400 次/会话」） |
| 停轮 | `SESSION_DEAD`，**依据=连续 3 只整只被拒**（`000815`/`000823`/`000829`），`rc=2` |
| 新文案 | 首次生效，已明确分开「依据」「已处理 427/666」「未启用游标」三件事 |
| deferred | 3 只（`000815`/`000823`/`000829`），明早 07:00 自动前置重试 |

**当日覆盖核验（20:36）**：`data/xq_timeline` 714 个文件，中位 mtime **0.6h**；队列内 666 只**全部 ≤12h**（其中 >12h 的 290 只均为 07:00 轮所采）；`>24h` 仅 **48 只**，恰好等于 714−666 = 48，即**队列外历史文件**，不在采集范围。→ **今日 666/666 覆盖达成**，靠 07:00（400）+ 19:52（427，含重叠）两段接力完成。

算术提示：单轮 400 只 × 每日 1 轮 → 单票刷新周期 666/400 ≈ **1.67 天**；要当日全覆盖，两段是下限。

### 23.4 代码改动（1 处，仅文案）

停轮日志原写「近 20 只坏率 15%（3/20 只被拒）——冷却自愈无效，游标已落在 600724 之后」，两处误导：
1. 把**窗口读数**当成**触发依据**播报（实际依据是连续 3 只全灭，15% 远低于 30% 阈值）；
2. `--order mtime` 模式 `cursor=off`，却声称「游标已落在……之后」——**未写游标却说写了**。

改为分别输出「依据=连续 N 只整只被拒 / 窗口坏率 X%≥Y%」与「已处理 i/n」，并按 `cursor_on` 分别说明续跑路径。阈值、降速衰减、`next_interval` 均未改（理由见 23.2：对预算型限流改衰减无效，不做无效改动）。验收：`news_collector/tests` 99 passed。

### 23.5 SLA 的第三次同类盲区

今日 SLA 判雪球 **GREEN / 666-666 / 48h 陈旧 0**——三条全绿，而事实是**今日 40% 未刷新**：

- 覆盖 666/666 统计的是「有文件」，不是「今日采过」；
- 48h 陈旧统计的是「昨日采过」，266 只昨日采过故为 0。

这是「源级绿灯掩盖单票状态」的第三次发作（前两次：11-12 尾部 136 只、11-13 单票陈旧维度）。**待办**：SLA 增 24h 刷新率列（`fresh_24h_pct`），判定仍按原规则，仅作旁证栏。归 L1（本席自主）。

### 23.6 待马超配准：分片调度（五要素）

> **已被 §24 取代**（2026-11-14 21:30）：正式呈批件为 `CR-20261114-01`，且排序参数已由
> `--order mtime` 改为 `--order attempt`、第二段改用 `--quota 300` 精确覆盖余量。
> 本节仅留作当时的推理记录。

按老田 2026-11-14 新规，属 L2。

1. **命令全文**：新增触发点（候选 12:30 或 22:30），执行同一条
   `python.exe -X utf8 news_collector\run_xq_daily.py`（`--queue all --pages 1 --order mtime`）。
   因 `--order mtime` 无游标，第二轮自动从「mtime 未变」的票起，**无需新增参数**。
2. **影响面**：仅多一次雪球会话；不新增登录、不改队列、不改 07:00 轮参数。
3. **不可逆点**：无（append-on-new，重复不落盘）。
4. **替代方案**：① 把队列砍到 ≤400（与扩源方向相反）；② 维持现状，接受单票约 2 日一更；
   ③ 拆成 07:00 / 12:30 / 22:30 三段各 ≤400（覆盖最稳，占用三个时段）。
5. **回滚**：`Unregister-ScheduledTask` 或禁用该触发器，07:00 轮不受影响。

### 23.7 采集审批分级（老田 2026-11-14 定版）

老田原话：「数据的采集的时候我觉得不要到人为了，直接马超配准了就可以采了。」

| 级 | 谁定 | 事项 |
|---|---|---|
| **L1 本席自主** | 不需报批 | 采集器缺陷修复、护栏与参数调优、只读探针、缺口复核与白名单、补采重跑、单测、台账文档 |
| **L2 马超配准** | INTEL0 马超 | 采集类计划任务注册/改时间/改参数/停用、队列扩缩（含 `xq_include_*`）、SLA/停轮/降速阈值、新源接入、分母口径、分片调度 |
| **L3 老田保留** | 老田 | 动生产服务器（内网）、系统级安全配置（防火墙/服务/代理/账户）、删数据、对外发布、资金合规 |

**纪律不变，只换审批人**：L2 仍须五要素，由马超裁定，不再排队等老田。「免人工审批」≠「免留痕」——L1/L2 一律落本台账，L2 记配准人与时间。
同步修订：§20 的 `CollectSlaDaily` 巡检任务注册，审批人由老田改为**马超**（L2）。

## §24 马超配准通道 + 尝试账本排序（2026-11-14 21:30）

### 24.1 通道：`collect_change.py` + `collect_change_request.json`

**问题**：§23.7 把 L2 审批人换成马超，但马超不在本席会话里，没有实时消息通道。若不落
一个可读可写的载体，「配准即执行」会退化成「口头同意、留痕缺失」。

**方案**：呈批件即通道。本席用 CLI 建呈批件（含五要素），马超会话用同一 CLI 读待批、
写配准结论，本席再按结论执行。状态机由代码守，不靠记性：

```
pending ──approved──> approved ──applied──> applied ──rolled_back──> rolled_back
   └────rejected────> rejected（终态）        approved ──rejected──> rejected
```

三道 fail-closed 守卫（单测锁死）：
1. **五要素缺任一项 → 不许配准**（驳回不受限，驳回不需要论证可行性）；
2. **配准人为空 → 不许配准**（留痕必须能追到人）；
3. **状态迁移单向**：`pending` 不得直接 `applied`（跳过配准即执行）、`applied` 不得回
   `approved`、`rejected`/`rolled_back` 为终态。

副作用防护：呈批件原子写（tmp+fsync+`os.replace`），结构异常即拒绝加载。

命令（马超侧同样适用）：

```bash
python collect_change.py list [--status pending]
python collect_change.py show CR-20261114-01
python collect_change.py decide CR-20261114-01 --status approved --by "INTEL0 马超" --note "..."
python collect_change.py apply  CR-20261114-01 --note "已注册，读回核对通过"
python collect_change.py rollback CR-20261114-01 --note "..."
```

退出码：0 正常；2 业务拒绝（五要素不全/迁移非法）；1 运行错误。

### 24.2 已呈批两件（均 pending，等马超）

| id | 事项 | 关键读数 |
|---|---|---|
| `CR-20261114-01` | 雪球分片调度：新增 12:30 第二段 | 07:00 轮 400/666 上限；第二段 `--quota 300` 起点=266 未采 + 3 deferred；请求量 400→700/日；刷新周期 1.67 天 → 1.0 天 |
| `CR-20261114-02` | 08:00 情绪快照与雪球完成时刻的依赖 | A 雪球提前 06:30 / B 快照后移 09:00 / C 快照侧标 incomplete / D 不动；取舍取决于「08:00–09:00 有无下游读情绪卡」，本席不掌握调用清单 |

CR-02 若不裁决，A 方案（提前 06:30）会与 CR-01 的第二段叠：前者把 07:00 轮提前，后者
补余量，两者不冲突但同属时间轴变更，建议一并裁定。

**本席未做**：未注册任何任务、未改任何触发器、未改队列。呈批件仅描述命令全文。

### 24.3 尝试账本：`--order attempt`（L1，已生效）

**缺陷**（§22.3 提出，本日处置）：排序键是落盘文件 mtime，而雪球是追加写——某票确实
多日无新帖时 mtime 不动，次日仍是最老 → 继续排队首、每轮白采。冷票一多就在队首固定
占位，把真正待刷新的挤到后面。**这是 11-12「尾部饥饿」的镜像缺陷（头部拥堵）。**

**改动**（`xq_stock_timeline.py`）：
- 新增 `_attempt.json` 账本 `{code: epoch}`，`_load_attempt` / `_save_attempt`（原子写）/
  `_record_attempt`（就地登记，返回是否首次入账）；
- `order_by_staleness(codes, data_dir=None, attempt=None)` 排序键：**账本 > mtime > 0.0**。
  账本为空时行为与旧版逐字节一致（单测锁死，防回退时行为漂移）；
- `--order` 增 `attempt`，`mtime` 保留为旧口径（排障对照）；`run_xq_daily.py` 已切到 `attempt`；
- **只在采成功时登记**：整只被拒的码保持旧时间戳，下轮仍排最前，保证重试优先。

**验证**：`news_collector/tests` **127 passed**（新增 attempt 6 条、参数透传 3 条）。
干跑：`666 只，其中 0 只有尝试记录、666 只回退 mtime`——账本首次建立于 11-15 07:00。

**未做**：未给旧数据补账本（补了等于伪造尝试时间，会掩盖真实陈旧度；回退 mtime 是诚实
的降级）；未改 21:00 分片轮（其队列 246 只、单轮跑得完，无需排序）。

### 24.4 `run_xq_daily.py` 参数透传白名单（L1，为 CR-01 铺路）

第二段需要「同一脚本只改本轮规模」，故加透传：只放行 `--quota N` / `--quota=N`，
其它一律丢弃并打印 WARN。

**为什么必须白名单**：调度侧多写一个参数就可能改变语义——`--codes` 会把全量轮变成
单票探针轮、`--no-cursor` 会破坏断点语义。放行全部参数的风险远大于收益。

**测试方式**：`run_xq_daily.py` 是脚本，import 即执行采集（会开浏览器、写数据），故用
AST 抽出 `_extra_ok` 单函数编译执行，不碰模块顶层——这是「可测」与「不触发副作用」
之间的取舍。

### 24.5 本轮未做

- 未批注/未经马超配准就执行任何调度变更（CR-01/02 全 pending）；
- 未给 `_attempt.json` 预置历史（理由见 24.3）；
- 未动 `--quota` 生产值（07:00 主轮仍 0=不限，撞墙由护栏兜；第二段才用 300）；
- 未动电池策略剩余 24 条（需管理员权限，脚本已就位待老田右键运行）。






### 25. INTEL0 配准批复（2026-11-15 09:43 晨会，马超 → A0 汇报后落字）

**依据**：老田 2026-11-14 授权「情报板块采集类计划任务注册／改时间／改参数／停用，与采集队列扩缩、
SLA／停轮／降速阈值，改由马超（INTEL0）配准即可执行」；本批复只作配准，**不改 org.json、不扩裁决权**。
适用顺序 `org.json > resume.yaml > 已签批 SOP > 草案／工单`；业务终裁、成品签发、例外豁免仍归 A0／老田，
方法／质量复核归 QA1。本席职权止于受理登记、分派、催办、台账与本节采集类配准。
请求单原件 `collect_change_request.json` 两条 `status=pending` **保留不改**（档案 append-only），批复效力以本节为准。

#### CR-20261114-01（雪球 12:30 第二段）→ **准，附 4 条件**

1. 只新增调度项；脚本与 07:00 主轮参数零改动，第二段按单 `--quota 300`（`--queue all --pages 1 --order attempt`）。
2. 首三日为观察期：每日回报覆盖只数、`SESSION_DEAD` 撞墙点、HTTP 401／403 计数，回报落本节后续 append。
3. 一旦出现 401／403 或 403 持续升级，**立即停用第二段并回退**（按请求单 rollback），同日上报 INTEL0；不得先跑满再报。
4. 单日请求量 400→700 的账号／IP 风控代价已随批复呈 A0 知悉；**本批复不代替 RightsGate 审批**，
   不开启任何真实 fetcher 的 `XQ_LIVE_ARMED`，不触碰 `intel_collector` 三方向待签材料。

#### CR-20261114-02（08:00 情绪快照依赖四选一）→ **准 C；A／B 暂缓，D 不取**

**理由**：请求单自述「不掌握 08:00–09:00 下游调用清单，该点须配准人判断」。A／B 都建立在这个未知前提上，
在缺证据时改调度 = 拿假设当事实；C 把完整性问题转成显式 `incomplete` 标注，符合「失败可见、不把未观测写成无事」，
零调度风险、可逆、不动下游。D 把偶发读半截数据留成隐性风险，不取。

**条件**：① 只改输出口径（当日雪球轮未跑完 → 情绪卡标 `incomplete`，带 `as_of` 与判据），**不动任何触发器**；
② 马良同日补《08:00–09:00 情绪卡下游调用清单》，清单出来后再呈 A0 定是否叠加 A／B；
③ `incomplete` 不得写成 `ok`，不得回填旧数据冒充当日。

#### 边界（批复 ≠ 已执行）

本席只配准，**不代注册计划任务、不改脚本、不启停服务、不碰生产数据根、不删任何文件**。
执行由 OSINT1 马良按晨会派单落，执行回执另 append 本节；A0 若否决，停用即回退，数据无需回滚（append-on-new）。
留痕：本节即配准回执；未提交、未推送，入库时机由 A0 定。

---

### 26. INTEL0 派工登记（2026-11-15 10:08，按「按计划去实施」指令下发）

依据：本节承接晨会汇报 §六「在手事项分解派单（10 件）」与 §25 配准批复。
老田指令「按计划去实施」→ INTEL0 行使**受理／分派／催办／台账**本职，正式下发派工单。
纪律：**派单 ≠ 已完成，配准 ≠ 已执行，上行 ≠ 裁决**；本节只记派发与状态，执行回执由承办席另 append。

#### 26.1 派工单（W1-W10，状态为派发时点快照）

| # | 承办 | 指令 | 交付物 | 时限 | 派发时状态 |
|---|---|---|---|---|---|
| W1 | 马良 OSINT1 | CR-01 雪球 12:30 第二段（§25 准，附 4 条件） | 调度回执 + 首三日观测（覆盖／撞墙点／401／403） | 11-15 内注册，11-18 出三日报告 | 已配准，待执行回执；见 401／403 立即停并回退 |
| W2 | 马良 OSINT1 | CR-02 准 C；A／B 暂缓，D 不取 | `incomplete` 标注实现 + 《08:00-09:00 情绪卡下游调用清单》 | 11-15 EOD | 已配准，待执行回执；**不动任何触发器** |
| W3 | 马良 OSINT1（协同 L6 A 组） | T-192 tushare 频控补确认（裁定 #10） | 频控确认 JSON + 首跑时间建议 | 10:30 周任务落地后 | **待触发**：截至派发 `ann_sync-2026-11-15.log` 尚未生成，10:30 未到点，不伪造 `ok` |
| W4 | 马良／采集工位 | 19 天缺失交易日清单转执行 | 逐日 `tools/top_list_fetch.py --day` 回执 | 交采集工位排期 | 清单已交付；本轮 R1 实测补证：扩到 10-01 可把样本从 8 日扩大，交采集工位；**不改脚本、不落盘** |
| W5 | 马谡 ANALYST1 | 补 S2／S3 回执 + 第七课（5H 卡／五段式／反证栏） | S4 复盘输入包（只写 `pending`） | 11-15 EOD | 未见回执，**S4 在证据齐后才算数**；终审仍 A0；S5 11-16 推广不抢跑 |
| W6 | 马谡 ANALYST1 | 632 code 存量消化 + track record 首批回填 | 消化台账 + 首批双维评级 | 本周批 | 工具权限 `tools=[]` 待 A0 另授工单，**本席不擅自开口子** |
| W7 | 马钧 MAJUN1 | 催办 KG 情绪只读查询需求 | 查询清单 + 字段 + 粒度，**不写代码** | 11-16 12:00 前 | 催办已发；未签收不建表，与 W9 闸门联动 |
| W8 | 马钧 MAJUN1 | 核对 KG 更新通知协议 v1.3 落地 | 落地回执 | 11-16 前 | 催办已发；禁止默认接受／提前自动结算 |
| W9 | 马岱 MADAI1（协作单） | 情绪产品补签催办，不开替代签收例外 | 催办留痕 + 签收回复 | 11-16 12:00 前 | **编外，走协作单不走指挥链**；不代签、不倒填、不建表 |
| W10 | INTEL0 本人 | A1-A6 呈裁件备料，一次呈 A0 | 六件待裁清单 + 建议 + 实证 | 今日上午 | **本轮已完成 A1 实证备料**（见 26.2）；其余 5 件仍待 A0 |

> 组织口径不变：dept=intel 11 席**无 MADAI1**，马岱只能发协作单；编内 `MAJUN1` 与数据智能部 `DEV1` 马钧不是同一人，不得串位。

#### 26.2 本轮新增实证：判定闸 R1「30% 门槛」即时实测（INTEL0 受理，不作裁决）

按 FINAL v1 v1.3 / 判定闸报告 §R1 **写死的分子分母**，用本地三表只读实测 8 个交易日，
结果随 `tmp/intel0_r1_coverage_20261115.json` 留档：

| 口径 | n | 均值 | 落档 |
|---|---|---|---|
| A 字面写死：全表分母 + `hm_detail[trade_date-1]`（报告原文） | 7 | 7.2% | **fail** |
| B 全表分母 + 同日席位 | 8 | 32.7% | **pass** |
| C 仅涨停档 U 分母 + T-1 席位 | 7 | 7.0% | **fail** |
| D 仅涨停档 U 分母 + 同日席位 | 8 | 37.1% | **pass** |

- 分母数据**本地齐备**：`limit_list_d` 日分片 8 日逐格可复现，
  `limit` 枚举 {"Z": 239, "U": 639, "D": 50}（U 涨停／D 跌停／Z 炸板）。
  判定闸报告 §R1 与 FINAL v1 §566 的「本地无表／待 T-192 首跑后实测」属**事实误记**；T-192 采公告 `anns_d`，不产涨停池。
- **决定落档的是 `trade_date-1` 席位对齐，不是分母取不取 U**：
  字面口径 A/C 为 7.2%／7.0% → `fail`（<20% 撤席位维）；
  同日对齐 B/D 为 32.7%／37.1% → `pass`（≥30%）；差 4-5 倍，结论相反。
- 独立单日复算（11-11）与探针逐值吻合，排除脚本 bug；首日 11-02 的前一交易日 10-30 本地无快照，字面口径 n=7 记 `-`，**缺失可见不硬凑**。

#### 26.3 呈 A0／QA1 待定义（本席不裁定）

| # | 事项 | 状态变化 | 仍归谁 |
|---|---|---|---|
| A1 | 分母与 T-192 解绑 | **实测已完成**；从「待甲乙裁定」推进到「实证已出，待口径定义落字」 | A0（口径裁决）／QA1（方法复核） |
| A1-a | ① 分母是否只取涨停档 U | 四种组合均给出，未选定 | A0／QA1 |
| A1-b | ② `hm_detail[trade_date-1]` 对齐方式 | **P0**：字面 7.2% `fail` vs 同日 32.7-37.1% `pass`；按判例 #38 不为让指标变绿改口径，不自行选定 | A0／QA1 |
| A1-c | FINAL v1 §566 与判定闸报告 §R1 口径回写 | **未改**：两份均经老田签字生效，回写属成品签发层 | A0 一句话落字 |
| A2 | 情绪产品补签 | 催办继续，**不开替代签收例外**；F-02／F-08 与 11-16 12:00 建表闸门继续锁死 | A0 |
| A3 | SOP v0.2 QA1 会签 | 不阻塞，催办已发 | QA1 |
| A4 | PIR-001／002 内容定义 | 只登记覆盖状态，未定义内容 | A0 |
| A5 | `intel_collector` 三方向签字 | 只备料，不绕闸 | A0 + RightsGate |
| A6 | 删除类重大动作 | **本轮零涉及**，按令不碰 | A0 保留 |

#### 边界（本节即下发回执）

- 本节为 **append-only 纯追加**；未删任何文件、未改 §25 批复与历史行、未改请求单原件、未改 `org.json`／`resume.yaml`。
- 未改判定闸报告与 FINAL v1、未改任何计划任务／脚本／采集器、未启停服务、未调 API、未读凭据、未代签、未倒填、未建表、未提交未推送。
- 执行回执由各承办席另 append 本节之后；超期 24h 由 INTEL0 催办。
- 全部统计值由只读探针运行时注入，日期／章节号由系统时间与现有台账派生。

---

### 27. INTEL0 通知链路清账与即时回执（2026-11-15 10:32）

指令：老田「把所有链路、所有要发给你的通知及时回复」→ INTEL0 行使**受理／登记／回执／催办／台账**本职。
依据：SOP v0.2 §三红线、§五 S0 受理、§七真回执；KG 通知协议 v1.3；`org.json`「只受理不裁决」。
**本节即回执**，逐条给状态；不代签、不代发、不代裁、不改任何系统配置。

#### 27.1 入向链路盘点（五条，实测非设想）

| 链 | 载体 | 派发时状态 | 闭环 |
|---|---|---|---|
| A 满宠情报收件箱 | `agent_platform/intel_inbox/` md+json 成对 + meta sha256 + ACK + jsonl 台账 | 今日 manchong_brief_2026-11-15 `accept`，inbox 待受理 0、rejected 0 | **最完整**：送达／哈希／幂等／拒绝可见 |
| B KG 更新通知 | 协议 v1.3（L1/L2/L3、六类订阅席、逐席显式 ACK） | 当前**无新 L2/L3 待回执**；历史 #1/#2/#3 按 v1.3 标注不再作先例 | 有 |
| C 田丰／军团晨报 | `deliverables/daily_briefs/2026-11-15/rollup.json` → `a0_briefing.md`，17:35 push，P0 immediate／P1 1h／P2 daily | 12/17 汇报、failed 5；**全为通道故障**，非席位不干活 | **只有汇总、无决策回执位** |
| D 采集告警 outbox | `news_collector/outbox/pending_*` → `.sent.*` | 今日已发 55 条；`pending_card.json` 待发、`ai_brief.json` `ok=false` 但 `pushed=true`（降级推送） | **触达回执缺失** |
| E 审批／桥接 | `work/approvals/approvals.json`、`agent_platform/bridge_inbox/` | approvals.json **不存在（无待批件）**；bridge inbox 堆积 248、processed 仅 4 且最后 2026-11-07 23:31 | **消费侧断** |

#### 27.2 逐条回执（本节即回复，状态按 SOP 真回执口径）

| # | 通知／事项 | INTEL0 回执 | 后续 |
|---|---|---|---|
| N1 | 满宠 `manchong_brief_2026-11-15` | **已受理**，`ack=received`、`duplicate=false`、双文件 sha256 校验通过。依 SOP 原则 3，**received 只证明送达与完整性，不等于同意内容** | 内容三项见 N2 |
| N2 | 满宠汇报内三项：治理队列 `pending=5` 且近 1h 打标 0、`GubaCrawl failed`、5 个 Kg 任务 success | ① 停摆迹象记 `partial`、不判停摆，催 DATA1 复核；② `GubaCrawl` 属采集工位，转 W 催办、不代修；③ 5 任务 success 归档留痕 | 各归口回执另 append |
| N3 | 田丰晨报中「情报部 INTEL0＝昨天完成：无」 | **更正，不认可该记载**：近 11 份晨报中 INTEL0 被记为「无」共 9 次；11-14 本线实有交付（配准批复、FINAL v1 收口、判定闸报告、`31af8f0cf`／`fc6cf03ed` 提交）。**不改田丰文件**，只在本台账留更正并上行 A0 | 见 27.4 |
| N4 | `outbox/pending_card.json`（10:07 未发，含 JEV 判定 0 利多／1 利空／1 中性） | **登记待发、不代发**；`ai_brief.json` `ok=false` 但已 `pushed=true` → 按失败可见记 `incomplete`，不把降级内容冒充 AI 结论 | 待采集工位确认触达 |
| N5 | 今日 55 条 `.sent.*` 告警 | 催**触达回执**：`.sent` 只证明写出，不证明送达；未拿到 delivered 一律记 `partial` | 不代收、不伪造 `ok` |
| N6 | KG 通知 L2/L3 | **当前无待回执件**；历史三行按 v1.3「显式 ACK 唯一有效」不再作先例引用 | 新事件一律逐席 ACK |
| N7 | PIR-001／002 | 仍为格式占位，**内容定义归 A0**；本席只登记覆盖状态、催办，不代裁 | 呈 A0 |
| N8 | 审批队列 | `approvals.json` 不存在 → **本轮无需批复件**；不新建空队列、不造审批记录 | 无 |
| N9 | 晨报 failed 5 席 | 逐席核为**通道故障**：DEV3=RuntimeError:{'code': 'llm_error',；ACAD1=RuntimeError:{'code': 'llm_error',；AI1=RuntimeError:{'code': 'channel_not；HUANGZHONG=RuntimeError:{'code': 'llm_error',；ZHAOYUN=RuntimeError:{'code': 'llm_error',。按「失败可见」催归口修通道，不据此判席位不干活；**不重启服务、不改系统配置**（归老田） | 归口回执 |

#### 27.3 W3 新增实证（10:32 落地，频控收口）

`ann_sync-2026-11-15.log` 实测：

```
[20261115-103000] START ann_history_sync --apply
[20261115-103000] END rc=0
```

- **回执**：`rc=0`、限频痕迹 **无**（日志中无 429／rate／限频／FAIL／Traceback）。
- W3 由 `wait` 收为 **`partial`**：本轮无触发限频、周任务正常结束；但日志仅两行、无账号实时配额数据，**不写「配额足够」、不判 `ok`**。
- 首跑时间属裁定与采集配置，本席不代裁、不改调度。

#### 27.4 更正记录（append-only，不改写任何历史行）

- 事实：田丰晨报链路连续把 INTEL0 记为「昨天完成：无」，与本线台账／git 提交可见产出不符。
- 定性：**跨链可见性断裂**，不是「情报部没干活」，也不是「晨报造假」——两条链各写各的账，无统一收件口与对账。
- 处置：本台账留更正 + 上行 A0；**不改田丰 `a0_briefing.md`／`rollup.json`**，不倒填、不追认历史状态。
- 建议（属设计，不擅自实施）：每日一行跨链对账索引，把军情司台账条目映射进晨报汇总口径。

#### 27.5 边界

- 本节纯追加；未删任何文件、未改历史行、未改任何通知队列／收件箱实体、未代发通知、未代收 ACK、未代签。
- 未启停服务、未改计划任务／代理／防火墙、未调 API、未读凭据、未提交未推送。
- 事实值（12/17、failed 5、bridge 248/4、sent 55、mis 9）均由只读探针运行时注入，未经人工誊写。


### 28. INTEL0 三项钩子执行留痕（2026-11-15 11:08，老田「执行 ①②③」）

#### 28.1 ① A1-a／A1-b 口径：矩阵补全、事实层回写、裁决层待 A0 一句话

- 补算发现「席位对齐方向」矛盾：FINAL v1 §556 = S=D+1（席位 T+1 覆盖前一日涨停），§565／研究报告 §84 = S=D-1，§96 标「已解决」但方向未对齐。上一轮四口径矩阵漏了业务最自洽的 S=D+1 档，本轮补齐为六口径。
- 全表×S=D-1：n=7，均值 7.2%，fail。
- 全表×S=D：n=8，均值 32.7%，pass。
- 全表×S=D+1：n=7，均值 10.8%，fail。
- 仅U×S=D-1：n=7，均值 7.0%，fail。
- 仅U×S=D：n=8，均值 37.1%，pass。
- 仅U×S=D+1：n=7，均值 14.4%，fail。
- 关键：两种有依据口径均 fail；唯一 pass 的同日档 hit/num=99.6%／100.0% 属同源自证，不成立。
- 已回写（事实层）：FINAL v1 §566 行末指针 + 新增文末「附：R1 30% 门槛实测补记」；判定闸报告 R1 标题下指针 + 新增附记。**口径定义、verdict、签字结论一律未代裁**。
- 待 A0：A1-a（分母全表／仅 U）与 A1-b（对齐方向）一句话落字 + QA1 会签；拿到后按定义定档并定稿。

#### 28.2 ② 决策回执位：规格已定，落字位留 A0，生成器归田丰

- 实测 `deliverables/daily_briefs/2026-11-15/a0_briefing.md`（118 行 / 22,814 B）**无任何决策回执位**；12 部门全提「需决策」，只有汇总、无裁决回流，这是 11-08→11-15 连续 8 天同组问题重复的直接原因。
- 规格（INTEL0 定，可直接落）：`IR-YYYYMMDD-NNN` 编号｜提出部门｜问题摘要｜A0 裁决（采纳／否决／挂起）｜理由｜责任人｜时限｜回流证据｜关闭时间；**received ≠ ack ≠ decided 三态分离**；结果回流 PIR／track record。
- 12 部门重复 5 类待结案：三席失联（11 部门）、补签（12）、P0 否决人（9）、9 项 flaky（8）、乱码议题（7）；逐类须 A0 落一次字，之后晨报不再重复提。
- 归属：写 `a0_briefing` 的生成器是 `tools/hq_dept_brief.py`（git grep 命中），属田丰线；INTEL0 **不擅改他人生成器、不代 A0 落字**，只交规格与结案清单。

#### 28.3 ③ 11-16 12:00 前四项核对（提前只读复核，今日不判逾期）

- **补签闸门**：`2026-11-12_情绪数据产品_执行跟踪表.md` §一/§二/§七 仍为「马岱／张松待签收、未建表」，三封 bridge 升级 `lg-7cad811b49f4`／`lg-0f0a1d0e2656`／`lg-8b4e83f69f57` 仍 `inbox` 无 answered。F-02／F-08 闸门**继续锁死**：补签不落 → 11-16 12:00 建表不得启动；不开替代签收例外、不代签、不倒填。
- **S5 推广**：Hermes `analyst1` profile 存在，`cron/` mtime 11-14 23:42；但 S4 复盘输入（S2 首批 10 code、S3 试批回执）仓内仍查无，W5 未回。S5 11-16 推广**不得抢跑**，工具权限待 A0 工单。
- **W7 KG 需求**：`docs/standards/KG更新通知台账.md` mtime 11-14 04:19；跟踪表 §四「马钧只读需求模板」状态未开始；W7 查询清单／字段／粒度**未见提交**，11-16 12:00 前到期，继续催办不代做。
- **bridge 消费侧**：inbox 248 件（最新入队 11-15 09:12，今早仍在增）、scheduled 1、processed 4（停更 11-07）→ **未恢复且在持续堆积**；`approvals.json` 不存在。归口修消费侧通道，INTEL0 不改调度／配置／服务。
- **W3 收尾**：`ann_sync-2026-11-15.log` 10:30 START／END rc=0、无限频痕迹；账号实时配额仍取不到，频控维持 `partial`，不写「配额足够」。

#### 28.4 边界

- 本轮只动 3 个 tracked 文件：FINAL v1、判定闸报告、COLLECT_REGISTER.md；均**未暂存、未提交、未推送**（入库时机由 A0 定）。
- 未删文件、未改田丰生成器／晨报、未改 org.json／请求单原件、未代发、未代收 ACK、未代签、未建表、未启停服务、未调 API、未读凭据。
- 全部统计值由只读探针运行时注入；日期／章节号由系统时间与现有台账派生，未经人工誊写。
### 29. INTEL0 受理留痕：A0 一句话裁定单与到期前复查（2026-11-15 14:51）

- 触发：老田原样贴回上一轮「下一步」三句。按会话衔接只读同步现场后，先做职权核查再动作。
- **职权核查**：2026-11-15 13:52／13:54 并行提交落地 `docs/ops/2026-11-15_鲁肃部门自主权契约_v1.md` 与 4 张 intel 席位卡；契约适用面封闭为 `dept=invest` 13 席，`departments[intel]` 无 authority_policy/no_gate，INTEL0 「只受理不裁决」2 处原文俱在 → **贴回原话不构成 P0 口径裁决权转授**。
- **呈 A0 裂缝**：4 张 intel 席位卡 prompt 注入「L0 自主执行…不等审批」，与 org charter 张力待裁；均未改 tools/memory_grants，马谡 S5 工具权限仍未开。
- ① 交付 `tmp/2026-11-15_INTEL0_A0一句话裁定单.md`：把 A1-a/A1-b 压成勾选单，附六口径定档表（运行时取自探针）与「照建议／定」两种极简放行开关；INTEL0 不代裁。
- ① 备料 `tmp/intel0_r1_finalize_pending.py`：参数化定档回写补丁，**本轮只落盘不执行**；无 `--approve` 拒跑、默认 dry-run、纯追加定档记录、写前锚点断言。
- ② 决策回执位规格与 5 类结案清单已备（三席失联 11／补签 12／P0 否决人 9／9 项 flaky 11／乱码 7 部门）；生成器属田丰线，未擅改、未代 A0 落字。
- ③ 到期前复查（14:51）：补签／S5／W7／bridge 四项与 11:00 **零变化**；bridge inbox 248（最新 11-15 09:12）vs processed 4（最新 11-07 23:31），未恢复且仍堆积；approvals.json exists=False。今天不是到期点，**不判逾期、不判结论**，到期 11-16 12:00。
- 现场：HEAD `a95ddf110`，共享暂存区 0；本轮只追加本节，三份文书 numstat 不变，未暂存／未提交／未推送（入库时机由 A0 定）。
- 未删文件、未改 org.json／契约／田丰晨报与生成器／请求单原件，未代签、未建表、未启停服务、未调 API、未读凭据。
- 全部统计值由只读探针运行时注入；日期／章节号由系统时间与现有台账派生，未经人工誊写。
### 30. A0 定档回写留痕（2026-11-16 01:44）

- A0 凭证：`A0 老田「照建议」：A1-a=U / A1-b=next（一句话裁定单 §二）`
- 口径：A1-a=U / A1-b=next → 14.4% / fail（n=7）。
- 已回写 FINAL v1 与判定闸报告各一段「A0 定档记录」，纯追加；未改阈值、未删文件、未代裁。
- QA1 会签待补、不阻塞（A0 既有裁定：后续补齐项，不影响生效）；未代签、未倒填。
- 全部值运行时取自探针与系统时间，未经人工誊写。
## §25 缺口发现器 gap_scan.py 建成 + 首轮基线（2026-11-16 02:43）

**背景**：老田 2026-11-15 定本席职责 —— 「数据补齐的板块，随时随地在发现我们的池子里哪些票应该新增数据了、哪些票应该补充数据了，哪些票的新闻有欠缺、哪些票贴吧有欠缺，还有什么地方有此类型数据」。本节是该职责的可执行落地。

### 25.1 工具

- 文件：`news_collector/gap_scan.py`（只读；写盘仅 `data/gap_scan.json`、`data/gap_scan.md`、`data/_gap_index.json` 索引缓存）
- 规模：989 只池内标的 × 7 个源 × 10 个池声明；首跑全量建索引 3.8s，有缓存后 0.4s
- CLI：`--stale-hours/--thin/--top/--only/--json/--md/--no-cache/--no-slack`
- 状态机（八态 + 三不适用）：`MISSING`/`EMPTY`/`STALE_Q`/`THIN_Q`/`NO_QUEUE`/`STALE_OFFQ`/`THIN_OFFQ`/`NO_NEWS` + `WLT`/`NA_MARKET`/`NA_ASHARE`
- **核心区分**：`_Q` = 在该源日常采集队列内（可执行缺陷）；`_OFFQ`/`NO_QUEUE` = 队列外（存量遗留，只能靠扩源解决，L2 决策）。不区分则 2500+ 条噪音淹没真缺口。

### 25.2 首轮基线（时点 2026-11-16T02:42:39，阈值 stale=96.0h、thin=3）

| 源 | 适用/池内 | 有数据 | 合法空 | A 应采未采 | B 队列外 | C 队列内该补 | D 新闻空窗 |
|---|---|---|---|---|---|---|---|
| `irm` | 481/989 | 122 | 1 | **0** | 344 | 14 | 0 |
| `sse` | 469/989 | 7 | 13 | **0** | 435 | 14 | 0 |
| `taoguba` | 950/989 | 378 | 1 | **0** | 543 | 28 | 0 |
| `guba` | 982/989 | 71 | 0 | **0** | 910 | 1 | 0 |
| `xq` | 982/989 | 495 | 0 | **0** | 310 | 177 | 0 |
| `qianping` | 950/989 | 947 | 0 | **0** | 3 | 0 | 0 |
| `news` | 982/989 | 209 | 0 | **0** | 0 | 0 | 773 |

行动分组合计：A 应采未采 **0**、B 队列外未覆盖 2545、C 队列内该补 234、D 新闻空窗 773。

**三条结论（数据/判据，不含研判）**：

1. **A=0**：日常队列内（actionable 65 只）没有「承诺了却没采到」的硬缺陷 —— 采集器本身不缺件。
2. **缺口主因是队列窄，不是采不到**：池 989 只 vs 日常队列 65 只，覆盖差 15 倍。队列外的 2545 条缺口全部来自「从未排上或历史上采过就停」。
3. **新闻维度**：池内 989 只中近 7 日有打标新闻的 468 只、近 48h 仅 21 只、从未有过新闻 34 只；全市场有打标关联 5345 只（近 7 日 2429 只）。新闻打标链路正常（`xd_notice`/`xd_news` 100%、`announcement` 36%、宏观源 1% 属正常），空窗是内容事实。

### 25.3 首跑暴露并已修正的四类假缺口（防回退，已入单测）

1. **跨市场假缺口**：irm 只落深市 00/30（479 文件全深市）、sse 只落沪市 60/68（209 文件全沪市）、taoguba 无 92、qianping 不采北交所 → 一律 `NA_MARKET`，不计缺口。
2. **日度快照假缺口**：千股千评每码在最新快照天然 1 条，套 `thin_n=3` 会把 845 只全判「太薄」→ 该源跳过条数判定，只判在不在最新快照。
3. **队列内外混报**：见 25.1 状态机。混报会让真缺口被 400+ 条噪音淹没。
4. **周末假空窗**：48h 窗口跨周末时源端本不产内容；实测**全市场**打标新闻的 7 日覆盖与 48h 覆盖相差一个数量级，池内亦然（见结论 3 的两个数，勿混读为断供），差距几乎全为周末效应 → 加 `_weekend_slack`（过去 3 天每个周末日 +24h，与 collect_sla 同判例），`--no-slack` 可关。

另修两条**测试时序缺陷**（非产品缺陷）：`test_stale_thresholds_and_exit_code` 与 `test_consecutive_bad_escalates_to_red` 原用 `datetime.now()` 造相对时间，周末跑时阈值被松弛 +24h/日，必挂（11-15 周日实测 2 failed）。修法：仅这两个用例 monkeypatch `_weekend_slack`→0，周末语义仍由 `test_weekend_slack_prevents_false_red` 单独覆盖。

### 25.4 验收

- 新增 `news_collector/tests/test_gap_scan.py`：**53 条**（市场过滤矩阵、状态机八态、日度快照免薄判、新闻无命中≠未排队、周末松弛、`ts_code`/纯字符串代码兼容、严重度与分组不变量全覆盖）。
- 全量 `news_collector/tests`：**180 passed**（原 127 + 新增 53）。

### 25.5 待上级裁定（本席未做、不擅自做）

- **注册每日缺口巡检**（拟 09:40，随巡检线）：属计划任务注册，L2 归马超配准；五要素出齐后另行呈报。
- **扩源/扩队列**：B 类 2545 条属扩源决策（侯风 30 / 两池 82 / k_stock 426 / S12 20 / unified40 16 / 缠论 465 的并集）。其中 `k_stock 实持仓` 有票在 guba/xq/irm 上**零覆盖**，优先级建议由上级定。
- **新源接入**：脚本 §六 已列 8 类候选方向（公告原文/调研纪要/龙虎榜/研报/第三方问董秘/专利招投标招聘/工商穿透/短社媒），**均未核验可得性、未接入**，接入须 L2 配准。

### 25.6 现场与边界

- 本轮只新增 2 文件（`gap_scan.py`、`tests/test_gap_scan.py`）+ 修改 1 测试文件（`tests/test_collect_sla.py` 两处 monkeypatch），并生成 2 产物（`data/gap_scan.json`、`data/gap_scan.md`）与 1 索引缓存。**均未暂存、未提交、未推送**（共享 .git，入库时机另行报批）。
- 临时侦察脚本 `_gap_recon.py/.json` 已进回收站（recycle_del，复核不在）。
- 未触网、未启浏览器、未改采集产物、未改阈值、未改他人线代码、未启停服务、未读凭据。
- 全部统计值运行时读 `data/gap_scan.json` 注入，日期取系统时间，未经人工誊写。


## §25-2 清单体检与定向补采（2026-11-16，老田交来 10 只票逐源体检）

**起因**：老田交来 10 只票（缠论 1/2/3 买 + 青萍之末 RPS 混合口径），要求按「哪些该新增、哪些该补充、哪个源欠缺、还有哪儿有此类数据」逐票体检并补齐。

### 25-2.1 工具缺陷：`--only` 静默丢弃池外票（本轮最先必须修的）

首跑 `gap_scan.py --only <10 只>` 只吐出 **2 只**（300234、600400），其余 8 只无声消失、无任何提示。原实现 `pools = {k:v for k,v in pools.items() if k in want}` 只保留池内命中，池外票连同「七个源一个文件都没有」这个事实一起被丢掉——读起来像「体检通过」。**这种静默失败比报错危险**（同 §24 `_attempt` 判例族）。

修法：抽出纯函数 `apply_only_filter(pools, only)`，池外票补空 entry（weight=0）+ `pool_out` 标记，排名过滤，末尾显式播报「不在任何池内」。新增 6 条单测锁死。

首跑复现的原始事实（修前只看到 2 只）：

| 代码 | 名称 | 池归属（修前可见） |
|---|---|---|
| 300234 | 开尔新材 | houfeng_cand164 |
| 600400 | 红豆股份 | kstock_orders |
| 其余 8 只 | — | **池外，工具未报** |

### 25-2.2 根因：缠论采集队列停在 2026-11-06，与策略侧脱节（本轮最重要的发现）

| 项 | 读数 | 出处 |
|---|---|---|
| 采集队列 `target_queue_chanlun.json` asof | **2026-11-06** | 文件字段 `asof` |
| 队列去重票数 | 465 | `all` 去后缀去重 |
| 队列文件 mtime | 2026-11-09 09:03（7 天未重生成） | `ls -la` |
| 策略侧信号库最新批次 | **2026-11-13** | `quant_backtest/data/chanlun_pool.jsonl` `detect_date` |
| 该批次买点票数（1/2/3买） | **308** | 同上，按 `type` 含「买」去重 |
| 与队列交集 | **24** | 集合交 |
| 队列漏掉的当日买点票 | **284** | 集合差 |

**判据**：**覆盖率 24/308 = 7.8%**（交集 ÷ 策略当日买点集合），即当日买点票有 92.2% 从未进过采集队列。另按 Jaccard 计为 24/749 = 3.2%（并集 749 = 465 + 308 − 24，分母含队列独有的 441 只）——**两个口径不可混用**：前者回答「策略的票采到几成」，后者回答「两个集合有多重合」。老田交来的 10 只中 8 只池外，与前一口径完全吻合——不是采不到，是队列没跟上策略侧。

口径说明（防误读）：

1. `chanlun_pool.jsonl` 是**信号历史库**（全量 12576 行），portal 只取最新批次展示；本表的「买点票数」按 `detect_date` 取最新批次、`type` 含「买」去重，**不含卖点**，所以不能与「当日全部信号数」混用。
2. 队列 `all` 字段 489 条、去重 465 只，说明同一票在队列里有多个 tag；比对一律用去重集合。
3. 队列 asof 早于文件 mtime（11-06 vs 11-09），说明 11-09 那次生成也没带来新快照，**生成器取数日期与写盘日期不同源**，这一点需策略线确认。

### 25-2.3 定向补采：本轮实际补齐 888 条

四个源单票直采（不碰雪球，避开 07:00 生产轮的会话配额）：

| 源 | 动作 | 新增 | 结果 |
|---|---|---|---|
| 互动易 irm | 深市 5 只 | +20 | 000782/300688/300804 首次落盘；002737 接口 total=3 与本地一致（已采尽） |
| 上证e互动 sse | 沪市 5 只 | +4 | 601668 +3、603175 +1；600511/600641/600400 平台返回 0 条 |
| 淘股吧 taoguba | 10 只 ×1 页 | +144 | 8 只从 0 变 18~20 条，最新帖 7.9~29.7h |
| 东财股吧 guba | 9 只 ×1 页 | +720 | 9 只各 80 条（002737 被反爬拦，见 26.4） |

补采后十只票五源落盘（行数，`-` 表示无文件）：

| 代码 | irm | sse | taoguba | guba | xq | 小计 |
|---|---|---|---|---|---|---|
| 601668 | - | 3 | 20 | 80 | - | 103 |
| 600511 | - | - | 20 | 80 | - | 100 |
| 002737 | 3 | - | 20 | - | 80 | 103 |
| 603175 | - | 1 | 20 | 80 | - | 101 |
| 600641 | - | - | 20 | 80 | - | 100 |
| 000782 | 8 | - | 18 | 80 | - | 106 |
| 600400 | - | - | 18 | 80 | - | 98 |
| 300234 | - | - | 20 | 80 | 209 | 309 |
| 300688 | 4 | - | 20 | 80 | - | 104 |
| 300804 | 8 | - | 20 | 80 | - | 108 |
| **合计** | | | | | | **1232** |

补采前实测为 344 行（仅 002737/600400/300234 三只有文件），本轮净增 **888** 行。股吧覆盖 9/10、淘股吧 10/10。

### 25-2.4 东财股吧反爬：`no_marker` 是拦截页，不是「该票无内容」

首轮 10 只里 601668、002737 报 `FAIL no_marker`。落盘 HTML 取证：

| 代码 | HTTP | HTML 长度 | 页面标题 | 内嵌 article_list |
|---|---|---|---|---|
| 601668 | 200 | 56,874 | **身份核实** | 无 |
| 002737（诊断时） | 200 | 332,720 | 葵花药业(002737)股吧… | 有 |
| 600511（对照组） | 200 | 336,993 | 国药股份(600511)股吧… | 有 |

**判据**：HTTP 200 + 标题「身份核实」+ 体积仅正常页 1/6，是反爬拦截页；同一只票在下一次访问又能正常返回（002737 诊断时正常、随后连打 3 次全拦）→ 触发条件是**访问频次累计**，与票无关。**短时硬重试会加重封禁**，已按降速纪律停手，002737 留待冷却后补。

附带缺陷（未修，记入待办）：`guba_pw.py` 遇 `no_marker` 只记 FAIL、不落尝试账本，而 `gap_scan` 只能看到「无文件」→ 会把「被拦过」读成「从未采过」。与 §24 `_attempt.json` 同族，需给 guba 也建尝试账本后才能区分。

### 25-2.5 剩余缺口与判据（只列事实，不下结论）

| 缺口 | 数量 | 判据 |
|---|---|---|
| 池外（未进任何采集队列） | 8/10 | §26.2 队列脱节 |
| 雪球 xq 无队列 | 8/10 | 队列不含该票；xq 只按队列跑，不按需求跑 |
| 官方问答稀薄 | 6/10 有数据但 84~1746h | irm `total` 与本地一致、sse 返回 0 条 → **内容事实，非断供** |
| 新闻空窗 | 4/10 超 96h | 000782(99h)、600400(1107h)、300234(546h)、300804(546h) |
| 股吧被拦 | 1/10（002737） | §26.4 反爬，待冷却补 |

「官方问答稀薄」这条特别标注：irm 补采时打印 `total=N 新增 0`，即接口自报的总数已全部在本地；这与「漏采」是两种完全不同的东西，按 §六 三层信源判据，**无内容 ≠ 没采到**，不可写成覆盖缺陷。

### 25-2.6 呈批（L2，报马超）

CR-20261116-01 **缠论采集队列刷新**：队列 asof 停在 2026-11-06，与策略侧 2026-11-13 批次交集仅 24/308。选项 A：按策略最新买点批次重生成队列（覆盖 284 只，需先确认生成器口径）；选项 B：维持现队列 + 对新增买点票走单票补采（成本随票数线性上升）；选项 C：不动作，等策略线自行修生成器。五要素与影响面见呈批单，**未改任何队列**。

### 25-2.7 验收

- 新增 `tests/test_gap_scan.py` 6 条（池外不丢 / 标记正确 / 零权重 / None 透传 / 后缀短码 / 空列表）；全量 `news_collector/tests` **186 passed**（原 180 + 6）。
- 台账本节全部数字由 `_append_s26.py` 现场计算注入，未手工誊写。

### 25-2.8 局限性

1. 补采只是**时点快照**，不构成持续覆盖——这 8 只票仍在队列外，下一轮不会自动续采。
2. 队列交集按 `detect_date` 最新批次算；若策略侧批次定义变化（例如改为滚动窗口），本表读数不可比。
3. 股吧 80 条是首页最热一页，非全量；仅覆盖「今日补齐」口径。
4. 雪球本轮未碰，xq 缺口未验证是否可补（配额优先给 07:00 生产轮）。

_（本节结束）_

## §25-3 策略中台每日出票 → 采集池（2026-11-16，老田指令「读取更新策略中台今天选股的数据」）

**背景**：老田 11-16 08:09 指令读策略中台当日选股并据此补齐。中台出票文件 `portal/data/picks_history/todays_<YYYYMMDD>.json`，生成时刻为交易日 18:20（收盘后）。11-16 08:09 时当日批次未生成，最新可得为 **11-13（周五）批次**，此事实由 `picks_pool.py` 的 `is_today` 字段显式标注，不作「今日数据」使用。

**出票规模**：649 只（T2 7 / T3 642），n_hit≥2 共 48 只。

### 25-3.1 逐源覆盖（分母 = 649 只出票，已剔除跨市场不适用项）

| 源 | 适用只数 | 有数据(OK) | 覆盖率 |
|---|---|---|---|
| irm | 361 | 36 | 10.0% |
| sse | 278 | 1 | 0.4% |
| taoguba | 639 | 155 | 24.3% |
| guba | 639 | 12 | 1.9% |
| xq | 639 | 134 | 21.0% |
| qianping | 639 | 636 | 99.5% |
| news | 639 | 187 | 29.3% |

**与注册池的关系**：649 只中 **432 只（66.6%）不在任何已注册池内**（注册池合计 989 只）。
结论：缺口主因是**队列没跟上策略侧**，不是采集器失效——与 §25-2 缠论队列 asof 停在 11-06 同一病根。

### 25-3.2 L1 定向补采（n_hit≥2 ∪ T2 共 49 只）

| 源 | 补采前 | 补采后 | 净增 | 新落盘文件 |
|---|---|---|---|---|
| irm | 365 | 454 | +89 | 5 |
| sse | 18 | 22 | +4 | 1 |
| taoguba | 1835 | 2082 | +247 | 12 |
| guba | 1065 | 4678 | +3613 | 39 |
| xq | 2815 | 2815 | +0 | 0 |

五源合计净增 **3953** 条。49 只 × 7 源缺口项由 **172 降至 116**（净减 56）。
**雪球未动**：今日 07:00 轮三次尝试均停在 403/666（连续被拒 3 只、尝试账本 400 只），会话已被标记，继续补采只会加重封禁，34 只零覆盖票留冷却后处理（与 CR-20261114-01 分片调度叠加评估）。

### 25-3.3 遗留判据

1. **补采=时点快照**：49 只仍不在任何队列，下轮 07:00 不续采，会掉回零覆盖。治本是队列，已呈批 **CR-20261116-02**（pending）。
2. **官方问答「有数据但 OK 少」是内容事实**：irm 补采 89 条后 OK 仍只 6 只——append-on-new 语义下 `max_ts` 是内容最后新增时间，官方回复数月不更新即判 STALE，不等于未采集。不得据此报「采集失效」。
3. **输入脏字符判例（新，同日）**：CRLF 代码清单经 shell 传递后每只票尾随 `\r`，`zfill(6)` 得 7 字符 → 649 只全部判成 `NA_ASHARE`（不适用该源），报告呈现为「零缺口」。已修 `gap_scan.norm_code`：剥全部空白 + 6 位数字校验，归一失败 **fail-loud 报错退出码 2**，不再静默。同族于 §25-2 的 `--only` 静默丢弃池外票——**输入异常不能表现为「没问题」**。
4. **新增工具** `picks_pool.py`：读最新可得出票批次、校验「今天」（`is_today`/`note`）、输出 LF 行尾 6 位代码清单；单测 9 条。**不写队列、不改中台产物**。

---

_（本节所有数字由 `_append_s253.py` 运行时从 `_picks_gap.json` / `_pri_gap_after.json` / `_base_pri.json` / `todays_20261113.json` 注入，未经人工誊写。）_
## §25-4 巡检发现的两处采集侧缺陷（2026-11-16 早间，均为「不适用被读成故障」类）

### 25-4.1 market_sentiment.py 除零：整个市场情绪段 rc=1 中断

**现象**：08:13 `SentimentSnapshot` 串联日志 `market_sentiment.py rc=1`，Traceback 指向 `market_breadth()` 第 121 行 `sum(ps) / sum(ws)` ZeroDivisionError。

**根因**：极性源条目 `n` 为 0 时仍被 append 进权重列表 `ws`，`ws` 非空但 `sum(ws)==0`，`if not ws` 的守卫因此失效。即**「没有样本量」与「没有该源」被当成同一件事**。

**修复**：`n<=0` 的源直接跳过（既不计权重也不计极性）；卡片无任何有效样本则不计入广度分母。修复后实跑 `index=59.6 state=乐观 breadth=68.2% n_codes=1387 n_samples=22691`。

### 25-4.2 taoguba PARSE_EMPTY_GUARD 假警报：源不覆盖的市场被算进分母

**现象**：08:19 `kstock_intel_daily.log` 报 `rc=2`，`PARSE_EMPTY 81% > 30%（首页 13/16 解析 0 行）`，13 只全为北交所 920 前缀。

**取证**（只读探针，绕过本机 MITM 代理后直连）：

| 代码 | HTTP | 页面字节 | headImg 块 | 标题 |
|---|---|---|---|---|
| 920021 | 200 | 29739 | 无 | 错误页面_淘股吧 |
| 920599 | 200 | 29739 | 无 | 错误页面_淘股吧 |
| 920087 | 200 | 29739 | 无 | 错误页面_淘股吧 |
| 600519 | 200 | 263111 | 有 | 贵州茅台sh600519股票吧行情… |
| 000001 | 200 | 271231 | 有 | 平安银行sz000001股票吧行情… |

**判据**：北交所在淘股吧**没有个股版面**，返回的是错误页而非「有版面但无帖」。三只票页面字节完全相同（29739）也印证是同一张错误页。

**修复**：新增 `UNSUPPORTED_PREFIX=("92","43","83","87","88")` 与纯函数 `split_unsupported()`，采集前剔除并在日志显式声明「跳过 N 只：源不覆盖该市场」，**不计入护栏分母**。效果：`taoguba_collector.py 920021,920599,600519,000001 --pages 1` → 跳过 2 只、`首页解析0行 0/2 (0%)`、rc=0（修复前同输入必 rc=2）。

**为什么必须报**：护栏本意是把「源端无帖」与「我们瞎了」分开；把「源不覆盖」混进分母后，假警报会成为常态，真正的挑战页故障反而被淹没——与 §25-3.3 的输入脏字符同族：**不适用不能表现为故障，故障也不能表现为不适用**。

### 25-4.3 回归测试

`tests/test_collect_guards_1116.py` 新增 8 条（北交所过滤 4 条 + 除零回归 4 条）。全量：**207 passed in 17.53s**。

---

_（本节数字由 `_append_s254.py` 运行时注入：市场指数读 `data/market_sentiment.json`，测试结果读 pytest 实际输出。）_