import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { GROUP_KEYS } from '../config/navigation';

const ALL_MARKETS = ['CN', 'US', 'HK', 'CRYPTO'] as const;
/** Market 类型基于全集，保持不变（避免过滤收窄破坏其他文件的类型/label 查询） */
export type Market = typeof ALL_MARKETS[number];

/**
 * 启用市场列表：可用 Vite 环境变量 VITE_ENABLED_MARKETS（逗号分隔，如 "US,CRYPTO"）过滤。
 * 不设置时 = 四市场全开（严格向后兼容）。
 */
const _enabledMarkets = (import.meta.env.VITE_ENABLED_MARKETS ?? '')
  .split(',')
  .map((s: string) => s.trim().toUpperCase())
  .filter(Boolean);
export const MARKETS = (_enabledMarkets.length
  ? ALL_MARKETS.filter((m) => _enabledMarkets.includes(m))
  : ALL_MARKETS) as readonly Market[];

export const MARKET_META: Record<Market, { label: string; currency: string; short: string }> = {
  CN: { label: 'A股', currency: 'CNY', short: '¥' },
  US: { label: '美股', currency: 'USD', short: '$' },
  HK: { label: '港股', currency: 'HKD', short: 'HK$' },
  CRYPTO: { label: '加密', currency: 'USDT', short: '₮' },
};

/** URL 市场段（小写）→ 市场 API 枚举（大写） */
const ROUTE_TO_MARKET: Record<string, Market> = {
  cn: 'CN',
  us: 'US',
  hk: 'HK',
  crypto: 'CRYPTO',
};

/** 从 /:market URL 段派生市场（非法/缺失回 CN；spec §2.3 不再用 localStorage） */
export function marketFromRoute(seg: string | undefined): Market {
  return (seg && ROUTE_TO_MARKET[seg.toLowerCase()]) || 'CN';
}

interface MarketContextValue {
  market: Market;
  setMarket: (m: Market) => void;
  currency: string;
  currencySymbol: string;
}

const MarketContext = createContext<MarketContextValue | null>(null);

/**
 * 从 /:market/:group URL 派生 [market, group]。
 * 用 useLocation 而非 useParams：Provider 挂在 <Routes> 外层，useParams 恒为空。
 */
function useMarketFromUrl(): [Market, string | undefined] {
  const location = useLocation();
  const segs = location.pathname.split('/').filter(Boolean);
  const market = marketFromRoute(segs[0]);
  const group = segs[1] && GROUP_KEYS.includes(segs[1]) ? segs[1] : undefined;
  return [market, group];
}

/**
 * MarketProvider：market 由 /:market URL 段派生（useLocation）。
 * ⚠️ 必须挂在 BrowserRouter 内部（main.tsx）。
 * setMarket(m) = navigate(`/${m 小写}/${当前组 || overview}`)，保持当前所在组。
 */
export function MarketProvider({ children }: { children: ReactNode }) {
  const [market, group] = useMarketFromUrl();
  const navigate = useNavigate();

  const value = useMemo<MarketContextValue>(() => {
    const meta = MARKET_META[market];
    return {
      market,
      currency: meta.currency,
      currencySymbol: meta.short,
      setMarket: (m: Market) => {
        navigate(`/${m.toLowerCase()}/${group ?? 'overview'}`);
      },
    };
  }, [market, group, navigate]);

  return <MarketContext.Provider value={value}>{children}</MarketContext.Provider>;
}

export function useMarket(): MarketContextValue {
  const ctx = useContext(MarketContext);
  if (!ctx) throw new Error('useMarket must be used within MarketProvider');
  return ctx;
}

/**
 * 统一中国股市语义：红涨绿跌。
 * 保留 market 参数仅为兼容既有调用；不再按市场切换。
 */
export function pnlColor(value: number, _market: Market): string {
  if (value === 0) return '#8B93A7';
  return value > 0 ? '#F0524D' : '#12A150';
}

/** 格式化金额，带正负号 */
export function fmtMoney(value: number, digits = 2): string {
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

/** 格式化百分比 */
export function fmtPct(value: number, digits = 2): string {
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}%`;
}
