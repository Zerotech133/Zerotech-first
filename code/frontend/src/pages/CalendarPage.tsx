import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '../services/api';
import { useMarket } from '../contexts/MarketContext';

interface CalendarItem {
  trade_date: string;
  is_open: boolean;
}

export default function CalendarPage() {
  const { t } = useTranslation('pages');
  const { market } = useMarket();
  const [items, setItems] = useState<CalendarItem[]>([]);

  useEffect(() => {
    // 跟随当前市场（此前写死 market=CN）
    api<CalendarItem[]>(`/market/calendar?market=${market}&limit=100`).then(setItems).catch(console.error);
  }, [market]);

  return (
    <div>
      <h1>{t('calendar.title')}</h1>
      <table className="table">
        <thead><tr><th>{t('calendar.colDate')}</th><th>{t('calendar.colIsOpen')}</th></tr></thead>
        <tbody>
          {items.map((d) => (
            <tr key={d.trade_date}><td>{d.trade_date}</td><td>{d.is_open ? t('calendar.open') : t('calendar.closed')}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
