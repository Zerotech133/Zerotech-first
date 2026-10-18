import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button,
  Card,
  Col,
  Input,
  Modal,
  Row,
  Segmented,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { api } from '../services/api';
import { useMarket } from '../contexts/MarketContext';
import { useStrategies } from '../hooks/useStrategies';
import SymbolLink from '../components/SymbolLink';
import { useRole } from '../hooks/useRole';

const { Title, Text } = Typography;

/* ───────────────────────── 类型（后端契约） ───────────────────────── */

interface PlanRow {
  plan_id: string;
  candidate_id?: string;
  ts_code: string;
  strategy_version_id: string;
  strategy_name: string;
  plan_status: string;
  ref_price?: number | null;
  stop_loss_price?: number | null;
  take_profit_price?: number | null;
  confidence_level?: number | null;
  risk_result?: string | null;
  risk_rules?: Record<string, { result?: string; note?: string; [k: string]: unknown }> | null;
  created_at?: string;
}

/* ───────────────────────── 常量与格式化 ───────────────────────── */

/** label 为 i18n key（pages 命名空间），渲染处 t(label) */
const STATUS_META: Record<string, { label: string; color: string }> = {
  PENDING: { label: 'plans.statusPending', color: 'orange' },
  APPROVED: { label: 'plans.statusApproved', color: 'green' },
  REJECTED: { label: 'plans.statusRejected', color: 'red' },
  EXPIRED: { label: 'plans.statusExpired', color: 'default' },
  CANCELLED: { label: 'plans.statusCancelled', color: 'default' },
};

const STATUS_ORDER = ['PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', 'CANCELLED'];

/** 风控结论 → 展示（label 为 i18n key；PASS 绿 / WARNING 黄 / BLOCK 红；DB 存 PASS/PASS_WITH_WARNING/REJECT） */
function riskMeta(v?: string | null): { label: string; color: string } | null {
  if (!v) return null;
  if (v === 'PASS') return { label: 'plans.riskPass', color: 'green' };
  if (v === 'PASS_WITH_WARNING' || v === 'WARNING') return { label: 'plans.riskWarning', color: 'gold' };
  if (v === 'REJECT' || v === 'BLOCK') return { label: 'plans.riskBlock', color: 'red' };
  return { label: v, color: 'default' };
}

function num(v: unknown): number {
  const n = typeof v === 'string' ? parseFloat(v) : typeof v === 'number' ? v : NaN;
  return Number.isFinite(n) ? n : 0;
}

function fmtPrice(v: unknown): string {
  return v == null ? '—' : num(v).toFixed(2);
}

function fmtDate(v?: string): string {
  return (v || '').slice(0, 10);
}

function fmtPct(v: unknown): string {
  return v == null ? '—' : `${(num(v) * 100).toFixed(1)}%`;
}

/* ───────────────────────── 风控明细（展开行） ───────────────────────── */

function RuleDetail({ rules }: { rules?: PlanRow['risk_rules'] }) {
  const { t } = useTranslation('pages');
  if (!rules || Object.keys(rules).length === 0) {
    return <Text type="secondary">{t('plans.noRiskRuleDetail')}</Text>;
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {Object.entries(rules).map(([name, detail]) => {
        const result = String(detail?.result ?? '—');
        const meta =
          result === 'PASS' ? { label: t('plans.riskPass'), color: 'green' }
          : result === 'WARNING' ? { label: t('plans.riskWarning'), color: 'gold' }
          : result === 'BLOCK' || result === 'REJECT' ? { label: t('plans.riskBlock'), color: 'red' }
          : { label: result, color: 'default' };
        const extras = Object.entries(detail ?? {})
          .filter(([k]) => k !== 'result')
          .map(([k, v]) => `${k}=${String(v)}`)
          .join('　');
        return (
          <div key={name} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
            <Tag color={meta.color} style={{ marginInlineEnd: 0 }}>{meta.label}</Tag>
            <Text strong style={{ fontFamily: 'monospace', minWidth: 180 }}>{name}</Text>
            {extras && <Text type="secondary">{extras}</Text>}
          </div>
        );
      })}
    </div>
  );
}

/* ───────────────────────── 页面 ───────────────────────── */

export default function PlansPage() {
  const { t } = useTranslation('pages');
  const { canWrite } = useRole();  // VIEWER 只读：隐藏审批/驳回
  const { market } = useMarket();
  const { strategies } = useStrategies();
  const [apiRef, contextHolder] = message.useMessage();

  const [plans, setPlans] = useState<PlanRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>('ALL');
  const [strategy, setStrategy] = useState('');
  const [prevMarket, setPrevMarket] = useState(market);
  const [acting, setActing] = useState<string | null>(null);

  // 拒绝弹窗
  const [rejecting, setRejecting] = useState<PlanRow | null>(null);
  const [reason, setReason] = useState('');
  const [rejectOpen, setRejectOpen] = useState(false);

  // 切市场重置策略筛选
  if (prevMarket !== market) {
    setPrevMarket(market);
    setStrategy('');
  }

  const load = () => {
    const params = new URLSearchParams({ market, limit: '500' });
    if (strategy) params.set('strategy', strategy);
    setLoading(true);
    api<PlanRow[]>(`/plans?${params.toString()}`)
      .then((list) => setPlans(Array.isArray(list) ? list : []))
      .catch(() => setPlans([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, strategy]);

  /* 状态计数（当前筛选集内） */
  const counts = useMemo(() => {
    const m: Record<string, number> = { PENDING: 0, APPROVED: 0, REJECTED: 0, EXPIRED: 0, CANCELLED: 0 };
    plans.forEach((p) => {
      if (m[p.plan_status] != null) m[p.plan_status] += 1;
    });
    return m;
  }, [plans]);

  const filtered = useMemo(
    () => (statusFilter === 'ALL' ? plans : plans.filter((p) => p.plan_status === statusFilter)),
    [plans, statusFilter],
  );

  /* 审批操作 */
  const approve = async (p: PlanRow) => {
    setActing(p.plan_id);
    try {
      await api(`/plans/${p.plan_id}/approve`, {
        method: 'POST',
        body: JSON.stringify({ approver: 'user' }),
      });
      apiRef.open({
        type: 'success',
        content: (
          <span>
            {t('plans.approvedToast1', { code: p.ts_code })}<strong>{t('plans.approvedToastBold')}</strong>{t('plans.approvedToast2')}{' '}
            <a href="/approvals" style={{ fontWeight: 600 }}>{t('plans.approvedToastLink')}</a>
            {' '}{t('plans.approvedToast3')}
          </span>
        ),
        duration: 7,
      });
      load();
    } catch {
      /* api() 已 toast */
    } finally {
      setActing(null);
    }
  };

  const openReject = (p: PlanRow) => {
    setRejecting(p);
    setReason('');
    setRejectOpen(true);
  };

  const submitReject = async () => {
    if (!rejecting) return;
    if (!reason.trim()) {
      apiRef.warning(t('plans.rejectReasonRequired'));
      return;
    }
    setActing(rejecting.plan_id);
    try {
      await api(`/plans/${rejecting.plan_id}/reject`, {
        method: 'POST',
        body: JSON.stringify({ approver: 'user', reason: reason.trim() }),
      });
      apiRef.success(t('plans.rejectedToast', { code: rejecting.ts_code }));
      setRejectOpen(false);
      setRejecting(null);
      setReason('');
      load();
    } catch {
      /* api() 已 toast */
    } finally {
      setActing(null);
    }
  };

  /* ── 表格列 ── */
  const columns: ColumnsType<PlanRow> = [
    {
      title: t('plans.colSymbol'),
      dataIndex: 'ts_code',
      key: 'ts_code',
      render: (v: string) => <SymbolLink code={v} />,
    },
    {
      title: t('plans.colStrategy'),
      dataIndex: 'strategy_name',
      key: 'strategy_name',
      render: (v: string, row) => <Tag style={{ fontSize: 11 }}>{v || row.strategy_version_id}</Tag>,
    },
    {
      title: t('plans.colStatus'),
      dataIndex: 'plan_status',
      key: 'plan_status',
      filters: STATUS_ORDER.map((s) => ({ text: t(STATUS_META[s].label), value: s })),
      onFilter: (v, row) => row.plan_status === v,
      render: (v: string) => {
        const meta = STATUS_META[v] ?? { label: v, color: 'default' };
        return <Tag color={meta.color}>{t(meta.label)}</Tag>;
      },
    },
    { title: t('plans.colRefPrice'), dataIndex: 'ref_price', key: 'ref_price', align: 'right', render: (v) => fmtPrice(v) },
    {
      title: t('plans.colStopLoss'),
      dataIndex: 'stop_loss_price',
      key: 'stop_loss_price',
      align: 'right',
      render: (v) => <span style={{ color: '#F0524D' }}>{fmtPrice(v)}</span>,
    },
    {
      title: t('plans.colTakeProfit'),
      dataIndex: 'take_profit_price',
      key: 'take_profit_price',
      align: 'right',
      render: (v) => <span style={{ color: '#12A150' }}>{fmtPrice(v)}</span>,
    },
    {
      title: t('plans.colConfidence'),
      dataIndex: 'confidence_level',
      key: 'confidence_level',
      align: 'right',
      render: (v) => fmtPct(v),
    },
    {
      title: t('plans.colRiskResult'),
      dataIndex: 'risk_result',
      key: 'risk_result',
      render: (v) => {
        const meta = riskMeta(v);
        return meta ? <Tag color={meta.color}>{t(meta.label)}</Tag> : <span style={{ color: '#8B93A7' }}>—</span>;
      },
    },
    {
      title: t('plans.colCreatedAt'),
      dataIndex: 'created_at',
      key: 'created_at',
      render: (v: string) => <span style={{ color: '#8B93A7' }}>{fmtDate(v)}</span>,
    },
    {
      title: t('plans.colActions'),
      key: 'actions',
      render: (_, row) =>
        canWrite && row.plan_status === 'PENDING' ? (
          <Space size={4}>
            <Button
              size="small"
              type="primary"
              loading={acting === row.plan_id}
              onClick={() => approve(row)}
            >
              {t('plans.approve')}
            </Button>
            <Button
              size="small"
              danger
              disabled={acting === row.plan_id}
              onClick={() => openReject(row)}
            >
              {t('plans.reject')}
            </Button>
          </Space>
        ) : null,
    },
  ];

  const segOptions = [
    { label: `${t('plans.segAll')} ${plans.length}`, value: 'ALL' },
    ...STATUS_ORDER.map((s) => ({
      label: `${t(STATUS_META[s].label)} ${counts[s]}`,
      value: s,
    })),
  ];

  return (
    <div>
      {contextHolder}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <Title level={4} style={{ margin: 0 }}>{t('plans.title')}</Title>
        <Select
          size="small"
          style={{ minWidth: 180 }}
          placeholder={t('plans.allStrategies')}
          allowClear
          value={strategy || undefined}
          onChange={(v) => setStrategy(v || '')}
          options={strategies.map((s) => ({ label: s, value: s }))}
        />
        <Button size="small" onClick={load} loading={loading}>{t('plans.refresh')}</Button>
      </div>

      {/* 统计条：各状态计数卡 */}
      <Row gutter={12} style={{ marginBottom: 16 }}>
        {STATUS_ORDER.map((s) => (
          <Col key={s} xs={12} sm={8} md={4}>
            <Card size="small">
              <Statistic
                title={t(STATUS_META[s].label)}
                value={counts[s]}
                styles={{ content: {
                  fontSize: 20,
                  color: s === 'PENDING' ? '#F6A609' : s === 'APPROVED' ? '#12A150' : s === 'REJECTED' ? '#F0524D' : '#8B93A7',
                } }}
              />
            </Card>
          </Col>
        ))}
      </Row>

      <div style={{ marginBottom: 12 }}>
        <Segmented options={segOptions} value={statusFilter} onChange={(v) => setStatusFilter(v as string)} />
      </div>

      <Table<PlanRow>
        rowKey="plan_id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={filtered}
        pagination={{ pageSize: 50, showSizeChanger: false }}
        expandable={{
          expandedRowRender: (row) => (
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>{t('plans.riskRuleDetailTitle', { id: row.plan_id })}</Text>
              <div style={{ marginTop: 8 }}>
                <RuleDetail rules={row.risk_rules} />
              </div>
            </div>
          ),
          rowExpandable: (row) => !!row.risk_rules && Object.keys(row.risk_rules).length > 0,
        }}
      />

      {/* 拒绝弹窗：原因必填 */}
      <Modal
        open={rejectOpen}
        title={rejecting ? t('plans.rejectModalTitleWith', { code: rejecting.ts_code }) : t('plans.rejectModalTitle')}
        okText={t('plans.confirmReject')}
        okButtonProps={{ danger: true }}
        cancelText={t('common:action.cancel')}
        confirmLoading={acting === rejecting?.plan_id}
        onOk={submitReject}
        onCancel={() => {
          setRejectOpen(false);
          setRejecting(null);
          setReason('');
        }}
      >
        <div style={{ marginBottom: 8 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>{t('plans.rejectReasonLabel')}</Text>
        </div>
        <Input.TextArea
          rows={3}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder={t('plans.rejectReasonPlaceholder')}
        />
      </Modal>
    </div>
  );
}
