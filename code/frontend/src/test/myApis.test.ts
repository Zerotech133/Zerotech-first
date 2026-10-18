// Task 2 契约测试：typed owner-scoped API clients（Phase D 多用户前端）。
// 直接 spy global fetch（而非 mock api 模块），断言穿透 api() 封装后的真实
// URL/method/body——证明客户端复用 services/api（BASE=/api/v1、Bearer token、
// JSON 头、{code} 错误翻译）且普通端点全部落在 /api/v1/my/*（公共目录读除外）。
// 安全铁律：所有写请求体绝不携带 user_id/owner_user_id（owner 恒取自 JWT）。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  listCatalog,
  listMyStrategies,
  createPrivateStrategy,
  updatePrivateStrategy,
  deleteMyStrategy,
  requestPublication,
  createMyStrategyVersion,
  getMyStrategySpec,
  listMyInstances,
  createMyInstance,
  updateMyInstance,
  enableMyInstance,
  disableMyInstance,
  deleteMyInstance,
  requestUpgrade,
  type CatalogStrategy,
  type MyStrategy,
  type MyStrategyInstance,
  type MyStrategyVersion,
  type StrategyCode,
} from '../services/myStrategiesApi';
import {
  listMyAccounts,
  createMyAccount,
  type MyAccount,
} from '../services/myAccountsApi';
import {
  getMyNotificationPreferences,
  patchMyNotificationPreferences,
  createTelegramChallenge,
  getTelegramBindingStatus,
  unbindTelegram,
  type NotificationPreference,
} from '../services/notificationsApi';

// ---------- fetch mock 范式（成功路径；错误路径由 api() 统一处理） ----------

let fetchSpy: ReturnType<typeof vi.spyOn>;

function mockApiOnce(data: unknown): void {
  const text = JSON.stringify(data);
  fetchSpy.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => data,
    text: async () => text,
  } as unknown as Response);
}

function callOf(index = 0): [string, RequestInit | undefined] {
  expect(fetchSpy.mock.calls.length).toBeGreaterThan(index);
  return fetchSpy.mock.calls[index] as [string, RequestInit | undefined];
}

function sentBody(init?: RequestInit): Record<string, unknown> {
  expect(init?.body).toBeTruthy();
  return JSON.parse(String(init?.body)) as Record<string, unknown>;
}

/** 安全铁律：写请求体（若存在）绝不含 user_id / owner_user_id。 */
function expectNoOwnerKeys(init?: RequestInit): void {
  if (!init?.body) return;
  const parsed = JSON.parse(String(init.body)) as Record<string, unknown>;
  expect(parsed).not.toHaveProperty('user_id');
  expect(parsed).not.toHaveProperty('owner_user_id');
}

beforeEach(() => {
  fetchSpy = vi.spyOn(globalThis, 'fetch');
  localStorage.setItem('zhice_token', 'contract-token');
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

// ---------- 类型化样例（兼作编译期 DTO 契约：字段缺失/错型 tsc 即报错） ----------

const catalogStrategy: CatalogStrategy = {
  strategy_id: 'st-macd',
  strategy_name: 'MACD 趋势',
  description: '公开趋势策略',
  market: 'CN',
  strategy_type: 'MACD',
  status: 'ACTIVE',
  is_builtin: true,
  owner_user_id: null,
  version_count: 2,
  // 真形态：version_no 带 v 前缀；版本状态为 STAGING/ACTIVE（无 PUBLISHED 态）
  versions: [
    { strategy_version_id: 'sv-macd-1', version_no: 'v1', status: 'ACTIVE' },
    { strategy_version_id: 'sv-macd-2', version_no: 'v2', status: 'STAGING' },
  ],
};

// Phase D T4 起列表响应含版本侧字段（后端 my_strategies._serialize）：
// description/version_count/latest_version_no/versions（升序）。
const myStrategy: MyStrategy = {
  strategy_id: 'st-mine',
  strategy_name: '我的私有策略',
  market: 'CN',
  visibility: 'PRIVATE',
  publication_status: 'NONE',
  owner_user_id: 'u-alice',
  status: 'ACTIVE',
  description: '我的描述',
  version_count: 2,
  latest_version_no: 'v2',
  versions: [
    { strategy_version_id: 'st-mine-v1', version_no: 'v1', status: 'ACTIVE' },
    { strategy_version_id: 'st-mine-v2-0a1b', version_no: 'v2', status: 'STAGING' },
  ],
};

const myInstance: MyStrategyInstance = {
  user_strategy_id: 'usi-1',
  user_id: 'u-alice',
  strategy_version_id: 'sv-macd-2',
  account_id: 'acc-1',
  enabled: true,
  auto_execute: false,
  execution_mode: 'MANUAL',
  capital_quota: '100000',
  max_position_ratio: '0.1',
  risk_parameters: {},
  strategy_parameters: {},
  notification_preferences: {},
  // 升级意图三元组（后端 my_strategy_instances._serialize，d0d3a8f 起）
  upgrade_available_version_id: 'sv-macd-3',
  pending_upgrade_version_id: null,
  upgrade_requested_at: null,
};

const myStrategyCode: StrategyCode = {
  kind: 'spec',
  editable: true,
  content: '{"spec_version":1,"market":"CN","universe":{"type":"all"}}',
  strategy_type: 'V2_EXTERNAL',
  strategy_version_id: 'st-mine-v2-0a1b',
};

const myAccount: MyAccount = {
  account_id: 'acc-1',
  user_id: 'u-alice',
  market: 'US',
  currency: 'USD',
  account_type: 'SPOT',
  initial_capital: '500000',
  cash_balance: '450000',
  reserved_cash: '0',
  status: 'ACTIVE',
};

const prefs: NotificationPreference = {
  user_id: 'u-alice',
  channel: 'telegram',
  market_reports_enabled: { CN: true, US: false },
  signal_enabled: true,
  fill_enabled: true,
  morning_report_enabled: false,
  evening_report_enabled: false,
  quiet_hours: null,
  timezone: 'Asia/Shanghai',
  locale: 'zh',
};

// ---------- myStrategiesApi：目录与私有策略 ----------

describe('myStrategiesApi：目录与私有策略', () => {
  it('listCatalog 走公共目录 GET /strategies（唯一允许的非 /my/ 路径）', async () => {
    mockApiOnce([catalogStrategy]);
    const out = await listCatalog('CN');
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/strategies?market=CN');
    expect(init?.method ?? 'GET').toBe('GET');
    expect(out).toEqual([catalogStrategy]);
  });

  it('listCatalog 无过滤时不带查询串', async () => {
    mockApiOnce([]);
    await listCatalog();
    expect(callOf()[0]).toBe('/api/v1/strategies');
  });

  it('listMyStrategies 走 /my/ 并携带 Bearer token（复用 api() 封装）', async () => {
    mockApiOnce([myStrategy]);
    const out = await listMyStrategies('US');
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/my/strategies?market=US');
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer contract-token');
    expect(out).toEqual([myStrategy]);
  });

  it('createPrivateStrategy POST /my/strategies，body 无 owner 键', async () => {
    mockApiOnce(myStrategy);
    const out = await createPrivateStrategy({
      strategy_name: '我的私有策略',
      market: 'CN',
      spec: { params: { fast: 5 } },
    });
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/my/strategies');
    expect(init?.method).toBe('POST');
    expect(sentBody(init)).toEqual({
      strategy_name: '我的私有策略',
      market: 'CN',
      spec: { params: { fast: 5 } },
    });
    expectNoOwnerKeys(init);
    expect(out.strategy_id).toBe('st-mine');
  });

  it('updatePrivateStrategy PUT /my/strategies/{id}，body 仅 name/description', async () => {
    mockApiOnce({ ...myStrategy, strategy_name: '新名' });
    await updatePrivateStrategy('st-mine', { name: '新名', description: '说明' });
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/my/strategies/st-mine');
    expect(init?.method).toBe('PUT');
    expect(sentBody(init)).toEqual({ name: '新名', description: '说明' });
    expectNoOwnerKeys(init);
  });

  it('requestPublication POST /my/strategies/{id}/publish，无 body', async () => {
    mockApiOnce({
      request_id: 'pr-1',
      source_strategy_id: 'st-mine',
      status: 'PENDING_REVIEW',
    });
    const out = await requestPublication('st-mine');
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/my/strategies/st-mine/publish');
    expect(init?.method).toBe('POST');
    expect(init?.body).toBeUndefined();
    expect(out.request_id).toBe('pr-1');
  });

  it('createMyStrategyVersion POST /my/strategies/{id}/versions，body 仅 spec（Phase D T4）', async () => {
    const created: MyStrategyVersion = {
      strategy_version_id: 'st-mine-v3-9f8e',
      strategy_id: 'st-mine',
      version_no: 'v3',
      status: 'STAGING',
    };
    mockApiOnce(created);
    const spec = { spec_version: 1, market: 'CN', universe: { type: 'all' } };
    const out = await createMyStrategyVersion('st-mine', spec);
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/my/strategies/st-mine/versions');
    expect(init?.method).toBe('POST');
    expect(sentBody(init)).toEqual({ spec });
    expectNoOwnerKeys(init);
    expect(out.version_no).toBe('v3');
    expect(out.strategy_id).toBe('st-mine');
  });

  it('getMyStrategySpec GET /strategies/{id}/code（spec 预填，公共读路径）', async () => {
    mockApiOnce(myStrategyCode);
    const out = await getMyStrategySpec('st-mine');
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/strategies/st-mine/code');
    expect(init?.method ?? 'GET').toBe('GET');
    expect(out.kind).toBe('spec');
    expect(out.strategy_version_id).toBe('st-mine-v2-0a1b');
  });
});

// ---------- myStrategiesApi：运行实例 ----------

describe('myStrategiesApi：运行实例', () => {
  it('listMyInstances GET /my/strategy-instances?market&enabled', async () => {
    mockApiOnce([myInstance]);
    const out = await listMyInstances('CN', true);
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/my/strategy-instances?market=CN&enabled=true');
    expect(init?.method ?? 'GET').toBe('GET');
    // 金额字段后端 Decimal→str
    expect(out[0]?.capital_quota).toBe('100000');
    expect(out[0]?.enabled).toBe(true);
  });

  it('createMyInstance POST：启用向导默认独立子账户（NEW_DEDICATED）', async () => {
    mockApiOnce(myInstance);
    await createMyInstance({
      strategy_version_id: 'sv-macd-2',
      account_mode: 'NEW_DEDICATED',
      initial_capital: 100000,
      execution_mode: 'AUTO',
      auto_execute: true,
      max_position_ratio: 0.2,
    });
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/my/strategy-instances');
    expect(init?.method).toBe('POST');
    // 请求体金额是 JSON number（后端 pydantic float），响应才是 string
    expect(sentBody(init)).toEqual({
      strategy_version_id: 'sv-macd-2',
      account_mode: 'NEW_DEDICATED',
      initial_capital: 100000,
      execution_mode: 'AUTO',
      auto_execute: true,
      max_position_ratio: 0.2,
    });
    expectNoOwnerKeys(init);
  });

  it('createMyInstance POST：绑定既有共享账户（account_id）', async () => {
    mockApiOnce(myInstance);
    await createMyInstance({
      strategy_version_id: 'sv-macd-2',
      account_id: 'acc-1',
      initial_capital: 5000,
    });
    const [, init] = callOf();
    expect(sentBody(init)).toEqual({
      strategy_version_id: 'sv-macd-2',
      account_id: 'acc-1',
      initial_capital: 5000,
    });
    expectNoOwnerKeys(init);
  });

  it('updateMyInstance PATCH /my/strategy-instances/{id}', async () => {
    mockApiOnce({ ...myInstance, auto_execute: false });
    await updateMyInstance('usi-1', { auto_execute: false, capital_quota: 50000 });
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/my/strategy-instances/usi-1');
    expect(init?.method).toBe('PATCH');
    expect(sentBody(init)).toEqual({ auto_execute: false, capital_quota: 50000 });
    expectNoOwnerKeys(init);
  });

  it('enable/disable PATCH 子路径且无 body', async () => {
    mockApiOnce(myInstance);
    await enableMyInstance('usi-1');
    expect(callOf(0)[0]).toBe('/api/v1/my/strategy-instances/usi-1/enable');
    expect(callOf(0)[1]?.method).toBe('PATCH');
    expect(callOf(0)[1]?.body).toBeUndefined();

    mockApiOnce({ ...myInstance, enabled: false });
    await disableMyInstance('usi-1');
    expect(callOf(1)[0]).toBe('/api/v1/my/strategy-instances/usi-1/disable');
    expect(callOf(1)[1]?.method).toBe('PATCH');
  });

  it('requestUpgrade PATCH .../upgrade body {target_version_id}（后端端点待落地，先钉契约）', async () => {
    mockApiOnce(myInstance);
    await requestUpgrade('usi-1', 'sv-macd-2');
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/my/strategy-instances/usi-1/upgrade');
    expect(init?.method).toBe('PATCH');
    expect(sentBody(init)).toEqual({ target_version_id: 'sv-macd-2' });
    expectNoOwnerKeys(init);
  });

  // B5 生命周期收口：owner 侧删除（策略 / 实例）均走 DELETE 且无 body。
  it('deleteMyStrategy DELETE /my/strategies/{id}，无 body（409 绑定实例走 api() 翻译）', async () => {
    mockApiOnce({ deleted: 'strategy-CN-abc' });
    await deleteMyStrategy('strategy-CN-abc');
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/my/strategies/strategy-CN-abc');
    expect(init?.method).toBe('DELETE');
    expect(init?.body).toBeUndefined();
  });

  it('deleteMyInstance DELETE /my/strategy-instances/{id}，无 body', async () => {
    mockApiOnce({ deleted: 'usi-1', account_id: null });
    await deleteMyInstance('usi-1');
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/my/strategy-instances/usi-1');
    expect(init?.method).toBe('DELETE');
    expect(init?.body).toBeUndefined();
  });
});

// ---------- myAccountsApi ----------

describe('myAccountsApi', () => {
  it('listMyAccounts GET /my/accounts?market', async () => {
    mockApiOnce([myAccount]);
    const out = await listMyAccounts('HK');
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/my/accounts?market=HK');
    expect(init?.method ?? 'GET').toBe('GET');
    expect(out[0]?.cash_balance).toBe('450000');
  });

  it('createMyAccount POST：body 数字 initial_capital 且无 user_id', async () => {
    mockApiOnce(myAccount);
    await createMyAccount({ market: 'US', account_type: 'SPOT', initial_capital: 50000 });
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/my/accounts');
    expect(init?.method).toBe('POST');
    expect(sentBody(init)).toEqual({ market: 'US', account_type: 'SPOT', initial_capital: 50000 });
    expectNoOwnerKeys(init);
  });
});

// ---------- notificationsApi ----------

describe('notificationsApi', () => {
  it('getMyNotificationPreferences GET（缺省 channel=telegram）', async () => {
    mockApiOnce(prefs);
    const out = await getMyNotificationPreferences();
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/my/notification-preferences?channel=telegram');
    expect(init?.method ?? 'GET').toBe('GET');
    expect(out.locale).toBe('zh');
  });

  it('patchMyNotificationPreferences PATCH：strict body 且无 user_id/channel', async () => {
    mockApiOnce({ ...prefs, signal_enabled: false, locale: 'en' });
    await patchMyNotificationPreferences('telegram', {
      signal_enabled: false,
      quiet_hours: { start: '22:00', end: '07:00' },
      locale: 'en',
    });
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/my/notification-preferences?channel=telegram');
    expect(init?.method).toBe('PATCH');
    expect(sentBody(init)).toEqual({
      signal_enabled: false,
      quiet_hours: { start: '22:00', end: '07:00' },
      locale: 'en',
    });
    expectNoOwnerKeys(init);
  });

  it('createTelegramChallenge POST /auth/me/telegram/challenge，无 body', async () => {
    mockApiOnce({
      url: 'https://t.me/zhice_bot?start=synthetic-token',
      expires_at: '2026-09-07T10:10:00Z',
    });
    const out = await createTelegramChallenge();
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/auth/me/telegram/challenge');
    expect(init?.method).toBe('POST');
    expect(init?.body).toBeUndefined();
    expect(out.url).toContain('https://t.me/');
    expect(out.expires_at).toBe('2026-09-07T10:10:00Z');
  });

  it('getTelegramBindingStatus GET status（chat_id 后端已脱敏）', async () => {
    mockApiOnce({ bound: true, chat_id: '******1234', bot_username: 'zhice_bot' });
    const out = await getTelegramBindingStatus();
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/auth/me/telegram/status');
    expect(init?.method ?? 'GET').toBe('GET');
    expect(out.bound).toBe(true);
    expect(out.chat_id).toBe('******1234');
  });

  it('unbindTelegram DELETE /auth/me/telegram', async () => {
    mockApiOnce({ ok: true, telegram_chat_id: null });
    await unbindTelegram();
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/auth/me/telegram');
    expect(init?.method).toBe('DELETE');
  });
});
