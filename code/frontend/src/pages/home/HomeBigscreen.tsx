import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { authHeaders } from '../../services/api';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';
import { MARKETS, MARKET_META, pnlColor, type Market } from '../../contexts/MarketContext';
import ThemeMenu from '../../components/ThemeMenu';

/**
 * 指挥大屏首页（Task 3 · 照 docs/mockups/theme-variants.html #bs 段 1:1）。
 * 数据：/portfolio/cross-market + 四市场 /pipeline + /market/regime
 *      + /system/data-quality(CN) + /circuit-breaker/status + /alerts。
 * 全部 API 失败静默降级为「—」（不弹 Toast），30s 自动刷新（页面不可见暂停）。
 */

/* ── 静默 GET（绕过 services/api 的 Toast） ── */
async function get<T>(path: string): Promise<T> {
  const resp = await fetch(`/api/v1${path}`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json() as Promise<T>;
}

/* ── API 契约 ── */
interface MarketSummary {
  market: Market;
  currency: string;
  total_equity: number;
  unrealized_pnl: number;
  today_pnl: number;
  total_pnl: number;
  position_count: number;
  account_count: number;
  equity_cny: number;
}
interface CrossMarketData {
  markets: MarketSummary[];
  total_cny: number;
  total_pnl_cny: number;
  fx_rates?: Record<string, number>;
}
interface Funnel {
  candidate: number;
  plan: number;
  approval_pending: number;
  position: number;
  replay: number;
}

/* ── 市场视觉常量（旗标色四市场固定，不随主题变） ── */
const FLAG: Record<Market, string> = { CN: 'flag-cn', US: 'flag-us', HK: 'flag-hk', CRYPTO: 'flag-crypto' };
const FLAG_DOT: Record<Market, string> = {
  CN: 'flag-dot-cn',
  US: 'flag-dot-us',
  HK: 'flag-dot-hk',
  CRYPTO: 'flag-dot-crypto',
};
const CCY_NOTE: Record<Market, string> = {
  CN: 'CNY · SH/SZ/BJ',
  US: 'USD · NASDAQ/NYSE',
  HK: 'HKD · HKEX',
  CRYPTO: 'USDT · BINANCE · 24×7',
};
const REGIME_META: Record<string, { labelKey: string; cls: string }> = {
  BULL: { labelKey: 'home.regimeBull', cls: 'bull' },
  BEAR: { labelKey: 'home.regimeBear', cls: 'bear' },
  SIDEWAYS: { labelKey: 'home.regimeSideways', cls: 'side' },
};
const WEEK_KEYS = ['weekSun', 'weekMon', 'weekTue', 'weekWed', 'weekThu', 'weekFri', 'weekSat'] as const;

/** fx_rates 缺失时的本地兜底（口径与后端一致） */
const LOCAL_FX: Record<string, number> = { CNY: 1, USD: 7.2, HKD: 0.92, USDT: 7.2 };

/* ── 格式化 ── */
const p2 = (n: number) => String(n).padStart(2, '0');
/** 整数格式化：非有限数（NaN / Infinity）显示为 — */
const fmtInt = (v: number) => Number.isFinite(v) ? Math.round(v).toLocaleString('en-US') : '—';
/** 带符号格式化：非有限数（NaN / Infinity）显示为 — */
const fmtSigned = (v: number, digits = 0) =>
  Number.isFinite(v)
    ? `${v > 0 ? '+' : ''}${v.toLocaleString('en-US', { maximumFractionDigits: digits })}`
    : '—';
const arrow = (v: number) => (v > 0 ? '▲' : v < 0 ? '▼' : '·');
const cap99 = (n: number) => (n > 99 ? '99+' : String(n));
const convPct = (funnel: Funnel | undefined) => {
  if (!funnel || !funnel.candidate) return null;
  return Math.round((funnel.position / funnel.candidate) * 100);
};

export default function HomeBigscreen() {
  const { t } = useTranslation('pages');
  const navigate = useNavigate();
  const [now, setNow] = useState(() => new Date());
  const [cross, setCross] = useState<CrossMarketData | null>(null);
  const [funnels, setFunnels] = useState<Partial<Record<Market, Funnel>>>({});
  const [regimes, setRegimes] = useState<Partial<Record<Market, string>>>({});
  const [quality, setQuality] = useState<number | null>(null);
  const [breakerActive, setBreakerActive] = useState<boolean | null>(null);
  const [openAlerts, setOpenAlerts] = useState<number | null>(null);

  /* 未登录：大屏保持公开可看（骨架「—」），顶部给登录引导。
     401 的 "Failed to load resource" 红字是浏览器对失败请求自动打印、JS 无法抑制，
     而本屏 8 个端点全部需要鉴权——无 token 时发了必 401 且数据恒为空，
     因此直接跳过请求（骨架照常），登录回来后轮询自动恢复。 */
  const hasToken = Boolean(localStorage.getItem('zhice_token'));

  /* 每秒时钟 */
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(t);
  }, []);

  /* 全量拉取（失败各自静默；未登录跳过，见 hasToken 注释） */
  const load = useCallback(() => {
    if (!localStorage.getItem('zhice_token')) return;
    get<CrossMarketData>('/portfolio/cross-market').then(setCross).catch(() => {});
    MARKETS.forEach((m) => {
      get<{ funnel: Funnel }>(`/pipeline?market=${m}`)
        .then((r) => setFunnels((prev) => ({ ...prev, [m]: r.funnel })))
        .catch(() => {});
      get<{ regime: string }>(`/market/regime?market=${m}`)
        .then((r) => setRegimes((prev) => ({ ...prev, [m]: r.regime })))
        .catch(() => {});
    });
    get<{ quality_score: number }>('/system/data-quality?market=CN')
      .then((r) => setQuality(r.quality_score))
      .catch(() => setQuality(null));
    get<{ active: boolean }>('/circuit-breaker/status')
      .then((r) => setBreakerActive(r.active))
      .catch(() => setBreakerActive(null));
    get<{ status?: string }[]>('/alerts')
      .then((r) => setOpenAlerts(Array.isArray(r) ? r.filter((a) => a?.status === 'OPEN').length : 0))
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [load]);
  useAutoRefresh(load, 30_000, [load]);

  /* ── 派生指标 ── */
  // 仅统计启用市场(US/CRYPTO)：后端 cross.markets 含全部 4 市场，按 MARKETS 过滤，
  // 令指挥总览的账户/持仓/盈亏/总权益口径与产品启用市场一致。
  const _enabled = new Set<Market>(MARKETS);
  const marketRows = (cross?.markets ?? []).filter((m) => _enabled.has(m.market));
  const byMarket = new Map(marketRows.map((m) => [m.market, m]));
  const fx = cross?.fx_rates ?? LOCAL_FX;
  const rateOf = (ccy: string) => fx[ccy] ?? LOCAL_FX[ccy] ?? 1;
  const totalAccounts = marketRows.reduce((s, m) => s + (m.account_count ?? 0), 0);
  const posTotal = marketRows.reduce((s, m) => s + (m.position_count ?? 0), 0);
  const apprTotal = MARKETS.reduce((s, m) => s + (funnels[m]?.approval_pending ?? 0), 0);
  const todayCny = marketRows.reduce((s, m) => s + (m.today_pnl ?? 0) * rateOf(m.currency), 0);
  const realizedCny = marketRows.reduce(
    (s, m) => s + ((m.total_pnl ?? 0) - (m.unrealized_pnl ?? 0)) * rateOf(m.currency),
    0,
  );

  const timeStr = `${p2(now.getHours())}:${p2(now.getMinutes())}:${p2(now.getSeconds())}`;
  const dateStr = `${now.getFullYear()}-${p2(now.getMonth() + 1)}-${p2(now.getDate())} · ${t(`home.${WEEK_KEYS[now.getDay()]}`)} · UTC+8`;

  /* 质量环（≥90 绿 / ≥70 金 / 否则红） */
  const ringColor = quality == null ? 'var(--violet)' : quality >= 90 ? 'var(--green)' : quality >= 70 ? 'var(--gold)' : 'var(--red)';
  const ringDeg = quality == null ? 0 : Math.max(0, Math.min(100, quality)) * 3.6;

  // 总权益/总盈亏改从启用市场的行重算（不用后端 cross.total_cny/total_pnl_cny 的 4 市场汇总）
  const totalEquityCny = marketRows.reduce((s, m) => s + (m.total_equity ?? 0) * rateOf(m.currency), 0);
  const totalPnl = marketRows.reduce((s, m) => s + (m.total_pnl ?? 0) * rateOf(m.currency), 0);

  return (
    <div className="bs-page">
      {/* ── 头部：Logo + 时钟 + 扫描线 + 状态 pills ── */}
      <div className="bs-head">
        <div>
          <div className="logo" style={{ fontSize: 34, fontWeight: 700, letterSpacing: 4, color: '#e8c27a' }}>司马徽研究院</div>
          <div className="logo-sub">{t('home.subtitle')}</div>
        </div>
        <div className="head-clock">
          <div className="t">{timeStr}</div>
          <div className="d">{dateStr}</div>
        </div>
        <div className="scan-line" />
        <div className="head-status">
          <div className="pill">
            <span className="ok">●</span> {t('home.scheduler')}
          </div>
          <div className="pill">
            {breakerActive == null ? (
              <span>○</span>
            ) : breakerActive ? (
              <span className="bad">●</span>
            ) : (
              <span className="ok">●</span>
            )}{' '}
            {t('home.simTrade')} {breakerActive == null ? '—' : breakerActive ? t('home.simStop') : t('home.simAuto')}
          </div>
          <div className="pill">
            {breakerActive == null ? (
              <span>○</span>
            ) : breakerActive ? (
              <span className="bad">●</span>
            ) : (
              <span className="ok">●</span>
            )}{' '}
            {t('home.breaker')} {breakerActive == null ? '—' : breakerActive ? t('home.breakerTriggered') : t('home.breakerNormal')}
          </div>
          {/* 主题切换入口（Task 5：与工作区顶栏共用 ThemeMenu） */}
          <ThemeMenu />
        </div>
      </div>

      {/* ── 未登录引导条：骨架照常展示，顶部提示 + 去登录 ── */}
      {!hasToken && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 14,
            marginBottom: 14,
            padding: '10px 16px',
            borderRadius: 10,
            border: '1px solid color-mix(in srgb, var(--gold, #F6A609) 45%, transparent)',
            background: 'color-mix(in srgb, var(--gold, #F6A609) 10%, transparent)',
            fontSize: 13,
          }}
        >
          <span>{t('home.loginHint')}</span>
          <button
            onClick={() => navigate('/login')}
            style={{
              padding: '4px 18px',
              borderRadius: 6,
              border: 'none',
              cursor: 'pointer',
              fontSize: 13,
              fontWeight: 600,
              background: 'var(--gold, #F6A609)',
              color: '#101014',
            }}
          >
            {t('home.goLogin')}
          </button>
        </div>
      )}

      {/* ── 指挥总览 KPI ── */}
      <div className="bs-title">
        <h2>{t('home.cmdOverview')}</h2>
        <span className="en">{t('home.cmdOverviewEn')}</span>
        <div className="line" />
      </div>
      <div className="kpis">
        <div className="glass kpi">
          <i className="corner tl" />
          <i className="corner br" />
          <div className="label">
            {t('home.kpiTotalAsset')} <span className="mono kpi-tag">{t('home.kpiTagTotal')}</span>
          </div>
          <div className="value num">
            {cross ? (
              <>
                <span className="cur">¥</span>
                {fmtInt(totalEquityCny)}
              </>
            ) : (
              '—'
            )}
          </div>
          <div className="sub">
            <span>{cross ? t('home.marketCount', { n: marketRows.length || MARKETS.length }) : '—'}</span>
            <span>{cross ? t('home.accountCount', { n: totalAccounts }) : '—'}</span>
          </div>
          <svg className="spark" width="90" height="30">
            <polyline
              points="0,22 12,20 24,23 36,16 48,18 60,12 72,14 84,8 90,10"
              fill="none"
              strokeWidth="1.5"
              opacity=".7"
              style={{ stroke: 'var(--cyan)' }}
            />
          </svg>
        </div>

        <div className="glass kpi">
          <i className="corner tl" />
          <i className="corner br" />
          <div className="label">
            {t('home.kpiTotalPnl')} <span className="mono kpi-tag">{t('home.kpiTotalPnl')}</span>
          </div>
          <div
            className={`value num ${totalPnl != null ? (totalPnl > 0 ? 'glow-red' : totalPnl < 0 ? 'glow-green' : '') : ''}`}
            style={totalPnl != null ? { color: pnlColor(totalPnl, 'CN') } : undefined}
          >
            {totalPnl != null ? fmtSigned(totalPnl) : '—'}
          </div>
          <div className="sub">
            <span>{cross ? t('home.realized', { v: fmtSigned(realizedCny) }) : t('home.realizedTotal')}</span>
          </div>
        </div>

        <div className="glass kpi">
          <i className="corner tl" />
          <i className="corner br" />
          <div className="label">
            {t('home.kpiTodayPnl')} <span className="mono kpi-tag">{t('home.kpiTagToday')}</span>
          </div>
          <div
            className={`value num ${cross ? (todayCny > 0 ? 'glow-red' : todayCny < 0 ? 'glow-green' : '') : ''}`}
            style={cross ? { color: pnlColor(todayCny, 'CN') } : undefined}
          >
            {cross ? fmtSigned(todayCny) : '—'}
          </div>
          <div className="sub">
            <span>{t('home.overnightPlusDay')}</span>
            <span>{t('home.fourMarketCny')}</span>
          </div>
        </div>

        <div className="glass kpi">
          <i className="corner tl" />
          <i className="corner br" />
          <div className="label">
            {t('home.kpiPosAppr')} <span className="mono kpi-tag">{t('home.kpiTagPosAppr')}</span>
          </div>
          <div className="value num">
            {cross ? posTotal : '—'}
            {cross && <span className="kpi-sep"> / </span>}
            {cross && <span style={{ color: 'var(--gold)' }}>{apprTotal}</span>}
          </div>
          <div className="sub">
            <span>{t('home.fourMarketPos')}</span>
            <span style={{ color: 'var(--gold)' }}>{t('home.needManual', { n: cross ? apprTotal : '—' })}</span>
          </div>
        </div>
      </div>

      {/* ── 市场矩阵 ── */}
      <div className="bs-title">
        <h2>{t('home.marketMatrix')}</h2>
        <span className="en">{t('home.clickEnter')}</span>
        <div className="line" />
      </div>
      <div className="mks">
        {MARKETS.map((m) => {
          const summary = byMarket.get(m);
          const funnel = funnels[m];
          const regime = REGIME_META[regimes[m] ?? ''] ?? null;
          const today = summary?.today_pnl;
          return (
            <div
              key={m}
              className="glass mk"
              role="button"
              tabIndex={0}
              onClick={() => navigate(`/${m.toLowerCase()}/overview`)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') navigate(`/${m.toLowerCase()}/overview`);
              }}
            >
              <div className="sweep" />
              <i className="corner tl" />
              <i className="corner br" />
              <div className="mk-head">
                <span className={`flag ${FLAG[m]}`} />
                <span className="mk-name">{t(`common:market.${m}`)}</span>
                {regime && <span className={`regime ${regime.cls}`}>{t(regime.labelKey)}</span>}
              </div>
              <div className="mk-ccy">{CCY_NOTE[m]}</div>
              <div className="mk-eq num">{summary && Number.isFinite(summary.equity_cny) ? `¥ ${fmtInt(summary.equity_cny)}` : '—'}</div>
              <div className="mk-pnl" style={today != null ? { color: pnlColor(today, m) } : undefined}>
                {today != null ? t('home.mkToday', { arrow: arrow(today), val: fmtSigned(today, 2), ccy: MARKET_META[m].short }) : t('home.mkTodayEmpty')}
              </div>
              <div className="mk-funnel">
                <div className="fnode">
                  <b>{funnel ? cap99(funnel.candidate) : '—'}</b>
                  <span>{t('home.funnelCandidate')}</span>
                </div>
                <div className="fnode alert">
                  <b>{funnel ? cap99(funnel.approval_pending) : '—'}</b>
                  <span>{t('home.funnelAppr')}</span>
                </div>
                <div className="fnode">
                  {/* 当前在仓行数（cross-market 口径，与顶部 KPI「四市场持仓」、
                      工作台 KPI 一致）；funnel.position 是历史曾持仓的标的数
                      （含已清仓），两者口径不同，这里取「当前」 */}
                  <b>{summary?.position_count != null ? cap99(summary.position_count) : funnel ? cap99(funnel.position) : '—'}</b>
                  <span>{t('home.funnelPosition')}</span>
                </div>
                <div className="fnode">
                  <b>{funnel ? cap99(funnel.replay) : '—'}</b>
                  <span>{t('home.funnelReplay')}</span>
                </div>
              </div>
              <div className="mk-enter">{t('home.enterWorkspace')}</div>
            </div>
          );
        })}
      </div>

      {/* ── 底部：闭环漏斗表 + 系统健康 ── */}
      <div className="bs-low">
        <div className="glass bs-panel">
          <i className="corner tl" />
          <i className="corner br" />
          <div className="panel-label" style={{ marginBottom: 12 }}>
            {t('home.closedLoopFunnel')}
            {/* 口径说明：漏斗各列为「历史累计到达该阶段的标的数」（含已了结），
                与顶部 KPI 的当前口径不同——候选(478 历史)≠侧栏候选(最新池日)、
                持仓列≠当前在仓行数 */}
            <span style={{ marginLeft: 10, fontSize: 11, color: 'var(--txt-dim, #8B93A7)' }}>{t('home.funnelScopeNote')}</span>
          </div>
          <table className="tbl">
            <thead>
              <tr>
                <th>{t('home.colMarket')}</th>
                <th>{t('home.funnelCandidate')}</th>
                <th>{t('home.colPlan')}</th>
                <th>{t('home.funnelAppr')}</th>
                <th>{t('home.funnelPosition')}</th>
                <th>{t('home.funnelReplay')}</th>
                <th style={{ width: 190 }}>{t('home.colConversion')}</th>
              </tr>
            </thead>
            <tbody>
              {MARKETS.map((m) => {
                const f = funnels[m];
                const pct = convPct(f);
                return (
                  <tr key={m}>
                    <td className="name">
                      <span className={`flag-dot ${FLAG_DOT[m]}`} />
                      {t(`common:market.${m}`)}
                    </td>
                    <td>{f ? cap99(f.candidate) : '—'}</td>
                    <td>{f ? cap99(f.plan) : '—'}</td>
                    <td style={{ color: f && f.approval_pending > 0 ? 'var(--gold)' : undefined }}>
                      {f ? cap99(f.approval_pending) : '—'}
                    </td>
                    <td>{f ? cap99(f.position) : '—'}</td>
                    <td>{f ? cap99(f.replay) : '—'}</td>
                    <td>
                      <span className="conv-bar">
                        <i style={{ width: pct != null ? `${Math.min(100, pct)}%` : 0 }} />
                      </span>
                      <span className="mono">{pct != null ? `${pct}%` : '—'}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="glass bs-panel">
          <i className="corner tl" />
          <i className="corner br" />
          <div className="panel-label" style={{ marginBottom: 12 }}>
            {t('home.systemHealth')}
          </div>
          <div className="sys-ring">
            <div
              className="ring"
              style={
                quality != null
                  ? {
                      background: `conic-gradient(${ringColor} 0deg ${ringDeg}deg, var(--ring-track) ${ringDeg}deg 360deg)`,
                      boxShadow: `0 0 16px color-mix(in srgb, ${ringColor} 25%, transparent)`,
                    }
                  : undefined
              }
            >
              <div className="ring-in">
                <b style={quality != null ? { color: ringColor } : undefined}>
                  {quality != null ? quality.toFixed(1) : '—'}
                </b>
                <span>{t('home.qualityScore')}</span>
              </div>
            </div>
            <div className="sys-rows">
              <div className="sys-row">
                <span>{t('home.scheduler')}</span>
                <b>
                  <i className="ok-dot" />
                  {t('home.running')}
                </b>
              </div>
              <div className="sys-row">
                <span>{t('home.breakerLabel')}</span>
                <b style={breakerActive ? { color: 'var(--red)' } : { color: 'var(--green)' }}>
                  {breakerActive == null ? '—' : breakerActive ? t('home.breakerStatusTriggered') : t('home.breakerNormal')}
                </b>
              </div>
              <div className="sys-row">
                <span>{t('home.simTrade')}</span>
                <b style={breakerActive ? { color: 'var(--gold)' } : { color: 'var(--cyan)' }}>
                  {breakerActive == null ? '—' : breakerActive ? 'OFF' : 'AUTO'}
                </b>
              </div>
              <div className="sys-row">
                <span>{t('home.activeAlerts')}</span>
                <b style={openAlerts ? { color: 'var(--gold)' } : undefined}>
                  {openAlerts == null ? '—' : openAlerts}
                </b>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
