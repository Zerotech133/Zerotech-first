// B3-6 测试：自助计费页展示永久权益。permanent 会员/订阅显示"永久有效"而非 —。
// C-6 测试：收银台弹窗按 grant_status 展示发放态（发放中/已发放/失败）。
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../services/billingApi', () => ({
  createOrder: vi.fn(),
  getOrder: vi.fn(),
  getMembership: vi.fn(),
  getPlans: vi.fn(),
  listBuiltinStrategies: vi.fn(),
  listSubscriptions: vi.fn(),
}));

import '../i18n';
import BillingPage from '../pages/BillingPage';
import {
  createOrder, getMembership, getPlans, listBuiltinStrategies, listSubscriptions,
} from '../services/billingApi';

const mCreateOrder = vi.mocked(createOrder);
const mGetMembership = vi.mocked(getMembership);
const mGetPlans = vi.mocked(getPlans);
const mListBuiltins = vi.mocked(listBuiltinStrategies);
const mListSubs = vi.mocked(listSubscriptions);

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('zhice_token', 'tok');
  mGetPlans.mockResolvedValue({
    currency: 'USDT', period: 'month', membership_monthly_usdt: 9.9,
    strategy_default_monthly_usdt: 99, strategy_overrides: {},
  });
  mListBuiltins.mockResolvedValue([
    {
      strategy_id: 'st-a', strategy_name: '策略A', description: '', market: 'US',
      strategy_type: 'MACD', status: 'ACTIVE', is_builtin: true, owner_user_id: null,
      version_count: 1, versions: [],
    },
  ] as never);
});

afterEach(() => vi.restoreAllMocks());

describe('BillingPage 永久权益展示', () => {
  it('永久会员显示"永久有效"', async () => {
    mGetMembership.mockResolvedValue({
      tier: 'member', valid_until: null, is_member: true,
      is_permanent: true, effective_status: 'permanent',
    });
    mListSubs.mockResolvedValue({ items: [] });
    render(<MemoryRouter><BillingPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getAllByText('永久有效').length).toBeGreaterThan(0));
  });

  it('永久订阅显示"永久有效"', async () => {
    mGetMembership.mockResolvedValue({
      tier: 'free', valid_until: null, is_member: false,
      is_permanent: false, effective_status: 'free',
    });
    mListSubs.mockResolvedValue({
      items: [{
        strategy_id: 'st-a', valid_until: null, active: true,
        is_permanent: true, effective_status: 'permanent',
      }],
    });
    render(<MemoryRouter><BillingPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('永久有效')).toBeInTheDocument());
  });
});

describe('BillingPage 收银台发放态展示', () => {
  // 收银台弹窗 orderStatus==='paid' 时按 grant_status 显示不同标签。
  const checkoutOrder = (grant_status: string, grant_pending: boolean) => ({
    order_id: 'o1', product_type: 'membership', product_ref: null, months: 1,
    amount_usdt: '9.9', trade_type: 'usdt', status: 'paid', pay_address: 'TRC20-addr',
    actual_amount: null, grant_status, grant_pending,
    payment_url: null, expiration_time: null, trade_id: null,
  });

  beforeEach(() => {
    mGetMembership.mockResolvedValue({
      tier: 'free', valid_until: null, is_member: false,
      is_permanent: false, effective_status: 'free',
    });
    mListSubs.mockResolvedValue({ items: [] });
  });

  async function clickBuy() {
    render(<MemoryRouter><BillingPage /></MemoryRouter>);
    // antd Button 在两个汉字间插空格（"购买"→"购 买"），用正则匹配可访问名。
    const buy = await screen.findByRole('button', { name: /购\s*买/ });
    fireEvent.click(buy);
  }

  it('已支付发放中显示"正在发放权益"', async () => {
    mCreateOrder.mockResolvedValue(checkoutOrder('pending', true) as never);
    await clickBuy();
    expect(await screen.findByText('支付成功，正在发放权益…')).toBeInTheDocument();
  });

  it('已支付已到账显示"权益已发放"', async () => {
    mCreateOrder.mockResolvedValue(checkoutOrder('granted', false) as never);
    await clickBuy();
    expect(await screen.findByText('权益已发放')).toBeInTheDocument();
  });

  it('已支付发放失败显示"发放失败"提示', async () => {
    mCreateOrder.mockResolvedValue(checkoutOrder('failed', false) as never);
    await clickBuy();
    expect(await screen.findByText('发放失败，已通知管理员处理')).toBeInTheDocument();
  });
});
