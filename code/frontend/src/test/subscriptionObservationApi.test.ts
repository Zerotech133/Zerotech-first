import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getSubscriptionPositions, getSubscriptionTrades } from '../services/subscriptionObservationApi';

let fetchSpy: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  fetchSpy = vi.spyOn(globalThis, 'fetch');
  localStorage.setItem('zhice_token', 't'); localStorage.setItem('zhice_lang', 'zh');
});
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

function mockOnce(data: unknown) {
  fetchSpy.mockResolvedValueOnce({ ok: true, status: 200, json: async () => data, text: async () => JSON.stringify(data) } as unknown as Response);
}

describe('subscriptionObservationApi', () => {
  it('getSubscriptionPositions GET 正确 URL + encode', async () => {
    mockOnce([{ ts_code: '000001' }]);
    await getSubscriptionPositions('v1-cn-strat-4');
    expect(fetchSpy.mock.calls[0][0]).toBe('/api/v1/my/subscriptions/v1-cn-strat-4/positions');
  });
  it('getSubscriptionTrades 透传 AbortSignal', async () => {
    mockOnce([]);
    const ac = new AbortController();
    await getSubscriptionTrades('v1-cn-strat-4', ac.signal);
    expect((fetchSpy.mock.calls[0][1] as RequestInit)?.signal).toBe(ac.signal);
  });
});
