import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../contexts/MarketContext', async (orig) => ({
  ...(await orig<typeof import('../contexts/MarketContext')>()),
  useMarket: () => ({ market: 'CRYPTO', setMarket: vi.fn() }),
}));
vi.mock('../services/mirrorSimApi', () => ({
  createMirrorAccount: vi.fn(), getMirrorAccount: vi.fn(),
}));
vi.mock('../services/myStrategiesApi', () => ({ listCatalog: vi.fn().mockResolvedValue([
  { strategy_id: 's1', strategy_name: '虚拟币均值回归', market: 'CRYPTO', owner_user_id: null,
    versions: [{ strategy_version_id: 'sv1' }] }]) }));
vi.mock('../services/billingApi', () => ({ listSubscriptions: vi.fn().mockResolvedValue({
  items: [{ strategy_id: 's1', active: true, effective_status: 'active' }] }) }));
vi.mock('../services/subscriptionObservationApi', () => ({
  getSubscriptionPositions: vi.fn().mockResolvedValue([]),
  getSubscriptionTrades: vi.fn().mockResolvedValue([]),
}));
vi.mock('../services/api', () => ({ api: vi.fn().mockResolvedValue(
  { strategy_version_id: 'sv1', market: 'CRYPTO', strategy_name: '虚拟币均值回归',
    account: { initial_capital: 10000, available_cash: 10000, market_value: 0,
      total_equity: 10000, unrealized_pnl: 0, unrealized_pnl_pct: 0,
      position_ratio: 0, position_count: 0 },
    positions: [], trades: [],
    performance: { total_replays: 0, wins: 0, losses: 0, win_rate: 0, total_pnl: 0, avg_pnl: 0 },
  }) }));

import '../i18n';
import StrategyDetailPage from '../pages/StrategyDetailPage';
import { createMirrorAccount, getMirrorAccount } from '../services/mirrorSimApi';

const mCreate = vi.mocked(createMirrorAccount);
const mGet = vi.mocked(getMirrorAccount);

beforeEach(() => { vi.clearAllMocks(); localStorage.setItem('zhice_token', 't'); localStorage.setItem('zhice_role', 'USER'); });
afterEach(() => vi.restoreAllMocks());

describe('StrategyDetailPage 跟单模拟盘', () => {
  it('无账户显示"开模拟盘"，创建后展示我的模拟盘', async () => {
    mGet.mockRejectedValueOnce({ status: 404 });
    mCreate.mockResolvedValue({ mirror_account_id: 'm1', strategy_id: 's1', market: 'CRYPTO',
      initial_capital: '10000', cash_balance: '10000', status: 'active', created_at: null,
      positions: [], equity: 10000, total_pnl: 0, recent_fills: [] } as never);
    render(<MemoryRouter><StrategyDetailPage id="sv1" /></MemoryRouter>);
    const btn = await screen.findByRole('button', { name: /开\s*模拟盘|开模拟盘/ });
    fireEvent.click(btn);
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByRole('spinbutton'), { target: { value: '10000' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /确\s*定|OK|开\s*模拟盘|开模拟盘/ }));
    await waitFor(() => expect(mCreate).toHaveBeenCalledWith('s1', 10000));
    expect(await screen.findByText('我的模拟盘')).toBeInTheDocument();
  });
});
