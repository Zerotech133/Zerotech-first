# zhice-OS V2 前端重设计实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 spec `docs/superpowers/specs/2026-08-19-frontend-redesign-design.md` 重构前端为「指挥大屏 + 市场工作区」双层结构，5 主题可切换（默认 carbon），37 页归入 8 菜单组。

**Architecture:** 两个 Layout（BigscreenLayout 全屏 / WorkspaceLayout 顶栏+侧栏+底栏）+ CSS 变量主题系统（body[data-theme]）+ URL 市场段路由（/{market}/{group}）。全部复用现有 116 个后端 API。

**Tech Stack:** React 19 + react-router 6 + antd(ConfigProvider 暗色) + 现有 hooks/组件

---

## Task 1: 主题基建（5 套 CSS 变量 + ThemeProvider）

**Files:**
- Create: `frontend/src/themes/tokens.css`（5 套 `body[data-theme=...]` 变量 + 全局暗色基础样式）
- Create: `frontend/src/themes/ThemeContext.tsx`（useTheme：theme/setTheme，localStorage['zhice_theme'] 记忆，默认 carbon）
- Modify: `frontend/src/main.tsx`（import tokens.css；ThemeProvider 包裹；antd ConfigProvider 改 `theme.darkAlgorithm` + token 映射）
- Modify: `frontend/src/styles.css`（清掉与暗色冲突的旧亮色规则，保留动画/toast）

- [ ] Step 1: 写 `tokens.css`——从 `docs/mockups/theme-variants.html` 提取 5 套变量（carbon/aurora/emerald/amber/cyber 各含 --bg-deep/--bg/--card/--card-solid/--line/--line-bright/--cyan/--violet/--violet-deep/--gold/--red/--green/--txt/--txt-dim/--txt-faint/--glow1/2/3/--grid），附 body 基础暗色样式、滚动条暗色、背景网格与光晕 div 样式（.bg-grid/.bg-glow）
- [ ] Step 2: 写 `ThemeContext.tsx`——`ThemeProvider` 初始化读 localStorage（无则 'carbon'），`setTheme(t)` 同时写 data-theme 属性 + localStorage；导出 `useTheme()` 与 `THEMES` 常量（key+中文名）
- [ ] Step 3: 改 `main.tsx`——`<ThemeProvider>` 内 `ConfigProvider theme={{algorithm: theme.darkAlgorithm, token:{colorPrimary:'#8E94A3', colorBgBase:'#101014', borderRadius:6, fontSize:13}}}`
- [ ] Step 4: 临时验证——`npm run build` 通过；浏览器手查 data-theme 切换 body 背景变化
- [ ] Step 5: Commit `feat: theme foundation - 5 CSS variable themes with carbon default`

## Task 2: 两态 AppShell + 路由重构

**Files:**
- Create: `frontend/src/layouts/BigscreenLayout.tsx`（渲染 .bg-grid/.bg-glow 背景 + Outlet，无侧栏底栏）
- Create: `frontend/src/layouts/WorkspaceLayout.tsx`（顶栏：品牌/市场Tabs(cn|us|hk|crypto)/阶段chip/⌘K/🌐大屏/头像；侧栏 216px：8 菜单项+徽标+底部实时状态块；底栏 30px StatusBar；内容区 Outlet）
- Create: `frontend/src/layouts/NavSide.tsx`（8 项菜单数据与渲染，徽标数从 /overview 拉取）
- Create: `frontend/src/layouts/StatusBar.tsx`（市场·阶段·候选·待授权·今日盈亏·质量·时钟，useAutoRefresh 30s）
- Modify: `frontend/src/App.tsx`（全部重写：`/`→BigscreenLayout+HomeBigscreen；`/:market/:group`→WorkspaceLayout+8 组路由；旧 37 路径全部 `<Navigate to>` 重定向；ProtectedRoute 保留用于组路由）
- Modify: `frontend/src/contexts/MarketContext.tsx`（market 改为 `useParams` 派生（/:market 段小写→大写映射，默认 cn），保留 pnlColor/fmtMoney/fmtPct/MARKET_META 导出；setMarket 改为 navigate）
- Modify: `frontend/src/config/navigation.ts`（重写为 8 组 NAV_GROUPS 常量：key/label/icon/route）

- [ ] Step 1: 写 NavSide/StatusBar/两 Layout（样式照 v3 原型 WorkspaceLayout 段抄：固定顶栏 56/侧栏 216/底栏 30、玻璃拟态、发光激活条）
- [ ] Step 2: 重写 App.tsx 路由表——新结构：
  ```
  /                          BigscreenLayout > HomeBigscreen
  /:market(cn|us|hk|crypto)/:group(overview|strategies|candidates|signals|risk|trading|replay|system) > WorkspaceLayout > 组页
  旧路径 Redirect 表：/dashboard→/cn/overview, /market→/cn/overview?tab=quotes, /kline→/cn/overview?tab=kline,
  /positions→/cn/trading, /accounts→/cn/trading?tab=accounts, /approvals→/cn/signals?tab=approvals,
  /plans→/cn/signals, /pipeline→/cn/signals?tab=pipeline, /risk→/cn/risk, /candidates→/cn/candidates,
  /replay→/cn/replay, /pnl-calendar→/cn/replay?tab=calendar, /strategies→/cn/strategies,
  /strategy-performance→/cn/strategies?tab=perf, /research→/cn/strategies?tab=ai,
  /backtest→/cn/strategies?tab=backtest, /model-ops→/cn/strategies?tab=models,
  /fundamentals→/cn/strategies?tab=fund, /regime→/cn/strategies?tab=regime,
  /market-overview→/cn/overview?tab=overview, /portfolio→/cn/trading?tab=portfolio,
  /daily-report→/cn/replay?tab=report, /knowledge→/cn/replay?tab=knowledge,
  /learning-report→/cn/replay?tab=learning, /workflow→/cn/trading?tab=workflow,
  /system-status→/cn/system, /alerts→/cn/system?tab=alerts, /automation→/cn/system?tab=automation,
  /governance-board→/cn/system?tab=governance, /multi-strategy→/cn/system?tab=multi,
  /calendar→/cn/system?tab=calendar, /settings→/cn/system?tab=settings,
  /exports→/cn/system?tab=exports, /bigscreen→/（大屏即首页）, /login 保留
  ```
- [ ] Step 3: MarketContext URL 派生改造（useMarket 返回值接口不变，内部实现换）
- [ ] Step 4: 组页临时占位（8 个 `GroupPlaceholder` 显示组名）让路由跑通；`npm run build` + 手查：`/` 大屏空壳、`/cn/overview` 有壳+菜单、旧 `/positions` 跳转
- [ ] Step 5: Commit `feat: dual-mode app shell with market-prefixed routes and legacy redirects`

## Task 3: 指挥大屏页（真数据）

**Files:**
- Create: `frontend/src/pages/home/HomeBigscreen.tsx`（v3 原型大屏段 1:1 实现：头部品牌流光+时钟+扫描线、4 合计 KPI（cross-market total_cny/total_pnl_cny/今日合计/持仓&待授权）、四市场卡片（equity_cny/regime/今日/漏斗 4 节点/alert 高亮）、闭环漏斗表（四市场 funnel+转化率）、系统健康环（quality_score+调度/熔断/告警）；30s useAutoRefresh；点卡片 navigate /{m}/overview）

- [ ] Step 1: 实现 HomeBigscreen（API：`/portfolio/cross-market`、`/pipeline?market=X`×4、`/market/regime?market=X`×4、`/system/data-quality?market=CN`、`/circuit-breaker/status`；样式全走 CSS 变量+glass 类，v3 大屏 CSS 落进 tokens.css 的 bigscreen 区块）
- [ ] Step 2: 验证——浏览器 `/` 显示真实四市场数据；卡片点击进 /cn/overview
- [ ] Step 3: Commit `feat: home command bigscreen with live cross-market data`

## Task 4: 8 组容器页（Tab 聚合现有 37 页）

每组一个容器组件：antd Tabs（?tab= 记忆），内嵌现有页面组件（内容原样），顶部统一 PageHeader（面包屑 COMMAND/{市场}/{组} + meta）。

**Files:**
- Create: `frontend/src/pages/groups/OverviewGroup.tsx`（总览(DashboardPage)/行情(MarketPage)/K线(KlinePage)/市场概览(MarketOverviewPage)）
- Create: `frontend/src/pages/groups/StrategiesGroup.tsx`（策略(StrategiesPage)/绩效(StrategyPerformancePage)/AI研究(ResearchPage)/回测(BacktestPage)/模型实验(ModelOpsPage)/基本面(FundamentalsPage)/市场状态(RegimePage)）
- Create: `frontend/src/pages/groups/CandidatesGroup.tsx`（CandidatesPage）
- Create: `frontend/src/pages/groups/SignalsGroup.tsx`（计划(PlansPage)/授权(ApprovalsPage)/闭环流水线(PipelinePage)）
- Create: `frontend/src/pages/groups/RiskGroup.tsx`（风控中心(RiskPage)/自动化熔断(AutomationPage)）
- Create: `frontend/src/pages/groups/TradingGroup.tsx`（持仓(PositionsPage)/账户(AccountsPage)/组合(PortfolioPage)/工作流(WorkflowPage)）
- Create: `frontend/src/pages/groups/ReplayGroup.tsx`（复盘(ReplayPage)/盈亏日历(PnlCalendarPage)/知识库(KnowledgePage)/学习报告(LearningReportPage)/日报(DailyReportPage)）
- Create: `frontend/src/pages/groups/SystemGroup.tsx`（系统(SystemStatusPage)/告警(AlertsPage)/自动化(AutomationPage)/治理(GovernanceBoardPage)/多策略(MultiStrategyPage)/日历(CalendarPage)/设置(SettingsPageV2)/导出(ExportsPage)）
- Create: `frontend/src/components/PageHeader.tsx`
- Modify: Task 2 的 8 个占位替换为上述容器

- [ ] Step 1: PageHeader + OverviewGroup 先行，跑通一组（/cn/overview 四 Tab 可切、?tab 记忆）
- [ ] Step 2: 其余 7 组容器逐个落（纯组合，无新逻辑）
- [ ] Step 3: 37 旧页全量回归手查清单（spec §8）：授权通过/拒绝、计划审批、候选抽屉、跳 K 线、自动刷新、⌘K、SymbolLink
- [ ] Step 4: `npm run build`；Commit `feat: 8 nav groups aggregating all existing pages`

## Task 5: 主题切换器 + ⌘K 命令 + 收尾

**Files:**
- Modify: `frontend/src/layouts/WorkspaceLayout.tsx`（顶栏主题 Dropdown 5 选）
- Modify: `frontend/src/components/CommandPalette.tsx`（「主题」分组：5 条切换命令）
- Modify: `frontend/src/pages/SettingsPageV2.tsx`（主题区：5 张预览卡点击切换）
- Modify: `frontend/src/styles.css`（清残留亮色，body 加 transition）

- [ ] Step 1: 三处主题切换入口实现
- [ ] Step 2: 全主题走查——5 主题 × 两态截图对比，A股红涨绿跌全主题正确（up=--red down=--green）
- [ ] Step 3: 旧导航残留清理（旧 WORKSPACES 常量与死代码删除）
- [ ] Step 4: 最终验收（spec §8 全项）+ `pytest -q`（后端不动）+ build
- [ ] Step 5: Commit `feat: theme switcher in topbar/palette/settings; final polish`

---

## 自检（对照 spec）

- §2 双层结构/8 菜单/旧路由重定向 → Task 2/4 ✅
- §3 5 主题 carbon 默认/记忆/全变量化 → Task 1/5 ✅
- §4 v3 视觉 → Task 1+2+3 ✅
- §5 组件清单 → Task 1-5 ✅
- §8 验收标准全部覆盖 ✅
