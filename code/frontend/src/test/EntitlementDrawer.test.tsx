// B3-3/B3-4 测试：EntitlementDrawer 只读四区 + 三块独立写操作。
// 只读：切换用户清空 + 迟到响应隔离。写：角色（危险降级二次确认）/会员（permanent 设置 + 撤销确认文案）
// /订阅（候选来自 grantable 端点 + 批量授权）；inactive 用户会员/订阅写 disabled；写成功 reload + onChanged。
// 复杂日期选择（会员 until 模式的 DatePicker）留 Playwright E2E（B3-7），单测聚焦 permanent 与 API 调用契约。
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';

vi.mock('../services/adminEntitlementsApi', () => ({
  getUserDetail: vi.fn(),
  changeRole: vi.fn(),
  setMembership: vi.fn(),
  revokeMembership: vi.fn(),
  setSubscriptions: vi.fn(),
  revokeSubscriptions: vi.fn(),
  listGrantableStrategies: vi.fn(),
}));

import '../i18n';
import EntitlementDrawer from '../components/admin-entitlements/EntitlementDrawer';
import {
  getUserDetail, changeRole, setMembership, revokeMembership,
  setSubscriptions, revokeSubscriptions, listGrantableStrategies,
} from '../services/adminEntitlementsApi';
import { AdminApiError, type UserDetail } from '../components/admin-entitlements/entitlementModel';

const mockGetDetail = vi.mocked(getUserDetail);
const mockChangeRole = vi.mocked(changeRole);
const mockSetMembership = vi.mocked(setMembership);
const mockRevokeMembership = vi.mocked(revokeMembership);
const mockSetSubscriptions = vi.mocked(setSubscriptions);
const mockRevokeSubscriptions = vi.mocked(revokeSubscriptions);
const mockListGrantable = vi.mocked(listGrantableStrategies);

function detailFor(userId: string, username: string, overrides: Partial<UserDetail['user']> = {}): UserDetail {
  return {
    user: { user_id: userId, username, role: 'USER', is_active: true, auth_version: 3, email: null, ...overrides },
    membership: {
      tier: 'member', stored_status: 'active', effective_status: 'permanent',
      is_active: true, is_permanent: true, valid_until: null, revoked_at: null, version: 2,
    },
    subscriptions: [
      {
        strategy_id: 'st-active', stored_status: 'active', effective_status: 'active',
        is_active: true, is_permanent: false, valid_until: '2026-12-31T00:00:00Z',
        revoked_at: null, version: 1,
      },
      {
        strategy_id: 'st-revoked', stored_status: 'revoked', effective_status: 'revoked',
        is_active: false, is_permanent: false, valid_until: null,
        revoked_at: '2026-09-01T00:00:00Z', version: 3,
      },
    ],
    recent_audits: [
      {
        audit_id: 'aud-1', operation_id: 'op-1', actor_user_id: 'admin-1', target_user_id: userId,
        source_type: 'ADMIN', action: 'membership.set', resource_type: 'MEMBERSHIP',
        resource_id: null, reason: '赠送永久会员', before_state: {}, after_state: {},
        source_order_id: null, created_at: '2026-09-10T00:00:00Z',
      },
    ],
    payment_grant_summary: { pending: 1 },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('zhice_role', 'ADMIN');
  mockListGrantable.mockResolvedValue({
    items: [
      { strategy_id: 'st-active', strategy_name: '活跃策略', market: 'US' },
      { strategy_id: 'st-new', strategy_name: '新策略', market: 'CRYPTO' },
    ],
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------- 只读四区（B3-3） ----------

describe('EntitlementDrawer 四区只读展示', () => {
  it('打开时拉取详情并展示五个分区标题与会员实时态', async () => {
    mockGetDetail.mockResolvedValueOnce(detailFor('u-A', 'alice'));
    render(<EntitlementDrawer userId="u-A" open onClose={() => {}} />);
    await screen.findByText('alice');
    expect(mockGetDetail).toHaveBeenCalledWith('u-A', expect.anything());
    for (const sec of ['角色', '会员', '订阅', '操作记录', '支付']) {
      expect(screen.getAllByText(sec).length).toBeGreaterThan(0);
    }
    expect(screen.getAllByText('永久').length).toBeGreaterThan(0);
  });

  it('订阅区含撤销历史（已撤销订阅仍展示）', async () => {
    mockGetDetail.mockResolvedValueOnce(detailFor('u-A', 'alice'));
    render(<EntitlementDrawer userId="u-A" open onClose={() => {}} />);
    await screen.findByText('st-active');
    expect(screen.getByText('st-revoked')).toBeInTheDocument();
    expect(screen.getAllByText('已撤销').length).toBeGreaterThan(0);
  });

  it('操作记录区展示动作与原因', async () => {
    mockGetDetail.mockResolvedValueOnce(detailFor('u-A', 'alice'));
    render(<EntitlementDrawer userId="u-A" open onClose={() => {}} />);
    await screen.findByText('membership.set');
    expect(screen.getByText('赠送永久会员')).toBeInTheDocument();
  });

  it('支付区展示待处理计数', async () => {
    mockGetDetail.mockResolvedValueOnce(detailFor('u-A', 'alice'));
    render(<EntitlementDrawer userId="u-A" open onClose={() => {}} />);
    await screen.findByText('alice');
    expect(screen.getByText(/待处理支付订单/)).toBeInTheDocument();
  });

  it('切换用户时清空旧详情，只展示新用户', async () => {
    mockGetDetail.mockResolvedValueOnce(detailFor('u-A', 'alice'));
    const { rerender } = render(<EntitlementDrawer userId="u-A" open onClose={() => {}} />);
    await screen.findByText('alice');
    mockGetDetail.mockResolvedValueOnce(detailFor('u-B', 'bob'));
    rerender(<EntitlementDrawer userId="u-B" open onClose={() => {}} />);
    await screen.findByText('bob');
    expect(screen.queryByText('alice')).toBeNull();
  });

  it('迟到响应不覆盖新用户详情（epoch 防护）', async () => {
    let resolveA: (d: UserDetail) => void = () => {};
    mockGetDetail.mockReturnValueOnce(new Promise<UserDetail>((r) => { resolveA = r; }));
    const { rerender } = render(<EntitlementDrawer userId="u-A" open onClose={() => {}} />);
    mockGetDetail.mockResolvedValueOnce(detailFor('u-B', 'bob'));
    rerender(<EntitlementDrawer userId="u-B" open onClose={() => {}} />);
    await screen.findByText('bob');
    resolveA(detailFor('u-A', 'alice'));
    await waitFor(() => expect(screen.queryByText('alice')).toBeNull());
    expect(screen.getByText('bob')).toBeInTheDocument();
  });
});

// ---------- 写操作（B3-4） ----------

describe('EntitlementDrawer 写操作', () => {
  function roleSection() { return within(screen.getByTestId('ent-role-section')); }
  function memSection() { return within(screen.getByTestId('ent-membership-section')); }
  function subSection() { return within(screen.getByTestId('ent-subscription-section')); }

  it('非危险改角色：直接提交 changeRole 并 reload + onChanged', async () => {
    mockGetDetail.mockResolvedValue(detailFor('u-A', 'alice', { role: 'USER' }));
    mockChangeRole.mockResolvedValueOnce({ user_id: 'u-A', username: 'alice', role: 'TRADER', auth_version: 4 });
    const onChanged = vi.fn();
    render(<EntitlementDrawer userId="u-A" open onClose={() => {}} onChanged={onChanged} />);
    await screen.findByText('alice');

    fireEvent.mouseDown(roleSection().getByRole('combobox'));
    fireEvent.click(await screen.findByText('交易员'));
    fireEvent.change(roleSection().getByPlaceholderText('操作原因'), { target: { value: '晋升' } });
    fireEvent.click(roleSection().getByRole('button', { name: '更新角色' }));

    await waitFor(() => expect(mockChangeRole).toHaveBeenCalled());
    const [uid, payload] = mockChangeRole.mock.calls[0];
    expect(uid).toBe('u-A');
    expect(payload).toMatchObject({ role: 'TRADER', authVersion: 3, reason: '晋升' });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('危险降级（ADMIN→非ADMIN）弹二次确认后才提交', async () => {
    mockGetDetail.mockResolvedValue(detailFor('u-A', 'boss', { role: 'ADMIN' }));
    mockChangeRole.mockResolvedValueOnce({ user_id: 'u-A', username: 'boss', role: 'VIEWER', auth_version: 4 });
    render(<EntitlementDrawer userId="u-A" open onClose={() => {}} />);
    await screen.findByText('boss');

    fireEvent.mouseDown(roleSection().getByRole('combobox'));
    fireEvent.click(await screen.findByText('只读'));
    fireEvent.change(roleSection().getByPlaceholderText('操作原因'), { target: { value: '降权' } });
    fireEvent.click(roleSection().getByRole('button', { name: '更新角色' }));

    await screen.findByText(/将管理员降级/);
    expect(mockChangeRole).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'OK' }));
    await waitFor(() => expect(mockChangeRole).toHaveBeenCalled());
    expect(mockChangeRole.mock.calls[0][1]).toMatchObject({ role: 'VIEWER' });
  });

  it('会员设置永久：保存调用 setMembership(permanent, 当前 version)', async () => {
    mockGetDetail.mockResolvedValue(detailFor('u-A', 'alice'));
    mockSetMembership.mockResolvedValueOnce({ membership: { effective_status: 'permanent', version: 3, is_permanent: true } });
    render(<EntitlementDrawer userId="u-A" open onClose={() => {}} />);
    await screen.findByText('alice');

    fireEvent.click(memSection().getByRole('radio', { name: '永久' }));
    fireEvent.change(memSection().getByPlaceholderText('操作原因'), { target: { value: '赠送永久' } });
    fireEvent.click(memSection().getByRole('button', { name: '保存会员' }));

    await waitFor(() => expect(mockSetMembership).toHaveBeenCalled());
    expect(mockSetMembership.mock.calls[0][1]).toMatchObject({ mode: 'permanent', reason: '赠送永久', version: 2 });
  });

  it('撤销会员：确认文案含"停止新开仓"，确认后调 revokeMembership', async () => {
    mockGetDetail.mockResolvedValue(detailFor('u-A', 'alice'));
    mockRevokeMembership.mockResolvedValueOnce({
      membership: { effective_status: 'revoked', version: 3, is_permanent: false },
      opening_risk_cleanup: {
        disabled_instance_ids: [], cancelled_plan_ids: [], cancelled_approval_ids: [],
        cancelled_intent_ids: [], cancelled_request_ids: [], released_reservation_ids: [],
        released_amount: '0', pending_external_cancellations: [],
      },
    });
    render(<EntitlementDrawer userId="u-A" open onClose={() => {}} />);
    await screen.findByText('alice');

    fireEvent.click(memSection().getByRole('button', { name: '撤销会员' }));
    await screen.findByText(/停止新开仓/);
    expect(mockRevokeMembership).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'OK' }));
    await waitFor(() => expect(mockRevokeMembership).toHaveBeenCalled());
    expect(mockRevokeMembership.mock.calls[0][1]).toMatchObject({ version: 2 });
  });

  it('inactive 用户：会员保存禁用（仍可改角色/撤销）', async () => {
    mockGetDetail.mockResolvedValue(detailFor('u-A', 'alice', { is_active: false }));
    render(<EntitlementDrawer userId="u-A" open onClose={() => {}} />);
    await screen.findByText('alice');
    expect(memSection().getByRole('button', { name: '保存会员' })).toBeDisabled();
    expect(roleSection().getByRole('button', { name: '更新角色' })).toBeEnabled();
  });

  it('批量授权订阅：候选来自 grantable，提交 setSubscriptions 带 versions（缺=0）', async () => {
    mockGetDetail.mockResolvedValue(detailFor('u-A', 'alice'));
    mockSetSubscriptions.mockResolvedValueOnce({ subscriptions: [] });
    render(<EntitlementDrawer userId="u-A" open onClose={() => {}} />);
    await screen.findByText('alice');
    await waitFor(() => expect(mockListGrantable).toHaveBeenCalled());

    fireEvent.mouseDown(subSection().getByRole('combobox'));
    fireEvent.click(await screen.findByText(/新策略/));
    fireEvent.click(subSection().getByRole('radio', { name: '永久' }));
    fireEvent.change(subSection().getByPlaceholderText('操作原因'), { target: { value: '批量授权' } });
    fireEvent.click(subSection().getByRole('button', { name: '批量授权订阅' }));

    await waitFor(() => expect(mockSetSubscriptions).toHaveBeenCalled());
    const payload = mockSetSubscriptions.mock.calls[0][1];
    expect(payload).toMatchObject({ mode: 'permanent', reason: '批量授权' });
    expect(payload.strategyIds).toContain('st-new');
    expect(payload.versions['st-new']).toBe(0);
  });

  it('单项撤销订阅调 revokeSubscriptions（version 取自详情）', async () => {
    mockGetDetail.mockResolvedValue(detailFor('u-A', 'alice'));
    mockRevokeSubscriptions.mockResolvedValueOnce({ subscriptions: [] });
    render(<EntitlementDrawer userId="u-A" open onClose={() => {}} />);
    await screen.findByText('st-active');

    const row = screen.getByText('st-active').closest('tr') as HTMLElement;
    // antd 对两个 CJK 字符的按钮会插入空格（"撤销"→"撤 销"），用正则匹配。
    fireEvent.click(within(row).getByRole('button', { name: /撤\s*销/ }));
    await screen.findByText(/确认撤销该订阅/);
    // 第三个 Modal（撤销订阅）的确认按钮：定位该 Modal 内的 OK，避免与其它 Modal 冲突。
    const dialog = screen.getByText(/确认撤销该订阅/).closest('.ant-modal') as HTMLElement;
    fireEvent.click(within(dialog).getByRole('button', { name: 'OK' }));
    await waitFor(() => expect(mockRevokeSubscriptions).toHaveBeenCalled());
    const payload = mockRevokeSubscriptions.mock.calls[0][1];
    expect(payload.strategyIds).toEqual(['st-active']);
    expect(payload.versions['st-active']).toBe(1);
  }, 15000);
});

// ---------- 写可靠性：未知结果重试 + 自降级登出（B3-5） ----------

describe('EntitlementDrawer 写可靠性', () => {
  function roleSection() { return within(screen.getByTestId('ent-role-section')); }
  function memSection() { return within(screen.getByTestId('ent-membership-section')); }

  it('自降级：改自己角色为非 ADMIN 成功后清 token+role 跳登录', async () => {
    localStorage.setItem('zhice_token', 'tok');
    localStorage.setItem('zhice_role', 'ADMIN');
    const hrefs: string[] = [];
    Object.defineProperty(window, 'location', {
      value: { set href(v: string) { hrefs.push(v); }, get href() { return ''; } },
      writable: true, configurable: true,
    });
    mockGetDetail.mockResolvedValue(detailFor('u-self', 'me', { role: 'ADMIN' }));
    mockChangeRole.mockResolvedValueOnce({ user_id: 'u-self', username: 'me', role: 'VIEWER', auth_version: 4 });
    render(<EntitlementDrawer userId="u-self" open onClose={() => {}} actorUserId="u-self" />);
    await screen.findByText('me');

    fireEvent.mouseDown(roleSection().getByRole('combobox'));
    fireEvent.click(await screen.findByText('只读'));
    fireEvent.change(roleSection().getByPlaceholderText('操作原因'), { target: { value: '自降权' } });
    fireEvent.click(roleSection().getByRole('button', { name: '更新角色' }));
    await screen.findByText(/将管理员降级/);
    fireEvent.click(screen.getByRole('button', { name: 'OK' }));

    await waitFor(() => expect(mockChangeRole).toHaveBeenCalled());
    await waitFor(() => {
      expect(localStorage.getItem('zhice_token')).toBeNull();
      expect(localStorage.getItem('zhice_role')).toBeNull();
    });
    expect(hrefs).toContain('/login');
  });

  it('未知结果（retryable）提供"重试原操作"，复用同一幂等键', async () => {
    mockGetDetail.mockResolvedValue(detailFor('u-A', 'alice'));
    mockSetMembership
      .mockRejectedValueOnce(new AdminApiError('timeout', { retryable: true }))
      .mockResolvedValueOnce({ membership: { effective_status: 'permanent', version: 3, is_permanent: true } });
    render(<EntitlementDrawer userId="u-A" open onClose={() => {}} />);
    await screen.findByText('alice');

    fireEvent.click(memSection().getByRole('radio', { name: '永久' }));
    fireEvent.change(memSection().getByPlaceholderText('操作原因'), { target: { value: '赠送' } });
    fireEvent.click(memSection().getByRole('button', { name: '保存会员' }));

    await waitFor(() => expect(mockSetMembership).toHaveBeenCalledTimes(1));
    const retryBtn = await memSection().findByRole('button', { name: '重试原操作' });
    const key1 = mockSetMembership.mock.calls[0][2];
    fireEvent.click(retryBtn);
    await waitFor(() => expect(mockSetMembership).toHaveBeenCalledTimes(2));
    expect(mockSetMembership.mock.calls[1][2]).toBe(key1);
  });

  it('4xx 已知失败不提供重试按钮', async () => {
    mockGetDetail.mockResolvedValue(detailFor('u-A', 'alice'));
    mockSetMembership.mockRejectedValueOnce(
      new AdminApiError('conflict', { status: 409, code: 'entitlements.version_conflict' }),
    );
    render(<EntitlementDrawer userId="u-A" open onClose={() => {}} />);
    await screen.findByText('alice');

    fireEvent.click(memSection().getByRole('radio', { name: '永久' }));
    fireEvent.change(memSection().getByPlaceholderText('操作原因'), { target: { value: 'x' } });
    fireEvent.click(memSection().getByRole('button', { name: '保存会员' }));

    await waitFor(() => expect(mockSetMembership).toHaveBeenCalled());
    expect(memSection().queryByRole('button', { name: '重试原操作' })).toBeNull();
  });
});
