// B3-1 管理员权益前端模型：DTO 类型（逐字段对齐 Gate 0 后端契约）+ 请求体构建纯函数
// + 统一错误分类。纯逻辑不依赖 React / fetch，便于单测与在 UI 各处复用。
//
// 契约来源（后端权威）：
//  - domain/billing/entitlement.py membership_view / subscription_view
//  - application/services/admin_entitlement_service.py list_users / get_user_detail
//  - api/schemas/admin_entitlements.py 写体；api/routers/admin_entitlements.py 错误码映射
//  - api/routers/billing.py GET /strategies（grantable 候选）

export type EffectiveStatus = 'free' | 'active' | 'permanent' | 'expired' | 'revoked';

/** 用户摘要里的会员实时态（list_users 投影，非完整 view）。 */
export interface MembershipSummary {
  effective_status: EffectiveStatus;
  is_permanent: boolean;
  valid_until: string | null;
  version: number;
}

export interface UserSummary {
  user_id: string;
  username: string;
  role: string;
  is_active: boolean;
  auth_version: number;
  membership: MembershipSummary;
  active_subscription_count: number;
}

export interface UsersPage {
  items: UserSummary[];
  total: number;
  page: number;
  page_size: number;
}

/** 详情页完整会员视图（membership_view：stored/effective 分离）。 */
export interface MembershipDetail {
  tier: string;
  stored_status: string;
  effective_status: EffectiveStatus;
  is_active: boolean;
  is_permanent: boolean;
  valid_until: string | null;
  revoked_at: string | null;
  version: number;
}

export interface SubscriptionDetail {
  strategy_id: string;
  stored_status: string;
  effective_status: EffectiveStatus;
  is_active: boolean;
  is_permanent: boolean;
  valid_until: string | null;
  revoked_at: string | null;
  version: number;
}

export interface AuditView {
  audit_id: string;
  operation_id: string | null;
  actor_user_id: string;
  target_user_id: string;
  source_type: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  reason: string;
  before_state: unknown;
  after_state: unknown;
  source_order_id: string | null;
  created_at: string | null;
}

export interface UserDetail {
  user: {
    user_id: string;
    username: string;
    role: string;
    is_active: boolean;
    auth_version: number;
    email: string | null;
  };
  membership: MembershipDetail;
  subscriptions: SubscriptionDetail[];
  recent_audits: AuditView[];
  payment_grant_summary: { pending: number };
}

/** 写响应快照（membership_snapshot / sub_snapshot，字段少于 view）。 */
export interface MembershipSnapshot {
  effective_status: EffectiveStatus;
  version: number;
  is_permanent: boolean;
  valid_until?: string | null;
}

export interface SubscriptionSnapshot {
  strategy_id: string;
  effective_status: EffectiveStatus;
  version: number;
  is_permanent: boolean;
  valid_until?: string | null;
}

export interface GrantableStrategy {
  strategy_id: string;
  strategy_name: string;
  market: string;
}

export interface AuditsPage {
  items: AuditView[];
  total: number;
  page: number;
  page_size: number;
}

export type EntitlementMode = 'until' | 'permanent';

// ---------- 统一错误 ----------

/**
 * 管理员权益操作的归一化错误。
 *  - retryable=true 仅用于「未知结果」（网络/超时）——写操作可用同幂等键安全重试。
 *  - 任何 HTTP 状态（含 4xx/5xx）都是「已知失败」，retryable=false。
 */
export class AdminApiError extends Error {
  readonly status?: number;
  readonly code?: string;
  readonly retryable: boolean;

  constructor(
    message: string,
    opts: { status?: number; code?: string; retryable?: boolean } = {},
  ) {
    super(message);
    this.name = 'AdminApiError';
    this.status = opts.status;
    this.code = opts.code;
    this.retryable = opts.retryable ?? false;
  }
}

/** 把 api() 抛出的错误（带 status/code 或 retryable 标记）归一为 AdminApiError。 */
export function classifyError(err: unknown): AdminApiError {
  if (err instanceof AdminApiError) return err;
  const e = err as { message?: unknown; status?: unknown; code?: unknown; retryable?: unknown };
  const status = typeof e?.status === 'number' ? e.status : undefined;
  const code = typeof e?.code === 'string' ? e.code : undefined;
  // 已有 HTTP 状态 → 已知失败不可重试；否则仅当明确标记网络/超时 retryable。
  const retryable = status === undefined && e?.retryable === true;
  return new AdminApiError(
    typeof e?.message === 'string' ? e.message : '操作失败',
    { status, code, retryable },
  );
}

// ---------- 请求体构建纯函数 ----------

/** 去重 + 升序排序（稳定锁序与稳定指纹的前提）。 */
export function normalizeStrategyIds(ids: string[]): string[] {
  return Array.from(new Set(ids)).sort();
}

/** 生成一次性幂等键（前缀 + UUID，长度/字符满足后端 8~128 可打印 ASCII）。
 *  B3-5 会"冻结"某次操作的键以便超时后安全重试；此函数只负责产键。 */
export function newIdempotencyKey(): string {
  return `idem-${globalThis.crypto.randomUUID()}`;
}

/** 归一到 UTC ISO（带毫秒与 Z）；非法日期抛 AdminApiError。 */
export function toUtcIso(d: Date | string): string {
  const date = d instanceof Date ? d : new Date(d);
  if (Number.isNaN(date.getTime())) {
    throw new AdminApiError('invalid date', { code: 'entitlements.invalid_expiry' });
  }
  return date.toISOString();
}

/** 仅保留选中策略的版本键；任一缺失抛 versions_mismatch（不发无效请求）。 */
function pickVersions(ids: string[], versions: Record<string, number>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const id of ids) {
    if (!(id in versions)) {
      throw new AdminApiError('versions 与 strategy_ids 不一致', {
        code: 'entitlements.versions_mismatch',
      });
    }
    out[id] = versions[id];
  }
  return out;
}

export interface MembershipInput {
  mode: EntitlementMode;
  validUntil?: Date | string | null;
  reason: string;
  version: number;
}

export function buildMembershipBody(input: MembershipInput): Record<string, unknown> {
  const body: Record<string, unknown> = {
    mode: input.mode,
    reason: input.reason,
    version: input.version,
  };
  if (input.mode === 'until') {
    if (input.validUntil == null) {
      throw new AdminApiError('until 模式需提供到期时间', {
        code: 'entitlements.invalid_expiry',
      });
    }
    body.valid_until = toUtcIso(input.validUntil);
  }
  return body;
}

export interface SetSubscriptionsInput {
  strategyIds: string[];
  mode: EntitlementMode;
  validUntil?: Date | string | null;
  reason: string;
  versions: Record<string, number>;
}

export function buildSetSubscriptionsBody(
  input: SetSubscriptionsInput,
): Record<string, unknown> {
  const ids = normalizeStrategyIds(input.strategyIds);
  const versions = pickVersions(ids, input.versions);
  const body: Record<string, unknown> = {
    strategy_ids: ids,
    mode: input.mode,
    reason: input.reason,
    versions,
  };
  if (input.mode === 'until') {
    if (input.validUntil == null) {
      throw new AdminApiError('until 模式需提供到期时间', {
        code: 'entitlements.invalid_expiry',
      });
    }
    body.valid_until = toUtcIso(input.validUntil);
  }
  return body;
}

export interface RevokeSubscriptionsInput {
  strategyIds: string[];
  reason: string;
  versions: Record<string, number>;
}

export function buildRevokeSubscriptionsBody(
  input: RevokeSubscriptionsInput,
): Record<string, unknown> {
  const ids = normalizeStrategyIds(input.strategyIds);
  const versions = pickVersions(ids, input.versions);
  return { strategy_ids: ids, reason: input.reason, versions };
}
