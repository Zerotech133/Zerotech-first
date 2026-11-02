import { useTranslation } from 'react-i18next';
import { NavLink } from 'react-router-dom';
import { NAV_GROUPS } from '../config/navigation';
import type { Market } from '../contexts/MarketContext';
import { useRole } from '../hooks/useRole';

interface NavSideProps {
  market: Market;
  /** 候选池徽标数（/overview brief.candidates，WorkspaceLayout 拉取） */
  candidateCount: number;
  /** 计划与信号徽标数（/overview brief.approvals_pending） */
  approvalCount: number;
  /** 实时状态块：市场阶段中文（如 盘中） */
  phaseLabel: string | null;
  /** 实时状态块：调度器文案（默认 运行中） */
  schedulerLabel: string;
}

/** 徽标只挂这两个组（spec §5：候选数/待授权数金色） */
const BADGE_KEY: Record<string, 'candidateCount' | 'approvalCount'> = {
  candidates: 'candidateCount',
  signals: 'approvalCount',
};

/**
 * 工作区左侧栏（原型 .ws-side）：8 项菜单 + 金色徽标 + 底部实时状态块。
 * 激活态 .nav-item.on（渐变左边条 + 边框）由 tokens.css 提供，NavLink className 切换。
 */
export default function NavSide({ market, candidateCount, approvalCount, phaseLabel, schedulerLabel }: NavSideProps) {
  const { t } = useTranslation('pages');
  const { isViewer } = useRole();
  const badges: Record<string, number> = { candidateCount, approvalCount };
  // VIEWER 只读：只保留 总览/策略/交易/复盘 四组，隐藏候选/信号/风控/系统。
  const groups = isViewer
    ? NAV_GROUPS.filter(g => ['overview', 'strategies', 'trading', 'replay'].includes(g.key))
    : NAV_GROUPS;

  return (
    <aside className="ws-side">
      <NavLink
        to={`/${market.toLowerCase()}/agent`}
        className={({ isActive }) => `nav-item${isActive ? ' on' : ''}`}
        title="司马徽智能体工作台"
        style={{ marginBottom: 6 }}
      >
        <span className="nav-ico">✦</span>
        司马徽
      </NavLink>
      <div className="nav-sep" />

      {groups.map(g => {
        const badgeKey = BADGE_KEY[g.key];
        const badge = badgeKey != null ? badges[badgeKey] : null;
        return (
          <NavLink
            key={g.key}
            to={`/${market.toLowerCase()}/${g.key}`}
            className={({ isActive }) => `nav-item${isActive ? ' on' : ''}`}
            /* 徽标口径：候选=最新池日候选标的数；待办=当前市场 PENDING 授权数
               （与闭环流水线漏斗的「历史累计」口径不同，deep-test C8） */
            title={badgeKey === 'candidateCount' ? t('nav.candidateBadgeNote') : badgeKey === 'approvalCount' ? t('nav.approvalBadgeNote') : undefined}
          >
            <span className="nav-ico">{g.icon}</span>
            {t(g.labelKey)}
            {badge != null && badge > 0 && <span className="nav-badge gold">{badge}</span>}
          </NavLink>
        );
      })}

      <div className="nav-sep" />

      <div className="nav-stat">
        <div className="hd">{t('nav.status.title')} <i>● {t('nav.status.online')}</i></div>
        <div className="st-row">
          <span>{t('nav.status.phase')}</span>
          <b style={{ color: 'var(--green)' }}>{phaseLabel ?? '—'}</b>
        </div>
        <div className="st-row">
          <span>{t('nav.status.todos')}</span>
          <b style={approvalCount > 0 ? { color: 'var(--gold)' } : undefined}>{t('nav.status.items', { count: approvalCount })}</b>
        </div>
        <div className="st-row">
          <span>{t('nav.status.scheduler')}</span>
          <b>{schedulerLabel}</b>
        </div>
        <div className="st-row">
          <span>{t('nav.status.refresh')}</span>
          <b>30s</b>
        </div>
      </div>
    </aside>
  );
}
