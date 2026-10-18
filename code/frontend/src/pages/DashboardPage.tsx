import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Card, Statistic, Row, Col, Tag, Table, Empty, Spin, Button, Select, Tooltip } from 'antd';
import { ArrowRightOutlined } from '@ant-design/icons';
import { api } from '../services/api';
import { useMarket, pnlColor, fmtPct, MARKET_META } from '../contexts/MarketContext';
import { useStrategies } from '../hooks/useStrategies';
import { useAutoRefresh } from '../hooks/useAutoRefresh';
import SymbolLink from '../components/SymbolLink';

interface AccountStats {
  market: string;
  total_equity: number;
  available_cash: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  position_ratio: number;
  position_count: number;
  account_count: number;
}

interface Funnel {
  market: string;
  candidate: number;
  plan: number;
  approval_pending: number;
  position: number;
  replay: number;
}

interface Decision {
  ts_code: string;
  strategy_version_id: string;
  market: string;
  stage: string;
  stages: Record<string, { done: boolean; [k: string]: unknown }>;
  attention: boolean;
}

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

/** 市场阶段 → Tag 配色（V1 OverviewPage 口径：盘前蓝/盘中绿/午休黄/盘后橙/休市灰；文案见 dashboard.phase.*） */
const PHASE_COLOR: Record<OverviewBrief['phase'], string> = {
  PRE_MARKET: 'blue',
  TRADING: 'green',
  LUNCH_BREAK: 'gold',
  POST_MARKET: 'orange',
  CLOSED: 'default',
};

/** 闭环漏斗步骤 → 配色（文案见 dashboard.funnel.*） */
const FUNNEL_STEPS: { key: keyof Omit<Funnel, 'market'>; color: string }[] = [
  { key: 'candidate', color: '#5B6CF0' },
  { key: 'plan', color: '#7C89F5' },
  { key: 'approval_pending', color: '#F6A609' },
  { key: 'position', color: '#12A150' },
  { key: 'replay', color: '#8B93A7' },
];

export default function DashboardPage() {
  const { t } = useTranslation('pages');
  const { market } = useMarket();
  const { strategies, accounts, strategy, setStrategy } = useStrategies();
  const navigate = useNavigate();
  const [stats, setStats] = useState<AccountStats | null>(null);
  const [funnel, setFunnel] = useState<Funnel | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [brief, setBrief] = useState<OverviewBrief | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      api<AccountStats>(`/accounts/stats?market=${market}`).catch(() => null),
      api<{ funnel: Funnel; decisions: Decision[] }>(`/pipeline?market=${market}${strategy ? `&strategy=${strategy}` : ''}`).catch(() => null),
      api<OverviewBrief>(`/overview?market=${market}`).catch(() => null),
    ]).then(([s, p, o]) => {
      setStats(s);
      setFunnel(p?.funnel ?? null);
      setDecisions(p?.decisions ?? []);
      setBrief(o);
      setLoading(false);
    });
  }, [market, strategy]);

  // 盘中 30s 自动刷新（页面不可见时暂停）
  const fetchQuiet = () => {
    Promise.all([
      api<AccountStats>(`/accounts/stats?market=${market}`).catch(() => null),
      api<{ funnel: Funnel; decisions: Decision[] }>(`/pipeline?market=${market}${strategy ? `&strategy=${strategy}` : ''}`).catch(() => null),
      api<OverviewBrief>(`/overview?market=${market}`).catch(() => null),
    ]).then(([s, p, o]) => {
      setStats(s);
      setFunnel(p?.funnel ?? null);
      setDecisions(p?.decisions ?? []);
      setBrief(o);
    });
  };
  useAutoRefresh(fetchQuiet, 30000, [market, strategy]);

  const attentionItems = decisions.filter(d => d.attention);
  const heldPositions = decisions.filter(d => d.stages.position?.done);

  if (loading) return <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>;

  // 选中策略时，KPI 切换为该策略账户的数据；未选时显示市场汇总
  const strategyAccount = strategy ? accounts.find(a => a.strategy_version_id === strategy) : undefined;
  const kpi = strategyAccount ?? stats;
  const equityColor = kpi ? pnlColor(kpi.unrealized_pnl, market) : '#1E2330';

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, margin: 0 }}>{t('dashboard.title', { market: t('market.' + market, { ns: 'common' }) })}</h1>
        <span style={{ color: '#8B93A7', fontSize: 13 }}>
          {MARKET_META[market].currency} · {t('dashboard.simAccount')}{strategy ? ` · ${t('dashboard.strategyTag', { id: strategy })}` : ''}
        </span>
        <div style={{ flex: 1 }} />
        <Select
          size="small"
          placeholder={t('dashboard.allStrategies')}
          allowClear
          style={{ width: 200 }}
          value={strategy || undefined}
          onChange={v => setStrategy(v ?? '')}
          options={strategies.map(s => ({ value: s, label: s }))}
        />
      </div>

      {/* 总览简报条（V1 OverviewPage 六卡浓缩）：交易日 · 阶段 · 候选 · 待授权 · 今日盈亏 */}
      {brief && (
        <Card size="small" style={{ marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 24, flexWrap: 'wrap' }}>
            <span style={{ color: '#8B93A7', fontSize: 13 }}>
              {t('dashboard.briefTradeDate')} <b style={{ color: '#1E2330' }}>{brief.trade_date ?? '—'}</b>
            </span>
            <Tag color={PHASE_COLOR[brief.phase] ?? 'default'}>
              {t(`dashboard.phase.${brief.phase}`)}
            </Tag>
            <span style={{ fontSize: 13 }}>
              {t('dashboard.candidates')} <b style={{ fontSize: 16 }}>{brief.candidates}</b>
            </span>
            <span style={{ fontSize: 13 }}>
              {t('dashboard.pending')} <b style={{ fontSize: 16, color: brief.approvals_pending > 0 ? '#F6A609' : '#1E2330' }}>{brief.approvals_pending}</b>
            </span>
            <span style={{ fontSize: 13 }}>
              {t('dashboard.todayPnl')}{' '}
              <b style={{ fontSize: 16, color: pnlColor(brief.today_pnl, market) }}>
                {brief.today_pnl > 0 ? '+' : ''}{brief.today_pnl.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </b>
            </span>
            <span style={{ fontSize: 12, color: '#8B93A7', marginLeft: 'auto' }}>
              {t('dashboard.totalEquity')} {MARKET_META[market].short}{brief.total_equity.toLocaleString('en-US')}
            </span>
          </div>
        </Card>
      )}

      {/* 账户 KPI：未选策略 = 市场汇总；选中策略 = 该策略账户 */}
      <Row gutter={16} style={{ marginBottom: 20 }}>
        <Col span={6}>
          <Card size="small">
            {/* 口径说明（deep-test C6/C7）：本卡为全局模拟盘口径——该市场全部
                策略账户（含零仓/legacy）合并，与「我的账户」页的个人资金账户
                是两套并存语义，故加 tooltip 标注而非改数字 */}
            <Statistic title={
              <Tooltip title={t('dashboard.equityScopeNote')}>
                <span>{t('dashboard.totalEquity')} ℹ</span>
              </Tooltip>
            } value={kpi?.total_equity ?? 0} precision={0}
              prefix={MARKET_META[market].short} styles={{ content: { fontSize: 22 } }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title={t('dashboard.kpiFloatPnl')} value={kpi?.unrealized_pnl ?? 0} precision={2}
              styles={{ content: { color: equityColor, fontSize: 22 } }}
              prefix={kpi && kpi.unrealized_pnl > 0 ? '+' : ''}
              suffix={<span style={{ fontSize: 13, color: equityColor }}>{kpi ? ` (${fmtPct(kpi.unrealized_pnl_pct)})` : ''}</span>} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title={t('dashboard.kpiAvailable')} value={kpi?.available_cash ?? 0} precision={0}
              prefix={MARKET_META[market].short} styles={{ content: { fontSize: 22 } }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title={t('dashboard.kpiPosition')} value={kpi?.position_ratio ?? 0} precision={1} suffix="%"
              styles={{ content: { fontSize: 22 } }} />
            <div style={{ fontSize: 12, color: '#8B93A7', marginTop: 2 }}>
              {kpi?.position_count ?? 0} {t('dashboard.stockUnit')} · {strategy ? t('dashboard.singleStrategyAccount') : t('dashboard.multiStrategyAccount', { n: stats?.account_count ?? 0 })}
            </div>
          </Card>
        </Col>
      </Row>

      {/* 闭环漏斗（各列为历史累计到达该阶段的标的数，含已了结——与
          侧栏/晨报的「最新池日候选」口径不同，见 tooltip） */}
      <Card size="small" title={
        <Tooltip title={t('dashboard.funnelScopeNote')}>
          <span>{t('dashboard.funnelTitle')} ℹ</span>
        </Tooltip>
      } style={{ marginBottom: 20 }}
        extra={<Button type="link" size="small" onClick={() => navigate(`/${market.toLowerCase()}/signals?tab=pipeline`)}>{t('dashboard.viewPipeline')} <ArrowRightOutlined /></Button>}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
          {FUNNEL_STEPS.map((step, i) => {
            const val = funnel ? funnel[step.key] : 0;
            return (
              <div key={step.key} style={{ display: 'flex', alignItems: 'center', flex: 1 }}>
                <div style={{ flex: 1, textAlign: 'center', padding: '8px 4px', background: `${step.color}14`, borderRadius: 8 }}>
                  <div style={{ fontSize: 26, fontWeight: 700, color: step.color }}>{val}</div>
                  <div style={{ fontSize: 12, color: '#4A5268' }}>{t(`dashboard.funnel.${step.key}`)}</div>
                </div>
                {i < FUNNEL_STEPS.length - 1 && <ArrowRightOutlined style={{ color: '#C5CBD8', margin: '0 6px' }} />}
              </div>
            );
          })}
        </div>
      </Card>

      <Row gutter={16}>
        {/* 需要动手 */}
        <Col span={12}>
          <Card size="small" title={<span>🔔 {t('dashboard.needAction')} {attentionItems.length > 0 && <Tag color="orange">{attentionItems.length}</Tag>}</span>}
            styles={{ body: { maxHeight: 360, overflow: 'auto' } }}>
            {attentionItems.length === 0 ? (
              <Empty description={t('dashboard.emptyAttention')} image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : (
              attentionItems.map(d => (
                <div key={d.ts_code} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid #F2F3F7' }}>
                  <div>
                    <SymbolLink code={d.ts_code} />
                    <Tag color="orange" style={{ marginLeft: 8 }}>{t('dashboard.pending')}</Tag>
                  </div>
                  <Button size="small" type="primary" onClick={() => navigate(`/${market.toLowerCase()}/signals?tab=approvals`)}>{t('dashboard.goApprove')}</Button>
                </div>
              ))
            )}
          </Card>
        </Col>

        {/* 持仓 Top */}
        <Col span={12}>
          <Card size="small" title={t('dashboard.heldTitle')}>
            <Table
              size="small"
              rowKey="ts_code"
              pagination={false}
              scroll={{ y: 300 }}
              dataSource={heldPositions.slice(0, 20)}
              columns={[
                { title: t('dashboard.colSymbol'), dataIndex: 'ts_code', key: 'ts_code', render: (v: string) => <SymbolLink code={v} /> },
                {
                  title: t('dashboard.colQty'), key: 'qty',
                  render: (_: unknown, d: Decision) => String((d.stages.position as { qty?: number })?.qty ?? '—'),
                },
                {
                  title: t('dashboard.colOpenPrice'), key: 'price',
                  render: (_: unknown, d: Decision) => {
                    const p = (d.stages.position as { open_price?: number })?.open_price;
                    return p != null ? p.toFixed(2) : '—';
                  },
                },
                {
                  title: t('dashboard.colStage'), dataIndex: 'stage', key: 'stage',
                  render: (v: string) => <Tag>{v}</Tag>,
                },
              ]}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
