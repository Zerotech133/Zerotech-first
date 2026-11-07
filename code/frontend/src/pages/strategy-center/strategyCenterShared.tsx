import { Button, Empty } from 'antd';
import type { ReactNode } from 'react';
import {
  fmtDate,
  gradeClass,
  strategyStatus,
  type StrategyCenterMeta,
  type StrategyCenterStrategy,
} from './strategyCenterData';

export function PreviewBanner({ meta }: { meta: StrategyCenterMeta }) {
  return (
    <div className="sc-banner">
      <span className="sc-banner-dot" />
      <div>
        <b>只读</b>
        <span> · qengine_sync API，as-of {fmtDate(meta.asof)} · 不是生产页面，不触发选股/建仓/批准</span>
        <span className="sc-banner-meta">
          {meta.database} / {meta.schema} · qengine {meta.source_commit || '—'} · {meta.sync_id || '—'}
        </span>
      </div>
    </div>
  );
}

export function PreviewUnavailable({
  message = '策略中台只读 API 暂不可用。',
  onRetry,
}: {
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="sc-page">
      <div className="sc-panel sc-empty-panel">
        <Empty description={message}>
          {onRetry ? <Button size="small" onClick={onRetry}>重试</Button> : null}
        </Empty>
      </div>
    </div>
  );
}

export function GradeBadge({ grade }: { grade: string | null | undefined }) {
  const g = grade || '未评级';
  return <span className={`sc-grade ${gradeClass(g)}`}>{g}</span>;
}

export function StrategyStatusBadge({ strategy }: { strategy: StrategyCenterStrategy }) {
  const s = strategyStatus(strategy);
  return <span className={`sc-status sc-status-${s.tone}`}>{s.label}</span>;
}

export function Sparkline({
  values,
  ariaLabel = '净值走势',
}: {
  values: number[];
  ariaLabel?: string;
}) {
  if (!values.length) return <div className="sc-spark sc-spark-empty" />;
  const w = 240;
  const h = 48;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = values
    .map((v, i) => {
      const x = (i / Math.max(1, values.length - 1)) * (w - 4) + 2;
      const y = h - 4 - ((v - min) / span) * (h - 8);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  const last = values[values.length - 1];
  const lastX = w - 2;
  const lastY = h - 4 - ((last - min) / span) * (h - 8);
  return (
    <svg className="sc-spark" viewBox={`0 0 ${w} ${h}`} role="img" aria-label={ariaLabel}>
      <polyline points={points} fill="none" stroke="var(--gold)" strokeWidth="1.6" />
      <circle cx={lastX} cy={lastY} r="2.6" fill="var(--gold)" />
    </svg>
  );
}

export function Metric({
  label,
  value,
  suffix,
  note,
  tone,
}: {
  label: string;
  value: ReactNode;
  suffix?: string;
  note?: ReactNode;
  tone?: 'up' | 'down';
}) {
  return (
    <div className="sc-metric">
      <div className="sc-metric-label">{label}</div>
      <div className={`sc-metric-value ${tone || ''}`}>
        {value}
        {suffix ? <span className="sc-metric-suffix">{suffix}</span> : null}
      </div>
      {note ? <div className="sc-metric-note">{note}</div> : null}
    </div>
  );
}

export function SectionHead({ title, extra }: { title: string; extra?: ReactNode }) {
  return (
    <div className="sc-section-head">
      <h3>{title}</h3>
      {extra ? <span className="sc-section-extra">{extra}</span> : null}
    </div>
  );
}
