/**
 * MarketContext 单元测试
 * 分两部分：
 *  1. 纯函数（无 DOM / 无 Router 依赖）
 *  2. MarketProvider 组件（需要 MemoryRouter）
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import {
  marketFromRoute,
  pnlColor,
  fmtMoney,
  fmtPct,
  MarketProvider,
  useMarket,
} from '../contexts/MarketContext';

// ── 纯函数测试 ────────────────────────────────────────────────────────────────

describe('marketFromRoute', () => {
  it('cn → CN', () => expect(marketFromRoute('cn')).toBe('CN'));
  it('us → US', () => expect(marketFromRoute('us')).toBe('US'));
  it('hk → HK', () => expect(marketFromRoute('hk')).toBe('HK'));
  it('crypto → CRYPTO', () => expect(marketFromRoute('crypto')).toBe('CRYPTO'));
  it('大写 CN 同样合法', () => expect(marketFromRoute('CN')).toBe('CN'));
  it('非法段 → 回落到 CN', () => expect(marketFromRoute('invalid')).toBe('CN'));
  it('undefined → 回落到 CN', () => expect(marketFromRoute(undefined)).toBe('CN'));
  it('空字符串 → 回落到 CN', () => expect(marketFromRoute('')).toBe('CN'));
});

describe('pnlColor', () => {
  it('A股盈利 → 红色', () => expect(pnlColor(100, 'CN')).toBe('#F0524D'));
  it('A股亏损 → 绿色', () => expect(pnlColor(-100, 'CN')).toBe('#12A150'));
  it('美股盈利 → 红色（统一红涨绿跌）', () => expect(pnlColor(100, 'US')).toBe('#F0524D'));
  it('美股亏损 → 绿色（统一红涨绿跌）', () => expect(pnlColor(-100, 'US')).toBe('#12A150'));
  it('港股盈利 → 红色（统一红涨绿跌）', () => expect(pnlColor(50, 'HK')).toBe('#F0524D'));
  it('加密盈利 → 红色（统一红涨绿跌）', () => expect(pnlColor(1, 'CRYPTO')).toBe('#F0524D'));
  it('零值 → 灰色', () => expect(pnlColor(0, 'CN')).toBe('#8B93A7'));
  it('零值（US）→ 灰色', () => expect(pnlColor(0, 'US')).toBe('#8B93A7'));
});

describe('fmtMoney', () => {
  it('正数带加号，千位分隔', () => expect(fmtMoney(1234.56)).toBe('+1,234.56'));
  it('负数不带加号', () => expect(fmtMoney(-1234.56)).toBe('-1,234.56'));
  it('零', () => expect(fmtMoney(0)).toBe('0.00'));
  it('自定义小数位', () => expect(fmtMoney(1.5, 0)).toBe('+2'));
});

describe('fmtPct', () => {
  it('正数带加号和百分号', () => expect(fmtPct(3.14)).toBe('+3.14%'));
  it('负数不带加号', () => expect(fmtPct(-2.5)).toBe('-2.50%'));
  it('零', () => expect(fmtPct(0)).toBe('0.00%'));
  it('自定义精度', () => expect(fmtPct(1.2345, 1)).toBe('+1.2%'));
});

// ── MarketProvider 组件测试 ───────────────────────────────────────────────────

function MarketDisplay() {
  const { market, currency, currencySymbol, setMarket } = useMarket();
  return (
    <div>
      <span data-testid="market">{market}</span>
      <span data-testid="currency">{currency}</span>
      <span data-testid="symbol">{currencySymbol}</span>
      <button onClick={() => setMarket('US')}>切换美股</button>
    </div>
  );
}

function renderProvider(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <MarketProvider>
        <MarketDisplay />
      </MarketProvider>
    </MemoryRouter>,
  );
}

describe('MarketProvider', () => {
  it('/us/overview → market=US, currency=USD', () => {
    renderProvider('/us/overview');
    expect(screen.getByTestId('market')).toHaveTextContent('US');
    expect(screen.getByTestId('currency')).toHaveTextContent('USD');
    expect(screen.getByTestId('symbol')).toHaveTextContent('$');
  });

  it('/cn/overview → market=CN（A股）', () => {
    renderProvider('/cn/overview');
    expect(screen.getByTestId('market')).toHaveTextContent('CN');
    expect(screen.getByTestId('currency')).toHaveTextContent('CNY');
  });

  it('/hk/trading → market=HK', () => {
    renderProvider('/hk/trading');
    expect(screen.getByTestId('market')).toHaveTextContent('HK');
  });

  it('/crypto/risk → market=CRYPTO', () => {
    renderProvider('/crypto/risk');
    expect(screen.getByTestId('market')).toHaveTextContent('CRYPTO');
  });

  it('非法市场段 / → 回落 CN', () => {
    renderProvider('/');
    expect(screen.getByTestId('market')).toHaveTextContent('CN');
  });

  it('setMarket 点击后重新渲染（navigate 生效）', () => {
    renderProvider('/cn/overview');
    expect(screen.getByTestId('market')).toHaveTextContent('CN');
    // 点击后路由变为 /us/overview，Provider 重新读取 URL
    fireEvent.click(screen.getByRole('button', { name: '切换美股' }));
    expect(screen.getByTestId('market')).toHaveTextContent('US');
  });

  it('useMarket 在 Provider 外抛出错误', () => {
    // 捕获 console.error 避免噪音
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() =>
      render(
        <MemoryRouter>
          <MarketDisplay />
        </MemoryRouter>,
      ),
    ).toThrow('useMarket must be used within MarketProvider');
    spy.mockRestore();
  });
});
