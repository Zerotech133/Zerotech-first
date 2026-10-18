/**
 * 计费/收银台客户端（P3）。复用 api<T>()（token/JSON/401/错误码翻译），
 * DTO 逐字段对齐后端 billing.py：金额一律 string（Decimal→str）；
 * 请求体绝不带 user_id（owner 恒由后端从 JWT 取）。
 */
import { api } from './api';
import { MARKETS } from '../contexts/MarketContext';
import { listCatalog, type CatalogStrategy } from './myStrategiesApi';

/** 价格表（后端 GET /billing/plans）。 */
export interface BillingPlans {
  currency: string;
  period: string;
  membership_monthly_usdt: number;
  strategy_default_monthly_usdt: number;
  strategy_overrides: Record<string, number>;
}

/** 会员态（后端 GET /billing/membership）。 */
export interface MembershipView {
  tier: string; // free | member
  valid_until: string | null;
  is_member: boolean;
  is_permanent?: boolean; // B3-6：永久会员（valid_until 为 null 但有效）
  effective_status?: string; // B3-6：free | active | permanent | expired | revoked
}

/** 订阅项（后端 GET /billing/subscriptions items[]）。 */
export interface SubscriptionView {
  strategy_id: string;
  valid_until: string | null;
  active: boolean;
  is_permanent?: boolean; // B3-6
  effective_status?: string; // B3-6
}

/** 支付订单（后端 payment_service._order_view / create_order）。 */
export interface PaymentOrderView {
  order_id: string;
  product_type: string; // membership | subscription
  product_ref: string | null;
  months: number;
  amount_usdt: string;
  trade_type: string;
  status: string; // pending | paid | expired | canceled
  pay_address: string | null;
  actual_amount: string | null;
  expired_at?: string | null;
  paid_at?: string | null;
  created_at?: string | null;
  grant_status?: string; // C-6：pending|processing|retrying|granted|failed|manual_review
  grant_pending?: boolean; // C-6：已支付且发放尚未定局
}

/** 下单收银台回执（后端 create_order）。 */
export interface CheckoutView extends PaymentOrderView {
  payment_url: string | null;
  expiration_time: number | null; // unix 秒
  trade_id: string | null;
}

export interface CreateOrderBody {
  product_type: 'membership' | 'subscription';
  product_ref?: string | null;
  months: number;
}

export function getPlans(): Promise<BillingPlans> {
  return api<BillingPlans>('/billing/plans');
}

export function getMembership(): Promise<MembershipView> {
  return api<MembershipView>('/billing/membership');
}

export function listSubscriptions(): Promise<{ items: SubscriptionView[] }> {
  return api<{ items: SubscriptionView[] }>('/billing/subscriptions');
}

export function createOrder(body: CreateOrderBody): Promise<CheckoutView> {
  return api<CheckoutView>('/billing/orders', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function getOrder(orderId: string): Promise<PaymentOrderView> {
  return api<PaymentOrderView>(`/billing/orders/${encodeURIComponent(orderId)}`);
}

export function listOrders(): Promise<{ items: PaymentOrderView[] }> {
  return api<{ items: PaymentOrderView[] }>('/billing/orders');
}

/** 可订阅策略：平台策略（owner_user_id 为 null，即 PUBLIC 平台/内置策略，含 TREND
 *  与 VIBE_EXTERNAL 等所有类型），且仅启用市场（US/CRYPTO）。动态取目录 → 后期新增
 *  平台策略会自动出现在订阅页（无需改代码）。 */
export async function listBuiltinStrategies(): Promise<CatalogStrategy[]> {
  const all = await listCatalog();
  const enabled = MARKETS as readonly string[];
  return all.filter((s) => s.owner_user_id === null && enabled.includes(s.market));
}
