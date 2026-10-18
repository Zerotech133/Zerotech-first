import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { NAV_GROUPS } from '../config/navigation';
import { useMarket } from '../contexts/MarketContext';

interface PageHeaderProps {
  /** 组 key（NAV_GROUPS 之一），面包屑第三段取其中文 label */
  groupKey: string;
  /** h1 标题 */
  title: string;
  /** 标题右侧等宽小字（如 tab key 枚举） */
  meta?: ReactNode;
  /** 右侧操作 slot */
  extra?: ReactNode;
}

/**
 * 组容器页头（mockup .crumb + .pg-head）：
 * COMMAND / {市场中文} / {组中文}（市场 cyan 高亮）+ h1 标题 + mono meta + 右侧操作。
 * 市场由 /:market URL 段派生（useMarket）；样式内联消费 tokens.css 变量，随 5 套主题联动。
 */
export default function PageHeader({ groupKey, title, meta, extra }: PageHeaderProps) {
  const { t } = useTranslation('pages');
  const { market } = useMarket();
  const group = NAV_GROUPS.find(g => g.key === groupKey);
  return (
    <>
      <div
        className="mono"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          marginBottom: 4,
          fontSize: 11,
          color: 'var(--txt-faint)',
          letterSpacing: 1,
        }}
      >
        {t('pageHeader.command')} / <b style={{ color: 'var(--cyan)', fontWeight: 600 }}>{t('market.' + market, { ns: 'common' })}</b> / {group ? t(group.labelKey) : groupKey}
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 26 }}>
        <h1 style={{ fontSize: 21, fontWeight: 700, margin: 0 }}>{title}</h1>
        {meta != null && <span className="mono" style={{ fontSize: 12, color: 'var(--txt-dim)' }}>{meta}</span>}
        {extra != null && (
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 10, alignItems: 'center' }}>{extra}</div>
        )}
      </div>
    </>
  );
}
