import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '../services/api';

interface Fund {
  end_date: string;
  ann_date: string | null;
  roe: string | null;
  debt_to_assets: string | null;
  netprofit_yoy: string | null;
  profit_dedt_cny: string | null;
}

export default function FundamentalsPage() {
  const { t } = useTranslation('pages');
  const [tsCode, setTsCode] = useState('600519.SH');
  const [items, setItems] = useState<Fund[]>([]);

  const load = async () => {
    const data = await api<Fund[]>(`/market/fundamentals/${encodeURIComponent(tsCode)}`);
    setItems(data);
  };

  return (
    <div>
      <h1>{t('fundamentals.title')}</h1>
      <input value={tsCode} onChange={(e) => setTsCode(e.target.value)} />
      <button onClick={load}>{t('fundamentals.query')}</button>
      <table className="table">
        <thead><tr><th>{t('fundamentals.colEndDate')}</th><th>{t('fundamentals.colAnnDate')}</th><th>ROE</th><th>{t('fundamentals.colDebtRatio')}</th><th>{t('fundamentals.colNetProfitYoy')}</th><th>{t('fundamentals.colProfitDedt')}</th></tr></thead>
        <tbody>
          {items.map((f) => (
            <tr key={f.end_date}>
              <td>{f.end_date}</td>
              <td>{f.ann_date}</td>
              <td>{f.roe}</td>
              <td>{f.debt_to_assets}</td>
              <td>{f.netprofit_yoy}</td>
              <td>{f.profit_dedt_cny}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
