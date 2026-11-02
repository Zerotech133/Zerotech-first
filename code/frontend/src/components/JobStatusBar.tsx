import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Space, Tag, Typography } from 'antd';
import { authHeaders } from '../services/api';

/**
 * 每日任务运行态状态条（P4-3，只读）。
 *
 * 数据源：GET /api/v1/system/job-status（后端只读读取 C:\Temp\zhice_job_status.json）；
 *         partial_failure 时再 GET /api/v1/system/daily-alert 展示尾部失败告警摘要。
 * 展示：运行中/成功/失败/部分失败/未上报、数据日、计划时间、完成时间、来源（定时/手动）。
 * 只读：仅 GET（轮询 60s + 手动刷新），不写 DB、不触发任务、不改导航。
 */

const { Text } = Typography;
const BASE = import.meta.env.VITE_API_BASE || '/api/v1';
const POLL_MS = 60_000;

export interface JobStatusPayload {
  status?: string;
  scheduled_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  source?: string | null;
  data_date?: string | null;
  rc?: number | null;
  steps?: unknown;
  message?: string | null;
  error?: string | null;
  job?: string | null;
  [key: string]: unknown;
}

export interface DailyAlertFailure {
  name?: string;
  rc?: number | null;
  stderr?: string | null;
  stderr_bytes?: number | null;
  stderr_truncated?: boolean | null;
  [key: string]: unknown;
}

export interface DailyAlertPayload {
  event?: string;
  severity?: string;
  generated_at?: string | null;
  run_id?: string | null;
  source?: string | null;
  scheduled_at?: string | null;
  data_date?: string | null;
  status?: string;
  rc?: number | null;
  message?: string | null;
  job_status_path?: string | null;
  failures?: DailyAlertFailure[];
  [key: string]: unknown;
}

type LoadState =
  | { kind: 'loading'; fetchedAt: string }
  | { kind: 'ok'; data: JobStatusPayload; fetchedAt: string }
  | { kind: 'unreported'; fetchedAt: string }
  | { kind: 'unavailable'; fetchedAt: string; reason: string };

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  running: { label: '运行中', color: 'processing' },
  success: { label: '成功', color: 'success' },
  failed: { label: '失败', color: 'error' },
  partial_failure: { label: '部分失败', color: 'warning' },
};

const SOURCE_LABELS: Record<string, string> = {
  schedule: '定时',
  scheduled: '定时',
  cron: '定时',
  timer: '定时',
  manual: '手动',
};

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

function nowLabel(): string {
  const d = new Date();
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** 时间统一压成 `YYYY-MM-DD HH:mm`（ISO/带 T/带秒都兼容）；空值显示 —。 */
export function formatJobTime(value: unknown): string {
  if (value == null || value === '') return '—';
  const s = String(value).trim();
  const m = s.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : s;
}

/** 兼容直返对象 / {job:{...}} / {data:{...}} 三种响应包法。 */
export function normalizeJobStatus(raw: unknown): JobStatusPayload | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const obj = raw as Record<string, unknown>;
  for (const key of ['job', 'data', 'status_detail', 'payload']) {
    const nested = obj[key];
    if (nested && typeof nested === 'object' && !Array.isArray(nested)) {
      const candidate = nested as Record<string, unknown>;
      if (typeof candidate.status === 'string') return candidate as JobStatusPayload;
    }
  }
  return typeof obj.status === 'string' ? (obj as JobStatusPayload) : null;
}

/** 兼容直返对象 / {alert:{...}} 两种包法；无 failures 视为无告警。 */
export function normalizeDailyAlert(raw: unknown): DailyAlertPayload | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const outer = raw as Record<string, unknown>;
  const nested = outer.alert;
  const obj = nested && typeof nested === 'object' && !Array.isArray(nested)
    ? (nested as Record<string, unknown>)
    : outer;
  if (!Array.isArray(obj.failures) || obj.failures.length === 0) return null;
  return obj as DailyAlertPayload;
}

function alertColor(severity: unknown): string {
  const s = String(severity ?? '').toUpperCase();
  if (s === 'P0' || s === 'P1') return 'error';
  if (s === 'P2') return 'warning';
  return 'orange';
}

function alertTooltip(alert: DailyAlertPayload): string {
  const lines: string[] = [];
  if (alert.message) lines.push(String(alert.message));
  for (const f of alert.failures ?? []) {
    lines.push(`${f.name ?? '?'} rc=${f.rc ?? '?'}${f.stderr ? `: ${String(f.stderr).split('\n')[0]}` : ''}`);
  }
  return lines.join('\n');
}

function sourceLabel(source: unknown): string {
  if (source == null || source === '') return '—';
  const key = String(source).toLowerCase();
  return SOURCE_LABELS[key] || String(source);
}

function fieldValueText(value: unknown): string {
  if (value == null || value === '') return '—';
  return String(value);
}

export default function JobStatusBar() {
  const [state, setState] = useState<LoadState>({ kind: 'loading', fetchedAt: nowLabel() });
  const [alert, setAlert] = useState<DailyAlertPayload | null>(null);
  const alive = useRef(true);

  const loadAlert = useCallback(async () => {
    try {
      const resp = await fetch(`${BASE}/system/daily-alert`, {
        method: 'GET',
        headers: authHeaders(),
        cache: 'no-store',
      });
      if (!alive.current) return;
      if (resp.status === 204 || !resp.ok) {
        setAlert(null);
        return;
      }
      const raw: unknown = await resp.json().catch(() => null);
      setAlert(normalizeDailyAlert(raw));
    } catch {
      if (alive.current) setAlert(null);
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const resp = await fetch(`${BASE}/system/job-status`, {
        method: 'GET',
        headers: authHeaders(),
        cache: 'no-store',
      });
      if (!alive.current) return;
      const fetchedAt = nowLabel();
      // 404/503 = 任务尚未上报（后端文件不存在时按约定返回）
      if (resp.status === 404 || resp.status === 503) {
        setState({ kind: 'unreported', fetchedAt });
        setAlert(null);
        return;
      }
      if (!resp.ok) {
        setState({ kind: 'unavailable', fetchedAt, reason: `HTTP ${resp.status}` });
        setAlert(null);
        return;
      }
      const raw: unknown = await resp.json().catch(() => null);
      const data = normalizeJobStatus(raw);
      if (!data) {
        setState({ kind: 'unavailable', fetchedAt, reason: '响应结构不合法' });
        setAlert(null);
        return;
      }
      setState({ kind: 'ok', data, fetchedAt });
      // 仅在 partial_failure 时拉取告警摘要，避免 success 时额外请求/旧告警残留
      if (String(data.status ?? '').toLowerCase() === 'partial_failure') {
        void loadAlert();
      } else {
        setAlert(null);
      }
    } catch (e) {
      if (!alive.current) return;
      setState({
        kind: 'unavailable',
        fetchedAt: nowLabel(),
        reason: e instanceof Error ? e.message : '网络错误',
      });
      setAlert(null);
    }
  }, [loadAlert]);

  useEffect(() => {
    alive.current = true;
    void load();
    const timer = setInterval(() => { void load(); }, POLL_MS);
    return () => {
      alive.current = false;
      clearInterval(timer);
    };
  }, [load]);

  const data = state.kind === 'ok' ? state.data : null;
  const statusKey = (data?.status || '').toLowerCase();
  const statusMeta = STATUS_MAP[statusKey];
  const statusLabel = state.kind === 'unreported' ? '未上报'
    : state.kind === 'loading' ? '读取中'
      : state.kind === 'unavailable' ? '不可用'
        : (statusMeta ? statusMeta.label : (data?.status ? String(data.status) : '未知'));
  const statusColor = state.kind === 'unreported' || state.kind === 'unavailable' || state.kind === 'loading'
    ? 'default'
    : (statusMeta ? statusMeta.color : 'default');

  return (
    <div
      role="status"
      aria-live="polite"
      title="每日任务运行态（只读：仅 GET /system/job-status /system/daily-alert，不写库）"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        flexWrap: 'wrap',
        padding: '7px 12px',
        marginBottom: 14,
        border: '1px solid var(--line)',
        borderRadius: 8,
        background: 'var(--card)',
        fontSize: 12,
        lineHeight: 1.5,
      }}
    >
      <Text strong style={{ fontSize: 12 }}>运行态</Text>
      <Tag color={statusColor} style={{ marginInlineEnd: 0 }}>{statusLabel}</Tag>

      {alert && (
        <span
          title={alertTooltip(alert)}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
        >
          <Tag color={alertColor(alert.severity)} style={{ marginInlineEnd: 0 }}>
            告警 {String(alert.severity ?? 'P1')}
          </Tag>
          <Text type="secondary">
            尾部失败 {(alert.failures ?? []).length} 项
            {(alert.failures ?? [])[0]?.name ? `：${(alert.failures ?? [])[0]?.name}` : ''}
            {(alert.failures ?? [])[0]?.rc != null ? ` rc=${(alert.failures ?? [])[0]?.rc}` : ''}
          </Text>
        </span>
      )}
      {statusKey === 'partial_failure' && !alert && (
        <Text type="secondary">部分失败（告警详情未上报）</Text>
      )}

      {state.kind === 'loading' && <Text type="secondary">读取中…</Text>}
      {state.kind === 'unreported' && (
        <Text type="secondary">任务尚未上报（等待每日任务首次运行；本页只读，不触发任务）</Text>
      )}
      {state.kind === 'unavailable' && (
        <Text type="secondary">状态不可用：{state.reason}</Text>
      )}

      {data && (
        <Space size={14} wrap>
          <span>
            <Text type="secondary">数据日 </Text>
            <Text className="mono">{formatJobTime(data.data_date)}</Text>
          </span>
          <span>
            <Text type="secondary">计划 </Text>
            <Text className="mono">{formatJobTime(data.scheduled_at)}</Text>
          </span>
          <span>
            <Text type="secondary">完成 </Text>
            <Text className="mono">{formatJobTime(data.finished_at)}</Text>
          </span>
          {data.started_at != null && (
            <span>
              <Text type="secondary">开始 </Text>
              <Text className="mono">{formatJobTime(data.started_at)}</Text>
            </span>
          )}
          <span>
            <Text type="secondary">来源 </Text>
            <Tag color="blue" style={{ marginInlineEnd: 0 }}>{sourceLabel(data.source)}</Tag>
          </span>
          {statusKey === 'failed' && data.rc != null && (
            <span>
              <Text type="secondary">rc </Text>
              <Text className="mono">{fieldValueText(data.rc)}</Text>
            </span>
          )}
          {state.kind === 'ok' && data.message != null && data.message !== '' && (
            <Text type="secondary" ellipsis={{ tooltip: String(data.message) }} style={{ maxWidth: 360 }}>
              {String(data.message)}
            </Text>
          )}
        </Space>
      )}

      <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        <Text type="secondary" className="mono" style={{ fontSize: 11 }}>更新 {state.fetchedAt}</Text>
        <Button size="small" type="text" onClick={() => { void load(); }}>刷新</Button>
      </span>
    </div>
  );
}
