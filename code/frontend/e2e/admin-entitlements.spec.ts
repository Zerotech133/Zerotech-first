// B3-7 管理员权益管理 E2E。
// - 真实链路 smoke：admin/123456 登录，经 vite proxy → 真实后端 8102，验证端到端 auth。
// - route mock：前端行为（摘要只读角色 + 会员实时态 + 抽屉四区 + 写超时"重试原操作"），
//   不依赖真实 dev 库数据、不写入 dev 库，稳定可重复。
import { test, expect, type Page } from '@playwright/test';

test.describe.configure({ mode: 'serial' });

// ---------- 真实链路 smoke ----------
test.describe('真实链路 smoke', () => {
  test('admin 登录成功后离开登录页', async ({ page }) => {
    await page.goto('/login');
    await page.locator('input[type="password"]').fill('123456');
    await page.locator('input:not([type="password"])').first().fill('admin');
    await page.getByRole('button', { name: /登\s*录|Login|Sign/i }).first().click();
    await expect(page).not.toHaveURL(/\/login/, { timeout: 15000 });
  });
});

// ---------- route mock 前端行为 ----------
const USER_ROW = {
  user_id: 'u-alice', username: 'alice', role: 'USER', is_active: true, auth_version: 3,
  membership: { effective_status: 'permanent', is_permanent: true, valid_until: null, version: 2 },
  active_subscription_count: 2,
};
const DETAIL = {
  user: { user_id: 'u-alice', username: 'alice', role: 'USER', is_active: true, auth_version: 3, email: null },
  membership: {
    tier: 'member', stored_status: 'active', effective_status: 'permanent',
    is_active: true, is_permanent: true, valid_until: null, revoked_at: null, version: 2,
  },
  subscriptions: [{
    strategy_id: 'st-a', stored_status: 'active', effective_status: 'active',
    is_active: true, is_permanent: false, valid_until: '2026-12-31T00:00:00Z',
    revoked_at: null, version: 1,
  }],
  recent_audits: [],
  payment_grant_summary: { pending: 0 },
};

async function setupAdmin(page: Page): Promise<void> {
  // 真实登录（经 vite proxy → 后端 8102），ProtectedRoute 用真实 token 验证通过。
  await page.goto('/login');
  await page.locator('input[type="password"]').fill('123456');
  await page.locator('input:not([type="password"])').first().fill('admin');
  await page.getByRole('button', { name: /登\s*录|Login|Sign/i }).first().click();
  await page.waitForURL((u) => !u.pathname.endsWith('/login'), { timeout: 15000 });
  // 登录后 mock 权益数据端点（不 mock /auth/me，沿用真实会话）。
  await page.route('**/api/v1/admin/entitlements/users?*', (r) => r.fulfill({
    json: { items: [USER_ROW], total: 1, page: 1, page_size: 20 },
  }));
  await page.route('**/api/v1/admin/entitlements/users/u-alice', (r) =>
    (r.request().method() === 'GET' ? r.fulfill({ json: DETAIL }) : r.continue()));
  await page.route('**/api/v1/billing/strategies', (r) => r.fulfill({
    json: { items: [{ strategy_id: 'st-a', strategy_name: '活跃策略', market: 'US' }] },
  }));
}

test.describe('权益管理（route mock）', () => {
  test.beforeEach(async ({ page }) => { await setupAdmin(page); });

  test('admin 看到用户摘要：只读角色 Tag + 会员实时态', async ({ page }) => {
    await page.goto('/cn/system?tab=users');
    await expect(page.getByText('alice')).toBeVisible();
    await expect(page.getByText('普通用户')).toBeVisible();
    await expect(page.getByText('永久')).toBeVisible();
  });

  test('打开权益抽屉展示四区与订阅', async ({ page }) => {
    await page.goto('/cn/system?tab=users');
    await page.getByRole('button', { name: '权限与权益' }).first().click();
    await expect(page.getByText('操作记录')).toBeVisible();
    await expect(page.getByText('st-a')).toBeVisible();
  });

  test('会员设置超时 → 出现"重试原操作"（未知结果不自动重发）', async ({ page }) => {
    await page.goto('/cn/system?tab=users');
    await page.getByRole('button', { name: '权限与权益' }).first().click();
    await page.getByText('操作记录').waitFor();

    let calls = 0;
    await page.route('**/api/v1/admin/entitlements/users/u-alice/membership', (r) => {
      calls += 1;
      if (calls === 1) return r.abort('failed'); // 首次网络失败 → retryable
      return r.fulfill({ json: { membership: { effective_status: 'permanent', version: 3, is_permanent: true } } });
    });

    const mem = page.getByTestId('ent-membership-section');
    await mem.getByRole('radio', { name: '永久' }).click();
    await mem.getByPlaceholder('操作原因').fill('E2E 赠送永久');
    await mem.getByRole('button', { name: '保存会员' }).click();

    await expect(mem.getByRole('button', { name: '重试原操作' })).toBeVisible({ timeout: 20000 });
  });
});

// ---------- C-7 追加：自助发放态 + 管理员支付发放管理 ----------
async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.locator('input[type="password"]').fill('123456');
  await page.locator('input:not([type="password"])').first().fill('admin');
  await page.getByRole('button', { name: /登\s*录|Login|Sign/i }).first().click();
  await page.waitForURL((u) => !u.pathname.endsWith('/login'), { timeout: 15000 });
}

const GRANT_ORDER = (grant_status: string, ambiguous = false) => ({
  order_id: ambiguous ? 'o-amb' : 'o-failed', user_id: 'u1',
  product_type: 'membership', product_ref: null, months: 1, amount_usdt: '9.90000000',
  status: 'paid', grant_status, grant_attempts: 3, automatic_grant_attempts: 3,
  next_grant_retry_at: null, last_grant_error_code: null, last_grant_error_summary: null,
  grant_lease_owner: null, grant_lease_until: null, historical_grant_ambiguous: ambiguous,
  grant_reviewed_at: null, grant_reviewed_by_user_id: null, grant_review_decision: null,
  granted_at: null, paid_at: '2026-09-10T00:00:00Z',
  product_name_snapshot: 'membership', market_snapshot: null,
});

test.describe('自助支付发放态（route mock）', () => {
  test('支付成功但发放中 → 显示"正在发放权益"', async ({ page }) => {
    await login(page);
    await page.route('**/api/v1/billing/plans', (r) => r.fulfill({
      json: { currency: 'USDT', period: 'month', membership_monthly_usdt: 9.9,
        strategy_default_monthly_usdt: 99, strategy_overrides: {} },
    }));
    await page.route('**/api/v1/billing/membership', (r) => r.fulfill({
      json: { tier: 'free', valid_until: null, is_member: false, is_permanent: false, effective_status: 'free' },
    }));
    await page.route('**/api/v1/billing/subscriptions', (r) => r.fulfill({ json: { items: [] } }));
    await page.route('**/api/v1/strategies*', (r) => r.fulfill({ json: [] }));
    await page.route('**/api/v1/billing/orders', (r) => (r.request().method() === 'POST'
      ? r.fulfill({ json: {
          order_id: 'o-e2e', product_type: 'membership', product_ref: null, months: 1,
          amount_usdt: '9.9', trade_type: 'usdt.trc20', status: 'paid', pay_address: 'TRC20-addr',
          actual_amount: null, grant_status: 'pending', grant_pending: true,
          payment_url: null, expiration_time: null, trade_id: null,
        } })
      : r.continue()));

    await page.goto('/billing');
    await page.getByRole('button', { name: /购\s*买/ }).first().click();
    await expect(page.getByText('支付成功，正在发放权益…')).toBeVisible({ timeout: 15000 });
  });
});

test.describe('管理员支付发放管理（route mock）', () => {
  test('失败发放手动重试 → 队列刷新为已发放', async ({ page }) => {
    await login(page);
    // 用 retried 标志而非调用计数：React StrictMode 会双触发列表加载 effect，
    // 计数法会误把初始态翻成 granted。retry 端点被调后才转 granted。
    let retried = false;
    await page.route('**/api/v1/admin/entitlements/payment-grants?*', (r) =>
      r.fulfill({ json: {
        items: [GRANT_ORDER(retried ? 'granted' : 'failed')], total: 1, page: 1, page_size: 20,
      } }));
    await page.route('**/api/v1/admin/entitlements/payment-grants/o-failed/retry', (r) => {
      retried = true;
      return r.fulfill({ json: { status: 'granted', order: GRANT_ORDER('granted') } });
    });

    await page.goto('/cn/system?tab=payment-grants');
    await expect(page.getByText('o-failed')).toBeVisible({ timeout: 15000 });
    await page.getByRole('button', { name: /重\s*试发放/ }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByPlaceholder(/操作原因/).fill('E2E 手动补发');
    await dialog.getByRole('button', { name: /重\s*试发放/ }).click();
    // 重试成功后列表刷新：状态 Tag 与操作列均显示"已发放"（取首个即状态 Tag）。
    await expect(page.getByText('已发放').first()).toBeVisible({ timeout: 15000 });
  });

  test('历史歧义订单显式复核', async ({ page }) => {
    await login(page);
    await page.route('**/api/v1/admin/entitlements/payment-grants?*', (r) =>
      r.fulfill({ json: { items: [GRANT_ORDER('manual_review', true)], total: 1, page: 1, page_size: 20 } }));
    let reviewed = false;
    await page.route('**/api/v1/admin/entitlements/payment-grants/o-amb/review', (r) => {
      reviewed = true;
      return r.fulfill({ json: { status: 'granted', order: GRANT_ORDER('granted') } });
    });

    await page.goto('/cn/system?tab=payment-grants');
    await expect(page.getByText('o-amb')).toBeVisible({ timeout: 15000 });
    await page.getByRole('button', { name: /复\s*核/ }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByText(/对账标记已发放/).click();
    await dialog.getByPlaceholder(/操作原因/).fill('E2E 对账无误');
    await dialog.getByRole('button', { name: /复\s*核/ }).click();
    await expect.poll(() => reviewed, { timeout: 15000 }).toBe(true);
  });
});
