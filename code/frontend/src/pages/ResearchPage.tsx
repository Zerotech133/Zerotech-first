import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../services/api';

interface Regime {
  market: string;
  regime: string;
  ret5?: number;
  ret20?: number;
}

interface ReplaySummary {
  total: number;
  win_rate: number;
  wins: number;
  losses: number;
  total_pnl: string;
}

interface AssistantResult {
  answer: string;
  regime?: Regime;
  replay_summary?: ReplaySummary;
}

const MARKETS = ['CN', 'US', 'HK', 'CRYPTO'] as const;

const REGIME_META: Record<string, { labelKey: string; color: string }> = {
  BULL: { labelKey: 'research.regimeBull', color: '#F0524D' },
  BEAR: { labelKey: 'research.regimeBear', color: '#12A150' },
  SIDEWAYS: { labelKey: 'research.regimeSideways', color: '#F6A609' },
  UNKNOWN: { labelKey: 'research.regimeUnknown', color: '#8B93A7' },
};

export default function ResearchPage() {
  const { t } = useTranslation('pages');
  const PRESETS = [
    t('research.preset1'),
    t('research.preset2'),
    t('research.preset3'),
  ];
  const [market, setMarket] = useState('CN');
  const [question, setQuestion] = useState(PRESETS[0]);
  const [result, setResult] = useState<AssistantResult | null>(null);
  const [loading, setLoading] = useState(false);

  const run = async () => {
    setLoading(true);
    try {
      const data = await api<AssistantResult>('/research/assistant', {
        method: 'POST',
        body: JSON.stringify({ market, question }),
      });
      setResult(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const regimeInfo = result?.regime ? REGIME_META[result.regime.regime] || REGIME_META.UNKNOWN : null;

  return (
    <div>
      <h1 style={{ fontSize: 20, marginBottom: 4 }}>{t('research.title')}</h1>
      <p style={{ fontSize: 12, color: '#8B93A7', marginBottom: 16 }}>{t('research.subtitle')}</p>

      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <select value={market} onChange={e => setMarket(e.target.value)}
          style={{ padding: '6px 10px', borderRadius: 4, border: '1px solid #E8EAF0', fontSize: 13 }}>
          {MARKETS.map(m => <option key={m} value={m}>{t(`common:market.${m}`)}</option>)}
        </select>
        <input value={question} onChange={e => setQuestion(e.target.value)}
          style={{ padding: '6px 12px', borderRadius: 4, border: '1px solid #E8EAF0', fontSize: 13, width: 320 }} />
        <button onClick={run} disabled={loading}
          style={{ padding: '6px 20px', borderRadius: 4, background: '#5B6CF0', color: '#fff', border: 'none', cursor: 'pointer', fontSize: 13 }}>
          {loading ? t('research.analyzing') : t('research.run')}
        </button>
      </div>

      <div style={{ marginBottom: 20, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {PRESETS.map(p => (
          <button key={p} onClick={() => setQuestion(p)}
            style={{ padding: '4px 12px', borderRadius: 16, border: '1px solid #E8EAF0', background: question === p ? '#EEF0FE' : '#fff', color: '#5B6CF0', cursor: 'pointer', fontSize: 12 }}>
            {p}
          </button>
        ))}
      </div>

      {result && (
        <div>
          {/* 市场状态 + 复盘摘要卡片 */}
          <div style={{ display: 'flex', gap: 16, marginBottom: 16, flexWrap: 'wrap' }}>
            {result.regime && regimeInfo && (
              <div style={{ background: '#fff', border: '1px solid #E8EAF0', borderRadius: 8, padding: '14px 20px', minWidth: 180 }}>
                <div style={{ fontSize: 12, color: '#8B93A7', marginBottom: 6 }}>{t('research.marketStatus')}</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: regimeInfo.color }}>{t(regimeInfo.labelKey)}</div>
                <div style={{ fontSize: 11, color: '#8B93A7', marginTop: 4 }}>
                  {t('research.regimeReturns', { r5: ((result.regime.ret5 ?? 0) * 100).toFixed(2), r20: ((result.regime.ret20 ?? 0) * 100).toFixed(2) })}
                </div>
              </div>
            )}
            {result.replay_summary && (
              <div style={{ background: '#fff', border: '1px solid #E8EAF0', borderRadius: 8, padding: '14px 20px', minWidth: 180 }}>
                <div style={{ fontSize: 12, color: '#8B93A7', marginBottom: 6 }}>{t('research.historyPerf')}</div>
                <div style={{ fontSize: 20, fontWeight: 700 }}>{(result.replay_summary.win_rate * 100).toFixed(1)}%</div>
                <div style={{ fontSize: 11, color: '#8B93A7', marginTop: 4 }}>
                  {t('research.replaySummary', { total: result.replay_summary.total, wins: result.replay_summary.wins, losses: result.replay_summary.losses })}
                </div>
              </div>
            )}
          </div>

          {/* 分析结论 */}
          <div style={{ background: '#F8F9FC', borderRadius: 8, padding: '16px 20px', borderLeft: '3px solid #5B6CF0' }}>
            <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.7, color: '#1E2330', margin: 0 }}>
              {result.answer}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
