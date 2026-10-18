import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../services/api';
import SymbolLink from '../components/SymbolLink';
import { useRole } from '../hooks/useRole';

interface Plan {
  plan_id: string;
  ts_code: string;
  risk: string;
}

interface ApprovalItem {
  request_id: string;
  ts_code: string;
  direction: string;
  status: string;
}

interface WorkflowResult {
  market: string;
  trade_date: string;
  candidates: number;
  plans: Plan[];
  approvals?: ApprovalItem[];
}

const MARKETS = ['CN', 'US', 'HK', 'CRYPTO'] as const;

// 展示映射（key 为后端枚举值，值为 i18n key，不改变比较/传参语义）
const RISK_LABEL_KEYS: Record<string, string> = { PASS: 'workflow.riskPass', WARNING: 'workflow.riskWarning', REJECT: 'workflow.riskReject', BLOCK: 'workflow.riskBlock' };
const DIRECTION_LABEL_KEYS: Record<string, string> = { BUY: 'workflow.dirBuy', SELL: 'workflow.dirSell' };
const STATUS_LABEL_KEYS: Record<string, string> = { PENDING: 'workflow.statusPending', APPROVED: 'workflow.statusApproved', REJECTED: 'workflow.statusRejected' };

const tag = (color: string): React.CSSProperties => ({
  display: 'inline-block', padding: '2px 8px', borderRadius: 4,
  fontSize: 11, fontWeight: 600, background: color, color: '#fff',
});

export default function WorkflowPage() {
  const { t } = useTranslation('pages');
  const { canWrite } = useRole();  // VIEWER 只读：隐藏运行盘前/盘后
  const [market, setMarket] = useState<typeof MARKETS[number]>('CN');
  const [result, setResult] = useState<WorkflowResult | null>(null);
  const [loading, setLoading] = useState<'pre-market' | 'post-market' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async (type: 'pre-market' | 'post-market') => {
    setLoading(type);
    setError(null);
    try {
      const data = await api<WorkflowResult>(`/workflow/${type}?market=${market}`, { method: 'POST' });
      setResult(data);
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setLoading(null);
    }
  };

  const riskColor = (r: string) => r === 'PASS' ? '#12A150' : '#F0524D';

  return (
    <div>
      <h1 style={{ fontSize: 20, marginBottom: 16 }}>{t('workflow.title')}</h1>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20 }}>
        <select
          value={market}
          onChange={e => setMarket(e.target.value as typeof MARKETS[number])}
          style={{ padding: '6px 10px', borderRadius: 4, border: '1px solid #ddd', fontSize: 13 }}
        >
          {MARKETS.map(m => <option key={m} value={m}>{t(`common:market.${m}`)}</option>)}
        </select>
        {canWrite && (
          <button
            onClick={() => run('pre-market')}
            disabled={!!loading}
            style={{ padding: '6px 16px', borderRadius: 4, background: '#5B6CF0', color: '#fff', border: 'none', cursor: 'pointer', fontSize: 13 }}
          >
            {loading === 'pre-market' ? t('workflow.running') : t('workflow.runPre')}
          </button>
        )}
        {canWrite && (
          <button
            onClick={() => run('post-market')}
            disabled={!!loading}
            style={{ padding: '6px 16px', borderRadius: 4, background: '#1E2330', color: '#C5CBD8', border: '1px solid #ddd', cursor: 'pointer', fontSize: 13 }}
          >
            {loading === 'post-market' ? t('workflow.running') : t('workflow.runPost')}
          </button>
        )}
        {loading && <span style={{ color: '#8B93A7', fontSize: 13 }}>{loading === 'pre-market' ? t('workflow.runningPre') : t('workflow.runningPost')}</span>}
      </div>

      {error && <div style={{ color: '#F0524D', marginBottom: 12, fontSize: 13 }}>{error}</div>}

      {result && (
        <div>
          {/* 摘要行 */}
          <div style={{ display: 'flex', gap: 24, marginBottom: 20, padding: '12px 16px', background: '#F8F9FC', borderRadius: 8, fontSize: 14 }}>
            <span>📅 <b>{result.trade_date}</b></span>
            <span>🎯 {t('workflow.candidates')} <b>{result.candidates}</b> {t('workflow.unitStock')}</span>
            <span>📋 {t('workflow.plans')} <b>{result.plans.length}</b> {t('workflow.unitItem')}</span>
            <span>⏳ {t('workflow.pendingApproval')} <b>{result.approvals?.filter(a => a.status === 'PENDING').length ?? 0}</b> {t('workflow.unitItem')}</span>
          </div>

          {/* 计划表格 */}
          {result.plans.length > 0 && (
            <div style={{ marginBottom: 20 }}>
              <h3 style={{ fontSize: 14, color: '#4A5268', marginBottom: 8 }}>{t('workflow.planTitle')}</h3>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #E8EAF0' }}>
                    <th style={{ textAlign: 'left', padding: '6px 8px', color: '#8B93A7', fontWeight: 500 }}>{t('workflow.colSymbol')}</th>
                    <th style={{ textAlign: 'left', padding: '6px 8px', color: '#8B93A7', fontWeight: 500 }}>{t('workflow.colRisk')}</th>
                    <th style={{ textAlign: 'left', padding: '6px 8px', color: '#8B93A7', fontWeight: 500 }}>{t('workflow.colPlanId')}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.plans.map(p => (
                    <tr key={p.plan_id} style={{ borderBottom: '1px solid #F2F3F7' }}>
                      <td style={{ padding: '8px 8px' }}><SymbolLink code={p.ts_code} /></td>
                      <td style={{ padding: '8px 8px' }}>
                        <span style={tag(riskColor(p.risk))}>{RISK_LABEL_KEYS[p.risk] ? t(RISK_LABEL_KEYS[p.risk]) : p.risk}</span>
                      </td>
                      <td style={{ padding: '8px 8px', color: '#8B93A7', fontSize: 11 }}>{p.plan_id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* 授权列表 */}
          {result.approvals && result.approvals.length > 0 && (
            <div>
              <h3 style={{ fontSize: 14, color: '#4A5268', marginBottom: 8 }}>{t('workflow.approvalTitle')}</h3>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #E8EAF0' }}>
                    <th style={{ textAlign: 'left', padding: '6px 8px', color: '#8B93A7', fontWeight: 500 }}>{t('workflow.colSymbol')}</th>
                    <th style={{ textAlign: 'left', padding: '6px 8px', color: '#8B93A7', fontWeight: 500 }}>{t('workflow.colDirection')}</th>
                    <th style={{ textAlign: 'left', padding: '6px 8px', color: '#8B93A7', fontWeight: 500 }}>{t('workflow.colStatus')}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.approvals.map(a => (
                    <tr key={a.request_id} style={{ borderBottom: '1px solid #F2F3F7' }}>
                      <td style={{ padding: '8px 8px' }}><SymbolLink code={a.ts_code} /></td>
                      <td style={{ padding: '8px 8px' }}>
                        <span style={tag(a.direction === 'BUY' ? '#12A150' : '#F0524D')}>{DIRECTION_LABEL_KEYS[a.direction] ? t(DIRECTION_LABEL_KEYS[a.direction]) : a.direction}</span>
                      </td>
                      <td style={{ padding: '8px 8px' }}>
                        <span style={tag(a.status === 'PENDING' ? '#F6A609' : a.status === 'APPROVED' ? '#12A150' : '#8B93A7')}>{STATUS_LABEL_KEYS[a.status] ? t(STATUS_LABEL_KEYS[a.status]) : a.status}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {result.plans.length === 0 && (
            <div style={{ color: '#8B93A7', fontSize: 13, textAlign: 'center', padding: 24 }}>
              {t('workflow.noPlans')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
