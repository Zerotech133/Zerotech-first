import { useCallback, useEffect, useState } from 'react';
import { Button, Card, Col, message, Popconfirm, Row, Segmented, Spin, Table, Tag, Typography } from 'antd';
import { useTranslation } from 'react-i18next';
import { api } from '../services/api';
import { useAutoRefresh } from '../hooks/useAutoRefresh';
import { useMarket, pnlColor, fmtMoney, type Market } from '../contexts/MarketContext';
import { strategyLabel } from '../i18n/strategyName';
import { useRole } from '../hooks/useRole';

const { Text } = Typography;

/* ───────────────────────── 类型（后端 /governance-ext 契约） ───────────────────────── */

/** /versions-dashboard 单版本卡片 */
interface VersionEntry {
  strategy_version_id: string;
  strategy_name: string;
  market: string;
  version_no: string;
  status: string;
  position_count: number;
  unrealized_pnl: number;
}

interface VersionsDashboard {
  ACTIVE: VersionEntry[];
  STAGING: VersionEntry[];
  DRAFT: VersionEntry[];
  generated_at: string;
}

/** /red-lines 单项 */
interface RedLineRow {
  line_name: string;
  threshold: string;
  current: string;
  status: 'PASS' | 'BREACH';
  detail: string;
}

interface RedLinesResp {
  lines: RedLineRow[];
  market: string;
}

/** /errors/trend 周桶 */
interface TrendWeek {
  week_start: string;
  counts: Record<string, number>;
}

interface TrendResp {
  weeks: TrendWeek[];
  dimensions: string[];
}

/** /governance/promotion-decisions 单条晋升/回滚/延后决策 */
interface PromotionDecision {
  decision_id: string;
  strategy_version_id: string;
  decision: string;
  from_status: string;
  to_status: string;
  reason: string;
  samples: number | null;
  win_rate: number | null;
  pnl_ratio: number | null;
  confirm_status: string;
  created_at: string;
}

/* ───────────────────────── 常量 ───────────────────────── */

/** 归因维度 → i18n key */
const DIM_LABEL_KEYS: Record<string, string> = {
  SELECTION: 'governance.dimSelection',
  ENTRY: 'governance.dimEntry',
  EXIT: 'governance.dimExit',
  MARKET: 'governance.dimMarket',
  RISK: 'governance.dimRisk',
  BEHAVIOR: 'governance.dimBehavior',
};

/** 市场代码 → Tag 颜色 */
const MARKET_COLOR: Record<string, string> = {
  CN: 'volcano',
  US: 'blue',
  HK: 'purple',
  CRYPTO: 'gold',
};

const COLS: { key: 'ACTIVE' | 'STAGING' | 'DRAFT'; labelKey: string; color: string }[] = [
  { key: 'ACTIVE', labelKey: 'governance.colActive', color: 'green' },
  { key: 'STAGING', labelKey: 'governance.colStaging', color: 'gold' },
  { key: 'DRAFT', labelKey: 'governance.colDraft', color: 'default' },
];

/** 决策类型 → i18n key / 颜色 */
const DECISION_LABEL_KEYS: Record<string, string> = { PROMOTE: 'governance.decisionPromote', ROLLBACK: 'governance.decisionRollback', DEFER: 'governance.decisionDefer' };
const DECISION_COLOR: Record<string, string> = { PROMOTE: 'green', ROLLBACK: 'red', DEFER: 'default' };

/* ───────────────────────── 版本卡片 ───────────────────────── */

function VersionCard({ v, market }: { v: VersionEntry; market: Market }) {
  const { t } = useTranslation('pages');
  return (
    <Card size="small" style={{ marginBottom: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
        <b style={{ fontSize: 13 }}>{strategyLabel(v.strategy_name)}</b>
        <Tag color={MARKET_COLOR[v.market] ?? 'default'} style={{ marginRight: 0 }}>
          {t('market.' + v.market, { ns: 'common' })}
        </Tag>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6, fontSize: 12 }}>
        <span style={{ color: '#8B93A7' }}>
          v{v.version_no} · {t('governance.holding')} <b style={{ color: '#C5CBD8' }}>{v.position_count}</b>
        </span>
        <span
          style={{
            fontFamily: 'monospace',
            fontWeight: 700,
            color: v.unrealized_pnl === 0 ? '#8B93A7' : pnlColor(v.unrealized_pnl, market),
          }}
        >
          {fmtMoney(v.unrealized_pnl, 0)}
        </span>
      </div>
    </Card>
  );
}

/* ───────────────────────── 页面 ───────────────────────── */

export default function GovernanceBoardPage() {
  const { t } = useTranslation('pages');
  const { canWrite } = useRole();  // VIEWER 只读：隐藏确认/灰度
  const { market } = useMarket();

  const [dashboard, setDashboard] = useState<VersionsDashboard | null>(null);
  const [redLines, setRedLines] = useState<RedLineRow[]>([]);
  const [trend, setTrend] = useState<TrendResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<string | number>('versions');
  const [decisions, setDecisions] = useState<PromotionDecision[]>([]);
  const [confirming, setConfirming] = useState<string | null>(null);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      api<VersionsDashboard>(`/governance-ext/versions-dashboard?market=${market}`).catch(() => null),
      api<RedLinesResp>(`/governance-ext/red-lines?market=${market}`).catch(() => null),
      api<TrendResp>(`/governance-ext/errors/trend?market=${market}&weeks=8`).catch(() => null),
      // 晋升决策为全局（approver_type=SYSTEM，不按 market 过滤）
      api<PromotionDecision[]>(`/governance/promotion-decisions?limit=100`).catch(() => null),
    ]).then(([d, r, tr, pd]) => {
      if (cancelled) return;
      setDashboard(d ?? { ACTIVE: [], STAGING: [], DRAFT: [], generated_at: '' });
      setRedLines(r?.lines ?? []);
      setTrend(tr ?? { weeks: [], dimensions: [] });
      setDecisions(pd ?? []);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [market]);

  useEffect(() => { return load(); }, [load]);
  useAutoRefresh(load, 30000, [market]);

  // 确认晋升/回滚/延后决策（不传 init.headers：auth+content-type 由 api 默认提供）
  const handleConfirm = async (id: string) => {
    setConfirming(id);
    try {
      await api(`/governance/promotion-decisions/${id}/confirm`, { method: 'POST' });
      message.success(t('governance.msgDecisionConfirmed'));
      load();
    } catch {
      /* api 内部已 showToast 报错 */
    } finally {
      setConfirming(null);
    }
  };

  // 对拟晋升 PROD 的版本开启 14 天灰度观察（后端已就绪，此前无 UI 入口）
  const handleGrayStart = async (sv: string) => {
    setConfirming(sv);
    try {
      await api(`/governance/gray/start/${encodeURIComponent(sv)}`, {
        method: 'POST',
        body: JSON.stringify({ scope: '10%仓位上限', observation_days: 14 }),
      });
      message.success(t('governance.msgGrayStarted'));
      load();
    } catch {
      /* handled */
    } finally {
      setConfirming(null);
    }
  };

  const dims = trend?.dimensions ?? [];
  const pendingDecisions = decisions.filter((d) => d.confirm_status === 'PENDING').length;
  const breachCount = redLines.filter((l) => l.status === 'BREACH').length;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, margin: 0 }}>{t('governance.title')}</h1>
        <span style={{ color: '#8B93A7', fontSize: 13 }}>
          {dashboard
            ? t('governance.summaryCounts', { active: dashboard.ACTIVE.length, staging: dashboard.STAGING.length, draft: dashboard.DRAFT.length })
            : t('governance.summaryFallback')}
          {redLines.length > 0 && (
            <> · {t('governance.redlineLabel')} {breachCount > 0
              ? <span style={{ color: '#F0524D', fontWeight: 700 }}>{t('governance.redlineBreach', { n: breachCount })}</span>
              : <span style={{ color: '#12A150' }}>{t('governance.redlineAllPass')}</span>}</>
          )}
        </span>
      </div>

      <Segmented
        value={tab}
        onChange={setTab}
        options={[
          { label: t('governance.tabVersions'), value: 'versions' },
          { label: t('governance.tabRedlines'), value: 'redlines' },
          { label: t('governance.tabErrors'), value: 'errors' },
          { label: pendingDecisions > 0 ? `${t('governance.tabDecisions')} (${pendingDecisions})` : t('governance.tabDecisions'), value: 'decisions' },
        ]}
        style={{ marginBottom: 16 }}
      />

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
      ) : (
        <>
          {/* ── Tab 1 版本看板：三列卡片 ── */}
          {tab === 'versions' && dashboard && (
            <Row gutter={12}>
              {COLS.map((c) => (
                <Col key={c.key} span={8}>
                  <div style={{ marginBottom: 8, fontWeight: 700 }}>
                    <Tag color={c.color}>{t(c.labelKey)}</Tag>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {t('governance.versionCount', { n: dashboard[c.key].length })}
                    </Text>
                  </div>
                  {dashboard[c.key].length === 0 ? (
                    <div style={{ color: '#8B93A7', fontSize: 12, padding: '12px 4px' }}>—</div>
                  ) : (
                    dashboard[c.key].map((v) => <VersionCard key={v.strategy_version_id} v={v} market={market} />)
                  )}
                </Col>
              ))}
            </Row>
          )}

          {/* ── Tab 2 红线检查 ── */}
          {tab === 'redlines' && (
            <Table<RedLineRow>
              rowKey="line_name"
              size="small"
              dataSource={redLines}
              pagination={false}
              columns={[
                {
                  title: t('governance.colLineName'), dataIndex: 'line_name', key: 'line_name', width: 150,
                  render: (v: string) => <b>{v}</b>,
                },
                {
                  title: t('governance.colThreshold'), dataIndex: 'threshold', key: 'threshold', width: 160,
                  render: (v: string) => <span style={{ fontFamily: 'monospace' }}>{v}</span>,
                },
                {
                  title: t('governance.colCurrent'), dataIndex: 'current', key: 'current', width: 130, align: 'right',
                  render: (v: string) => <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{v}</span>,
                },
                {
                  title: t('governance.colStatus'), dataIndex: 'status', key: 'status', width: 90,
                  render: (v: RedLineRow['status']) => (
                    <Tag color={v === 'BREACH' ? 'red' : 'green'}>{v === 'BREACH' ? t('governance.breach') : t('governance.pass')}</Tag>
                  ),
                },
                {
                  title: t('governance.colDetail'), dataIndex: 'detail', key: 'detail',
                  render: (v: string) => <Text type="secondary" style={{ fontSize: 12 }}>{v}</Text>,
                },
              ]}
            />
          )}

          {/* ── Tab 3 错误趋势：近 8 周矩阵 ── */}
          {tab === 'errors' && (
            <Table<TrendWeek>
              rowKey="week_start"
              size="small"
              dataSource={trend?.weeks ?? []}
              pagination={false}
              scroll={{ x: 'max-content' }}
              columns={[
                {
                  title: t('governance.colWeekStart'), dataIndex: 'week_start', key: 'week_start', width: 110, fixed: 'left',
                  render: (v: string) => <span style={{ fontFamily: 'monospace' }}>{v}</span>,
                },
                ...dims.map((d) => ({
                  title: DIM_LABEL_KEYS[d] ? t(DIM_LABEL_KEYS[d]) : d,
                  key: d,
                  width: 90,
                  align: 'right' as const,
                  render: (_: unknown, row: TrendWeek) => {
                    const v = row.counts?.[d] ?? 0;
                    return (
                      <span
                        style={{
                          fontFamily: 'monospace',
                          fontWeight: v > 0 ? 700 : 400,
                          color: v > 0 ? '#F0524D' : '#5A6178',
                          background: v > 0 ? 'rgba(240,82,77,0.08)' : 'transparent',
                          padding: '2px 8px',
                          borderRadius: 4,
                        }}
                      >
                        {v}
                      </span>
                    );
                  },
                })),
              ]}
            />
          )}

          {/* ── Tab 4 待确认决策：SYSTEM 产出的晋升/回滚/延后，人工确认入口 ── */}
          {tab === 'decisions' && (
            <Table<PromotionDecision>
              rowKey="decision_id"
              size="small"
              dataSource={decisions}
              pagination={false}
              columns={[
                {
                  title: t('governance.colSv'), dataIndex: 'strategy_version_id', key: 'sv', width: 170,
                  render: (v: string) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</span>,
                },
                {
                  title: t('governance.colDecision'), dataIndex: 'decision', key: 'decision', width: 80,
                  render: (v: string) => <Tag color={DECISION_COLOR[v] ?? 'default'}>{DECISION_LABEL_KEYS[v] ? t(DECISION_LABEL_KEYS[v]) : v}</Tag>,
                },
                {
                  title: t('governance.colTransition'), key: 'transition', width: 150,
                  render: (_: unknown, r: PromotionDecision) => (
                    <span style={{ fontSize: 12, fontFamily: 'monospace' }}>{r.from_status} → {r.to_status}</span>
                  ),
                },
                {
                  title: t('governance.colWinRate'), dataIndex: 'win_rate', key: 'win_rate', width: 70, align: 'right',
                  render: (v: number | null) => (v == null ? '—' : `${(v * 100).toFixed(0)}%`),
                },
                {
                  title: t('governance.colSamples'), dataIndex: 'samples', key: 'samples', width: 60, align: 'right',
                  render: (v: number | null) => v ?? '—',
                },
                {
                  title: t('governance.colReason'), dataIndex: 'reason', key: 'reason',
                  render: (v: string) => <Text type="secondary" style={{ fontSize: 12 }}>{v}</Text>,
                },
                {
                  title: t('governance.colConfirm'), dataIndex: 'confirm_status', key: 'confirm_status', width: 80,
                  render: (v: string) => (
                    <Tag color={v === 'CONFIRMED' ? 'green' : 'gold'}>{v === 'CONFIRMED' ? t('governance.confirmed') : t('governance.pendingConfirm')}</Tag>
                  ),
                },
                {
                  title: t('governance.colAction'), key: 'action', width: 170,
                  render: (_: unknown, r: PromotionDecision) => (
                    !canWrite || r.confirm_status !== 'PENDING' ? null : (
                      <div style={{ display: 'flex', gap: 8 }}>
                        <Popconfirm
                          title={t('governance.confirmDecisionTitle', { decision: DECISION_LABEL_KEYS[r.decision] ? t(DECISION_LABEL_KEYS[r.decision]) : r.decision, from: r.from_status, to: r.to_status })}
                          onConfirm={() => handleConfirm(r.decision_id)}
                          okText={t('common:action.confirm')}
                          cancelText={t('common:action.cancel')}
                        >
                          <Button size="small" type="primary" loading={confirming === r.decision_id}>{t('common:action.confirm')}</Button>
                        </Popconfirm>
                        {r.to_status === 'PROD' && (
                          <Popconfirm
                            title={t('governance.confirmGrayTitle', { sv: r.strategy_version_id })}
                            onConfirm={() => handleGrayStart(r.strategy_version_id)}
                            okText={t('governance.grayStart')}
                            cancelText={t('common:action.cancel')}
                          >
                            <Button size="small" loading={confirming === r.strategy_version_id}>{t('governance.grayBtn')}</Button>
                          </Popconfirm>
                        )}
                      </div>
                    )
                  ),
                },
              ]}
            />
          )}
        </>
      )}
    </div>
  );
}
