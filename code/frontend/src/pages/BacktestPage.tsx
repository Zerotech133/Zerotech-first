import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../services/api';

interface EquityPoint { date: string; value: number; }
interface Trade { date: string; action: string; price: number; pnl?: number; }

interface BacktestResult {
  ts_code: string;
  start: string;
  end: string;
  total_return?: number;
  annual_return?: number;
  sharpe?: number;
  max_drawdown?: number;
  win_rate?: number;
  trade_count?: number;
  equity_curve?: EquityPoint[];
  trades?: Trade[];
  error?: string;
}

function KpiCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{
      background: '#fff', border: '1px solid #E8EAF0', borderRadius: 8,
      padding: '14px 20px', minWidth: 130, textAlign: 'center',
    }}>
      <div style={{ fontSize: 11, color: '#8B93A7', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color: color || '#1E2330' }}>{value}</div>
    </div>
  );
}

function EquityChart({ points }: { points: EquityPoint[] }) {
  if (!points || points.length < 2) return null;
  const W = 700, H = 200, PAD = { top: 16, left: 64, right: 16, bottom: 24 };
  const values = points.map(p => p.value);
  const minV = Math.min(...values), maxV = Math.max(...values), spanV = maxV - minV || 1;
  const x = (i: number) => PAD.left + (i / (points.length - 1)) * (W - PAD.left - PAD.right);
  const y = (v: number) => PAD.top + (1 - (v - minV) / spanV) * (H - PAD.top - PAD.bottom);
  const initial = points[0].value;
  const isProfit = values[values.length - 1] >= initial;
  const color = isProfit ? '#F0524D' : '#12A150';
  let path = '';
  points.forEach((p, i) => { path += `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`; });
  const labelStep = Math.ceil(points.length / 6);
  const yLabels = [0, 0.25, 0.5, 0.75, 1].map(t => ({ v: minV + t * spanV, py: y(minV + t * spanV) }));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', background: '#F8F9FC', borderRadius: 8, marginBottom: 20 }}>
      {yLabels.map(({ v, py }) => (
        <g key={v}>
          <line x1={PAD.left} y1={py} x2={W - PAD.right} y2={py} stroke="#E8EAF0" />
          <text x={PAD.left - 4} y={py + 4} textAnchor="end" fontSize={9} fill="#8B93A7">{(v / 1000).toFixed(0)}k</text>
        </g>
      ))}
      <path d={path} fill="none" stroke={color} strokeWidth={2} />
      {points.map((p, i) =>
        i % labelStep === 0 ? <text key={i} x={x(i)} y={H - 4} textAnchor="middle" fontSize={9} fill="#8B93A7">{p.date.slice(5)}</text> : null
      )}
    </svg>
  );
}

export default function BacktestPage() {
  const { t } = useTranslation('pages');
  const [tsCode, setTsCode] = useState('600519.SH');
  const [start, setStart] = useState('2025-01-01');
  const [end, setEnd] = useState('2026-08-14');
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(false);

  const run = async () => {
    setLoading(true);
    try {
      const data = await api<BacktestResult>('/backtest/run', {
        method: 'POST',
        body: JSON.stringify({
          ts_code: tsCode, start, end,
          spec: {
            spec_version: 1, market: 'CN',
            universe: { codes: [tsCode] },
            factors: [{ name: 'rsi', params: { window: 14 } }],
            rules: [
              { feature: 'rsi', op: '<', value: 30, action: 'enter' },
              { feature: 'rsi', op: '>', value: 70, action: 'exit' },
            ],
          },
        }),
      });
      setResult(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const fmt = (v?: number, suffix = '%', digits = 2) =>
    v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)}${suffix}`;
  const pctColor = (v?: number) => v == null ? '#1E2330' : v >= 0 ? '#F0524D' : '#12A150';

  const inp: React.CSSProperties = { padding: '6px 10px', borderRadius: 4, border: '1px solid #E8EAF0', fontSize: 13, width: 130 };

  return (
    <div>
      <h1 style={{ fontSize: 20, marginBottom: 16 }}>{t('backtest.title')}</h1>
      <div style={{ display: 'flex', gap: 10, marginBottom: 20, flexWrap: 'wrap', alignItems: 'center' }}>
        <label style={{ fontSize: 12, color: '#8B93A7' }}>{t('backtest.symbol')}</label>
        <input value={tsCode} onChange={e => setTsCode(e.target.value)} style={inp} />
        <label style={{ fontSize: 12, color: '#8B93A7' }}>{t('backtest.start')}</label>
        <input value={start} onChange={e => setStart(e.target.value)} style={inp} />
        <label style={{ fontSize: 12, color: '#8B93A7' }}>{t('backtest.end')}</label>
        <input value={end} onChange={e => setEnd(e.target.value)} style={inp} />
        <button onClick={run} disabled={loading} style={{
          padding: '6px 20px', borderRadius: 4, background: '#5B6CF0',
          color: '#fff', border: 'none', cursor: 'pointer', fontSize: 13,
        }}>{loading ? t('backtest.running') : t('backtest.run')}</button>
      </div>

      {result && (
        result.error ? (
          <div style={{ color: '#F0524D', fontSize: 13 }}>{t('backtest.errorPrefix')}{result.error}</div>
        ) : (
          <>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 20 }}>
              <KpiCard label={t('backtest.kpiTotalReturn')} value={fmt(result.total_return)} color={pctColor(result.total_return)} />
              <KpiCard label={t('backtest.kpiAnnualReturn')} value={fmt(result.annual_return)} color={pctColor(result.annual_return)} />
              <KpiCard label={t('backtest.kpiSharpe')} value={result.sharpe?.toFixed(2) ?? '—'} color={result.sharpe != null && result.sharpe > 1 ? '#12A150' : '#F0524D'} />
              <KpiCard label={t('backtest.kpiMaxDrawdown')} value={fmt(result.max_drawdown)} color="#F0524D" />
              <KpiCard label={t('backtest.kpiTradeWin')} value={`${result.trade_count ?? '—'} / ${result.win_rate != null ? (result.win_rate * 100).toFixed(0) + '%' : '—'}`} />
            </div>

            {result.equity_curve && result.equity_curve.length > 0 && (
              <>
                <h3 style={{ fontSize: 14, color: '#4A5268', marginBottom: 8 }}>{t('backtest.equityTitle')}</h3>
                <EquityChart points={result.equity_curve} />
              </>
            )}

            {result.trades && result.trades.length > 0 && (
              <>
                <h3 style={{ fontSize: 14, color: '#4A5268', marginBottom: 8 }}>{t('backtest.tradesTitle')}</h3>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ borderBottom: '2px solid #E8EAF0' }}>
                      {[t('backtest.colDate'), t('backtest.colAction'), t('backtest.colPrice'), t('backtest.colPnl')].map(h => (
                        <th key={h} style={{ textAlign: 'left', padding: '6px 10px', color: '#8B93A7', fontWeight: 500 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((t2, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid #F2F3F7' }}>
                        <td style={{ padding: '8px 10px' }}>{t2.date}</td>
                        <td style={{ padding: '8px 10px' }}>
                          <span style={{ color: t2.action === 'BUY' ? '#12A150' : '#F0524D', fontWeight: 600 }}>{t2.action === 'BUY' ? t('backtest.buy') : t('backtest.sell')}</span>
                        </td>
                        <td style={{ padding: '8px 10px' }}>{t2.price?.toFixed(2)}</td>
                        <td style={{ padding: '8px 10px', color: (t2.pnl ?? 0) >= 0 ? '#F0524D' : '#12A150', fontWeight: 600 }}>
                          {t2.pnl != null ? `${t2.pnl >= 0 ? '+' : ''}${t2.pnl.toFixed(2)}` : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </>
        )
      )}
    </div>
  );
}
