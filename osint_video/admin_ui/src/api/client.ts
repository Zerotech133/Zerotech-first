import type {
  ApiEnvelope,
  ChangesResponse,
  CursorPage,
  DashboardSummary,
  EvidenceChain,
  Job,
  JobDetail,
  KnowledgeUnit,
  KnowledgeUnitDetail,
  StrategyEvidenceBinding,
  LinkRoute,
  LinkSubmitRequest,
  LinkSubmitResponse,
  LinkTask,
  IngestTask,
  IngestSubmitResponse,
} from '../types';

/** Error returned by the management API. */
export class ApiError extends Error {
  public readonly code: number;
  public readonly status: number;

  public constructor(message: string, code: number, status: number) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
  }
}

const API_BASE = '/api/v1';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...(init?.headers ?? {}),
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : '网络连接失败';
    throw new ApiError(message, -1, 0);
  }

  let envelope: ApiEnvelope<T>;
  try {
    envelope = (await response.json()) as ApiEnvelope<T>;
  } catch {
    throw new ApiError('服务返回了无效数据', -2, response.status);
  }

  if (!response.ok || envelope.code !== 0) {
    throw new ApiError(envelope.message || '请求失败', envelope.code, response.status);
  }
  return envelope.data;
}

function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') search.set(key, String(value));
  });
  const result = search.toString();
  return result ? `?${result}` : '';
}

/** Typed management API client. Every method unwraps {code,data,message}. */
export const apiClient = {
  getDashboard(window: '24h' | '7d' = '24h'): Promise<DashboardSummary> {
    return request<DashboardSummary>(`/dashboard/summary${query({ window })}`);
  },
  getChanges(cursor: string | null, scope: 'dashboard' | 'links' | 'knowledge' | 'jobs'): Promise<ChangesResponse> {
    return request<ChangesResponse>(`/changes${query({ cursor, scope })}`);
  },
  listLinks(params: { status?: string; route?: LinkRoute; cursor?: string; size?: number } = {}): Promise<CursorPage<LinkTask>> {
    return request<CursorPage<LinkTask>>(`/links${query(params)}`);
  },
  submitLinks(payload: LinkSubmitRequest): Promise<LinkSubmitResponse> {
    return request<LinkSubmitResponse>('/links', { method: 'POST', body: JSON.stringify(payload) });
  },
  ingestLinks(urls: string[]): Promise<IngestSubmitResponse> {
    return request<IngestSubmitResponse>('/ingest/links', { method: 'POST', body: JSON.stringify({ urls }) });
  },
  ingestFiles(files: File[]): Promise<IngestSubmitResponse> {
    const body = new FormData();
    files.forEach((file) => body.append('files', file));
    return request<IngestSubmitResponse>('/ingest/files', { method: 'POST', body });
  },
  listIngestTasks(): Promise<CursorPage<IngestTask>> {
    return request<CursorPage<IngestTask>>('/ingest/tasks');
  },
  retryLink(taskId: string, reason: string, idempotencyKey: string): Promise<LinkTask> {
    return request<LinkTask>(`/links/${encodeURIComponent(taskId)}/retry`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ reason }),
    });
  },
  listKnowledge(params: { q?: string; status?: string; cursor?: string; size?: number } = {}): Promise<CursorPage<KnowledgeUnit>> {
    return request<CursorPage<KnowledgeUnit>>(`/knowledge/units${query(params)}`);
  },
  getKnowledgeUnit(unitKey: string, version?: number): Promise<KnowledgeUnitDetail> {
    return request<KnowledgeUnitDetail>(`/knowledge/units/${encodeURIComponent(unitKey)}${query({ version })}`);
  },
  getEvidenceChain(strategyId: string, version?: number): Promise<EvidenceChain> {
    return request<EvidenceChain>(`/strategies/${encodeURIComponent(strategyId)}/evidence-chain${query({ version })}`);
  },
  bindStrategyEvidence(strategyId: string, payload: StrategyEvidenceBinding): Promise<unknown> {
    return request<unknown>(`/strategies/${encodeURIComponent(strategyId)}/evidence`, { method: 'POST', body: JSON.stringify(payload) });
  },
  unbindStrategyEvidence(strategyId: string, evidenceId: string): Promise<unknown> {
    return request<unknown>(`/strategies/${encodeURIComponent(strategyId)}/evidence/${encodeURIComponent(evidenceId)}`, { method: 'DELETE' });
  },
  listJobs(params: { stage?: string; state?: string; cursor?: string; size?: number } = {}): Promise<CursorPage<Job>> {
    return request<CursorPage<Job>>(`/jobs${query(params)}`);
  },
  listDrilldown(params: { stage?: string; state?: string; size?: number } = {}): Promise<CursorPage<JobDetail>> {
    return request<CursorPage<JobDetail>>(`/jobs${query({ ...params, size: params.size ?? 100 })}`);
  },
  getLink(taskId: string): Promise<LinkTask> {
    return request<LinkTask>(`/links/${encodeURIComponent(taskId)}`);
  },
  healthz(): Promise<DashboardSummary['health']> {
    return request<DashboardSummary['health']>('/healthz');
  },
};

export type ApiClient = typeof apiClient;
