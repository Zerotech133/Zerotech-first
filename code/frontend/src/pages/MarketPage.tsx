import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { api } from '../services/api';
import SymbolLink from '../components/SymbolLink';
import { useMarketQuoteSocket } from '../hooks/useMarketQuoteSocket';

interface Quote {
  ts_code: string;
  name?: string;
  price: number | null;
  pre_close: number | null;
  pct_change: number | null;
  volume?: number;
  turnover?: number;
}

const MARKETS = ['CN', 'US', 'HK', 'CRYPTO'] as const;
type Market = typeof MARKETS[number];

export default function MarketPage() {
  const { t } = useTranslation('pages');
  const [market, setMarket] = useState<Market>('CN');
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    setLoading(true);
    api<Quote[]>(`/market/quotes?market=${market}`)
      .then(setQuotes)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [market]);

  // WebSocket 只对 CN 订阅实时推送（token 为空不连，REST 上面的 effect 已兜底）
  useMarketQuoteSocket(() => {
    if (market === 'CN') {
      api<Quote[]>(`/market/quotes?market=CN`).then(setQuotes).catch(() => {});
    }
  }, [market]);

  const filtered = quotes.filter(q =>
    !search ||
    q.ts_code.toLowerCase().includes(search.toLowerCase()) ||
    (q.name || '').includes(search)
  );

  const tabStyle = (active: boolean): React.CSSProperties => ({
    padding: '8px 20px',
    borderRadius: '6px 6px 0 0',
    border: 'none',
    cursor: 'pointer',
    fontSize: 13,
    fontWeight: active ? 600 : 400,
    background: active ? '#5B6CF0' : 'transparent',
    color: active ? '#fff' : '#8B93A7',
    transition: 'all 0.15s',
  });

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>{t('market.title')}</h1>
        {market === 'CN' && (
          <span style={{ fontSize: 11, color: '#12A150', background: '#F0FFF4', padding: '2px 8px', borderRadius: 10, border: '1px solid #12A150' }}>
            {t('market.wsRealtime')}
          </span>
        )}
      </div>

      {/* 市场切换 Tab */}
      <div style={{ display: 'flex', gap: 2, borderBottom: '2px solid #E8EAF0', marginBottom: 16 }}>
        {MARKETS.map(m => (
          <button key={m} onClick={() => { setMarket(m); setSearch(''); }} style={tabStyle(market === m)}>
            {t(`common:market.${m}`)}（{m}）
          </button>
        ))}
      </div>

      {/* 搜索框 */}
      <div style={{ marginBottom: 12 }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder={t('market.searchPlaceholder', { market })}
          style={{
            padding: '6px 12px', borderRadius: 6, border: '1px solid #E8EAF0',
            fontSize: 13, width: 240, outline: 'none',
          }}
        />
        {search && (
          <span style={{ marginLeft: 8, fontSize: 12, color: '#8B93A7' }}>
            {filtered.length} / {quotes.length} {t('market.itemsUnit')}
          </span>
        )}
      </div>

      {/* 行情表格 */}
      {loading ? (
        <div style={{ color: '#8B93A7', padding: '32px 0', textAlign: 'center' }}>{t('market.loading')}</div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #E8EAF0' }}>
              {[t('market.colCode'), t('market.colName'), t('market.colPrice'), t('market.colChangePct'), t('market.colPreClose')].map(h => (
                <th key={h} style={{ textAlign: 'left', padding: '8px 10px', color: '#8B93A7', fontWeight: 500 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={5} style={{ padding: '32px 0', textAlign: 'center', color: '#8B93A7' }}>
                  {quotes.length === 0 ? t('market.emptyQuotes', { market }) : t('market.noMatch')}
                </td>
              </tr>
            ) : (
              filtered.map(q => {
                const pct = q.pct_change;
                const color = pct == null ? '#1E2330' : pct > 0 ? '#F0524D' : pct < 0 ? '#12A150' : '#1E2330';
                return (
                  <tr
                    key={q.ts_code}
                    style={{ borderBottom: '1px solid #F2F3F7', cursor: 'pointer' }}
                    onClick={() => navigate(`/kline?symbol=${encodeURIComponent(q.ts_code)}`)}
                    onMouseEnter={e => (e.currentTarget.style.background = '#F8F9FC')}
                    onMouseLeave={e => (e.currentTarget.style.background = '')}
                  >
                    <td style={{ padding: '10px 10px' }}><SymbolLink code={q.ts_code} name={q.name} /></td>
                    <td style={{ padding: '10px 10px', color: '#4A5268' }}>{q.name || '—'}</td>
                    <td style={{ padding: '10px 10px', fontWeight: 600, color }}>{q.price ?? '—'}</td>
                    <td style={{ padding: '10px 10px', color, fontWeight: 600 }}>
                      {pct != null ? `${pct > 0 ? '+' : ''}${pct}%` : '—'}
                    </td>
                    <td style={{ padding: '10px 10px', color: '#8B93A7' }}>{q.pre_close ?? '—'}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
