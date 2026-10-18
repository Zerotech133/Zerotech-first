import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Card, Input, Table, Tag, Timeline, Typography } from 'antd';
import { api } from '../services/api';
import { useMarket } from '../contexts/MarketContext';
import { useAutoRefresh } from '../hooks/useAutoRefresh';

const { Text } = Typography;

interface Status {
  version: string;
  strategies: number;
  strategy_versions: number;
  approvals: number;
  positions: number;
  replays: number;
  quotes: number;
}

interface QualityIssue {
  missing_close: number;
  zero_volume: number;
  abnormal_change: number;
  stale: number;
}

interface DataQuality {
  market: string;
  trade_date: string | null;
  total_instruments: number;
  checked: number;
  issues: QualityIssue;
  quality_score: number;
}

interface ContinuityTop {
  symbol: string;
  missing_days: number;
  first: string;
  last: string;
}

interface Continuity {
  market: string;
  start: string;
  end: string;
  symbols_with_gaps: number;
  total_missing: number;
  top: ContinuityTop[];
}

interface SystemTask {
  task_name: string;
  trade_date: string;
  phase: string;
  status: 'COMPLETED' | 'MISSING' | 'PENDING';
  detail: string;
  /** B7 C7：COMPLETED 由数据存在性反推（V2 无任务运行表）→ 显示「已完成（推断）」。 */
  inferred?: boolean;
}

interface AuditEvent {
  time: string | null;
  actor: string;
  action: string;
  target: string;
  detail: string;
}

interface TraceEvent {
  stage: string;
  ts_code: string;
  detail: string;
  time: string | null;
}

const TABS = [
  { key: 'status' },
  { key: 'quality' },
  { key: 'continuity' },
  { key: 'tasks' },
  { key: 'audit' },
] as const;
type TabKey = (typeof TABS)[number]['key'];

/** 四类问题的配色（V1 大白话口径；文案见 systemStatus.issue.*） */
const ISSUE_META: { key: keyof QualityIssue; color: string }[] = [
  { key: 'missing_close', color: '#F6A609' },
  { key: 'zero_volume', color: '#F6A609' },
  { key: 'abnormal_change', color: '#F0524D' },
  { key: 'stale', color: '#8B93A7' },
];

function fmtTime(t: string | null): string {
  if (!t) return '—';
  return t.slice(0, 19).replace('T', ' ');
}

function Item({ label, value, note }: { label: string; value: string | number; note?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 0', borderBottom: '1px solid #F2F3F7' }}>
      <span style={{ fontSize: 13, color: '#4A5268' }}>{label}</span>
      <span style={{ fontSize: 13, fontWeight: 600, color: '#1E2330' }}>
        {value}{note && <span style={{ fontWeight: 400, color: '#8B93A7', marginLeft: 6, fontSize: 11 }}>{note}</span>}
      </span>
    </div>
  );
}

export default function SystemStatusPage() {
  const { t } = useTranslation('pages');
  const { market } = useMarket();
  const [tab, setTab] = useState<TabKey>('status');
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState(false);
  const [quality, setQuality] = useState<DataQuality | null>(null);
  const [tasks, setTasks] = useState<SystemTask[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [traceId, setTraceId] = useState('');
  const [trace, setTrace] = useState<TraceEvent[] | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [continuity, setContinuity] = useState<Continuity | null>(null);

  const loadStatus = useCallback(() => {
    api<Status>('/status').then(s => { setStatus(s); setError(false); }).catch(() => setError(true));
  }, []);

  const loadQuality = useCallback(() => {
    api<DataQuality>(`/system/data-quality?market=${market}`).then(setQuality).catch(() => setQuality(null));
  }, [market]);

  const loadTasks = useCallback(() => {
    api<SystemTask[]>(`/system/tasks?market=${market}&limit=50`).then(setTasks).catch(() => setTasks([]));
  }, [market]);

  const loadAudit = useCallback(() => {
    api<AuditEvent[]>('/system/audit/recent?limit=30').then(setAudit).catch(() => setAudit([]));
  }, []);

  const loadContinuity = useCallback(() => {
    api<Continuity>(`/system/continuity?market=${market}&days=60`).then(setContinuity).catch(() => setContinuity(null));
  }, [market]);

  useEffect(() => {
    loadStatus();
    loadQuality();
    loadTasks();
  }, [loadStatus, loadQuality, loadTasks]);

  useEffect(() => {
    if (tab === 'audit') loadAudit();
    if (tab === 'continuity') loadContinuity();
  }, [tab, loadAudit, loadContinuity]);

  // 30s 自动刷新（页面不可见时暂停）
  useAutoRefresh(() => {
    loadStatus();
    loadQuality();
    loadTasks();
    if (tab === 'audit') loadAudit();
  }, 30000, [market, tab]);

  const queryTrace = () => {
    const id = traceId.trim();
    if (!id) return;
    setTraceLoading(true);
    setTraceError(null);
    api<TraceEvent[]>(`/system/audit/trace/${encodeURIComponent(id)}`)
      .then(r => setTrace(r))
      .catch(e => { setTrace(null); setTraceError(e.message); })
      .finally(() => setTraceLoading(false));
  };

  const score = quality?.quality_score ?? 0;
  const scoreColor = score >= 95 ? '#12A150' : score >= 80 ? '#F6A609' : '#F0524D';
  const missingCount = tasks.filter(t => t.status === 'MISSING').length;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>{t('systemStatus.title')}</h1>
        <Tag color={error ? 'error' : 'success'}>{error ? t('systemStatus.offline') : t('systemStatus.online')}</Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>{t('systemStatus.autoRefresh')}</Text>
        <div style={{ flex: 1 }} />
        <Text type="secondary" style={{ fontSize: 12 }}>{t('market.' + market, { ns: 'common' })}</Text>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 4, borderBottom: '2px solid #E8EAF0', marginBottom: 20 }}>
        {TABS.map(tabItem => (
          <button
            key={tabItem.key}
            onClick={() => setTab(tabItem.key)}
            style={{
              padding: '8px 16px', border: 'none', cursor: 'pointer', fontSize: 13,
              background: 'transparent', color: tab === tabItem.key ? '#1E2330' : '#8B93A7',
              fontWeight: tab === tabItem.key ? 600 : 400,
              borderBottom: `2px solid ${tab === tabItem.key ? '#5B6CF0' : 'transparent'}`,
              marginBottom: -2,
            }}
          >
            {t(`systemStatus.tab.${tabItem.key}`)}
            {tabItem.key === 'tasks' && missingCount > 0 && (
              <span style={{ marginLeft: 6, color: '#F0524D', fontSize: 11 }}>{t('systemStatus.tasksMissing', { n: missingCount })}</span>
            )}
          </button>
        ))}
      </div>

      {/* Tab 1 运行状态（原有内容 + 数据质量卡） */}
      {tab === 'status' && (
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <div style={{ background: '#fff', border: '1px solid #E8EAF0', borderRadius: 8, padding: '0 20px', minWidth: 360 }}>
            {!status && !error && <div style={{ color: '#8B93A7', fontSize: 13, padding: '16px 0' }}>{t('systemStatus.loading')}</div>}
            {error && <div style={{ color: '#F0524D', fontSize: 13, padding: '16px 0' }}>{t('systemStatus.backendError')}</div>}
            {status && (
              <>
                <Item label={t('systemStatus.itemVersion')} value={status.version} />
                <Item label={t('systemStatus.itemQuotes')} value={status.quotes} note={t('systemStatus.unitStock')} />
                <Item label={t('systemStatus.itemStrategies')} value={status.strategies} />
                <Item label={t('systemStatus.itemStrategyVersions')} value={status.strategy_versions} />
                <Item label={t('systemStatus.itemApprovals')} value={status.approvals} />
                <Item label={t('systemStatus.itemPositions')} value={status.positions} />
                <Item label={t('systemStatus.itemReplays')} value={status.replays} />
              </>
            )}
          </div>
          {/* 数据质量卡 */}
          <Card size="small" style={{ minWidth: 260 }} title={`${t('systemStatus.qualityCardTitle')}${quality?.trade_date ? ` · ${quality.trade_date}` : ''}`}>
            {quality ? (
              <div style={{ textAlign: 'center', padding: '12px 0' }}>
                <div style={{ fontSize: 44, fontWeight: 700, color: scoreColor, lineHeight: 1.1 }}>
                  {quality.quality_score.toFixed(1)}
                </div>
                <div style={{ fontSize: 12, color: '#8B93A7', marginBottom: 8 }}>{t('systemStatus.qualityScore')}</div>
                <div style={{ fontSize: 12, color: '#4A5268' }}>
                  {t('systemStatus.checkedInfo', { checked: quality.checked, total: quality.total_instruments, issues: Object.values(quality.issues).reduce((s, n) => s + n, 0) })}
                </div>
              </div>
            ) : (
              <div style={{ color: '#8B93A7', fontSize: 13, textAlign: 'center', padding: 20 }}>{t('systemStatus.loading')}</div>
            )}
          </Card>
        </div>
      )}

      {/* Tab 2 数据质量 */}
      {tab === 'quality' && (
        <div>
          <div style={{ display: 'flex', gap: 16, marginBottom: 16, flexWrap: 'wrap' }}>
            <Card size="small" style={{ minWidth: 220 }} styles={{ body: { textAlign: 'center', padding: '20px 16px' } }}>
              <div style={{ fontSize: 48, fontWeight: 700, color: scoreColor, lineHeight: 1.1 }}>
                {quality ? quality.quality_score.toFixed(1) : '—'}
              </div>
              <div style={{ fontSize: 12, color: '#8B93A7', marginTop: 6 }}>{t('systemStatus.qualityScore')}</div>
            </Card>
            <Card size="small" styles={{ body: { padding: '16px 20px' } }}>
              <div style={{ fontSize: 12, color: '#8B93A7' }}>{t('systemStatus.tradeDateLabel')} {quality?.trade_date ?? '—'}</div>
              <div style={{ fontSize: 13, margin: '6px 0' }}>{t('systemStatus.checkedPrefix')} <b>{quality?.checked ?? '—'}</b> {t('systemStatus.dayKUnit')}</div>
              <div style={{ fontSize: 13 }}>{t('systemStatus.totalPrefix')} <b>{quality?.total_instruments ?? '—'}</b> {t('systemStatus.totalUnit')}</div>
            </Card>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16 }}>
            {ISSUE_META.map(meta => {
              const count = quality?.issues[meta.key] ?? 0;
              return (
                <Card key={meta.key} size="small">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontSize: 13, fontWeight: 600, color: count > 0 ? meta.color : '#1E2330' }}>{t(`systemStatus.issue.${meta.key}.label`)}</span>
                    <span style={{ fontSize: 26, fontWeight: 700, color: count > 0 ? meta.color : '#12A150' }}>{count}</span>
                  </div>
                  <div style={{ fontSize: 12, color: '#8B93A7', marginTop: 6 }}>{t(`systemStatus.issue.${meta.key}.desc`)}</div>
                </Card>
              );
            })}
          </div>
          <div style={{ fontSize: 12, color: '#8B93A7', marginTop: 16 }}>
            {t('systemStatus.scoreFormula')}
          </div>
        </div>
      )}

      {/* Tab 连续性 */}
      {tab === 'continuity' && (
        <div>
          <div style={{ display: 'flex', gap: 16, marginBottom: 16, flexWrap: 'wrap' }}>
            <Card size="small" style={{ minWidth: 200 }} styles={{ body: { textAlign: 'center', padding: '20px 16px' } }}>
              <div style={{ fontSize: 44, fontWeight: 700, lineHeight: 1.1,
                color: (continuity?.symbols_with_gaps ?? 0) > 0 ? '#F6A609' : '#12A150' }}>
                {continuity ? continuity.symbols_with_gaps : '—'}
              </div>
              <div style={{ fontSize: 12, color: '#8B93A7', marginTop: 6 }}>{t('systemStatus.gapSymbols')}</div>
            </Card>
            <Card size="small" style={{ minWidth: 200 }} styles={{ body: { textAlign: 'center', padding: '20px 16px' } }}>
              <div style={{ fontSize: 44, fontWeight: 700, lineHeight: 1.1,
                color: (continuity?.total_missing ?? 0) > 0 ? '#F0524D' : '#12A150' }}>
                {continuity ? continuity.total_missing : '—'}
              </div>
              <div style={{ fontSize: 12, color: '#8B93A7', marginTop: 6 }}>{t('systemStatus.gapTotalDays')}</div>
            </Card>
            <Card size="small" styles={{ body: { padding: '16px 20px' } }}>
              <div style={{ fontSize: 12, color: '#8B93A7' }}>{t('systemStatus.windowPrefix')} {continuity?.start ?? '—'} ~ {continuity?.end ?? '—'}</div>
              <div style={{ fontSize: 13, margin: '6px 0' }}>{t('systemStatus.baseline', { market: t('market.' + market, { ns: 'common' }) })}</div>
              <div style={{ fontSize: 12, color: '#8B93A7' }}>{t('systemStatus.backfillPrefix')} <code>check_continuity.py --repair</code> {t('systemStatus.backfillSuffix')}</div>
            </Card>
          </div>
          <Table<ContinuityTop>
            rowKey="symbol"
            size="small"
            dataSource={continuity?.top ?? []}
            pagination={false}
            locale={{ emptyText: continuity ? t('systemStatus.contEmpty') : t('systemStatus.loading') }}
            columns={[
              { title: t('systemStatus.contColSymbol'), dataIndex: 'symbol', key: 'symbol', width: 160, render: v => <b>{v}</b> },
              {
                title: t('systemStatus.contColMissingDays'), dataIndex: 'missing_days', key: 'missing_days', width: 120,
                sorter: (a, b) => a.missing_days - b.missing_days, defaultSortOrder: 'descend',
                render: (v: number) => <Tag color={v > 10 ? 'error' : 'warning'}>{v}</Tag>,
              },
              { title: t('systemStatus.contColFirst'), dataIndex: 'first', key: 'first', width: 130 },
              { title: t('systemStatus.contColLast'), dataIndex: 'last', key: 'last', width: 130 },
            ]}
          />
          <div style={{ fontSize: 12, color: '#8B93A7', marginTop: 12 }}>
            {t('systemStatus.contFooter')}
          </div>
        </div>
      )}

      {/* Tab 3 任务状态 */}
      {tab === 'tasks' && (
        <Table<SystemTask>
          rowKey={r => `${r.trade_date}-${r.task_name}`}
          size="small"
          dataSource={tasks}
          pagination={false}
          columns={[
            { title: t('systemStatus.taskColDate'), dataIndex: 'trade_date', key: 'trade_date', width: 110 },
            { title: t('systemStatus.taskColName'), dataIndex: 'task_name', key: 'task_name', width: 130, render: v => <b>{v}</b> },
            { title: t('systemStatus.taskColPhase'), dataIndex: 'phase', key: 'phase', width: 90, render: v => <Tag color="blue">{v}</Tag> },
            { title: t('systemStatus.taskColDetail'), dataIndex: 'detail', key: 'detail' },
            {
              title: t('systemStatus.taskColStatus'), dataIndex: 'status', key: 'status', width: 120,
              render: (v: SystemTask['status'], r: SystemTask) =>
                v === 'COMPLETED' ? (
                  <Tag color="success">
                    {r.inferred ? t('systemStatus.statusCompletedInferred') : t('systemStatus.statusCompleted')}
                  </Tag>
                )
                  : v === 'MISSING' ? <Tag color="error">{t('systemStatus.statusMissing')}</Tag>
                    : <Tag>{t('systemStatus.statusPending')}</Tag>,
            },
          ]}
        />
      )}

      {/* Tab 4 审计事件 + Trace 查询 */}
      {tab === 'audit' && (
        <div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
            <Input
              placeholder={t('systemStatus.tracePlaceholder')}
              value={traceId}
              onChange={e => { setTraceId(e.target.value); setTrace(null); setTraceError(null); }}
              onPressEnter={queryTrace}
              style={{ maxWidth: 480 }}
              allowClear
            />
            <button
              onClick={queryTrace}
              disabled={!traceId.trim() || traceLoading}
              style={{
                padding: '4px 16px', borderRadius: 4, border: 'none',
                cursor: !traceId.trim() || traceLoading ? 'not-allowed' : 'pointer',
                background: '#5B6CF0', color: '#fff', fontSize: 13,
                opacity: !traceId.trim() || traceLoading ? 0.5 : 1,
              }}
            >
              {traceLoading ? t('systemStatus.querying') : t('systemStatus.query')}
            </button>
          </div>

          {traceError && <div style={{ color: '#F0524D', fontSize: 13, marginBottom: 12 }}>{traceError}</div>}

          {trace && (
            <Card size="small" title={t('systemStatus.traceTitle', { id: traceId.trim(), n: trace.length })} style={{ marginBottom: 16 }}>
              {trace.length === 0 ? (
                <div style={{ color: '#8B93A7', fontSize: 13 }}>{t('systemStatus.noTraceEvents')}</div>
              ) : (
                <Timeline
                  items={trace.map((ev, i) => ({
                    key: i,
                    color: ev.stage === '成交' ? 'green' : ev.stage === '风控' ? 'orange' : 'blue',
                    children: (
                      <div>
                        <Tag color={ev.stage === '成交' ? 'success' : ev.stage === '风控' ? 'warning' : 'processing'}>{ev.stage}</Tag>
                        <b style={{ fontSize: 13 }}>{ev.ts_code}</b>
                        <span style={{ fontSize: 12, color: '#4A5268', marginLeft: 8 }}>{ev.detail}</span>
                        <div style={{ fontSize: 11, color: '#8B93A7', fontFamily: 'monospace' }}>{fmtTime(ev.time)}</div>
                      </div>
                    ),
                  }))}
                />
              )}
            </Card>
          )}

          <Table<AuditEvent>
            rowKey={(r, i) => `${r.time}-${i}`}
            size="small"
            dataSource={audit}
            pagination={{ pageSize: 15, showSizeChanger: false }}
            columns={[
              { title: t('systemStatus.auditColTime'), dataIndex: 'time', key: 'time', width: 170, render: v => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{fmtTime(v)}</span> },
              { title: t('systemStatus.auditColActor'), dataIndex: 'actor', key: 'actor', width: 90, render: v => v === 'SYSTEM' ? <Tag color="purple">{t('systemStatus.actorSystem')}</Tag> : <Tag color="geekblue">{t('systemStatus.actorHuman')}</Tag> },
              { title: t('systemStatus.auditColAction'), dataIndex: 'action', key: 'action', width: 110 },
              { title: t('systemStatus.auditColTarget'), dataIndex: 'target', key: 'target', width: 110, render: v => <b>{v}</b> },
              { title: t('systemStatus.auditColDetail'), dataIndex: 'detail', key: 'detail', render: v => <span style={{ fontSize: 12, color: '#4A5268' }}>{v}</span> },
            ]}
          />
        </div>
      )}
    </div>
  );
}
