// C-7 管理员支付发放管理 API 客户端（ADMIN）。复用 services/api（token/JSON/401/错误码
// 翻译）与 adminEntitlementsApi 的写超时/幂等/归一化错误工具（writeWithTimeout+idemHeaders）。
//
// 铁律同权益写：GET 接 AbortSignal；写请求体绝不含 actor（actor 取自 JWT）；写操作经
// writeWithTimeout（超时→retryable AdminApiError，可同幂等键安全重试）；错误统一归一为
// AdminApiError（供 UI 按 code/retryable 决策）。
import { api } from './api';
import { idemHeaders, writeWithTimeout } from './adminEntitlementsApi';
import { AdminApiError, classifyError } from '../components/admin-entitlements/entitlementModel';

const BASE = '/admin/entitlements/payment-grants';

function enc(v: string): string {
  return encodeURIComponent(v);
}

function toUtc(v: Date | string): string {
  return (v instanceof Date ? v : new Date(v)).toISOString();
}

/** 支付发放订单管理视图（对齐后端 _grant_order_view：金额→str、时间→ISO）。 */
export interface PaymentGrantOrder {
  order_id: string;
  user_id: string;
  product_type: string; // membership | subscription
  product_ref: string | null;
  months: number;
  amount_usdt: string;
  status: string; // pending | paid | expired | canceled
  grant_status: string; // pending|processing|retrying|granted|failed|manual_review
  grant_attempts: number;
  automatic_grant_attempts: number;
  next_grant_retry_at: string | null;
  last_grant_error_code: string | null;
  last_grant_error_summary: string | null;
  grant_lease_owner: string | null;
  grant_lease_until: string | null;
  historical_grant_ambiguous: boolean;
  grant_reviewed_at: string | null;
  grant_reviewed_by_user_id: string | null;
  grant_review_decision: string | null; // RETRY | MARK_GRANTED
  granted_at: string | null;
  paid_at: string | null;
  product_name_snapshot: string;
  market_snapshot: string | null;
}

export interface PaymentGrantsPage {
  items: PaymentGrantOrder[];
  total: number;
  page: number;
  page_size: number;
}

export interface GrantWriteResult {
  status: string; // granted | already_granted | retrying | pending | manual_review ...
  order: PaymentGrantOrder;
}

export type ReviewDecision = 'RETRY' | 'MARK_GRANTED';

// ---------- 只读查询（透传 AbortSignal；错误由 api() 统一处理/抛出） ----------

export function listPaymentGrants(
  params: {
    page: number;
    pageSize: number;
    userId?: string;
    grantStatus?: string;
    fromAt?: Date | string;
    toAt?: Date | string;
  },
  signal?: AbortSignal,
): Promise<PaymentGrantsPage> {
  const parts = [`page=${params.page}`, `page_size=${params.pageSize}`];
  const uid = params.userId?.trim();
  if (uid) parts.push(`user_id=${enc(uid)}`);
  if (params.grantStatus) parts.push(`grant_status=${enc(params.grantStatus)}`);
  if (params.fromAt) parts.push(`from_at=${enc(toUtc(params.fromAt))}`);
  if (params.toAt) parts.push(`to_at=${enc(toUtc(params.toAt))}`);
  return api<PaymentGrantsPage>(`${BASE}?${parts.join('&')}`, { signal });
}

// ---------- 写操作（超时检测 + 幂等键 + 归一化错误） ----------

/** 管理员手动重试发放。granted→返当前；历史歧义→grant_review_required；活租约→grant_in_progress。 */
export async function retryGrant(
  orderId: string,
  input: { reason: string },
  idempotencyKey: string,
): Promise<GrantWriteResult> {
  try {
    const body = JSON.stringify({ reason: input.reason });
    return await writeWithTimeout((signal) =>
      api<GrantWriteResult>(`${BASE}/${enc(orderId)}/retry`, {
        method: 'POST', body, headers: idemHeaders(idempotencyKey), signal,
      }),
    );
  } catch (err) {
    throw classifyError(err);
  }
}

/** 历史歧义订单复核：RETRY 转重试队列；MARK_GRANTED 对账标记（不发权益）。 */
export async function reviewGrant(
  orderId: string,
  input: { decision: ReviewDecision; reason: string },
  idempotencyKey: string,
): Promise<GrantWriteResult> {
  try {
    const body = JSON.stringify({ decision: input.decision, reason: input.reason });
    return await writeWithTimeout((signal) =>
      api<GrantWriteResult>(`${BASE}/${enc(orderId)}/review`, {
        method: 'POST', body, headers: idemHeaders(idempotencyKey), signal,
      }),
    );
  } catch (err) {
    throw classifyError(err);
  }
}

export { AdminApiError };
