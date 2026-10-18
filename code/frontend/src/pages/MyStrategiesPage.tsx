/**
 * 我的策略（Phase D Task 4）：本人私有策略管理，挂 StrategiesGroup ?tab=mine
 * （canManageOwn=USER/ADMIN 才可见 tab）。
 *
 * 能力边界：
 * - 列表：/my/strategies 返回"公共 + 本人私有"，此处仅渲染本人私有行
 *   （visibility==='PRIVATE' 前端过滤——后端无该查询参数；他人私有后端本就不可见）；
 * - 新建（B5）：「新建策略」弹窗（策略名 / market 下拉 / 最小可用 spec 模板
 *   可编辑 JSON），调 createPrivateStrategy（POST /my/strategies）——此前该
 *   API 在 myStrategiesApi 定义但全仓零调用，页面无创建入口；
 * - 删除（B5）：行内 Popconfirm 调 deleteMyStrategy（DELETE /my/strategies/{id}）；
 *   仍被实例绑定时后端 409 {code: strategy.bound_by_instances}，api() 按
 *   errors.json 自动翻译 toast（引导先去 running 页删实例）；
 * - 编辑：名称/描述走 updatePrivateStrategy（PUT /my/strategies/{id}）；
 * - spec 不可变：编辑参数入口=「新版本」弹窗，确认"将创建新版本 vN（旧版本保持
 *   不变）"后调 createMyStrategyVersion（POST /my/strategies/{id}/versions），
 *   绝不直接改旧版本；spec 预填复用 GET /strategies/{id}/code（与 StrategiesPage
 *   同形态的 JSON 文本域）；
 * - 发布申请：requestPublication；PENDING_REVIEW 禁用（提示审核中）；409 错误码
 *   strategy.already_published / strategy.publication_already_pending 给对应文案
 *   （api() 抛出的 Error.message 含 detail code）；
 * - 升级提示（策略侧）：version_count>1 的行显示「有新版本」；实例维度的
 *   pending_upgrade/upgrade_available 横幅与升级按钮归 T5（running 实例页）。
 */
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Alert, Button, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { TFunction } from 'i18next';
import {
  createMyStrategyVersion,
  createPrivateStrategy,
  deleteMyStrategy,
  getMyStrategySpec,
  listCatalog,
  listMyStrategies,
  requestPublication,
  updatePrivateStrategy,
  type CatalogStrategy,
  type MyStrategy,
} from '../services/myStrategiesApi';
import { listSubscriptions, type SubscriptionView } from '../services/billingApi';
import { useMarket } from '../contexts/MarketContext';
import { strategyLabel } from '../i18n/strategyName';
import { useRole } from '../hooks/useRole';

/** 表格行：本人私有策略（MyStrategy）+ 已订阅的平台策略（__subscribed 标记，只读）。 */
type Row = MyStrategy & { __subscribed?: boolean };

/** 订阅是否生效：优先 effective_status（B3-6），旧响应缺省时回退 active 布尔。 */
function isSubActive(s: SubscriptionView): boolean {
  const st = s.effective_status;
  if (st) return st === 'active' || st === 'permanent';
  return s.active;
}

/** 平台目录策略 → 只读订阅行（形状对齐 MyStrategy，publication 归位「已公开」）。 */
function subToRow(c: CatalogStrategy): Row {
  return {
    strategy_id: c.strategy_id,
    strategy_name: c.strategy_name,
    market: c.market,
    visibility: 'PUBLIC',
    publication_status: 'PUBLISHED',
    owner_user_id: null,
    status: c.status,
    description: c.description,
    version_count: c.version_count,
    latest_version_no: c.versions[c.versions.length - 1]?.version_no ?? null,
    versions: c.versions,
    __subscribed: true,
  };
}

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

// 四市场（与 MarketPage/ResearchPage 同口径）；新建弹窗 market 下拉用。
const MARKETS = ['CN', 'US', 'HK', 'CRYPTO'] as const;

/** 最小可用 spec 模板（可编辑）：strategy_version/spec 必填 spec_version/market/
 * universe；market 在提交时强制与所选下拉一致（后端校验 spec.market == market）。 */
function minimalSpecTemplate(market: string): string {
  return JSON.stringify(
    { spec_version: 1, market, universe: { type: 'all' }, rules: [] },
    null,
    2,
  );
}

// 与 StrategiesPage/StrategyCatalogPage 同口径的生命周期状态配色
const STATUS_META: Record<string, { labelKey: string; color: string }> = {
  ACTIVE: { labelKey: 'strategies.statusActive', color: 'green' },
  STAGING: { labelKey: 'strategies.statusStaging', color: 'blue' },
  DRAFT: { labelKey: 'strategies.statusDraft', color: 'default' },
  PENDING_REVIEW: { labelKey: 'strategies.statusPendingReview', color: 'gold' },
  REJECTED: { labelKey: 'strategies.statusRejected', color: 'red' },
  DISABLED: { labelKey: 'strategies.statusDisabled', color: 'default' },
};

// 发布状态徽章：私有源审核通过后 publication_status 归位 PRIVATE（派生公共副本独立存在）
const PUB_META: Record<string, { labelKey: string; color: string }> = {
  PRIVATE: { labelKey: 'myStrategies.pubPrivate', color: 'default' },
  PENDING_REVIEW: { labelKey: 'myStrategies.pubPending', color: 'gold' },
  PUBLISHED: { labelKey: 'myStrategies.pubPublished', color: 'green' },
};

function statusTag(status: string, t: TFunction) {
  const m = STATUS_META[status];
  return <Tag color={m?.color ?? 'default'}>{m ? t(m.labelKey) : status}</Tag>;
}

function pubTag(pubStatus: string, t: TFunction) {
  const m = PUB_META[pubStatus];
  return <Tag color={m?.color ?? 'default'}>{m ? t(m.labelKey) : pubStatus}</Tag>;
}

function versionCountOf(r: MyStrategy): number {
  return r.version_count ?? r.versions?.length ?? 0;
}

/** 下一版本号：现有 version_no 数值最大者 +1（与后端 create_version 同口径）。 */
function nextVersionNo(r: MyStrategy): string {
  const nums = (r.versions ?? [])
    .map((v) => Number.parseInt(v.version_no.replace(/^v/i, ''), 10))
    .filter((n) => Number.isFinite(n));
  const max = nums.length ? Math.max(...nums) : versionCountOf(r);
  return `v${(Number.isFinite(max) ? max : 0) + 1}`;
}

export default function MyStrategiesPage() {
  const { t } = useTranslation('pages');
  const { market } = useMarket();
  const { canManageOwn } = useRole();
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [pubErr, setPubErr] = useState<string | null>(null);

  // 编辑元信息（名称/描述）
  const [metaTarget, setMetaTarget] = useState<MyStrategy | null>(null);
  const [metaForm] = Form.useForm();
  const [saving, setSaving] = useState(false);

  // 新版本（spec 不可变编辑）
  const [versionTarget, setVersionTarget] = useState<MyStrategy | null>(null);
  const [specDraft, setSpecDraft] = useState('');
  const [specLoading, setSpecLoading] = useState(false);

  // 新建策略（B5：createPrivateStrategy 此前零调用，页面无创建入口）
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();
  const [createSpecDraft, setCreateSpecDraft] = useState(minimalSpecTemplate(market));
  const [creating, setCreating] = useState(false);

  const openCreate = () => {
    setCreateSpecDraft(minimalSpecTemplate(market));
    createForm.setFieldsValue({ strategy_name: '', market });
    setCreateOpen(true);
  };

  const submitCreate = async () => {
    const v = await createForm.validateFields();
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(createSpecDraft) as Record<string, unknown>;
    } catch {
      message.error(t('myStrategies.msgSpecInvalid'));
      return;
    }
    // spec.market 强制与所选 market 一致（后端 422 market_mismatch 预防——
    // 模板已预填，用户改 JSON 后仍以下拉为准）。
    parsed.market = v.market as string;
    setCreating(true);
    try {
      await createPrivateStrategy({
        strategy_name: v.strategy_name as string,
        market: v.market as string,
        spec: parsed,
      });
      message.success(t('myStrategies.msgCreated'));
      setCreateOpen(false);
      // 所建 market 与当前过滤不同时提示切换（列表按当前 market 过滤）
      if (v.market !== market) {
        message.info(t('myStrategies.msgCreatedOtherMarket', { market: v.market as string }));
      }
      load();
    } catch { /* api() 已 toast（同名 409 strategy.name_taken / spec 422 等） */ }
    finally { setCreating(false); }
  };

  const removeStrategy = async (r: MyStrategy) => {
    try {
      await deleteMyStrategy(r.strategy_id);
      message.success(t('myStrategies.msgDeleted'));
      load();
    } catch { /* api() 已 toast（绑定实例 409 strategy.bound_by_instances 翻译） */ }
  };

  const load = useCallback(() => {
    setLoading(true);
    // 并行拉：本人私有 + 订阅项 + 平台目录（订阅/目录任一失败不影响私有列表渲染）。
    Promise.allSettled([listMyStrategies(market), listSubscriptions(), listCatalog(market)])
      .then(([mineR, subsR, catR]) => {
        const mine: Row[] =
          mineR.status === 'fulfilled'
            ? mineR.value.filter((r) => r.visibility === 'PRIVATE')
            : [];
        let subRows: Row[] = [];
        if (subsR.status === 'fulfilled' && catR.status === 'fulfilled') {
          const activeIds = new Set(
            subsR.value.items.filter(isSubActive).map((s) => s.strategy_id),
          );
          subRows = catR.value
            .filter((c) => c.owner_user_id === null && activeIds.has(c.strategy_id))
            .map(subToRow);
        }
        setRows([...mine, ...subRows]);
      })
      .finally(() => setLoading(false));
  }, [market]);

  useEffect(() => { load(); }, [load]);

  const openEdit = (r: MyStrategy) => {
    setPubErr(null);
    setMetaTarget(r);
    metaForm.setFieldsValue({ name: r.strategy_name, description: r.description ?? '' });
  };

  const saveMeta = async () => {
    if (!metaTarget) return;
    const v = await metaForm.validateFields();
    setSaving(true);
    try {
      await updatePrivateStrategy(metaTarget.strategy_id, {
        name: v.name as string,
        description: (v.description ?? '') as string,
      });
      message.success(t('myStrategies.msgSaved'));
      setMetaTarget(null);
      load();
    } catch { /* api() 已 toast */ } finally { setSaving(false); }
  };

  const openNewVersion = async (r: MyStrategy) => {
    setPubErr(null);
    setVersionTarget(r);
    setSpecDraft('');
    setSpecLoading(true);
    try {
      // 预填当前 spec（与 StrategiesPage 编辑器同形态：JSON 文本域）
      const c = await getMyStrategySpec(r.strategy_id);
      if (c.kind === 'spec') setSpecDraft(c.content);
    } catch { /* 读失败留空，用户可手动粘贴 */ } finally { setSpecLoading(false); }
  };

  const submitNewVersion = async () => {
    if (!versionTarget) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(specDraft);
    } catch {
      message.error(t('myStrategies.msgSpecInvalid'));
      return;
    }
    setSaving(true);
    try {
      const v = await createMyStrategyVersion(
        versionTarget.strategy_id,
        parsed as Record<string, unknown>,
      );
      message.success(t('myStrategies.msgVersionCreated', { version: v.version_no }));
      setVersionTarget(null);
      load();
    } catch { /* api() 已 toast */ } finally { setSaving(false); }
  };

  const publish = async (r: MyStrategy) => {
    setPubErr(null);
    try {
      await requestPublication(r.strategy_id);
      message.success(t('myStrategies.msgPublishRequested'));
      load();
    } catch (err) {
      // api() 对 {detail:"code"} 形态抛出的 message 含原始 JSON 文本；按 code 映射文案
      const msg = err instanceof Error ? err.message : '';
      if (msg.includes('strategy.already_published')) {
        setPubErr(t('myStrategies.errAlreadyPublished'));
      } else if (msg.includes('strategy.publication_already_pending')) {
        setPubErr(t('myStrategies.errPending'));
      } else {
        setPubErr(msg || t('myStrategies.errPublishFailed'));
      }
    }
  };

  const columns: ColumnsType<Row> = [
    { title: t('strategies.colName'), dataIndex: 'strategy_name', key: 'strategy_name', width: 180,
      render: (v: string, r) => (
        <Space size={6}>
          <b>{strategyLabel(v)}</b>
          {r.__subscribed && <Tag color="cyan">{t('myStrategies.subscribedTag')}</Tag>}
        </Space>
      ) },
    { title: t('strategies.colMarket'), dataIndex: 'market', key: 'market', width: 80,
      render: (v: string) => t(`market.${v}`, { ns: 'common' }) },
    { title: t('strategies.colStatus'), dataIndex: 'status', key: 'status', width: 90,
      render: (v: string) => statusTag(v, t) },
    { title: t('myStrategies.colPublication'), dataIndex: 'publication_status', key: 'publication_status', width: 100,
      render: (v: string) => pubTag(v, t) },
    { title: t('strategies.colVersion'), key: 'version', width: 190, render: (_: unknown, r) => {
      const vc = versionCountOf(r);
      const latest = r.latest_version_no ?? r.versions?.[vc - 1]?.version_no;
      return (
        <Space size={4} wrap>
          <Text type="secondary">{t('myStrategies.versionCount', { count: vc })}</Text>
          {latest && <Tag>{t('myStrategies.latestVersion', { version: latest })}</Tag>}
          {vc > 1 && <Tag color="blue">{t('myStrategies.hasNewVersion')}</Tag>}
        </Space>
      );
    } },
    { title: t('strategies.colAction'), key: 'action', width: 280, render: (_: unknown, r) => {
      // 订阅行只读：仅「查看」跳详情（沿用 StrategiesPage 的 version_id → /strategies/:id）；
      // 绝不渲染编辑/删除/发布等自建操作。
      if (r.__subscribed) {
        const vid = r.versions?.[0]?.strategy_version_id ?? r.strategy_id;
        return <Link to={`/strategies/${encodeURIComponent(vid)}`}>{t('myStrategies.actView')}</Link>;
      }
      return canManageOwn ? (
        <Space size={12} wrap>
          <a onClick={() => openEdit(r)}>{t('myStrategies.actEdit')}</a>
          <a onClick={() => openNewVersion(r)}>{t('myStrategies.actNewVersion')}</a>
          <span>
            <Button
              size="small"
              type="primary"
              ghost
              disabled={r.publication_status === 'PENDING_REVIEW'}
              onClick={() => publish(r)}
            >
              {t('myStrategies.actPublish')}
            </Button>
            {r.publication_status === 'PENDING_REVIEW' && (
              <div><Text type="secondary" style={{ fontSize: 12 }}>{t('myStrategies.publishPendingHint')}</Text></div>
            )}
          </span>
          <Popconfirm
            title={t('myStrategies.delConfirmTitle')}
            description={t('myStrategies.delConfirmDesc')}
            okText={t('myStrategies.delConfirmOk')}
            cancelText={t('common:action.cancel')}
            okButtonProps={{ danger: true }}
            onConfirm={() => removeStrategy(r)}
          >
            <Button size="small" danger ghost>{t('myStrategies.actDelete')}</Button>
          </Popconfirm>
        </Space>
      ) : (
        <Text type="secondary">—</Text>
      );
    } },
  ];

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>{t('myStrategies.title')}</h1>
        <Tag color="blue" style={{ fontSize: 13, padding: '2px 10px' }}>
          {t(`market.${market}`, { ns: 'common' })}（{market}）
        </Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>{t('myStrategies.hint')}</Text>
        <div style={{ flex: 1 }} />
        {canManageOwn && (
          <Button type="primary" onClick={openCreate}>{t('myStrategies.createBtn')}</Button>
        )}
        <Button onClick={load}>{t('strategies.refresh')}</Button>
      </div>

      {pubErr && (
        <Alert
          type="warning"
          showIcon
          closable
          style={{ marginBottom: 12 }}
          message={pubErr}
          onClose={() => setPubErr(null)}
        />
      )}

      <Table<MyStrategy>
        rowKey="strategy_id"
        size="small"
        loading={loading}
        dataSource={rows}
        columns={columns}
        pagination={{ pageSize: 20, showSizeChanger: true }}
      />

      {/* 编辑元信息（名称/描述；spec 不可变，改参数走「新版本」） */}
      <Modal
        title={t('myStrategies.editTitle')}
        open={!!metaTarget}
        onOk={saveMeta}
        onCancel={() => setMetaTarget(null)}
        okText={t('common:action.save')}
        cancelText={t('common:action.cancel')}
        confirmLoading={saving}
      >
        <Form form={metaForm} layout="vertical">
          <Form.Item label={t('myStrategies.formName')} name="name"
            rules={[{ required: true, message: t('myStrategies.ruleName') }]}>
            <Input maxLength={128} />
          </Form.Item>
          <Form.Item label={t('myStrategies.formDesc')} name="description">
            <TextArea rows={2} maxLength={512} placeholder={t('strategies.phDesc')} />
          </Form.Item>
          <Paragraph type="secondary" style={{ fontSize: 12 }}>
            {t('myStrategies.editSpecHint')}
          </Paragraph>
        </Form>
      </Modal>

      {/* 创建新版本（spec 不可变编辑=新建 vN+1，旧版本行绝不改动） */}
      <Modal
        title={t('myStrategies.newVersionTitle')}
        open={!!versionTarget}
        onOk={submitNewVersion}
        onCancel={() => setVersionTarget(null)}
        okText={t('myStrategies.createVersionOk')}
        cancelText={t('common:action.cancel')}
        confirmLoading={saving}
        width={640}
      >
        {versionTarget && (
          <>
            <Paragraph type="warning" style={{ fontSize: 13 }}>
              {t('myStrategies.newVersionConfirm', {
                name: strategyLabel(versionTarget.strategy_name),
                version: nextVersionNo(versionTarget),
              })}
            </Paragraph>
            <Form layout="vertical">
              <Form.Item label={t('myStrategies.specLabel')} htmlFor="my-new-version-spec">
                <TextArea
                  id="my-new-version-spec"
                  value={specDraft}
                  onChange={(e) => setSpecDraft(e.target.value)}
                  rows={12}
                  disabled={specLoading}
                  placeholder={specLoading ? t('strategies.loading') : '{}'}
                  style={{ fontFamily: 'monospace', fontSize: 12 }}
                />
              </Form.Item>
            </Form>
          </>
        )}
      </Modal>

      {/* 新建私有策略（B5：createPrivateStrategy 的首个调用方） */}
      <Modal
        title={t('myStrategies.createTitle')}
        open={createOpen}
        onOk={submitCreate}
        onCancel={() => setCreateOpen(false)}
        okText={t('myStrategies.createBtn')}
        cancelText={t('common:action.cancel')}
        confirmLoading={creating}
        width={640}
      >
        <Form form={createForm} layout="vertical">
          <Form.Item label={t('myStrategies.formName')} name="strategy_name"
            rules={[{ required: true, message: t('myStrategies.ruleName') }]}>
            <Input maxLength={128} placeholder={t('myStrategies.createNamePlaceholder')} />
          </Form.Item>
          <Form.Item label={t('strategies.colMarket')} name="market"
            rules={[{ required: true }]}>
            <Select
              options={MARKETS.map((m) => ({
                value: m,
                label: `${t(`market.${m}`, { ns: 'common' })}（${m}）`,
              }))}
            />
          </Form.Item>
          <Form.Item label={t('myStrategies.specLabel')} htmlFor="my-create-spec">
            <TextArea
              id="my-create-spec"
              value={createSpecDraft}
              onChange={(e) => setCreateSpecDraft(e.target.value)}
              rows={10}
              style={{ fontFamily: 'monospace', fontSize: 12 }}
            />
          </Form.Item>
          <Paragraph type="secondary" style={{ fontSize: 12 }}>
            {t('myStrategies.createSpecHint')}
          </Paragraph>
        </Form>
      </Modal>
    </div>
  );
}
