import { useEffect, useRef, useState } from 'react';
import {
  Button,
  Card,
  Col,
  Row,
  Segmented,
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
} from 'antd';
import { ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { api } from '../services/api';
import { useMarket, pnlColor, MARKET_META, type Market } from '../contexts/MarketContext';
import { showToast } from '../components/Toast';
import { strategyLabel } from '../i18n/strategyName';

const { Title, Text } = Typography;

/* ───────────────────────── 类型（后端契约） ───────────────────────── */

type ReportType = 'morning' | 'evening';

interface ReportPayload {
  report_id: string | null;
  market: string;
  report_date: string;
  report_type: ReportType;
  facts: Record<string, unknown>;
  narrative: string;
  model_used: string | null;
  source?: 'db' | 'computed';
}

interface ReportListItem {
  report_id: string;
  report_date: string;
  report_type: string;
  model_used: string | null;
}

/* ───────────────────────── 工具 ───────────────────────── */

function num(v: unknown): number | null {
  const n = typeof v === 'number' ? v : typeof v === 'string' ? parseFloat(v) : NaN;
  return Number.isFinite(n) ? n : null;
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function asArray(v: unknown): Record<string, unknown>[] {
  return Array.isArray(v)
    ? (v.filter((x) => x && typeof x === 'object') as Record<string, unknown>[])
    : [];
}

function fmtSigned(v: number, digits = 2): string {
  return `${v > 0 ? '+' : ''}${v.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

interface FactCard {
  key: string;
  title: string;
  value: string;
  raw?: number;
  isPnl?: boolean;
}

/** 从 facts（V2 现算 / V1 导入两种形态）推导统一的事实卡片行 */
function deriveCards(facts: Record<string, unknown>, market: string, t: TFunction): FactCard[] {
  const cards: FactCard[] = [];
  const planStatus = asRecord(facts.plan_status) ?? {};
  const merged = asRecord(facts.merged);

  // 候选标的数：V2 candidate_assets；V1 形态取各策略候选数之和
  let candidates = num(facts.candidate_assets);
  if (candidates == null) {
    candidates = asArray(facts.strategy_candidates).reduce(
      (acc, sc) => acc + (num(sc.count) ?? 0),
      0,
    );
  }
  if (candidates != null) cards.push({ key: 'cand', title: t('dailyReport.candidateAssets'), value: `${candidates}` });

  // 交易计划数：V2 plans_today；V1 形态取 plan_status 汇总
  let plans = num(facts.plans_today);
  if (plans == null) {
    plans = Object.values(planStatus).reduce<number>(
      (acc, v) => acc + (num(v) ?? 0),
      0,
    );
  }
  if (plans != null) cards.push({ key: 'plans', title: t('dailyReport.tradePlans'), value: `${plans}` });

  // 待授权：V2 approvals_pending；V1 形态用 plan_status.PENDING 近似（待审计划）
  const pending = num(facts.approvals_pending) ?? num(planStatus.PENDING);
  if (pending != null) cards.push({ key: 'pending', title: t('dailyReport.pending'), value: `${pending}` });

  // 当日平仓 / 盈亏 / 胜率（晚报）
  const closed = num(facts.closed_trades);
  if (closed != null) cards.push({ key: 'closed', title: t('dailyReport.closedTrades'), value: `${closed}` });

  const pnl = num(facts.total_pnl) ?? (merged ? num(merged.today_pnl) : null);
  if (pnl != null) {
    cards.push({ key: 'pnl', title: t('dailyReport.todayPnl'), value: fmtSigned(pnl), raw: pnl, isPnl: true });
  }

  const winRate = num(facts.win_rate);
  if (winRate != null) {
    cards.push({ key: 'win', title: t('dailyReport.winRate'), value: `${(winRate * 100).toFixed(1)}%` });
  }

  // 新建仓：V2 opened_positions；V1 形态取 today_fills 笔数
  const opened = num(facts.opened_positions) ?? asArray(facts.today_fills).length;
  if (opened != null) cards.push({ key: 'opened', title: t('dailyReport.openedPositions'), value: `${opened}` });

  // 市场环境
  const regime = asRecord(facts.regime);
  if (regime) {
    const label =
      typeof regime.label === 'string'
        ? regime.label
        : t(`dailyReport.regime.${String(regime.regime)}`, { defaultValue: t('dailyReport.regime.UNKNOWN') });
    cards.push({ key: 'regime', title: t('dailyReport.marketRegime'), value: label });
  }

  // 基准指数涨跌（取第一只，通常为上证指数）
  const moves = asArray(asRecord(facts.index_moves)?.moves);
  const firstMove = moves.find((m) => num(m.pct) != null);
  if (firstMove) {
    const pct = num(firstMove.pct)!;
    cards.push({
      key: 'index',
      title: firstMove.name != null ? String(firstMove.name) : t('dailyReport.benchmarkIndex'),
      value: `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`,
      raw: pct,
      isPnl: true,
    });
  }

  // V1 形态补充：总资产 / 持仓数
  if (merged) {
    const equity = num(merged.total_equity);
    if (equity != null) {
      cards.push({
        key: 'equity',
        title: t('dailyReport.mergedEquity'),
        value: `${MARKET_META[market as Market].short}${equity.toLocaleString('en-US', { maximumFractionDigits: 0 })}`,
      });
    }
    const positions = num(merged.position_count);
    if (positions != null) cards.push({ key: 'pos', title: t('dailyReport.positionCount'), value: `${positions}` });
  }
  return cards;
}

interface StrategyScore {
  name: string;
  trades: number;
  pnl: number;
  winRate: number;
}

function deriveScores(facts: Record<string, unknown>): StrategyScore[] {
  return asArray(facts.strategy_scores).map((s) => ({
    name: strategyLabel(s.strategy_name as string | null | undefined) || String(s.strategy_version_id ?? '—'),
    trades: num(s.trades) ?? 0,
    pnl: num(s.pnl) ?? 0,
    winRate: num(s.win_rate) ?? 0,
  }));
}

/* ───────────────────────── 页面 ───────────────────────── */

export default function DailyReportPage() {
  const { t } = useTranslation('pages');
  const { market } = useMarket();
  const [reportType, setReportType] = useState<ReportType>('morning');
  const [report, setReport] = useState<ReportPayload | null>(null);
  const [history, setHistory] = useState<ReportListItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  /** 点击历史项联动切换类型时，跳过 effect 里的 latest 拉取 */
  const skipLatestRef = useRef(false);

  const loadHistory = () => {
    api<ReportListItem[]>(`/reports/list?market=${market}&limit=30`)
      .then(setHistory)
      .catch(console.error);
  };

  const loadLatest = (type: ReportType) => {
    setLoading(true);
    setSelectedId(null);
    api<ReportPayload>(`/reports/latest?market=${market}&report_type=${type}`)
      .then(setReport)
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  const loadDetail = (id: string) => {
    setLoading(true);
    api<ReportPayload>(`/reports/${encodeURIComponent(id)}`)
      .then((r) => {
        setReport(r);
        setSelectedId(id);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadHistory();
    if (skipLatestRef.current) {
      skipLatestRef.current = false;
      return;
    }
    loadLatest(reportType);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, reportType]);

  const onGenerate = () => {
    setGenerating(true);
    api<ReportPayload>('/reports/generate', {
      method: 'POST',
      body: JSON.stringify({ market, report_type: reportType }),
    })
      .then((r) => {
        setReport(r);
        setSelectedId(null);
        showToast(t('dailyReport.generated'), 'success');
      })
      .catch(console.error)
      .finally(() => setGenerating(false));
  };

  const onHistoryClick = (item: ReportListItem) => {
    if (item.report_id === selectedId) return;
    const t: ReportType = item.report_type === 'evening' ? 'evening' : 'morning';
    if (t !== reportType) {
      skipLatestRef.current = true;
      setReportType(t);
    }
    loadDetail(item.report_id);
  };

  const facts = report?.facts ?? {};
  const cards = deriveCards(facts, market, t);
  const scores = deriveScores(facts);
  const typeLabel = report?.report_type === 'evening' ? t('dailyReport.eveningLabel') : t('dailyReport.morningLabel');
  const sourceLabel = selectedId ? t('dailyReport.srcHistory') : report?.source === 'computed' ? t('dailyReport.srcComputed') : t('dailyReport.srcLatest');

  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          marginBottom: 16,
          flexWrap: 'wrap',
        }}
      >
        <Title level={4} style={{ margin: 0 }}>
          {t('dailyReport.title')}
        </Title>
        <Segmented
          value={reportType}
          onChange={(v) => setReportType(v as ReportType)}
          options={[
            { label: t('dailyReport.segMorning'), value: 'morning' },
            { label: t('dailyReport.segEvening'), value: 'evening' },
          ]}
        />
        <Text type="secondary">{report?.report_date ?? '—'}</Text>
        <Space>
          <Button
            icon={<ReloadOutlined />}
            size="small"
            loading={loading}
            onClick={() => loadLatest(reportType)}
          >
            {t('dailyReport.refresh')}
          </Button>
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            size="small"
            loading={generating}
            onClick={onGenerate}
          >
            {t('dailyReport.generate')}
          </Button>
        </Space>
        <div style={{ flex: 1 }} />
        <Space size={4}>
          {report?.report_type ? (
            <Tag color={report.report_type === 'evening' ? 'purple' : 'blue'}>{typeLabel}</Tag>
          ) : null}
          <Tag>{sourceLabel}</Tag>
          {report?.model_used ? <Tag>{report.model_used}</Tag> : null}
          <Text type="secondary">{t('market.' + market, { ns: 'common' })}</Text>
        </Space>
      </div>

      <Row gutter={16}>
        <Col xs={24} lg={17}>
          {/* 事实卡片行：facts 有什么展示什么 */}
          <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
            {cards.map((c) => (
              <Col xs={8} md={6} xl={cards.length > 6 ? 3 : 4} key={c.key}>
                <Card size="small">
                  <Statistic
                    title={c.title}
                    value={c.value}
                    styles={{ content: 
                      c.isPnl && c.raw != null
                        ? { color: pnlColor(c.raw, market), fontSize: 20 }
                        : { fontSize: 20 }
                     }}
                  />
                </Card>
              </Col>
            ))}
            {cards.length === 0 && !loading ? (
              <Col span={24}>
                <Card size="small">
                  <Text type="secondary">{t('dailyReport.noFacts')}</Text>
                </Card>
              </Col>
            ) : null}
          </Row>

          {/* 分策略成绩单（晚报现算 facts 才有） */}
          {scores.length > 0 ? (
            <Card size="small" title={t('dailyReport.scoresTitle')} style={{ marginBottom: 16 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: '#8c8c8c' }}>
                    <th style={{ padding: '4px 8px', fontWeight: 500 }}>{t('dailyReport.scoreColStrategy')}</th>
                    <th style={{ padding: '4px 8px', fontWeight: 500 }}>{t('dailyReport.scoreColTrades')}</th>
                    <th style={{ padding: '4px 8px', fontWeight: 500 }}>{t('dailyReport.scoreColTodayPnl')}</th>
                    <th style={{ padding: '4px 8px', fontWeight: 500 }}>{t('dailyReport.scoreColWinRate')}</th>
                  </tr>
                </thead>
                <tbody>
                  {scores.map((s) => (
                    <tr key={s.name} style={{ borderTop: '1px solid #f0f0f0' }}>
                      <td style={{ padding: '5px 8px' }}>{s.name}</td>
                      <td style={{ padding: '5px 8px' }}>{s.trades}</td>
                      <td
                        style={{
                          padding: '5px 8px',
                          color: pnlColor(s.pnl, market),
                          fontWeight: 600,
                        }}
                      >
                        {fmtSigned(s.pnl)}
                      </td>
                      <td style={{ padding: '5px 8px' }}>{(s.winRate * 100).toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          ) : null}

          {/* 叙事正文 */}
          <Card size="small" title={t('dailyReport.narrativeTitle')} styles={{ body: { padding: 0 } }}>
            {loading && !report ? (
              <div style={{ padding: 48, textAlign: 'center' }}>
                <Spin />
              </div>
            ) : (
              <div
                style={{
                  borderLeft: '3px solid #1677ff',
                  margin: 16,
                  padding: '4px 0 4px 14px',
                  whiteSpace: 'pre-wrap',
                  fontSize: 13.5,
                  lineHeight: 1.9,
                }}
              >
                {report?.narrative || t('dailyReport.noNarrative')}
              </div>
            )}
          </Card>
        </Col>

        {/* 历史列表 */}
        <Col xs={24} lg={7}>
          <Card
            size="small"
            title={t('dailyReport.historyTitle')}
            extra={
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t('dailyReport.historyCount', { n: history.length })}
              </Text>
            }
          >
            <div style={{ maxHeight: 560, overflowY: 'auto' }}>
              {history.map((h) => (
                <div
                  key={h.report_id}
                  onClick={() => onHistoryClick(h)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '7px 8px',
                    borderRadius: 6,
                    cursor: 'pointer',
                    background: h.report_id === selectedId ? '#e6f4ff' : undefined,
                  }}
                >
                  <Text style={{ fontSize: 13, flex: 1 }}>{h.report_date}</Text>
                  <Tag
                    color={h.report_type === 'evening' ? 'purple' : 'blue'}
                    style={{ marginRight: 0 }}
                  >
                    {h.report_type === 'evening' ? t('dailyReport.tagEvening') : t('dailyReport.tagMorning')}
                  </Tag>
                  <Text
                    type="secondary"
                    style={{
                      fontSize: 11,
                      maxWidth: 92,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {h.model_used ?? '—'}
                  </Text>
                </div>
              ))}
              {history.length === 0 ? (
                <div style={{ padding: 24, textAlign: 'center' }}>
                  <Text type="secondary">{t('dailyReport.noHistory')}</Text>
                </div>
              ) : null}
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
