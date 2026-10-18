import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMirrorAccount, getMirrorAccount } from '../services/mirrorSimApi';

let fetchSpy: ReturnType<typeof vi.spyOn>;
function mockOnce(data: unknown, status = 200) {
  fetchSpy.mockResolvedValueOnce({ ok: status < 300, status,
    json: async () => data, text: async () => JSON.stringify(data) } as unknown as Response);
}
beforeEach(() => { fetchSpy = vi.spyOn(globalThis, 'fetch'); localStorage.setItem('zhice_token', 't'); });
afterEach(() => { fetchSpy.mockRestore(); localStorage.clear(); });

describe('mirrorSimApi', () => {
  it('create POST body strategy_id+initial_capital', async () => {
    mockOnce({ mirror_account_id: 'm1', strategy_id: 's1', status: 'active' });
    await createMirrorAccount('s1', 10000);
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/my/mirror-accounts');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ strategy_id: 's1', initial_capital: 10000 });
  });
  it('detail GET by strategy', async () => {
    mockOnce({ strategy_id: 's1', status: 'active', positions: [], equity: 10000, total_pnl: 0 });
    const d = await getMirrorAccount('s1');
    expect((fetchSpy.mock.calls[0] as [string])[0]).toContain('/my/mirror-accounts/s1');
    expect(d.status).toBe('active');
  });
});
