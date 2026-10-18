import { useTranslation } from 'react-i18next';

export default function ExportsPage() {
  const { t } = useTranslation('pages');
  return (
    <div>
      <h1>{t('exports.title')}</h1>
      <p>
        <a href="/api/v1/exports/replays" download>{t('exports.exportReplaysCsv')}</a>
      </p>
    </div>
  );
}
