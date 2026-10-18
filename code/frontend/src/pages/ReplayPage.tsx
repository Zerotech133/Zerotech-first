import { useEffect, useState } from 'react';
import {
  Table, Card, Statistic, Button, Space, Tag, Row, Col, Typography, Progress, Spin,
  Select, Tabs, Segmented,
} from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import type { ColumnsType } from 'antd/es/table';
import { api } from '../services/api';
import { useMarket, pnlColor, fmtMoney, fmtPct } from '../contexts/MarketContext';
import type { Market } from '../contexts/MarketContext';
import { useStrategies } from '../hooks/useStrategies';
import { useAutoRefresh } from '../hooks/useAutoRefresh';
import SymbolLink from '../components/SymbolLink';

const { Title, Text } = Typography;

/* ───────────────────────── 归因（单笔展开 + 错误统计共用） ───────────────────────── */

interface AttrRow {
  dimension: string;
  weight: number;
  pnl_amount: number;
  note: string;
}

function AttributionPanel({ replayId, market }: { replayId: string; market: Market }) {
  const { t } = useTranslation('pages');
  const [attrs, setAttrs] = useState<AttrRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // replay_id 含斜杠（CRYPTO ts_code 拼入 id）走 path 必 404，须走 query 形态
    api<AttrRow[]>(`/replay/attribution?replay_id=${encodeURIComponent(replayId)}`)
      .then(setAttrs)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [replayId]);

  if (loading) return <Spin size="small" style={{ padding: 16 }} />;
  if (attrs.length === 0) return <Text type="secondary" style={{ padding: 16 }}>{t('replay.noAttribution')}</Text>;

  return (
    <div style={{ padding: '8px 24px 8px 48px' }}>
      {attrs.map((a) => {
        const label = t(`replay.dim.${a.dimension}`, { defaultValue: a.dimension });
        const pct = Math.round(a.weight * 100);
        const color = a.pnl_amount >= 0 ? pnlColor(1, market) : pnlColor(-1, market);
        return (
          <Row key={a.dimension} gutter={8} align="middle" style={{ marginBottom: 6 }}>
            <Col span={3}>
              <Text strong style={{ fontSize: 12 }}>{label}</Text>
            </Col>
            <Col span={8}>
              <Progress
                percent={pct}
                size="small"
                strokeColor={color}
                format={(p) => `${p}%`}
              />
            </Col>
            <Col span={4} style={{ textAlign: 'right' }}>
              <Text style={{ color, fontSize: 12 }}>{fmtMoney(a.pnl_amount)}</Text>
            </Col>
            <Col span={9}>
              {a.note && <Text type="secondary" style={{ fontSize: 11 }}>{a.note}</Text>}
            </Col>
          </Row>
        );
      })}
    </div>
  );
}

/* ───────────────────────── Tab 1：当日案例 ───────────────────────── */

interface Replay {
  replay_id: string;
  strategy_version_id: string;
  market: string;
  ts_code: string;
  pnl: string;
  pnl_pct: string;
  exit_reason: string;
  status: string;
}

const EXIT_COLOR: Record<string, string> = {
  TAKE_PROFIT: 'green',
  STOP_LOSS: 'red',
  MANUAL: 'default',
};

function CasesTab({
  items, loading, strategy, market,
}: {
  items: Replay[]; loading: boolean; strategy: string; market: Market;
}) {
  const { t } = useTranslation('pages');
  const filtered = items.filter(r =>
    r.market === market && (!strategy || r.strategy_version_id === strategy),
  );

  const pnls = filtered.map(r => parseFloat(r.pnl) || 0);
  const totalPnl = pnls.reduce((a, b) => a + b, 0);
  const wins = pnls.filter(v => v > 0).length;
  const losses = pnls.filter(v => v < 0).length;
  const winRate = filtered.length > 0
    ? ((wins / filtered.length) * 100).toFixed(1)
    : '0.0';

  const columns: ColumnsType<Replay> = [
    {
      title: t('replay.colSymbol'),
      dataIndex: 'ts_code',
      key: 'ts_code',
      render: (v: string) => (v ? <SymbolLink code={v} /> : <span>—</span>),
    },
    {
      title: t('replay.colStrategy'),
      dataIndex: 'strategy_version_id',
      key: 'strategy_version_id',
      render: (v: string) => v ? (
        <Tag color="blue" style={{ fontFamily: 'monospace' }}>{v}</Tag>
      ) : (
        <Tag>{t('replay.noStrategy')}</Tag>
      ),
    },
    {
      title: t('replay.colPnl'),
      dataIndex: 'pnl',
      key: 'pnl',
      render: (v: string) => {
        const val = parseFloat(v) || 0;
        return <b style={{ color: pnlColor(val, market) }}>{fmtMoney(val)}</b>;
      },
      sorter: (a, b) => (parseFloat(a.pnl) || 0) - (parseFloat(b.pnl) || 0),
    },
    {
      title: t('replay.colPnlPct'),
      dataIndex: 'pnl_pct',
      key: 'pnl_pct',
      render: (v: string) => {
        const val = parseFloat(v) || 0;
        return <b style={{ color: pnlColor(val, market) }}>{fmtPct(val)}</b>;
      },
      sorter: (a, b) => (parseFloat(a.pnl_pct) || 0) - (parseFloat(b.pnl_pct) || 0),
    },
    {
      title: t('replay.colExitReason'),
      dataIndex: 'exit_reason',
      key: 'exit_reason',
      render: (v: string) => {
        const color = EXIT_COLOR[v];
        return color
          ? <Tag color={color}>{t(`replay.exit.${v}`)}</Tag>
          : <Tag>{v}</Tag>;
      },
      filters: [
        { text: t('replay.exit.TAKE_PROFIT'), value: 'TAKE_PROFIT' },
        { text: t('replay.exit.STOP_LOSS'), value: 'STOP_LOSS' },
        { text: t('replay.exit.MANUAL'), value: 'MANUAL' },
      ],
      onFilter: (value, record) => record.exit_reason === value,
    },
    {
      title: t('replay.colStatus'),
      dataIndex: 'status',
      key: 'status',
      render: (v: string) => <Tag color="default">{v}</Tag>,
    },
  ];

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic title={t('replay.statTrades')} value={filtered.length} suffix={t('replay.unitTrades')} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title={t('replay.statTotalPnl')}
              value={Math.abs(totalPnl)}
              precision={2}
              prefix={totalPnl >= 0 ? '+' : '-'}
              styles={{ content: { color: pnlColor(totalPnl, market) } }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title={t('replay.statWinRate')} value={winRate} suffix="%" />
          </Card>
        </Col>
        <Col span={3}>
          <Card size="small">
            <Statistic
              title={t('replay.statWins')}
              value={wins}
              suffix={t('replay.unitTrades')}
              styles={{ content: { color: pnlColor(1, market) } }}
            />
          </Card>
        </Col>
        <Col span={3}>
          <Card size="small">
            <Statistic
              title={t('replay.statLosses')}
              value={losses}
              suffix={t('replay.unitTrades')}
              styles={{ content: { color: pnlColor(-1, market) } }}
            />
          </Card>
        </Col>
      </Row>

      <Table<Replay>
        columns={columns}
        dataSource={filtered}
        rowKey="replay_id"
        loading={loading}
        size="small"
        pagination={{ pageSize: 20, showSizeChanger: true }}
        locale={{ emptyText: t('replay.emptyCases') }}
        expandable={{
          expandedRowRender: (record) => (
            <AttributionPanel replayId={record.replay_id} market={record.market as Market} />
          ),
          rowExpandable: () => true,
        }}
      />
    </div>
  );
}

/* ───────────────────────── Tab 2：决策复盘 ───────────────────────── */

interface DecisionReview {
  review_id: string;
  review_date: string | null;
  decision_type: string;
  ts_code: string | null;
  name: string | null;
  strategy_name: string | null;
  score: number | null;
  verdict: string | null;
  grade: string; // GOOD / FAIR / POOR
  metrics: Record<string, unknown>;
}

interface ReviewSummary {
  market: string;
  review_date: string | null;
  types: Record<string, {
    count: number; avg_score: number; good: number; fair: number; poor: number;
  }>;
  best: Array<{ review_date: string | null; decision_type: string; ts_code: string; score: number; verdict: string | null }>;
  worst: Array<{ review_date: string | null; decision_type: string; ts_code: string; score: number; verdict: string | null }>;
}

const GRADE_COLOR: Record<string, string> = {
  GOOD: 'green',
  FAIR: 'gold',
  POOR: 'red',
};

function DecisionReviewsTab({ market, refreshKey }: { market: Market; refreshKey: number }) {
  const { t } = useTranslation('pages');
  /** 决策类型文案（key 与后端库内类型同口径） */
  const DECISION_TYPE_LABELS: Record<string, string> = {
    CANDIDATE: t('replay.decisionType.CANDIDATE'),
    ENTRY: t('replay.decisionType.ENTRY'),
    EXIT: t('replay.decisionType.EXIT'),
    MISSED: t('replay.decisionType.MISSED'),
  };
  /** 三类 V1 语义类型（Segmented 筛选值，后端映射到库内类型） */
  const REVIEW_TYPE_OPTIONS = [
    { label: t('replay.reviewTypeAll'), value: '' },
    { label: t('replay.reviewTypeSelection'), value: 'SELECTION_QUALITY' },
    { label: t('replay.reviewTypeEntry'), value: 'ENTRY_TIMING' },
    { label: t('replay.reviewTypeMissed'), value: 'MISSED_REVIEW' },
  ];
  const [reviewType, setReviewType] = useState('');
  const [items, setItems] = useState<DecisionReview[]>([]);
  const [summary, setSummary] = useState<ReviewSummary | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams({ market, limit: '200' });
    if (reviewType) params.set('review_type', reviewType);
    api<DecisionReview[]>(`/replay/decision-reviews?${params.toString()}`)
      .then(setItems)
      .catch(console.error)
      .finally(() => setLoading(false));
    api<ReviewSummary>(`/replay/decision-reviews/summary?market=${market}`)
      .then(setSummary)
      .catch(console.error);
  }, [market, reviewType, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const bestKeys = new Set((summary?.best ?? []).map(b => `${b.decision_type}:${b.ts_code}`));
  const worstKeys = new Set((summary?.worst ?? []).map(w => `${w.decision_type}:${w.ts_code}`));

  const columns: ColumnsType<DecisionReview> = [
    {
      title: t('replay.revColDate'),
      dataIndex: 'review_date',
      key: 'review_date',
      width: 100,
      render: (v: string) => <Text style={{ fontFamily: 'monospace', fontSize: 12 }}>{v ?? '—'}</Text>,
      sorter: (a, b) => (a.review_date ?? '').localeCompare(b.review_date ?? ''),
      defaultSortOrder: 'descend',
    },
    {
      title: t('replay.revColType'),
      dataIndex: 'decision_type',
      key: 'decision_type',
      width: 90,
      render: (v: string) => <Tag color="blue">{DECISION_TYPE_LABELS[v] ?? v}</Tag>,
      filters: Object.entries(DECISION_TYPE_LABELS).map(([value, text]) => ({ text, value })),
      onFilter: (value, record) => record.decision_type === value,
    },
    {
      title: t('replay.revColSymbol'),
      dataIndex: 'ts_code',
      key: 'ts_code',
      width: 150,
      render: (v: string, r: DecisionReview) => (
        <span>
          {v ? <SymbolLink code={v} name={r.name ?? undefined} /> : <span>—</span>}
          {r.name && (
            <Text type="secondary" style={{ marginLeft: 6, fontSize: 12 }}>{r.name}</Text>
          )}
        </span>
      ),
    },
    {
      title: t('replay.revColStrategy'),
      dataIndex: 'strategy_name',
      key: 'strategy_name',
      width: 140,
      render: (v: string) => (
        <Text type="secondary" style={{ fontSize: 12 }}>{v ?? '—'}</Text>
      ),
    },
    {
      title: t('replay.revColScore'),
      dataIndex: 'score',
      key: 'score',
      width: 80,
      align: 'right',
      render: (v: number | null) => v == null ? (
        <Text type="secondary">—</Text>
      ) : (
        <b style={{ color: v > 0 ? pnlColor(1, market) : v < 0 ? pnlColor(-1, market) : undefined }}>
          {v.toFixed(1)}
        </b>
      ),
      sorter: (a, b) => (a.score ?? 0) - (b.score ?? 0),
    },
    {
      title: t('replay.revColGrade'),
      dataIndex: 'grade',
      key: 'grade',
      width: 80,
      render: (v: string) => {
        const color = GRADE_COLOR[v];
        return color ? <Tag color={color}>{t(`replay.grade.${v}`)}</Tag> : <Tag>{v}</Tag>;
      },
      filters: [
        { text: t('replay.grade.GOOD'), value: 'GOOD' },
        { text: t('replay.grade.FAIR'), value: 'FAIR' },
        { text: t('replay.grade.POOR'), value: 'POOR' },
      ],
      onFilter: (value, record) => record.grade === value,
    },
    {
      title: t('replay.revColVerdict'),
      dataIndex: 'verdict',
      key: 'verdict',
      ellipsis: true,
      render: (v: string) => (
        <Text type="secondary" style={{ fontSize: 12 }}>{v ?? '—'}</Text>
      ),
    },
    {
      title: t('replay.revColFlag'),
      key: 'rank_flag',
      width: 90,
      render: (_, r: DecisionReview) => {
        const key = `${r.decision_type}:${r.ts_code}`;
        return (
          <Space size={4}>
            {bestKeys.has(key) && <Tag color="green" style={{ marginRight: 0 }}>{t('replay.flagBest')}</Tag>}
            {worstKeys.has(key) && <Tag color="red" style={{ marginRight: 0 }}>{t('replay.flagWorst')}</Tag>}
          </Space>
        );
      },
    },
  ];

  // 汇总卡顺序：选股质量 / 建仓时机 / 出场时机 / 错过复盘（有数据的才展示）
  const cardOrder = ['CANDIDATE', 'ENTRY', 'EXIT', 'MISSED'].filter(typeKey => summary?.types[typeKey]);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <Segmented
          value={reviewType}
          onChange={(v) => setReviewType(v as string)}
          options={REVIEW_TYPE_OPTIONS}
        />
        {summary?.review_date && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('replay.latestReviewDay', { date: summary.review_date })}
          </Text>
        )}
      </div>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        {cardOrder.map(typeKey => {
          const s = summary!.types[typeKey];
          return (
            <Col span={6} key={typeKey}>
              <Card size="small">
                <Statistic
                  title={t('replay.summaryCardTitle', { type: DECISION_TYPE_LABELS[typeKey] ?? typeKey, count: s.count })}
                  value={s.avg_score.toFixed(1)}
                  suffix={t('replay.avgSuffix')}
                  styles={{ content: {
                    color: s.avg_score > 0 ? pnlColor(1, market) : s.avg_score < 0 ? pnlColor(-1, market) : undefined,
                    fontSize: 22,
                  } }}
                />
                <div style={{ marginTop: 4 }}>
                  <Space size={4}>
                    <Tag color="green" style={{ marginRight: 0 }}>{t('replay.grade.GOOD')} {s.good}</Tag>
                    <Tag color="gold" style={{ marginRight: 0 }}>{t('replay.grade.FAIR')} {s.fair}</Tag>
                    <Tag color="red" style={{ marginRight: 0 }}>{t('replay.grade.POOR')} {s.poor}</Tag>
                  </Space>
                </div>
              </Card>
            </Col>
          );
        })}
        {cardOrder.length === 0 && !loading && (
          <Col span={24}>
            <Card size="small"><Text type="secondary">{t('replay.emptyReviewSummary')}</Text></Card>
          </Col>
        )}
      </Row>

      <Table<DecisionReview>
        columns={columns}
        dataSource={items}
        rowKey="review_id"
        loading={loading}
        size="small"
        pagination={{ pageSize: 20, showSizeChanger: true }}
        locale={{ emptyText: t('replay.emptyReviews') }}
      />
    </div>
  );
}

/* ───────────────────────── Tab 3：错误统计 ───────────────────────── */

interface AttributionStats {
  market: string;
  total: number;
  dimensions: Array<{
    dimension: string;
    count: number;
    pnl_amount: number;
    pct: number;
  }>;
}

function ErrorStatsTab({ market, refreshKey }: { market: Market; refreshKey: number }) {
  const { t } = useTranslation('pages');
  const [stats, setStats] = useState<AttributionStats | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    api<AttributionStats>(`/replay/attribution-stats?market=${market}`)
      .then(setStats)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [market, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading && !stats) return <div style={{ padding: 48, textAlign: 'center' }}><Spin /></div>;

  const dims = stats?.dimensions ?? [];
  const total = stats?.total ?? 0;

  if (dims.length === 0) {
    return (
      <Card size="small">
        <Text type="secondary">{t('replay.noDimData')}</Text>
      </Card>
    );
  }

  return (
    <div>
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        {dims.map(d => (
          <Col xs={12} md={8} xl={4} key={d.dimension}>
            <Card size="small">
              <Statistic
                title={t(`replay.dim.${d.dimension}`, { defaultValue: d.dimension })}
                value={d.count}
                suffix={t('replay.unitTimes')}
                styles={{ content: { fontSize: 22 } }}
              />
              <Text type="secondary" style={{ fontSize: 12, color: pnlColor(d.pnl_amount, market) }}>
                {fmtMoney(d.pnl_amount)}
              </Text>
            </Card>
          </Col>
        ))}
      </Row>

      <Card size="small" title={t('replay.dimShareTitle', { total })}>
        {dims.map(d => {
          const pct = Math.round(d.pct * 100);
          const color = d.pnl_amount >= 0 ? pnlColor(1, market) : pnlColor(-1, market);
          return (
            <Row key={d.dimension} gutter={8} align="middle" style={{ marginBottom: 8 }}>
              <Col span={3}>
                <Text strong style={{ fontSize: 12 }}>{t(`replay.dim.${d.dimension}`, { defaultValue: d.dimension })}</Text>
              </Col>
              <Col span={13}>
                <Progress
                  percent={pct}
                  size="small"
                  strokeColor={color}
                  format={(p) => `${p}%（${d.count}）`}
                />
              </Col>
              <Col span={8} style={{ textAlign: 'right' }}>
                <Text type="secondary" style={{ fontSize: 12, color }}>
                  {fmtMoney(d.pnl_amount)}
                </Text>
              </Col>
            </Row>
          );
        })}
      </Card>
    </div>
  );
}

/* ───────────────────────── 页面 ───────────────────────── */

export default function ReplayPage() {
  const { t } = useTranslation('pages');
  const { market } = useMarket();
  const { strategies, strategy, setStrategy } = useStrategies();
  const [items, setItems] = useState<Replay[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const load = () => {
    setLoading(true);
    setRefreshKey(k => k + 1);
    api<Replay[]>('/replay')
      .then(setItems)
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useAutoRefresh(load, 30000, [market, strategy]);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <Space align="center">
          <Title level={4} style={{ margin: 0 }}>{t('replay.title')}</Title>
          <Button
            icon={<ReloadOutlined />}
            size="small"
            onClick={load}
            loading={loading}
          >
            {t('replay.refresh')}
          </Button>
        </Space>
        <div style={{ flex: 1 }} />
        <Select
          size="small"
          placeholder={t('replay.allStrategies')}
          allowClear
          style={{ width: 200 }}
          value={strategy || undefined}
          onChange={v => setStrategy(v ?? '')}
          options={strategies.map(s => ({ value: s, label: s }))}
        />
      </div>

      <Tabs
        defaultActiveKey="cases"
        items={[
          {
            key: 'cases',
            label: t('replay.tabCases'),
            children: <CasesTab items={items} loading={loading} strategy={strategy} market={market} />,
          },
          {
            key: 'decisions',
            label: t('replay.tabDecisions'),
            children: <DecisionReviewsTab market={market} refreshKey={refreshKey} />,
          },
          {
            key: 'errors',
            label: t('replay.tabErrors'),
            children: <ErrorStatsTab market={market} refreshKey={refreshKey} />,
          },
        ]}
      />
    </div>
  );
}
