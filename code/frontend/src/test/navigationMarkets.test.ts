import { afterEach, describe, expect, it, vi } from 'vitest';

async function loadMarketRoutes(value?: string) {
  vi.resetModules();
  vi.unstubAllEnvs();
  if (value !== undefined) vi.stubEnv('VITE_ENABLED_MARKETS', value);
  return (await import('../config/navigation')).MARKET_ROUTES;
}

describe('MARKET_ROUTES', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it('按 VITE_ENABLED_MARKETS 过滤并规范化市场', async () => {
    const routes = await loadMarketRoutes(' CN, us ,CRYPTO ');
    expect(routes).toEqual(['cn', 'us', 'crypto']);
  });

  it('未配置 VITE_ENABLED_MARKETS 时默认启用四市场', async () => {
    const routes = await loadMarketRoutes();
    expect(routes).toEqual(['cn', 'us', 'hk', 'crypto']);
  });
});
