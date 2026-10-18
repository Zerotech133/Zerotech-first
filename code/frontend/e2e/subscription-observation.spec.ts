// Task 3-2 订阅观察 E2E。
// - 真实 admin/123456 登录（经 vite proxy → 后端 8102），沿用真实会话。
// - route mock 订阅/目录/私有/观察端点：不依赖 dev 库数据、稳定可重复。
// 旅程：我的策略看到已订阅缠论（只读「查看」）→ 进详情页 → 平台持仓 + 成交·信号（入场/出场）。
import { test, expect, type Page } from '@playwright/test';

test.describe.configure({ mode: 'serial' });

const STRAT_ID = 'v1-cn-strat-4';
const VERSION_ID = 'v1-cn-sv-4';

const CATALOG = [{
  strategy_id: STRAT_ID, strategy_name: '缠论CN', description: '', market: 'CN',
  strategy_type: 'CHAN', status: 'ACTIVE', is_builtin: true, owner_user_id: null,
  version_count: 1,
  versions: [{ strategy_version_id: VERSION_ID, version_no: 'v4', status: 'ACTIVE' }],
}];

const SUBSCRIPTIONS = {
  items: [{
    strategy_id: STRAT_ID, valid_until: null, active: true,
    is_permanent: false, effective_status: 'active',
  }],
};

const DETAIL = {
  strategy_version_id: VERSION_ID, market: 'CN',
  account: {
    initial_capital: 100000, available_cash: 50000, market_value: 50000,
    total_equity: 100000, unrealized_pnl: 0, unrealized_pnl_pct: 0,
    position_ratio: 50, position_count: 1,
  },
  positions: [], trades: [],
  performance: { total_replays: 0, wins: 0, losses: 0, win_rate: 0, total_pnl: 0, avg_pnl: 0 },
};

const PLATFORM_POSITIONS = [{
  position_id: 'pos-1', ts_code: '600519', market: 'CN', direction: 'LONG',
  open_price: 1700, close_price: null, open_qty: 100, close_qty: 0, pnl_amount: 500,
  status: 'OPEN', account_id: 'acc-x', opened_at: '2026-09-01T09:30:00', closed_at: null,
}];

const PLATFORM_TRADES = [{
  fill_id: 'fil-1', market: 'CN', ts_code: '600519', fill_price: 1700, fill_qty: 100,
  fill_amount: 170000, realized_pnl: 0, fill_time: '2026-09-01T09:30:00', signal: 'entry',
}];

async function setupSubscriber(page: Page): Promise<void> {
  await page.goto('/login');
  await page.locator('input[type="password"]').fill('123456');
  await page.locator('input:not([type="password"])').first().fill('admin');
  await page.getByRole('button', { name: /登\s*录|Login|Sign/i }).first().click();
  await page.waitForURL((u) => !u.pathname.endsWith('/login'), { timeout: 15000 });

  // 订阅 / 目录 / 私有列表（我的策略页 load）。
  await page.route('**/api/v1/billing/subscriptions', (r) => r.fulfill({ json: SUBSCRIPTIONS }));
  await page.route('**/api/v1/my/strategies?*', (r) => r.fulfill({ json: [] }));
  await page.route('**/api/v1/strategies?*', (r) => r.fulfill({ json: CATALOG }));
  // 详情页 detail（version_id）。
  await page.route('**/api/v1/strategies/*/detail*', (r) => r.fulfill({ json: DETAIL }));
  // 观察端点（strategy_id）。
  await page.route(`**/api/v1/my/subscriptions/${STRAT_ID}/positions`, (r) =>
    r.fulfill({ json: PLATFORM_POSITIONS }));
  await page.route(`**/api/v1/my/subscriptions/${STRAT_ID}/trades`, (r) =>
    r.fulfill({ json: PLATFORM_TRADES }));
}

test.describe('订阅观察（route mock）', () => {
  test.beforeEach(async ({ page }) => { await setupSubscriber(page); });

  test('我的策略看到已订阅缠论（只读），进详情页看到平台持仓与成交·信号', async ({ page }) => {
    await page.goto('/cn/strategies?tab=mine');
    // 已订阅内置策略出现在「我的策略」，带只读标记，无自建操作、仅「查看」。
    await expect(page.getByText('缠论CN')).toBeVisible();
    await expect(page.getByText('已订阅')).toBeVisible();
    await expect(page.getByText('查看')).toBeVisible();

    await page.getByText('查看').click();

    // 详情页订阅观察区：平台持仓 + 成交·信号（入场 Tag）。
    await expect(page.getByText('平台持仓')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('成交·信号')).toBeVisible();
    await expect(page.getByText('入场')).toBeVisible();
    await expect(page.getByText('600519').first()).toBeVisible();
  });
});
