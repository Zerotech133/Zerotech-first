import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../services/api';

interface Regime {
  market: string;
  benchmark: string;
  regime: string;
  ret5: number;
  ret20: number;
}

const REGIME_LABEL_KEYS: Record<string, string> = {
  BULL: 'regime.bull',
  BEAR: 'regime.bear',
  SIDEWAYS: 'regime.sideways',
  UNKNOWN: 'regime.unknown',
};

export default function RegimePage() {
  const { t } = useTranslation('pages');
  const [market, setMarket] = useState('CN');
  const [regime, setRegime] = useState<Regime | null>(null);

  useEffect(() => {
    api<Regime>(`/market/regime?market=${market}`).then(setRegime).catch(console.error);
  }, [market]);

  return (
    <div>
      <h1>{t('regime.title')}</h1>
      <select value={market} onChange={(e) => setMarket(e.target.value)}>
        <option value="CN">{t('common:market.CN')}</option><option value="US">{t('common:market.US')}</option><option value="HK">{t('common:market.HK')}</option><option value="CRYPTO">{t('common:market.CRYPTO')}</option>
      </select>
      {regime && (
        <p>{regime.market ? t(`common:market.${regime.market}`) : regime.market} / {regime.benchmark} / {REGIME_LABEL_KEYS[regime.regime] ? t(REGIME_LABEL_KEYS[regime.regime]) : regime.regime} / {t('regime.ret5')} {regime.ret5} / {t('regime.ret20')} {regime.ret20}</p>
      )}
    </div>
  );
}
