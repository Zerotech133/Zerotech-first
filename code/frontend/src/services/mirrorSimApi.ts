import { api } from './api';

export interface MirrorPosition {
  symbol: string; quantity: string; avg_cost: string;
  current_price: number | null; market_value: number | null; unrealized_pnl: number | null;
}
export interface MirrorFill {
  symbol: string; side: string; quantity: string; price: string; reason: string; created_at: string | null;
}
export interface MirrorAccountDetail {
  mirror_account_id: string; strategy_id: string; market: string;
  initial_capital: string; cash_balance: string; status: string; created_at: string | null;
  positions: MirrorPosition[]; equity: number; total_pnl: number; recent_fills: MirrorFill[];
}

const enc = (v: string) => encodeURIComponent(v);

export function createMirrorAccount(strategyId: string, initialCapital: number): Promise<MirrorAccountDetail> {
  return api<MirrorAccountDetail>('/my/mirror-accounts', {
    method: 'POST', body: JSON.stringify({ strategy_id: strategyId, initial_capital: initialCapital }),
  });
}
export function getMirrorAccount(strategyId: string, signal?: AbortSignal): Promise<MirrorAccountDetail> {
  return api<MirrorAccountDetail>(`/my/mirror-accounts/${enc(strategyId)}`, { signal });
}
