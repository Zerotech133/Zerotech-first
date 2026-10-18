/**
 * Task 14 UI 测试：WxPusherBind（微信扫码绑定卡）。
 *
 * 计划最小测试（语义一致，fake timers 驱动轮询/倒计时）：
 * 1. 未绑定点「扫码绑定微信」→ 展示服务端二维码 <img alt=微信绑定二维码
 *    src=qr_image_url>；challenge token 绝不渲染为文本（响应即使多带 token 字段
 *    也不得出现在 DOM）；
 * 2. 扫码期轮询 getNotificationBindings（生成后立即首查 + 每 3s）：发现 wxpusher
 *    active 即展示脱敏 UID、立即停止轮询（调用数不再涨）并回调 onBindingChanged；
 * 3. 二维码过期（倒计时归零）：清空二维码状态，「重新生成二维码」可用、点击二次
 *    生成；过期后轮询停止；
 * 4. 解绑需二次确认（Popconfirm）：确认前 unbindWxPusher 不被调，确认后恰 1 次。
 * 另补：卸载无 timer 泄漏（vi.getTimerCount()==0）；测试按钮调 testWxPusher；
 * revoked 态渲染 + 重新绑定入口；challenge 失败 best-effort 文案；en locale 无
 * 中文 fallback。
 *
 * 安全铁律：二维码仅 <img src=qr_image_url>（后端生成、不含 token），不用 antd
 * QRCode 二次编码；临时 URL/倒计时只存 React state，不写 localStorage。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

vi.mock('../services/notificationsApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/notificationsApi')>();
  return {
    ...actual,
    createWxPusherChallenge: vi.fn(),
    getNotificationBindings: vi.fn(),
    unbindWxPusher: vi.fn(),
    testWxPusher: vi.fn(),
  };
});

import '../i18n'; // 触发 i18n 初始化（默认 zh），否则 t() 返回 key 而非中文
import i18n from '../i18n';
import WxPusherBind from '../components/WxPusherBind';
import {
  createWxPusherChallenge,
  getNotificationBindings,
  testWxPusher,
  unbindWxPusher,
  type NotificationBinding,
  type WxPusherChallenge,
} from '../services/notificationsApi';

const mockCreate = vi.mocked(createWxPusherChallenge);
const mockGetBindings = vi.mocked(getNotificationBindings);
const mockUnbind = vi.mocked(unbindWxPusher);
const mockTest = vi.mocked(testWxPusher);

// ---------- 类型化样例（对齐 notificationsApi DTO；recipient 后端已脱敏） ----------

const ACTIVE_BINDING: NotificationBinding = {
  channel: 'wxpusher',
  status: 'active',
  recipient_masked: 'UID_***3456',
  bound_at: '2026-09-01T08:00:00Z',
};

const REVOKED_BINDING: NotificationBinding = {
  channel: 'wxpusher',
  status: 'revoked',
  recipient_masked: null,
  bound_at: null,
};

/** 一次性二维码 challenge（qr_image_url 由后端生成、不含 token）。 */
const challenge = (ms: number, path = 'q1'): WxPusherChallenge => ({
  qr_image_url: `https://wxpusher.zjie.work/qr/s/${path}.png`,
  expires_at: new Date(Date.now() + ms).toISOString(),
});

describe('WxPusherBind（微信扫码绑定卡）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  /** 推进 fake timers 并在 act 内冲刷微任务（React 重渲染可见）。 */
  async function flush(ms = 0) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  it('1. 未绑定点「扫码绑定微信」→ 展示服务端二维码 img（alt/src 正确），challenge token 绝不渲染', async () => {
    // 模拟响应多带 token 字段（后端契约本不返回）——组件也不得把它带进 DOM
    const leaky = { ...challenge(300_000), token: 'synthetic-wxpusher-token' };
    mockCreate.mockResolvedValue(leaky as WxPusherChallenge);
    mockGetBindings.mockResolvedValue([]);

    render(<WxPusherBind binding={null} onBindingChanged={vi.fn()} />);
    await flush(0);
    fireEvent.click(screen.getByRole('button', { name: '扫码绑定微信' }));
    await flush(0);

    const img = screen.getByAltText('微信绑定二维码');
    expect(img).toHaveAttribute('src', 'https://wxpusher.zjie.work/qr/s/q1.png');
    expect(screen.getByText(/有效期剩余/)).toBeInTheDocument(); // 倒计时展示
    expect(document.body.textContent).not.toContain('synthetic-wxpusher-token');
  });

  it('2. 扫码期轮询：发现 wxpusher active 即展示脱敏 UID、立即停止轮询并回调 onBindingChanged', async () => {
    mockCreate.mockResolvedValue(challenge(600_000));
    mockGetBindings
      .mockResolvedValueOnce([]) // 生成二维码后立即首查：未绑定
      .mockResolvedValue([ACTIVE_BINDING]); // t+3s 轮询：已 active
    const onChanged = vi.fn();

    render(<WxPusherBind binding={null} onBindingChanged={onChanged} />);
    await flush(0);
    fireEvent.click(screen.getByRole('button', { name: '扫码绑定微信' }));
    await flush(0); // 首查（未绑定）
    expect(mockGetBindings).toHaveBeenCalledTimes(1);

    await flush(3000); // 轮询发现 active
    expect(screen.getByText(/UID_\*\*\*3456/)).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledTimes(1);

    const calls = mockGetBindings.mock.calls.length;
    await flush(6000); // 绑定后不再轮询
    expect(mockGetBindings.mock.calls.length).toBe(calls);
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it('3. 二维码过期：清空二维码状态，「重新生成二维码」可用并可再生成；过期后轮询停止', async () => {
    mockCreate
      .mockResolvedValueOnce(challenge(1000, 'q1')) // 1 秒即过期
      .mockResolvedValue(challenge(300_000, 'q2'));
    mockGetBindings.mockResolvedValue([]);

    render(<WxPusherBind binding={null} onBindingChanged={vi.fn()} />);
    await flush(0);
    fireEvent.click(screen.getByRole('button', { name: '扫码绑定微信' }));
    await flush(0);
    expect(screen.getByAltText('微信绑定二维码')).toBeInTheDocument();

    await flush(1100); // 倒计时归零 → expired
    expect(screen.queryByAltText('微信绑定二维码')).not.toBeInTheDocument();
    expect(screen.getByText('二维码已过期，请重新生成')).toBeInTheDocument();
    const regen = screen.getByRole('button', { name: '重新生成二维码' });
    expect(regen).not.toBeDisabled();

    const calls = mockGetBindings.mock.calls.length; // 过期态下不再轮询
    await flush(7000);
    expect(mockGetBindings.mock.calls.length).toBe(calls);

    fireEvent.click(regen); // 点击可再生成
    await flush(0);
    expect(mockCreate).toHaveBeenCalledTimes(2);
    expect(screen.getByAltText('微信绑定二维码')).toHaveAttribute(
      'src',
      'https://wxpusher.zjie.work/qr/s/q2.png',
    );
  });

  it('4. 解绑需二次确认：确认前 unbindWxPusher 不被调，确认后恰 1 次', async () => {
    mockUnbind.mockResolvedValue({ ok: true });
    const onChanged = vi.fn();
    render(<WxPusherBind binding={ACTIVE_BINDING} onBindingChanged={onChanged} />);
    await flush(0);
    expect(screen.getByText(/UID_\*\*\*3456/)).toBeInTheDocument();

    // antd 两字中文按钮自动插入空格（解绑 → "解 绑"），须用正则
    fireEvent.click(screen.getByRole('button', { name: /解\s*绑/ }));
    await flush(100); // Popconfirm 弹层
    expect(mockUnbind).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /确\s*认/ }));
    await flush(0);
    expect(mockUnbind).toHaveBeenCalledTimes(1);
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it('5. 卸载清理全部 timer（轮询/倒计时无泄漏）', async () => {
    mockCreate.mockResolvedValue(challenge(600_000));
    mockGetBindings.mockResolvedValue([]);
    const { unmount } = render(<WxPusherBind binding={null} onBindingChanged={vi.fn()} />);
    await flush(0);
    fireEvent.click(screen.getByRole('button', { name: '扫码绑定微信' }));
    await flush(0);
    expect(vi.getTimerCount()).toBeGreaterThan(0); // 轮询 + 倒计时 interval 在跑
    // antd Button 点击自带 wave 动效的一次性 setTimeout（与组件无关），先放它到期
    await flush(600);

    unmount();
    expect(vi.getTimerCount()).toBe(0);
    const calls = mockGetBindings.mock.calls.length;
    await flush(20_000);
    expect(mockGetBindings.mock.calls.length).toBe(calls);
  });

  it('6. 已绑定视图：脱敏 UID + 绑定时间 + 测试按钮调 testWxPusher', async () => {
    mockTest.mockResolvedValue({ ok: true });
    render(<WxPusherBind binding={ACTIVE_BINDING} onBindingChanged={vi.fn()} />);
    await flush(0);
    expect(screen.getByText(/UID_\*\*\*3456/)).toBeInTheDocument();
    expect(screen.getByText(/绑定时间/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /发送测试消息/ }));
    await flush(0);
    await flush(0);
    expect(mockTest).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/发送失败/)).not.toBeInTheDocument();
  });

  it('6b. 测试失败（ok=false）：显示失败文案，组件不崩', async () => {
    mockTest.mockResolvedValue({ ok: false, reason: 'network' });
    render(<WxPusherBind binding={ACTIVE_BINDING} onBindingChanged={vi.fn()} />);
    await flush(0);

    fireEvent.click(screen.getByRole('button', { name: /发送测试消息/ }));
    await flush(0);
    await flush(0);
    expect(screen.getByText(/发送失败/)).toBeInTheDocument();
  });

  it('7. revoked 态：显示已解绑文案 + 重新绑定入口（点击生成新二维码）', async () => {
    mockCreate.mockResolvedValue(challenge(300_000, 'q3'));
    mockGetBindings.mockResolvedValue([]);
    render(<WxPusherBind binding={REVOKED_BINDING} onBindingChanged={vi.fn()} />);
    await flush(0);
    expect(screen.getByText('微信绑定已解除')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /解\s*绑/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /重新绑定/ }));
    await flush(0);
    expect(mockCreate).toHaveBeenCalledTimes(1);
    expect(screen.getByAltText('微信绑定二维码')).toBeInTheDocument();
  });

  it('8. challenge 创建失败：显示加载失败文案（best-effort），组件不崩', async () => {
    mockCreate.mockRejectedValue(new Error('boom'));
    render(<WxPusherBind binding={null} onBindingChanged={vi.fn()} />);
    await flush(0);
    fireEvent.click(screen.getByRole('button', { name: '扫码绑定微信' }));
    await flush(0);
    expect(screen.getByText('二维码加载失败，请重试')).toBeInTheDocument();
    expect(screen.queryByAltText('微信绑定二维码')).not.toBeInTheDocument();
  });

  it('9. en locale：渲染英文文案，无中文 fallback', async () => {
    try {
      await act(async () => {
        await i18n.changeLanguage('en');
      });
      render(<WxPusherBind binding={null} onBindingChanged={vi.fn()} />);
      await flush(0);
      expect(screen.getByText('WeChat Notifications')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Scan QR to bind WeChat' })).toBeInTheDocument();
      expect(document.body.textContent).not.toContain('扫码绑定微信');
    } finally {
      await act(async () => {
        await i18n.changeLanguage('zh');
      });
    }
  });
});
