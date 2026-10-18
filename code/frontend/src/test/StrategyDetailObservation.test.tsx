// Task 3-1：订阅者在策略详情页看到平台持仓 + 成交·信号（entry/exit）；非订阅者不渲染、不拉数据。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../contexts/MarketContext', async (orig) => ({
  ...(await orig<typeof import('../contexts/MarketContext')>()),
  useMarket: () => ({ market: 'CN', setMarket: vi.fn() }),
}));
vi.mock('../services/api', () => ({ api: vi.fn() }));
vi.mock('../services/myStrategiesApi', async (orig) => ({
  ...(await orig<typeof import('../services/myStrategiesApi')>()),
  listCatalog: vi.fn(),
}));
vi.mock('../services/billingApi', async (orig) => ({
  ...(await orig<typeof import('../services/billingApi')>()),
  listSubscriptions: vi.fn(),
}));
vi.mock('../services/subscriptionObservationApi', () => ({
  getSubscriptionPositions: vi.fn(),
  getSubscriptionTrades: vi.fn(),
}));

import '../i18n';
import StrategyDetailPage from '../pages/StrategyDetailPage';
import { api } from '../services/api';
import { listCatalog } from '../services/myStrategiesApi';
import { listSubscriptions } from '../services/billingApi';
import { getSubscriptionPositions, getSubscriptionTrades } from '../services/subscriptionObservationApi';

const mApi = vi.mocked(api);
const mListCatalog = vi.mocked(listCatalog);
const mListSubs = vi.mocked(listSubscriptions);
const mGetPos = vi.mocked(getSubscriptionPositions);
const mGetTrades = vi.mocked(getSubscriptionTrades);

const DETAIL = {
  strategy_version_id: 'v1-cn-sv-4', market: 'CN',
  account: {
    initial_capital: 100000, available_cash: 50000, market_value: 50000,
    total_equity: 100000, unrealized_pnl: 0, unrealized_pnl_pct: 0,
    position_ratio: 50, position_count: 1,
  },
  positions: [], trades: [],
  performance: { total_replays: 0, wins: 0, losses: 0, win_rate: 0, total_pnl: 0, avg_pnl: 0 },
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('zhice_token', 'tok');
  localStorage.setItem('zhice_role', 'USER');
  mApi.mockResolvedValue(DETAIL as never);
  mListCatalog.mockResolvedValue([
    {
      strategy_id: 'v1-cn-strat-4', strategy_name: '缠论CN', description: '', market: 'CN',
      strategy_type: 'CHAN', status: 'ACTIVE', is_builtin: true, owner_user_id: null,
      version_count: 1,
      versions: [{ strategy_version_id: 'v1-cn-sv-4', version_no: 'v4', status: 'ACTIVE' }],
    },
  ]);
  mGetPos.mockResolvedValue([
    {
      position_id: 'pos-1', ts_code: '600519', market: 'CN', direction: 'LONG',
      open_price: 1700, close_price: null, open_qty: 100, close_qty: 0, pnl_amount: 500,
      status: 'OPEN', account_id: 'acc-x', opened_at: '2026-09-01T09:30:00', closed_at: null,
    },
  ]);
  mGetTrades.mockResolvedValue([
    {
      fill_id: 'fil-1', market: 'CN', ts_code: '600519', fill_price: 1700, fill_qty: 100,
      fill_amount: 170000, realized_pnl: 0, fill_time: '2026-09-01T09:30:00', signal: 'entry',
    },
  ]);
});

afterEach(() => vi.restoreAllMocks());

function renderDetail() {
  return render(<MemoryRouter><StrategyDetailPage id="v1-cn-sv-4" /></MemoryRouter>);
}

describe('StrategyDetailPage 订阅观察区', () => {
  it('订阅者渲染平台持仓 + 成交·信号（入场 Tag）', async () => {
    mListSubs.mockResolvedValue({
      items: [{ strategy_id: 'v1-cn-strat-4', valid_until: null, active: true, is_permanent: false, effective_status: 'active' }],
    });
    renderDetail();
    await waitFor(() => expect(screen.getByText('平台持仓')).toBeInTheDocument());
    expect(screen.getByText('成交·信号')).toBeInTheDocument();
    // 持仓/成交表均含标的 600519（至少 2 处）
    await waitFor(() => expect(screen.getAllByText('600519').length).toBeGreaterThanOrEqual(2));
    expect(screen.getByText('入场')).toBeInTheDocument();
    expect(mGetPos).toHaveBeenCalledWith('v1-cn-strat-4', expect.anything());
  });

  it('非订阅者不渲染观察区且不拉观察数据', async () => {
    mListSubs.mockResolvedValue({ items: [] });
    renderDetail();
    // 等 detail KPI 渲染完成
    await waitFor(() => expect(screen.getByText('v1-cn-sv-4')).toBeInTheDocument());
    await waitFor(() => expect(mListSubs).toHaveBeenCalled());
    expect(screen.queryByText('平台持仓')).not.toBeInTheDocument();
    expect(screen.queryByText('成交·信号')).not.toBeInTheDocument();
    expect(mGetPos).not.toHaveBeenCalled();
    expect(mGetTrades).not.toHaveBeenCalled();
  });
});
