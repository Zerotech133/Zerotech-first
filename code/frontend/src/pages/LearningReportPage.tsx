import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '../services/api';

interface ReplayReport {
  total: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: string;
}

interface DecayItem {
  strategy_version_id: string;
  older_win_rate: number;
  recent_win_rate: number;
  decay: number;
  status: string;
}

interface Governance {
  checked: number;
  suggestions: { strategy_version_id: string; action: string; reason: string }[];
}

interface LearningReport {
  replay: ReplayReport;
  decay: DecayItem[];
  governance: Governance;
}

export default function LearningReportPage() {
  const { t } = useTranslation('pages');
  const DECAY_STATUS_LABELS: Record<string, string> = { DECAY: t('learningReport.decayStatus.DECAY'), NORMAL: t('learningReport.decayStatus.NORMAL') };
  const ACTION_LABELS: Record<string, string> = { DEMOTE: t('learningReport.action.DEMOTE'), PROMOTE: t('learningReport.action.PROMOTE'), DISABLE: t('learningReport.action.DISABLE') };
  const [report, setReport] = useState<LearningReport | null>(null);

  useEffect(() => {
    api<LearningReport>('/reports/learning').then(setReport).catch(console.error);
  }, []);

  if (!report) return <div>{t('learningReport.loading')}</div>;

  return (
    <div>
      <h1>{t('learningReport.title')}</h1>
      <h2>{t('learningReport.replayStats')}</h2>
      <p>{t('learningReport.totalTrades', { count: report.replay.total })}</p>
      <p>{t('learningReport.winLoss', { wins: report.replay.wins, losses: report.replay.losses })}</p>
      <p>{t('learningReport.winRate', { pct: (report.replay.win_rate * 100).toFixed(2) })}</p>
      <p>{t('learningReport.totalPnl', { pnl: report.replay.total_pnl })}</p>

      <h2>{t('learningReport.decayTitle')}</h2>
      <table className="table">
        <thead>
          <tr><th>{t('learningReport.colStrategy')}</th><th>{t('learningReport.colOlderWin')}</th><th>{t('learningReport.colRecentWin')}</th><th>{t('learningReport.colDecay')}</th><th>{t('learningReport.colStatus')}</th></tr>
        </thead>
        <tbody>
          {report.decay.map((d) => (
            <tr key={d.strategy_version_id}>
              <td>{d.strategy_version_id}</td>
              <td>{d.older_win_rate}</td>
              <td>{d.recent_win_rate}</td>
              <td>{d.decay}</td>
              <td>{DECAY_STATUS_LABELS[d.status] ?? d.status}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>{t('learningReport.govTitle')}</h2>
      {report.governance.suggestions.length === 0 ? (
        <p>{t('learningReport.noSuggestions')}</p>
      ) : (
        <ul>
          {report.governance.suggestions.map((s) => (
            <li key={s.strategy_version_id}>{s.strategy_version_id} → {ACTION_LABELS[s.action] ?? s.action}：{s.reason}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
