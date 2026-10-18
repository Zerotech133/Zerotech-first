// Task 2-2：MyStrategiesPage 纳入订阅内置策略（只读标记「已订阅」，无自建操作按钮）。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// 保留 MarketContext 其他导出（billingApi 依赖 MARKETS），仅固定 useMarket→CN。
vi.mock('../contexts/MarketContext', async (orig) => ({
  ...(await orig<typeof import('../contexts/MarketContext')>()),
  useMarket: () => ({ market: 'CN', setMarket: vi.fn() }),
}));

// 私有列表 + 公共目录用 mock；其余 myStrategiesApi 函数保留真实（渲染不触发）。
vi.mock('../services/myStrategiesApi', async (orig) => ({
  ...(await orig<typeof import('../services/myStrategiesApi')>()),
  listMyStrategies: vi.fn(),
  listCatalog: vi.fn(),
}));
vi.mock('../services/billingApi', async (orig) => ({
  ...(await orig<typeof import('../services/billingApi')>()),
  listSubscriptions: vi.fn(),
}));

import '../i18n';
import MyStrategiesPage from '../pages/MyStrategiesPage';
import { listCatalog, listMyStrategies } from '../services/myStrategiesApi';
import { listSubscriptions } from '../services/billingApi';

const mListMine = vi.mocked(listMyStrategies);
const mListCatalog = vi.mocked(listCatalog);
const mListSubs = vi.mocked(listSubscriptions);

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('zhice_token', 'tok');
  localStorage.setItem('zhice_role', 'USER'); // canManageOwn=true → 私有行有自建操作
  mListMine.mockResolvedValue([
    {
      strategy_id: 'usi-priv-1', strategy_name: '我的私有策略', market: 'CN',
      visibility: 'PRIVATE', publication_status: 'PRIVATE', owner_user_id: 'u1',
      status: 'DRAFT', version_count: 1,
      versions: [{ strategy_version_id: 'priv-sv-1', version_no: 'v1', status: 'DRAFT' }],
    },
  ]);
  mListCatalog.mockResolvedValue([
    {
      strategy_id: 'v1-cn-strat-4', strategy_name: '缠论CN', description: '', market: 'CN',
      strategy_type: 'CHAN', status: 'ACTIVE', is_builtin: true, owner_user_id: null,
      version_count: 1,
      versions: [{ strategy_version_id: 'v1-cn-sv-4', version_no: 'v4', status: 'ACTIVE' }],
    },
  ]);
  mListSubs.mockResolvedValue({
    items: [{
      strategy_id: 'v1-cn-strat-4', valid_until: null, active: true,
      is_permanent: false, effective_status: 'active',
    }],
  });
});

afterEach(() => vi.restoreAllMocks());

function renderPage() {
  return render(<MemoryRouter><MyStrategiesPage /></MemoryRouter>);
}

describe('MyStrategiesPage 纳入订阅策略', () => {
  it('已订阅的内置策略出现在列表并带「已订阅」标记', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('缠论CN')).toBeInTheDocument());
    expect(screen.getByText('已订阅')).toBeInTheDocument();
  });

  it('订阅行只读：无编辑/删除/发布按钮，仅「查看」', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('缠论CN')).toBeInTheDocument());
    // 自建操作用 <a>（编辑/新版本）；仅私有行渲染 → 各 1 个，订阅行无
    expect(screen.getAllByText('编辑')).toHaveLength(1);
    expect(screen.getAllByText('新版本')).toHaveLength(1);
    // 订阅行提供只读「查看」入口
    expect(screen.getByText('查看')).toBeInTheDocument();
  });

  it('未生效订阅（expired/revoked）不纳入', async () => {
    mListSubs.mockResolvedValue({
      items: [{
        strategy_id: 'v1-cn-strat-4', valid_until: null, active: false,
        is_permanent: false, effective_status: 'expired',
      }],
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('我的私有策略')).toBeInTheDocument());
    expect(screen.queryByText('缠论CN')).not.toBeInTheDocument();
    expect(screen.queryByText('已订阅')).not.toBeInTheDocument();
  });
});
