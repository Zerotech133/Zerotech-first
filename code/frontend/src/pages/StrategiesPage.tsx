import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import {
  Button, Drawer, Form, Input, Modal, Popconfirm, Select,
  Space, Table, Tag, Typography, message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { TFunction } from 'i18next';
import { api } from '../services/api';
import { MARKETS, useMarket } from '../contexts/MarketContext';
import { strategyLabel } from '../i18n/strategyName';
import { useRole } from '../hooks/useRole';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

/* ───────── 类型（后端契约） ───────── */
interface VersionBrief {
  strategy_version_id: string;
  version_no: string;
  status: string;
}
interface StrategyRow {
  strategy_id: string;
  strategy_name: string;
  description: string;
  market: string;
  strategy_type: string;
  status: string;
  is_builtin: boolean;
  editable: boolean;
  research_owner?: string | null;
  lifecycle_status?: string | null;
  version_count: number;
  versions: VersionBrief[];
}
interface CodeResp {
  kind: 'python' | 'spec';
  editable: boolean;
  content: string;
  strategy_type: string;
  strategy_version_id: string | null;
}

const STATUS_META: Record<string, { labelKey: string; color: string }> = {
  ACTIVE: { labelKey: 'strategies.statusActive', color: 'green' },
  STAGING: { labelKey: 'strategies.statusStaging', color: 'blue' },
  DRAFT: { labelKey: 'strategies.statusDraft', color: 'default' },
  PENDING_REVIEW: { labelKey: 'strategies.statusPendingReview', color: 'gold' },
  REJECTED: { labelKey: 'strategies.statusRejected', color: 'red' },
  DISABLED: { labelKey: 'strategies.statusDisabled', color: 'default' },
};
const STATUS_OPTIONS = ['ACTIVE', 'STAGING', 'DISABLED', 'PENDING_REVIEW', 'DRAFT', 'REJECTED'];
const CREATE_TYPES = ['V2_EXTERNAL', 'VIBE_EXTERNAL'];

const DEFAULT_SPEC = JSON.stringify({
  spec_version: 1,
  market: 'CN',
  universe: { codes: ['600519.SH'] },
  factors: [{ name: 'momentum', params: { window: 20 } }],
  rules: [{ feature: 'momentum', op: '>', value: 0.02, action: 'enter' }],
  risk: { stop_loss: 0.08, take_profit: 0.2, max_position_ratio: 0.1 },
  execution: { frequency: 'daily', direction: 'long', max_orders_per_day: 10 },
}, null, 2);

function statusTag(status: string, t: TFunction) {
  const m = STATUS_META[status];
  return <Tag color={m?.color ?? 'default'}>{m ? t(m.labelKey) : status}</Tag>;
}

export default function StrategiesPage() {
  const { t } = useTranslation('pages');
  const { canWrite } = useRole();  // VIEWER 只读：隐藏新建/编辑/删除/保存等写操作入口
  const { market } = useMarket();  // 跟随顶部工作区市场（与全站一致：/cn→CN、/hk→HK…）
  const [rows, setRows] = useState<StrategyRow[]>([]);
  const [loading, setLoading] = useState(false);

  // 编辑抽屉
  const [editing, setEditing] = useState<StrategyRow | null>(null);
  const [code, setCode] = useState<CodeResp | null>(null);
  const [specDraft, setSpecDraft] = useState('');
  const [metaForm] = Form.useForm();
  const [saving, setSaving] = useState(false);

  // 新建
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();

  const load = useCallback(() => {
    setLoading(true);
    api<StrategyRow[]>(`/strategies?market=${market}`)
      .then(setRows)
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [market]);

  useEffect(() => { load(); }, [load]);

  const openEdit = async (row: StrategyRow) => {
    setEditing(row);
    setCode(null);
    setSpecDraft('');
    metaForm.setFieldsValue({
      strategy_name: row.strategy_name,
      description: row.description,
      status: row.status,
    });
    try {
      const c = await api<CodeResp>(`/strategies/${row.strategy_id}/code`);
      setCode(c);
      if (c.kind === 'spec') setSpecDraft(c.content);
    } catch { /* toast 已弹 */ }
  };

  const saveMeta = async () => {
    if (!editing) return;
    const v = await metaForm.validateFields();
    setSaving(true);
    try {
      await api(`/strategies/${editing.strategy_id}`, { method: 'PUT', body: JSON.stringify(v) });
      message.success(t('strategies.msgSaved'));
      setEditing(null);
      load();
    } catch { /* toast 已弹 */ } finally { setSaving(false); }
  };

  const saveSpec = async () => {
    if (!editing || !code?.strategy_version_id) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(specDraft);
    } catch {
      message.error(t('strategies.msgSpecInvalid'));
      return;
    }
    setSaving(true);
    try {
      await api(`/strategies/version/${code.strategy_version_id}/spec`, {
        method: 'PUT', body: JSON.stringify({ spec: parsed }),
      });
      message.success(t('strategies.msgSpecSaved'));
    } catch { /* toast 已弹 */ } finally { setSaving(false); }
  };

  const doDelete = async (row: StrategyRow) => {
    try {
      await api(`/strategies/${row.strategy_id}`, { method: 'DELETE' });
      message.success(t('strategies.msgDeleted'));
      load();
    } catch { /* 409 等 toast 已弹 */ }
  };

  const doCreate = async () => {
    const v = await createForm.validateFields();
    let spec: unknown;
    try {
      spec = JSON.parse(v.spec);
    } catch {
      message.error(t('strategies.msgSpecInvalid'));
      return;
    }
    try {
      await api('/strategies/create', {
        method: 'POST',
        body: JSON.stringify({
          strategy_name: v.strategy_name, market: v.market,
          strategy_type: v.strategy_type, description: v.description ?? '', spec,
        }),
      });
      message.success(t('strategies.msgCreated'));
      setCreateOpen(false);
      createForm.resetFields();
      load();
    } catch { /* toast 已弹 */ }
  };

  const columns: ColumnsType<StrategyRow> = [
    { title: t('strategies.colId'), dataIndex: 'strategy_id', key: 'strategy_id', width: 150,
      render: (v: string) => <Text code style={{ fontSize: 11 }}>{v}</Text> },
    { title: t('strategies.colName'), dataIndex: 'strategy_name', key: 'strategy_name', width: 170,
      render: (v: string) => <b>{strategyLabel(v)}</b> },
    { title: t('strategies.colDesc'), dataIndex: 'description', key: 'description', ellipsis: true,
      render: (v: string) => v || <Text type="secondary">—</Text> },
    { title: t('strategies.colMarket'), dataIndex: 'market', key: 'market', width: 80,
      render: (v: string) => t('market.' + v, { ns: 'common' }) },
    { title: t('strategies.colType'), dataIndex: 'strategy_type', key: 'strategy_type', width: 150,
      render: (v: string, r) => (
        <Space size={4}>
          <Text>{v}</Text>
          {r.is_builtin ? <Tag color="purple">{t('strategies.builtin')}</Tag> : <Tag color="cyan">{t('strategies.external')}</Tag>}
        </Space>
      ) },
    { title: t('strategies.colStatus'), dataIndex: 'status', key: 'status', width: 90, render: (v: string) => statusTag(v, t) },
    { title: t('strategies.colVersion'), dataIndex: 'version_count', key: 'version_count', width: 60 },
    { title: t('strategies.colOwner', { defaultValue: '负责人' }), dataIndex: 'research_owner', key: 'research_owner', width: 100,
      render: (v: string | null) => v || <Text type="secondary">—</Text> },
    { title: t('strategies.colLifecycle', { defaultValue: '生命周期' }), dataIndex: 'lifecycle_status', key: 'lifecycle_status', width: 100,
      render: (v: string | null) => v ? <Tag color={v === 'active' || v === 'live' ? 'green' : v === 'research' ? 'red' : 'blue'}>{v}</Tag> : <Text type="secondary">—</Text> },
    { title: t('strategies.colAction'), key: 'action', width: 180, render: (_: unknown, r) => (
      <Space size={8}>
        {canWrite && r.editable && <a onClick={() => openEdit(r)}>{t('strategies.actEdit')}</a>}
        {r.versions[0] && (
          <Link to={`/strategies/${r.versions[0].strategy_version_id}`}>{t('strategies.actDetail')}</Link>
        )}
        {canWrite && r.editable && (
          <Popconfirm title={t('strategies.confirmDelete', { name: strategyLabel(r.strategy_name) })} okText={t('strategies.actDelete')} cancelText={t('common:action.cancel')}
            okButtonProps={{ danger: true }} onConfirm={() => doDelete(r)}>
            <a style={{ color: '#F0524D' }}>{t('strategies.actDelete')}</a>
          </Popconfirm>
        )}
      </Space>
    ) },
  ];

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>{t('strategies.title')}</h1>
        <Tag color="blue" style={{ fontSize: 13, padding: '2px 10px' }}>
          {t('market.' + market, { ns: 'common' })}（{market}）
        </Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>{t('strategies.hintSwitchMarket')}</Text>
        <div style={{ flex: 1 }} />
        <Button onClick={load}>{t('strategies.refresh')}</Button>
        {canWrite && (
          <Button type="primary"
            onClick={() => { createForm.resetFields(); createForm.setFieldsValue({ market }); setCreateOpen(true); }}>
            {t('strategies.createBtn')}
          </Button>
        )}
      </div>

      <Table<StrategyRow>
        rowKey="strategy_id"
        size="small"
        loading={loading}
        dataSource={rows}
        columns={columns}
        pagination={{ pageSize: 20, showSizeChanger: true }}
      />

      {/* 编辑抽屉 */}
      <Drawer
        title={editing ? t('strategies.drawerEditTitle', { name: strategyLabel(editing.strategy_name) }) : ''}
        size="large"
        open={!!editing}
        onClose={() => setEditing(null)}
        extra={canWrite ? <Button type="primary" loading={saving} onClick={saveMeta}>{t('common:action.save')}</Button> : null}
      >
        {editing && (
          <>
            <Form form={metaForm} layout="vertical">
              <Form.Item label={t('strategies.formId')}>
                <Text code>{editing.strategy_id}</Text>
                <Text type="secondary" style={{ marginLeft: 8 }}>
                  {t('market.' + editing.market, { ns: 'common' })} · {editing.strategy_type}
                  {editing.is_builtin ? t('strategies.parenBuiltin') : t('strategies.parenExternal')}
                </Text>
              </Form.Item>
              <Form.Item label={t('strategies.formName')} name="strategy_name"
                rules={[{ required: true, message: t('strategies.ruleName') }]}>
                <Input maxLength={128} />
              </Form.Item>
              <Form.Item label={t('strategies.formDesc')} name="description">
                <TextArea rows={2} maxLength={512} placeholder={t('strategies.phDesc')} />
              </Form.Item>
              <Form.Item label={t('strategies.formStatus')} name="status">
                <Select options={STATUS_OPTIONS.map((s) => ({
                  value: s, label: `${STATUS_META[s] ? t(STATUS_META[s].labelKey) : s}（${s}）` }))} />
              </Form.Item>
            </Form>

            <Typography.Title level={5} style={{ marginTop: 8 }}>
              {t('strategies.codeTitle')} {code ? (code.kind === 'python' ? t('strategies.codePythonRO') : t('strategies.codeSpecEditable')) : ''}
            </Typography.Title>
            {!code && <Text type="secondary">{t('strategies.loading')}</Text>}
            {code?.kind === 'python' && (
              <pre style={{
                background: '#0F1117', color: '#C5CBD8', padding: 12, borderRadius: 6,
                fontSize: 12, maxHeight: 360, overflow: 'auto', margin: 0,
              }}>{code.content}</pre>
            )}
            {code?.kind === 'spec' && (
              <>
                <TextArea value={specDraft} onChange={(e) => setSpecDraft(e.target.value)}
                  rows={16} style={{ fontFamily: 'monospace', fontSize: 12 }} />
                <div style={{ marginTop: 8 }}>
                  {canWrite && (
                    <Button type="primary" ghost loading={saving} onClick={saveSpec}
                      disabled={!code.strategy_version_id}>{t('strategies.saveSpecBtn')}</Button>
                  )}
                  {!code.strategy_version_id && (
                    <Text type="secondary" style={{ marginLeft: 8 }}>{t('strategies.noVersionHint')}</Text>
                  )}
                </div>
              </>
            )}
          </>
        )}
      </Drawer>

      {/* 新建 Modal（VIEWER 隐藏） */}
      {canWrite && (
      <Modal title={t('strategies.createTitle')} open={createOpen} onOk={doCreate}
        onCancel={() => setCreateOpen(false)} okText={t('strategies.createOk')} cancelText={t('common:action.cancel')} width={600}>
        <Form form={createForm} layout="vertical"
          initialValues={{ market: 'CN', strategy_type: 'V2_EXTERNAL', spec: DEFAULT_SPEC }}>
          <Form.Item label={t('strategies.formName')} name="strategy_name"
            rules={[{ required: true, message: t('strategies.ruleName') }]}>
            <Input maxLength={128} placeholder={t('strategies.phNameExample')} />
          </Form.Item>
          <Space size={12} style={{ display: 'flex' }}>
            <Form.Item label={t('strategies.formMarket')} name="market" style={{ flex: 1 }}>
              <Select options={MARKETS.map((m) => ({ value: m, label: t('market.' + m, { ns: 'common' }) }))} />
            </Form.Item>
            <Form.Item label={t('strategies.formType')} name="strategy_type" style={{ flex: 1 }}>
              <Select options={CREATE_TYPES.map((ct) => ({ value: ct, label: ct }))} />
            </Form.Item>
          </Space>
          <Form.Item label={t('strategies.formDesc')} name="description">
            <TextArea rows={2} maxLength={512} />
          </Form.Item>
          <Form.Item label={t('strategies.formSpec')} name="spec"
            rules={[{ required: true, message: t('strategies.ruleSpec') }]}>
            <TextArea rows={12} style={{ fontFamily: 'monospace', fontSize: 12 }} />
          </Form.Item>
          <Paragraph type="secondary" style={{ fontSize: 12 }}>
            {t('strategies.createHint')}
          </Paragraph>
        </Form>
      </Modal>
      )}
    </div>
  );
}
