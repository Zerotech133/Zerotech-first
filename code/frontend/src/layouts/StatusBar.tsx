import { useEffect, useState, type CSSProperties } from 'react';
import { useTranslation } from 'react-i18next';
import { fmtMoney, pnlColor, type Market } from '../contexts/MarketContext';

interface StatusBarProps {
  market: Market;
  /** 市场阶段原始值（如 TRADING），缺省显示 — */
  phase?: string | null;
  /** 阶段中文标签（如 盘中） */
  phaseLabel?: string | null;
  candidateCount: number;
  approvalCount: number;
  todayPnl: number | null;
  /** 数据质量分（0-100，仅 CN 简化口径） */
  quality: number | null;
}

/** 每秒时钟（YYYY-MM-DD HH:mm:ss） */
function useClock(): string {
  const [now, setNow] = useState(() => fmtClock(new Date()));
  useEffect(() => {
    const t = window.setInterval(() => setNow(fmtClock(new Date())), 1000);
    return () => window.clearInterval(t);
  }, []);
  return now;
}

function fmtClock(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/* 非盘中阶段的静止小圆点（呼吸灯只在盘中亮绿） */
const dotStatic: CSSProperties = {
  width: 7,
  height: 7,
  borderRadius: '50%',
  background: 'var(--txt-faint)',
  display: 'inline-block',
  flex: 'none',
};

/**
 * 工作区固定底栏（原型 .ws-foot）：市场·阶段呼吸灯 | 候选 | 待授权 | 今日盈亏 | REFRESH/QUALITY/时钟。
 * 数据全部由 WorkspaceLayout props 注入（useAutoRefresh 30s 拉取）。
 */
export default function StatusBar(props: StatusBarProps) {
  const { t } = useTranslation('pages');
  const clock = useClock();
  const { market, phase, phaseLabel, candidateCount, approvalCount, todayPnl, quality } = props;
  const isLive = phase === 'TRADING';

  return (
    <footer className="ws-foot">
      <span className="live">
        {isLive ? <i className="ok-dot" /> : <i style={dotStatic} />}
        {t('market.' + market, { ns: 'common' })} · {phaseLabel ?? '—'}
      </span>
      <span title={t('statusBar.candidateNote')}>{t('statusBar.candidates', { count: candidateCount })}</span>
      <span
        title={t('statusBar.approvalNote')}
        style={approvalCount > 0 ? { color: 'var(--gold)' } : undefined}
      >
        {t('statusBar.pendingApprovals', { count: approvalCount })}
      </span>
      <span style={todayPnl != null ? { color: pnlColor(todayPnl, market) } : undefined}>
        {t('statusBar.today')} {todayPnl == null ? '—' : fmtMoney(todayPnl, 0)}
      </span>
      <span style={{ marginLeft: 'auto' }} title={t('statusBar.qualityNote')}>
        {t('statusBar.refresh')} 30s · {t('statusBar.quality')} {quality == null ? '—' : quality.toFixed(1)} · {clock}
      </span>
    </footer>
  );
}
