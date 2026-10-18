import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '../services/api';
import SymbolLink from '../components/SymbolLink';
import { useMarket } from '../contexts/MarketContext';

interface Quote {
  ts_code: string;
  name: string;
  price: number;
  pct_change: number | null;
}

export default function MarketOverviewPage() {
  const { t } = useTranslation('pages');
  const { market } = useMarket();
  const [data, setData] = useState<{ total: number; gainers: Quote[]; losers: Quote[] } | null>(null);

  useEffect(() => {
    // 跟随当前市场（此前写死 market=CN，切美股/港股仍显示 A 股标的）
    api<{ total: number; gainers: Quote[]; losers: Quote[] }>(`/market/overview?market=${market}`).then(setData).catch(console.error);
  }, [market]);

  if (!data) return <div>{t('marketOverview.loading')}</div>;

  return (
    <div>
      <h1>{t('marketOverview.title')}</h1>
      <p>{t('marketOverview.total', { count: data.total })}</p>
      <h2>{t('marketOverview.gainers')}</h2>
      <table className="table">
        <thead><tr><th>{t('marketOverview.colCode')}</th><th>{t('marketOverview.colName')}</th><th>{t('marketOverview.colPrice')}</th><th>{t('marketOverview.colChange')}</th></tr></thead>
        <tbody>
          {data.gainers.map((q) => (
            <tr key={q.ts_code}><td><SymbolLink code={q.ts_code} name={q.name} /></td><td>{q.name}</td><td>{q.price}</td><td>{q.pct_change}</td></tr>
          ))}
        </tbody>
      </table>
      <h2>{t('marketOverview.losers')}</h2>
      <table className="table">
        <thead><tr><th>{t('marketOverview.colCode')}</th><th>{t('marketOverview.colName')}</th><th>{t('marketOverview.colPrice')}</th><th>{t('marketOverview.colChange')}</th></tr></thead>
        <tbody>
          {data.losers.map((q) => (
            <tr key={q.ts_code}><td><SymbolLink code={q.ts_code} name={q.name} /></td><td>{q.name}</td><td>{q.price}</td><td>{q.pct_change}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
