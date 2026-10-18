import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../services/api';

interface Perf {
  strategy_version_id: string;
  trade_count: number;
  win_rate: number;
  total_pnl: number;
  avg_pnl: number;
}

const fmt = (n: number) =>
  (n ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function StrategyPerformancePage() {
  const { t } = useTranslation('pages');
  const [items, setItems] = useState<Perf[]>([]);

  useEffect(() => {
    api<Perf[]>('/strategies/performance').then(setItems).catch(console.error);
  }, []);

  return (
    <div>
      <h1>{t('strategyPerformance.title')}</h1>
      <table className="table">
        <thead><tr><th>{t('strategyPerformance.colVersion')}</th><th>{t('strategyPerformance.colTradeCount')}</th><th>{t('strategyPerformance.colWinRate')}</th><th>{t('strategyPerformance.colTotalPnl')}</th><th>{t('strategyPerformance.colAvgPnl')}</th></tr></thead>
        <tbody>
          {items.map((p) => (
            <tr key={p.strategy_version_id}>
              <td>{p.strategy_version_id}</td>
              <td>{p.trade_count}</td>
              <td>{(p.win_rate * 100).toFixed(1)}%</td>
              <td>{fmt(p.total_pnl)}</td>
              <td>{fmt(p.avg_pnl)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
