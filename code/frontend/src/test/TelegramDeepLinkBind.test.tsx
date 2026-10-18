/**
 * Task 6 UI 测试：Telegram deep-link 绑定（TelegramDeepLinkBind），Phase D 多用户前端。
 * Task 15 起原内嵌的用户级偏好表单测试段已随组件契约变更（props 驱动双渠道
 * 矩阵）迁出至 src/test/UserNotificationPreferencesForm.test.tsx。
 *
 * 计划红测试语义（docs/superpowers/plans/2026-09-06-multi-user-frontend-migration-rollout.md
 * Task 6 Step 1，照 RTL 适配）：
 * - 绑定：createChallenge → window.open(url 含 start= token, '_blank',
 *   'noopener,noreferrer')；token 绝不渲染为文本；无手动 chat_id 输入框；
 * - 轮询治理：挑战活跃期每 3s 查 status；bound 即停（不再涨）；卸载即停；
 * - 已绑定：显示脱敏 chat_id（尾 4）+ 解绑确认弹窗 → unbind → 回未绑定；
 * - 过期：提示 + 重新生成（第二次 createChallenge）；
 * - 绑定未开启：status bot_username=null / 409 telegram.binding_disabled →
 *   管理员引导文案（不报错堆栈）
 *
 * 安全铁律：token 原文与完整 chat_id 绝不出现在 DOM；manual PATCH 路径已死。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('../services/notificationsApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/notificationsApi')>();
  return {
    ...actual,
    createTelegramChallenge: vi.fn(),
    getTelegramBindingStatus: vi.fn(),
    unbindTelegram: vi.fn(),
    testTelegram: vi.fn(),
  };
});

import '../i18n'; // 触发 i18n 初始化（默认 zh），否则 t() 返回 key 而非中文
import i18n from '../i18n';
import TelegramDeepLinkBind from '../components/TelegramDeepLinkBind';
import {
  createTelegramChallenge,
  getTelegramBindingStatus,
  unbindTelegram,
  testTelegram,
} from '../services/notificationsApi';

const mockChallenge = vi.mocked(createTelegramChallenge);
const mockStatus = vi.mocked(getTelegramBindingStatus);
const mockUnbind = vi.mocked(unbindTelegram);
const mockTest = vi.mocked(testTelegram);

// jsdom 未实现 window.open：以 spy 接管并断言调用参数
const openSpy = vi.spyOn(window, 'open').mockImplementation((() => null) as typeof window.open);

// ---------- 类型化样例（对齐 auth.py / my_notification_preferences.py 契约） ----------

const UNBOUND = { bound: false, chat_id: null, bot_username: 'zhice_bot' };

/** 一次性 challenge（url 含 token——仅 window.open 用，绝不渲染）。 */
const challenge = (ms: number) => ({
  url: 'https://t.me/zhice_bot?start=synthetic-token',
  expires_at: new Date(Date.now() + ms).toISOString(),
});

// ---------- TelegramDeepLinkBind（deep-link 绑定；fake timers 驱动轮询/倒计时） ----------

describe('TelegramDeepLinkBind（deep-link 绑定）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  /** 推进 fake timers 并在 act 内冲刷微任务（React 重渲染可见）。 */
  async function flush(ms = 0) {
    const { act } = await import('@testing-library/react');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  it('绑定流程：点绑定→打开 Telegram（window.open 含 start= token，_blank + noopener,noreferrer）；token 绝不渲染为文本；无手动 chat_id 输入框', async () => {
    mockStatus.mockResolvedValue(UNBOUND);
    mockChallenge.mockResolvedValue(challenge(300_000));
    render(<TelegramDeepLinkBind />);
    await flush(0); // mount status

    fireEvent.click(screen.getByRole('button', { name: '绑定 Telegram' }));
    await flush(0); // challenge → active
    fireEvent.click(screen.getByRole('button', { name: '打开 Telegram' }));

    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(openSpy).toHaveBeenCalledWith(
      'https://t.me/zhice_bot?start=synthetic-token',
      '_blank',
      'noopener,noreferrer',
    );
    // 安全铁律：一次性 token 不出现在 DOM；卡片内无任何文本输入框（manual 路径已死）
    expect(document.body.textContent).not.toContain('synthetic-token');
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    // bot 用户名展示 + 倒计时出现
    expect(screen.getByText(/@zhice_bot/)).toBeInTheDocument();
    expect(screen.getByText(/有效期剩余/)).toBeInTheDocument();
  });

  it('轮询治理：活跃期每 3s 查 status，bound 即停并展示脱敏 chat_id，此后不再轮询', async () => {
    mockStatus
      .mockResolvedValueOnce(UNBOUND) // mount
      .mockResolvedValueOnce(UNBOUND) // poll @3s
      .mockResolvedValueOnce({ bound: true, chat_id: '****1234', bot_username: 'zhice_bot' }); // poll @6s
    mockChallenge.mockResolvedValue(challenge(600_000));
    render(<TelegramDeepLinkBind />);
    await flush(0);
    fireEvent.click(screen.getByRole('button', { name: '绑定 Telegram' }));
    await flush(0);
    expect(screen.getByRole('button', { name: '打开 Telegram' })).toBeInTheDocument();

    await flush(6000); // 两轮轮询，第二轮 bound
    expect(screen.getByText(/已绑定：\*+1234/)).toBeInTheDocument();
    const callsAfterBound = mockStatus.mock.calls.length;

    await flush(15_000); // 绑定后不再发任何 status 请求
    expect(mockStatus.mock.calls.length).toBe(callsAfterBound);
  });

  it('轮询治理：卸载即停（防泄漏轮询）', async () => {
    mockStatus.mockResolvedValue(UNBOUND);
    mockChallenge.mockResolvedValue(challenge(600_000));
    const { unmount } = render(<TelegramDeepLinkBind />);
    await flush(0);
    fireEvent.click(screen.getByRole('button', { name: '绑定 Telegram' }));
    await flush(0);
    unmount();
    const n = mockStatus.mock.calls.length;
    await flush(20_000);
    expect(mockStatus.mock.calls.length).toBe(n);
  });

  it('过期：提示 + 重新生成按钮（打开链接入口消失）；点击重新生成第二次 createChallenge', async () => {
    mockStatus.mockResolvedValue(UNBOUND);
    mockChallenge
      .mockResolvedValueOnce(challenge(3000)) // 3 秒即过期
      .mockResolvedValue(challenge(300_000));
    render(<TelegramDeepLinkBind />);
    await flush(0);
    fireEvent.click(screen.getByRole('button', { name: '绑定 Telegram' }));
    await flush(0);

    await flush(4000); // 倒计时归零 → expired
    expect(screen.getByText('绑定链接已过期，请重新生成')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '打开 Telegram' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '重新生成' }));
    await flush(0);
    expect(mockChallenge).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', { name: '打开 Telegram' })).toBeInTheDocument();
  });

  it('已绑定视图：显示脱敏 chat_id（尾 4）+ 解绑确认弹窗 → unbind → 回未绑定', async () => {
    mockStatus
      .mockResolvedValueOnce({ bound: true, chat_id: '****6789', bot_username: 'zhice_bot' })
      .mockResolvedValue(UNBOUND); // 解绑后 refresh
    mockUnbind.mockResolvedValue({ ok: true, telegram_chat_id: null });
    render(<TelegramDeepLinkBind />);
    await flush(0);
    expect(screen.getByText(/已绑定：\*+6789/)).toBeInTheDocument();

    // antd 两字中文按钮自动插入空格（解绑 → "解 绑"），须用正则
    fireEvent.click(screen.getByRole('button', { name: /解\s*绑/ }));
    await flush(100); // Popconfirm 弹层
    fireEvent.click(screen.getByRole('button', { name: /确\s*认/ }));
    await flush(0); // unbind + refresh
    expect(mockUnbind).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: '绑定 Telegram' })).toBeInTheDocument();
  });

  it('管理员未开启（status bot_username=null）：显示引导文案，不提供绑定按钮', async () => {
    mockStatus.mockResolvedValue({ bound: false, chat_id: null, bot_username: null });
    render(<TelegramDeepLinkBind />);
    await flush(0);
    expect(screen.getByText('管理员未开启 Telegram 绑定，请联系管理员开启后再试。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '绑定 Telegram' })).not.toBeInTheDocument();
    expect(mockChallenge).not.toHaveBeenCalled();
  });

  // ---------- B7 A1：测试消息 reason → 人话提示 ----------

  const BOUND = { bound: true, chat_id: '****6789', bot_username: 'zhice_bot' };

  it('已绑定点「发送测试消息」成功：无失败 Alert，按钮复位', async () => {
    mockStatus.mockResolvedValue(BOUND);
    mockTest.mockResolvedValue({ ok: true });
    render(<TelegramDeepLinkBind />);
    await flush(0);

    // loading 态 accessible name 带 "loading " 前缀 → 用正则匹配
    fireEvent.click(screen.getByRole('button', { name: /发送测试消息/ }));
    await flush(0);
    await flush(0);
    expect(mockTest).toHaveBeenCalledTimes(1);
    // 成功不渲染失败 Alert
    expect(screen.queryByText(/发送失败/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /发送测试消息/ })).not.toBeDisabled();
  });

  it('测试失败 reason=network → 网络不通人话提示（含检查代理）', async () => {
    mockStatus.mockResolvedValue(BOUND);
    mockTest.mockResolvedValue({ ok: false, reason: 'network' });
    render(<TelegramDeepLinkBind />);
    await flush(0);

    fireEvent.click(screen.getByRole('button', { name: /发送测试消息/ }));
    await flush(0);
    await flush(0);
    expect(
      screen.getByText(/网络不通（服务器需能访问 api.telegram.org，请检查代理设置）/),
    ).toBeInTheDocument();
  });

  it('测试失败 reason=not_configured → 未配置提示；http_401 → token 失效提示', async () => {
    mockStatus.mockResolvedValue(BOUND);
    mockTest.mockResolvedValueOnce({ ok: false, reason: 'not_configured' });
    render(<TelegramDeepLinkBind />);
    await flush(0);

    fireEvent.click(screen.getByRole('button', { name: /发送测试消息/ }));
    await flush(0);
    await flush(0);
    expect(screen.getByText(/推送未配置（缺少 bot token）/)).toBeInTheDocument();

    // loading 解除后再点（disabled 按钮 jsdom 不派发 click），换 http_401 覆盖前一条
    mockTest.mockResolvedValueOnce({ ok: false, reason: 'http_401' });
    await flush(50);
    fireEvent.click(screen.getByRole('button', { name: /发送测试消息/ }));
    await flush(0);
    await flush(0);
    expect(screen.getByText(/bot token 已失效或无权向该会话发消息/)).toBeInTheDocument();
    expect(screen.queryByText(/推送未配置/)).not.toBeInTheDocument();
  });

  it('challenge 409 telegram.binding_disabled：转管理员引导文案（不抛错误堆栈）', async () => {
    mockStatus.mockResolvedValue(UNBOUND);
    // api() 层把 detail.code 翻译为 i18n.t(code, ns=errors) 后抛出——用同 key 构造
    mockChallenge.mockRejectedValue(
      new Error(i18n.t('telegram.binding_disabled', { ns: 'errors' })),
    );
    render(<TelegramDeepLinkBind />);
    await flush(0);
    fireEvent.click(screen.getByRole('button', { name: '绑定 Telegram' }));
    await flush(0);
    expect(screen.getByText('Telegram 绑定未开启')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '打开 Telegram' })).not.toBeInTheDocument();
  });

  // Task 15 必修：可选 prop onBindingChanged——轮询发现 bound 与解绑成功后回调
  // （父级容器刷新渠道总览）；缺省不回调，上方既有用例均无 prop 渲染，行为不变。
  it('onBindingChanged：轮询发现 bound 与解绑成功后各恰回调一次', async () => {
    const onChanged = vi.fn();
    mockStatus
      .mockResolvedValueOnce(UNBOUND) // 卡初始自查
      .mockResolvedValueOnce(BOUND) // t+3s 轮询发现 bound
      .mockResolvedValue(UNBOUND); // 解绑后 refresh
    mockChallenge.mockResolvedValue(challenge(600_000));
    mockUnbind.mockResolvedValue({ ok: true, telegram_chat_id: null });
    render(<TelegramDeepLinkBind onBindingChanged={onChanged} />);
    await flush(0);

    fireEvent.click(screen.getByRole('button', { name: '绑定 Telegram' }));
    await flush(0);
    await flush(3000); // 轮询发现 bound → onBound → 回调#1
    expect(screen.getByText(/已绑定：\*+6789/)).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledTimes(1);

    // antd 两字中文按钮自动插空格（解绑 → "解 绑"），须用正则
    fireEvent.click(screen.getByRole('button', { name: /解\s*绑/ }));
    await flush(100); // Popconfirm 弹层
    fireEvent.click(screen.getByRole('button', { name: /确\s*认/ }));
    await flush(0); // unbind + refresh → 回调#2
    expect(onChanged).toHaveBeenCalledTimes(2);
  });
});
