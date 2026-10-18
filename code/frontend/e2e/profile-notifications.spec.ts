// Task 16 E2E：Profile 通知渠道（WxPusher 扫码绑定 + 双渠道偏好矩阵）。
// - 纯 route mock（合成数据）：登录 /auth/login、会话 /auth/me、绑定总览、双渠道
//   偏好、Telegram status、扫码 challenge 全部 page.route 拦截——不经真实后端，
//   不依赖 dev 库数据，稳定可重复；只验证前端 UI 状态机。
// - 旅程一：VIEWER 登录 → 微信通知卡未绑定 → 扫码（二维码可见）→ 轮询发现
//   active（UID 脱敏可见）→ 矩阵「微信 策略信号」开关只 PATCH wxpusher 的
//   signal_enabled，Telegram 列不受影响（仍 checked、零 PATCH）。
// - 旅程二：英文 locale 渲染全部渠道控件，无中文回退。
import { test, expect, type Page } from '@playwright/test';

type Channel = 'wxpusher' | 'telegram';
type Role = 'viewer';

/** 合成 VIEWER 账号（仅存在于 route mock 应答中）。 */
const VIEWER = {
  username: 'viewer',
  userId: 'u-e2e-viewer',
  password: 'e2e-synthetic-password',
};

/** 合成偏好（后端 get_or_create 缺省全开；矩阵四事件 + 渠道级配置字段）。 */
function syntheticPref(channel: Channel, overrides: Record<string, unknown> = {}) {
  return {
    user_id: VIEWER.userId,
    channel,
    market_reports_enabled: {},
    signal_enabled: true,
    fill_enabled: true,
    morning_report_enabled: true,
    evening_report_enabled: true,
    system_alerts_enabled: true,
    quiet_hours: null,
    timezone: 'Asia/Shanghai',
    locale: 'zh',
    ...overrides,
  };
}

/** mockNotificationBindings 入参：unbound=该渠道无绑定行（列表缺席）。 */
interface BindingFixture {
  channel: Channel;
  status: 'active' | 'none';
  masked?: string;
}

export function bound(channel: Channel, masked: string): BindingFixture {
  return { channel, status: 'active', masked };
}

export function unbound(channel: Channel): BindingFixture {
  return { channel, status: 'none' };
}

/** 每个 page 一份可变 mock 状态（switchBindingMockToBound 就地翻新应答数据）。 */
interface NotifMockState {
  bindings: {
    channel: Channel;
    status: string;
    recipient_masked: string | null;
    bound_at: string | null;
  }[];
  prefs: Record<Channel, Record<string, unknown>>;
  patches: Record<Channel, Record<string, unknown>[]>;
}

const mockStates = new WeakMap<Page, NotifMockState>();

/**
 * 登录为给定角色（route mock）：拦截 /auth/login 与 /auth/me，走真实登录页 UI，
 * 落地回 /profile（先 goto /profile 触发 ProtectedRoute 重定向，state.from 回跳）。
 * locale 经 addInitScript 预置 zhice_lang（i18n 启动即读），登录应答 locale 同步跟随。
 * 同时预挂一组「空但合法」的通知端点应答，避免落地瞬间的真实网络请求；后续
 * mockNotificationBindings 注册的路由优先级更高（Playwright 后注册先匹配）。
 */
async function loginAs(page: Page, role: Role, opts: { locale?: 'zh' | 'en' } = {}): Promise<void> {
  const locale = opts.locale ?? 'zh';
  await page.addInitScript((lang) => localStorage.setItem('zhice_lang', lang), locale);

  await page.route('**/api/v1/auth/login', (route) =>
    route.fulfill({
      json: { token: 'e2e-synthetic-token', role: 'VIEWER', username: VIEWER.username, locale },
    }),
  );
  await page.route('**/api/v1/auth/me', (route) =>
    route.fulfill({
      json: { user_id: VIEWER.userId, username: VIEWER.username, role: 'VIEWER', email: null },
    }),
  );
  // 预挂空态：登录落地到 /profile 的首屏加载不触网（随后被正式 mock 覆盖）。
  await page.route('**/api/v1/auth/me/notification-bindings', (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route('**/api/v1/my/notification-preferences*', (route) =>
    route.fulfill({ json: syntheticPref('telegram') }),
  );
  await page.route('**/api/v1/auth/me/telegram/status', (route) =>
    route.fulfill({ json: { bound: false, chat_id: null, bot_username: null } }),
  );

  await page.goto('/profile'); // 无 token → ProtectedRoute 重定向 /login（state.from=/profile）
  await page.locator('input[type="password"]').fill(VIEWER.password);
  await page.locator('input:not([type="password"])').first().fill(VIEWER.username);
  await page.getByRole('button', { name: /登\s*录|Login|Sign/i }).first().click();
  await page.waitForURL((u) => u.pathname.endsWith('/profile'), { timeout: 15000 });
}

/**
 * 挂通知端点正式 mock（覆盖 loginAs 的预挂空态）：
 * - GET /auth/me/notification-bindings → fixtures 中 active 行（unbound=缺席）；
 * - GET/PATCH /my/notification-preferences?channel= → 状态偏好；PATCH 记录请求体
 *   并回服务端合并后的真相（onSaved 受控回显，不做本地乐观）；
 * - GET /auth/me/telegram/status → 手动绑定态（bot 未配置）；
 * - POST .../wxpusher/challenge → 合成二维码 URL（远期过期，测试窗口内不失效）。
 */
async function mockNotificationBindings(page: Page, fixtures: BindingFixture[]): Promise<void> {
  const state: NotifMockState = {
    bindings: fixtures
      .filter((f) => f.status === 'active')
      .map((f) => ({
        channel: f.channel,
        status: 'active',
        recipient_masked: f.masked ?? null,
        bound_at: '2026-09-18T00:00:00Z',
      })),
    prefs: { wxpusher: syntheticPref('wxpusher'), telegram: syntheticPref('telegram') },
    patches: { wxpusher: [], telegram: [] },
  };
  mockStates.set(page, state);

  await page.route('**/api/v1/auth/me/notification-bindings', (route) =>
    route.fulfill({ json: state.bindings }),
  );
  await page.route('**/api/v1/auth/me/notification-bindings/wxpusher/challenge', (route) =>
    route.fulfill({
      json: {
        qr_image_url: 'https://e2e.invalid/wxpusher-qr-synthetic.png',
        expires_at: '2099-01-01T00:00:00Z',
      },
    }),
  );
  await page.route('**/api/v1/my/notification-preferences*', (route) => {
    const url = new URL(route.request().url());
    const channel = (url.searchParams.get('channel') ?? 'telegram') as Channel;
    if (route.request().method() === 'PATCH') {
      const body = JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>;
      state.patches[channel].push(body);
      return route.fulfill({ json: syntheticPref(channel, { ...state.prefs[channel], ...body }) });
    }
    return route.fulfill({ json: syntheticPref(channel, state.prefs[channel]) });
  });
  await page.route('**/api/v1/auth/me/telegram/status', (route) =>
    route.fulfill({ json: { bound: true, chat_id: '****5678', bot_username: null } }),
  );
}

/**
 * 把某渠道 mock 翻成已绑定（模拟用户在另一端完成扫码确认）：
 * 绑定总览出现 active 行 + 该渠道偏好可读。WxPusherBind 的轮询（3s 周期）发现
 * active 后停轮询并通知容器刷新，无需重新注册路由。
 */
async function switchBindingMockToBound(
  page: Page,
  channel: Channel,
  masked: string,
): Promise<void> {
  const state = mockStates.get(page);
  if (!state) throw new Error('switchBindingMockToBound: 先调用 mockNotificationBindings');
  state.bindings = [
    ...state.bindings.filter((b) => b.channel !== channel),
    { channel, status: 'active', recipient_masked: masked, bound_at: '2026-09-18T00:00:00Z' },
  ];
}

/**
 * 断言矩阵开关触发过且仅触发过预期 PATCH：等待该渠道出现请求记录，首个请求体
 * 深等于 expected（矩阵契约：每次切换只 PATCH 所改渠道的所改字段）。
 */
async function expectPreferencePatch(
  page: Page,
  channel: Channel,
  expected: Record<string, unknown>,
): Promise<void> {
  const state = mockStates.get(page);
  if (!state) throw new Error('expectPreferencePatch: 先调用 mockNotificationBindings');
  await expect.poll(() => state.patches[channel].length, { timeout: 10000 }).toBeGreaterThan(0);
  expect(state.patches[channel][0]).toEqual(expected);
}

// ──────────────────────────────────────────────────────────────────────────────

test.describe('Profile 通知渠道（route mock）', () => {
  test('VIEWER binds WxPusher and saves channel preferences independently', async ({ page }) => {
    await loginAs(page, 'viewer');
    await mockNotificationBindings(page, [unbound('wxpusher'), bound('telegram', '****5678')]);
    await page.goto('/profile');

    // 微信通知卡（未绑定）：提供扫码绑定入口。
    await expect(page.getByRole('heading', { name: '微信通知' })).toBeVisible();
    await page.getByRole('button', { name: '扫码绑定微信' }).click();

    // 扫码中：后端生成的二维码 URL 直出 <img>（alt 固定文案）。
    await expect(page.locator('img[alt="微信绑定二维码"]')).toBeVisible();

    // 另一端完成扫码确认 → 轮询发现 active：脱敏 UID 可见，矩阵微信列放开。
    await switchBindingMockToBound(page, 'wxpusher', 'UID_***3456');
    await expect(page.getByText('UID_***3456')).toBeVisible({ timeout: 10000 });

    // 「微信 策略信号」开关：checked→off，只 PATCH wxpusher 的 signal_enabled。
    await page.getByRole('switch', { name: '微信 策略信号' }).click();
    await expectPreferencePatch(page, 'wxpusher', { signal_enabled: false });
    await expect(page.getByRole('switch', { name: '微信 策略信号' })).not.toBeChecked();

    // Telegram 列独立：开关仍 checked（服务端真相），且零 PATCH（不受微信列影响）。
    await expect(page.getByRole('switch', { name: 'Telegram 策略信号' })).toBeChecked();
    expect(mockStates.get(page)?.patches.telegram).toEqual([]);
  });

  test('English UI renders all channel controls without Chinese fallback', async ({ page }) => {
    await loginAs(page, 'viewer', { locale: 'en' });
    await mockNotificationBindings(page, [unbound('wxpusher'), bound('telegram', '****5678')]);
    await page.goto('/profile');

    await expect(page.getByRole('heading', { name: 'WeChat notifications' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'WeChat' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Telegram' })).toBeVisible();
    // 未绑定微信卡的动作入口也是英文（无中文回退）。
    await expect(page.getByRole('button', { name: 'Scan QR to bind WeChat' })).toBeVisible();
  });
});
