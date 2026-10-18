import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../components/PageHeader', () => ({ default: () => <div>系统与设置</div> }));
vi.mock('../pages/SystemStatusPage', () => ({ default: () => <div>系统状态页</div> }));
vi.mock('../pages/MonitorPage', () => ({ default: () => null }));
vi.mock('../pages/AlertsPage', () => ({ default: () => null }));
vi.mock('../pages/AutomationPage', () => ({ default: () => null }));
vi.mock('../pages/GovernanceBoardPage', () => ({ default: () => null }));
vi.mock('../pages/MultiStrategyPage', () => ({ default: () => null }));
vi.mock('../pages/CalendarPage', () => ({ default: () => null }));
vi.mock('../pages/SettingsPageV2', () => ({ default: () => null }));
vi.mock('../pages/ExportsPage', () => ({ default: () => null }));
vi.mock('../services/api', () => ({ api: vi.fn() }));
// UsersPage 现以 adminEntitlementsApi.listUsers 为数据源（B3-2）；mock 它避免渲染时真实请求。
vi.mock('../services/adminEntitlementsApi', () => ({ listUsers: vi.fn() }));

import '../i18n'; // 触发 i18n 初始化（默认 zh），否则 t() 返回 key 而非中文，断言全挂
import SystemGroup from '../pages/groups/SystemGroup';
import { getRoleCapabilities } from '../hooks/useRole';
import { listUsers } from '../services/adminEntitlementsApi';

const mockListUsers = vi.mocked(listUsers);

function renderSystem(path = '/cn/system') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SystemGroup />
    </MemoryRouter>,
  );
}

describe('用户管理权限可见性', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    // SystemGroup 可能挂载 UsersPage；给它一个空用户页，聚焦 Tab 可见性断言。
    mockListUsers.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 });
  });

  it('ADMIN 能看到用户管理 Tab', () => {
    localStorage.setItem('zhice_role', 'ADMIN');
    renderSystem();
    expect(screen.getByRole('tab', { name: '用户管理' })).toBeInTheDocument();
  });

  it('VIEWER 看不到用户管理 Tab', () => {
    localStorage.setItem('zhice_role', 'VIEWER');
    renderSystem();
    expect(screen.queryByRole('tab', { name: '用户管理' })).not.toBeInTheDocument();
  });

  it('VIEWER 直接访问 users tab 时显示无权限', () => {
    localStorage.setItem('zhice_role', 'VIEWER');
    renderSystem('/cn/system?tab=users');
    expect(screen.getByText('无权限')).toBeInTheDocument();
  });
});

describe('角色能力模型 getRoleCapabilities', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('USER can manage own resources but not platform resources', () => {
    localStorage.setItem('zhice_role', 'USER');
    expect(getRoleCapabilities()).toEqual(expect.objectContaining({
      canManageOwn: true,
      canManagePlatform: false,
      isReadOnly: false,
    }));
  });

  it('VIEWER remains read-only', () => {
    localStorage.setItem('zhice_role', 'VIEWER');
    expect(getRoleCapabilities()).toEqual(expect.objectContaining({
      isViewer: true,
      isUser: false,
      canManageOwn: false,
      canManagePlatform: false,
      isReadOnly: true,
      canWrite: false,
    }));
  });

  it('ADMIN can manage platform (and own) resources', () => {
    localStorage.setItem('zhice_role', 'ADMIN');
    expect(getRoleCapabilities()).toEqual(expect.objectContaining({
      canManageOwn: true,
      canManagePlatform: true,
      isReadOnly: false,
      canWrite: true,
    }));
  });
});
