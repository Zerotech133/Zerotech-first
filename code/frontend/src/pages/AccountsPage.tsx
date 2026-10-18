import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Card,
  Col,
  Collapse,
  Progress,
  Row,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnType, ColumnsType } from 'antd/es/table';
import {
  fmtPct,
  pnlColor,
  useMarket,
} from '../contexts/MarketContext';
import { api } from '../services/api';
import { strategyLabel } from '../i18n/strategyName';

const { Title } = Typography;

/** 6 个归因维度（与后端 ATTRIBUTION_DIMS 同口径） */
const ATTRIBUTION_DIMS = ['SELECTION', 'ENTRY', 'EXIT', 'MARKET', 'RISK', 'BEHAVIOR'] as const;

/** 归因维度 i18n key（pages 命名空间，key 与后端 ATTRIBUTION_DIMS 同口径，仅用于展示） */
const ATTRIBUTION_DIM_LABELS: Record<(typeof ATTRIBUTION_DIMS)[number], string> = {
  SELECTION: 'accounts.dimSelection',
  ENTRY: 'accounts.dimEntry',
  EXIT: 'accounts.dimExit',
  MARKET: 'accounts.dimMarket',
  RISK: 'accounts.dimRisk',
  BEHAVIOR: 'accounts.dimBehavior',
};

interface AccountSummary {
  total_equity: number;
  available_cash: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  position_ratio: number;
  position_count: number;
  account_count: number;
}

interface StrategyAccount {
  strategy_version_id: string;
  initial_capital: number;
  available_cash: number;
  market_value: number;
  total_equity: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  position_ratio: number;
  position_count: number;
}

/** /accounts-ext/risk-summary 返回契约 */
interface RiskSummaryRow {
  strategy_version_id: string;
  strategy_name: string;
  position_ratio: number;
  max_single_weight: number;
  today_pnl: number;
  daily_loss_limit_pct: number;
  breach_daily_loss: boolean;
}

/** /accounts-ext/attribution 返回契约 */
interface AttributionRow {
  strategy_version_id: string;
  strategy_name: string;
  dimensions: Record<string, number>;
  total_pnl: number;
}

function fmtAmount(value: number, symbol: string): string {
  return `${symbol}${value.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export default function AccountsPage() {
  const { t } = useTranslation('pages');
  const { market, currencySymbol } = useMarket();
  const [summary, setSummary] = useState<AccountSummary | null>(null);
  const [accounts, setAccounts] = useState<StrategyAccount[]>([]);
  const [riskRows, setRiskRows] = useState<RiskSummaryRow[]>([]);
  const [attribution, setAttribution] = useState<AttributionRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      api<AccountSummary>(`/accounts/stats?market=${market}`).catch(() => null),
      api<StrategyAccount[]>(`/accounts/stats/by-strategy?market=${market}`).catch(
        () => [],
      ),
      api<RiskSummaryRow[]>(`/accounts-ext/risk-summary?market=${market}`).catch(
        () => [],
      ),
      api<AttributionRow[]>(`/accounts-ext/attribution?market=${market}`).catch(
        () => [],
      ),
    ]).then(([s, a, r, attr]) => {
      setSummary(s);
      setAccounts(a || []);
      setRiskRows(Array.isArray(r) ? r : []);
      setAttribution(Array.isArray(attr) ? attr : []);
      setLoading(false);
    });
  }, [market]);

  const columns: ColumnsType<StrategyAccount> = [
    {
      title: t('accounts.colStrategyVersion'),
      dataIndex: 'strategy_version_id',
      key: 'strategy_version_id',
      render: (id: string) => (
        <Tag color="blue" style={{ fontFamily: 'monospace' }}>
          {id}
        </Tag>
      ),
    },
    {
      title: t('accounts.colInitialCapital'),
      dataIndex: 'initial_capital',
      key: 'initial_capital',
      align: 'right',
      render: (v: number) => fmtAmount(v, currencySymbol),
    },
    {
      title: t('accounts.colTotalEquity'),
      dataIndex: 'total_equity',
      key: 'total_equity',
      align: 'right',
      render: (v: number) => (
        <strong>{fmtAmount(v, currencySymbol)}</strong>
      ),
    },
    {
      title: t('accounts.colAvailCash'),
      dataIndex: 'available_cash',
      key: 'available_cash',
      align: 'right',
      render: (v: number) => fmtAmount(v, currencySymbol),
    },
    {
      title: t('accounts.colMarketValue'),
      dataIndex: 'market_value',
      key: 'market_value',
      align: 'right',
      render: (v: number) => fmtAmount(v, currencySymbol),
    },
    {
      title: t('accounts.colFloatPnl'),
      key: 'pnl',
      align: 'right',
      render: (_: unknown, row: StrategyAccount) => {
        const color = pnlColor(row.unrealized_pnl, market);
        return (
          <span style={{ color }}>
            {fmtAmount(row.unrealized_pnl, currencySymbol)}
            <br />
            <small>{fmtPct(row.unrealized_pnl_pct)}</small>
          </span>
        );
      },
    },
    {
      title: t('accounts.colPositionRatio'),
      dataIndex: 'position_ratio',
      key: 'position_ratio',
      width: 140,
      render: (v: number) => {
        const pct = Math.round(v);
        const strokeColor =
          pct > 80 ? '#ff4d4f' : pct > 50 ? '#faad14' : '#52c41a';
        return (
          <Progress
            percent={pct}
            size="small"
            strokeColor={strokeColor}
            format={(p) => `${p}%`}
          />
        );
      },
    },
    {
      title: t('accounts.colPositionCount'),
      dataIndex: 'position_count',
      key: 'position_count',
      align: 'center',
      render: (v: number) => <Tag>{v}</Tag>,
    },
  ];

  const riskColumns: ColumnsType<RiskSummaryRow> = [
    {
      title: t('accounts.colStrategy'),
      key: 'strategy',
      render: (_, row) => (
        <Space size={6}>
          <span>{strategyLabel(row.strategy_name)}</span>
          <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#8B93A7' }}>
            {row.strategy_version_id}
          </span>
        </Space>
      ),
    },
    {
      title: t('accounts.colPositionPct'),
      dataIndex: 'position_ratio',
      key: 'position_ratio',
      width: 140,
      render: (v: number) => {
        const pct = Math.round(v);
        return (
          <Progress
            percent={pct}
            size="small"
            strokeColor={pct > 80 ? '#ff4d4f' : pct > 50 ? '#faad14' : '#52c41a'}
            format={(p) => `${p}%`}
          />
        );
      },
    },
    {
      title: t('accounts.colMaxSingleWeight'),
      dataIndex: 'max_single_weight',
      key: 'max_single_weight',
      align: 'right',
      render: (v: number) => (
        <span style={{ color: v > 20 ? '#ff4d4f' : undefined }}>{v.toFixed(2)}%</span>
      ),
    },
    {
      title: t('accounts.colTodayPnl'),
      dataIndex: 'today_pnl',
      key: 'today_pnl',
      align: 'right',
      render: (v: number, row) => (
        <span style={{ color: pnlColor(v, market) }}>
          {fmtAmount(v, currencySymbol)}
        </span>
      ),
    },
    {
      title: t('accounts.colDailyRedline'),
      key: 'breach',
      align: 'center',
      render: (_, row) => (
        <Tag color={row.breach_daily_loss ? 'red' : 'green'}>
          {row.breach_daily_loss
            ? t('accounts.breachOver', { pct: row.daily_loss_limit_pct })
            : t('accounts.breachUnder', { pct: row.daily_loss_limit_pct })}
        </Tag>
      ),
    },
  ];

  const attributionColumns: ColumnsType<AttributionRow> = [
    {
      title: t('accounts.colStrategy'),
      key: 'strategy',
      render: (_, row) => (
        <Space size={6}>
          <span>{strategyLabel(row.strategy_name)}</span>
          <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#8B93A7' }}>
            {row.strategy_version_id}
          </span>
        </Space>
      ),
    },
    ...ATTRIBUTION_DIMS.map<ColumnType<AttributionRow>>((dim) => ({
      title: t(ATTRIBUTION_DIM_LABELS[dim]),
      key: dim,
      align: 'right',
      render: (_, row) => {
        const v = row.dimensions[dim] ?? 0;
        return (
          <span style={{ color: pnlColor(v, market) }}>
            {fmtAmount(v, currencySymbol)}
          </span>
        );
      },
    })),
    {
      title: t('accounts.colTotal'),
      dataIndex: 'total_pnl',
      key: 'total_pnl',
      align: 'right',
      render: (v: number) => (
        <strong style={{ color: pnlColor(v, market) }}>
          {fmtAmount(v, currencySymbol)}
        </strong>
      ),
    },
  ];

  return (
    <div style={{ padding: '24px' }}>
      <Title level={4} style={{ marginBottom: 24 }}>
        {t('accounts.title', { market: t(`common:market.${market}`) })}
      </Title>

      <Spin spinning={loading}>
        {/* KPI 汇总 */}
        <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
          <Col xs={24} sm={12} md={8} lg={4}>
            <Card size="small">
              <Statistic
                title={t('accounts.statTotalEquity')}
                value={summary?.total_equity ?? 0}
                precision={2}
                prefix={currencySymbol}
                styles={{ content: { fontSize: 20 } }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={8} lg={6}>
            <Card size="small">
              <Statistic
                title={t('accounts.statFloatPnl')}
                value={summary?.unrealized_pnl ?? 0}
                precision={2}
                prefix={currencySymbol}
                suffix={
                  summary
                    ? ` (${fmtPct(summary.unrealized_pnl_pct)})`
                    : undefined
                }
                styles={{ content: {
                  fontSize: 20,
                  color: summary
                    ? pnlColor(summary.unrealized_pnl, market)
                    : undefined,
                } }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={8} lg={5}>
            <Card size="small">
              <Statistic
                title={t('accounts.statAvailCash')}
                value={summary?.available_cash ?? 0}
                precision={2}
                prefix={currencySymbol}
                styles={{ content: { fontSize: 20 } }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={8} lg={5}>
            <Card size="small">
              <Statistic
                title={t('accounts.statPositionRatio')}
                value={Math.round(summary?.position_ratio ?? 0)}
                suffix="%"
                styles={{ content: { fontSize: 20 } }}
              />
              <Progress
                percent={Math.round(summary?.position_ratio ?? 0)}
                size="small"
                showInfo={false}
                style={{ marginTop: 8 }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={8} lg={4}>
            <Card size="small">
              <Statistic
                title={t('accounts.statAccountsHoldings')}
                value={summary?.account_count ?? 0}
                suffix={t('accounts.accountsHoldingsSuffix', { count: summary?.position_count ?? 0 })}
                styles={{ content: { fontSize: 20 } }}
              />
            </Card>
          </Col>
        </Row>

        {/* 分策略虚拟账户表格 */}
        <Card title={t('accounts.byStrategyTitle')} size="small">
          <Table<StrategyAccount>
            columns={columns}
            dataSource={accounts}
            rowKey="strategy_version_id"
            pagination={false}
            size="middle"
            locale={{ emptyText: t('accounts.emptyAccounts') }}
            scroll={{ x: 900 }}
          />
        </Card>

        {/* 风险摘要 */}
        <Card title={t('accounts.riskSummaryTitle')} size="small" style={{ marginTop: 16 }}>
          <Table<RiskSummaryRow>
            columns={riskColumns}
            dataSource={riskRows}
            rowKey="strategy_version_id"
            pagination={false}
            size="middle"
            locale={{ emptyText: t('accounts.emptyRisk') }}
          />
        </Card>

        {/* 账户归因（可折叠） */}
        <Collapse
          style={{ marginTop: 16 }}
          items={[
            {
              key: 'attribution',
              label: t('accounts.attributionTitle'),
              children: (
                <Table<AttributionRow>
                  columns={attributionColumns}
                  dataSource={attribution}
                  rowKey="strategy_version_id"
                  pagination={false}
                  size="small"
                  locale={{ emptyText: t('accounts.emptyAttribution') }}
                  scroll={{ x: 800 }}
                />
              ),
            },
          ]}
        />
      </Spin>
    </div>
  );
}
