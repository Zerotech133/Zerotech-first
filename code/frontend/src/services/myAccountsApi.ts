/**
 * owner-scoped 账户客户端：列表/创建（/api/v1/my/accounts）。
 *
 * 铁律：复用 api<T>()（token/JSON 头/401 跳登录/{code} 错误翻译），绝不直接
 * fetch；请求体绝不携带 user_id（owner 恒由后端从 JWT 取）。DTO 字段逐一对齐
 * 后端 my_accounts.py _serialize（金额 Decimal→string；创建请求金额是 number）。
 *
 * 运行实例（/my/strategy-instances）属策略域，见 myStrategiesApi。
 */
import { api } from './api';

/** 我的账户（后端 my_accounts.py _serialize）。 */
export interface MyAccount {
  account_id: string;
  user_id: string;
  market: string;
  currency: string;
  account_type: string; // SPOT / FUTURES ...
  initial_capital: string; // Decimal→str
  cash_balance: string; // Decimal→str
  reserved_cash: string; // Decimal→str
  status: string; // ACTIVE / FROZEN / CLOSED
}

export interface CreateMyAccountBody {
  market: string;
  account_type: string;
  initial_capital: number; // 请求体为 JSON number（后端 pydantic float）
}

export function listMyAccounts(market?: string): Promise<MyAccount[]> {
  const qs = market ? `?market=${encodeURIComponent(market)}` : '';
  return api<MyAccount[]>(`/my/accounts${qs}`);
}

export function createMyAccount(body: CreateMyAccountBody): Promise<MyAccount> {
  return api<MyAccount>('/my/accounts', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}
