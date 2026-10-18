// 手动 Telegram 绑定组件：未绑定→填 chat_id 绑定；已绑定→展示脱敏+解绑。
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../services/notificationsApi', () => ({
  getTelegramBindingStatus: vi.fn(),
  bindTelegramManual: vi.fn(),
  unbindTelegram: vi.fn(),
}));

import '../i18n';
import TelegramManualBind from '../components/TelegramManualBind';
import {
  getTelegramBindingStatus, bindTelegramManual, unbindTelegram,
} from '../services/notificationsApi';

const mStatus = vi.mocked(getTelegramBindingStatus);
const mBind = vi.mocked(bindTelegramManual);
const mUnbind = vi.mocked(unbindTelegram);

beforeEach(() => { vi.clearAllMocks(); localStorage.setItem('zhice_token', 't'); });
afterEach(() => vi.restoreAllMocks());

describe('TelegramManualBind', () => {
  it('未绑定→填 chat_id 点绑定调用 bindTelegramManual', async () => {
    mStatus.mockResolvedValueOnce({ bound: false, chat_id: null, bot_username: null });
    mStatus.mockResolvedValueOnce({ bound: true, chat_id: '••••5678', bot_username: null });
    mBind.mockResolvedValue({ ok: true, telegram_chat_id: '12345678' } as never);
    render(<TelegramManualBind />);
    const input = await screen.findByPlaceholderText(/chat_id/i);
    fireEvent.change(input, { target: { value: '12345678' } });
    fireEvent.click(screen.getByRole('button', { name: /绑\s*定|Bind/ }));
    await waitFor(() => expect(mBind).toHaveBeenCalledWith('12345678'));
  });

  it('已绑定→显示脱敏 chat_id + 点解绑调用 unbindTelegram', async () => {
    mStatus.mockResolvedValueOnce({ bound: true, chat_id: '••••5678', bot_username: null });
    mStatus.mockResolvedValueOnce({ bound: false, chat_id: null, bot_username: null });
    mUnbind.mockResolvedValue({ ok: true, telegram_chat_id: null });
    render(<TelegramManualBind />);
    expect(await screen.findByText('••••5678')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /解\s*绑|Unbind/ }));
    await waitFor(() => expect(mUnbind).toHaveBeenCalledTimes(1));
  });

  it('空 chat_id 不提交', async () => {
    mStatus.mockResolvedValue({ bound: false, chat_id: null, bot_username: null });
    render(<TelegramManualBind />);
    await screen.findByPlaceholderText(/chat_id/i);
    fireEvent.click(screen.getByRole('button', { name: /绑\s*定|Bind/ }));
    await waitFor(() => expect(mBind).not.toHaveBeenCalled());
  });

  // Task 15 必修：可选 prop onBindingChanged——绑定/解绑成功后回调（父级容器
  // 刷新渠道总览）；缺省不回调，独立使用行为不变（上方既有用例即无 prop 渲染）。
  it('onBindingChanged：绑定成功与解绑成功后各恰回调一次', async () => {
    const onChanged = vi.fn();
    mStatus
      .mockResolvedValueOnce({ bound: false, chat_id: null, bot_username: null }) // 卡初始自查
      .mockResolvedValueOnce({ bound: true, chat_id: '••••5678', bot_username: null }) // 绑定后 refresh
      .mockResolvedValueOnce({ bound: false, chat_id: null, bot_username: null }); // 解绑后 refresh
    mBind.mockResolvedValue({ ok: true, telegram_chat_id: '12345678' } as never);
    mUnbind.mockResolvedValue({ ok: true, telegram_chat_id: null });
    render(<TelegramManualBind onBindingChanged={onChanged} />);

    const input = await screen.findByPlaceholderText(/chat_id/i);
    fireEvent.change(input, { target: { value: '12345678' } });
    fireEvent.click(screen.getByRole('button', { name: /绑\s*定|Bind/ }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: /解\s*绑|Unbind/ }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(2));
  });
});
