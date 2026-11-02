/**
 * RegisterPage 冒烟测试
 *  1. 渲染：用户名/邮箱/密码/确认密码输入框 + 注册按钮
 *  2. 密码一致时提交以 POST /auth/register 调用 api
 *  3. 两次密码不一致时显示提示且不调用 api
 *
 * api 整体 mock；RegisterPage 用 useLang() + Link，须包 LanguageProvider + Router。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import RegisterPage from '../pages/RegisterPage';
import { LanguageProvider } from '../contexts/LanguageContext';
import i18n from '../i18n';

vi.mock('../services/api', () => ({
  api: vi.fn(),
  authHeaders: vi.fn(() => ({})),
}));
vi.mock('../config/navigation', () => ({
  MARKET_ROUTES: ['cn', 'us', 'crypto'],
}));
import { api } from '../services/api';
const mockApi = vi.mocked(api);

function renderRegisterPage() {
  return render(
    <LanguageProvider>
      <MemoryRouter>
        <RegisterPage />
      </MemoryRouter>
    </LanguageProvider>,
  );
}

describe('RegisterPage', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    localStorage.removeItem('zhice_token');
    localStorage.setItem('zhice_lang', 'zh');
    await i18n.changeLanguage('zh');
  });

  it('渲染用户名/邮箱/密码/确认密码输入框和注册按钮', () => {
    renderRegisterPage();
    expect(screen.getByPlaceholderText('用户名')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('邮箱')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('密码')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('确认密码')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '注册' })).toBeInTheDocument();
  });

  it('品牌区展示司马徽研究院', () => {
    renderRegisterPage();
    expect(screen.getByText('司马徽研究院')).toBeInTheDocument();
    expect(screen.queryByText('量化交易系统')).not.toBeInTheDocument();
  });

  it('品牌区只显示启用市场并生成完整中英文副标题', async () => {
    const user = userEvent.setup();
    renderRegisterPage();
    expect(screen.getByText('A股 · 美股 · 加密货币 · 全自动')).toBeInTheDocument();
    expect(screen.queryByText(/港股/)).not.toBeInTheDocument();

    await user.click(screen.getByText('EN'));
    expect(screen.getByText('A-Share · US · Crypto · Fully Automated')).toBeInTheDocument();
    expect(screen.queryByText(/HK/)).not.toBeInTheDocument();
  });

  it('密码一致时以 POST /auth/register 调用 api', async () => {
    mockApi.mockResolvedValueOnce({ token: 'tok', username: 'alice', role: 'VIEWER', locale: 'zh' });
    const user = userEvent.setup();
    renderRegisterPage();

    await user.type(screen.getByPlaceholderText('用户名'), 'alice');
    await user.type(screen.getByPlaceholderText('邮箱'), 'alice@example.com');
    await user.type(screen.getByPlaceholderText('密码'), 'secret6');
    await user.type(screen.getByPlaceholderText('确认密码'), 'secret6');
    await user.click(screen.getByRole('button', { name: '注册' }));

    // 注册成功后 setLang 会额外同步 locale(PATCH /auth/me)，故不断言仅一次；只验 register 调用
    expect(mockApi).toHaveBeenCalledWith(
      '/auth/register',
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('"email":"alice@example.com"'),
      }),
    );
  });

  it('两次密码不一致时显示提示且不调用 api', async () => {
    const user = userEvent.setup();
    renderRegisterPage();

    await user.type(screen.getByPlaceholderText('密码'), 'secret6');
    await user.type(screen.getByPlaceholderText('确认密码'), 'secret7');
    await user.click(screen.getByRole('button', { name: '注册' }));

    await waitFor(() => {
      expect(screen.getByText(/不一致/)).toBeInTheDocument();
    });
    expect(mockApi).not.toHaveBeenCalled();
  });
});
