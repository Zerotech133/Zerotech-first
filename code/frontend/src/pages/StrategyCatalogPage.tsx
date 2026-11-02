/**
 * 策略广场（Phase D Task 3）：公共策略目录 + "使用策略"启用向导入口。
 *
 * owner-scoped：目录走公共读 listCatalog；启用走 /my/* typed 客户端（向导内）。
 * 角色口径：仅 canManageOwn（USER/ADMIN）显示"使用策略"操作按钮，VIEWER 只读目录。
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button, Space, Table, Tag, Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { TFunction } from 'i18next';
import { listCatalog, type CatalogStrategy } from '../services/myStrategiesApi';
import { useMarket } from '../contexts/MarketContext';
import { strategyLabel } from '../i18n/strategyName';
import { useRole } from '../hooks/useRole';
import EnableStrategyWizard from '../components/strategy/EnableStrategyWizard';

const { Text } = Typography;

// 与 StrategiesPage 同口径的状态配色（复用 strategies.* i18n 键）
const STATUS_META: Record<string, { labelKey: string; color: string }> = {
  ACTIVE: { labelKey: 'strategies.statusActive', color: 'green' },
  STAGING: { labelKey: 'strategies.statusStaging', color: 'blue' },
  DRAFT: { labelKey: 'strategies.statusDraft', color: 'default' },
  PENDING_REVIEW: { labelKey: 'strategies.statusPendingReview', color: 'gold' },
  REJECTED: { labelKey: 'strategies.statusRejected', color: 'red' },
  DISABLED: { labelKey: 'strategies.statusDisabled', color: 'default' },
};

function statusTag(status: string, t: TFunction) {
  const m = STATUS_META[status];
  return <Tag color={m?.color ?? 'default'}>{m ? t(m.labelKey) : status}</Tag>;
}

export default function StrategyCatalogPage() {
  const { t } = useTranslation('pages');
  const { market } = useMarket(); // 跟随顶部工作区市场（/cn→CN…），与全站一致
  const { canManageOwn } = useRole();
  const [rows, setRows] = useState<CatalogStrategy[]>([]);
  const [loading, setLoading] = useState(false);
  const [wizardTarget, setWizardTarget] = useState<CatalogStrategy | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    listCatalog(market)
      .then(setRows)
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [market]);

  useEffect(() => { load(); }, [load]);

  const columns: ColumnsType<CatalogStrategy> = [
    { title: t('strategies.colName'), dataIndex: 'strategy_name', key: 'strategy_name', width: 180,
      render: (v: string) => <b>{strategyLabel(v)}</b> },
    { title: t('strategies.colDesc'), dataIndex: 'description', key: 'description', ellipsis: true,
      render: (v: string) => v || <Text type="secondary">—</Text> },
    { title: t('strategies.colMarket'), dataIndex: 'market', key: 'market', width: 80,
      render: (v: string) => t(`market.${v}`, { ns: 'common' }) },
    { title: t('strategies.colType'), dataIndex: 'strategy_type', key: 'strategy_type', width: 140,
      render: (v: string, r) => (
        <Space size={4}>
          <Text>{v}</Text>
          {r.is_sidecar
            ? <Tag color="geekblue">研究策略</Tag>
            : (r.is_builtin
              ? <Tag color="purple">{t('strategies.builtin')}</Tag>
              : <Tag color="cyan">{t('strategies.external')}</Tag>)}
        </Space>
      ) },
    { title: t('strategies.colStatus'), dataIndex: 'status', key: 'status', width: 90,
      render: (v: string) => statusTag(v, t) },
    { title: t('strategies.colVersion'), dataIndex: 'version_count', key: 'version_count', width: 60 },
    { title: t('strategies.colOwner', { defaultValue: '负责人' }), dataIndex: 'research_owner', key: 'research_owner', width: 100,
      render: (v: string | null, r) => <Text>{v || r.owner_user_id || '—'}</Text> },
    { title: t('strategies.colLifecycle', { defaultValue: '生命周期' }), dataIndex: 'lifecycle_status', key: 'lifecycle_status', width: 100,
      render: (v: string | null) => v ? <Tag color={v === 'active' || v === 'live' ? 'green' : v === 'research' ? 'red' : 'blue'}>{v}</Tag> : <Text type="secondary">—</Text> },
    { title: t('strategies.colAction'), key: 'action', width: 120, render: (_: unknown, r) => (
      r.is_sidecar ? (
        <Tag>仅供研究</Tag>
      ) : canManageOwn ? (
        <Button size="small" type="primary" onClick={() => setWizardTarget(r)}>
          {t('strategyCatalog.useBtn')}
        </Button>
      ) : (
        <Text type="secondary">—</Text>
      )
    ) },
  ];

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>{t('strategyCatalog.title')}</h1>
        <Tag color="blue" style={{ fontSize: 13, padding: '2px 10px' }}>
          {t(`market.${market}`, { ns: 'common' })}（{market}）
        </Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>{t('strategyCatalog.hint')}</Text>
        <div style={{ flex: 1 }} />
        <Button onClick={load}>{t('strategies.refresh')}</Button>
      </div>

      <Table<CatalogStrategy>
        rowKey="strategy_id"
        size="small"
        loading={loading}
        dataSource={rows}
        columns={columns}
        pagination={{ pageSize: 20, showSizeChanger: true }}
      />

      {/* 使用策略 → 启用向导（key 保证每行打开都重置向导状态） */}
      {wizardTarget && (
        <EnableStrategyWizard
          key={wizardTarget.strategy_id}
          open
          strategy={wizardTarget}
          onClose={() => setWizardTarget(null)}
          onDone={load}
        />
      )}
    </div>
  );
}
