// Task 13 契约测试：通知渠道 API（wxpusher 绑定端点 + 渠道化偏好 channel query）。
// 与 myApis.test.ts 同范式：直接 spy global fetch，断言穿透 api() 封装后的真实
// URL/method/body。安全铁律：请求体绝不携带 user_id；patch body 不含 channel
// （channel 只进 URL query，owner 恒由后端从 JWT 取）。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  getMyNotificationPreferences,
  patchMyNotificationPreferences,
  getNotificationBindings,
  createWxPusherChallenge,
  unbindWxPusher,
  testWxPusher,
  type NotificationBinding,
  type NotificationPreference,
  type WxPusherChallenge,
} from '../services/notificationsApi';

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

beforeEach(() => {
  fetchSpy = vi.spyOn(globalThis, 'fetch');
  localStorage.setItem('zhice_token', 'contract-token');
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

// ---------- 类型化样例（兼作编译期 DTO 契约：字段缺失/错型 tsc 即报错） ----------

const prefsWxPusher: NotificationPreference = {
  user_id: 'u-alice',
  channel: 'wxpusher',
  market_reports_enabled: { CN: true, US: false },
  signal_enabled: true,
  fill_enabled: true,
  morning_report_enabled: false,
  evening_report_enabled: false,
  quiet_hours: null,
  timezone: 'Asia/Shanghai',
  locale: 'zh',
};

const bindings: NotificationBinding[] = [
  {
    channel: 'telegram',
    status: 'active',
    recipient_masked: '******1234',
    bound_at: '2026-09-01T08:00:00Z',
  },
  { channel: 'wxpusher', status: 'revoked', recipient_masked: null, bound_at: null },
];

const wxpusherChallenge: WxPusherChallenge = {
  qr_image_url: '/api/v1/notification-bindings/qr/abc123.png',
  expires_at: '2026-09-18T10:10:00Z',
};

// ---------- 渠道化偏好：channel 只进 query，绝不进 body ----------

describe('notificationsApi：渠道化偏好', () => {
  it('loads and patches preferences for an explicit channel', async () => {
    mockApiOnce(prefsWxPusher);
    const out = await getMyNotificationPreferences('wxpusher');
    const [url, init] = callOf(0);
    expect(url).toBe('/api/v1/my/notification-preferences?channel=wxpusher');
    expect(init?.method ?? 'GET').toBe('GET');
    expect(out.signal_enabled).toBe(true);

    mockApiOnce({ ...prefsWxPusher, signal_enabled: false });
    await patchMyNotificationPreferences('wxpusher', { signal_enabled: false });
    const [purl, pinit] = callOf(1);
    expect(purl).toBe('/api/v1/my/notification-preferences?channel=wxpusher');
    expect(pinit?.method).toBe('PATCH');
    // 恰为 {signal_enabled:false}：不含 channel / user_id
    expect(sentBody(pinit)).toEqual({ signal_enabled: false });
  });

  it('缺省 channel=telegram 兼容：get 无参调用 URL 带 ?channel=telegram', async () => {
    mockApiOnce({ ...prefsWxPusher, channel: 'telegram' });
    await getMyNotificationPreferences();
    expect(callOf(0)[0]).toBe('/api/v1/my/notification-preferences?channel=telegram');

    mockApiOnce({ ...prefsWxPusher, channel: 'telegram', fill_enabled: false });
    await patchMyNotificationPreferences('telegram', { fill_enabled: false });
    const [purl, pinit] = callOf(1);
    expect(purl).toBe('/api/v1/my/notification-preferences?channel=telegram');
    expect(sentBody(pinit)).toEqual({ fill_enabled: false });
  });
});

// ---------- WxPusher 绑定端点：无 user_id（owner 恒取自 JWT） ----------

describe('notificationsApi：WxPusher 绑定端点', () => {
  it('uses the WxPusher binding endpoints without user_id', async () => {
    mockApiOnce(wxpusherChallenge);
    const out = await createWxPusherChallenge();
    const [url, init] = callOf(0);
    expect(url).toBe('/api/v1/auth/me/notification-bindings/wxpusher/challenge');
    expect(url.endsWith('/auth/me/notification-bindings/wxpusher/challenge')).toBe(true);
    expect(init?.method).toBe('POST');
    expect(init?.body).toBeUndefined();
    expect(out.qr_image_url).toBe('/api/v1/notification-bindings/qr/abc123.png');
    expect(out.expires_at).toBe('2026-09-18T10:10:00Z');
  });

  it('getNotificationBindings GET /auth/me/notification-bindings', async () => {
    mockApiOnce(bindings);
    const out = await getNotificationBindings();
    const [url, init] = callOf(0);
    expect(url).toBe('/api/v1/auth/me/notification-bindings');
    expect(init?.method ?? 'GET').toBe('GET');
    expect(out).toEqual(bindings);
  });

  it('testWxPusher POST .../wxpusher/test，无 body', async () => {
    mockApiOnce({ ok: true });
    const out = await testWxPusher();
    const [url, init] = callOf(0);
    expect(url).toBe('/api/v1/auth/me/notification-bindings/wxpusher/test');
    expect(init?.method).toBe('POST');
    expect(init?.body).toBeUndefined();
    expect(out.ok).toBe(true);
  });

  it('unbindWxPusher DELETE .../wxpusher，无 body', async () => {
    mockApiOnce({ ok: true });
    await unbindWxPusher();
    const [url, init] = callOf(0);
    expect(url).toBe('/api/v1/auth/me/notification-bindings/wxpusher');
    expect(init?.method).toBe('DELETE');
    expect(init?.body).toBeUndefined();
  });
});
