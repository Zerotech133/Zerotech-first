import { useEffect, useState, useCallback } from 'react';
import { Card, Table, Tag, Button, Space, Steps, Segmented, Spin, message, Select } from 'antd';
import { ArrowRightOutlined, CheckOutlined, CloseOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';

import { api } from '../services/api';
import { useMarket } from '../contexts/MarketContext';
import { useStrategies } from '../hooks/useStrategies';
import { useAutoRefresh } from '../hooks/useAutoRefresh';
import SymbolLink from '../components/SymbolLink';
import { useRole } from '../hooks/useRole';

interface StageInfo {
  done: boolean;
  [k: string]: unknown;
}
interface Decision {
  ts_code: string;
  strategy_version_id: string;
  strategy_name: string;
  market: string;
  stage: string;
  stages: {
    candidate: StageInfo & { score?: number | null; reason?: string | null };
    plan: StageInfo & { plan_id?: string | null };
    risk: StageInfo & { result?: string | null };
    approval: StageInfo & { status?: string | null; request_id?: string | null };
    position: StageInfo & { qty?: number | null; open_price?: number | null; float_pnl?: number | null; status?: string | null };
    replay: StageInfo & { pnl?: number | null; outcome?: string | null };
  };
  attention: boolean;
}
interface Funnel {
  market: string;
  candidate: number;
  plan: number;
  approval_pending: number;
  position: number;
  replay: number;
}

const STAGE_ORDER = ['candidate', 'plan', 'risk', 'approval', 'position', 'replay'] as const;
const STAGE_LABEL_KEYS: Record<string, string> = {
  candidate: 'pipeline.stageCandidate', plan: 'pipeline.stagePlan', risk: 'pipeline.stageRisk',
  approval: 'pipeline.stageApproval', position: 'pipeline.stagePosition', replay: 'pipeline.stageReplay',
};

const FUNNEL_STEPS: { key: keyof Omit<Funnel, 'market'>; labelKey: string; color: string }[] = [
  { key: 'candidate', labelKey: 'pipeline.stageCandidate', color: '#5B6CF0' },
  { key: 'plan', labelKey: 'pipeline.stagePlan', color: '#7C89F5' },
  { key: 'approval_pending', labelKey: 'pipeline.pendingApproval', color: '#F6A609' },
  { key: 'position', labelKey: 'pipeline.stagePosition', color: '#12A150' },
  { key: 'replay', labelKey: 'pipeline.stageReplay', color: '#8B93A7' },
];

/** 6 段进度条 */
function StageBar({ decision }: { decision: Decision }) {
  const { t } = useTranslation('pages');
  const currentIdx = STAGE_ORDER.indexOf(decision.stage as typeof STAGE_ORDER[number]);
  return (
    <div style={{ display: 'flex', gap: 3, minWidth: 300 }}>
      {STAGE_ORDER.map((s, i) => {
        const info = decision.stages[s];
        const isPendingApproval = s === 'approval' && info.status === 'PENDING';
        let color = '#E8EAF0'; // todo
        if (info.done) color = '#12A150';
        else if (isPendingApproval) color = '#F6A609';
        else if (i === currentIdx) color = '#5B6CF0';
        return (
          <div key={s} style={{ flex: 1, textAlign: 'center' }}>
            <div style={{ height: 5, borderRadius: 3, background: color }} />
            <div style={{ fontSize: 10, color: '#8B93A7', marginTop: 3 }}>
              {isPendingApproval ? t('pipeline.pendingApproval') : t(STAGE_LABEL_KEYS[s])}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** 展开的决策时间线 */
function DecisionTimeline({ decision }: { decision: Decision }) {
  const { t } = useTranslation('pages');
  const s = decision.stages;
  const items = [
    {
      title: t('pipeline.tlCandidateTitle'),
      description: s.candidate.done ? (s.candidate.reason || t('pipeline.tlScore', { score: s.candidate.score ?? '—' })) : t('pipeline.tlNotPooled'),
      status: s.candidate.done ? 'finish' : 'wait',
    },
    {
      title: t('pipeline.tlPlanTitle'),
      description: s.plan.done ? t('pipeline.tlPlanDesc', { id: s.plan.plan_id ?? '' }) : t('pipeline.tlNotGenerated'),
      status: s.plan.done ? 'finish' : 'wait',
    },
    {
      title: t('pipeline.tlRiskTitle'),
      description: s.risk.done ? t('pipeline.tlRiskDesc', { result: s.risk.result ?? '' }) : t('pipeline.tlNotChecked'),
      status: s.risk.done ? 'finish' : 'wait',
    },
    {
      title: t('pipeline.tlApprovalTitle'),
      description: s.approval.status === 'PENDING' ? t('pipeline.tlApPending')
        : s.approval.status === 'APPROVED' ? t('pipeline.tlApApproved')
        : s.approval.status === 'REJECTED' ? t('pipeline.tlApRejected') : t('pipeline.tlApNotPushed'),
      status: s.approval.status === 'PENDING' ? 'process'
        : s.approval.done ? 'finish' : 'wait',
    },
    {
      title: t('pipeline.tlPositionTitle'),
      description: s.position.done ? t('pipeline.tlPositionDesc', { qty: s.position.qty ?? '—', price: s.position.open_price?.toFixed(2) ?? '—' }) : t('pipeline.tlNotOpened'),
      status: s.position.done ? 'finish' : 'wait',
    },
    {
      title: t('pipeline.tlReplayTitle'),
      description: s.replay.done ? t('pipeline.tlReplayDesc', { pnl: s.replay.pnl?.toFixed(2) ?? '—', outcome: s.replay.outcome ?? '' }) : t('pipeline.tlNotReplayed'),
      status: s.replay.done ? 'finish' : 'wait',
    },
  ] as const;

  return (
    <div style={{ padding: '12px 24px', background: '#FAFBFD' }}>
      <Steps
        size="small"
        direction="horizontal"
        items={items.map(it => ({ title: it.title, description: it.description, status: it.status as 'finish' | 'wait' | 'process' }))}
      />
    </div>
  );
}

export default function PipelinePage() {
  const { t } = useTranslation('pages');
  const { canWrite } = useRole();  // VIEWER 只读：隐藏批准/驳回
  const { market } = useMarket();
  const { strategies, strategy, setStrategy } = useStrategies();
  const [funnel, setFunnel] = useState<Funnel | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>('all');
  const [acting, setActing] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    api<{ funnel: Funnel; decisions: Decision[] }>(`/pipeline?market=${market}${strategy ? `&strategy=${strategy}` : ''}`)
      .then(r => { setFunnel(r.funnel); setDecisions(r.decisions); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [market, strategy]);

  useEffect(() => { load(); }, [load]);

  // 盘中 30s 自动刷新（静默，不带 loading）
  const loadQuiet = useCallback(() => {
    api<{ funnel: Funnel; decisions: Decision[] }>(`/pipeline?market=${market}${strategy ? `&strategy=${strategy}` : ''}`)
      .then(r => { setFunnel(r.funnel); setDecisions(r.decisions); })
      .catch(() => {});
  }, [market, strategy]);
  useAutoRefresh(loadQuiet, 30000, [market, strategy]);

  const approve = async (requestId: string) => {
    setActing(requestId);
    try {
      await api(`/approvals/${encodeURIComponent(requestId)}/approve`, { method: 'POST', body: JSON.stringify({ actor: 'user' }) });
      message.success(t('pipeline.msgApproved'));
      load();
    } catch {
      message.error(t('pipeline.msgActionFail'));
    } finally { setActing(null); }
  };
  const reject = async (requestId: string) => {
    setActing(requestId);
    try {
      await api(`/approvals/${encodeURIComponent(requestId)}/reject`, { method: 'POST', body: JSON.stringify({ actor: 'user', reason: 'user rejected' }) });
      message.success(t('pipeline.msgRejected'));
      load();
    } catch {
      message.error(t('pipeline.msgActionFail'));
    } finally { setActing(null); }
  };

  const filtered = decisions.filter(d => {
    if (filter === 'attention') return d.attention;
    if (filter === 'position') return d.stages.position?.done;
    if (filter === 'replay') return d.stages.replay?.done;
    return true;
  });

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, margin: 0 }}>{t('pipeline.title', { market: t('market.' + market, { ns: 'common' }) })}</h1>
        <span style={{ color: '#8B93A7', fontSize: 13 }}>{t('pipeline.flow')}</span>
        <div style={{ flex: 1 }} />
        <Select
          size="small"
          placeholder={t('pipeline.allStrategies')}
          allowClear
          style={{ width: 200 }}
          value={strategy || undefined}
          onChange={v => setStrategy(v ?? '')}
          options={strategies.map(s => ({ value: s, label: s }))}
        />
      </div>

      {/* 漏斗 */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          {FUNNEL_STEPS.map((step, i) => {
            const val = funnel ? funnel[step.key] : 0;
            return (
              <div key={step.key} style={{ display: 'flex', alignItems: 'center', flex: 1 }}>
                <div style={{ flex: 1, textAlign: 'center', padding: '6px 4px', background: `${step.color}14`, borderRadius: 8 }}>
                  <div style={{ fontSize: 24, fontWeight: 700, color: step.color }}>{val}</div>
                  <div style={{ fontSize: 12, color: '#4A5268' }}>{t(step.labelKey)}</div>
                </div>
                {i < FUNNEL_STEPS.length - 1 && <ArrowRightOutlined style={{ color: '#C5CBD8', margin: '0 6px' }} />}
              </div>
            );
          })}
        </div>
      </Card>

      {/* 筛选 */}
      <div style={{ marginBottom: 12 }}>
        <Segmented
          value={filter}
          onChange={v => setFilter(v as string)}
          options={[
            { label: t('pipeline.filterAll', { n: decisions.length }), value: 'all' },
            { label: t('pipeline.filterAttention', { n: decisions.filter(d => d.attention).length }), value: 'attention' },
            { label: t('pipeline.filterPosition', { n: decisions.filter(d => d.stages.position?.done).length }), value: 'position' },
            { label: t('pipeline.filterReplay', { n: decisions.filter(d => d.stages.replay?.done).length }), value: 'replay' },
          ]}
        />
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
      ) : (
        <Table<Decision>
          rowKey="ts_code"
          size="small"
          dataSource={filtered}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          expandable={{
            expandedRowRender: (record) => <DecisionTimeline decision={record} />,
          }}
          columns={[
            {
              title: t('pipeline.colSymbol'), dataIndex: 'ts_code', key: 'ts_code', width: 120,
              render: (v: string, d: Decision) => (
                <span>
                  <SymbolLink code={v} />
                  {d.attention && <Tag color="orange" style={{ marginLeft: 6 }}>{t('pipeline.tagAttention')}</Tag>}
                </span>
              ),
            },
            {
              title: t('pipeline.colStrategy'), dataIndex: 'strategy_name', key: 'strategy_name', width: 150,
              render: (v: string) => v && v !== '未分配' ? (
                <Tag color="blue" style={{ fontFamily: 'monospace' }}>{v}</Tag>
              ) : <span style={{ color: '#8B93A7', fontSize: 12 }}>{t('pipeline.unassigned')}</span>,
            },
            {
              title: t('pipeline.colProgress'), key: 'progress', width: 340,
              render: (_: unknown, d: Decision) => <StageBar decision={d} />,
            },
            {
              title: t('pipeline.colStage'), dataIndex: 'stage', key: 'stage', width: 90,
              render: (v: string) => <Tag color="blue">{STAGE_LABEL_KEYS[v] ? t(STAGE_LABEL_KEYS[v]) : v}</Tag>,
            },
            {
              title: t('pipeline.colAction'), key: 'action', width: 160,
              render: (_: unknown, d: Decision) => {
                const ap = d.stages.approval;
                if (canWrite && ap.status === 'PENDING' && ap.request_id) {
                  return (
                    <Space>
                      <Button size="small" type="primary" icon={<CheckOutlined />} loading={acting === ap.request_id}
                        onClick={() => approve(ap.request_id!)}>{t('pipeline.btnApprove')}</Button>
                      <Button size="small" danger icon={<CloseOutlined />} loading={acting === ap.request_id}
                        onClick={() => reject(ap.request_id!)}>{t('pipeline.btnReject')}</Button>
                    </Space>
                  );
                }
                return <span style={{ color: '#C5CBD8', fontSize: 12 }}>—</span>;
              },
            },
          ]}
        />
      )}
    </div>
  );
}
