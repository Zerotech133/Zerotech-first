/**
 * 导航配置（spec §2 双层结构）：
 * - NAV_GROUPS：工作区左侧 8 项菜单（严格 8 项，用户指定；CommandPalette 导航命令同源）
 * - MARKET_ROUTES：URL 市场段合法值（/:market/:group 中的小写段）
 * - LEGACY_REDIRECTS：旧路径 → 新 /{market}/{group}?tab=x 重定向表（plan Task 2 Step 2 照抄）
 * （旧四工作区 WORKSPACES 常量已随 Task 5 主题切换器一并移除）
 */

export interface NavGroup {
  key: string;
  /** i18n key（pages 命名空间，如 'nav.overview'）；消费端用 t(labelKey) 翻译 */
  labelKey: string;
  icon: string;
}

export const NAV_GROUPS: readonly NavGroup[] = [
  { key: 'overview', labelKey: 'nav.overview', icon: '◈' },
  { key: 'strategies', labelKey: 'nav.strategies', icon: '◇' },
  { key: 'candidates', labelKey: 'nav.candidates', icon: '◆' },
  { key: 'signals', labelKey: 'nav.signals', icon: '▣' },
  { key: 'risk', labelKey: 'nav.risk', icon: '⛨' },
  { key: 'trading', labelKey: 'nav.trading', icon: '▲' },
  { key: 'replay', labelKey: 'nav.replay', icon: '◉' },
  { key: 'system', labelKey: 'nav.system', icon: '⚙' },
];

export const GROUP_KEYS: readonly string[] = NAV_GROUPS.map(g => g.key);

const ALL_ROUTES = ['cn', 'us', 'hk', 'crypto'] as const;
/** 类型保持全集，避免过滤破坏其他文件的类型推导 */
export type MarketRoute = (typeof ALL_ROUTES)[number];

/**
 * URL 市场段合法值：可用 Vite 环境变量 VITE_ENABLED_MARKETS（逗号分隔，如 "US,CRYPTO"）过滤。
 * 不设置时 = 四市场全开（严格向后兼容）。
 */
const _enabledRoutes = (import.meta.env.VITE_ENABLED_MARKETS ?? '')
  .split(',')
  .map((s: string) => s.trim().toLowerCase())
  .filter(Boolean);
export const MARKET_ROUTES: readonly MarketRoute[] = _enabledRoutes.length
  ? ALL_ROUTES.filter((r) => _enabledRoutes.includes(r))
  : ALL_ROUTES;

export interface LegacyRedirect {
  from: string;
  to: string;
}

/** 旧路径 → 新路径（收藏链接不断；原 query 由 <LegacyNavigate> 透传合并） */
export const LEGACY_REDIRECTS: readonly LegacyRedirect[] = [
  { from: '/dashboard', to: '/cn/overview' },
  { from: '/market', to: '/cn/overview?tab=quotes' },
  { from: '/kline', to: '/cn/overview?tab=kline' },
  { from: '/positions', to: '/cn/trading' },
  { from: '/accounts', to: '/cn/trading?tab=accounts' },
  { from: '/approvals', to: '/cn/signals?tab=approvals' },
  { from: '/plans', to: '/cn/signals' },
  { from: '/pipeline', to: '/cn/signals?tab=pipeline' },
  { from: '/risk', to: '/cn/risk' },
  { from: '/candidates', to: '/cn/candidates' },
  { from: '/replay', to: '/cn/replay' },
  { from: '/pnl-calendar', to: '/cn/replay?tab=calendar' },
  { from: '/strategies', to: '/cn/strategies' },
  { from: '/strategy-performance', to: '/cn/strategies?tab=perf' },
  { from: '/research', to: '/cn/strategies?tab=ai' },
  { from: '/backtest', to: '/cn/strategies?tab=backtest' },
  { from: '/model-ops', to: '/cn/strategies?tab=models' },
  { from: '/fundamentals', to: '/cn/strategies?tab=fund' },
  { from: '/regime', to: '/cn/strategies?tab=regime' },
  { from: '/market-overview', to: '/cn/overview?tab=overview' },
  { from: '/portfolio', to: '/cn/trading?tab=portfolio' },
  { from: '/daily-report', to: '/cn/replay?tab=report' },
  { from: '/knowledge', to: '/cn/replay?tab=knowledge' },
  { from: '/learning-report', to: '/cn/replay?tab=learning' },
  { from: '/workflow', to: '/cn/trading?tab=workflow' },
  { from: '/system-status', to: '/cn/system' },
  { from: '/alerts', to: '/cn/system?tab=alerts' },
  { from: '/automation', to: '/cn/system?tab=automation' },
  { from: '/governance-board', to: '/cn/system?tab=governance' },
  { from: '/multi-strategy', to: '/cn/system?tab=multi' },
  { from: '/calendar', to: '/cn/system?tab=calendar' },
  { from: '/settings', to: '/cn/system?tab=settings' },
  { from: '/exports', to: '/cn/system?tab=exports' },
  { from: '/bigscreen', to: '/bigscreen' },
];
