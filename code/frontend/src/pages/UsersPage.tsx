import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Table, Button, Modal, Form, Input, Select, Tag, Space, Popconfirm, Tooltip, Alert, message,
} from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { api } from '../services/api';
import { listUsers } from '../services/adminEntitlementsApi';
import EntitlementDrawer from '../components/admin-entitlements/EntitlementDrawer';
import type { EffectiveStatus, UserSummary } from '../components/admin-entitlements/entitlementModel';

const ROLE_LABEL_KEYS: Record<string, string> = {
  ADMIN: 'users.roleAdmin', USER: 'users.roleUser', TRADER: 'users.roleTrader',
  RESEARCHER: 'users.roleResearcher', RISK_MANAGER: 'users.roleRisk', VIEWER: 'users.roleViewer',
};
// USER 补齐；顺序与规格一致（ADMIN,USER,TRADER,RESEARCHER,RISK_MANAGER,VIEWER）。
const ALL_ROLES = ['ADMIN', 'USER', 'TRADER', 'RESEARCHER', 'RISK_MANAGER', 'VIEWER'];

const ROLE_COLOR: Record<string, string> = {
  ADMIN: 'geekblue', USER: 'blue', TRADER: 'cyan', RESEARCHER: 'purple',
  RISK_MANAGER: 'volcano', VIEWER: 'default',
};

const MEM_LABEL_KEYS: Record<EffectiveStatus, string> = {
  free: 'users.memFree', active: 'users.memActive', permanent: 'users.memPermanent',
  expired: 'users.memExpired', revoked: 'users.memRevoked',
};
const MEM_COLOR: Record<EffectiveStatus, string> = {
  free: 'default', active: 'green', permanent: 'gold', expired: 'orange', revoked: 'red',
};

const PAGE_SIZE = 20;

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export default function UsersPage() {
  const { t } = useTranslation('pages');
  const roleLabel = (r: string) => t(ROLE_LABEL_KEYS[r] ?? r);
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  // 当前操作者 id：抽屉据此判断"自降级"（改自己角色为非 ADMIN）后需登出。
  const [actorId, setActorId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [pwTarget, setPwTarget] = useState<string | null>(null);
  const [createForm] = Form.useForm();
  const [pwForm] = Form.useForm();

  const load = async () => {
    setLoading(true);
    try {
      const res = await listUsers({ page, pageSize: PAGE_SIZE, query });
      setUsers(res.items);
      setTotal(res.total);
      setLoadError(null);
    } catch (error) {
      // 查询失败严禁投影成"免费"：有旧数据则保留并提示过期，无数据则进入错误态。
      if (users.length > 0) {
        message.warning(t('users.staleWarning'));
      } else {
        setLoadError(errorText(error));
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, query]);

  useEffect(() => {
    api<{ user_id: string }>('/auth/me')
      .then((u) => setActorId(u.user_id))
      .catch(() => setActorId(null));
  }, []);

  const handleCreate = async () => {
    try {
      const v = await createForm.validateFields();
      await api('/auth/users', { method: 'POST', body: JSON.stringify(v) });
      message.success(t('users.createSuccess'));
      setCreateOpen(false);
      createForm.resetFields();
      await load();
    } catch (error) {
      message.error(t('users.createFailed', { err: errorText(error) }));
    }
  };

  const handleDeactivate = async (user_id: string, auth_version: number) => {
    try {
      await api(`/auth/users/${user_id}`, {
        method: 'DELETE', body: JSON.stringify({ auth_version }),
      });
      message.success(t('users.deactivated'));
      await load();
    } catch (error) {
      message.error(t('users.deactivateFailed', { err: errorText(error) }));
    }
  };

  const handlePw = async () => {
    try {
      const v = await pwForm.validateFields();
      await api(`/auth/users/${pwTarget}/password`, { method: 'PUT', body: JSON.stringify({ new_password: v.new_password }) });
      message.success(t('users.pwChanged'));
      setPwTarget(null);
      pwForm.resetFields();
    } catch (error) {
      message.error(t('users.pwChangeFailed', { err: errorText(error) }));
    }
  };

  const renderMembership = (_: unknown, row: UserSummary) => {
    const st = row.membership.effective_status;
    return (
      <Space size={4}>
        <Tag color={MEM_COLOR[st]}>{t(MEM_LABEL_KEYS[st])}</Tag>
        {st === 'active' && row.membership.valid_until && (
          <span style={{ fontSize: 12, opacity: 0.7 }}>
            {t('users.validUntil', { date: row.membership.valid_until.slice(0, 10) })}
          </span>
        )}
      </Space>
    );
  };

  const columns = [
    { title: t('users.colUsername'), dataIndex: 'username', key: 'username' },
    {
      title: t('users.colRole'), dataIndex: 'role', key: 'role',
      render: (role: string) => <Tag color={ROLE_COLOR[role] ?? 'default'}>{roleLabel(role)}</Tag>,
    },
    { title: t('users.colMembership'), key: 'membership', render: renderMembership },
    {
      title: t('users.colSubscriptions'), dataIndex: 'active_subscription_count', key: 'subs',
      render: (n: number) => <span>{n}</span>,
    },
    {
      title: t('users.colStatus'), dataIndex: 'is_active', key: 'is_active',
      render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? t('users.statusActive') : t('users.statusInactive')}</Tag>,
    },
    {
      title: t('users.colActions'), key: 'actions',
      render: (_: unknown, row: UserSummary) => (
        <Space>
          <Button size="small" type="link" onClick={() => setSelectedUserId(row.user_id)}>
            {t('users.btnEntitlements')}
          </Button>
          <Tooltip title={t('users.pendingPaymentsTip')}>
            <Button size="small" type="link" disabled>{t('users.btnPendingPayments')}</Button>
          </Tooltip>
          <Button size="small" onClick={() => setPwTarget(row.user_id)}>{t('users.btnChangePw')}</Button>
          {row.is_active && (
            <Popconfirm title={t('users.confirmDeactivate')} onConfirm={() => handleDeactivate(row.user_id, row.auth_version)}>
              <Button size="small" danger>{t('users.btnDeactivate')}</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  const showErrorState = loadError !== null && users.length === 0;

  return (
    <div style={{ padding: '16px 0' }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <span style={{ fontWeight: 600, fontSize: 15 }}>{t('users.title')}</span>
        <Space>
          <Input.Search
            allowClear
            placeholder={t('users.searchPlaceholder')}
            style={{ width: 220 }}
            onSearch={(v) => { setPage(1); setQuery(v.trim()); }}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>{t('users.refresh')}</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>{t('users.newUser')}</Button>
        </Space>
      </div>

      {showErrorState ? (
        <Alert
          type="error"
          showIcon
          message={t('users.loadErrorTitle')}
          description={loadError}
          action={<Button size="small" onClick={() => void load()}>{t('users.retry')}</Button>}
        />
      ) : (
        <Table
          dataSource={users}
          columns={columns}
          rowKey="user_id"
          loading={loading}
          size="middle"
          rowClassName={(row) => (row.user_id === selectedUserId ? 'ant-table-row-selected' : '')}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            showSizeChanger: false,
            onChange: (p) => setPage(p),
          }}
        />
      )}

      <Modal title={t('users.newUser')} open={createOpen} onOk={handleCreate} onCancel={() => setCreateOpen(false)} okText={t('users.okCreate')}>
        <Form form={createForm} layout="vertical">
          <Form.Item name="username" label={t('users.colUsername')} rules={[{ required: true, message: t('users.ruleUsername') }]}>
            <Input />
          </Form.Item>
          <Form.Item name="password" label={t('users.formInitPassword')} rules={[{ required: true, min: 6, message: t('users.rulePwMin') }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="role" label={t('users.colRole')} initialValue="VIEWER">
            <Select options={ALL_ROLES.map(r => ({ value: r, label: roleLabel(r) }))} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={t('users.modalPwTitle')} open={!!pwTarget} onOk={handlePw} onCancel={() => setPwTarget(null)} okText={t('common:action.save')}>
        <Form form={pwForm} layout="vertical">
          <Form.Item name="new_password" label={t('users.formNewPassword')} rules={[{ required: true, min: 6, message: t('users.rulePwMin') }]}>
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>

      <EntitlementDrawer
        userId={selectedUserId}
        open={!!selectedUserId}
        onClose={() => setSelectedUserId(null)}
        onChanged={() => void load()}
        actorUserId={actorId}
      />
    </div>
  );
}
