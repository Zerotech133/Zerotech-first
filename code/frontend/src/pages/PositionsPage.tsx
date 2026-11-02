import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button,
  Card,
  Col,
  Empty,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { LeftOutlined, ReloadOutlined, RightOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { api } from '../services/api';
import { MARKET_META, fmtMoney, fmtPct, pnlColor, useMarket } from '../contexts/MarketContext';
import SymbolLink from '../components/SymbolLink';
import { useAutoRefresh } from '../hooks/useAutoRefresh';
import { useMarketQuoteSocket } from '../hooks/useMarketQuoteSocket';
import { strategyLabel } from '../i18n/strategyName';

const { Title, Text } = Typography;

/* ───────────────────────── 类型（后端契约） ───────────────────────── */

/** /accounts/stats 与 /accounts/stats/by-strategy 单项（合并版无 strategy_version_id） */
interface AccountStat {
  strategy_version_id?: string;
  strategy_name?: string;
  initial_capital?: number;
  available_cash?: number;
  market_value?: number;
  total_equity?: number;
  unrealized_pnl?: number;
  unrealized_pnl_pct?: number;
  today_pnl?: number;
  total_pnl?: number;
  total_pnl_pct?: number;
  realized_pnl?: number;
  realized_pnl_pct?: number;
  position_ratio?: number;
  position_count?: number;
}

/** /positions?market=X[&strategy=Y] 单项 */
interface PositionRow {
  position_id: string;
  strategy_version_id: string;
  strategy_name?: string;
  market: string;
  ts_code: string;
  direction: string;
  open_qty?: number | string;
  available_qty?: number | string;
  open_price?: number | string;
  current_price?: number | string;
  cost_amount?: number | string;
  market_value?: number | string;
  float_pnl?: number | string;
  float_pnl_pct?: number | string;
  today_pnl?: number | string;
  weight_pct?: number | string;
  status?: string;
  opened_at?: string;
}

/** /strategies/{id}/detail 的 trades 单项 */
interface TradeRow {
  fill_id: string;
  ts_code: string;
  side: string;
  price?: number | string;
  qty?: number | string;
  amount?: number | string;
  fill_time: string;
  realized_pnl?: number | null;
  reason?: string | null;
}

interface StrategyDetailResp {
  trades?: TradeRow[];
}

interface DayPnl {
  day: number;
  pnl: number;
  count: number;
  wins: number;
  losses: number;
}

interface PnlCalendarResp {
  market: string;
  year: number | null;
  month: number | null;
  days: DayPnl[];
  summary: {
    total_pnl: number;
    win_days: number;
    loss_days: number;
    win_rate: number;
  };
}

/* ───────────────────────── 格式化工具 ───────────────────────── */

/** 后端数值可能以 string/number 下发，统一转 number */
function num(v: unknown): number {
  const n = typeof v === 'string' ? parseFloat(v) : typeof v === 'number' ? v : NaN;
  return Number.isFinite(n) ? n : 0;
}

function fmtAmt(v: unknown): string {
  return num(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtQty(v: unknown): string {
  return num(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function fmtPrice(v: unknown): string {
  return num(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 3 });
}

function fmtDate(v?: string): string {
  return (v || '').slice(0, 10);
}

function fmtTime(v?: string): string {
  return (v || '').slice(0, 16).replace('T', ' ');
}

const WEEK_KEYS = ['weekMon', 'weekTue', 'weekWed', 'weekThu', 'weekFri', 'weekSat', 'weekSun'] as const;

/** 热力背景：强度 0~1 映射 alpha 0.08~0.25，统一红涨绿跌 */
function heatBg(pnl: number, maxAbs: number, market: string): string {
  const intensity = maxAbs > 0 ? Math.min(1, Math.abs(pnl) / maxAbs) : 0;
  const alpha = 0.08 + intensity * 0.17;
  const useRed = pnl > 0;
  return useRed ? `rgba(240,82,77,${alpha.toFixed(3)})` : `rgba(18,161,80,${alpha.toFixed(3)})`;
}

/* ───────────────────────── 页面 ───────────────────────── */

export default function PositionsPage() {
  const { t } = useTranslation('pages');
  const { market, currencySymbol: symbol } = useMarket();

  // 账户选择：'' = 合并账户，否则为 strategy_version_id
  const [account, setAccount] = useState('');
  const [accounts, setAccounts] = useState<AccountStat[]>([]);
  const [mergedStats, setMergedStats] = useState<AccountStat | null>(null);
  const [prevMarket, setPrevMarket] = useState(market);

  const [positions, setPositions] = useState<PositionRow[]>([]);
  const [posLoading, setPosLoading] = useState(false);
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [tradeLoading, setTradeLoading] = useState(false);

  const [calYear, setCalYear] = useState<number | null>(null);
  const [calMonth, setCalMonth] = useState<number | null>(null);
  const [calData, setCalData] = useState<PnlCalendarResp | null>(null);
  const [calLoading, setCalLoading] = useState(false);

  const [tick, setTick] = useState(0);
  const refreshAll = () => setTick((t) => t + 1);

  // 实时刷新：优先 WebSocket 推送驱动（服务端每 ~10s 广播行情快照，紧跟快照落库），
  // WS 断线时回落 30s 轮询（页面隐藏自动暂停）。两者都只触发既有取数 effect，
  // 现价=日线base+新鲜快照覆盖由后端统一计算。
  // （token 为空不连 WS、StrictMode 延迟建连等细节见 hook 头注释）
  const wsAliveRef = useMarketQuoteSocket(refreshAll, []);
  useAutoRefresh(() => { if (!wsAliveRef.current) refreshAll(); }, 30_000, []);

  // 切市场重置账户（各市场 strategy_version_id 不同，避免带旧市场策略参数请求）
  if (prevMarket !== market) {
    setPrevMarket(market);
    setAccount('');
  }

  const isMerged = account === '';
  const currentStats: AccountStat | null = isMerged
    ? mergedStats
    : accounts.find((a) => a.strategy_version_id === account) ?? null;

  /* 账户选择器数据：合并 stats + 各策略 stats（随刷新按钮一并刷新） */
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api<AccountStat[]>(`/accounts/stats/by-strategy?market=${market}`).catch(() => []),
      api<AccountStat>(`/accounts/stats?market=${market}`).catch(() => null),
    ]).then(([list, merged]) => {
      if (cancelled) return;
      setAccounts(Array.isArray(list) ? list : []);
      setMergedStats(merged);
    });
    return () => {
      cancelled = true;
    };
  }, [market, tick]);

  /* 持仓明细（随账户切换） */
  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams({ market });
    if (!isMerged) params.set('strategy', account);
    setPosLoading(true);
    api<PositionRow[]>(`/positions?${params.toString()}`)
      .then((list) => {
        if (!cancelled) setPositions(Array.isArray(list) ? list : []);
      })
      .catch(() => {
        if (!cancelled) setPositions([]);
      })
      .finally(() => {
        if (!cancelled) setPosLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, account, tick]);

  /* 成交记录：仅策略账户（合并账户无单一 detail id，显示引导提示） */
  useEffect(() => {
    if (isMerged) {
      setTrades([]);
      return;
    }
    let cancelled = false;
    setTradeLoading(true);
    api<StrategyDetailResp>(`/strategies/${account}/detail?market=${market}`)
      .then((d) => {
        if (cancelled) return;
        const list = Array.isArray(d?.trades) ? d.trades : [];
        setTrades([...list].sort((a, b) => (b.fill_time || '').localeCompare(a.fill_time || '')));
      })
      .catch(() => {
        if (!cancelled) setTrades([]);
      })
      .finally(() => {
        if (!cancelled) setTradeLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [market, account, tick]);

  /* 盈亏日历（随账户/年月切换；首次不带年月由后端返回最新月） */
  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams({ market });
    if (calYear != null && calMonth != null) {
      params.set('year', String(calYear));
      params.set('month', String(calMonth));
    }
    if (!isMerged) params.set('strategy', account);
    setCalLoading(true);
    api<PnlCalendarResp>(`/reports/pnl-calendar?${params.toString()}`)
      .then((r) => {
        if (cancelled) return;
        setCalData(r);
        if (r?.year != null && (r.year !== calYear || r.month !== calMonth)) {
          setCalYear(r.year);
          setCalMonth(r.month);
        }
      })
      .catch(() => {
        if (!cancelled) setCalData(null);
      })
      .finally(() => {
        if (!cancelled) setCalLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, account, calYear, calMonth, tick]);

  /* 日历月导航 */
  const shiftMonth = (delta: number) => {
    if (calYear == null || calMonth == null) return;
    const m = calMonth + delta;
    if (m < 1) {
      setCalYear(calYear - 1);
      setCalMonth(12);
    } else if (m > 12) {
      setCalYear(calYear + 1);
      setCalMonth(1);
    } else {
      setCalMonth(m);
    }
  };

  const dayMap = useMemo(() => {
    const map = new Map<number, DayPnl>();
    calData?.days?.forEach((d) => map.set(d.day, d));
    return map;
  }, [calData]);

  const maxAbsPnl = useMemo(
    () => Math.max(0, ...Array.from(dayMap.values()).map((d) => Math.abs(num(d.pnl)))),
    [dayMap],
  );

  // 月历网格（周一起始）
  const now = new Date();
  const cells: Array<{ day: number; rec?: DayPnl } | null> = [];
  if (calYear != null && calMonth != null) {
    const firstDow = new Date(calYear, calMonth - 1, 1).getDay(); // 0=周日
    const offset = (firstDow + 6) % 7;
    const daysInMonth = new Date(calYear, calMonth, 0).getDate();
    for (let i = 0; i < offset; i++) cells.push(null);
    for (let d = 1; d <= daysInMonth; d++) cells.push({ day: d, rec: dayMap.get(d) });
  }
  const todayDay =
    now.getFullYear() === calYear && now.getMonth() + 1 === calMonth ? now.getDate() : -1;

  /* ── Tab 1 持仓明细列 ── */
  const strategyColumn: ColumnsType<PositionRow> = [
    {
      title: t('positions.colStrategy'),
      dataIndex: 'strategy_name',
      key: 'sv',
      render: (v: string, row: PositionRow) => (
        <Tag style={{ fontSize: 11 }}>{v || row.strategy_version_id}</Tag>
      ),
    },
  ];

  const baseColumns: ColumnsType<PositionRow> = [
    {
      title: t('positions.colSymbol'),
      dataIndex: 'ts_code',
      key: 'ts_code',
      render: (v: string) => <SymbolLink code={v} />,
    },
    {
      title: t('positions.colDirection'),
      dataIndex: 'direction',
      key: 'direction',
      render: (v: string) => (
        <Tag color={v === 'LONG' ? 'blue' : 'orange'}>
          {v === 'LONG' ? t('positions.dirLong') : v === 'SHORT' ? t('positions.dirShort') : v}
        </Tag>
      ),
    },
    {
      title: t('positions.colQtyAvail'),
      key: 'qty_avail',
      align: 'right',
      render: (_: unknown, row: PositionRow) => (
        <span>
          <b>{fmtQty(row.open_qty)}</b>
          <span style={{ color: '#8B93A7' }}> / {fmtQty(row.available_qty)}</span>
        </span>
      ),
    },
    { title: t('positions.colOpenPrice'), dataIndex: 'open_price', key: 'open_price', align: 'right', render: (v: unknown) => fmtPrice(v) },
    {
      title: t('positions.colCurrentPrice'),
      dataIndex: 'current_price',
      key: 'current_price',
      align: 'right',
      render: (v: unknown) => <b>{fmtPrice(v)}</b>,
    },
    {
      title: t('positions.colFloatPnl'),
      key: 'float_pnl',
      align: 'right',
      sorter: (a, b) => num(a.float_pnl) - num(b.float_pnl),
      render: (_: unknown, row: PositionRow) => (
        <span style={{ color: pnlColor(num(row.float_pnl), market), fontWeight: 600 }}>
          {fmtMoney(num(row.float_pnl))}
          <br />
          <small style={{ fontWeight: 400 }}>{fmtPct(num(row.float_pnl_pct))}</small>
        </span>
      ),
    },
    {
      title: t('positions.colTodayPnl'),
      dataIndex: 'today_pnl',
      key: 'today_pnl',
      align: 'right',
      sorter: (a, b) => num(a.today_pnl) - num(b.today_pnl),
      render: (v: unknown) => (
        <span style={{ color: pnlColor(num(v), market), fontWeight: 600 }}>{fmtMoney(num(v))}</span>
      ),
    },
    {
      title: t('positions.colWeight'),
      dataIndex: 'weight_pct',
      key: 'weight_pct',
      width: 110,
      sorter: (a, b) => num(a.weight_pct) - num(b.weight_pct),
      render: (v: unknown) => (
        <Progress
          percent={Math.min(100, Math.round(num(v)))}
          size="small"
          style={{ maxWidth: 92, marginBottom: 0 }}
        />
      ),
    },
    {
      title: t('positions.colOpenDate'),
      dataIndex: 'opened_at',
      key: 'opened_at',
      render: (v?: string) => fmtDate(v),
    },
  ];

  const positionColumns: ColumnsType<PositionRow> = isMerged
    ? [baseColumns[0], ...strategyColumn, ...baseColumns.slice(1)]
    : baseColumns;

  /* ── Tab 2 成交记录列 ── */
  const tradeColumns: ColumnsType<TradeRow> = [
    {
      title: t('positions.colFillTime'),
      dataIndex: 'fill_time',
      key: 'fill_time',
      render: (v: string) => (
        <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{fmtTime(v)}</span>
      ),
    },
    {
      title: t('positions.colSymbol'),
      dataIndex: 'ts_code',
      key: 'ts_code',
      render: (v: string) => <SymbolLink code={v} />,
    },
    {
      title: t('positions.colDirection'),
      dataIndex: 'side',
      key: 'side',
      render: (v: string) =>
        // A股语境：买红、卖绿
        v === 'BUY' ? <Tag color="red">{t('positions.sideBuy')}</Tag> : <Tag color="green">{t('positions.sideSell')}</Tag>,
    },
    { title: t('positions.colFillPrice'), dataIndex: 'price', key: 'price', align: 'right', render: (v: unknown) => fmtPrice(v) },
    { title: t('positions.colQty'), dataIndex: 'qty', key: 'qty', align: 'right', render: (v: unknown) => fmtQty(v) },
    { title: t('positions.colAmount'), dataIndex: 'amount', key: 'amount', align: 'right', render: (v: unknown) => fmtAmt(v) },
    {
      title: t('positions.colPnl'),
      dataIndex: 'realized_pnl',
      key: 'realized_pnl',
      align: 'right',
      render: (v: number | null | undefined, row: TradeRow) =>
        row.side === 'SELL' && v != null ? (
          <span style={{ color: pnlColor(num(v), market), fontWeight: 600 }}>{fmtMoney(num(v))}</span>
        ) : (
          <span style={{ color: '#8B93A7' }}>—</span>
        ),
    },
    {
      title: t('positions.colReason'),
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

  /* 账户选择器选项：合并账户 + 各策略账户（label = 策略 ID + 权益摘要） */
  const accountOptions = [
    {
      value: '',
      label: mergedStats
        ? t('positions.mergedAccountEquity', { count: accounts.length, equity: `${symbol}${fmtAmt(mergedStats.total_equity)}` })
        : t('positions.mergedAccount', { count: accounts.length }),
    },
    ...accounts.map((a) => ({
      value: a.strategy_version_id ?? '',
      label: t('positions.strategyEquity', { name: strategyLabel(a.strategy_name) || a.strategy_version_id, equity: `${symbol}${fmtAmt(a.total_equity)}` }),
    })),
  ];

  const calSummary = calData?.summary;
  const calHasData = (calData?.days?.length ?? 0) > 0;

  return (
    <div>
      {/* ── 顶部：标题 + 刷新 + 账户选择器 ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <Space align="center">
          <Title level={4} style={{ margin: 0 }}>{t('positions.title')}</Title>
          <Button icon={<ReloadOutlined />} size="small" onClick={refreshAll}>{t('positions.refresh')}</Button>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {isMerged
              ? t('positions.mergedAccountBrief', { count: accounts.length })
              : strategyLabel(currentStats?.strategy_name) || account}
          </Text>
        </Space>
        <div style={{ flex: 1 }} />
        <Select
          size="small"
          style={{ width: 260 }}
          value={account}
          onChange={setAccount}
          options={accountOptions}
        />
      </div>

      {/* ── 账户概览条：9 卡 flex 均分一行（宽屏一行/中屏折行），统一高度 ── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: 12,
          marginBottom: 16,
        }}
      >
        <Card size="small" style={{ display: 'flex', flexDirection: 'column' }}>
          <Statistic title={t('positions.statTotalEquity')} value={num(currentStats?.total_equity)} precision={2} prefix={symbol} styles={{ content: { fontSize: 16 } }} />
        </Card>
        <Card size="small" style={{ display: 'flex', flexDirection: 'column' }}>
          <Statistic title={t('positions.statAvailCash')} value={num(currentStats?.available_cash)} precision={2} prefix={symbol} styles={{ content: { fontSize: 16 } }} />
        </Card>
        <Card size="small" style={{ display: 'flex', flexDirection: 'column' }}>
          <Statistic title={t('positions.statMarketValue')} value={num(currentStats?.market_value)} precision={2} prefix={symbol} styles={{ content: { fontSize: 16 } }} />
        </Card>
        <Card size="small" style={{ display: 'flex', flexDirection: 'column' }}>
          <Statistic
            title={t('positions.statFloatPnl')}
            value={num(currentStats?.unrealized_pnl)}
            precision={2}
            suffix={currentStats ? ` (${fmtPct(num(currentStats.unrealized_pnl_pct))})` : undefined}
            styles={{ content: { fontSize: 16, color: pnlColor(num(currentStats?.unrealized_pnl), market) } }}
          />
        </Card>
        <Card size="small" style={{ display: 'flex', flexDirection: 'column' }}>
          <Statistic
            title={t('positions.statTodayPnl')}
            value={num(currentStats?.today_pnl)}
            precision={2}
            styles={{ content: { fontSize: 16, color: pnlColor(num(currentStats?.today_pnl), market) } }}
          />
        </Card>
        <Card size="small" style={{ display: 'flex', flexDirection: 'column' }}>
          <Statistic
            title={t('positions.statRealizedPnl')}
            value={num(currentStats?.realized_pnl)}
            precision={2}
            suffix={currentStats ? ` (${fmtPct(num(currentStats.realized_pnl_pct))})` : undefined}
            styles={{ content: { fontSize: 16, color: pnlColor(num(currentStats?.realized_pnl), market) } }}
          />
        </Card>
        <Card size="small" style={{ display: 'flex', flexDirection: 'column' }}>
          <Statistic
            title={t('positions.statTotalPnl')}
            value={num(currentStats?.total_pnl)}
            precision={2}
            suffix={currentStats ? ` (${fmtPct(num(currentStats.total_pnl_pct))})` : undefined}
            styles={{ content: { fontSize: 16, color: pnlColor(num(currentStats?.total_pnl), market) } }}
          />
        </Card>
        <Card size="small" style={{ display: 'flex', flexDirection: 'column' }}>
          <Statistic title={t('positions.statPosition')} value={Math.round(num(currentStats?.position_ratio))} suffix="%" styles={{ content: { fontSize: 16 } }} />
          <Progress
            percent={Math.min(100, Math.round(num(currentStats?.position_ratio)))}
            size="small"
            showInfo={false}
            style={{ marginTop: 'auto', marginBottom: 0 }}
            strokeColor={
              num(currentStats?.position_ratio) > 80 ? '#ff4d4f' : num(currentStats?.position_ratio) > 50 ? '#faad14' : '#52c41a'
            }
          />
        </Card>
        <Card size="small" style={{ display: 'flex', flexDirection: 'column' }}>
          <Statistic title={t('positions.statPositionCount')} value={num(currentStats?.position_count)} suffix={t('positions.unitStock')} styles={{ content: { fontSize: 16 } }} />
        </Card>
      </div>

      {/* ── 3 Tab：持仓明细 / 成交记录 / 盈亏日历 ── */}
      <Tabs
        defaultActiveKey="positions"
        items={[
          {
            key: 'positions',
            label: t('positions.tabPositions'),
            children: (
              <Table<PositionRow>
                columns={positionColumns}
                dataSource={positions}
                rowKey="position_id"
                loading={posLoading}
                size="small"
                pagination={{ pageSize: 20, showSizeChanger: true }}
                locale={{ emptyText: t('positions.emptyPositions') }}
                scroll={{ x: 1100 }}
              />
            ),
          },
          {
            key: 'trades',
            label: t('positions.tabTrades'),
            children: isMerged ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={t('positions.tradesSelectStrategy')}
                style={{ padding: '32px 0' }}
              />
            ) : (
              <Table<TradeRow>
                columns={tradeColumns}
                dataSource={trades}
                rowKey="fill_id"
                loading={tradeLoading}
                size="small"
                pagination={{ pageSize: 20, showSizeChanger: true }}
                locale={{ emptyText: t('positions.emptyTrades') }}
              />
            ),
          },
          {
            key: 'calendar',
            label: t('positions.tabCalendar'),
            children: (
              <div>
                <Card size="small" style={{ marginBottom: 16 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
                    <Space size="large">
                      <Button size="small" icon={<LeftOutlined />} onClick={() => shiftMonth(-1)} disabled={calLoading || calYear == null} />
                      <Text strong style={{ fontSize: 16 }}>
                        {calYear != null && calMonth != null ? t('positions.yearMonth', { year: calYear, month: calMonth }) : '—'}
                      </Text>
                      <Button size="small" icon={<RightOutlined />} onClick={() => shiftMonth(1)} disabled={calLoading || calMonth == null} />
                    </Space>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {t(`common:market.${market}`)} · {symbol} · {t('positions.calMarkToMarket')} · {isMerged ? t('positions.mergedShort') : account}
                    </Text>
                  </div>

                  {calLoading && !calData ? (
                    <div style={{ padding: 48, textAlign: 'center' }}><Spin /></div>
                  ) : !calHasData ? (
                    <div style={{ padding: 48, textAlign: 'center' }}>
                      <Text type="secondary" style={{ fontSize: 14 }}>{t('positions.calNoData')}</Text>
                    </div>
                  ) : (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 6 }}>
                      {WEEK_KEYS.map((w) => (
                        <div key={w} style={{ textAlign: 'center', padding: '2px 0' }}>
                          <Text type="secondary" style={{ fontSize: 12 }}>{t(`positions.${w}`)}</Text>
                        </div>
                      ))}
                      {cells.map((cell, i) => {
                        if (cell == null) return <div key={`blank-${i}`} style={{ minHeight: 72 }} />;
                        const rec = cell.rec;
                        const bg = rec ? heatBg(num(rec.pnl), maxAbsPnl, market) : '#f5f6f8';
                        const isToday = cell.day === todayDay;
                        return (
                          <div
                            key={cell.day}
                            style={{
                              minHeight: 72,
                              padding: '6px 8px',
                              borderRadius: 6,
                              background: bg,
                              border: isToday ? '1.5px solid #5b6cf0' : '1px solid transparent',
                              display: 'flex',
                              flexDirection: 'column',
                              justifyContent: 'space-between',
                            }}
                          >
                            <Text style={{ fontSize: 12, color: rec ? undefined : '#b0b5c0' }}>{cell.day}</Text>
                            {rec ? (
                              <>
                                <div style={{ fontWeight: 600, fontSize: 12, color: pnlColor(num(rec.pnl), market), lineHeight: 1.3 }}>
                                  {fmtMoney(num(rec.pnl))}
                                </div>
                                <Text type="secondary" style={{ fontSize: 11 }}>
                                  {t('positions.dayStat', { count: rec.count, wins: rec.wins, losses: rec.losses })}
                                </Text>
                              </>
                            ) : (
                              <Text type="secondary" style={{ fontSize: 11 }}>—</Text>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </Card>

                <Row gutter={[12, 12]}>
                  <Col xs={12} md={6}>
                    <Card size="small">
                      <Statistic
                        title={t('positions.calMonthlyPnl')}
                        value={num(calSummary?.total_pnl)}
                        precision={2}
                        styles={{ content: { color: pnlColor(num(calSummary?.total_pnl), market) } }}
                      />
                    </Card>
                  </Col>
                  <Col xs={12} md={6}>
                    <Card size="small">
                      <Statistic title={t('positions.calWinDays')} value={num(calSummary?.win_days)} suffix={t('positions.unitDay')} styles={{ content: { color: pnlColor(1, market) } }} />
                    </Card>
                  </Col>
                  <Col xs={12} md={6}>
                    <Card size="small">
                      <Statistic title={t('positions.calLossDays')} value={num(calSummary?.loss_days)} suffix={t('positions.unitDay')} styles={{ content: { color: pnlColor(-1, market) } }} />
                    </Card>
                  </Col>
                  <Col xs={12} md={6}>
                    <Card size="small">
                      <Statistic title={t('positions.calWinRate')} value={(num(calSummary?.win_rate) * 100).toFixed(1)} suffix="%" />
                    </Card>
                  </Col>
                </Row>
              </div>
            ),
          },
        ]}
      />
    </div>
  );
}
