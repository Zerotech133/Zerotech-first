/**
 * 运行实例（Phase D Task 5）：本人策略运行实例，挂 StrategiesGroup ?tab=running
 * （canManageOwn=USER/ADMIN 才可见 tab）。
 *
 * 能力边界：
 * - 列表：listMyInstances(market) owner-scoped；策略名/版本号/市场经
 *   listMyStrategies(market)（公共+本人私有，含 versions）的
 *   strategy_version_id → 索引解析；账户列经 listMyAccounts(market) 解析类型徽章；
 * - 启停：行内 Switch → enableMyInstance / disableMyInstance（专用端点）；
 * - 升级横幅（Phase A 意图语义，Phase B 接线后自动切换）：
 *   · pending_upgrade_version_id 非空 → "升级已确认，待旧持仓平仓后自动切换"
 *     + upgrade_requested_at（优先展示；此时不再提供确认按钮）；
 *   · 否则 upgrade_available_version_id 非空 → "有新版本可用" + 确认升级按钮；
 *     该字段全仓暂无写入方（横幅曾因此死代码，Important #4），后端字段为空
 *     但页面已算出升级候选（同策略最新可运行版本 > 当前）时同样出横幅兜底；
 *   目标版本来源：listMyStrategies 的 versions——同一策略下 version_no 严格大于
 *   当前版本、状态可运行（ACTIVE/STAGING，对齐后端 _RUNNABLE_VERSION_STATUSES）
 *   的候选；默认 = upgrade_available_version_id，调 requestUpgrade(id, target)。
 *   （公开策略启用与私有策略启用的实例都能解析：/my/strategies 返回公共+本人私有。）
 * - 通知偏好：行操作弹窗复用 NotificationPreferenceForm（9 键），保存调
 *   updateMyInstance({notification_preferences})（后端 InstanceUpdateBody 白名单含
 *   该字段，见 my_strategy_instances.py）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert, Button, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Typography, message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  deleteMyInstance,
  disableMyInstance,
  enableMyInstance,
  listMyInstances,
  listMyStrategies,
  requestUpgrade,
  updateMyInstance,
  type MyStrategy,
  type MyStrategyInstance,
  type MyStrategyVersionBrief,
} from '../services/myStrategiesApi';
import { listMyAccounts, type MyAccount } from '../services/myAccountsApi';
import { useMarket } from '../contexts/MarketContext';
import { strategyLabel } from '../i18n/strategyName';
import { useRole } from '../hooks/useRole';
import NotificationPreferenceForm from '../components/strategy/NotificationPreferenceForm';
import { NOTIFICATION_KEYS, versionNoNum } from '../components/strategy/wizardModel';

const { Text } = Typography;

/** 升级申请时间的确定性展示：ISO 原串切 'YYYY-MM-DD HH:mm'（不做时区换算）。 */
function fmtRequestTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(iso);
  return m ? `${m[1]} ${m[2]}` : iso;
}

interface VersionRef {
  strategy: MyStrategy;
  version: MyStrategyVersionBrief;
}

export default function MyStrategyInstancesPage() {
  const { t } = useTranslation('pages');
  const { market } = useMarket();
  const { canManageOwn } = useRole();
  const [rows, setRows] = useState<MyStrategyInstance[]>([]);
  const [accounts, setAccounts] = useState<MyAccount[]>([]);
  const [strategies, setStrategies] = useState<MyStrategy[]>([]);
  const [loading, setLoading] = useState(false);

  // 升级确认弹窗
  const [upgradeTarget, setUpgradeTarget] = useState<MyStrategyInstance | null>(null);
  const [upgradeVersionId, setUpgradeVersionId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // 通知偏好弹窗
  const [prefsTarget, setPrefsTarget] = useState<MyStrategyInstance | null>(null);
  const [prefsDraft, setPrefsDraft] = useState<Record<string, boolean>>({});

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([listMyInstances(market), listMyAccounts(market), listMyStrategies(market)])
      .then(([insts, accs, ss]) => {
        setRows(insts);
        setAccounts(accs);
        setStrategies(ss);
      })
      .catch(() => {
        setRows([]);
        setAccounts([]);
        setStrategies([]);
      })
      .finally(() => setLoading(false));
  }, [market]);

  useEffect(() => { load(); }, [load]);

  /** strategy_version_id → {strategy, version} 索引（公共+本人私有全量）。 */
  const versionIndex = useMemo(() => {
    const m = new Map<string, VersionRef>();
    for (const s of strategies) {
      for (const v of s.versions ?? []) {
        m.set(v.strategy_version_id, { strategy: s, version: v });
      }
    }
    return m;
  }, [strategies]);

  const accountById = useMemo(() => {
    const m = new Map<string, MyAccount>();
    for (const a of accounts) m.set(a.account_id, a);
    return m;
  }, [accounts]);

  /**
   * 升级候选：同策略下 version_no 严格大于当前、状态可运行（ACTIVE/STAGING）
   * 的版本，version_no 降序（最新在前）。对齐后端 request_upgrade 校验口径
   * （跨策略拒绝 / 同版本拒绝 / 不可运行拒绝）。
   */
  const upgradeCandidates = useCallback((r: MyStrategyInstance): MyStrategyVersionBrief[] => {
    const ref = versionIndex.get(r.strategy_version_id);
    if (!ref) return [];
    const cur = versionNoNum(ref.version.version_no);
    return (ref.strategy.versions ?? [])
      .filter((v) =>
        v.strategy_version_id !== r.strategy_version_id
        && (v.status === 'ACTIVE' || v.status === 'STAGING')
        && versionNoNum(v.version_no) > cur)
      .sort((a, b) => versionNoNum(b.version_no) - versionNoNum(a.version_no));
  }, [versionIndex]);

  const openUpgrade = (r: MyStrategyInstance) => {
    setUpgradeTarget(r);
    // 默认目标 = upgrade_available_version_id（若仍在候选内），否则最新候选
    const candidates = upgradeCandidates(r);
    const suggested = r.upgrade_available_version_id
      && candidates.some((c) => c.strategy_version_id === r.upgrade_available_version_id)
      ? r.upgrade_available_version_id
      : candidates[0]?.strategy_version_id ?? null;
    setUpgradeVersionId(suggested);
  };

  const submitUpgrade = async () => {
    if (!upgradeTarget || !upgradeVersionId) return;
    setSaving(true);
    try {
      await requestUpgrade(upgradeTarget.user_strategy_id, upgradeVersionId);
      message.success(t('runningInstances.msgUpgradeRequested'));
      setUpgradeTarget(null);
      load();
    } catch { /* api() 已 toast（404/400 code 映射） */ } finally { setSaving(false); }
  };

  const openPrefs = (r: MyStrategyInstance) => {
    setPrefsTarget(r);
    // 9 键全展开（缺省键按后端默认=开），保存时整组提交
    const raw = (r.notification_preferences ?? {}) as Record<string, unknown>;
    setPrefsDraft(
      Object.fromEntries(
        NOTIFICATION_KEYS.map((k) => [k, raw[k] !== false]),
      ) as Record<string, boolean>,
    );
  };

  const submitPrefs = async () => {
    if (!prefsTarget) return;
    setSaving(true);
    try {
      await updateMyInstance(prefsTarget.user_strategy_id, {
        notification_preferences: prefsDraft,
      });
      message.success(t('runningInstances.msgNotifSaved'));
      setPrefsTarget(null);
      load();
    } catch { /* api() 已 toast */ } finally { setSaving(false); }
  };

  const toggleEnabled = async (r: MyStrategyInstance, next: boolean) => {
    try {
      if (next) {
        await enableMyInstance(r.user_strategy_id);
        message.success(t('runningInstances.msgEnabled'));
      } else {
        await disableMyInstance(r.user_strategy_id);
        message.success(t('runningInstances.msgDisabled'));
      }
      load();
    } catch { /* api() 已 toast（如共享配额溢出 409） */ }
  };

  /** 删除实例（B5 运营死锁解锁）：需先停用且无 OPEN 持仓；409 code 由 api()
   * 按 errors.json 翻译 toast（instance.not_disabled / instance.has_open_positions）。 */
  const removeInstance = async (r: MyStrategyInstance) => {
    try {
      await deleteMyInstance(r.user_strategy_id);
      message.success(t('runningInstances.msgDeleted'));
      load();
    } catch { /* api() 已 toast */ }
  };

  const columns: ColumnsType<MyStrategyInstance> = [
    { title: t('runningInstances.colStrategy'), key: 'strategy', width: 200, render: (_: unknown, r) => {
      const ref = versionIndex.get(r.strategy_version_id);
      if (!ref) return <Text code style={{ fontSize: 11 }}>{r.strategy_version_id}</Text>;
      return (
        <Space size={6}>
          <b>{strategyLabel(ref.strategy.strategy_name)}</b>
          <Tag>{ref.version.version_no}</Tag>
        </Space>
      );
    } },
    { title: t('strategies.colMarket'), key: 'market', width: 80, render: (_: unknown, r) => {
      const ref = versionIndex.get(r.strategy_version_id);
      return t(`market.${ref?.strategy.market ?? market}`, { ns: 'common' });
    } },
    { title: t('runningInstances.colAccount'), dataIndex: 'account_id', key: 'account_id', width: 190,
      render: (v: string) => {
        const acc = accountById.get(v);
        return (
          <Space size={4}>
            <Text code style={{ fontSize: 11 }}>{v}</Text>
            {acc?.account_type === 'SHARED_PORTFOLIO' && <Tag color="cyan">{t('myAccounts.typeShared')}</Tag>}
            {acc?.account_type === 'STRATEGY_DEDICATED' && <Tag color="geekblue">{t('myAccounts.typeDedicated')}</Tag>}
          </Space>
        );
      } },
    { title: t('runningInstances.colEnabled'), dataIndex: 'enabled', key: 'enabled', width: 90,
      render: (v: boolean, r) => (
        <Switch
          checked={v}
          size="small"
          disabled={!canManageOwn}
          onChange={(next) => toggleEnabled(r, next)}
        />
      ) },
    { title: t('runningInstances.colExecution'), dataIndex: 'execution_mode', key: 'execution_mode', width: 130,
      render: (v: string, r) => (
        <Space size={4}>
          <Tag color={v === 'MANUAL' ? 'default' : 'blue'}>{v}</Tag>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {r.auto_execute ? t('runningInstances.autoExecuteOn') : t('runningInstances.autoExecuteOff')}
          </Text>
        </Space>
      ) },
    { title: t('runningInstances.colQuota'), dataIndex: 'capital_quota', key: 'capital_quota', width: 100, align: 'right',
      render: (v: string | null) => (v === null ? '—' : v) },
    { title: t('runningInstances.colMaxRatio'), dataIndex: 'max_position_ratio', key: 'max_position_ratio', width: 90, align: 'right' },
    { title: t('runningInstances.colUpgrade'), key: 'upgrade', width: 320, render: (_: unknown, r) => {
      // pending 优先：已确认升级则只展示等待平仓切换，不再提供确认按钮
      if (r.pending_upgrade_version_id) {
        return (
          <Alert
            type="warning"
            showIcon
            title={t('runningInstances.upgradePending')}
            description={t('runningInstances.upgradeRequestedAt', {
              time: fmtRequestTime(r.upgrade_requested_at),
            })}
          />
        );
      }
      // available 兜底（Important #4）：upgrade_available_version_id 全仓暂无写入方
      // （后端 scheduler/发布流程待立项），字段为空但页面已算出更高可运行版本
      // （upgradeCandidates 非空）时同样出"有新版本可用"横幅；pending 态语义
      // 仍以后端字段为准（上方分支）。
      if (r.upgrade_available_version_id || upgradeCandidates(r).length > 0) {
        return (
          <Space size={8} wrap>
            <Alert type="info" showIcon title={t('runningInstances.upgradeAvailable')} />
            {canManageOwn && (
              <Button size="small" type="primary" ghost onClick={() => openUpgrade(r)}>
                {t('runningInstances.upgradeConfirmBtn')}
              </Button>
            )}
          </Space>
        );
      }
      return <Text type="secondary">—</Text>;
    } },
    { title: t('runningInstances.colAction'), key: 'action', width: 200, render: (_: unknown, r) => (
      canManageOwn ? (
        <Space size={8}>
          <Button size="small" onClick={() => openPrefs(r)}>
            {t('runningInstances.notifBtn')}
          </Button>
          <Popconfirm
            title={t('runningInstances.delConfirmTitle')}
            description={
              r.enabled ? t('runningInstances.delHintEnabled') : t('runningInstances.delHint')
            }
            okText={t('runningInstances.delConfirmOk')}
            cancelText={t('common:action.cancel')}
            okButtonProps={{ danger: true }}
            disabled={r.enabled}
            onConfirm={() => removeInstance(r)}
          >
            <Button size="small" danger ghost disabled={r.enabled}>
              {t('runningInstances.delBtn')}
            </Button>
          </Popconfirm>
        </Space>
      ) : (
        <Text type="secondary">—</Text>
      )
    ) },
  ];

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>{t('runningInstances.title')}</h1>
        <Tag color="blue" style={{ fontSize: 13, padding: '2px 10px' }}>
          {t(`market.${market}`, { ns: 'common' })}（{market}）
        </Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>{t('runningInstances.hint')}</Text>
        <div style={{ flex: 1 }} />
        <Button onClick={load}>{t('strategies.refresh')}</Button>
      </div>

      <Table<MyStrategyInstance>
        rowKey="user_strategy_id"
        size="small"
        loading={loading}
        dataSource={rows}
        columns={columns}
        pagination={{ pageSize: 20, showSizeChanger: true }}
      />

      {/* 确认升级：目标版本下拉（默认 upgrade_available_version_id） */}
      <Modal
        title={t('runningInstances.upgradeTitle')}
        open={!!upgradeTarget}
        onOk={submitUpgrade}
        onCancel={() => setUpgradeTarget(null)}
        okText={t('runningInstances.upgradeConfirmBtn')}
        cancelText={t('common:action.cancel')}
        confirmLoading={saving}
        okButtonProps={{ disabled: !upgradeVersionId }}
      >
        {upgradeTarget && (
          <>
            <p style={{ marginBottom: 12 }}>
              {t('runningInstances.upgradeHint')}
            </p>
            <div style={{ marginBottom: 6, fontWeight: 500 }}>
              {t('runningInstances.upgradeTargetLabel')}
            </div>
            <Select
              style={{ width: '100%' }}
              value={upgradeVersionId ?? undefined}
              onChange={(v) => setUpgradeVersionId(v as string)}
              placeholder={t('runningInstances.upgradeNoCandidates')}
              options={upgradeCandidates(upgradeTarget).map((v) => ({
                value: v.strategy_version_id,
                label: `${v.version_no}（${v.status}）`,
              }))}
            />
            <Text type="secondary" style={{ display: 'block', fontSize: 12, marginTop: 8 }}>
              {t('runningInstances.upgradeTargetHint')}
            </Text>
          </>
        )}
      </Modal>

      {/* 实例级通知偏好（复用 9 键开关组；保存走 updateMyInstance patch） */}
      <Modal
        title={t('runningInstances.notifTitle')}
        open={!!prefsTarget}
        onOk={submitPrefs}
        onCancel={() => setPrefsTarget(null)}
        okText={t('common:action.save')}
        cancelText={t('common:action.cancel')}
        confirmLoading={saving}
      >
        <NotificationPreferenceForm value={prefsDraft} onChange={setPrefsDraft} />
      </Modal>
    </div>
  );
}
