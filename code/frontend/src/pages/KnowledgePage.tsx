import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '../services/api';
import SymbolLink from '../components/SymbolLink';

interface ReplayItem {
  replay_id: string;
  ts_code: string;
  pnl: string;
  exit_reason: string;
}

export default function KnowledgePage() {
  const { t } = useTranslation('pages');
  const [q, setQ] = useState('');
  const [items, setItems] = useState<ReplayItem[]>([]);

  const search = async () => {
    const data = await api<ReplayItem[]>(`/knowledge/replay/search?q=${encodeURIComponent(q)}`);
    setItems(data);
  };

  return (
    <div>
      <h1>{t('knowledge.title')}</h1>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('knowledge.searchPlaceholder')} />
      <button onClick={search}>{t('knowledge.search')}</button>
      <table className="table">
        <thead>
          <tr><th>{t('knowledge.colId')}</th><th>{t('knowledge.colSymbol')}</th><th>{t('knowledge.colPnl')}</th><th>{t('knowledge.colExit')}</th></tr>
        </thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.replay_id}>
              <td>{r.replay_id}</td>
              <td><SymbolLink code={r.ts_code} /></td>
              <td>{r.pnl}</td>
              <td>{r.exit_reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
