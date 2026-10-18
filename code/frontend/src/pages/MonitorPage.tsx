import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '../services/api';

interface MonitorSummary {
  portfolio: { position_count: number; total_replay_pnl_cny: number };
  decay: unknown[];
  alerts: unknown[];
}

function Pill({ label, count, color }: { label: string; count: number; color: string }) {
  return (
    <div style={{ background: '#fff', border: '1px solid #E8EAF0', borderRadius: 8, padding: '12px 20px', display: 'flex', alignItems: 'center', gap: 12 }}>
      <div style={{ width: 8, height: 8, borderRadius: '50%', background: color }} />
      <div>
        <div style={{ fontSize: 12, color: '#8B93A7' }}>{label}</div>
        <div style={{ fontSize: 20, fontWeight: 700 }}>{count}</div>
      </div>
    </div>
  );
}

export default function MonitorPage() {
  const { t } = useTranslation('pages');
  const [summary, setSummary] = useState<MonitorSummary | null>(null);

  useEffect(() => {
    api<MonitorSummary>('/monitor/summary').then(setSummary).catch(console.error);
    const t = setInterval(() => {
      api<MonitorSummary>('/monitor/summary').then(setSummary).catch(() => {});
    }, 30000);
    return () => clearInterval(t);
  }, []);

  if (!summary) return <div style={{ color: '#8B93A7', padding: 20 }}>{t('monitor.loading')}</div>;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>{t('monitor.title')}</h1>
        <span style={{ fontSize: 11, color: '#8B93A7' }}>{t('monitor.autoRefresh')}</span>
      </div>

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 24 }}>
        <Pill label={t('monitor.pillPositions')} count={summary.portfolio.position_count} color="#5B6CF0" />
        <Pill label={t('monitor.pillDecay')} count={summary.decay.length} color={summary.decay.length > 0 ? '#F6A609' : '#12A150'} />
        <Pill label={t('monitor.pillAlerts')} count={summary.alerts.length} color={summary.alerts.length > 0 ? '#F0524D' : '#12A150'} />
      </div>

      {summary.decay.length === 0 && summary.alerts.length === 0 && (
        <div style={{ color: '#12A150', fontSize: 13, background: '#F0FFF4', borderRadius: 8, padding: '14px 20px' }}>
          {t('monitor.healthy')}
        </div>
      )}
    </div>
  );
}
