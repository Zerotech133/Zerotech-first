/**
 * LoginPage 单元测试
 *
 * 覆盖场景：
 *  1. 页面渲染：用户名/密码输入框 + 登录按钮存在
 *  2. 提交时以正确参数调用 api()
 *  3. 登录成功后将 token 写入 localStorage
 *  4. API 抛出错误时显示"登录失败"提示
 *  5. 按 Enter 触发登录
 *
 * api 模块整体 mock，不发真实网络请求。
 * LoginPage 使用 useLang()，故渲染时须包 LanguageProvider。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import LoginPage from '../pages/LoginPage';
import { LanguageProvider } from '../contexts/LanguageContext';
import i18n from '../i18n';

// ── Mock api 模块 ──────────────────────────────────────────────────────────────
vi.mock('../services/api', () => ({
  api: vi.fn(),
  authHeaders: vi.fn(() => ({})),
}));
vi.mock('../config/navigation', () => ({
  MARKET_ROUTES: ['cn', 'us', 'crypto'],
}));

// 在 mock 声明之后 import，这样得到的是 mock 版本
import { api } from '../services/api';
const mockApi = vi.mocked(api);

// ── 工具函数 ───────────────────────────────────────────────────────────────────
function renderLoginPage() {
  return render(
    <LanguageProvider>
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    </LanguageProvider>,
  );
}

describe('LoginPage', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    localStorage.removeItem('zhice_token');
    localStorage.setItem('zhice_lang', 'zh');
    await i18n.changeLanguage('zh');
  });

  // 场景 1：页面元素存在
  it('渲染用户名输入框、密码输入框和登录按钮', () => {
    renderLoginPage();
    expect(screen.getByPlaceholderText('用户名')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('密码')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '登录' })).toBeInTheDocument();
  });

  it('品牌区展示司马徽研究院', () => {
    renderLoginPage();
    expect(screen.getByText('司马徽研究院')).toBeInTheDocument();
    expect(screen.queryByText('量化交易系统')).not.toBeInTheDocument();
  });

  it('用户名输入框默认值为空（公开系统不预填 admin）', () => {
    renderLoginPage();
    expect(screen.getByPlaceholderText('用户名')).toHaveValue('');
  });

  it('品牌区只显示启用市场并生成完整中英文副标题', async () => {
    const user = userEvent.setup();
    renderLoginPage();
    expect(screen.getByText('A股 · 美股 · 加密货币 · 全自动')).toBeInTheDocument();
    expect(screen.queryByText(/港股/)).not.toBeInTheDocument();

    await user.click(screen.getByText('EN'));
    expect(screen.getByText('A-Share · US · Crypto · Fully Automated')).toBeInTheDocument();
    expect(screen.queryByText(/HK/)).not.toBeInTheDocument();
  });

  // 场景 2：提交时调用正确 API
  it('点击登录时以 POST /auth/login 调用 api', async () => {
    mockApi.mockResolvedValueOnce({
      token: 'test-token',
      username: 'admin',
      role: 'admin',
    });
    const user = userEvent.setup();
    renderLoginPage();

    await user.type(screen.getByPlaceholderText('用户名'), 'admin');
    await user.type(screen.getByPlaceholderText('密码'), 'secret');
    await user.click(screen.getByRole('button', { name: '登录' }));

    expect(mockApi).toHaveBeenCalledOnce();
    expect(mockApi).toHaveBeenCalledWith(
      '/auth/login',
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('"username":"admin"'),
      }),
    );
  });

  // 场景 3：登录成功写入 token
  it('登录成功后将 token 写入 localStorage', async () => {
    mockApi.mockResolvedValueOnce({
      token: 'my-secret-token',
      username: 'admin',
      role: 'admin',
    });
    const user = userEvent.setup();
    renderLoginPage();

    await user.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => {
      expect(localStorage.getItem('zhice_token')).toBe('my-secret-token');
    });
  });

  it('登录成功后显示成功提示', async () => {
    mockApi.mockResolvedValueOnce({
      token: 'tok',
      username: 'admin',
      role: 'viewer',
    });
    const user = userEvent.setup();
    renderLoginPage();

    await user.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => {
      expect(screen.getByText(/登录成功/)).toBeInTheDocument();
    });
  });

  // 场景 4：API 报错显示"登录失败"
  it('API 抛出错误时显示"登录失败"提示', async () => {
    mockApi.mockRejectedValueOnce(new Error('用户名或密码错误'));
    const user = userEvent.setup();
    renderLoginPage();

    await user.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => {
      expect(screen.getByText(/登录失败/)).toBeInTheDocument();
      expect(screen.getByText(/用户名或密码错误/)).toBeInTheDocument();
    });
  });

  it('非 Error 类型的抛出值也能显示在错误提示中', async () => {
    mockApi.mockRejectedValueOnce('网络超时');
    const user = userEvent.setup();
    renderLoginPage();

    await user.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => {
      expect(screen.getByText(/登录失败/)).toBeInTheDocument();
    });
  });

  // 场景 5：Enter 键触发登录
  it('在密码框按 Enter 触发登录', async () => {
    mockApi.mockResolvedValueOnce({
      token: 'tok',
      username: 'admin',
      role: 'admin',
    });
    const user = userEvent.setup();
    renderLoginPage();

    await user.click(screen.getByPlaceholderText('密码'));
    await user.keyboard('{Enter}');

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledOnce();
    });
  });
});
