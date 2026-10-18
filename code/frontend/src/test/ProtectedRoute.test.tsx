/**
 * ProtectedRoute 单元测试
 *
 * 覆盖场景：
 *  1. localStorage 无 token → 重定向 /login
 *  2. 有 token 且后端验证通过 → 渲染子组件
 *  3. 有 token 但后端返回非 ok → 重定向 /login
 *  4. 有 token 但网络错误 → 放行（避免网络抖动强制退出）
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import ProtectedRoute from '../components/ProtectedRoute';

// 简单占位组件
const LoginPage = () => <div>登录页</div>;
const Dashboard = () => <div>仪表盘</div>;

/** 包装 ProtectedRoute 在真实路由树中 */
function renderWithRouter(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="*"
          element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ProtectedRoute', () => {
  beforeEach(() => {
    localStorage.removeItem('zhice_token');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('无 token → 重定向到 /login', async () => {
    renderWithRouter('/dashboard');
    await waitFor(() => {
      expect(screen.getByText('登录页')).toBeInTheDocument();
    });
    expect(screen.queryByText('仪表盘')).not.toBeInTheDocument();
  });

  it('有效 token（后端返回 ok）→ 渲染子组件', async () => {
    localStorage.setItem('zhice_token', 'valid-token');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, status: 200 }),
    );

    renderWithRouter('/dashboard');
    await waitFor(() => {
      expect(screen.getByText('仪表盘')).toBeInTheDocument();
    });
    expect(screen.queryByText('登录页')).not.toBeInTheDocument();
  });

  it('无效 token（后端返回非 ok）→ 重定向到 /login', async () => {
    localStorage.setItem('zhice_token', 'expired-token');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 401 }),
    );

    renderWithRouter('/dashboard');
    await waitFor(() => {
      expect(screen.getByText('登录页')).toBeInTheDocument();
    });
    // token 应被清除
    expect(localStorage.getItem('zhice_token')).toBeNull();
  });

  it('网络错误 → 放行（渲染子组件）', async () => {
    localStorage.setItem('zhice_token', 'any-token');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new TypeError('Failed to fetch')),
    );

    renderWithRouter('/dashboard');
    await waitFor(() => {
      expect(screen.getByText('仪表盘')).toBeInTheDocument();
    });
  });

  it('验证期间不闪跳（初始渲染返回 null）', () => {
    localStorage.setItem('zhice_token', 'token');
    // fetch 永不 resolve，模拟 pending 状态
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})));

    const { container } = renderWithRouter('/dashboard');
    // 此时 status=checking，ProtectedRoute 返回 null
    expect(container.firstChild).toBeNull();
  });
});
