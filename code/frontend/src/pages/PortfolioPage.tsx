import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Card,
  Col,
  Progress,
  Row,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useAutoRefresh } from '../hooks/useAutoRefresh';
import {
  MARKETS,
  MARKET_META,
  fmtPct,
  pnlColor,
  type Market,
} from '../contexts/MarketContext';
import { api } from '../services/api';

const { Title, Text } = Typography;

// ── /portfolio/cross-market 返回契约（跨市场 CNY 归一） ──
interface MarketSummary {
  market: Market;
  currency: string;
  total_equity: number;
  available_cash: number;
  market_value: number;
  unrealized_pnl: number;
  today_pnl: number;
  total_pnl: number;
  position_count: number;
  account_count: number;
  equity_cny: number;
  pnl_cny: number;
}
interface CrossMarketData {
  markets: MarketSummary[];
  total_cny: number;
  total_pnl_cny: number;
  fx_rates: Record<string, number>;
}

// ── /accounts/stats/by-strategy 返回契约 ──
interface StrategyAccount {
  strategy_version_id: string;
  strategy_name: string;
  market: Market;
  initial_capital: number;
  available_cash: number;
  market_value: number;
  total_equity: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  today_pnl: number;
  total_pnl: number;
  position_ratio: number;
  position_count: number;
}

const MARKET_TAG_COLOR: Record<Market, string> = {
  CN: 'red',
  US: 'blue',
  HK: 'orange',
  CRYPTO: 'purple',
};

const CURRENCY_SYMBOL: Record<string, string> = {
  CNY: '¥',
  USD: '$',
  HKD: 'HK$',
  USDT: '₮',
};

function fmtCNY(value: number): string {
  return `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
}

function fmtNative(value: number, currency: string): string {
  const sym = CURRENCY_SYMBOL[currency] ?? '';
  const sign = value >= 0 ? '' : '-';
  return `${sign}${sym}${Math.abs(value).toLocaleString('zh-CN', {
    maximumFractionDigits: 0,
  })}`;
}

export default function PortfolioPage() {
  const { t } = useTranslation('pages');
  const [data, setData] = useState<CrossMarketData | null>(null);
  const [accounts, setAccounts] = useState<StrategyAccount[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    Promise.all([
      api<CrossMarketData>('/portfolio/cross-market').catch(() => null),
      Promise.all(
        MARKETS.map((m) =>
          api<StrategyAccount[]>(`/accounts/stats/by-strategy?market=${m}`).catch(
            () => [] as StrategyAccount[],
          ),
        ),
      ),
    ]).then(([d, perMarket]) => {
      if (d) setData(d);
      setAccounts(perMarket.flat());
      setLoading(false);
    });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // 30s 自动刷新（页面不可见时暂停）
  useAutoRefresh(load, 30_000, [load]);

  if (!data) {
    return (
      <div style={{ padding: '24px', color: '#8B93A7' }}>
        <Spin size="small" style={{ marginRight: 8 }} />
        {loading ? t('portfolio.loading') : t('portfolio.noData')}
      </div>
    );
  }

  const covered = data.markets.filter((m) => m.account_count > 0);

  const columns: ColumnsType<StrategyAccount> = [
    {
      title: t('portfolio.colStrategy'),
      dataIndex: 'strategy_name',
      key: 'strategy_name',
      render: (name: string, row) => (
        <span>
          <span style={{ fontWeight: 600 }}>{name}</span>
          <br />
          <Text type="secondary" style={{ fontSize: 11, fontFamily: 'monospace' }}>
            {row.strategy_version_id}
          </Text>
        </span>
      ),
    },
    {
      title: t('portfolio.colMarket'),
      dataIndex: 'market',
      key: 'market',
      width: 110,
      render: (m: Market) => (
        <Tag color={MARKET_TAG_COLOR[m]}>
          {t(`common:market.${m}`)} {MARKET_META[m].currency}
        </Tag>
      ),
    },
    {
      title: t('portfolio.colEquity'),
      dataIndex: 'total_equity',
      key: 'total_equity',
      align: 'right',
      render: (v: number, row) => (
        <strong>{fmtNative(v, MARKET_META[row.market].currency)}</strong>
      ),
    },
    {
      title: t('portfolio.colFloatPnl'),
      key: 'unrealized_pnl',
      align: 'right',
      render: (_: unknown, row: StrategyAccount) => (
        <span style={{ color: pnlColor(row.unrealized_pnl, row.market) }}>
          {fmtNative(row.unrealized_pnl, MARKET_META[row.market].currency)}
          <br />
          <small>{fmtPct(row.unrealized_pnl_pct)}</small>
        </span>
      ),
    },
    {
      title: t('portfolio.colToday'),
      dataIndex: 'today_pnl',
      key: 'today_pnl',
      align: 'right',
      render: (v: number, row) => (
        <span style={{ color: pnlColor(v, row.market) }}>
          {fmtNative(v, MARKET_META[row.market].currency)}
        </span>
      ),
    },
    {
      title: t('portfolio.colTotalPnl'),
      dataIndex: 'total_pnl',
      key: 'total_pnl',
      align: 'right',
      render: (v: number, row) => (
        <span style={{ color: pnlColor(v, row.market) }}>
          {fmtNative(v, MARKET_META[row.market].currency)}
        </span>
      ),
    },
    {
      title: t('portfolio.colPosition'),
      dataIndex: 'position_ratio',
      key: 'position_ratio',
      width: 150,
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
      title: t('portfolio.colHolding'),
      dataIndex: 'position_count',
      key: 'position_count',
      align: 'center',
      width: 70,
      render: (v: number) => <Tag>{v}</Tag>,
    },
  ];

  return (
    <div style={{ padding: '24px' }}>
      <Title level={4} style={{ marginBottom: 4 }}>
        {t('portfolio.title')}
      </Title>
      <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 20 }}>
        {t('portfolio.subtitle')}
      </Text>

      {/* ── 合计卡（CNY） ───────────────────────── */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={12} md={8}>
          <Card size="small">
            <Statistic
              title={t('portfolio.totalAssetsCny')}
              value={data.total_cny}
              precision={2}
              prefix="¥"
              styles={{ content: { fontSize: 24, fontWeight: 700 } }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8}>
          <Card size="small">
            <Statistic
              title={t('portfolio.totalPnlCny')}
              value={Math.abs(data.total_pnl_cny)}
              precision={2}
              prefix={data.total_pnl_cny >= 0 ? '+¥' : '-¥'}
              styles={{ content: {
                fontSize: 24,
                fontWeight: 700,
                color:
                  data.total_pnl_cny > 0
                    ? '#F0524D'
                    : data.total_pnl_cny < 0
                      ? '#12A150'
                      : undefined,
              } }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8}>
          <Card size="small">
            <Statistic
              title={t('portfolio.coveredMarkets')}
              value={covered.length}
              suffix={`/ ${data.markets.length}`}
              styles={{ content: { fontSize: 24, fontWeight: 700 } }}
            />
            <div style={{ marginTop: 4 }}>
              {data.markets.map((m) => (
                <Tag
                  key={m.market}
                  color={
                    m.account_count > 0
                      ? MARKET_TAG_COLOR[m.market]
                      : 'default'
                  }
                  style={{ marginTop: 4 }}
                >
                  {t(`common:market.${m.market}`)}
                </Tag>
              ))}
            </div>
          </Card>
        </Col>
      </Row>

      {/* ── 四市场卡片行 ───────────────────────── */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        {data.markets.map((m) => {
          const sym = MARKET_META[m.market].currency;
          return (
            <Col key={m.market} xs={24} sm={12} md={6}>
              <Card
                size="small"
                title={
                  <span>
                    <Tag
                      color={MARKET_TAG_COLOR[m.market]}
                      style={{ marginRight: 6 }}
                    >
                      {t(`common:market.${m.market}`)}
                    </Tag>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {sym}
                    </Text>
                  </span>
                }
              >
                <Statistic
                  title={t('portfolio.totalEquity')}
                  value={m.total_equity}
                  precision={0}
                  prefix={CURRENCY_SYMBOL[sym] ?? ''}
                  styles={{ content: { fontSize: 20, fontWeight: 700 } }}
                />
                <div
                  style={{
                    fontSize: 15,
                    fontWeight: 600,
                    margin: '8px 0 4px',
                    color: pnlColor(m.total_pnl, m.market),
                  }}
                >
                  {t('portfolio.pnlPrefix')} {fmtNative(m.total_pnl, sym)}
                </div>
                <Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
                  {t('portfolio.holdingAccount', { positions: m.position_count, accounts: m.account_count })}
                </Text>
                <div style={{ marginTop: 8, fontSize: 12, color: '#8B93A7' }}>
                  ≈ {fmtCNY(m.equity_cny)}
                </div>
              </Card>
            </Col>
          );
        })}
      </Row>

      {/* ── 分策略账户表（全市场合并） ───────────────── */}
      <Card title={t('portfolio.byStrategyTitle')} size="small" style={{ marginBottom: 16 }}>
        <Table<StrategyAccount>
          columns={columns}
          dataSource={accounts}
          rowKey={(r) => `${r.market}:${r.strategy_version_id}`}
          pagination={false}
          size="middle"
          locale={{ emptyText: t('portfolio.emptyAccounts') }}
          scroll={{ x: 900 }}
        />
      </Card>

      {/* ── 汇率说明脚注 ───────────────────────── */}
      <div
        style={{
          color: '#8B93A7',
          fontSize: 12,
          background: '#F8F9FC',
          borderRadius: 8,
          padding: '12px 16px',
        }}
      >
        {t('portfolio.fxNotePrefix')}
        {Object.entries(data.fx_rates)
          .filter(([c]) => c !== 'CNY')
          .map(([c, r]) => t('portfolio.fxRateItem', { currency: c, rate: r }))
          .join('')}
        {t('portfolio.fxNoteSuffix')}
      </div>
    </div>
  );
}
