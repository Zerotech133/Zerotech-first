import { api } from './api';

/** 手动选股（受控）请求。市场枚举与后端 SelectionRunRequest 一致。 */
export interface SelectionRunRequest {
  market: 'CN' | 'HK' | 'US' | 'CRYPTO';
  trade_date?: string;
  strategy_type?: string;
  max_candidates?: number;
  idempotency_key?: string;
}

/** POST /api/v1/selection/run 的响应（后端 _response 结构；失败时 detail 内也带同名字段）。 */
export interface SelectionRunResult {
  run_id: string;
  status: string;
  source?: string;
  operator?: string;
  operator_id?: string;
  role?: string;
  market?: string;
  trade_date?: string;
  strategy_type?: string;
  max_candidates?: number;
  idempotency_key?: string | null;
  reason?: string | null;
  started_at?: string;
  finished_at?: string;
  duration_sec?: number;
  result_summary?: Record<string, unknown> | null;
  error?: string;
  replay?: boolean;
}

/**
 * 触发一轮受控手动选股。
 *
 * 后端契约（selection_run.py）：
 * - 默认关闭：未设置 SELECTION_RUN_ENABLED=1 时返回 503 `selection_run.disabled`；
 * - 仅 ADMIN/TRADER（前端隐藏入口，后端 require_roles 权威拦截）；
 * - 交易日校验（400 `selection.non_trading_day`）、并发锁（409 `selection.run_in_progress`）、
 *   幂等键（Idempotency-Key 头或 body.idempotency_key，两者取 body 优先）。
 * 网络错误沿用 api() 的 retryable 语义；HTTP 错误由 api() 提示后抛出（附 status/code）。
 */
export function runSelection(
  req: SelectionRunRequest,
  idempotencyKey?: string,
): Promise<SelectionRunResult> {
  const body: Record<string, unknown> = {
    market: req.market,
    strategy_type: req.strategy_type ?? 'TREND',
    max_candidates: req.max_candidates ?? 10,
  };
  if (req.trade_date) body.trade_date = req.trade_date;
  if (req.idempotency_key) body.idempotency_key = req.idempotency_key;
  return api<SelectionRunResult>('/selection/run', {
    method: 'POST',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
    body: JSON.stringify(body),
  });
}
