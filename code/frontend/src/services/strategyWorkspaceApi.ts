/** 策略工作台只读 API。
 * 后端路由已并入 main（8102 旧进程未重启期间，本地回退到 8104 测试实例）。
 * 生产/重启完成后可设 VITE_STRATEGY_WS_BASE=/api/v1。
 */
const PRIMARY = import.meta.env.VITE_STRATEGY_WS_BASE || import.meta.env.VITE_API_BASE || '/api/v1';

function headers(): Record<string, string> {
  const token = localStorage.getItem('zhice_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function get<T>(path: string): Promise<T> {
  const resp = await fetch(PRIMARY + path, { headers: headers() });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(PRIMARY + path, {
    method: 'POST',
    headers: { ...headers(), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json() as Promise<T>;
}

export interface Sidecar {
  strategy_id: string;
  display_name: string;
  status: string;
  owner: string;
  ops_support: string;
  source_type: string;
  health: string;
  data_date: string | null;
  note: string;
  source_kind?: string;
}

export interface PoolRow {
  pool_date: string; strategy_id: string; code: string; name: string; market: string;
  signal: string; score: number | null; selection_reason: string; selected_by: string;
  status: string; data_date: string | null;
}

export interface ReviewRow {
  review_date: string; topic: string; strategy_id: string; verdict: string;
  summary: string; evidence: string; reviewer: string;
}

export interface DailyRow {
  market: string; trade_date: string; data_date: string | null; code: string;
  name: string; signal: string; score: number | null; reason: string;
}

export const strategyWorkspaceApi = {
  list: () => get<{ items: Sidecar[] }>('/strategy-workspace/list'),
  profile: (id: string) => get<Sidecar>(`/strategy-workspace/${id}/profile`),
  daily: (id: string, market?: string) => get<{ strategy_id: string; trade_date: string | null; rows: DailyRow[] }>(`/strategy-workspace/${id}/daily` + (market ? `?market=${encodeURIComponent(market)}` : '')),
  observations: (id: string) => get<{ strategy_id: string; rows: any[] }>(`/strategy-workspace/${id}/observations`),
  backtest: (id: string) => get<{ strategy_id: string; rows: any[] }>(`/strategy-workspace/${id}/backtest`),
  acad1Pool: () => get<{ pool_date: string | null; rows: PoolRow[] }>('/acad1-research/pool'),
  acad1Reviews: () => get<{ rows: ReviewRow[] }>('/acad1-research/reviews'),
  acad1Stats: () => get<{ pool_total: number; obs_total: number; obs_done: number; bt_total: number; review_total: number; pool_date: string | null }>('/acad1-research/stats'),
  acad1Decisions: () => get<{ rows: any[] }>('/acad1-research/decisions'),
  acad1Calendar: () => get<{ rows: any[] }>('/acad1-research/calendar'),
  lusuQueue: () => get<{ pool_date: string | null; rows: any[] }>('/lusu-research/queue'),
  lusuDecide: (body: unknown) => post('/lusu-research/decision', body),
  stockResearchList: () => get<{ rows: any[] }>('/stock-research'),
  stockResearchSave: (body: unknown) => post('/stock-research/save', body),
  stockResearchReview: (body: unknown) => post('/stock-research/review', body),
  stockResearchHandoff: (body: unknown) => post('/stock-research/handoff', body),
  // P2-2：日报按市场独立，切市场 tab 时按 market 查询（后端不再返回 MULTI 跨市场日报）。
  selectionDesk: (market?: string) => get<{ date: string | null; pool_date?: string | null; market?: string | null; reports: any[]; pool: any[]; feedback: any[]; supervision?: any[]; pool_detail?: any[]; coverage?: any[] }>('/selection-desk' + (market ? `?market=${encodeURIComponent(market)}` : '')),
  selectionFeedback: (body: unknown) => post('/selection-desk/feedback', body),
  selectionSupervision: (body: unknown) => post('/selection-desk/supervision', body),
  simAccounts: () => get<{ rows: any[] }>('/sim-factor/accounts'),
  simPortfolio: (id: string) => get<any>('/sim-factor/portfolio?strategy_id=' + encodeURIComponent(id)),
  simOrder: (body: unknown) => post('/sim-factor/order', body),
  simAutoBuild: (body: unknown) => post('/sim-factor/auto-build', body),
  factors: () => get<{ rows: any[] }>('/sim-factor/factors'),
  createFactor: (body: unknown) => post('/sim-factor/factor', body),
  factorConsensus: (body: unknown) => post('/sim-factor/factor/consensus', body),
  factorPaper: (body: unknown) => post('/sim-factor/factor/paper', body),
};
