import { Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom';
import ProtectedRoute from './components/ProtectedRoute';
import { Toast } from './components/Toast';
import CommandPalette from './components/CommandPalette';
import BigscreenLayout from './layouts/BigscreenLayout';
import WorkspaceLayout from './layouts/WorkspaceLayout';
import { GROUP_KEYS, LEGACY_REDIRECTS, MARKET_ROUTES } from './config/navigation';
import ProfilePage from './pages/ProfilePage';
import BillingPage from './pages/BillingPage';
import HomeBigscreen from './pages/home/HomeBigscreen';
import OverviewGroup from './pages/groups/OverviewGroup';
import StrategiesGroup from './pages/groups/StrategiesGroup';
import CandidatesGroup from './pages/groups/CandidatesGroup';
import SignalsGroup from './pages/groups/SignalsGroup';
import RiskGroup from './pages/groups/RiskGroup';
import TradingGroup from './pages/groups/TradingGroup';
import ReplayGroup from './pages/groups/ReplayGroup';
import StockPanoramaPage from './pages/StockPanoramaPage';
import SelectedStocksPage from './pages/SelectedStocksPage';
import BaseSignalsPage from './pages/BaseSignalsPage';
import Acad1ComparePage from './pages/Acad1ComparePage';
import AgentWorkbenchPage from './pages/AgentWorkbenchPage';
import SystemGroup from './pages/groups/SystemGroup';

/* ============ 路由段合法性 ============ */

/** 默认落地市场 = 启用列表第一个（默认四市场时为 cn，向后兼容） */
const DEFAULT_MARKET = MARKET_ROUTES[0] ?? 'cn';

function isMarketRoute(seg: string | undefined): boolean {
  return !!seg && (MARKET_ROUTES as readonly string[]).includes(seg);
}

const EXTRA_GROUP_KEYS = ['agent'];

function isGroupRoute(seg: string | undefined): boolean {
  return !!seg && ((GROUP_KEYS as readonly string[]).includes(seg) || EXTRA_GROUP_KEYS.includes(seg));
}

/* ============ 旧路径重定向（query 透传合并） ============ */

/**
 * 旧路径 → 新 /{market}/{group}?tab=x（<Navigate replace>）。
 * 新目标自带 ?tab= 时，旧 query（如 /kline?symbol=xxx）原样追加，收藏链接不断。
 */
function LegacyNavigate({ to }: { to: string }) {
  const location = useLocation();
  const [path, targetQuery] = to.split('?');
  const oldQuery = location.search; // 已带 ?
  const merged =
    targetQuery && oldQuery
      ? `?${targetQuery}&${oldQuery.slice(1)}`
      : targetQuery
        ? `?${targetQuery}`
        : oldQuery;
  return <Navigate to={`${path}${merged}`} replace />;
}

/* ============ 校验：/:market/:group 非法段 → /cn/overview ============ */

/** 非法段直接替换整个元素树（不闪壳） */
function ValidatedWorkspace() {
  const location = useLocation();
  const segs = location.pathname.split('/').filter(Boolean);
  if (!isMarketRoute(segs[0]) || !isGroupRoute(segs[1])) {
    return <Navigate to={`/${DEFAULT_MARKET}/overview`} replace />;
  }
  return <WorkspaceLayout />;
}

/** /:market 裸访问 → 该市场 overview（市场非法回 cn） */
function MarketIndexRedirect() {
  const location = useLocation();
  const seg = location.pathname.split('/').filter(Boolean)[0] ?? '';
  const m = isMarketRoute(seg) ? seg : DEFAULT_MARKET;
  return <Navigate to={`/${m}/overview`} replace />;
}

/* ============ 组容器分发：URL 第二段 → 8 组容器（Task 4） ============ */

const GROUP_COMPONENTS: Record<string, React.ComponentType> = {
  overview: OverviewGroup,
  strategies: StrategiesGroup,
  candidates: CandidatesGroup,
  signals: SignalsGroup,
  risk: RiskGroup,
  trading: TradingGroup,
  replay: ReplayGroup,
  agent: AgentWorkbenchPage,
  system: SystemGroup,
};

/** /:market/:group → 对应组容器（合法组已由 ValidatedWorkspace 校验） */
function GroupOutlet() {
  const { group } = useParams();
  const Comp = (group && GROUP_COMPONENTS[group]) || OverviewGroup;
  return <Comp />;
}

/* ============ 策略详情重定向：/strategies/:id → ?tab=strategies&id= ============ */

/** StrategiesPage 内 Link 到 /strategies/:id，重定向进 strategies 组由 ?id= 渲染详情 */
function StrategyDetailRedirect() {
  const { id = '' } = useParams();
  return <Navigate to={`/${DEFAULT_MARKET}/strategies?tab=strategies&id=${encodeURIComponent(id)}`} replace />;
}

/* ============ 根组件：路由表 ============ */

export default function App() {
  return (
    <>
      <Routes>
        {/* 大屏态：全屏无侧栏底栏（保留在 /bigscreen） */}
        <Route path="/bigscreen" element={<ProtectedRoute><BigscreenLayout /></ProtectedRoute>}>
          <Route index element={<HomeBigscreen />} />
        </Route>

        {/* 首页 = 选股全景动线（工作区壳），作为复盘/回测/策略逻辑/智能体的引导页 */}
        <Route path="/" element={<ProtectedRoute><Navigate to="/cn/replay?tab=flow" replace /></ProtectedRoute>} />

        {/* ACAD1 候选横向比较：/:market/acad1-compare */}
        <Route path="/:market/acad1-compare" element={<ProtectedRoute><WorkspaceLayout /></ProtectedRoute>}>
          <Route index element={<Acad1ComparePage />} />
        </Route>

        {/* 全市场基础信号：/:market/base-signals */}
        <Route path="/:market/base-signals" element={<ProtectedRoute><WorkspaceLayout /></ProtectedRoute>}>
          <Route index element={<BaseSignalsPage />} />
        </Route>

        {/* 选出来的股票多维表：/:market/selected */}
        <Route path="/:market/selected" element={<ProtectedRoute><WorkspaceLayout /></ProtectedRoute>}>
          <Route index element={<SelectedStocksPage />} />
        </Route>

        {/* 个股全景：/:market/stock/:code */}
        <Route path="/:market/stock/:code" element={<ProtectedRoute><WorkspaceLayout /></ProtectedRoute>}>
          <Route index element={<StockPanoramaPage />} />
        </Route>

        {/* 工作区态：/:market/:group（8 组）——非法段兜底 /cn/overview */}
        <Route
          path="/:market/:group"
          element={(
            <ProtectedRoute>
              <ValidatedWorkspace />
            </ProtectedRoute>
          )}
        >
          <Route index element={<GroupOutlet />} />
          <Route path="*" element={<GroupOutlet />} />
        </Route>
        <Route path="/:market" element={<MarketIndexRedirect />} />

        {/* 不展示登录/注册页：旧地址直接回首页（本地自动会话） */}
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="/register" element={<Navigate to="/" replace />} />
        {/* 个人中心（需登录） */}
        <Route path="/profile" element={<ProtectedRoute><ProfilePage /></ProtectedRoute>} />
        <Route path="/billing" element={<ProtectedRoute><BillingPage /></ProtectedRoute>} />

        {/* 策略详情（StrategiesPage Link 目标）→ strategies 组 ?id= 渲染详情 */}
        <Route path="/strategies/:id" element={<StrategyDetailRedirect />} />

        {/* 旧 37 路径重定向（plan Task 2 Step 2 表逐条照抄） */}
        {LEGACY_REDIRECTS.map(r => (
          <Route key={r.from} path={r.from} element={<LegacyNavigate to={r.to} />} />
        ))}

        {/* 兜底 */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <Toast />
      <CommandPalette />
    </>
  );
}
