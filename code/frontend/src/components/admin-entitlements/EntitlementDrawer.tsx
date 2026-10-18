// B3-3/B3-4/B3-5 管理员权益抽屉：只读四区 + 三块独立写操作（角色/会员/订阅）。
// 可靠性：userId 变更 ++epoch + abort 旧请求，迟到响应按 epoch 丢弃；切换用户清空旧详情。
// 写操作：每块独立 loading、无"全部保存"；危险降级（ADMIN→非ADMIN）与撤销会员/订阅二次确认；
// 会员 until/permanent 互斥（free 用 version 0）；订阅候选来自 grantable 端点、versions 从详情取（缺=0）；
// inactive 用户可改角色/撤销已有权益，但设置会员/授权订阅禁用；写成功合并后 reload，reload 失败给重载入口。
// B3-5：写超时=未知结果 → 冻结同一 thunk（同幂等键同请求体），显示"重试原操作"，不自动重发、不换新键；
// 4xx 为已知失败不提供重试（下次操作用新键）；自降级（目标==当前 actor 且改非 ADMIN）成功即清 token+role 跳登录。
import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Drawer, Card, Descriptions, Table, Tag, Spin, Alert, Space, Select, Radio,
  DatePicker, Input, Button, Modal, message,
} from 'antd';
import type { Dayjs } from 'dayjs';
import {
  getUserDetail, changeRole, setMembership, revokeMembership,
  setSubscriptions, revokeSubscriptions, listGrantableStrategies,
} from '../../services/adminEntitlementsApi';
import {
  newIdempotencyKey, AdminApiError,
  type EffectiveStatus, type UserDetail, type SubscriptionDetail,
  type AuditView, type GrantableStrategy, type EntitlementMode,
} from './entitlementModel';

const ALL_ROLES = ['ADMIN', 'USER', 'TRADER', 'RESEARCHER', 'RISK_MANAGER', 'VIEWER'];
const ROLE_LABEL_KEYS: Record<string, string> = {
  ADMIN: 'users.roleAdmin', USER: 'users.roleUser', TRADER: 'users.roleTrader',
  RESEARCHER: 'users.roleResearcher', RISK_MANAGER: 'users.roleRisk', VIEWER: 'users.roleViewer',
};
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

type Thunk = () => Promise<void>;

function fmtDate(iso: string | null): string {
  return iso ? iso.slice(0, 19).replace('T', ' ') : '—';
}

interface Props {
  userId: string | null;
  open: boolean;
  onClose: () => void;
  onChanged?: () => void;
  actorUserId?: string | null;
}

export default function EntitlementDrawer({ userId, open, onClose, onChanged, actorUserId }: Props) {
  const { t } = useTranslation('pages');
  const epochRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [grantable, setGrantable] = useState<GrantableStrategy[]>([]);

  // 角色块
  const [roleValue, setRoleValue] = useState<string>('');
  const [roleReason, setRoleReason] = useState('');
  const [roleBusy, setRoleBusy] = useState(false);
  const [roleRetry, setRoleRetry] = useState<Thunk | null>(null);
  const [demoteOpen, setDemoteOpen] = useState(false);
  // 会员块
  const [memMode, setMemMode] = useState<EntitlementMode>('until');
  const [memUntil, setMemUntil] = useState<Dayjs | null>(null);
  const [memReason, setMemReason] = useState('');
  const [memBusy, setMemBusy] = useState(false);
  const [memRetry, setMemRetry] = useState<Thunk | null>(null);
  const [revokeMemOpen, setRevokeMemOpen] = useState(false);
  // 订阅块
  const [subSelected, setSubSelected] = useState<string[]>([]);
  const [subMode, setSubMode] = useState<EntitlementMode>('until');
  const [subUntil, setSubUntil] = useState<Dayjs | null>(null);
  const [subReason, setSubReason] = useState('');
  const [subBusy, setSubBusy] = useState(false);
  const [subRetry, setSubRetry] = useState<Thunk | null>(null);
  const [revokeSubTarget, setRevokeSubTarget] = useState<SubscriptionDetail | null>(null);

  const syncForms = useCallback((d: UserDetail) => {
    setRoleValue(d.user.role);
    setRoleReason(''); setMemReason(''); setSubSelected([]); setSubReason('');
    setRoleRetry(null); setMemRetry(null); setSubRetry(null);
  }, []);

  const reload = useCallback(async (): Promise<boolean> => {
    if (!userId) return false;
    const epoch = ++epochRef.current;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setLoading(true);
    try {
      const d = await getUserDetail(userId, ac.signal);
      if (epoch === epochRef.current) { setDetail(d); syncForms(d); setError(null); }
      return true;
    } catch (e) {
      if (epoch === epochRef.current && !ac.signal.aborted) {
        setError(e instanceof Error ? e.message : String(e));
      }
      return false;
    } finally {
      if (epoch === epochRef.current) setLoading(false);
    }
  }, [userId, syncForms]);

  useEffect(() => {
    if (!open || !userId) { setDetail(null); setError(null); return; }
    setDetail(null);
    void reload();
    return () => abortRef.current?.abort();
  }, [userId, open, reload]);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    listGrantableStrategies()
      .then((r) => { if (alive) setGrantable(r.items); })
      .catch(() => { if (alive) setGrantable([]); });
    return () => { alive = false; };
  }, [open]);

  const logoutSelf = () => {
    localStorage.removeItem('zhice_token');
    localStorage.removeItem('zhice_role');
    window.location.href = '/login';
  };

  /** 写操作统一执行：成功→（自定义 onSuccess 或 默认 reload+onChanged）；
   *  retryable（超时/网络=未知结果）→冻结同一 thunk 供"重试原操作"（同键同体，不自动重发）；
   *  4xx 已知失败→清除重试（下次操作新键），错误提示由 api() 统一 toast。 */
  async function runWrite(
    setBusy: Dispatch<SetStateAction<boolean>>,
    setRetry: Dispatch<SetStateAction<Thunk | null>>,
    thunk: Thunk,
    onSuccess?: () => void,
  ): Promise<void> {
    setBusy(true);
    setRetry(null);
    try {
      await thunk();
      if (onSuccess) { onSuccess(); return; }
      message.success(t('users.writeSuccess'));
      const ok = await reload();
      if (!ok) message.warning(t('users.refreshFailedAfterWrite'));
      onChanged?.();
    } catch (e) {
      const err = e as AdminApiError;
      if (err && err.retryable) setRetry(() => thunk);
    } finally {
      setBusy(false);
    }
  }

  const m = detail?.membership;
  const effStatus = m?.effective_status ?? 'free';
  const isActive = detail?.user.is_active ?? false;
  const versionOf = (sid: string): number =>
    detail?.subscriptions.find((s) => s.strategy_id === sid)?.version ?? 0;

  // ── 角色 ──
  const submitRole = () => {
    if (!detail) return;
    if (!roleReason.trim()) { message.error(t('users.reasonRequired')); return; }
    if (detail.user.role === 'ADMIN' && roleValue !== 'ADMIN') { setDemoteOpen(true); return; }
    doChangeRole();
  };
  const doChangeRole = () => {
    if (!detail) return;
    setDemoteOpen(false);
    const key = newIdempotencyKey();
    const target = detail.user.user_id;
    const role = roleValue;
    const payload = { role, authVersion: detail.user.auth_version, reason: roleReason.trim() };
    const thunk: Thunk = async () => { await changeRole(target, payload, key); };
    const selfDemote = !!actorUserId && target === actorUserId && role !== 'ADMIN';
    void runWrite(setRoleBusy, setRoleRetry, thunk, selfDemote ? logoutSelf : undefined);
  };

  // ── 会员 ──
  const submitMembership = () => {
    if (!detail) return;
    if (!memReason.trim()) { message.error(t('users.reasonRequired')); return; }
    if (memMode === 'until' && !memUntil) { message.error(t('users.memValidUntilRequired')); return; }
    const key = newIdempotencyKey();
    const target = detail.user.user_id;
    const input = {
      mode: memMode, validUntil: memMode === 'until' ? memUntil?.toDate() : undefined,
      reason: memReason.trim(), version: detail.membership.version,
    };
    const thunk: Thunk = async () => { await setMembership(target, input, key); };
    void runWrite(setMemBusy, setMemRetry, thunk);
  };
  const doRevokeMembership = () => {
    if (!detail) return;
    setRevokeMemOpen(false);
    const key = newIdempotencyKey();
    const target = detail.user.user_id;
    const reason = memReason.trim() || t('users.memRevoke');
    const version = detail.membership.version;
    const thunk: Thunk = async () => { await revokeMembership(target, { reason, version }, key); };
    void runWrite(setMemBusy, setMemRetry, thunk);
  };

  // ── 订阅 ──
  const submitGrant = () => {
    if (!detail) return;
    if (subSelected.length === 0) { message.error(t('users.subNoneSelected')); return; }
    if (!subReason.trim()) { message.error(t('users.reasonRequired')); return; }
    if (subMode === 'until' && !subUntil) { message.error(t('users.memValidUntilRequired')); return; }
    const key = newIdempotencyKey();
    const target = detail.user.user_id;
    const versions = Object.fromEntries(subSelected.map((sid) => [sid, versionOf(sid)]));
    const input = {
      strategyIds: subSelected, mode: subMode,
      validUntil: subMode === 'until' ? subUntil?.toDate() : undefined,
      reason: subReason.trim(), versions,
    };
    const thunk: Thunk = async () => { await setSubscriptions(target, input, key); };
    void runWrite(setSubBusy, setSubRetry, thunk);
  };
  const doRevokeSub = () => {
    if (!detail || !revokeSubTarget) return;
    const sid = revokeSubTarget.strategy_id;
    setRevokeSubTarget(null);
    const key = newIdempotencyKey();
    const target = detail.user.user_id;
    const reason = subReason.trim() || t('users.subRevokeOne');
    const thunk: Thunk = async () => {
      await revokeSubscriptions(target, { strategyIds: [sid], reason, versions: { [sid]: versionOf(sid) } }, key);
    };
    void runWrite(setSubBusy, setSubRetry, thunk);
  };

  const retryButton = (retry: Thunk | null, setBusy: Dispatch<SetStateAction<boolean>>, setRetry: Dispatch<SetStateAction<Thunk | null>>) =>
    retry ? (
      <Button danger onClick={() => void runWrite(setBusy, setRetry, retry)}>
        {t('users.retryOriginal')}
      </Button>
    ) : null;

  const subColumns = [
    { title: t('users.subColStrategy'), dataIndex: 'strategy_id', key: 'strategy_id' },
    {
      title: t('users.subColStatus'), dataIndex: 'effective_status', key: 'effective_status',
      render: (st: EffectiveStatus) => <Tag color={MEM_COLOR[st]}>{t(MEM_LABEL_KEYS[st])}</Tag>,
    },
    {
      title: t('users.subColValidUntil'), dataIndex: 'valid_until', key: 'valid_until',
      render: (v: string | null, row: SubscriptionDetail) => (row.is_permanent ? t('users.memPermanent') : fmtDate(v)),
    },
    { title: t('users.subColVersion'), dataIndex: 'version', key: 'version' },
    {
      title: t('users.colActions'), key: 'op',
      render: (_: unknown, row: SubscriptionDetail) =>
        row.effective_status !== 'revoked'
          ? <Button size="small" danger onClick={() => setRevokeSubTarget(row)}>{t('users.subRevokeOne')}</Button>
          : null,
    },
  ];

  const auditColumns = [
    { title: t('users.auditColTime'), dataIndex: 'created_at', key: 'created_at', render: (v: string | null) => fmtDate(v) },
    { title: t('users.auditColAction'), dataIndex: 'action', key: 'action' },
    { title: t('users.auditColActor'), dataIndex: 'actor_user_id', key: 'actor_user_id' },
    { title: t('users.auditColReason'), dataIndex: 'reason', key: 'reason' },
  ];

  return (
    <Drawer
      title={detail ? `${t('users.drawerTitle')} · ${detail.user.username}` : t('users.drawerTitle')}
      open={open}
      onClose={onClose}
      size={820}
      destroyOnClose
    >
      {loading && !detail ? (
        <div style={{ textAlign: 'center', padding: 48 }}><Spin /></div>
      ) : error && !detail ? (
        <Alert
          type="error" showIcon title={t('users.drawerLoadError')} description={error}
          action={<Button size="small" onClick={() => void reload()}>{t('users.reloadDetail')}</Button>}
        />
      ) : detail ? (
        <Space orientation="vertical" size={16} style={{ width: '100%' }}>
          {/* 角色区 */}
          <div data-testid="ent-role-section">
            <Card size="small" title={t('users.secRole')}>
              <Descriptions column={2} size="small" style={{ marginBottom: 12 }}>
                <Descriptions.Item label={t('users.colUsername')}>{detail.user.username}</Descriptions.Item>
                <Descriptions.Item label={t('users.colStatus')}>
                  <Tag color={isActive ? 'green' : 'default'}>
                    {isActive ? t('users.statusActive') : t('users.statusInactive')}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="auth_version">{detail.user.auth_version}</Descriptions.Item>
              </Descriptions>
              <Space wrap>
                <Select
                  value={roleValue}
                  style={{ width: 140 }}
                  onChange={setRoleValue}
                  options={ALL_ROLES.map((r) => ({ value: r, label: t(ROLE_LABEL_KEYS[r] ?? r) }))}
                />
                <Input
                  placeholder={t('users.writeReason')} value={roleReason}
                  style={{ width: 200 }} onChange={(e) => setRoleReason(e.target.value)}
                />
                <Button type="primary" loading={roleBusy} onClick={submitRole}>{t('users.roleSubmit')}</Button>
                {retryButton(roleRetry, setRoleBusy, setRoleRetry)}
                <Tag color={ROLE_COLOR[detail.user.role] ?? 'default'}>
                  {t(ROLE_LABEL_KEYS[detail.user.role] ?? detail.user.role)}
                </Tag>
              </Space>
            </Card>
          </div>

          {/* 会员区 */}
          <div data-testid="ent-membership-section">
            <Card size="small" title={t('users.secMembership')}>
              <Descriptions column={2} size="small" style={{ marginBottom: 12 }}>
                <Descriptions.Item label="tier">{m?.tier ?? 'free'}</Descriptions.Item>
                <Descriptions.Item label={t('users.fieldEffective')}>
                  <Tag color={MEM_COLOR[effStatus]}>{t(MEM_LABEL_KEYS[effStatus])}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label={t('users.fieldStored')}>{m?.stored_status ?? '—'}</Descriptions.Item>
                <Descriptions.Item label={t('users.subColValidUntil')}>
                  {m?.is_permanent ? t('users.memPermanent') : fmtDate(m?.valid_until ?? null)}
                </Descriptions.Item>
                <Descriptions.Item label={t('users.fieldRevokedAt')}>{fmtDate(m?.revoked_at ?? null)}</Descriptions.Item>
                <Descriptions.Item label={t('users.fieldVersion')}>{m?.version ?? 0}</Descriptions.Item>
              </Descriptions>
              {!isActive && <Alert type="warning" showIcon style={{ marginBottom: 12 }} title={t('users.inactiveCannotGrant')} />}
              <Space wrap>
                <Radio.Group value={memMode} onChange={(e) => setMemMode(e.target.value)}>
                  <Radio value="until">{t('users.memModeUntil')}</Radio>
                  <Radio value="permanent">{t('users.memModePermanent')}</Radio>
                </Radio.Group>
                {memMode === 'until' && <DatePicker showTime value={memUntil} onChange={setMemUntil} />}
                <Input
                  placeholder={t('users.writeReason')} value={memReason}
                  style={{ width: 180 }} onChange={(e) => setMemReason(e.target.value)}
                />
                <Button type="primary" loading={memBusy} disabled={!isActive} onClick={submitMembership}>
                  {t('users.memSubmit')}
                </Button>
                <Button danger loading={memBusy} onClick={() => setRevokeMemOpen(true)}>
                  {t('users.memRevoke')}
                </Button>
                {retryButton(memRetry, setMemBusy, setMemRetry)}
              </Space>
            </Card>
          </div>

          {/* 订阅区 */}
          <div data-testid="ent-subscription-section">
            <Card size="small" title={t('users.secSubscriptions')}>
              <Table
                dataSource={detail.subscriptions} columns={subColumns} rowKey="strategy_id"
                size="small" pagination={false}
                locale={{ emptyText: t('users.emptyText') }} style={{ marginBottom: 12 }}
              />
              <Space wrap>
                <Select
                  mode="multiple" value={subSelected} style={{ minWidth: 220 }}
                  placeholder={t('users.subCandidates')} onChange={setSubSelected}
                  options={grantable.map((s) => ({ value: s.strategy_id, label: `${s.strategy_name}（${s.market}）` }))}
                />
                <Radio.Group value={subMode} onChange={(e) => setSubMode(e.target.value)}>
                  <Radio value="until">{t('users.memModeUntil')}</Radio>
                  <Radio value="permanent">{t('users.memModePermanent')}</Radio>
                </Radio.Group>
                {subMode === 'until' && <DatePicker showTime value={subUntil} onChange={setSubUntil} />}
                <Input
                  placeholder={t('users.writeReason')} value={subReason}
                  style={{ width: 160 }} onChange={(e) => setSubReason(e.target.value)}
                />
                <Button type="primary" loading={subBusy} disabled={!isActive} onClick={submitGrant}>
                  {t('users.subGrant')}
                </Button>
                {retryButton(subRetry, setSubBusy, setSubRetry)}
              </Space>
            </Card>
          </div>

          {/* 操作记录区 */}
          <Card size="small" title={t('users.secAudits')}>
            <Table
              dataSource={detail.recent_audits as AuditView[]} columns={auditColumns} rowKey="audit_id"
              size="small" pagination={false} locale={{ emptyText: t('users.emptyText') }}
            />
          </Card>

          {/* 支付区 */}
          <Card size="small" title={t('users.secPayments')}>
            {t('users.paymentPendingCount', { count: detail.payment_grant_summary.pending })}
          </Card>
        </Space>
      ) : null}

      <Modal open={demoteOpen} title={t('users.confirmDemoteTitle')} onOk={doChangeRole} onCancel={() => setDemoteOpen(false)}>
        {t('users.confirmDemote')}
      </Modal>
      <Modal open={revokeMemOpen} title={t('users.confirmRevokeMemTitle')} onOk={doRevokeMembership} onCancel={() => setRevokeMemOpen(false)}>
        {t('users.confirmRevokeMem')}
      </Modal>
      <Modal open={revokeSubTarget !== null} title={t('users.confirmRevokeSubTitle')} onOk={doRevokeSub} onCancel={() => setRevokeSubTarget(null)}>
        {revokeSubTarget?.strategy_id}
      </Modal>
    </Drawer>
  );
}
