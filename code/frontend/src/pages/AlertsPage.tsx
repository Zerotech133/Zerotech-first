import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '../services/api';
import { useRole } from '../hooks/useRole';

interface Alert {
  alert_id: string;
  level: string;
  title: string;
  message: string;
  status: string;
}

export default function AlertsPage() {
  const { t } = useTranslation('pages');
  const { canWrite } = useRole();
  const LEVEL_LABEL: Record<string, string> = {
    INFO: t('alerts.level.INFO'),
    WARNING: t('alerts.level.WARNING'),
    ERROR: t('alerts.level.ERROR'),
    CRITICAL: t('alerts.level.CRITICAL'),
  };
  const STATUS_LABEL: Record<string, string> = {
    OPEN: t('alerts.status.OPEN'),
    ACKED: t('alerts.status.ACKED'),
  };
  const [alerts, setAlerts] = useState<Alert[]>([]);

  useEffect(() => {
    api<Alert[]>('/alerts').then(setAlerts).catch(console.error);
  }, []);

  const ack = async (id: string) => {
    await api(`/alerts/${id}/ack`, { method: 'POST' });
    setAlerts((prev) => prev.map((a) => (a.alert_id === id ? { ...a, status: 'ACKED' } : a)));
  };

  return (
    <div>
      <h1>{t('alerts.title')}</h1>
      <table className="table">
        <thead>
          <tr><th>{t('alerts.colLevel')}</th><th>{t('alerts.colTitle')}</th><th>{t('alerts.colMessage')}</th><th>{t('alerts.colStatus')}</th><th>{t('alerts.colAction')}</th></tr>
        </thead>
        <tbody>
          {alerts.map((a) => (
            <tr key={a.alert_id}>
              <td>{LEVEL_LABEL[a.level] ?? a.level}</td>
              <td>{a.title}</td>
              <td>{a.message}</td>
              <td>{STATUS_LABEL[a.status] ?? a.status}</td>
              <td>{a.status === 'OPEN' && canWrite && <button onClick={() => ack(a.alert_id)}>{t('common:action.confirm')}</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
