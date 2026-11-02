import { Dropdown } from 'antd';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Outlet, useNavigate, useParams } from 'react-router-dom';
import { NAV_GROUPS, MARKET_ROUTES } from '../config/navigation';
import { useMarket, type Market } from '../contexts/MarketContext';
import { openCommandPalette, MODK_HINT } from '../components/CommandPalette';
import LanguageMenu from '../components/LanguageMenu';
import ThemeMenu from '../components/ThemeMenu';
import { useAutoRefresh } from '../hooks/useAutoRefresh';
import { api } from '../services/api';
import NavSide from './NavSide';
import FloatingAgent from '../components/FloatingAgent';
import StatusBar from './StatusBar';

/** GET /overview?market=M 契约（同 DashboardPage，取 brief 所需字段子集） */
interface OverviewBrief {
  market: string;
  trade_date: string | null;
  phase: 'PRE_MARKET' | 'TRADING' | 'LUNCH_BREAK' | 'POST_MARKET' | 'CLOSED';
  candidates: number;
  approvals_pending: number;
  active_alerts: number;
  today_pnl: number;
  total_equity: number;
}

/** GET /system/data-quality 契约（简化只拉 CN） */
interface DataQuality {
  quality_score: number;
  trade_date?: string | null;
  total_instruments?: number;
  checked?: number;
  issues?: { stale?: number };
}

/** 市场阶段 → i18n key（pages 命名空间 phase.*，V1 OverviewPage 口径） */
const PHASE_KEY: Record<OverviewBrief['phase'], string> = {
  PRE_MARKET: 'phase.PRE_MARKET',
  TRADING: 'phase.TRADING',
  LUNCH_BREAK: 'phase.LUNCH_BREAK',
  POST_MARKET: 'phase.POST_MARKET',
  CLOSED: 'phase.CLOSED',
};

/**
 * 市场工作区壳（spec §2.1）：顶栏 56 + 侧栏 216 + 内容 Outlet + 底栏 30。
 * 数据：/overview?market={M}（30s 自动刷新）+ /system/data-quality?market=CN，
 * 注入 NavSide 徽标/状态块与 StatusBar。
 */
export default function WorkspaceLayout() {
  const { t } = useTranslation('pages');
  const { market } = useMarket();
  const { group } = useParams();
  const navigate = useNavigate();

  const [brief, setBrief] = useState<OverviewBrief | null>(null);
  const [quality, setQuality] = useState<number | null>(null);

  const fetchShell = () => {
    api<OverviewBrief>(`/overview?market=${market}`).then(setBrief).catch(() => {});
    api<DataQuality>(`/system/data-quality?market=${market}`)
      .then(d => {
        // 覆盖率过低时质量分无意义 → 显示「—」：分数公式把「该日无日K的标的」
        // (stale) 全算问题，如 US 休市/未采集时仅少量标的有日K，stale 占满
        // total_instruments → 分数被压到 0.0，看起来像数据坏了（deep-test D18）。
        const total = d?.total_instruments ?? 0;
        const stale = d?.issues?.stale ?? 0;
        const coverageOk = total === 0 || stale / total < 0.8;
        setQuality(coverageOk ? (d?.quality_score ?? null) : null);
      })
      .catch(() => setQuality(null));
  };

  useEffect(() => {
    setBrief(null);
    fetchShell();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market]);

  useAutoRefresh(fetchShell, 30000, [market]);

  /** 切市场：保持当前组 navigate(`/${m}/${当前group}`) */
  const switchMarket = (m: string) => {
    const g = group && NAV_GROUPS.some(x => x.key === group) ? group : 'overview';
    navigate(`/${m}/${g}`);
  };

  const phaseLabel = brief ? (PHASE_KEY[brief.phase] ? t(PHASE_KEY[brief.phase]) : brief.phase) : null;
  const candidateCount = brief?.candidates ?? 0;
  const approvalCount = brief?.approvals_pending ?? 0;

  return (
    <>
      {/* 背景网格 + 3 层柔光晕（原型两态共用，挂 body 级） */}
      <div className="bg-grid" />
      <div className="bg-glow g1" />
      <div className="bg-glow g2" />
      <div className="bg-glow g3" />

      <header className="ws-top">
        <button className="ws-logo" onClick={() => navigate('/')} title={t('shell.backToBigscreen')}>
          <span>司马徽研究院</span>
        </button>

        <nav className="mk-tabs">
          {MARKET_ROUTES.map(m => {
            const mk = m.toUpperCase() as Market;
            return (
              <button
                key={m}
                className={`mtab${market === mk ? ' on' : ''}`}
                onClick={() => switchMarket(m)}
              >
                {t('market.' + mk, { ns: 'common' })}
                <i className="dot" />
              </button>
            );
          })}
        </nav>

        <div className="ws-right">
          <span className="phase-chip">
            <i className="ok-dot" />
            {phaseLabel ?? t('shell.syncing')}
          </span>
          <button className="tbtn" onClick={openCommandPalette}>⌘K {t('shell.search')} <span className="mono">{MODK_HINT}</span></button>
          <button className="tbtn" onClick={() => navigate(`/${market.toLowerCase()}/agent`)} title="智能体">🤖 智能体</button>
          <button className="tbtn" onClick={() => navigate('/bigscreen')} title={t('shell.bigscreen')}>🌐 {t('shell.bigscreenShort')}</button>
          {/* 语言切换（中/英，逻辑在 LanguageMenu） */}
          <LanguageMenu />
          {/* 主题快捷切换（Task 5：Dropdown 5 选，逻辑在 ThemeMenu） */}
          <ThemeMenu />
          {/* 头像下拉：个人中心 / 退出登录 */}
          <Dropdown
            placement="bottomRight"
            menu={{
              items: [
                { key: 'profile', label: t('shell.profile'), onClick: () => navigate('/profile') },
                { key: 'billing', label: t('shell.billing'), onClick: () => navigate('/billing') },
                { type: 'divider' },
                { key: 'logout', label: t('shell.logout'), onClick: () => {
                  localStorage.removeItem('zhice_token');
                  localStorage.removeItem('zhice_role');
                  navigate('/login');
                } },
              ],
            }}
          >
            <div className="avatar" style={{ cursor: 'pointer' }}>A</div>
          </Dropdown>
        </div>
      </header>

      <NavSide
        market={market}
        candidateCount={candidateCount}
        approvalCount={approvalCount}
        phaseLabel={phaseLabel}
        schedulerLabel={t('shell.schedulerRunning')}
      />

      <main className="ws-body">
        <Outlet />
      </main>

      <FloatingAgent />
      <StatusBar
        market={market}
        phase={brief?.phase ?? null}
        phaseLabel={phaseLabel}
        candidateCount={candidateCount}
        approvalCount={approvalCount}
        todayPnl={brief ? brief.today_pnl : null}
        quality={quality}
      />
    </>
  );
}
