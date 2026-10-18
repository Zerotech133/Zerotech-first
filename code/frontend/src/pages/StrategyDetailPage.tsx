import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Button,
  Card,
  Col,
  Descriptions,
  InputNumber,
  Modal,
  Progress,
  Row,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { api } from '../services/api';
import {
  MARKET_META,
  fmtMoney,
  fmtPct,
  pnlColor,
  useMarket,
} from '../contexts/MarketContext';
import SymbolLink from '../components/SymbolLink';
import { useRole } from '../hooks/useRole';
import { listCatalog } from '../services/myStrategiesApi';
import { listSubscriptions, type SubscriptionView } from '../services/billingApi';
import {
  getSubscriptionPositions,
  getSubscriptionTrades,
  type PlatformPosition,
  type PlatformTrade,
} from '../services/subscriptionObservationApi';
import {
  createMirrorAccount,
  getMirrorAccount,
  type MirrorAccountDetail,
  type MirrorPosition,
} from '../services/mirrorSimApi';

/** 订阅是否生效：优先 effective_status（B3-6），旧响应缺省时回退 active 布尔。 */
function isSubActive(s: SubscriptionView): boolean {
  const st = s.effective_status;
  if (st) return st === 'active' || st === 'permanent';
  return s.active;
}

const { Title } = Typography;

interface DetailAccount {
  initial_capital: number;
  available_cash: number;
  market_value: number;
  total_equity: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  position_ratio: number;
  position_count: number;
}

interface DetailPosition {
  position_id: string;
  ts_code: string;
  direction: string;
  open_qty: number;
  open_price: number;
  current_price: number;
  float_pnl: number;
  float_pnl_pct: number;
  opened_at: string;
}

interface DetailTrade {
  fill_id: string;
  ts_code: string;
  side: 'BUY' | 'SELL';
  price: number;
  qty: number;
  amount: number;
  fill_time: string;
  realized_pnl?: number | null;
  reason?: string | null;
}

interface DetailPerformance {
  total_replays: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  avg_pnl: number;
}

interface StrategyDetail {
  strategy_version_id: string;
  market: string;
  account: DetailAccount;
  positions: DetailPosition[];
  trades: DetailTrade[];
  performance: DetailPerformance;
}

function fmtAmount(value: number, symbol: string): string {
  return `${symbol}${(value ?? 0).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function dateOnly(v: string): string {
  return (v || '').slice(0, 10);
}

function timeShort(v: string): string {
  return (v || '').slice(0, 16).replace('T', ' ');
}

export default function StrategyDetailPage({ id: idProp }: { id?: string } = {}) {
  const { t } = useTranslation('pages');
  const { isViewer } = useRole();
  const { id: idParam = '' } = useParams();
  const id = idProp ?? idParam;
  const { market } = useMarket();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<StrategyDetail | null>(null);
  const [loading, setLoading] = useState(true);
  // 订阅观察（只读）：本人订阅了该平台策略时，额外展示平台统一运行的持仓/成交·信号。
  // 详情页 id 为 strategy_version_id，观察 API 需 strategy_id——用平台目录反查，
  // 再以订阅生效判定作为唯一授权门（后端端点同样校验订阅，前端仅决定是否渲染/拉取）。
  const [obsStrategyId, setObsStrategyId] = useState<string | null>(null);
  const [platPositions, setPlatPositions] = useState<PlatformPosition[]>([]);
  const [platTrades, setPlatTrades] = useState<PlatformTrade[]>([]);
  const [obsPosLoading, setObsPosLoading] = useState(false);
  const [obsTradesLoading, setObsTradesLoading] = useState(false);
  const [mirror, setMirror] = useState<MirrorAccountDetail | null>(null);
  const [mirrorOpen, setMirrorOpen] = useState(false);
  const [mirrorCapital, setMirrorCapital] = useState<number>(10000);
  const [mirrorBusy, setMirrorBusy] = useState(false);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true);
    api<StrategyDetail>(`/strategies/${id}/detail?market=${market}`)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch(() => {
        if (!cancelled) setDetail(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id, market]);

  // 反查平台 strategy_id 并判定订阅是否生效（目录/订阅任一失败→按未订阅处理，不渲染观察区）。
  useEffect(() => {
    if (!id) {
      setObsStrategyId(null);
      return;
    }
    let cancelled = false;
    Promise.all([listCatalog(market), listSubscriptions()])
      .then(([cat, subs]) => {
        if (cancelled) return;
        const strat = cat.find(
          (c) =>
            c.owner_user_id === null &&
            (c.strategy_id === id || c.versions.some((v) => v.strategy_version_id === id)),
        );
        const activeIds = new Set(subs.items.filter(isSubActive).map((s) => s.strategy_id));
        setObsStrategyId(strat && activeIds.has(strat.strategy_id) ? strat.strategy_id : null);
      })
      .catch(() => {
        if (!cancelled) setObsStrategyId(null);
      });
    return () => {
      cancelled = true;
    };
  }, [id, market]);

  // 订阅生效时拉我的模拟盘（404→未建账户，显示"开模拟盘"按钮）。
  useEffect(() => {
    if (!obsStrategyId) { setMirror(null); return; }
    const ac = new AbortController();
    getMirrorAccount(obsStrategyId, ac.signal).then(setMirror).catch(() => setMirror(null));
    return () => ac.abort();
  }, [obsStrategyId]);

  async function submitMirror() {
    if (!obsStrategyId) return;
    if (!mirrorCapital || mirrorCapital <= 0) { message.error(t('strategyDetail.mirrorCapitalRequired')); return; }
    setMirrorBusy(true);
    try {
      const created = await createMirrorAccount(obsStrategyId, mirrorCapital);
      message.success(t('strategyDetail.mirrorCreated'));
      setMirror(created); setMirrorOpen(false);
    } catch { /* api 层已 toast */ } finally { setMirrorBusy(false); }
  }

  // 订阅生效时拉平台持仓/成交（各自独立 loading + AbortController 防迟到，失败互不影响）。
  useEffect(() => {
    if (!obsStrategyId) {
      setPlatPositions([]);
      setPlatTrades([]);
      return;
    }
    const acPos = new AbortController();
    const acTrades = new AbortController();
    setObsPosLoading(true);
    getSubscriptionPositions(obsStrategyId, acPos.signal)
      .then(setPlatPositions)
      .catch(() => undefined)
      .finally(() => setObsPosLoading(false));
    setObsTradesLoading(true);
    getSubscriptionTrades(obsStrategyId, acTrades.signal)
      .then(setPlatTrades)
      .catch(() => undefined)
      .finally(() => setObsTradesLoading(false));
    return () => {
      acPos.abort();
      acTrades.abort();
    };
  }, [obsStrategyId]);

  const account = detail?.account;
  const marketTag = detail?.market || market;
  const marketLabel = t('market.' + marketTag, { ns: 'common' });
  const symbol = MARKET_META[market].short;

  const positionColumns: ColumnsType<DetailPosition> = [
    {
      title: t('strategyDetail.colSymbol'),
      dataIndex: 'ts_code',
      key: 'ts_code',
      render: (v: string) => <SymbolLink code={v} />,
    },
    {
      title: t('strategyDetail.colDirection'),
      dataIndex: 'direction',
      key: 'direction',
      render: (v: string) => (
        <Tag color={v === 'LONG' ? 'blue' : 'orange'}>{v === 'LONG' ? t('strategyDetail.dirLong') : t('strategyDetail.dirShort')}</Tag>
      ),
    },
    { title: t('strategyDetail.colQty'), dataIndex: 'open_qty', key: 'open_qty', align: 'right' },
    { title: t('strategyDetail.colOpenPrice'), dataIndex: 'open_price', key: 'open_price', align: 'right' },
    {
      title: t('strategyDetail.colCurrentPrice'),
      dataIndex: 'current_price',
      key: 'current_price',
      align: 'right',
      render: (v: number) => <b>{v}</b>,
    },
    {
      title: t('strategyDetail.colFloatPnl'),
      dataIndex: 'float_pnl',
      key: 'float_pnl',
      align: 'right',
      sorter: (a, b) => a.float_pnl - b.float_pnl,
      render: (v: number) => (
        <b style={{ color: pnlColor(v, market) }}>{fmtMoney(v)}</b>
      ),
    },
    {
      title: t('strategyDetail.colFloatPnlPct'),
      dataIndex: 'float_pnl_pct',
      key: 'float_pnl_pct',
      align: 'right',
      sorter: (a, b) => a.float_pnl_pct - b.float_pnl_pct,
      render: (v: number) => (
        <b style={{ color: pnlColor(v, market) }}>{fmtPct(v)}</b>
      ),
    },
    {
      title: t('strategyDetail.colOpenTime'),
      dataIndex: 'opened_at',
      key: 'opened_at',
      render: (v: string) => dateOnly(v),
    },
  ];

  const tradeColumns: ColumnsType<DetailTrade> = [
    {
      title: t('strategyDetail.colFillTime'),
      dataIndex: 'fill_time',
      key: 'fill_time',
      render: (v: string) => timeShort(v),
    },
    {
      title: t('strategyDetail.colSymbol'),
      dataIndex: 'ts_code',
      key: 'ts_code',
      render: (v: string) => <SymbolLink code={v} />,
    },
    {
      title: t('strategyDetail.colDirection'),
      dataIndex: 'side',
      key: 'side',
      render: (v: DetailTrade['side']) =>
        // A股语境：买入红、卖出绿
        v === 'BUY' ? <Tag color="red">{t('strategyDetail.buy')}</Tag> : <Tag color="green">{t('strategyDetail.sell')}</Tag>,
    },
    { title: t('strategyDetail.colFillPrice'), dataIndex: 'price', key: 'price', align: 'right' },
    { title: t('strategyDetail.colQty'), dataIndex: 'qty', key: 'qty', align: 'right' },
    {
      title: t('strategyDetail.colAmount'),
      dataIndex: 'amount',
      key: 'amount',
      align: 'right',
      render: (v: number) => fmtAmount(v, ''),
    },
    {
      title: t('strategyDetail.colPnl'),
      dataIndex: 'realized_pnl',
      key: 'realized_pnl',
      align: 'right',
      render: (v: number | null | undefined, row: DetailTrade) =>
        row.side === 'SELL' && v != null ? (
          <b style={{ color: pnlColor(v, market) }}>{fmtMoney(v)}</b>
        ) : (
          <span style={{ color: '#8B93A7' }}>—</span>
        ),
    },
    {
      title: t('strategyDetail.colReason'),
      dataIndex: 'reason',
      key: 'reason',
      width: 220,
      render: (v: string | null | undefined) =>
        v ? (
          <Tooltip title={v} placement="topLeft">
            <span
              style={{
                display: 'inline-block',
                maxWidth: 210,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                verticalAlign: 'bottom',
                fontSize: 12,
              }}
            >
              {v}
            </span>
          </Tooltip>
        ) : (
          <span style={{ color: '#8B93A7' }}>—</span>
        ),
    },
  ];

  // 平台持仓表（只读观察）：标的/方向/开仓价/数量/盈亏/状态。
  const platPositionColumns: ColumnsType<PlatformPosition> = [
    { title: t('strategyDetail.colSymbol'), dataIndex: 'ts_code', key: 'ts_code',
      render: (v: string) => <SymbolLink code={v} /> },
    { title: t('strategyDetail.colDirection'), dataIndex: 'direction', key: 'direction',
      render: (v: string) => (
        <Tag color={v === 'LONG' ? 'blue' : 'orange'}>
          {v === 'LONG' ? t('strategyDetail.dirLong') : t('strategyDetail.dirShort')}
        </Tag>
      ) },
    { title: t('strategyDetail.colOpenPrice'), dataIndex: 'open_price', key: 'open_price', align: 'right' },
    { title: t('strategyDetail.colQty'), dataIndex: 'open_qty', key: 'open_qty', align: 'right' },
    { title: t('strategyDetail.colPnl'), dataIndex: 'pnl_amount', key: 'pnl_amount', align: 'right',
      render: (v: number | null) =>
        v != null ? <b style={{ color: pnlColor(v, market) }}>{fmtMoney(v)}</b> : <span style={{ color: '#8B93A7' }}>—</span> },
    { title: t('strategyDetail.colStatus'), dataIndex: 'status', key: 'status',
      render: (v: string) => <Tag>{v}</Tag> },
  ];

  // 平台成交·信号表（只读观察）：成交时间/标的/价格/数量/盈亏/信号（entry 入场 · exit 出场）。
  const platTradeColumns: ColumnsType<PlatformTrade> = [
    { title: t('strategyDetail.colFillTime'), dataIndex: 'fill_time', key: 'fill_time',
      render: (v: string) => timeShort(v) },
    { title: t('strategyDetail.colSymbol'), dataIndex: 'ts_code', key: 'ts_code',
      render: (v: string) => <SymbolLink code={v} /> },
    { title: t('strategyDetail.colFillPrice'), dataIndex: 'fill_price', key: 'fill_price', align: 'right' },
    { title: t('strategyDetail.colQty'), dataIndex: 'fill_qty', key: 'fill_qty', align: 'right' },
    { title: t('strategyDetail.colPnl'), dataIndex: 'realized_pnl', key: 'realized_pnl', align: 'right',
      render: (v: number | null) =>
        v != null ? <b style={{ color: pnlColor(v, market) }}>{fmtMoney(v)}</b> : <span style={{ color: '#8B93A7' }}>—</span> },
    { title: t('strategyDetail.colSignal'), dataIndex: 'signal', key: 'signal',
      render: (v: PlatformTrade['signal']) =>
        v === 'entry'
          ? <Tag color="blue">{t('strategyDetail.signalEntry')}</Tag>
          : <Tag color="orange">{t('strategyDetail.signalExit')}</Tag> },
  ];

  const perf = detail?.performance;

  return (
    <div>
      <Space align="center" style={{ marginBottom: 16 }}>
        <Button
          icon={<ArrowLeftOutlined />}
          size="small"
          onClick={() => navigate('/strategies')}
        />
        <Title level={4} style={{ margin: 0, fontFamily: 'monospace' }}>
          {id}
        </Title>
        <Tag color="blue">{marketLabel}</Tag>
      </Space>

      <Spin spinning={loading}>
        {/* KPI 行 */}
        <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
          <Col xs={24} sm={12} md={8} lg={5}>
            <Card size="small">
              <Statistic
                title={t('strategyDetail.kpiTotalEquity')}
                value={account?.total_equity ?? 0}
                precision={2}
                prefix={symbol}
                styles={{ content: { fontSize: 20 } }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={8} lg={6}>
            <Card size="small">
              <Statistic
                title={t('strategyDetail.kpiFloatPnl')}
                value={account?.unrealized_pnl ?? 0}
                precision={2}
                prefix={symbol}
                suffix={
                  account ? ` (${fmtPct(account.unrealized_pnl_pct)})` : undefined
                }
                styles={{ content: {
                  fontSize: 20,
                  color: account
                    ? pnlColor(account.unrealized_pnl, market)
                    : undefined,
                } }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={8} lg={5}>
            <Card size="small">
              <Statistic
                title={t('strategyDetail.kpiAvailCash')}
                value={account?.available_cash ?? 0}
                precision={2}
                prefix={symbol}
                styles={{ content: { fontSize: 20 } }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={8} lg={5}>
            <Card size="small">
              <Statistic
                title={t('strategyDetail.kpiPositionRatio')}
                value={Math.round(account?.position_ratio ?? 0)}
                suffix="%"
                styles={{ content: { fontSize: 20 } }}
              />
              <Progress
                percent={Math.round(account?.position_ratio ?? 0)}
                size="small"
                showInfo={false}
                style={{ marginTop: 8 }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={8} lg={3}>
            <Card size="small">
              <Statistic
                title={t('strategyDetail.kpiPositionCount')}
                value={account?.position_count ?? 0}
                suffix={t('strategyDetail.unitStock')}
                styles={{ content: { fontSize: 20 } }}
              />
            </Card>
          </Col>
        </Row>

        <Tabs
          defaultActiveKey={isViewer ? 'performance' : 'positions'}
          items={[
            {
              key: 'positions',
              label: t('strategyDetail.tabPositions'),
              children: (
                <Table<DetailPosition>
                  columns={positionColumns}
                  dataSource={detail?.positions ?? []}
                  rowKey="position_id"
                  pagination={{ pageSize: 20, showSizeChanger: true }}
                  size="small"
                  locale={{ emptyText: t('strategyDetail.emptyPositions') }}
                />
              ),
            },
            {
              key: 'trades',
              label: t('strategyDetail.tabTrades'),
              children: (
                <Table<DetailTrade>
                  columns={tradeColumns}
                  dataSource={detail?.trades ?? []}
                  rowKey="fill_id"
                  pagination={{ pageSize: 20, showSizeChanger: true }}
                  size="small"
                  locale={{ emptyText: t('strategyDetail.emptyTrades') }}
                />
              ),
            },
            {
              key: 'performance',
              label: t('strategyDetail.tabPerformance'),
              children: (
                <Row gutter={[16, 16]}>
                  <Col xs={12} md={8} lg={4}>
                    <Card size="small">
                      <Statistic
                        title={t('strategyDetail.perfReplays')}
                        value={perf?.total_replays ?? 0}
                        suffix={t('strategyDetail.unitTrade')}
                      />
                    </Card>
                  </Col>
                  <Col xs={12} md={8} lg={4}>
                    <Card size="small">
                      <Statistic
                        title={t('strategyDetail.perfWinRate')}
                        value={((perf?.win_rate ?? 0) * 100).toFixed(2)}
                        suffix="%"
                      />
                    </Card>
                  </Col>
                  <Col xs={12} md={8} lg={4}>
                    <Card size="small">
                      <Statistic
                        title={t('strategyDetail.perfWins')}
                        value={perf?.wins ?? 0}
                        suffix={t('strategyDetail.unitTrade')}
                        styles={{ content: { color: pnlColor(1, market) } }}
                      />
                    </Card>
                  </Col>
                  <Col xs={12} md={8} lg={4}>
                    <Card size="small">
                      <Statistic
                        title={t('strategyDetail.perfLosses')}
                        value={perf?.losses ?? 0}
                        suffix={t('strategyDetail.unitTrade')}
                        styles={{ content: { color: pnlColor(-1, market) } }}
                      />
                    </Card>
                  </Col>
                  <Col xs={12} md={8} lg={4}>
                    <Card size="small">
                      <Statistic
                        title={t('strategyDetail.perfTotalPnl')}
                        value={perf?.total_pnl ?? 0}
                        precision={2}
                        styles={{ content: {
                          color: pnlColor(perf?.total_pnl ?? 0, market),
                        } }}
                      />
                    </Card>
                  </Col>
                  <Col xs={12} md={8} lg={4}>
                    <Card size="small">
                      <Statistic
                        title={t('strategyDetail.perfAvgPnl')}
                        value={perf?.avg_pnl ?? 0}
                        precision={2}
                        styles={{ content: {
                          color: pnlColor(perf?.avg_pnl ?? 0, market),
                        } }}
                      />
                    </Card>
                  </Col>
                </Row>
              ),
            },
            {
              key: 'account',
              label: t('strategyDetail.tabAccount'),
              children: (
                <Card size="small">
                  <Descriptions
                    bordered
                    column={{ xs: 1, sm: 2, md: 3 }}
                    size="small"
                  >
                    <Descriptions.Item label={t('strategyDetail.acctInitial')}>
                      {fmtAmount(account?.initial_capital ?? 0, symbol)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t('strategyDetail.acctTotalEquity')}>
                      <b>{fmtAmount(account?.total_equity ?? 0, symbol)}</b>
                    </Descriptions.Item>
                    <Descriptions.Item label={t('strategyDetail.acctAvailCash')}>
                      {fmtAmount(account?.available_cash ?? 0, symbol)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t('strategyDetail.acctMarketValue')}>
                      {fmtAmount(account?.market_value ?? 0, symbol)}
                    </Descriptions.Item>
                    <Descriptions.Item label={t('strategyDetail.acctFloatPnl')}>
                      <span
                        style={{
                          color: pnlColor(account?.unrealized_pnl ?? 0, market),
                        }}
                      >
                        {fmtMoney(account?.unrealized_pnl ?? 0)}（
                        {fmtPct(account?.unrealized_pnl_pct ?? 0)}）
                      </span>
                    </Descriptions.Item>
                    <Descriptions.Item label={t('strategyDetail.acctPositionRatio')}>
                      <Progress
                        percent={Math.round(account?.position_ratio ?? 0)}
                        size="small"
                        style={{ maxWidth: 200 }}
                        strokeColor={
                          (account?.position_ratio ?? 0) > 80
                            ? '#ff4d4f'
                            : (account?.position_ratio ?? 0) > 50
                              ? '#faad14'
                              : '#52c41a'
                        }
                        format={(p) => `${p}%`}
                      />
                    </Descriptions.Item>
                  </Descriptions>
                </Card>
              ),
            },
          ].filter(i => !isViewer || !['positions', 'trades'].includes(i.key))}
        />

        {/* 订阅观察区：仅本人订阅该平台策略时渲染（只读，展示平台统一运行产出）。 */}
        {obsStrategyId && (
          <div style={{ marginTop: 24 }}>
            <Card size="small" title={t('strategyDetail.secPlatformPositions')} style={{ marginBottom: 16 }}>
              <Table<PlatformPosition>
                columns={platPositionColumns}
                dataSource={platPositions}
                rowKey="position_id"
                loading={obsPosLoading}
                size="small"
                pagination={{ pageSize: 10, showSizeChanger: true }}
                locale={{ emptyText: t('strategyDetail.emptyPositions') }}
              />
            </Card>
            <Card size="small" title={t('strategyDetail.secPlatformTrades')}>
              <Table<PlatformTrade>
                columns={platTradeColumns}
                dataSource={platTrades}
                rowKey="fill_id"
                loading={obsTradesLoading}
                size="small"
                pagination={{ pageSize: 10, showSizeChanger: true }}
                locale={{ emptyText: t('strategyDetail.emptyTrades') }}
              />
            </Card>
            <Card size="small" title={t('strategyDetail.mirrorTitle')} style={{ marginTop: 16 }}
              extra={!mirror ? (
                <Button type="primary" size="small" onClick={() => setMirrorOpen(true)}>
                  {t('strategyDetail.mirrorOpen')}
                </Button>) : null}>
              {!mirror ? (
                <Typography.Text type="secondary">{t('strategyDetail.mirrorEmpty')}</Typography.Text>
              ) : (
                <>
                  {mirror.status === 'frozen' && (
                    <Typography.Text type="warning">{t('strategyDetail.mirrorFrozen')}</Typography.Text>)}
                  <Space size={24} style={{ margin: '8px 0' }} wrap>
                    <span>{t('strategyDetail.mirrorEquity')}: {(mirror.equity ?? 0).toFixed(2)}</span>
                    <span>{t('strategyDetail.mirrorCash')}: {Number(mirror.cash_balance ?? 0).toFixed(2)}</span>
                    <span>{t('strategyDetail.mirrorPnl')}: {(mirror.total_pnl ?? 0).toFixed(2)}</span>
                  </Space>
                  <Table<MirrorPosition> size="small" rowKey="symbol" dataSource={mirror.positions}
                    pagination={false} locale={{ emptyText: t('strategyDetail.mirrorEmpty') }}
                    columns={[
                      { title: t('strategyDetail.mirrorColSymbol'), dataIndex: 'symbol' },
                      { title: t('strategyDetail.mirrorColQty'), dataIndex: 'quantity' },
                      { title: t('strategyDetail.mirrorColAvg'), dataIndex: 'avg_cost' },
                      { title: t('strategyDetail.mirrorColPrice'), dataIndex: 'current_price' },
                      { title: t('strategyDetail.mirrorColMv'), dataIndex: 'market_value' },
                      { title: t('strategyDetail.mirrorColUpnl'), dataIndex: 'unrealized_pnl' },
                    ]} />
                </>
              )}
            </Card>
            <Modal title={t('strategyDetail.mirrorOpen')} open={mirrorOpen} confirmLoading={mirrorBusy}
              onOk={submitMirror} onCancel={() => setMirrorOpen(false)}>
              <span>{t('strategyDetail.mirrorCapital')}：</span>
              <InputNumber min={1} value={mirrorCapital} onChange={(v) => setMirrorCapital(Number(v) || 0)}
                style={{ width: 200 }} />
            </Modal>
          </div>
        )}
      </Spin>
    </div>
  );
}
