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
