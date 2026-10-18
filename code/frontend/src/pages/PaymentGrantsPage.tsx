// C-7 管理员支付发放管理页：发放队列列表（筛 grant_status/user_id）+ 手动重试 + 历史歧义复核。
// 只读列表接 AbortController（epoch 防迟到响应）；写操作用 newIdempotencyKey（防重复提交）+
// AdminApiError（retryable 才本地保留重试语义，HTTP 错误由 api() 统一 toast）。actor 取自 JWT，
// 请求体绝不含 actor/user_id。
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button, Input, Modal, Radio, Select, Space, Table, Tag, Tooltip, Typography, message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  listPaymentGrants,
  retryGrant,
  reviewGrant,
  type PaymentGrantOrder,
  type ReviewDecision,
} from '../services/adminPaymentGrantsApi';
import { newIdempotencyKey, type AdminApiError } from '../components/admin-entitlements/entitlementModel';

const { Text } = Typography;
const PAGE_SIZE = 20;

// 发放态 → Tag 颜色（与自助收银台三态语义一致，管理面覆盖全状态机）。
const STATUS_COLOR: Record<string, string> = {
  granted: 'green',
  failed: 'red',
  manual_review: 'orange',
  processing: 'blue',
  retrying: 'gold',
  pending: 'default',
};

function fmt(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function PaymentGrantsPage() {
  const { t } = useTranslation('pages');
  const [data, setData] = useState<PaymentGrantOrder[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [userFilter, setUserFilter] = useState('');
  const [appliedUser, setAppliedUser] = useState('');
  const epochRef = useRef(0);

  const reload = useCallback(async () => {
    const epoch = ++epochRef.current;
    const ac = new AbortController();
    setLoading(true);
    try {
      const res = await listPaymentGrants(
        { page, pageSize: PAGE_SIZE, grantStatus: statusFilter, userId: appliedUser },
        ac.signal,
      );
      if (epoch !== epochRef.current) return; // 迟到响应丢弃
      setData(res.items);
      setTotal(res.total);
    } catch {
      if (epoch === epochRef.current) setData([]); // 错误由 api() toast；不投影
    } finally {
      if (epoch === epochRef.current) setLoading(false);
    }
  }, [page, statusFilter, appliedUser]);

  useEffect(() => {
    reload();
  }, [reload]);

  // ── 重试 Modal ──
  const [retryTarget, setRetryTarget] = useState<PaymentGrantOrder | null>(null);
  const [retryReason, setRetryReason] = useState('');
  const [retryBusy, setRetryBusy] = useState(false);

  async function submitRetry() {
    if (!retryTarget) return;
    if (!retryReason.trim()) {
      message.error(t('paymentGrants.reasonRequired'));
      return;
    }
    setRetryBusy(true);
    try {
      const res = await retryGrant(retryTarget.order_id, { reason: retryReason.trim() }, newIdempotencyKey());
      message.success(t('paymentGrants.retryResult', { status: res.status }));
      setRetryTarget(null);
      setRetryReason('');
      await reload();
    } catch (e) {
      const err = e as AdminApiError;
      if (!err?.retryable) setRetryTarget(null); // 已知失败关窗（api 已 toast）；未知结果保留
    } finally {
      setRetryBusy(false);
    }
  }

  // ── 复核 Modal ──
  const [reviewTarget, setReviewTarget] = useState<PaymentGrantOrder | null>(null);
  const [reviewDecision, setReviewDecision] = useState<ReviewDecision>('RETRY');
  const [reviewReason, setReviewReason] = useState('');
  const [reviewBusy, setReviewBusy] = useState(false);

  async function submitReview() {
    if (!reviewTarget) return;
    if (!reviewReason.trim()) {
      message.error(t('paymentGrants.reasonRequired'));
      return;
    }
    setReviewBusy(true);
    try {
      const res = await reviewGrant(
        reviewTarget.order_id,
        { decision: reviewDecision, reason: reviewReason.trim() },
        newIdempotencyKey(),
      );
      message.success(t('paymentGrants.reviewResult', { status: res.status }));
      setReviewTarget(null);
      setReviewReason('');
      await reload();
    } catch (e) {
      const err = e as AdminApiError;
      if (!err?.retryable) setReviewTarget(null);
    } finally {
      setReviewBusy(false);
    }
  }

  const columns: ColumnsType<PaymentGrantOrder> = [
    {
      title: t('paymentGrants.colOrder'), dataIndex: 'order_id', key: 'order_id',
      render: (v: string) => <Text copyable={{ text: v }} style={{ fontSize: 12 }}>{v}</Text>,
    },
    { title: t('paymentGrants.colUser'), dataIndex: 'user_id', key: 'user_id' },
    {
      title: t('paymentGrants.colProduct'), key: 'product',
      render: (_: unknown, r: PaymentGrantOrder) => (
        <span>
          {r.product_type === 'membership' ? t('paymentGrants.member') : t('paymentGrants.subscription')}
          {r.product_name_snapshot ? `（${r.product_name_snapshot}）` : ''}
          {r.market_snapshot ? ` · ${r.market_snapshot}` : ''}
        </span>
      ),
    },
    { title: t('paymentGrants.colAmount'), dataIndex: 'amount_usdt', key: 'amount_usdt' },
    {
      title: t('paymentGrants.colStatus'), key: 'grant_status',
      render: (_: unknown, r: PaymentGrantOrder) => (
        <Space size={4}>
          <Tag color={STATUS_COLOR[r.grant_status] ?? 'default'}>
            {t(`paymentGrants.status_${r.grant_status}`, r.grant_status)}
          </Tag>
          {r.historical_grant_ambiguous && (
            <Tag color="volcano">{t('paymentGrants.ambiguous')}</Tag>
          )}
        </Space>
      ),
    },
    {
      title: t('paymentGrants.colAttempts'), key: 'attempts',
      render: (_: unknown, r: PaymentGrantOrder) =>
        t('paymentGrants.attemptsFmt', { total: r.grant_attempts, auto: r.automatic_grant_attempts }),
    },
    {
      title: t('paymentGrants.colNextRetry'), key: 'next_retry',
      render: (_: unknown, r: PaymentGrantOrder) => fmt(r.next_grant_retry_at),
    },
    {
      title: t('paymentGrants.colError'), key: 'error',
      render: (_: unknown, r: PaymentGrantOrder) =>
        r.last_grant_error_code ? (
          <Tooltip title={r.last_grant_error_summary ?? ''}>
            <Text type="danger" style={{ fontSize: 12 }}>{r.last_grant_error_code}</Text>
          </Tooltip>
        ) : '—',
    },
    {
      title: t('paymentGrants.colPaidAt'), key: 'paid_at',
      render: (_: unknown, r: PaymentGrantOrder) => fmt(r.paid_at),
    },
    {
      title: t('paymentGrants.colAction'), key: 'action', fixed: 'right',
      render: (_: unknown, r: PaymentGrantOrder) => {
        if (r.historical_grant_ambiguous) {
          return (
            <Button size="small" onClick={() => { setReviewTarget(r); setReviewDecision('RETRY'); setReviewReason(''); }}>
              {t('paymentGrants.review')}
            </Button>
          );
        }
        if (r.grant_status === 'granted') return <Text type="secondary">{t('paymentGrants.status_granted')}</Text>;
        if (r.grant_status === 'processing') return <Text type="secondary">{t('paymentGrants.inProgress')}</Text>;
        return (
          <Button size="small" type="primary" ghost onClick={() => { setRetryTarget(r); setRetryReason(''); }}>
            {t('paymentGrants.retry')}
          </Button>
        );
      },
    },
  ];

  const STATUS_OPTIONS = ['pending', 'retrying', 'processing', 'failed', 'manual_review', 'granted'];

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          allowClear
          style={{ width: 200 }}
          placeholder={t('paymentGrants.filterAll')}
          value={statusFilter}
          onChange={(v) => { setStatusFilter(v); setPage(1); }}
          options={STATUS_OPTIONS.map((s) => ({ value: s, label: t(`paymentGrants.status_${s}`, s) }))}
        />
        <Input
          style={{ width: 220 }}
          placeholder={t('paymentGrants.filterUser')}
          value={userFilter}
          allowClear
          onChange={(e) => setUserFilter(e.target.value)}
          onPressEnter={() => { setAppliedUser(userFilter); setPage(1); }}
        />
        <Button onClick={() => { setAppliedUser(userFilter); setPage(1); }}>
          {t('paymentGrants.search')}
        </Button>
        <Button onClick={() => reload()}>{t('paymentGrants.refresh')}</Button>
      </Space>

      <Table<PaymentGrantOrder>
        rowKey="order_id"
        columns={columns}
        dataSource={data}
        loading={loading}
        scroll={{ x: 1100 }}
        locale={{ emptyText: t('paymentGrants.empty') }}
        pagination={{
          current: page, pageSize: PAGE_SIZE, total,
          showSizeChanger: false,
          onChange: (p) => setPage(p),
        }}
      />

      <Modal
        title={t('paymentGrants.retryTitle')}
        open={!!retryTarget}
        onOk={submitRetry}
        confirmLoading={retryBusy}
        onCancel={() => { setRetryTarget(null); setRetryReason(''); }}
        okText={t('paymentGrants.retry')}
      >
        <p><Text type="secondary">{retryTarget?.order_id}</Text></p>
        <Input.TextArea
          placeholder={t('paymentGrants.reason')}
          value={retryReason}
          onChange={(e) => setRetryReason(e.target.value)}
          maxLength={500}
          rows={3}
        />
      </Modal>

      <Modal
        title={t('paymentGrants.reviewTitle')}
        open={!!reviewTarget}
        onOk={submitReview}
        confirmLoading={reviewBusy}
        onCancel={() => { setReviewTarget(null); setReviewReason(''); }}
        okText={t('paymentGrants.review')}
      >
        <p><Text type="secondary">{reviewTarget?.order_id}</Text></p>
        <Radio.Group
          value={reviewDecision}
          onChange={(e) => setReviewDecision(e.target.value)}
          style={{ marginBottom: 12 }}
        >
          <Radio value="RETRY">{t('paymentGrants.decisionRetry')}</Radio>
          <Radio value="MARK_GRANTED">{t('paymentGrants.decisionMarkGranted')}</Radio>
        </Radio.Group>
        <Input.TextArea
          placeholder={t('paymentGrants.reason')}
          value={reviewReason}
          onChange={(e) => setReviewReason(e.target.value)}
          maxLength={500}
          rows={3}
        />
      </Modal>
    </div>
  );
}
