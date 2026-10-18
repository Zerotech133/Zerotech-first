// B3-1/B3-4/B3-5 管理员权益 API 客户端（ADMIN）。复用 services/api（token/JSON/401/错误码翻译），
// 写操作显式接 Idempotency-Key，请求体由 entitlementModel 纯函数构建（permanent 不发
// valid_until、until→UTC ISO、strategy_ids 去重排序 + versions 裁剪一致）。
//
// 铁律：GET 接 AbortSignal（防迟到响应）；写请求体绝不含 actor/user_id（actor 取自 JWT）；
// 写操作经 writeWithTimeout：超时→「未知结果」retryable=true（调用方可用同幂等键安全重试）；
// 写操作错误统一归一为 AdminApiError（供 UI 按 retryable/code 决策重试与提示）。
import { api } from './api';
import {
  AdminApiError,
  classifyError,
  buildMembershipBody,
  buildSetSubscriptionsBody,
  buildRevokeSubscriptionsBody,
  type UsersPage,
  type UserDetail,
  type AuditsPage,
  type GrantableStrategy,
  type MembershipSnapshot,
  type SubscriptionSnapshot,
  type MembershipInput,
  type SetSubscriptionsInput,
  type RevokeSubscriptionsInput,
} from '../components/admin-entitlements/entitlementModel';

const BASE = '/admin/entitlements';
const WRITE_TIMEOUT_MS = 15000;

function enc(v: string): string {
  return encodeURIComponent(v);
}

// ---------- 只读查询（透传 AbortSignal；错误由 api() 统一处理/抛出） ----------

export function listUsers(
  params: { page: number; pageSize: number; query?: string },
  signal?: AbortSignal,
): Promise<UsersPage> {
  const parts = [`page=${params.page}`, `page_size=${params.pageSize}`];
  const q = params.query?.trim();
  if (q) parts.push(`query=${enc(params.query as string)}`);
  return api<UsersPage>(`${BASE}/users?${parts.join('&')}`, { signal });
}

export function getUserDetail(userId: string, signal?: AbortSignal): Promise<UserDetail> {
  return api<UserDetail>(`${BASE}/users/${enc(userId)}`, { signal });
}

export interface AuditQueryInput {
  page: number;
  pageSize: number;
  targetUserId?: string;
  actorUserId?: string;
  action?: string;
  resourceType?: string;
  strategyId?: string;
  fromAt?: Date | string;
  toAt?: Date | string;
}

function toUtc(v: Date | string): string {
  return (v instanceof Date ? v : new Date(v)).toISOString();
}

export function listAudits(params: AuditQueryInput, signal?: AbortSignal): Promise<AuditsPage> {
  const parts = [`page=${params.page}`, `page_size=${params.pageSize}`];
  if (params.targetUserId) parts.push(`target_user_id=${enc(params.targetUserId)}`);
  if (params.actorUserId) parts.push(`actor_user_id=${enc(params.actorUserId)}`);
  if (params.action) parts.push(`action=${enc(params.action)}`);
  if (params.resourceType) parts.push(`resource_type=${enc(params.resourceType)}`);
  if (params.strategyId) parts.push(`strategy_id=${enc(params.strategyId)}`);
  if (params.fromAt) parts.push(`from_at=${enc(toUtc(params.fromAt))}`);
  if (params.toAt) parts.push(`to_at=${enc(toUtc(params.toAt))}`);
  return api<AuditsPage>(`${BASE}/audits?${parts.join('&')}`, { signal });
}

/** grantable 平台策略候选（订阅授权用；与支付/管理员同一资格谓词）。 */
export function listGrantableStrategies(
  signal?: AbortSignal,
): Promise<{ items: GrantableStrategy[] }> {
  return api<{ items: GrantableStrategy[] }>(`/billing/strategies`, { signal });
}

// ---------- 写操作（超时检测 + 幂等键 + 归一化错误） ----------

export function idemHeaders(key: string): Record<string, string> {
  return { 'Idempotency-Key': key };
}

/** 包裹写请求并附超时：超时 abort → 抛 retryable AdminApiError（未知结果，可同键重试）。
 *  其他错误（api() 的 HTTP 状态错误 / 网络错误）原样上抛，交由调用方 classifyError。 */
export async function writeWithTimeout<T>(run: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort('timeout'), WRITE_TIMEOUT_MS);
  try {
    return await run(ac.signal);
  } catch (e) {
    if (ac.signal.aborted && ac.signal.reason === 'timeout') {
      throw new AdminApiError('请求超时，结果未知', { retryable: true });
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

export interface MembershipWriteResult {
  membership: MembershipSnapshot;
  noop?: boolean;
}

export async function setMembership(
  userId: string,
  input: MembershipInput,
  idempotencyKey: string,
): Promise<MembershipWriteResult> {
  try {
    const body = JSON.stringify(buildMembershipBody(input));
    return await writeWithTimeout((signal) =>
      api<MembershipWriteResult>(`${BASE}/users/${enc(userId)}/membership`, {
        method: 'PUT', body, headers: idemHeaders(idempotencyKey), signal,
      }),
    );
  } catch (err) {
    throw classifyError(err);
  }
}

export interface OpeningRiskCleanup {
  disabled_instance_ids: string[];
  cancelled_plan_ids: string[];
  cancelled_approval_ids: string[];
  cancelled_intent_ids: string[];
  cancelled_request_ids: string[];
  released_reservation_ids: string[];
  released_amount: string;
  pending_external_cancellations: string[];
}

export interface RevokeMembershipResult {
  membership: MembershipSnapshot;
  opening_risk_cleanup: OpeningRiskCleanup;
  noop?: boolean;
}

export async function revokeMembership(
  userId: string,
  input: { reason: string; version: number },
  idempotencyKey: string,
): Promise<RevokeMembershipResult> {
  try {
    const body = JSON.stringify({ reason: input.reason, version: input.version });
    return await writeWithTimeout((signal) =>
      api<RevokeMembershipResult>(`${BASE}/users/${enc(userId)}/membership/revoke`, {
        method: 'POST', body, headers: idemHeaders(idempotencyKey), signal,
      }),
    );
  } catch (err) {
    throw classifyError(err);
  }
}

export interface SubscriptionsWriteResult {
  subscriptions: SubscriptionSnapshot[];
}

export async function setSubscriptions(
  userId: string,
  input: SetSubscriptionsInput,
  idempotencyKey: string,
): Promise<SubscriptionsWriteResult> {
  try {
    const body = JSON.stringify(buildSetSubscriptionsBody(input));
    return await writeWithTimeout((signal) =>
      api<SubscriptionsWriteResult>(`${BASE}/users/${enc(userId)}/subscriptions`, {
        method: 'PUT', body, headers: idemHeaders(idempotencyKey), signal,
      }),
    );
  } catch (err) {
    throw classifyError(err);
  }
}

export async function revokeSubscriptions(
  userId: string,
  input: RevokeSubscriptionsInput,
  idempotencyKey: string,
): Promise<SubscriptionsWriteResult> {
  try {
    const body = JSON.stringify(buildRevokeSubscriptionsBody(input));
    return await writeWithTimeout((signal) =>
      api<SubscriptionsWriteResult>(`${BASE}/users/${enc(userId)}/subscriptions/revoke`, {
        method: 'POST', body, headers: idemHeaders(idempotencyKey), signal,
      }),
    );
  } catch (err) {
    throw classifyError(err);
  }
}

export interface RoleWriteResult {
  user_id: string;
  username: string;
  role: string;
  auth_version: number;
}

/** 角色变更走 /auth 审计事务端点（非 /admin/entitlements）；body 含 reason，
 *  幂等键稳定可重试。成功返回新 role 与递增后的 auth_version（用于后续乐观锁）。 */
export async function changeRole(
  userId: string,
  input: { role: string; authVersion: number; reason: string },
  idempotencyKey: string,
): Promise<RoleWriteResult> {
  try {
    const body = JSON.stringify({ role: input.role, auth_version: input.authVersion, reason: input.reason });
    return await writeWithTimeout((signal) =>
      api<RoleWriteResult>(`/auth/users/${enc(userId)}/role`, {
        method: 'PUT', body, headers: idemHeaders(idempotencyKey), signal,
      }),
    );
  } catch (err) {
    throw classifyError(err);
  }
}
