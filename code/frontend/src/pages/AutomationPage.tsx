import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Button,
  Card,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { api } from '../services/api';
import { useAutoRefresh } from '../hooks/useAutoRefresh';
import { useRole } from '../hooks/useRole';

const { Title, Text } = Typography;

/* ───────────────────────── 类型（后端契约） ───────────────────────── */

interface ActionPerm {
  action: string;
  label: string;
  default_permission: string;
  configured_permission: string;
  effective_permission: string;
}

interface LogEntry {
  time: string;
  event: string;
  reason: string | null;
}

interface BreakerStatus {
  active: boolean;
  triggered_at: string | null;
  reason: string | null;
  actions: ActionPerm[];
  log: LogEntry[];
}

interface Rule {
  name: string;
  description: string;
  enabled: boolean;
}

/* ───────────────────────── 常量 ───────────────────────── */

const PERM_META: Record<string, { labelKey: string; color: string }> = {
  AUTO: { labelKey: 'automation.permAuto', color: 'green' },
  CONFIRM: { labelKey: 'automation.permConfirm', color: 'gold' },
  BLOCKED: { labelKey: 'automation.permBlocked', color: 'red' },
};

function fmtTime(v?: string | null): string {
  return (v || '').slice(0, 19).replace('T', ' ');
}

/* ───────────────────────── 页面 ───────────────────────── */

export default function AutomationPage() {
  const { t } = useTranslation('pages');
  const { canWrite } = useRole();  // VIEWER 只读：隐藏触发/重置/运行规则/权限调整
  const permLabel = (p: string) => (PERM_META[p]?.labelKey ? t(PERM_META[p].labelKey) : p);
  const PERM_OPTIONS = [
    { label: t('automation.permAutoOpt'), value: 'AUTO' },
    { label: t('automation.permConfirmOpt'), value: 'CONFIRM' },
    { label: t('automation.permBlockedOpt'), value: 'BLOCKED' },
  ];
  const [apiRef, contextHolder] = message.useMessage();

  const [status, setStatus] = useState<BreakerStatus | null>(null);
  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(false);
  const [runResult, setRunResult] = useState('');

  // 触发熔断弹窗
  const [triggerOpen, setTriggerOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // 权限切换中的动作
  const [changing, setChanging] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    api<BreakerStatus>('/circuit-breaker/status')
      .then(setStatus)
      .catch(() => setStatus(null))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    api<Rule[]>('/automation/rules').then(setRules).catch(() => setRules([]));
  }, []);

  // 30s 自动刷新状态（页面不可见时暂停）
  useAutoRefresh(load, 30000);

  const submitTrigger = async () => {
    if (!reason.trim()) return;
    setSubmitting(true);
    try {
      const s = await api<BreakerStatus>('/circuit-breaker/trigger', {
        method: 'POST',
        body: JSON.stringify({ reason: reason.trim() }),
      });
      setStatus(s);
      apiRef.error(t('automation.msgTriggered'));
      setTriggerOpen(false);
      setReason('');
    } catch {
      /* api() 已 toast */
    } finally {
      setSubmitting(false);
    }
  };

  const doReset = async () => {
    try {
      const s = await api<BreakerStatus>('/circuit-breaker/reset', { method: 'POST' });
      setStatus(s);
      apiRef.success(t('automation.msgReset'));
    } catch {
      /* api() 已 toast */
    }
  };

  const changePermission = async (action: string, permission: string) => {
    setChanging(action);
    try {
      const s = await api<BreakerStatus>(`/circuit-breaker/actions/${action}/permission`, {
        method: 'POST',
        body: JSON.stringify({ permission }),
      });
      setStatus(s);
      apiRef.success(t('automation.msgPermChanged', { action, perm: permLabel(permission) }));
    } catch {
      /* api() 已 toast */
    } finally {
      setChanging(null);
    }
  };

  const runRules = async () => {
    try {
      const data = await api<{ rules: unknown[] }>('/automation/run', { method: 'POST' });
      setRunResult(JSON.stringify(data));
    } catch {
      /* api() 已 toast */
    }
  };

  const active = status?.active ?? false;
  const actions = status?.actions ?? [];

  /* ── 权限矩阵列 ── */
  const columns: ColumnsType<ActionPerm> = [
    {
      title: t('automation.colAction'),
      dataIndex: 'action',
      key: 'action',
      render: (v: string) => <b style={{ fontFamily: 'monospace' }}>{v}</b>,
    },
    {
      title: t('automation.colDesc'),
      dataIndex: 'label',
      key: 'label',
      responsive: ['md'],
    },
    {
      title: t('automation.colPerm'),
      dataIndex: 'effective_permission',
      key: 'effective_permission',
      width: 130,
      render: (v: string, row) => {
        const meta = PERM_META[v] ?? { labelKey: '', color: 'default' };
        const label = meta.labelKey ? t(meta.labelKey) : v;
        // 熔断激活时 AUTO 冻结显示 BLOCKED，Tooltip 提示原级别
        if (active && row.configured_permission === 'AUTO' && v === 'BLOCKED') {
          return (
            <Tooltip title={t('automation.frozenTip', { perm: row.configured_permission })}>
              <Tag color={meta.color}>{label}</Tag>
            </Tooltip>
          );
        }
        return <Tag color={meta.color}>{label}</Tag>;
      },
    },
    {
      title: t('automation.colPermAdjust'),
      key: 'permission',
      width: 220,
      render: (_, row) => (
        <Select
          size="small"
          style={{ minWidth: 200 }}
          value={row.configured_permission}
          options={PERM_OPTIONS}
          loading={changing === row.action}
          disabled={changing === row.action}
          onChange={(v) => changePermission(row.action, v)}
        />
      ),
    },
  ];

  return (
    <div>
      {contextHolder}

      {/* ── 标题 + 熔断状态大标签 ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <Title level={4} style={{ margin: 0 }}>{t('automation.title')}</Title>
        <Tag
          color={active ? 'red' : 'green'}
          style={{ fontSize: 13, padding: '2px 10px', marginInlineEnd: 0 }}
        >
          {active ? t('automation.statusTriggered') : t('automation.statusNormal')}
        </Tag>
        <Button size="small" onClick={load} loading={loading}>{t('automation.refresh')}</Button>
      </div>

      {active && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          title={t('automation.alertMsg')}
          description={
            <Space orientation="vertical" size={2}>
              <Text>{t('automation.timeLabel')}{fmtTime(status?.triggered_at)}</Text>
              <Text>{t('automation.reasonLabel')}{status?.reason}</Text>
            </Space>
          }
        />
      )}

      {/* ── 熔断操作 ── */}
      <Card size="small" title={t('automation.cardBreakerOps')} style={{ marginBottom: 16 }}>
        <Space size={12} wrap>
          {canWrite && (
            <Button danger type="primary" onClick={() => setTriggerOpen(true)} disabled={active}>
              {t('automation.btnTrigger')}
            </Button>
          )}
          {canWrite && (
            <Popconfirm
              title={t('automation.confirmResetTitle')}
              description={t('automation.confirmResetDesc')}
              okText={t('automation.okReset')}
              cancelText={t('common:action.cancel')}
              onConfirm={doReset}
              disabled={!active}
            >
              <Button type="primary" disabled={!active}>
                {t('automation.btnReset')}
              </Button>
            </Popconfirm>
          )}
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('automation.actionCount', { n: actions.length })}
          </Text>
        </Space>
      </Card>

      {/* ── 动作权限矩阵 ── */}
      <Card size="small" title={t('automation.cardPermMatrix')} style={{ marginBottom: 16 }}>
        <Table<ActionPerm>
          rowKey="action"
          size="small"
          loading={loading}
          columns={canWrite ? columns : columns.filter((c) => c.key !== 'permission')}
          dataSource={actions}
          pagination={false}
        />
      </Card>

      {/* ── 触发日志 + 自动化规则（并列区块） ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 16 }}>
        <Card size="small" title={t('automation.cardLog')}>
          {(status?.log ?? []).length === 0 ? (
            <Text type="secondary">{t('automation.logEmpty')}</Text>
          ) : (
            <Timeline
              items={(status?.log ?? []).map((entry, i) => ({
                key: i,
                color: entry.event === 'TRIGGER' ? 'red' : entry.event === 'RESET' ? 'green' : 'blue',
                children: (
                  <div style={{ fontSize: 12 }}>
                    <Text strong>{entry.event}</Text>
                    {entry.reason ? <Text> — {entry.reason}</Text> : null}
                    <div>
                      <Text type="secondary" style={{ fontFamily: 'monospace' }}>
                        {fmtTime(entry.time)}
                      </Text>
                    </div>
                  </div>
                ),
              }))}
            />
          )}
        </Card>

        <Card
          size="small"
          title={t('automation.cardRules')}
          extra={canWrite ? <Button size="small" onClick={runRules}>{t('automation.btnRunRules')}</Button> : null}
        >
          <Table<Rule>
            rowKey="name"
            size="small"
            columns={[
              { title: t('automation.colRule'), dataIndex: 'name', key: 'name' },
              { title: t('automation.colDesc'), dataIndex: 'description', key: 'description' },
              {
                title: t('automation.colStatus'),
                dataIndex: 'enabled',
                key: 'enabled',
                width: 80,
                render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? t('automation.statusEnabled') : t('automation.statusDisabled')}</Tag>,
              },
            ]}
            dataSource={rules}
            pagination={false}
          />
          {runResult && (
            <pre style={{ marginTop: 8, fontSize: 11, maxHeight: 160, overflow: 'auto' }}>{runResult}</pre>
          )}
        </Card>
      </div>

      {/* ── 触发熔断弹窗（原因必填） ── */}
      <Modal
        open={triggerOpen}
        title={t('automation.btnTrigger')}
        okText={t('automation.okTrigger')}
        okButtonProps={{ danger: true, disabled: !reason.trim() }}
        cancelText={t('common:action.cancel')}
        confirmLoading={submitting}
        onOk={submitTrigger}
        onCancel={() => {
          setTriggerOpen(false);
          setReason('');
        }}
      >
        <p style={{ marginBottom: 8 }}>
          {t('automation.modalDescBefore')}<strong>{t('automation.modalDescStrong')}</strong>{t('automation.modalDescAfter')}
        </p>
        <Input.TextArea
          rows={3}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder={t('automation.modalReasonPlaceholder')}
        />
      </Modal>
    </div>
  );
}
