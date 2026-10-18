// C-7 PaymentGrantsPage：发放队列列表渲染 + 重试弹窗触发 retryGrant + 歧义行走复核。
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../services/adminPaymentGrantsApi', () => ({
  listPaymentGrants: vi.fn(),
  retryGrant: vi.fn(),
  reviewGrant: vi.fn(),
}));

import '../i18n';
import PaymentGrantsPage from '../pages/PaymentGrantsPage';
import {
  listPaymentGrants, retryGrant, reviewGrant,
} from '../services/adminPaymentGrantsApi';

const mList = vi.mocked(listPaymentGrants);
const mRetry = vi.mocked(retryGrant);
const mReview = vi.mocked(reviewGrant);

const base = {
  user_id: 'u1', product_type: 'membership', product_ref: null, months: 1,
  amount_usdt: '9.90000000', status: 'paid', grant_attempts: 3,
  automatic_grant_attempts: 3, next_grant_retry_at: null,
  last_grant_error_code: null, last_grant_error_summary: null,
  grant_lease_owner: null, grant_lease_until: null,
  grant_reviewed_at: null, grant_reviewed_by_user_id: null, grant_review_decision: null,
  granted_at: null, paid_at: '2026-09-10T00:00:00Z',
  product_name_snapshot: 'membership', market_snapshot: null,
};
const failedOrder = { ...base, order_id: 'o-failed', grant_status: 'failed', historical_grant_ambiguous: false };
const ambiguousOrder = { ...base, order_id: 'o-amb', grant_status: 'manual_review', historical_grant_ambiguous: true };

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('zhice_token', 'tok');
  localStorage.setItem('zhice_role', 'ADMIN');
});

afterEach(() => vi.restoreAllMocks());

describe('PaymentGrantsPage', () => {
  it('渲染发放队列并可对 failed 订单重试', async () => {
    mList.mockResolvedValue({ items: [failedOrder] as never, total: 1, page: 1, page_size: 20 });
    mRetry.mockResolvedValue({ status: 'granted', order: { ...failedOrder, grant_status: 'granted' } } as never);

    render(<MemoryRouter><PaymentGrantsPage /></MemoryRouter>);
    expect(await screen.findByText('o-failed')).toBeInTheDocument();

    fireEvent.click(await screen.findByRole('button', { name: /重\s*试发放|重试发放/ }));
    // 弹窗打开：填原因后确认
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByPlaceholderText(/操作原因/), { target: { value: '手动补发' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /重\s*试发放|重试发放/ }));

    await waitFor(() => expect(mRetry).toHaveBeenCalledTimes(1));
    const [orderId, input] = mRetry.mock.calls[0];
    expect(orderId).toBe('o-failed');
    expect(input).toEqual({ reason: '手动补发' });
  });

  it('缺原因不提交重试', async () => {
    mList.mockResolvedValue({ items: [failedOrder] as never, total: 1, page: 1, page_size: 20 });
    render(<MemoryRouter><PaymentGrantsPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: /重\s*试发放|重试发放/ }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: /重\s*试发放|重试发放/ }));
    await waitFor(() => expect(mRetry).not.toHaveBeenCalled());
  });

  it('历史歧义订单走复核（MARK_GRANTED）', async () => {
    mList.mockResolvedValue({ items: [ambiguousOrder] as never, total: 1, page: 1, page_size: 20 });
    mReview.mockResolvedValue({ status: 'granted', order: { ...ambiguousOrder, grant_status: 'granted' } } as never);

    render(<MemoryRouter><PaymentGrantsPage /></MemoryRouter>);
    expect(await screen.findByText('o-amb')).toBeInTheDocument();

    fireEvent.click(await screen.findByRole('button', { name: /复\s*核/ }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByText(/对账标记已发放/));
    fireEvent.change(within(dialog).getByPlaceholderText(/操作原因/), { target: { value: '对账无误' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /复\s*核/ }));

    await waitFor(() => expect(mReview).toHaveBeenCalledTimes(1));
    const [orderId, input] = mReview.mock.calls[0];
    expect(orderId).toBe('o-amb');
    expect(input).toEqual({ decision: 'MARK_GRANTED', reason: '对账无误' });
  });
});
