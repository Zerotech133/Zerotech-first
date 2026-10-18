/**
 * C2 修复测试：登录失败（真实 401 响应形态）必须在登录页显示错误红字。
 *
 * 与 LoginPage.test.tsx 的区别：那边文件级 vi.mock 整个 api 模块（场景 4 测
 * "api 抛异常"）；这里 **不 mock api**、stub global fetch 返回真实 401 响应
 * 形态——锁死 services/api.ts 对非 2xx 的真实处理分支：登录端点的 401 是
 * "凭证错误"业务响应，不得触发全局"清 token + window.location 跳转"（整页
 * 刷新会把红字一起刷掉，用户看到的是页面闪一下、无任何提示——deep-test C2
 * 根因；在 mock api 的文件里补此例测不到真分支，故独立成文件）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import LoginPage from '../pages/LoginPage';
import { LanguageProvider } from '../contexts/LanguageContext';
import i18n from '../i18n';

// 不 mock services/api —— 走真实 api() 封装（BASE/authHeaders/401 分支/i18n 翻译）

vi.mock('../config/navigation', () => ({
  MARKET_ROUTES: ['cn', 'us', 'crypto'],
}));

function renderLoginPage() {
  return render(
    <LanguageProvider>
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    </LanguageProvider>,
  );
}

function stub401() {
  // 后端真实 401 形态：{"detail":{"code":"auth.invalid_credentials"}}
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: false,
    status: 401,
    text: () => Promise.resolve(JSON.stringify({ detail: { code: 'auth.invalid_credentials' } })),
  }));
}

describe('LoginPage 真实 401 响应形态', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    localStorage.removeItem('zhice_token');
    localStorage.setItem('zhice_lang', 'zh');
    await i18n.changeLanguage('zh');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('错误密码（401 invalid_credentials）显示"用户名或密码错误"红字', async () => {
    stub401();
    const user = userEvent.setup();
    renderLoginPage();

    await user.type(screen.getByPlaceholderText('用户名'), 'admin');
    await user.type(screen.getByPlaceholderText('密码'), 'wrong-password');
    await user.click(screen.getByRole('button', { name: '登录' }));

    // 红字提示出现，且文案是翻译后的"用户名或密码错误"
    // （若走了旧的全局 401 跳转分支，文案会是"未授权，请重新登录"并被整页刷新吞掉）
    await waitFor(() => {
      expect(screen.getByText(/登录失败：用户名或密码错误/)).toBeInTheDocument();
    });
  });

  it('登录 401 失败后不写入 token', async () => {
    stub401();
    const user = userEvent.setup();
    renderLoginPage();

    await user.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => {
      expect(screen.getByText(/登录失败/)).toBeInTheDocument();
    });
    // 失败的登录绝不留下半套会话状态
    expect(localStorage.getItem('zhice_token')).toBeNull();
  });
});
