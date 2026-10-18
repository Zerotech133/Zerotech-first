import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  InputNumber,
  Progress,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
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
import SymbolLink from '../components/SymbolLink';
import { strategyLabel } from '../i18n/strategyName';
import { useRole } from '../hooks/useRole';

const { Title, Text } = Typography;

/* ───────────────────────── 类型（后端契约） ───────────────────────── */

interface ConflictPlan {
  plan_id: string;
  strategy_name: string;
  strategy_version_id: string;
  plan_status: string;
  confidence_level: number | null;
}
interface ConflictGroup {
  ts_code: string;
  plans: ConflictPlan[];
}
interface ConflictsResp {
  date: string | null;
  market: string;
  conflicts: ConflictGroup[];
  clean_count: number;
}
interface ResolveResp {
  date: string | null;
  mode: string;
  resolved_groups: number;
  cancelled_plans: string[];
}
interface QuotaBody {
  quota_pct: number;
  max_positions: number;
}
interface OverviewRow {
  strategy_version_id: string;
  strategy_name: string;
  position_count: number;
  market_value: number;
  position_ratio: number;
  quota_pct: number | null;
  max_positions: number | null;
  over_exposure: boolean;
  over_positions: boolean;
}

/* ───────────────────────── 常量 ───────────────────────── */

const MODE_OPTIONS = [
  { labelKey: 'multiStrategy.modePriority', value: 'PRIORITY' },
  { labelKey: 'multiStrategy.modeBlockAll', value: 'BLOCK_ALL' },
  { labelKey: 'multiStrategy.modeMergeDedup', value: 'MERGE_DEDUP' },
];
const MODE_HINT_KEYS: Record<string, string> = {
  PRIORITY: 'multiStrategy.modeHintPriority',
  BLOCK_ALL: 'multiStrategy.modeHintBlockAll',
  MERGE_DEDUP: 'multiStrategy.modeHintMergeDedup',
};
const STATUS_TAG_KEYS: Record<string, { labelKey: string; color: string }> = {
  PENDING: { labelKey: 'multiStrategy.statusPending', color: 'orange' },
  APPROVED: { labelKey: 'multiStrategy.statusApproved', color: 'green' },
};

/* ───────────────────────── 页面 ───────────────────────── */

export default function MultiStrategyPage() {
  const { t } = useTranslation('pages');
  const { canWrite } = useRole();  // VIEWER 只读：隐藏解决冲突/保存配额
  const { market, currencySymbol } = useMarket();
  const [messageApi, contextHolder] = message.useMessage();

  const [conflictData, setConflictData] = useState<ConflictsResp | null>(null);
  const [overview, setOverview] = useState<OverviewRow[]>([]);
  const [loading, setLoading] = useState(true);

  // 裁决操作
  const [mode, setMode] = useState<string>('PRIORITY');
  const [priorityOrder, setPriorityOrder] = useState<string[]>([]);
  const [resolving, setResolving] = useState(false);

  // 配额编辑
  const [quotaSv, setQuotaSv] = useState<string>('');
  const [quotaPct, setQuotaPct] = useState<number | null>(50);
  const [maxPositions, setMaxPositions] = useState<number | null>(10);
  const [savingQuota, setSavingQuota] = useState(false);

  const load = useCallback(() => {
    Promise.all([
      api<ConflictsResp>(`/multi-strategy/conflicts?market=${market}`).catch(() => null),
      api<OverviewRow[]>(`/multi-strategy/overview?market=${market}`).catch(() => [] as OverviewRow[]),
    ]).then(([cf, ov]) => {
      setConflictData(cf);
      setOverview(Array.isArray(ov) ? ov : []);
      setLoading(false);
    });
  }, [market]);

  useEffect(() => {
    setLoading(true);
    setPriorityOrder([]);
    load();
  }, [load]);

  useAutoRefresh(load, 30000, [market]);

  async function runResolve() {
    if (!conflictData || conflictData.conflicts.length === 0) return;
    setResolving(true);
    try {
      const resp = await api<ResolveResp>('/multi-strategy/resolve', {
        method: 'POST',
        body: JSON.stringify({
          market,
          date: conflictData.date,
          mode,
          priority_order: mode === 'PRIORITY' && priorityOrder.length > 0 ? priorityOrder : undefined,
        }),
      });
      messageApi.success(
        t('multiStrategy.msgResolveDone', { groups: resp.resolved_groups, plans: resp.cancelled_plans.length }),
      );
      load();
    } catch {
      /* api 已弹错误提示 */
    } finally {
      setResolving(false);
    }
  }

  async function saveQuota() {
    if (!quotaSv || quotaPct == null || maxPositions == null) return;
    setSavingQuota(true);
    try {
      const saved = await api<QuotaBody>(`/multi-strategy/quotas/${quotaSv}`, {
        method: 'PUT',
        body: JSON.stringify({ quota_pct: quotaPct, max_positions: maxPositions }),
      });
      messageApi.success(
        t('multiStrategy.msgQuotaSaved', { sv: quotaSv, pct: saved.quota_pct, max: saved.max_positions }),
      );
      load();
    } catch {
      /* api 已弹错误提示 */
    } finally {
      setSavingQuota(false);
    }
  }

  const svOptions = Array.from(
    new Set([
      ...overview.map((o) => o.strategy_version_id),
      ...(conflictData?.conflicts.flatMap((c) => c.plans.map((p) => p.strategy_version_id)) ?? []),
    ]),
  ).map((id) => {
    const named = overview.find((o) => o.strategy_version_id === id);
    return { value: id, label: named ? `${strategyLabel(named.strategy_name)}（${id}）` : id };
  });

  const conflicts = conflictData?.conflicts ?? [];

  const overviewColumns: ColumnsType<OverviewRow> = [
    {
      title: t('multiStrategy.colStrategy'),
      key: 'strategy',
      render: (_, row) => (
        <Space size={6}>
          <span>{strategyLabel(row.strategy_name)}</span>
          <Text type="secondary" style={{ fontFamily: 'monospace', fontSize: 12 }}>
            {row.strategy_version_id}
          </Text>
        </Space>
      ),
    },
    {
      title: t('multiStrategy.colPositionCount'),
      dataIndex: 'position_count',
      key: 'position_count',
      align: 'right',
      render: (v: number, row) => (
        <span>
          {v}
          {row.max_positions != null && (
            <Text type="secondary"> / {row.max_positions}</Text>
          )}
        </span>
      ),
    },
    {
      title: t('multiStrategy.colMarketValue'),
      dataIndex: 'market_value',
      key: 'market_value',
      align: 'right',
      render: (v: number) => `${currencySymbol}${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`,
    },
    {
      title: t('multiStrategy.colPositionRatio'),
      dataIndex: 'position_ratio',
      key: 'position_ratio',
      width: 160,
      render: (v: number, row) => {
        const pct = Math.round(v);
        const quota = row.quota_pct;
        const over = quota != null && v > quota;
        return (
          <Progress
            percent={pct}
            size="small"
            strokeColor={over ? '#ff4d4f' : pct > 80 ? '#faad14' : '#52c41a'}
            format={(p) => `${p}%${quota != null ? ` / ${quota}%` : ''}`}
          />
        );
      },
    },
    {
      title: t('multiStrategy.colQuota'),
      dataIndex: 'quota_pct',
      key: 'quota_pct',
      align: 'center',
      render: (v: number | null) => (v == null ? <Tag>{t('multiStrategy.notConfigured')}</Tag> : `${v}%`),
    },
    {
      title: t('multiStrategy.colMaxPositions'),
      dataIndex: 'max_positions',
      key: 'max_positions',
      align: 'center',
      render: (v: number | null) => v ?? '—',
    },
    {
      title: t('multiStrategy.colBreach'),
      key: 'breach',
      align: 'center',
      render: (_, row) => (
        <Space size={4}>
          {row.quota_pct == null && row.max_positions == null ? (
            <Tag>{t('multiStrategy.noQuota')}</Tag>
          ) : (
            <>
              {row.quota_pct != null && (
                <Tag color={row.over_exposure ? 'red' : 'green'}>
                  {row.over_exposure ? t('multiStrategy.overExposure') : t('multiStrategy.normalExposure')}
                </Tag>
              )}
              {row.max_positions != null && (
                <Tag color={row.over_positions ? 'red' : 'green'}>
                  {row.over_positions ? t('multiStrategy.overPositions') : t('multiStrategy.normalPositions')}
                </Tag>
              )}
            </>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: '24px' }}>
      {contextHolder}
      <Title level={4} style={{ marginBottom: 24 }}>
        {t('multiStrategy.title', { market: t('market.' + market, { ns: 'common' }) })}
      </Title>

      <Tabs
        defaultActiveKey="conflicts"
        items={[
          {
            key: 'conflicts',
            label: `${t('multiStrategy.tabConflicts')}${conflicts.length > 0 ? `（${conflicts.length}）` : ''}`,
            children: (
              <Spin spinning={loading}>
                {/* 裁决操作区 */}
                <Card title={t('multiStrategy.cardResolve')} size="small" style={{ marginBottom: 16 }}>
                  <Space orientation="vertical" size={12} style={{ width: '100%' }}>
                    <Space wrap size={12}>
                      <Segmented options={MODE_OPTIONS.map((o) => ({ label: t(o.labelKey), value: o.value }))} value={mode} onChange={(v) => setMode(v as string)} />
                      {canWrite && (
                        <Button danger type="primary" loading={resolving} disabled={conflicts.length === 0} onClick={runResolve}>
                          {t('multiStrategy.btnResolve')}
                        </Button>
                      )}
                      {conflictData?.date && (
                        <Text type="secondary">
                          {t('multiStrategy.hintPlanDate', { date: conflictData.date, clean: conflictData.clean_count })}
                        </Text>
                      )}
                    </Space>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {t(MODE_HINT_KEYS[mode])}
                    </Text>
                    {mode === 'PRIORITY' && (
                      <div>
                        <Text type="secondary" style={{ marginRight: 8, fontSize: 12 }}>
                          {t('multiStrategy.priorityLabel')}
                        </Text>
                        <Select
                          mode="multiple"
                          style={{ minWidth: 360 }}
                          placeholder={t('multiStrategy.phPriority')}
                          value={priorityOrder}
                          onChange={setPriorityOrder}
                          options={svOptions}
                          allowClear
                        />
                      </div>
                    )}
                  </Space>
                </Card>

                {/* 冲突组列表 */}
                {conflicts.length === 0 ? (
                  <Alert
                    type="success"
                    showIcon
                    title={t('multiStrategy.alertNoConflict')}
                    description={
                      conflictData?.date
                        ? t('multiStrategy.alertNoConflictDesc', { date: conflictData.date, clean: conflictData.clean_count })
                        : t('multiStrategy.alertNoActive')
                    }
                  />
                ) : (
                  <Row gutter={[16, 16]}>
                    {conflicts.map((group) => (
                      <Col xs={24} lg={12} key={group.ts_code}>
                        <Card
                          size="small"
                          title={<SymbolLink code={group.ts_code} />}
                          extra={<Tag color="volcano">{t('multiStrategy.tagStrategyCount', { n: group.plans.length })}</Tag>}
                        >
                          {group.plans.map((p) => {
                            const meta = STATUS_TAG_KEYS[p.plan_status];
                            const top =
                              p.confidence_level ===
                              Math.max(...group.plans.map((x) => x.confidence_level ?? 0));
                            return (
                              <div
                                key={p.plan_id}
                                style={{
                                  display: 'flex',
                                  alignItems: 'center',
                                  gap: 8,
                                  padding: '4px 0',
                                }}
                              >
                                <Tag color={meta?.color ?? 'default'} style={{ marginInlineEnd: 0 }}>
                                  {meta ? t(meta.labelKey) : p.plan_status}
                                </Tag>
                                <Text strong={top}>{strategyLabel(p.strategy_name)}</Text>
                                <Text type="secondary" style={{ fontFamily: 'monospace', fontSize: 12 }}>
                                  {p.strategy_version_id}
                                </Text>
                                <Text style={{ marginLeft: 'auto', fontFamily: 'monospace' }}>
                                  {t('multiStrategy.confidence')} {p.confidence_level != null ? p.confidence_level.toFixed(3) : '—'}
                                  {top && <Tag color="gold" style={{ marginLeft: 6 }}>{t('multiStrategy.tagTop')}</Tag>}
                                </Text>
                              </div>
                            );
                          })}
                        </Card>
                      </Col>
                    ))}
                  </Row>
                )}
              </Spin>
            ),
          },
          {
            key: 'quotas',
            label: t('multiStrategy.tabQuotas'),
            children: (
              <Spin spinning={loading}>
                {/* 配额设置 */}
                <Card title={t('multiStrategy.cardSetQuota')} size="small" style={{ marginBottom: 16 }}>
                  <Space wrap size={12}>
                    <Select
                      style={{ minWidth: 260 }}
                      placeholder={t('multiStrategy.phSelectStrategy')}
                      value={quotaSv || undefined}
                      onChange={(v) => setQuotaSv(v)}
                      options={svOptions}
                    />
                    <Space size={4}>
                      <Text type="secondary">{t('multiStrategy.labelQuotaPct')}</Text>
                      <InputNumber min={0} max={100} value={quotaPct} onChange={(v) => setQuotaPct(v)} style={{ width: 90 }} />
                    </Space>
                    <Space size={4}>
                      <Text type="secondary">{t('multiStrategy.labelMaxPositions')}</Text>
                      <InputNumber min={1} max={999} value={maxPositions} onChange={(v) => setMaxPositions(v)} style={{ width: 90 }} />
                    </Space>
                    {canWrite && (
                      <Button type="primary" loading={savingQuota} disabled={!quotaSv} onClick={saveQuota}>
                        {t('multiStrategy.btnSaveQuota')}
                      </Button>
                    )}
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {t('multiStrategy.hintMemoryConfig')}
                    </Text>
                  </Space>
                </Card>

                {/* 总览表 */}
                <Card title={t('multiStrategy.cardOverview')} size="small">
                  <Table<OverviewRow>
                    columns={overviewColumns}
                    dataSource={overview}
                    rowKey="strategy_version_id"
                    pagination={false}
                    size="middle"
                    locale={{ emptyText: t('multiStrategy.emptyOverview') }}
                  />
                </Card>
              </Spin>
            ),
          },
        ]}
      />
    </div>
  );
}
