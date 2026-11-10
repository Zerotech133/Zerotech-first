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

## 二、审计前提订正（对 2026-11-10 整合版 §1.2 的反核）

1. **"11-08/09 仍在提交"失真**：7 个实爬模块（#2-#8）git 状态全部为**未跟踪（??）**，从未提交过；
   11-08/09 的 commit 是管线/观测层（传播关系判定 30b115c16、L1 观测桥 fdb9476d6），不是爬虫本身。
2. **"未随 11-01 收口封存"失焦**：未检索到 11-01 收口令原文；且 #3/#4 明确带 11-08 批文，
   #2/#5-#8 属同批授权的标的驱动采集——性质是**新授权线**，不是收口漏网。
3. **真实缺口是治理而非封存**：批文只活在 docstring、模块未入 git、无台账（本表即补）。

## 三、待办（治理闭环）

- [x] **R1 模块入库**：#2-#8 连同批文号（C9A-KOL-001）+ #11 桥，已 `git add` + 提交（commit `a6d383bd9`，11 文件 / 1788 行，pre-commit 凭据扫描 0 命中；老田 2026-11-10 批）。推送 GitHub 受本环境网络限制（`github.com:443` 代理不可达），待另行批准走自建 git 远端（触 10.205.0.118，需单独点头）。
- [x] **R2 台账落地**：本文件（2026-11-10 建）
- [ ] **R3 路径修复**：`xq_kol.py:44` 写死 `C:\aios` → env `AIOS_ROOT`/`resolve_repo_root()`（阶段 A3）
- [ ] **R4 文档订正**：整合版 §1.2/§2.2 + `docs/采集情报_板块手册.md` 同步改判（2026-11-10 已同步）
- [ ] **红线维持**：采集频率/源/反爬不进渲染链路；#3/#4 增删号只改 `news_config.json`，不动代码
- [ ] **R5 Windows 计划任务**：`KStockIntelDaily`(每日07:30) 注册脚本已备（`register_kstock_intel_task.ps1`）；当前非管理员会话注册被拒（"拒绝访问"），待管理员以管理员 PowerShell 执行；WorkBuddy 自动化已作兜底(07:30 ACTIVE，id `a4c50019…`)
