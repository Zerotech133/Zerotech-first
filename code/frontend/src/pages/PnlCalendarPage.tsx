import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Card, Col, Row, Space, Spin, Statistic, Typography, Select } from 'antd';
import { LeftOutlined, RightOutlined, ReloadOutlined } from '@ant-design/icons';
import { api } from '../services/api';
import { useMarket, pnlColor, fmtMoney, MARKET_META } from '../contexts/MarketContext';
import { useStrategies } from '../hooks/useStrategies';

const { Title, Text } = Typography;

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
    total_pnl_pct: number;
    win_days: number;
    loss_days: number;
    win_rate: number;
  };
}

const WEEK_KEYS = ['weekMon', 'weekTue', 'weekWed', 'weekThu', 'weekFri', 'weekSat', 'weekSun'] as const;

/** 热力背景：强度 0~1 映射 alpha 0.08~0.25，统一红涨绿跌 */
function heatBg(pnl: number, maxAbs: number, market: string): string {
  const intensity = maxAbs > 0 ? Math.min(1, Math.abs(pnl) / maxAbs) : 0;
  const alpha = 0.08 + intensity * 0.17;
  const useRed = pnl > 0;
  return useRed ? `rgba(240,82,77,${alpha.toFixed(3)})` : `rgba(18,161,80,${alpha.toFixed(3)})`;
}

export default function PnlCalendarPage() {
  const { t } = useTranslation('pages');
  const { market } = useMarket();
  const { strategies, strategy, setStrategy } = useStrategies();
  const [year, setYear] = useState<number | null>(null);
  const [month, setMonth] = useState<number | null>(null);
  const [data, setData] = useState<PnlCalendarResp | null>(null);
  const [loading, setLoading] = useState(false);

  const load = (y: number | null, m: number | null, s: string = strategy) => {
    const params = new URLSearchParams({ market });
    if (y != null && m != null) {
      params.set('year', String(y));
      params.set('month', String(m));
    }
    if (s) params.set('strategy', s);
    setLoading(true);
    api<PnlCalendarResp>(`/reports/pnl-calendar?${params.toString()}`)
      .then((r) => {
        setData(r);
        if (r.year != null && (r.year !== y || r.month !== m)) {
          setYear(r.year);
          setMonth(r.month);
        }
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load(year, month, strategy);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, year, month, strategy]);

  const dayMap = useMemo(() => {
    const map = new Map<number, DayPnl>();
    data?.days.forEach((d) => map.set(d.day, d));
    return map;
  }, [data]);

  const maxAbsPnl = useMemo(
    () => Math.max(0, ...Array.from(dayMap.values()).map((d) => Math.abs(d.pnl))),
    [dayMap],
  );

  const shiftMonth = (delta: number) => {
    if (year == null || month == null) return;
    const m = month + delta;
    if (m < 1) { setYear(year - 1); setMonth(12); }
    else if (m > 12) { setYear(year + 1); setMonth(1); }
    else { setMonth(m); }
  };

  // 月历网格（周一起始）
  const cells: Array<{ day: number; rec?: DayPnl } | null> = [];
  if (year != null && month != null) {
    const firstDow = new Date(year, month - 1, 1).getDay(); // 0=周日
    const offset = (firstDow + 6) % 7;
    const daysInMonth = new Date(year, month, 0).getDate();
    for (let i = 0; i < offset; i++) cells.push(null);
    for (let d = 1; d <= daysInMonth; d++) cells.push({ day: d, rec: dayMap.get(d) });
  }

  const summary = data?.summary;
  const hasData = (data?.days?.length ?? 0) > 0;
  const symbol = MARKET_META[market].short;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <Space align="center">
          <Title level={4} style={{ margin: 0 }}>{t('pnlCalendar.title')}</Title>
          <Button icon={<ReloadOutlined />} size="small" onClick={() => load(year, month)} loading={loading}>
            {t('pnlCalendar.refresh')}
          </Button>
        </Space>
        <div style={{ flex: 1 }} />
        <Select
          size="small"
          placeholder={t('pnlCalendar.allStrategies')}
          allowClear
          style={{ width: 200 }}
          value={strategy || undefined}
          onChange={v => setStrategy(v ?? '')}
          options={strategies.map(s => ({ value: s, label: s }))}
        />
      </div>

      <Card size="small" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <Space size="large">
            <Button size="small" icon={<LeftOutlined />} onClick={() => shiftMonth(-1)} disabled={loading} />
            <Text strong style={{ fontSize: 16 }}>
              {year != null && month != null ? t('pnlCalendar.yearMonth', { year, month }) : '—'}
            </Text>
            <Button size="small" icon={<RightOutlined />} onClick={() => shiftMonth(1)} disabled={loading} />
          </Space>
          <Text type="secondary">
            {t(`common:market.${market}`)} · {symbol} · {t('pnlCalendar.markToMarket')}{strategy ? ` · ${t('pnlCalendar.strategyTag', { strategy })}` : ''}
          </Text>
        </div>

        {loading && !data ? (
          <div style={{ padding: 48, textAlign: 'center' }}><Spin /></div>
        ) : !hasData ? (
          <div style={{ padding: 48, textAlign: 'center' }}>
            <Text type="secondary" style={{ fontSize: 14 }}>{t('pnlCalendar.noData')}</Text>
          </div>
        ) : (
          <div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 6, marginBottom: 6 }}>
              {WEEK_KEYS.map((w) => (
                <div key={w} style={{ textAlign: 'center', padding: '2px 0' }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>{t(`pnlCalendar.${w}`)}</Text>
                </div>
              ))}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 6 }}>
              {cells.map((cell, i) => {
                if (cell == null) return <div key={`blank-${i}`} style={{ minHeight: 72 }} />;
                const rec = cell.rec;
                const bg = rec ? heatBg(rec.pnl, maxAbsPnl, market) : '#f5f6f8';
                return (
                  <div
                    key={cell.day}
                    style={{
                      minHeight: 72,
                      padding: '6px 8px',
                      borderRadius: 6,
                      background: bg,
                      display: 'flex',
                      flexDirection: 'column',
                      justifyContent: 'space-between',
                    }}
                  >
                    <Text style={{ fontSize: 12, color: rec ? undefined : '#b0b5c0' }}>{cell.day}</Text>
                    {rec ? (
                      <>
                        <div style={{ fontWeight: 600, fontSize: 12, color: pnlColor(rec.pnl, market), lineHeight: 1.3 }}>
                          {fmtMoney(rec.pnl)}
                        </div>
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          {t('pnlCalendar.dayStat', { count: rec.count, wins: rec.wins, losses: rec.losses })}
                        </Text>
                      </>
                    ) : (
                      <Text type="secondary" style={{ fontSize: 11 }}>—</Text>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </Card>

      <Row gutter={16}>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title={t('pnlCalendar.totalPnl')}
              value={Math.abs(summary?.total_pnl ?? 0)}
              precision={2}
              prefix={(summary?.total_pnl ?? 0) >= 0 ? '+' : '-'}
              suffix={
                <span style={{ fontSize: 14, color: pnlColor(summary?.total_pnl ?? 0, market) }}>
                  {`(${(summary?.total_pnl_pct ?? 0) >= 0 ? '+' : ''}${(summary?.total_pnl_pct ?? 0).toFixed(2)}%)`}
                </span>
              }
              styles={{ content: { color: pnlColor(summary?.total_pnl ?? 0, market) } }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title={t('pnlCalendar.winDays')}
              value={summary?.win_days ?? 0}
              suffix={t('pnlCalendar.unitDay')}
              styles={{ content: { color: pnlColor(1, market) } }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title={t('pnlCalendar.lossDays')}
              value={summary?.loss_days ?? 0}
              suffix={t('pnlCalendar.unitDay')}
              styles={{ content: { color: pnlColor(-1, market) } }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title={t('pnlCalendar.winRate')}
              value={((summary?.win_rate ?? 0) * 100).toFixed(1)}
              suffix="%"
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
