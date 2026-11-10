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
- [ ] **R4 文档订正**：整合版 §1.2/§2.2 + `docs/采集情报_板块手册.md` 同步改判（2026-11-10 已同步）
- [ ] **红线维持**：采集频率/源/反爬不进渲染链路；#3/#4 增删号只改 `news_config.json`，不动代码
- [ ] **R5 Windows 计划任务**：`KStockIntelDaily`(每日07:30) 注册脚本已备（`register_kstock_intel_task.ps1`）；当前非管理员会话注册被拒（"拒绝访问"），待管理员以管理员 PowerShell 执行；WorkBuddy 自动化已作兜底(07:30 ACTIVE，id `a4c50019…`)
- [x] **R6 侯风桥落地**：#12 桥完成（run_houfeng_intel.py + houfeng_watchlist.json + target_queue_houfeng.json）；首跑 2026-11-10 09:00 补齐 irm+162/sse+22/taoguba+373 条（失败0，合法空：10只SH无e互动、920599.BJ无淘股吧帖）；xq 并集合并验证 489=465+24 唯一代码全覆盖；WorkBuddy 自动化 `HoufengIntel`(07:40 ACTIVE, id `1e336f25…`)。已 git 入库（commit `347fb9570`），并经老田批准走自建 git 新仓 `aios.git` 推送（本地 remote `selfgit`）。
- [x] **R7 164 候选池补齐（2026-11-10）**：老田裁定"纳入补齐"。pick 文件只留计数、明细未落盘 → `houfeng_cand164_replay.py` 按 `_pick_20261106_report.py` 冻结参数只读复现 `M_off["BUY"][t_sig]`，双金标准校验（候选数=164、top30 逐只逐序一致）全 PASS → `houfeng_candidates_164.json`（SZ70/SH93/BJ1，含 30 只、净增 134）。runner 采集范围改为选股∪候选池（分批≤40），首跑 2026-11-10 10:42，覆盖增量以 `logs/houfeng_intel_full164.log` 的 BEFORE/AFTER 为准；合法空口径同 R6。**候选池为 20261106 冻结快照、非每日重算**；如需刷新须上级裁定后重跑复现脚本。
- [ ] **R8 xq 雪球队列扩容（待上级裁定）**：`target_queue_houfeng.json` 已登记 `candidate_pool`(164) + 开关 `xq_include_candidates`(默认 false)。开关 false → 07:00 `--queue all` = 489；置 true → 593（净增 104 只有头采集）。判据：164 池现仅 37/164 只有雪球数据（30 只选股 6/30、134 净增 31/134），扩容将新增约 104 只 Playwright 有头抓取，且 07:00→07:20→07:30→07:40→08:00 时序紧凑。裁定归上级，本席不擅动。
- [ ] **R9 服务端孤儿 pack 清理（待批）**：`10.205.0.118:/home/git/repos/aios.git/objects/pack/tmp_pack_i87Acd`（146M，首次大 pack 推送被客户端自设 timeout 掐断留下的孤儿，非 finalize pack）。`git fsck --full` 零错误、819M 主 pack 完好；删除属生产服务器操作，须老田批准后执行。
- [ ] **R10 两池 xq 雪球覆盖（待上级裁定）**：两池逐日滚动换仓，`target_queue_cohorts.json` 仅登记（开关 false）。若并入 07:00 有头管线需改 `xq_stock_timeline.load_queue` 增加该文件并集；判据：两池 59 只现仅 4 只有雪球数据，且两池与侯风/缠论队列交集极小（侯风 164 ∩ 两池 = 1 只）。逐日换仓意味着雪球队列每日变动，并入前须评估有头管线负载。
- [x] **R11 各策略出票统一汇总**：`strategy_picks_summary.py`（只读盘点 6 线：k_stock/侯风/两池/缠论/选股日更/策略库 S01-S12，含各线 OSINT 覆盖与缺口清单）→ `strategy_picks_summary.json`。k_stock 线 18 行持仓（ai_team 10 含港股3 / double_knife 2 / qingping_us 美股6）；缺口主线 = 两池 54/59 无覆盖（已由 #13 桥补齐）；selection_daily picks=0（S02 熔断暂停，无出票）。
- [x] **R12 OSINT 方法论落地（2026-11-10，老田批「按计划实施」）**：依据 `docs/OSINT方法论结合方案_2026-11-10.md`（范本=丁爸《美国陆军未来关键发展趋势深度研究》）。L1+L4：手册 §六 增补四组判据（三层信源结构/事实状态词法标注/传闻-澄清对账/输出纪律）；L3：**派生标注层** `fact_type_annotate.py`（只读四源 → `data/fact_type/{src}/{code6}.jsonl`，50815 行：tier1 1282 / tier3 49533，plan 3292/fact 2983/mixed 1484）——**不改采集器落盘 schema**（契约变更方案经评估不采用，留痕于手册 §6.5）；L2：对账底表 `rumor_verification.py`（→ `data/rumor_check/reconciliation_*.json`，632 code：两端齐备 102/仅官方 1/仅传闻 529，**核验结论栏一律留空待分析岗**）。两工具均只读源、零契约风险、可整体重跑；红线维持：不进渲染链路，标注≠事实裁定。
- [x] **R13 OSINT 收集方法落地 L1+L2（2026-11-11，老田批「执行L1+L2」）**：依据 `docs/OSINT收集方法学习_2026-11-11.md`（C1-C6：情报周期/PIR/NATO Admiralty Code/三角验证/SOCMINT）。**L1 零改动（手册 §6.6-6.9 即生效）**：独立性判据（转引≠独立源、结构性独立）、激励字段定义（六源倾向性基线）、回音室须知（单一伪影不支撑、样本偏差声明）、评级基线表（NATO Admiralty A-F×1-6 二维独立 + 六源可靠性基线）。**L2 小改 `rumor_verification.py`**：①核验栏加 `admiralty_code` 占位（NATO 格式如 `B2`，本席保持 null）；②code 级发帖量统计 `post_volume` + `rumor_by_source`/`official_by_source`（按源分列）；③官方端记录补 `src`（irm/sse 分离）。验收：全量重跑 summary 六项与基线完全一致（632/102/1/529/1282/37735），632 code 新字段断言 bad_count=0。**L3 待批（未执行）**：PIR 登记机制、xq 补 link 字段（溯源链断实证缺口 C6）、六源 track record 档案——均归上级裁定，本席不擅动。
- [x] **R14 OSINT 收集方法 L3 落地（2026-11-11，老田批「执行L3」）**：①**PIR 登记机制**：`docs/PIR_register.md`（PIR编号/需求/标的/源/指示器/覆盖状态/责任人，需求内容归上级填，本席只维护覆盖状态）；手册 §6.10。②**xq 补 link（C6 溯源缺口闭环）**：`xq_stock_timeline.py` API_JS 提取 permalink `https://xueqiu.com/{user_id}/{id}` + rec 落盘加 `link` 字段；存量 `xq_backfill_link.py` 幂等回填 **22768 行**（复跑 updated=0 确认幂等）；对账底表 rumor 端引用经 `rec.get("link")` 自动带溯源（C6 缺口从采集端到对账端全闭环）。③**六源 track record 档案**：`docs/source_track_record.md`（六源基线+校准口径，初期已验证列全 0 待分析岗回填 `admiralty_code`，本席不编造数字）；手册 §6.11。三项均只读/加成式 schema 变更（下游不消费 link 不受影响），红线维持：不替分析岗裁事实、不擅自扩源。
- [x] **R15 核验流转草案（2026-11-11，「下一步」自动推进 R12 治理闭环最后一公里）**：新增 `docs/核验流转草案.md`——定义 `rumor_verification.py` 产出的 `verification` 栏字段语义（status/by/note/admiralty_code）+ 填写主体建议（分析岗执笔、马超复核，归上级裁定）+ 流转流程 + 触发频率建议 + **已识别风险 R15-1（全量重跑新建带时间戳文件、已填 verification 不延续，建议改状态合并持久化，待裁定）**。边界重申：本席只生成底表与维护字段、不填任何结论。手册 §6.3 引用草案。
