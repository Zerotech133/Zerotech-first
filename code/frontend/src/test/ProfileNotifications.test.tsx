/**
 * Task 15 Profile 集成测试：NotificationChannelSettings 容器 + ProfilePage 接线。
 *
 * 计划最小测试（语义一致；notificationsApi 整体 mock + services/api mock，
 * fake timers 驱动 WxPusherBind 扫码轮询）：
 * 4. shows WeChat first and keeps Telegram as an optional overseas channel
 *    （heading 顺序：微信通知 在 Telegram（境外可选） 之前）
 * 5. refreshes the matrix immediately after binding changes（扫码确认 →
 *    WxPusherBind.onBindingChanged → 容器 refreshBindings → 微信列
 *    disabled → enabled，「请先绑定」消失）
 * 另补：Telegram 手动卡保留渲染（bot 未配置=生产现状）；bot 已配置 → deep-link
 * 卡（manual 输入框不出现）；revoked 渠道矩阵列 disabled。
 *
 * 容器契约：NotificationChannelSettings 一次加载 getNotificationBindings() +
 * 两渠道 preference，绑定/解绑统一 refreshBindings()；三个子组件不各自维护互不
 * 知晓的 binding 状态（Telegram manual/deep-link 模式按后端 status 的
 * bot_username 决定，不强制切换）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../services/api', () => ({ api: vi.fn() }));
vi.mock('../services/notificationsApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/notificationsApi')>();
  return {
    ...actual,
    getNotificationBindings: vi.fn(),
    getMyNotificationPreferences: vi.fn(),
    patchMyNotificationPreferences: vi.fn(),
    getTelegramBindingStatus: vi.fn(),
    bindTelegramManual: vi.fn(),
    unbindTelegram: vi.fn(),
    createWxPusherChallenge: vi.fn(),
    unbindWxPusher: vi.fn(),
    testWxPusher: vi.fn(),
  };
});

import '../i18n'; // 触发 i18n 初始化（默认 zh）
import ProfilePage from '../pages/ProfilePage';
import { LanguageProvider } from '../contexts/LanguageContext';
import { ThemeProvider } from '../themes/ThemeContext';
import { api } from '../services/api';
import {
  bindTelegramManual,
  createWxPusherChallenge,
  getMyNotificationPreferences,
  getNotificationBindings,
  getTelegramBindingStatus,
  unbindTelegram,
  type NotificationBinding,
  type NotificationChannel,
  type NotificationPreference,
  type WxPusherChallenge,
} from '../services/notificationsApi';

const mockApi = vi.mocked(api);
const mockBindings = vi.mocked(getNotificationBindings);
const mockPrefs = vi.mocked(getMyNotificationPreferences);
const mockStatus = vi.mocked(getTelegramBindingStatus);
const mockBindManual = vi.mocked(bindTelegramManual);
const mockUnbindTelegram = vi.mocked(unbindTelegram);
const mockCreate = vi.mocked(createWxPusherChallenge);

// ---------- 类型化样例（对齐 notificationsApi DTO；recipient 已脱敏） ----------

const TG_ACTIVE: NotificationBinding = {
  channel: 'telegram',
  status: 'active',
  recipient_masked: '••••5678',
  bound_at: '2026-09-01T08:00:00Z',
};

const WX_ACTIVE: NotificationBinding = {
  channel: 'wxpusher',
  status: 'active',
  recipient_masked: 'UID_***3456',
  bound_at: '2026-09-01T08:00:00Z',
};

const WX_REVOKED: NotificationBinding = {
  channel: 'wxpusher',
  status: 'revoked',
  recipient_masked: null,
  bound_at: null,
};

function pref(channel: NotificationChannel): NotificationPreference {
  return {
    user_id: 'u-1',
    channel,
    market_reports_enabled: { CN: true, US: false, HK: false, CRYPTO: false },
    signal_enabled: true,
    fill_enabled: true,
    morning_report_enabled: false,
    evening_report_enabled: false,
    quiet_hours: null,
    timezone: 'Asia/Shanghai',
    locale: 'zh',
  };
}

const challenge = (ms: number): WxPusherChallenge => ({
  qr_image_url: 'https://wxpusher.zjie.work/qr/s/q1.png',
  expires_at: new Date(Date.now() + ms).toISOString(),
});

/** 手动绑定部署（生产现状）：bot 未配置、Telegram 已绑定。 */
const MANUAL_STATUS = { bound: true, chat_id: '••••5678', bot_username: null };

function renderProfile() {
  return render(
    <LanguageProvider>
      <ThemeProvider>
        <MemoryRouter>
          <ProfilePage />
        </MemoryRouter>
      </ThemeProvider>
    </LanguageProvider>,
  );
}

/** 基线桩：/auth/me 就绪、Telegram 已绑定（手动模式）、微信未绑定、两渠道偏好就绪。 */
function stubBaseline() {
  mockApi.mockResolvedValue({
    user_id: 'u-1',
    username: 'alice',
    role: 'USER',
    email: 'a@b.c',
  } as never);
  mockBindings.mockResolvedValue([TG_ACTIVE]);
  mockPrefs.mockImplementation((channel?: NotificationChannel) =>
    Promise.resolve(pref(channel ?? 'telegram')),
  );
  mockStatus.mockResolvedValue(MANUAL_STATUS);
}

/** 推进 fake timers 并在 act 内冲刷微任务（React 重渲染可见）。 */
async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe('Profile 通知渠道集成（NotificationChannelSettings）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem('zhice_token', 't');
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('4. shows WeChat first and keeps Telegram as an optional overseas channel', async () => {
    stubBaseline();
    renderProfile();

    const headings = await screen.findAllByRole('heading');
    const names = headings.map((h) => h.textContent?.trim() ?? '');
    const wxIdx = names.indexOf('微信通知');
    const tgIdx = names.indexOf('Telegram（境外可选）');
    expect(wxIdx).toBeGreaterThanOrEqual(0);
    expect(tgIdx).toBeGreaterThan(wxIdx);
  });

  it('5. refreshes the matrix immediately after binding changes', async () => {
    vi.useFakeTimers();
    stubBaseline();
    mockBindings
      .mockResolvedValueOnce([TG_ACTIVE]) // 容器初始加载：微信未绑定
      .mockResolvedValueOnce([TG_ACTIVE]) // 扫码后 WxPusherBind 立即首查
      .mockResolvedValueOnce([TG_ACTIVE, WX_ACTIVE]) // t+3s 轮询发现微信 active
      .mockResolvedValue([TG_ACTIVE, WX_ACTIVE]); // refreshBindings 及其后
    mockCreate.mockResolvedValue(challenge(600_000));

    renderProfile();
    await flush(0);
    expect(screen.getByRole('switch', { name: '微信 策略信号' })).toBeDisabled();
    expect(screen.getAllByText('请先绑定').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: '扫码绑定微信' }));
    await flush(0); // 生成二维码 + 立即首查（未绑定）
    await flush(3000); // 轮询发现 active → onBindingChanged → refreshBindings

    expect(screen.getByRole('switch', { name: '微信 策略信号' })).toBeEnabled();
    expect(screen.queryByText('请先绑定')).not.toBeInTheDocument();
  });

  it('Telegram 手动卡保留渲染（bot 未配置：脱敏 chat_id + 解绑入口）', async () => {
    stubBaseline();
    renderProfile();

    expect(await screen.findByText('••••5678')).toBeInTheDocument();
    // antd 两字中文按钮自动插空格（解绑 → "解 绑"），须用正则
    expect(screen.getByRole('button', { name: /解\s*绑/ })).toBeInTheDocument();
  });

  it('bot 已配置 → 渲染 deep-link 卡（manual 输入框不出现）', async () => {
    stubBaseline();
    mockStatus.mockResolvedValue({ bound: false, chat_id: null, bot_username: 'zhice_bot' });
    renderProfile();

    // manual 与 deep-link 卡共用同名绑定按钮（telegram.bind）——先等容器按
    // bot_username 切换完成（manual 输入框消失），再等 deep-link 卡自身的
    // status 请求落定（loading→unbound）后断言按钮
    await waitFor(() =>
      expect(screen.queryByPlaceholderText(/chat_id/i)).not.toBeInTheDocument(),
    );
    expect(await screen.findByRole('button', { name: '绑定 Telegram' })).toBeInTheDocument();
  });

  it('revoked 渠道矩阵列 disabled，另一渠道不受影响', async () => {
    stubBaseline();
    mockBindings.mockResolvedValue([TG_ACTIVE, WX_REVOKED]);
    renderProfile();

    expect(await screen.findByRole('switch', { name: '微信 策略信号' })).toBeDisabled();
    expect(screen.getByRole('switch', { name: 'Telegram 策略信号' })).toBeEnabled();
  });

  it('Telegram 手动绑定成功 → 容器刷新，矩阵 TG 列 disabled→enabled', async () => {
    // 初始：两渠道均未绑定、手动模式未绑定
    mockApi.mockResolvedValue({
      user_id: 'u-1',
      username: 'alice',
      role: 'USER',
      email: 'a@b.c',
    } as never);
    mockPrefs.mockImplementation((channel?: NotificationChannel) =>
      Promise.resolve(pref(channel ?? 'telegram')),
    );
    mockBindings
      .mockResolvedValueOnce([]) // 容器初始加载：TG 未绑定
      .mockResolvedValue([TG_ACTIVE]); // 绑定回调 refreshBindings 及其后
    // 两次 Once 供 容器模式判定 + 卡初始自查 共用未绑定态；其后=绑定后
    mockStatus
      .mockResolvedValueOnce({ bound: false, chat_id: null, bot_username: null })
      .mockResolvedValueOnce({ bound: false, chat_id: null, bot_username: null })
      .mockResolvedValue({ bound: true, chat_id: '••••5678', bot_username: null });
    mockBindManual.mockResolvedValue({ ok: true, telegram_chat_id: '12345678' });

    renderProfile();
    expect(await screen.findByRole('switch', { name: 'Telegram 策略信号' })).toBeDisabled();

    const input = await screen.findByPlaceholderText(/chat_id/i);
    fireEvent.change(input, { target: { value: '12345678' } });
    fireEvent.click(screen.getByRole('button', { name: '绑定 Telegram' }));

    // 绑定成功 → 卡 onBindingChanged → 容器 refreshBindings → TG 列放开
    await waitFor(() =>
      expect(screen.getByRole('switch', { name: 'Telegram 策略信号' })).toBeEnabled(),
    );
    expect(mockBindings).toHaveBeenCalledTimes(2); // 初始 + 绑定回调刷新
  });

  it('Telegram 手动解绑成功 → 容器刷新，矩阵 TG 列 enabled→disabled', async () => {
    // 初始：TG 已绑定（手动模式）；解绑回调后 bindings 为空
    mockApi.mockResolvedValue({
      user_id: 'u-1',
      username: 'alice',
      role: 'USER',
      email: 'a@b.c',
    } as never);
    mockPrefs.mockImplementation((channel?: NotificationChannel) =>
      Promise.resolve(pref(channel ?? 'telegram')),
    );
    mockBindings
      .mockResolvedValueOnce([TG_ACTIVE]) // 容器初始加载
      .mockResolvedValue([]); // 解绑回调 refreshBindings 及其后
    mockStatus
      .mockResolvedValueOnce({ bound: true, chat_id: '••••5678', bot_username: null })
      .mockResolvedValueOnce({ bound: true, chat_id: '••••5678', bot_username: null })
      .mockResolvedValue({ bound: false, chat_id: null, bot_username: null });
    mockUnbindTelegram.mockResolvedValue({ ok: true, telegram_chat_id: null });

    renderProfile();
    expect(await screen.findByRole('switch', { name: 'Telegram 策略信号' })).toBeEnabled();

    // antd 两字中文按钮自动插空格（解绑 → "解 绑"），须用正则
    fireEvent.click(screen.getByRole('button', { name: /解\s*绑/ }));

    // 解绑成功 → 卡 onBindingChanged → 容器 refreshBindings → TG 列收回
    await waitFor(() =>
      expect(screen.getByRole('switch', { name: 'Telegram 策略信号' })).toBeDisabled(),
    );
    expect(mockBindings).toHaveBeenCalledTimes(2);
  });
});
