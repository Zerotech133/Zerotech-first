// B3-2 测试：UsersPage 管理员用户摘要列表（分页 + 会员实时态 + 有效订阅数 + 只读角色 Tag
// + 权益入口 + 待处理支付占位 + 查询失败错误态 + 刷新失败保留旧数据）。
// 数据源改为 adminEntitlementsApi.listUsers（/admin/entitlements/users 分页），角色不再可直接编辑。
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { message } from 'antd';

// Drawer 点开会调 getUserDetail + listGrantableStrategies；给 detail 一个挂起 promise、
// 候选一个空 resolve，聚焦列表行为而非抽屉内容。
vi.mock('../services/adminEntitlementsApi', () => ({
  listUsers: vi.fn(),
  getUserDetail: vi.fn(() => new Promise(() => {})),
  listGrantableStrategies: vi.fn(() => Promise.resolve({ items: [] })),
}));
// UsersPage 会读 /auth/me（取 actor id）；mock api 返回一个 resolved promise 避免 .then 崩溃。
vi.mock('../services/api', () => ({ api: vi.fn(() => Promise.resolve({ user_id: 'admin-self' })) }));

import '../i18n'; // 触发 i18n（默认 zh）
import UsersPage from '../pages/UsersPage';
import { listUsers } from '../services/adminEntitlementsApi';
import type { UsersPage as UsersPageDto, UserSummary } from '../components/admin-entitlements/entitlementModel';

const mockListUsers = vi.mocked(listUsers);

function pageOf(items: UserSummary[], total = items.length): UsersPageDto {
  return { items, total, page: 1, page_size: 20 };
}

const permanentUser: UserSummary = {
  user_id: 'u-1',
  username: 'alice',
  role: 'USER',
  is_active: true,
  auth_version: 3,
  membership: { effective_status: 'permanent', is_permanent: true, valid_until: null, version: 2 },
  active_subscription_count: 2,
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('zhice_role', 'ADMIN');
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('UsersPage 用户摘要列表', () => {
  it('渲染用户名、只读角色 Tag、会员实时态与有效订阅数', async () => {
    mockListUsers.mockResolvedValueOnce(pageOf([permanentUser]));
    render(<UsersPage />);
    await screen.findByText('alice');
    // 角色只读：显示"普通用户"标签，且列表区无可编辑下拉（combobox）。
    expect(screen.getByText('普通用户')).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).toBeNull();
    // 会员实时态=永久；有效订阅数=2。
    expect(screen.getByText('永久')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('提供"权限与权益"入口按钮，"待处理支付"按钮禁用（单元 C 前）', async () => {
    mockListUsers.mockResolvedValueOnce(pageOf([permanentUser]));
    render(<UsersPage />);
    await screen.findByText('alice');
    const entBtn = screen.getByRole('button', { name: '权限与权益' });
    expect(entBtn).toBeEnabled();
    fireEvent.click(entBtn); // 不报错即可（抽屉由 B3-3 接入）
    expect(screen.getByRole('button', { name: '待处理支付' })).toBeDisabled();
  });

  it('首次查询失败显示错误态，不把失败投影成"免费"会员', async () => {
    mockListUsers.mockRejectedValueOnce(new Error('boom'));
    render(<UsersPage />);
    await screen.findByText('加载用户列表失败');
    expect(screen.queryByText('免费')).toBeNull();
    expect(screen.queryByText('alice')).toBeNull();
  });

  it('刷新失败时保留上次数据并提示过期', async () => {
    mockListUsers.mockResolvedValueOnce(pageOf([permanentUser]));
    render(<UsersPage />);
    await screen.findByText('alice');

    const warnSpy = vi.spyOn(message, 'warning').mockImplementation(() => ({}) as never);
    mockListUsers.mockRejectedValueOnce(new Error('refresh failed'));
    fireEvent.click(screen.getByRole('button', { name: /刷新/ }));

    await waitFor(() => expect(warnSpy).toHaveBeenCalled());
    // 旧数据仍在，不被清空。
    expect(screen.getByText('alice')).toBeInTheDocument();
  });
});
