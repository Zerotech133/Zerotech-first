// B3-1 契约测试：管理员权益 API 客户端 + 幂等写 + 请求体构建 + 错误分类。
// 直接 spy global fetch，断言穿透 api() 封装后的真实 URL/method/body/headers。
// 对齐 Gate 0 后端契约（admin_entitlements 路由 + billing/strategies + 错误码体 {detail:{code}}）。
//
// 安全/可靠性铁律：
//  - 写请求体绝不携带 user_id/actor（actor 恒由后端从 JWT 取）。
//  - 写请求必带 Idempotency-Key；api() 强制 Authorization/Accept-Language 不被覆盖。
//  - permanent 不发 valid_until；until 一律转 UTC ISO；strategy_ids 去重排序 + versions 裁剪一致。
//  - 仅网络/超时可重试（retryable=true）；4xx/5xx 已知失败不可重试。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../services/api';
import {
  listUsers,
  getUserDetail,
  listAudits,
  listGrantableStrategies,
  setMembership,
  revokeMembership,
  setSubscriptions,
  revokeSubscriptions,
  changeRole,
} from '../services/adminEntitlementsApi';
import {
  AdminApiError,
  classifyError,
  normalizeStrategyIds,
  toUtcIso,
  newIdempotencyKey,
  buildMembershipBody,
  buildSetSubscriptionsBody,
  buildRevokeSubscriptionsBody,
  type UsersPage,
  type UserDetail,
} from '../components/admin-entitlements/entitlementModel';

let fetchSpy: ReturnType<typeof vi.spyOn>;

function mockOnce(data: unknown, status = 200): void {
  const text = JSON.stringify(data);
  fetchSpy.mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
    text: async () => text,
  } as unknown as Response);
}

function callOf(index = 0): [string, RequestInit | undefined] {
  expect(fetchSpy.mock.calls.length).toBeGreaterThan(index);
  return fetchSpy.mock.calls[index] as [string, RequestInit | undefined];
}

function headersOf(init?: RequestInit): Record<string, string> {
  return (init?.headers ?? {}) as Record<string, string>;
}

function sentBody(init?: RequestInit): Record<string, unknown> {
  expect(init?.body).toBeTruthy();
  return JSON.parse(String(init?.body)) as Record<string, unknown>;
}

beforeEach(() => {
  fetchSpy = vi.spyOn(globalThis, 'fetch');
  localStorage.setItem('zhice_token', 'contract-token');
  localStorage.setItem('zhice_role', 'ADMIN');
  localStorage.setItem('zhice_lang', 'zh');
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

// ---------- entitlementModel 纯函数 ----------

describe('entitlementModel 纯函数', () => {
  it('normalizeStrategyIds 去重 + 升序排序', () => {
    expect(normalizeStrategyIds(['st-b', 'st-a', 'st-b', 'st-c'])).toEqual([
      'st-a',
      'st-b',
      'st-c',
    ]);
  });

  it('toUtcIso 把带时区日期归一到 UTC ISO（Z 结尾）', () => {
    expect(toUtcIso('2026-09-10T12:00:00+08:00')).toBe('2026-09-10T04:00:00.000Z');
    expect(toUtcIso(new Date('2026-01-01T00:00:00Z'))).toBe('2026-01-01T00:00:00.000Z');
  });

  it('buildMembershipBody permanent 不带 valid_until', () => {
    expect(
      buildMembershipBody({ mode: 'permanent', reason: ' 赠送 ', version: 0 }),
    ).toEqual({ mode: 'permanent', reason: ' 赠送 ', version: 0 });
  });

  it('buildMembershipBody until 带 UTC ISO valid_until', () => {
    expect(
      buildMembershipBody({
        mode: 'until',
        validUntil: '2026-09-10T12:00:00+08:00',
        reason: '续费',
        version: 2,
      }),
    ).toEqual({
      mode: 'until',
      valid_until: '2026-09-10T04:00:00.000Z',
      reason: '续费',
      version: 2,
    });
  });

  it('buildMembershipBody until 缺日期抛错（不发无效请求）', () => {
    expect(() =>
      buildMembershipBody({ mode: 'until', reason: 'x', version: 1 }),
    ).toThrow();
  });

  it('buildSetSubscriptionsBody 去重排序 + versions 仅保留选中键', () => {
    const body = buildSetSubscriptionsBody({
      strategyIds: ['st-b', 'st-a', 'st-b'],
      mode: 'permanent',
      reason: '批量授权',
      versions: { 'st-a': 1, 'st-b': 0, 'st-z': 9 },
    });
    expect(body.strategy_ids).toEqual(['st-a', 'st-b']);
    expect(body.versions).toEqual({ 'st-a': 1, 'st-b': 0 });
    expect(body).not.toHaveProperty('valid_until');
  });

  it('buildSetSubscriptionsBody 缺选中策略版本抛 versions_mismatch', () => {
    expect(() =>
      buildSetSubscriptionsBody({
        strategyIds: ['st-a', 'st-b'],
        mode: 'permanent',
        reason: 'x',
        versions: { 'st-a': 1 },
      }),
    ).toThrow(AdminApiError);
  });

  it('buildRevokeSubscriptionsBody 去重排序 + versions 裁剪', () => {
    const body = buildRevokeSubscriptionsBody({
      strategyIds: ['st-b', 'st-a'],
      reason: '撤销',
      versions: { 'st-a': 3, 'st-b': 2 },
    });
    expect(body.strategy_ids).toEqual(['st-a', 'st-b']);
    expect(body.versions).toEqual({ 'st-a': 3, 'st-b': 2 });
  });
});

// ---------- classifyError ----------

describe('classifyError', () => {
  it('网络错误标记 retryable=true', () => {
    const netErr = Object.assign(new Error('网络连接失败'), { retryable: true });
    const e = classifyError(netErr);
    expect(e).toBeInstanceOf(AdminApiError);
    expect(e.retryable).toBe(true);
    expect(e.status).toBeUndefined();
  });

  it('HTTP 409 版本冲突不可重试且带 code/status', () => {
    const httpErr = Object.assign(new Error('版本冲突'), {
      status: 409,
      code: 'entitlements.version_conflict',
    });
    const e = classifyError(httpErr);
    expect(e.retryable).toBe(false);
    expect(e.status).toBe(409);
    expect(e.code).toBe('entitlements.version_conflict');
  });

  it('HTTP 422 不可重试', () => {
    const httpErr = Object.assign(new Error('参数错误'), {
      status: 422,
      code: 'entitlements.invalid_expiry',
    });
    expect(classifyError(httpErr).retryable).toBe(false);
  });
});

// ---------- api() headers 合并（B3-1 修复：init.headers 不得覆盖 Authorization/Accept-Language） ----------

describe('api() headers 合并', () => {
  it('自定义头与 Authorization/Accept-Language 共存，且不被 init.headers 覆盖', async () => {
    mockOnce({ ok: true });
    await api('/probe', {
      method: 'POST',
      body: JSON.stringify({ a: 1 }),
      headers: { 'Idempotency-Key': 'k-123456', Authorization: 'Bearer EVIL' },
    });
    const headers = headersOf(callOf()[1]);
    expect(headers['Idempotency-Key']).toBe('k-123456');
    expect(headers.Authorization).toBe('Bearer contract-token'); // 真实 token 胜出
    expect(headers['Accept-Language']).toBe('zh');
    expect(headers['Content-Type']).toBe('application/json');
  });

  it('GET 透传 AbortSignal', async () => {
    mockOnce({ items: [] });
    const controller = new AbortController();
    await listGrantableStrategies(controller.signal);
    expect(callOf()[1]?.signal).toBe(controller.signal);
  });
});

// ---------- 401 清 token + role（非 auth 端点） ----------

describe('401 会话失效清理', () => {
  let hrefSpy: string[];
  beforeEach(() => {
    hrefSpy = [];
    Object.defineProperty(window, 'location', {
      value: { set href(v: string) { hrefSpy.push(v); }, get href() { return ''; } },
      writable: true,
      configurable: true,
    });
  });

  it('admin 端点 401 同时清除 zhice_token 与 zhice_role 并跳登录', async () => {
    mockOnce({ detail: { code: 'auth.session_revoked' } }, 401);
    await expect(getUserDetail('u-1')).rejects.toThrow();
    expect(localStorage.getItem('zhice_token')).toBeNull();
    expect(localStorage.getItem('zhice_role')).toBeNull();
    expect(hrefSpy).toContain('/login');
  });
});

// ---------- 只读查询 API ----------

describe('adminEntitlementsApi 查询', () => {
  const usersPage: UsersPage = {
    items: [
      {
        user_id: 'u-1',
        username: 'alice',
        role: 'USER',
        is_active: true,
        auth_version: 3,
        membership: {
          effective_status: 'permanent',
          is_permanent: true,
          valid_until: null,
          version: 2,
        },
        active_subscription_count: 2,
      },
    ],
    total: 1,
    page: 1,
    page_size: 20,
  };

  it('listUsers GET /admin/entitlements/users?page&page_size&query', async () => {
    mockOnce(usersPage);
    const out = await listUsers({ page: 2, pageSize: 50, query: 'ali ce' });
    const [url, init] = callOf();
    expect(url).toBe(
      '/api/v1/admin/entitlements/users?page=2&page_size=50&query=ali%20ce',
    );
    expect(init?.method ?? 'GET').toBe('GET');
    expect(out.items[0]?.membership.effective_status).toBe('permanent');
  });

  it('listUsers 无 query 时不带 query 参数', async () => {
    mockOnce(usersPage);
    await listUsers({ page: 1, pageSize: 20 });
    expect(callOf()[0]).toBe('/api/v1/admin/entitlements/users?page=1&page_size=20');
  });

  it('getUserDetail GET 并 encodeURIComponent 路径', async () => {
    const detail = { user: { user_id: 'u/1' } } as unknown as UserDetail;
    mockOnce(detail);
    await getUserDetail('u/1');
    expect(callOf()[0]).toBe('/api/v1/admin/entitlements/users/u%2F1');
  });

  it('listAudits 把带时区时间转 UTC ISO 作为查询参数', async () => {
    mockOnce({ items: [], total: 0, page: 1, page_size: 50 });
    await listAudits({
      page: 1,
      pageSize: 50,
      targetUserId: 'u-1',
      fromAt: '2026-09-10T12:00:00+08:00',
    });
    const url = callOf()[0];
    expect(url).toContain('/api/v1/admin/entitlements/audits?');
    expect(url).toContain('page=1');
    expect(url).toContain('page_size=50');
    expect(url).toContain('target_user_id=u-1');
    expect(url).toContain('from_at=2026-09-10T04%3A00%3A00.000Z');
  });

  it('listGrantableStrategies GET /billing/strategies', async () => {
    mockOnce({ items: [{ strategy_id: 'st-a', strategy_name: 'A', market: 'US' }] });
    const out = await listGrantableStrategies();
    expect(callOf()[0]).toBe('/api/v1/billing/strategies');
    expect(out.items[0]?.strategy_id).toBe('st-a');
  });
});

// ---------- 写 API（幂等键 + 请求体构建 + 无 actor 键） ----------

describe('adminEntitlementsApi 写操作', () => {
  it('setMembership PUT membership：permanent 无 valid_until + Idempotency-Key', async () => {
    mockOnce({ membership: { effective_status: 'permanent', version: 1, is_permanent: true } });
    await setMembership(
      'u-1',
      { mode: 'permanent', reason: '赠送永久会员', version: 0 },
      'idem-perm-000001',
    );
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/admin/entitlements/users/u-1/membership');
    expect(init?.method).toBe('PUT');
    const body = sentBody(init);
    expect(body).toEqual({ mode: 'permanent', reason: '赠送永久会员', version: 0 });
    expect(body).not.toHaveProperty('valid_until');
    expect(body).not.toHaveProperty('user_id');
    expect(headersOf(init)['Idempotency-Key']).toBe('idem-perm-000001');
  });

  it('setMembership until：valid_until 转 UTC ISO', async () => {
    mockOnce({ membership: { effective_status: 'active', version: 3, is_permanent: false } });
    await setMembership(
      'u-1',
      { mode: 'until', validUntil: '2026-12-31T23:59:59+08:00', reason: '续费', version: 2 },
      'idem-until-00001',
    );
    const body = sentBody(callOf()[1]);
    expect(body.valid_until).toBe('2026-12-31T15:59:59.000Z');
    expect(body.version).toBe(2);
  });

  it('revokeMembership POST membership/revoke', async () => {
    mockOnce({ membership: { effective_status: 'revoked', version: 4, is_permanent: false } });
    await revokeMembership('u-1', { reason: '违规', version: 3 }, 'idem-revm-00001');
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/admin/entitlements/users/u-1/membership/revoke');
    expect(init?.method).toBe('POST');
    expect(sentBody(init)).toEqual({ reason: '违规', version: 3 });
    expect(headersOf(init)['Idempotency-Key']).toBe('idem-revm-00001');
  });

  it('setSubscriptions PUT subscriptions：去重排序 + versions 裁剪一致', async () => {
    mockOnce({ subscriptions: [] });
    await setSubscriptions(
      'u-1',
      {
        strategyIds: ['st-b', 'st-a', 'st-b'],
        mode: 'until',
        validUntil: '2026-09-10T12:00:00+08:00',
        reason: '批量',
        versions: { 'st-a': 1, 'st-b': 0, 'st-noise': 5 },
      },
      'idem-subs-00001',
    );
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/admin/entitlements/users/u-1/subscriptions');
    expect(init?.method).toBe('PUT');
    const body = sentBody(init);
    expect(body.strategy_ids).toEqual(['st-a', 'st-b']);
    expect(body.versions).toEqual({ 'st-a': 1, 'st-b': 0 });
    expect(body.valid_until).toBe('2026-09-10T04:00:00.000Z');
    expect(headersOf(init)['Idempotency-Key']).toBe('idem-subs-00001');
  });

  it('revokeSubscriptions POST subscriptions/revoke', async () => {
    mockOnce({ subscriptions: [] });
    await revokeSubscriptions(
      'u-1',
      { strategyIds: ['st-a'], reason: '撤销', versions: { 'st-a': 2 } },
      'idem-revs-00001',
    );
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/admin/entitlements/users/u-1/subscriptions/revoke');
    expect(init?.method).toBe('POST');
    expect(sentBody(init)).toEqual({
      strategy_ids: ['st-a'],
      reason: '撤销',
      versions: { 'st-a': 2 },
    });
  });

  it('写操作把 HTTP 409 规整为不可重试 AdminApiError（带 code）', async () => {
    mockOnce({ detail: { code: 'entitlements.version_conflict' } }, 409);
    const err = await setMembership(
      'u-1',
      { mode: 'permanent', reason: 'x', version: 0 },
      'idem-conf-00001',
    ).catch((e) => e);
    expect(err).toBeInstanceOf(AdminApiError);
    expect(err.status).toBe(409);
    expect(err.code).toBe('entitlements.version_conflict');
    expect(err.retryable).toBe(false);
  });
});

// ---------- 幂等键生成 ----------

describe('newIdempotencyKey', () => {
  it('生成 8~128 位可打印 ASCII 幂等键且每次不同', () => {
    const k1 = newIdempotencyKey();
    const k2 = newIdempotencyKey();
    expect(k1).not.toBe(k2);
    expect(k1.length).toBeGreaterThanOrEqual(8);
    expect(k1.length).toBeLessThanOrEqual(128);
    expect(/^[\x20-\x7e]+$/.test(k1)).toBe(true);
  });
});

// ---------- 角色变更（/auth 审计事务） ----------

describe('changeRole', () => {
  it('PUT /auth/users/{id}/role 带 reason + Idempotency-Key', async () => {
    mockOnce({ user_id: 'u-1', username: 'alice', role: 'TRADER', auth_version: 4 });
    const out = await changeRole(
      'u-1',
      { role: 'TRADER', authVersion: 3, reason: '晋升交易员' },
      'idem-role-000001',
    );
    const [url, init] = callOf();
    expect(url).toBe('/api/v1/auth/users/u-1/role');
    expect(init?.method).toBe('PUT');
    expect(sentBody(init)).toEqual({ role: 'TRADER', auth_version: 3, reason: '晋升交易员' });
    expect(headersOf(init)['Idempotency-Key']).toBe('idem-role-000001');
    expect(out.auth_version).toBe(4);
  });

  it('409 版本冲突规整为不可重试 AdminApiError', async () => {
    mockOnce({ detail: { code: 'auth.auth_version_conflict' } }, 409);
    const err = await changeRole(
      'u-1',
      { role: 'VIEWER', authVersion: 1, reason: 'x' },
      'idem-role-000002',
    ).catch((e) => e);
    expect(err).toBeInstanceOf(AdminApiError);
    expect(err.status).toBe(409);
    expect(err.code).toBe('auth.auth_version_conflict');
    expect(err.retryable).toBe(false);
  });
});
