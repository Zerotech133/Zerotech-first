/**
 * 策略中台（qengine_sync）只读数据类型与展示 helper。
 *
 * 数据由后端 /api/v1/strategy-center 提供；本模块不再 import 静态 JSON。
 * 所有 helper 只做格式化，不做推荐/排序/批准。
 */
export interface StrategyCenterMeta {
  title: string;
  subtitle: string;
  asof: string;
  generated_at: string | null;
  source_commit: string | null;
  source_dirty: boolean | null;
  sync_id: string | null;
  database: string;
  schema: string;
  note: string;
  latest_generated_at: string | null;
}

export interface StrategyCenterCounts {
  strategy: number;
  pool_strategy: number;
  day: number;
  pick: number;
  trade: number;
  pool_candidate: number;
  issue: number;
  pending: number;
  latest_picks: number;
}

export interface StrategyCenterPick {
  signal_date: string;
  sid: string;
  ts_code: string;
  name: string;
  industry?: string | null;
  rank_in?: number | null;
  gap_pct?: number | null;
  stop_px?: number | null;
  ref_px?: number | null;
  score?: number | null;
  vetoed?: number | null;
  veto_why?: string | null;
  [key: string]: unknown;
}

export interface StrategyCenterTopPick {
  ts_code: string;
  name: string;
  rank_in?: number | null;
  gap_pct?: number | null;
  stop_px?: number | null;
  ref_px?: number | null;
  score?: number | null;
  [key: string]: unknown;
}

export interface StrategyCenterStrategy {
  sid: string;
  name: string;
  system?: string | null;
  ekind?: string | null;
  tiers?: string | null;
  can_hold?: number | null;
  doc?: string | null;
  status?: string | null;
  status_label?: string | null;
  level?: string | null;
  n_total?: number | null;
  n_closed?: number | null;
  n_open?: number | null;
  n_days?: number | null;
  win?: number | null;
  avg?: number | null;
  med?: number | null;
  pl?: number | null;
  worst?: number | null;
  best?: number | null;
  exc?: number | null;
  exc_t?: number | null;
  stp?: number | null;
  hold?: number | null;
  first_sig?: string | null;
  last_sig?: string | null;
  n_issues?: number | null;
  enabled?: boolean | number | null;
  enabled_from?: string | null;
  enabled_why?: string | null;
  grade?: string | null;
  grade_why?: string | null;
  in_pool?: boolean;
  latest_pick_count?: number;
  latest_top_pick?: StrategyCenterTopPick | null;
  [key: string]: unknown;
}

export interface StrategyCenterDay {
  signal_date: string;
  entry_date?: string | null;
  n_signal: number;
  n_buy: number;
  n_strategies: number;
  gate_ok?: number | boolean | null;
  ret?: number | null;
  excess?: number | null;
  [key: string]: unknown;
}

export interface StrategyCenterNav {
  date: string;
  nav: number;
  ret_pct?: number | null;
  [key: string]: unknown;
}

export interface StrategyCenterIssue {
  issue_id: string | number;
  scope?: string | null;
  code?: string | null;
  level?: string | null;
  sev?: number | string | null;
  title: string;
  ev?: string | null;
  act?: string | null;
  first_date?: string | null;
  last_date?: string | null;
  hits?: number | null;
  status?: string | null;
  [key: string]: unknown;
}

export interface StrategyCenterDetailPick extends StrategyCenterPick {
  entry_px?: number | null;
  shares?: number | null;
  cost?: number | null;
  status?: string | null;
  exit_date?: string | null;
  exit_px?: number | null;
  exit_reason?: string | null;
  hold_days?: number | null;
  ret_net_pct?: number | null;
  ret_pct?: number | null;
  excess?: number | null;
}

export interface StrategyCenterTrade {
  track?: string | null;
  signal_date: string;
  entry_date?: string | null;
  sid: string;
  ts_code: string;
  name: string;
  status?: string | null;
  exit_date?: string | null;
  exit_px?: number | null;
  ret_net_pct?: number | null;
  exit_reason?: string | null;
  hold_days?: number | null;
  [key: string]: unknown;
}

export interface StrategyCenterStrategyDay {
  signal_date: string;
  n: number;
  closed?: number | null;
  open?: number | null;
  win?: number | null;
  avg?: number | null;
  exc?: number | null;
  level?: string | null;
  verdict?: string | null;
  [key: string]: unknown;
}

export interface StrategyCenterDetail {
  sid: string;
  strategy: StrategyCenterStrategy;
  days: StrategyCenterStrategyDay[];
  picks: StrategyCenterDetailPick[];
  trades: StrategyCenterTrade[];
  extremes: { best: StrategyCenterDetailPick | null; worst: StrategyCenterDetailPick | null };
  issues: StrategyCenterIssue[];
}

export interface StrategyCenterData {
  meta: StrategyCenterMeta;
  counts: StrategyCenterCounts;
  grade_counts: Record<string, number>;
  lx_count: number;
  nav: StrategyCenterNav[];
  latest_nav: StrategyCenterNav | null;
  days: StrategyCenterDay[];
  latest_day: StrategyCenterDay;
  pool: Array<{ sid: string; name: string }>;
  pool_day?: Record<string, unknown> | null;
  strategies: StrategyCenterStrategy[];
  latest_picks: StrategyCenterPick[];
  issues: StrategyCenterIssue[];
  detail?: StrategyCenterDetail | null;
}

export function fmtDate(value: string | null | undefined): string {
  if (!value) return '—';
  const s = String(value);
  if (/^\d{8}$/.test(s)) return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
  return s;
}

export function fmtNumber(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  return Number(value).toLocaleString('zh-CN');
}

export function fmtPct(value: number | null | undefined, suffix = '%'): string {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}${suffix}`;
}

export function fmtRatio(value: number | null | undefined, digits = 4): string {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  return Number(value).toFixed(digits);
}

export function signedClass(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return '';
  return Number(value) > 0 ? 'up' : Number(value) < 0 ? 'down' : '';
}

export function gradeOf(s: StrategyCenterStrategy): string {
  return s.grade || '未评级';
}

export function gradeClass(grade: string | null | undefined): string {
  if (grade === 'A') return 'sc-grade-a';
  if (grade === 'B') return 'sc-grade-b';
  if (grade === 'C') return 'sc-grade-c';
  if (grade === 'D') return 'sc-grade-d';
  return 'sc-grade-na';
}

export function strategyStatus(s: StrategyCenterStrategy): { label: string; tone: string } {
  if (!s.in_pool) return { label: '池外', tone: 'muted' };
  if (!s.enabled) return { label: '已停用', tone: 'danger' };
  return { label: '在池', tone: 'ok' };
}

/** 最新候选按“每个策略先取一条，再补足”展示，避免首页被单一策略占满。 */
export function diverseLatestPicks(
  data: StrategyCenterData,
  limit = 8,
): StrategyCenterPick[] {
  const picks = data.latest_picks || [];
  const buckets = new Map<string, StrategyCenterPick[]>();
  for (const p of picks) {
    const arr = buckets.get(p.sid) || [];
    arr.push(p);
    buckets.set(p.sid, arr);
  }
  const out: StrategyCenterPick[] = [];
  const sids = [...buckets.keys()];
  let idx = 0;
  while (out.length < limit) {
    let added = false;
    for (const sid of sids) {
      const p = buckets.get(sid)?.[idx];
      if (p) {
        out.push(p);
        added = true;
        if (out.length >= limit) break;
      }
    }
    if (!added) break;
    idx += 1;
  }
  return out;
}
