// C-7 契约测试：管理员支付发放 API 客户端。spy global fetch，断言穿透 api() 的真实
// URL/method/body/headers，对齐后端 admin_entitlements payment-grants 路由。
//
// 铁律：写请求体绝不含 actor/user_id（actor 取自 JWT）；写必带 Idempotency-Key；
// 列表筛选拼 query；仅超时可 retryable。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  listPaymentGrants,
  retryGrant,
  reviewGrant,
} from '../services/adminPaymentGrantsApi';

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

beforeEach(() => {
  fetchSpy = vi.spyOn(globalThis, 'fetch');
  localStorage.setItem('zhice_token', 'contract-token');
  localStorage.setItem('zhice_role', 'ADMIN');
  localStorage.setItem('zhice_lang', 'zh');
});

afterEach(() => {
  fetchSpy.mockRestore();
  localStorage.clear();
  vi.restoreAllMocks();
});

const ORDER = {
  order_id: 'o1', user_id: 'u1', product_type: 'membership', product_ref: null,
  months: 1, amount_usdt: '9.90000000', status: 'paid', grant_status: 'failed',
  grant_attempts: 3, automatic_grant_attempts: 3, next_grant_retry_at: null,
  last_grant_error_code: 'billing.grant_failed', last_grant_error_summary: 'boom',
  grant_lease_owner: null, grant_lease_until: null, historical_grant_ambiguous: false,
  grant_reviewed_at: null, grant_reviewed_by_user_id: null, grant_review_decision: null,
  granted_at: null, paid_at: '2026-09-10T00:00:00Z',
  product_name_snapshot: 'membership', market_snapshot: null,
};

describe('listPaymentGrants', () => {
  it('默认无筛选：仅 page/page_size', async () => {
    mockOnce({ items: [ORDER], total: 1, page: 1, page_size: 20 });
    const res = await listPaymentGrants({ page: 1, pageSize: 20 });
    const [url, init] = callOf();
    expect(url).toContain('/admin/entitlements/payment-grants?');
    expect(url).toContain('page=1');
    expect(url).toContain('page_size=20');
    expect(url).not.toContain('grant_status=');
    expect(url).not.toContain('user_id=');
    expect((init?.method ?? 'GET')).toBe('GET');
    expect(res.items[0].order_id).toBe('o1');
  });

  it('带 grant_status + user_id 筛选拼进 query', async () => {
    mockOnce({ items: [], total: 0, page: 2, page_size: 20 });
    await listPaymentGrants({ page: 2, pageSize: 20, grantStatus: 'failed', userId: ' u1 ' });
    const [url] = callOf();
    expect(url).toContain('grant_status=failed');
    expect(url).toContain('user_id=u1'); // trim 后编码
    expect(url).toContain('page=2');
  });
});

describe('retryGrant', () => {
  it('POST retry：body 仅 reason，带 Idempotency-Key，无 actor', async () => {
    mockOnce({ status: 'granted', order: { ...ORDER, grant_status: 'granted' } });
    const res = await retryGrant('o1', { reason: '手动补发' }, 'idem-retry-1');
    const [url, init] = callOf();
    expect(url).toContain('/admin/entitlements/payment-grants/o1/retry');
    expect(init?.method).toBe('POST');
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(body).toEqual({ reason: '手动补发' });
    expect(body).not.toHaveProperty('actor_user_id');
    expect(body).not.toHaveProperty('user_id');
    expect(headersOf(init)['Idempotency-Key']).toBe('idem-retry-1');
    expect(res.status).toBe('granted');
  });

  it('order_id 需编码', async () => {
    mockOnce({ status: 'retrying', order: ORDER });
    await retryGrant('o/1 x', { reason: 'r' }, 'k');
    const [url] = callOf();
    expect(url).toContain('/payment-grants/o%2F1%20x/retry');
  });
});

describe('reviewGrant', () => {
  it('POST review：body 含 decision + reason，带 Idempotency-Key', async () => {
    mockOnce({ status: 'pending', order: { ...ORDER, historical_grant_ambiguous: true } });
    const res = await reviewGrant('o1', { decision: 'RETRY', reason: '核对无误' }, 'idem-review-1');
    const [url, init] = callOf();
    expect(url).toContain('/admin/entitlements/payment-grants/o1/review');
    expect(init?.method).toBe('POST');
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(body).toEqual({ decision: 'RETRY', reason: '核对无误' });
    expect(headersOf(init)['Idempotency-Key']).toBe('idem-review-1');
    expect(res.status).toBe('pending');
  });

  it('MARK_GRANTED 决策透传', async () => {
    mockOnce({ status: 'granted', order: { ...ORDER, grant_status: 'granted' } });
    await reviewGrant('o1', { decision: 'MARK_GRANTED', reason: '对账' }, 'k');
    const [, init] = callOf();
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(body.decision).toBe('MARK_GRANTED');
  });
});
