import { api } from './api';

export interface PlatformPosition {
  position_id: string; ts_code: string; market: string; direction: string;
  open_price: number | null; close_price: number | null;
  open_qty: number | null; close_qty: number | null; pnl_amount: number | null;
  status: string; account_id: string | null; opened_at: string | null; closed_at: string | null;
}
export interface PlatformTrade {
  fill_id: string; market: string; ts_code: string;
  fill_price: number | null; fill_qty: number | null; fill_amount: number | null;
  realized_pnl: number | null; fill_time: string | null; signal: 'entry' | 'exit';
}

const enc = (v: string) => encodeURIComponent(v);

export function getSubscriptionPositions(strategyId: string, signal?: AbortSignal): Promise<PlatformPosition[]> {
  return api<PlatformPosition[]>(`/my/subscriptions/${enc(strategyId)}/positions`, { signal });
}
export function getSubscriptionTrades(strategyId: string, signal?: AbortSignal): Promise<PlatformTrade[]> {
  return api<PlatformTrade[]>(`/my/subscriptions/${enc(strategyId)}/trades`, { signal });
}
