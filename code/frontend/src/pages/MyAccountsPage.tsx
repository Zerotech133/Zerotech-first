/**
 * 我的账户（Phase D Task 5）：本人资金账户，挂 StrategiesGroup ?tab=accounts
 * （canManageOwn=USER/ADMIN 才可见 tab）。
 *
 * 能力边界：
 * - 列表：listMyAccounts(market) owner-scoped；可用资金 = 现金余额 − 冻结中
 *   （前端计算展示，后端无该字段）；
 * - 类型徽章：STRATEGY_DEDICATED=独立策略子账户 / SHARED_PORTFOLIO=共享组合
 *   （domain UserAccount.AccountType 仅此两种）；
 * - 行展开：共享账户 → 该账户下实例分组（listMyInstances 按 account_id 前端
 *   分组）+ 配额占用合计（启用实例 capital_quota 求和）；独立账户 → 单一实例提示；
 * - 新建：createMyAccount（market/account_type/initial_capital>0），市场下拉对齐
 *   MARKETS（VITE_ENABLED_MARKETS 过滤后的启用市场），绝不携带 user_id。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert, Button, Form, InputNumber, Modal, Select, Space, Switch, Table, Tag, Typography, message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { createMyAccount, listMyAccounts, type MyAccount } from '../services/myAccountsApi';
import { listMyInstances, type MyStrategyInstance } from '../services/myStrategiesApi';
import { MARKETS, useMarket } from '../contexts/MarketContext';
import { useRole } from '../hooks/useRole';

const { Text } = Typography;

/** 账户类型徽章：与 domain AccountType 一一对应（仅两种）。 */
function typeTag(accountType: string, t: (k: string) => string) {
  if (accountType === 'STRATEGY_DEDICATED') {
    return <Tag color="geekblue">{t('myAccounts.typeDedicated')}</Tag>;
  }
  if (accountType === 'SHARED_PORTFOLIO') {
    return <Tag color="cyan">{t('myAccounts.typeShared')}</Tag>;
  }
  return <Tag>{accountType}</Tag>;
}

/** 金额展示（与 AccountsPage 同风格千分位；后端 Decimal→string）。 */
function fmtMoney(v: string | number | null): string {
  if (v === null) return '—';
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString('en-US', { maximumFractionDigits: 2 }) : '—';
}

export default function MyAccountsPage() {
  const { t } = useTranslation('pages');
  const { market } = useMarket();
  const { canManageOwn } = useRole();
  const [rows, setRows] = useState<MyAccount[]>([]);
  const [instances, setInstances] = useState<MyStrategyInstance[]>([]);
  const [loading, setLoading] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    // 实例仅用于行展开分组（按 account_id 前端分组），与账户同市场拉取。
    Promise.all([listMyAccounts(market), listMyInstances(market)])
      .then(([accs, insts]) => { setRows(accs); setInstances(insts); })
      .catch(() => { setRows([]); setInstances([]); })
      .finally(() => setLoading(false));
  }, [market]);

  useEffect(() => { load(); }, [load]);

  /** account_id → 绑定实例（owner-scoped 全量按账户分组）。 */
  const instancesByAccount = useMemo(() => {
    const m = new Map<string, MyStrategyInstance[]>();
    for (const i of instances) {
      const list = m.get(i.account_id) ?? [];
      list.push(i);
      m.set(i.account_id, list);
    }
    return m;
  }, [instances]);

  const submitCreate = async () => {
    // 校验失败：错误提示由 Form 渲染，吞掉 rejection 避免 unhandled error
    const v = await createForm.validateFields().catch(() => null);
    if (!v) return;
    setSaving(true);
    try {
      await createMyAccount({
        market: v.market as string,
        account_type: v.account_type as string,
        initial_capital: v.initial_capital as number,
      });
      message.success(t('myAccounts.msgCreated'));
      setCreateOpen(false);
      createForm.resetFields();
      load();
    } catch { /* api() 已 toast */ } finally { setSaving(false); }
  };

  const columns: ColumnsType<MyAccount> = [
    { title: t('myAccounts.colAccountId'), dataIndex: 'account_id', key: 'account_id', width: 160,
      render: (v: string) => <Text code style={{ fontSize: 11 }}>{v}</Text> },
    { title: t('strategies.colMarket'), dataIndex: 'market', key: 'market', width: 80,
      render: (v: string) => t(`market.${v}`, { ns: 'common' }) },
    { title: t('myAccounts.colCurrency'), dataIndex: 'currency', key: 'currency', width: 70 },
    { title: t('myAccounts.colType'), dataIndex: 'account_type', key: 'account_type', width: 130,
      render: (v: string) => typeTag(v, t) },
    { title: t('myAccounts.colInitial'), dataIndex: 'initial_capital', key: 'initial_capital', width: 110, align: 'right',
      render: (v: string) => fmtMoney(v) },
    { title: t('myAccounts.colCash'), dataIndex: 'cash_balance', key: 'cash_balance', width: 110, align: 'right',
      render: (v: string) => fmtMoney(v) },
    { title: t('myAccounts.colReserved'), dataIndex: 'reserved_cash', key: 'reserved_cash', width: 100, align: 'right',
      render: (v: string) => fmtMoney(v) },
    { title: t('myAccounts.colAvailable'), key: 'available', width: 120, align: 'right',
      render: (_: unknown, r) => (
        <b>{fmtMoney(Number(r.cash_balance) - Number(r.reserved_cash))}</b>
      ) },
    { title: t('myAccounts.colStatus'), dataIndex: 'status', key: 'status', width: 90,
      render: (v: string) => (v === 'ACTIVE'
        ? <Tag color="green">{v}</Tag>
        : <Tag>{v}</Tag>) },
  ];

  /** 行展开：共享→实例分组+配额合计；独立→单一实例提示。 */
  const renderExpanded = (r: MyAccount) => {
    const bound = instancesByAccount.get(r.account_id) ?? [];
    const available = Number(r.cash_balance) - Number(r.reserved_cash);
    if (r.account_type === 'SHARED_PORTFOLIO') {
      const quotaTotal = bound
        .filter((i) => i.enabled)
        .reduce((sum, i) => sum + Number(i.capital_quota ?? 0), 0);
      return (
        <div>
          <Space size={16} style={{ marginBottom: 8 }}>
            <Text>
              {t('myAccounts.expandAvailable')}：
              <b>{fmtMoney(available)}</b> {r.currency}
            </Text>
            <Text>
              {t('myAccounts.expandQuotaTotal')}：
              <b>{fmtMoney(quotaTotal)}</b> {r.currency}
            </Text>
          </Space>
          <Table<MyStrategyInstance>
            rowKey="user_strategy_id"
            size="small"
            dataSource={bound}
            pagination={false}
            columns={[
              { title: t('myAccounts.expandColInstance'), dataIndex: 'user_strategy_id', key: 'user_strategy_id',
                render: (v: string) => <Text code style={{ fontSize: 11 }}>{v}</Text> },
              { title: t('myAccounts.expandColVersion'), dataIndex: 'strategy_version_id', key: 'strategy_version_id',
                render: (v: string) => <Text code style={{ fontSize: 11 }}>{v}</Text> },
              { title: t('myAccounts.expandColEnabled'), dataIndex: 'enabled', key: 'enabled', width: 80,
                render: (v: boolean) => <Switch checked={v} size="small" disabled /> },
              { title: t('myAccounts.expandColQuota'), dataIndex: 'capital_quota', key: 'capital_quota', width: 110, align: 'right',
                render: (v: string | null) => fmtMoney(v) },
            ]}
          />
        </div>
      );
    }
    return (
      <div>
        <Text type="secondary">{t('myAccounts.dedicatedSingleHint')}</Text>
        {bound.length === 0 && (
          <div><Text type="secondary">{t('myAccounts.noInstance')}</Text></div>
        )}
        {bound.map((i) => (
          <div key={i.user_strategy_id}>
            <Text code style={{ fontSize: 11 }}>{i.user_strategy_id}</Text>
            <Tag style={{ marginLeft: 8 }}>{i.enabled ? t('myAccounts.enabledOn') : t('myAccounts.enabledOff')}</Tag>
          </div>
        ))}
      </div>
    );
  };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>{t('myAccounts.title')}</h1>
        <Tag color="blue" style={{ fontSize: 13, padding: '2px 10px' }}>
          {t(`market.${market}`, { ns: 'common' })}（{market}）
        </Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>{t('myAccounts.hint')}</Text>
        <div style={{ flex: 1 }} />
        <Button onClick={load}>{t('strategies.refresh')}</Button>
        {canManageOwn && (
          <Button type="primary" onClick={() => { createForm.resetFields(); setCreateOpen(true); }}>
            {t('myAccounts.createBtn')}
          </Button>
        )}
      </div>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message={t('myAccounts.availableHint')}
      />

      <Table<MyAccount>
        rowKey="account_id"
        size="small"
        loading={loading}
        dataSource={rows}
        columns={columns}
        pagination={{ pageSize: 20, showSizeChanger: true }}
        expandable={{ expandedRowRender: renderExpanded }}
      />

      {/* 新建账户（仅 canManageOwn；请求体只含 market/account_type/initial_capital） */}
      <Modal
        title={t('myAccounts.createTitle')}
        open={createOpen}
        onOk={submitCreate}
        onCancel={() => setCreateOpen(false)}
        okText={t('myAccounts.createOk')}
        cancelText={t('common:action.cancel')}
        confirmLoading={saving}
      >
        <Form form={createForm} layout="vertical">
          <Form.Item label={t('myAccounts.formMarket')} name="market"
            rules={[{ required: true, message: t('myAccounts.ruleMarket') }]}>
            {/* 市场下拉对齐启用市场（VITE_ENABLED_MARKETS 过滤，MarketContext.MARKETS） */}
            <Select
              placeholder={t('myAccounts.phMarket')}
              options={MARKETS.map((m) => ({
                value: m,
                label: `${t(`market.${m}`, { ns: 'common' })}（${m}）`,
              }))}
            />
          </Form.Item>
          <Form.Item label={t('myAccounts.formType')} name="account_type"
            rules={[{ required: true, message: t('myAccounts.ruleType') }]}>
            <Select
              placeholder={t('myAccounts.phType')}
              options={[
                { value: 'STRATEGY_DEDICATED', label: t('myAccounts.typeDedicated') },
                { value: 'SHARED_PORTFOLIO', label: t('myAccounts.typeShared') },
              ]}
            />
          </Form.Item>
          <Form.Item label={t('myAccounts.formCapital')} name="initial_capital"
            rules={[{
              validator: (_, v: number | null) =>
                (v !== null && v !== undefined && v > 0)
                  ? Promise.resolve()
                  : Promise.reject(new Error(t('myAccounts.ruleCapital'))),
            }]}>
            <InputNumber style={{ width: '100%' }} step={10000} placeholder="100000" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
