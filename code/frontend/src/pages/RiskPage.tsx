import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Card, Segmented, Spin, Table, Tag, Typography } from 'antd';
import { api } from '../services/api';
import { useMarket, pnlColor } from '../contexts/MarketContext';
import SymbolLink from '../components/SymbolLink';

const { Text } = Typography;

/* ───────────────────────── 类型（后端契约） ───────────────────────── */

/** /risk/checks 单项 */
interface RiskCheckRow {
  check_id: string;
  plan_id: string;
  ts_code: string;
  strategy_version_id: string;
  check_stage: string;
  check_round: number;
  decision_id: string;
  decision_result: string;
  warning_flag: boolean;
  rule_results: Record<string, { result?: string; note?: string; [k: string]: unknown }> | null;
  approver_type: string | null;
  created_at: string | null;
}

/** /risk/deviation 单项 */
interface DeviationRow {
  ts_code: string;
  fill_price: number;
  intent_price: number;
  deviation_pct: number;
  fill_qty: number | null;
  fill_time: string | null;
}

interface DeviationResp {
  avg_deviation_pct: number | null;
  records: DeviationRow[];
}

/* ───────────────────────── 常量与工具 ───────────────────────── */

/** V2 决策结果 → 展示口径（V1 语义：通过/警告/拦截） */
const RESULT_TAG: Record<string, { color: string; labelKey: string }> = {
  PASS: { color: 'green', labelKey: 'risk.resultPass' },
  PASS_WITH_WARNING: { color: 'gold', labelKey: 'risk.resultWarning' },
  REJECT: { color: 'red', labelKey: 'risk.resultReject' },
};

const STAGE_LABEL_KEYS: Record<string, string> = {
  PRE_TRADE: 'risk.stagePre',
  INTRADAY: 'risk.stageIntra',
  POST_TRADE: 'risk.stagePost',
};

/** 规则名 → i18n key */
const RULE_LABEL_KEYS: Record<string, string> = {
  PRICE_RANGE: 'risk.rulePriceRange',
  MIN_LIQUIDITY: 'risk.ruleMinLiquidity',
  DATA_FRESHNESS: 'risk.ruleDataFreshness',
  STRATEGY_EXPOSURE: 'risk.ruleStrategyExposure',
  MAX_POSITION_RATIO: 'risk.ruleMaxPositionRatio',
  PORTFOLIO_TOTAL_EXPOSURE: 'risk.rulePortfolioExposure',
  SINGLE_SECURITY_EXPOSURE: 'risk.ruleSingleExposure',
  DAILY_LOSS: 'risk.ruleDailyLoss',
};

/** 展示结果筛选（V1 语义值，前端本地过滤已拉取的全量列表） */
type ResultFilter = 'ALL' | 'PASS' | 'WARNING' | 'BLOCK';

function fmtTime(v?: string | null): string {
  return (v || '').slice(0, 19).replace('T', ' ');
}

/** 计划 ID 截短显示（保留尾部区分度） */
function shortId(id: string): string {
  return id.length > 14 ? `…${id.slice(-11)}` : id;
}

/* ───────────────────────── 展开行：规则明细 ───────────────────────── */

function RuleResultsDetail({ record }: { record: RiskCheckRow }) {
  const { t } = useTranslation('pages');
  const rules = record.rule_results ?? {};
  const entries = Object.entries(rules);
  if (entries.length === 0) {
    return <Text type="secondary" style={{ fontSize: 12 }}>{t('risk.noRuleDetail')}</Text>;
  }
  return (
    <div style={{ padding: '8px 12px' }}>
      {entries.map(([name, detail]) => {
        const r = (detail?.result ?? '').toUpperCase();
        const pass = r === 'PASS';
        const warn = r === 'WARNING' || r === 'WARN';
        // SKIPPED=规则数据缺失跳过（灰色，区别于 REJECT 拦截）
        const skipped = r === 'SKIPPED';
        const color = pass ? '#12A150' : warn ? '#F6A609' : skipped ? '#8C8C8C' : '#F0524D';
        // 除 result 外的其余字段作为上下文展示（price/turnover/exposure_pct/note 等）
        const context = Object.entries(detail)
          .filter(([k]) => k !== 'result')
          .map(([k, v]) => `${k}=${String(v)}`)
          .join('，');
        return (
          <div
            key={name}
            style={{ display: 'flex', alignItems: 'baseline', gap: 10, padding: '3px 0', fontSize: 12 }}
          >
            <span style={{ width: 8, color, fontWeight: 700 }}>{pass ? '✓' : warn ? '⚠' : skipped ? '–' : '✗'}</span>
            <span style={{ width: 160, fontWeight: 600 }}>{RULE_LABEL_KEYS[name] ? t(RULE_LABEL_KEYS[name]) : name}</span>
            <span style={{ width: 90, color, fontWeight: 600 }}>{pass ? t('risk.resultPass') : warn ? t('risk.resultWarning') : skipped ? t('risk.resultSkipped') : r || t('risk.ruleUnknown')}</span>
            <Text type="secondary" style={{ flex: 1, fontSize: 12 }}>{context || '—'}</Text>
          </div>
        );
      })}
    </div>
  );
}

/* ───────────────────────── 页面 ───────────────────────── */

export default function RiskPage() {
  const { t } = useTranslation('pages');
  const { market } = useMarket();

  const [checks, setChecks] = useState<RiskCheckRow[]>([]);
  const [checksLoading, setChecksLoading] = useState(false);
  const [filter, setFilter] = useState<ResultFilter>('ALL');

  const [deviation, setDeviation] = useState<DeviationResp | null>(null);
  const [devLoading, setDevLoading] = useState(false);

  const [tab, setTab] = useState<string | number>('checks');

  /* 风控审查列表（拉全量后本地计数与筛选） */
  useEffect(() => {
    let cancelled = false;
    setChecksLoading(true);
    api<RiskCheckRow[]>(`/risk/checks?market=${market}&limit=200`)
      .then((rows) => { if (!cancelled) setChecks(Array.isArray(rows) ? rows : []); })
      .catch(() => { if (!cancelled) setChecks([]); })
      .finally(() => { if (!cancelled) setChecksLoading(false); });
    return () => { cancelled = true; };
  }, [market]);

  /* 偏差分析（进入 tab 时加载） */
  useEffect(() => {
    if (tab !== 'deviation') return;
    let cancelled = false;
    setDevLoading(true);
    api<DeviationResp>(`/risk/deviation?market=${market}&limit=200`)
      .then((d) => { if (!cancelled) setDeviation(d); })
      .catch(() => { if (!cancelled) setDeviation(null); })
      .finally(() => { if (!cancelled) setDevLoading(false); });
    return () => { cancelled = true; };
  }, [market, tab]);

  const counts = {
    ALL: checks.length,
    PASS: checks.filter(c => c.decision_result === 'PASS').length,
    WARNING: checks.filter(c => c.decision_result === 'PASS_WITH_WARNING').length,
    BLOCK: checks.filter(c => c.decision_result === 'REJECT').length,
  };

  const filtered = filter === 'ALL'
    ? checks
    : checks.filter(c => c.decision_result === filter
        || (filter === 'WARNING' && c.decision_result === 'PASS_WITH_WARNING')
        || (filter === 'BLOCK' && c.decision_result === 'REJECT'));

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, margin: 0 }}>{t('risk.title')}</h1>
        <span style={{ color: '#8B93A7', fontSize: 13 }}>{t('risk.subtitle')}</span>
      </div>

      <Segmented
        value={tab}
        onChange={setTab}
        options={[
          { label: t('risk.tabChecks'), value: 'checks' },
          { label: t('risk.tabDeviation'), value: 'deviation' },
        ]}
        style={{ marginBottom: 16 }}
      />

      {tab === 'checks' && (
        <>
          <div style={{ marginBottom: 12 }}>
            <Segmented
              value={filter}
              onChange={(v) => setFilter(v as ResultFilter)}
              options={[
                { label: t('risk.filterAll', { n: counts.ALL }), value: 'ALL' },
                { label: t('risk.filterPass', { n: counts.PASS }), value: 'PASS' },
                { label: t('risk.filterWarning', { n: counts.WARNING }), value: 'WARNING' },
                { label: t('risk.filterBlock', { n: counts.BLOCK }), value: 'BLOCK' },
              ]}
            />
          </div>
          {checksLoading ? (
            <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
          ) : (
            <Table<RiskCheckRow>
              rowKey="check_id"
              size="small"
              dataSource={filtered}
              pagination={{ pageSize: 20, showSizeChanger: false }}
              expandable={{ expandedRowRender: (record) => <RuleResultsDetail record={record} /> }}
              columns={[
                {
                  title: t('risk.colSymbol'), dataIndex: 'ts_code', key: 'ts_code', width: 110,
                  render: (v: string) => <SymbolLink code={v} />,
                },
                {
                  title: t('risk.colDecision'), dataIndex: 'decision_result', key: 'decision_result', width: 110,
                  render: (v: string) => {
                    const rt = RESULT_TAG[v] ?? { color: 'default', labelKey: '' };
                    return <Tag color={rt.color}>{rt.labelKey ? t(rt.labelKey) : v}</Tag>;
                  },
                },
                {
                  title: t('risk.colWarnFlag'), dataIndex: 'warning_flag', key: 'warning_flag', width: 90,
                  render: (v: boolean) => (v
                    ? <span style={{ color: '#F6A609', fontWeight: 700 }}>⚠ {t('risk.resultWarning')}</span>
                    : <span style={{ color: '#C5CBD8' }}>—</span>),
                },
                {
                  title: t('risk.colStage'), dataIndex: 'check_stage', key: 'check_stage', width: 100,
                  render: (v: string) => <Tag>{STAGE_LABEL_KEYS[v] ? t(STAGE_LABEL_KEYS[v]) : v}</Tag>,
                },
                {
                  title: t('risk.colPlanId'), dataIndex: 'plan_id', key: 'plan_id', width: 120,
                  render: (v: string) => (
                    <Text copyable={{ text: v }} style={{ fontFamily: 'monospace', fontSize: 12 }}>
                      {shortId(v)}
                    </Text>
                  ),
                },
                {
                  title: t('risk.colTime'), dataIndex: 'created_at', key: 'created_at', width: 160,
                  render: (v: string | null) => (
                    <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#8B93A7' }}>
                      {fmtTime(v)}
                    </span>
                  ),
                },
              ]}
            />
          )}
        </>
      )}

      {tab === 'deviation' && (
        <>
          <Card size="small" style={{ marginBottom: 12, maxWidth: 320 }}>
            <div style={{ fontSize: 12, color: '#8B93A7' }}>{t('risk.devAvgSlippage')}</div>
            <div style={{ fontSize: 26, fontWeight: 700, color: pnlColor(deviation?.avg_deviation_pct ?? 0, market) }}>
              {deviation?.avg_deviation_pct != null
                ? `${deviation.avg_deviation_pct >= 0 ? '+' : ''}${deviation.avg_deviation_pct.toFixed(3)}%`
                : '—'}
            </div>
            <div style={{ fontSize: 11, color: '#8B93A7', marginTop: 2 }}>
              {t('risk.devSlippageNote')}
            </div>
          </Card>
          {devLoading ? (
            <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
          ) : (
            <Table<DeviationRow>
              rowKey={(r) => `${r.ts_code}-${r.fill_time ?? ''}-${r.fill_price}`}
              size="small"
              dataSource={deviation?.records ?? []}
              pagination={{ pageSize: 20, showSizeChanger: false }}
              columns={[
                {
                  title: t('risk.colSymbol'), dataIndex: 'ts_code', key: 'ts_code', width: 110,
                  render: (v: string) => <SymbolLink code={v} />,
                },
                {
                  title: t('risk.colIntentPrice'), dataIndex: 'intent_price', key: 'intent_price', width: 110, align: 'right',
                  render: (v: number) => <span style={{ fontFamily: 'monospace' }}>{v.toFixed(3)}</span>,
                },
                {
                  title: t('risk.colFillPrice'), dataIndex: 'fill_price', key: 'fill_price', width: 110, align: 'right',
                  render: (v: number) => <span style={{ fontFamily: 'monospace' }}>{v.toFixed(3)}</span>,
                },
                {
                  title: t('risk.colDeviation'), dataIndex: 'deviation_pct', key: 'deviation_pct', width: 110, align: 'right',
                  render: (v: number) => (
                    <span style={{ fontFamily: 'monospace', fontWeight: 700, color: pnlColor(v, market) }}>
                      {v >= 0 ? '+' : ''}{v.toFixed(3)}%
                    </span>
                  ),
                },
                {
                  title: t('risk.colQty'), dataIndex: 'fill_qty', key: 'fill_qty', width: 110, align: 'right',
                  render: (v: number | null) => (
                    <span style={{ fontFamily: 'monospace' }}>
                      {v != null ? v.toLocaleString('en-US', { maximumFractionDigits: 2 }) : '—'}
                    </span>
                  ),
                },
                {
                  title: t('risk.colFillTime'), dataIndex: 'fill_time', key: 'fill_time', width: 170,
                  render: (v: string | null) => (
                    <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#8B93A7' }}>{fmtTime(v)}</span>
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
