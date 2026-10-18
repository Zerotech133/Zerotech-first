import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useTranslation } from 'react-i18next';
import { api } from '../services/api';
import { useAutoRefresh } from '../hooks/useAutoRefresh';
import { useMarket } from '../contexts/MarketContext';
import { strategyLabel } from '../i18n/strategyName';

const { Title, Text } = Typography;

/* ───────────────────────── 类型（后端 /model-ops 契约） ───────────────────────── */

interface ModelVersion {
  version: string;
  metrics: Record<string, number | string>;
  status: 'DRAFT' | 'ACTIVE' | 'ARCHIVED';
  created_at: string;
}

interface ModelEntry {
  model_id: string;
  name: string;
  task_type: string;
  task_type_label: string;
  versions: ModelVersion[];
  active_version: string | null;
  created_at: string;
}

interface ExperimentRun {
  experiment_id: string;
  strategy_version_id: string;
  strategy_name: string;
  status: string;
  config: Record<string, unknown>;
  result: Record<string, unknown>;
  created_at: string | null;
}

/* ───────────────────────── 常量 ───────────────────────── */

const TASK_TYPE_META: Record<string, { labelKey: string; color: string }> = {
  decay_detect: { labelKey: 'modelOps.taskDecayDetect', color: 'geekblue' },
  regime_classify: { labelKey: 'modelOps.taskRegimeClassify', color: 'purple' },
  trade_quality: { labelKey: 'modelOps.taskTradeQuality', color: 'cyan' },
};

const VERSION_STATUS_META: Record<string, { labelKey: string; color: string }> = {
  DRAFT: { labelKey: 'modelOps.vsDraft', color: 'default' },
  ACTIVE: { labelKey: 'modelOps.vsActive', color: 'green' },
  ARCHIVED: { labelKey: 'modelOps.vsArchived', color: 'default' },
};

const RUN_STATUS_META: Record<string, { labelKey: string; color: string }> = {
  RUNNING: { labelKey: 'modelOps.rsRunning', color: 'blue' },
  CONCLUDED: { labelKey: 'modelOps.rsConcluded', color: 'default' },
  PENDING: { labelKey: 'modelOps.rsPending', color: 'gold' },
  FAILED: { labelKey: 'modelOps.rsFailed', color: 'red' },
};

function fmtMetrics(m?: Record<string, unknown> | null): string {
  if (!m || Object.keys(m).length === 0) return '—';
  return Object.entries(m)
    .filter(([k]) => k !== 'learning_case_ids')
    .map(([k, v]) => `${k}=${typeof v === 'number' ? v : String(v)}`)
    .join(' · ');
}

function fmtDate(v?: string | null): string {
  if (!v) return '—';
  return v.slice(0, 19).replace('T', ' ');
}

function summary(obj: Record<string, unknown> | null | undefined, keys: string[], max = 80): string {
  const src = obj ?? {};
  const parts: string[] = [];
  for (const k of keys) {
    const v = src[k];
    if (v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && v.length === 0)) {
      parts.push(`${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`);
    }
  }
  const s = parts.join(' · ');
  return s.length > max ? `${s.slice(0, max)}…` : s || '—';
}

/* ───────────────────────── Tab 1：模型注册表 ───────────────────────── */

interface RegisterFormValues {
  model_id: string;
  name: string;
  task_type: string;
}

function RegistryTab() {
  const { t } = useTranslation('pages');
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [regOpen, setRegOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [promoting, setPromoting] = useState<string | null>(null);
  const [form] = Form.useForm<RegisterFormValues>();
  const [messageApi, contextHolder] = message.useMessage();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setModels(await api<ModelEntry[]>('/model-ops/models'));
    } catch {
      /* api() 已 toast */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const submitRegister = async () => {
    const values = await form.validateFields();
    setSubmitting(true);
    try {
      await api<ModelEntry>('/model-ops/models', {
        method: 'POST',
        body: JSON.stringify(values),
      });
      messageApi.success(t('modelOps.msgModelRegistered', { name: values.name }));
      setRegOpen(false);
      form.resetFields();
      load();
    } catch {
      /* api() 已 toast */
    } finally {
      setSubmitting(false);
    }
  };

  const promote = async (modelId: string, version: string) => {
    const key = `${modelId}/${version}`;
    setPromoting(key);
    try {
      const r = await api<{ archived: string | null }>(
        `/model-ops/models/${encodeURIComponent(modelId)}/versions/${encodeURIComponent(version)}/promote`,
        { method: 'POST' },
      );
      messageApi.success(
        r.archived ? t('modelOps.msgPromotedArchived', { version, archived: r.archived }) : t('modelOps.msgPromotedActive', { version }),
      );
      load();
    } catch {
      /* api() 已 toast */
    } finally {
      setPromoting(null);
    }
  };

  const versionColumns = (modelId: string): ColumnsType<ModelVersion> => [
    { title: t('modelOps.colVersion'), dataIndex: 'version', key: 'version', width: 90, render: (v: string) => <Text code>{v}</Text> },
    {
      title: t('modelOps.colMetrics'),
      dataIndex: 'metrics',
      key: 'metrics',
      render: (m: Record<string, number>) => <Text style={{ fontSize: 12 }}>{fmtMetrics(m)}</Text>,
    },
    {
      title: t('modelOps.colStatus'),
      dataIndex: 'status',
      key: 'status',
      width: 110,
      render: (s: string) => {
        const meta = VERSION_STATUS_META[s];
        return <Tag color={meta?.color ?? 'default'}>{meta ? t(meta.labelKey) : s}</Tag>;
      },
    },
    { title: t('modelOps.colCreatedAt'), dataIndex: 'created_at', key: 'created_at', width: 170, render: fmtDate },
    {
      title: t('modelOps.colAction'),
      key: 'action',
      width: 100,
      render: (_, row) => (
        <Button
          size="small"
          type="link"
          disabled={row.status === 'ACTIVE'}
          loading={promoting === `${modelId}/${row.version}`}
          onClick={() => promote(modelId, row.version)}
        >
          {row.status === 'ACTIVE' ? t('modelOps.btnCurrentActive') : t('modelOps.btnPromote')}
        </Button>
      ),
    },
  ];

  return (
    <div>
      {contextHolder}
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Space size={8}>
          <Text strong>{t('modelOps.registeredModels')}</Text>
          <Text type="secondary">{t('modelOps.registeredCount', { n: models.length })}</Text>
        </Space>
        <Space>
          <Button size="small" onClick={load} loading={loading}>{t('modelOps.refresh')}</Button>
          <Button size="small" type="primary" onClick={() => setRegOpen(true)}>{t('modelOps.btnRegister')}</Button>
        </Space>
      </div>

      {models.length === 0 ? (
        <Card size="small">
          <Empty
            description={
              <Space orientation="vertical" size={2}>
                <Text>{t('modelOps.emptyModels')}</Text>
                <Text type="secondary">{t('modelOps.emptyModelsHint')}</Text>
              </Space>
            }
          >
            <Button type="primary" onClick={() => setRegOpen(true)}>{t('modelOps.btnRegisterFirst')}</Button>
          </Empty>
        </Card>
      ) : (
        <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          {models.map((m) => {
            const tt = TASK_TYPE_META[m.task_type];
            const ttLabel = tt ? t(tt.labelKey) : (m.task_type_label || m.task_type);
            return (
              <Card
                key={m.model_id}
                size="small"
                title={
                  <Space size={8} wrap>
                    <Text strong>{m.name}</Text>
                    <Text code style={{ fontSize: 12 }}>{m.model_id}</Text>
                    <Tag color={tt?.color ?? 'default'}>{ttLabel}</Tag>
                    <Text type="secondary">{t('modelOps.versionCount', { n: m.versions.length })}</Text>
                    {m.active_version ? (
                      <Tag color="green">{t('modelOps.activeVersion', { version: m.active_version })}</Tag>
                    ) : (
                      <Tag>{t('modelOps.noActiveVersion')}</Tag>
                    )}
                  </Space>
                }
              >
                <Table<ModelVersion>
                  rowKey="version"
                  size="small"
                  columns={versionColumns(m.model_id)}
                  dataSource={m.versions}
                  pagination={false}
                  locale={{ emptyText: t('modelOps.emptyVersions') }}
                />
              </Card>
            );
          })}
        </Space>
      )}

      <Modal
        open={regOpen}
        title={t('modelOps.modalRegisterTitle')}
        okText={t('modelOps.modalRegisterOk')}
        cancelText={t('common:action.cancel')}
        confirmLoading={submitting}
        onOk={submitRegister}
        onCancel={() => { setRegOpen(false); form.resetFields(); }}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            name="model_id"
            label={t('modelOps.formModelId')}
            rules={[{ required: true, message: t('modelOps.ruleModelId') }, { pattern: /^[\w.-]+$/, message: t('modelOps.ruleModelIdPattern') }]}
          >
            <Input placeholder={t('modelOps.phModelId')} />
          </Form.Item>
          <Form.Item name="name" label={t('modelOps.formModelName')} rules={[{ required: true, message: t('modelOps.ruleName') }]}>
            <Input placeholder={t('modelOps.phModelName')} />
          </Form.Item>
          <Form.Item name="task_type" label={t('modelOps.formTaskType')} rules={[{ required: true, message: t('modelOps.ruleTaskType') }]}>
            <Select
              placeholder={t('modelOps.phTaskType')}
              options={Object.entries(TASK_TYPE_META).map(([value, meta]) => ({ value, label: t(meta.labelKey) }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

/* ───────────────────────── Tab 2：实验运行 ───────────────────────── */

interface ConcludeState {
  experiment: ExperimentRun | null;
  conclusion: string;
  adopt: boolean;
}

function ExperimentsTab() {
  const { t } = useTranslation('pages');
  const [runs, setRuns] = useState<ExperimentRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [conclude, setConclude] = useState<ConcludeState>({ experiment: null, conclusion: '', adopt: false });
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();
  const { market } = useMarket();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const qs = statusFilter ? `&status=${encodeURIComponent(statusFilter)}` : '';
      setRuns(await api<ExperimentRun[]>(`/model-ops/experiments?market=${market}${qs}`));
    } catch {
      /* api() 已 toast */
    } finally {
      setLoading(false);
    }
  }, [market, statusFilter]);

  useEffect(() => { load(); }, [load]);
  useAutoRefresh(load, 30000);

  const submitConclude = async () => {
    const exp = conclude.experiment;
    if (!exp || !conclude.conclusion.trim()) return;
    setSubmitting(true);
    try {
      const r = await api<{ suggestion: string | null }>(
        `/model-ops/experiments/${encodeURIComponent(exp.experiment_id)}/conclude`,
        { method: 'POST', body: JSON.stringify({ conclusion: conclude.conclusion, adopt: conclude.adopt }) },
      );
      messageApi.success(t('modelOps.msgConcluded', { id: exp.experiment_id }));
      if (r.suggestion) messageApi.info(r.suggestion, 5);
      setConclude({ experiment: null, conclusion: '', adopt: false });
      load();
    } catch {
      /* api() 已 toast */
    } finally {
      setSubmitting(false);
    }
  };

  const columns: ColumnsType<ExperimentRun> = [
    {
      title: t('modelOps.colExpId'),
      dataIndex: 'experiment_id',
      key: 'experiment_id',
      width: 120,
      render: (v: string) => <Text code style={{ fontSize: 12 }}>{v}</Text>,
    },
    {
      title: t('modelOps.colStrategy'),
      dataIndex: 'strategy_name',
      key: 'strategy_name',
      render: (v: string, row) => (
        <Space orientation="vertical" size={0}>
          <Text>{v}</Text>
          <Text type="secondary" code style={{ fontSize: 11 }}>{row.strategy_version_id}</Text>
        </Space>
      ),
    },
    {
      title: t('modelOps.colStatus'),
      dataIndex: 'status',
      key: 'status',
      width: 95,
      render: (s: string) => {
        const meta = RUN_STATUS_META[s];
        return <Tag color={meta?.color ?? 'default'}>{meta ? t(meta.labelKey) : s}</Tag>;
      },
    },
    {
      title: t('modelOps.colConfig'),
      key: 'config',
      responsive: ['md'],
      render: (_, row) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {summary(row.config, ['name', 'hypothesis'])}
        </Text>
      ),
    },
    {
      title: t('modelOps.colResult'),
      key: 'result',
      responsive: ['lg'],
      render: (_, row) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {summary(row.result, ['conclusion', 'score', 'adopt'])}
        </Text>
      ),
    },
    { title: t('modelOps.colCreatedDate'), dataIndex: 'created_at', key: 'created_at', width: 165, render: fmtDate },
    {
      title: t('modelOps.colAction'),
      key: 'action',
      width: 85,
      render: (_, row) => (
        <Button
          size="small"
          type="link"
          disabled={row.status === 'CONCLUDED'}
          onClick={() => setConclude({ experiment: row, conclusion: '', adopt: false })}
        >
          {t('modelOps.btnConclude')}
        </Button>
      ),
    },
  ];

  return (
    <div>
      {contextHolder}
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <Space size={8}>
          <Text strong>{t('modelOps.experimentRuns')}</Text>
          <Text type="secondary">{t('modelOps.runCount', { n: runs.length, market })}</Text>
        </Space>
        <Space>
          <Select
            size="small"
            allowClear
            placeholder={t('modelOps.phStatusFilter')}
            style={{ minWidth: 120 }}
            value={statusFilter}
            onChange={(v) => setStatusFilter(v)}
            options={[
              { value: 'RUNNING', label: t('modelOps.rsRunning') },
              { value: 'CONCLUDED', label: t('modelOps.rsConcluded') },
              { value: 'PENDING', label: t('modelOps.rsPending') },
              { value: 'FAILED', label: t('modelOps.rsFailed') },
            ]}
          />
          <Button size="small" onClick={load} loading={loading}>{t('modelOps.refresh')}</Button>
        </Space>
      </div>

      <Table<ExperimentRun>
        rowKey="experiment_id"
        size="small"
        columns={columns}
        dataSource={runs}
        loading={loading}
        pagination={{ pageSize: 15, showSizeChanger: false }}
        locale={{ emptyText: t('modelOps.emptyRuns') }}
      />

      <Modal
        open={conclude.experiment !== null}
        title={t('modelOps.modalConcludeTitle', { id: conclude.experiment?.experiment_id ?? '' })}
        okText={t('modelOps.modalConcludeOk')}
        cancelText={t('common:action.cancel')}
        okButtonProps={{ disabled: !conclude.conclusion.trim() }}
        confirmLoading={submitting}
        onOk={submitConclude}
        onCancel={() => setConclude({ experiment: null, conclusion: '', adopt: false })}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={strategyLabel(conclude.experiment?.strategy_name)}
          description={summary(conclude.experiment?.config, ['hypothesis'])}
        />
        <Input.TextArea
          rows={4}
          value={conclude.conclusion}
          onChange={(e) => setConclude((s) => ({ ...s, conclusion: e.target.value }))}
          placeholder={t('modelOps.phConclusion')}
        />
        <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Switch
            checked={conclude.adopt}
            onChange={(v) => setConclude((s) => ({ ...s, adopt: v }))}
          />
          <Text>{t('modelOps.adoptLabel')}</Text>
        </div>
      </Modal>
    </div>
  );
}

export default function ModelOpsPage() {
  const { t } = useTranslation('pages');
  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>{t('modelOps.title')}</Title>
        <Text type="secondary">{t('modelOps.subtitle')}</Text>
      </div>
      <Tabs
        defaultActiveKey="registry"
        items={[
          { key: 'registry', label: t('modelOps.tabRegistry'), children: <RegistryTab /> },
          { key: 'experiments', label: t('modelOps.tabExperiments'), children: <ExperimentsTab /> },
        ]}
      />
    </div>
  );
}
