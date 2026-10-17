/** API envelope shared by every management endpoint. */
export interface ApiEnvelope<T> {
  code: number;
  data: T;
  message: string;
}

/** Cursor metadata attached to all incremental/list responses. */
export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
  as_of: string;
}

/** Incremental refresh response. */
export interface ChangesResponse {
  cursor: string;
  changed_ids: string[];
  as_of: string;
}

/** Fixed health enum from management_platform_contract_v1. */
export type HealthState = 'healthy' | 'degraded' | 'unavailable' | 'unknown';
export type DashboardLevel = 'normal' | 'warning' | 'critical' | 'info';

export interface DashboardCard {
  key: string;
  label: string;
  value: number | string;
  delta?: number | string | null;
  level: DashboardLevel;
  as_of: string;
  drilldown?: string;
  description?: string;
}

export interface HealthStatus {
  api: HealthState;
  pg: HealthState;
  qdrant: HealthState;
  worker: HealthState;
  as_of?: string;
}

export interface AlertItem {
  id: string;
  title: string;
  detail: string;
  level: DashboardLevel;
  action?: string;
  created_at: string;
}

export interface DashboardSummary {
  cards: DashboardCard[];
  health: HealthStatus;
  alerts: AlertItem[];
  as_of: string;
}

export interface JobDetail {
  id: string;
  stage: string;
  state: string;
  attempt?: number;
  error_code?: string | null;
  error?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  updated_at?: string | null;
}

export type LinkTaskState =
  | 'pending'
  | 'parsed'
  | 'downloaded'
  | 'decrypted'
  | 'extracted'
  | 'done'
  | 'failed'
  | 'dead'
  | 'waiting_cookie'
  | 'quarantine';

export type LinkRoute = 'D' | 'B' | 'AUTO' | 'A';

export interface LinkTask {
  /** Public LinkTaskPublic DTO. Database id/status/retry_count are mapped server-side. */
  task_id: string;
  url_hash: string;
  state: LinkTaskState | string;
  route: LinkRoute;
  priority: number;
  attempt: number;
  error_code?: string | null;
  next_retry_at?: string | null;
  creator_name?: string | null;
  video_id?: string | null;
  created_at: string;
  updated_at: string;
  stage_duration_ms?: number | null;
}

export interface LinkSubmitRequest {
  urls: string[];
  created_by: string;
  route: LinkRoute;
  priority: number;
  client_request_id?: string;
}

export interface LinkSubmitResponse {
  accepted: number;
  duplicated: number;
  tasks: LinkTask[];
}

export type KnowledgeUnitStatus = 'candidate' | 'unreviewed' | 'reviewed' | 'published' | 'superseded' | 'unknown';
export type EvidenceChannel = 'asr' | 'ocr' | 'subtitle' | 'manual' | string;
export type EvidenceStatus = 'valid' | 'expired' | 'superseded' | 'draft' | 'unknown';
export type EvidenceRelation = 'supports' | 'contradicts' | 'context' | string;

/** Backend validity assessment for an Evidence snapshot. */
export interface EvidenceValidity {
  status: EvidenceStatus;
  is_valid: boolean;
  time_valid?: boolean;
  version_match?: boolean;
}

export interface KnowledgeUnit {
  unit_id: string;
  unit_key: string;
  title: string;
  content?: string | null;
  unit_type: string;
  status: KnowledgeUnitStatus;
  review_status?: KnowledgeUnitStatus | null;
  creator_name?: string | null;
  confidence?: number | null;
  /** Derived client field; backend may omit until evidence joins are enabled. */
  evidence_count?: number;
  quality?: number | null;
  source_time?: string | null;
  source_channel?: EvidenceChannel | null;
  start_sec?: number | null;
  end_sec?: number | null;
  version?: number;
  unit_version?: number;
  supersedes?: string | null;
  pg_updated_at?: string;
  created_at?: string;
  updated_at?: string;
  index_status?: 'ready' | 'pending' | 'failed' | 'unknown';
  index_error?: string | null;
  evidence_refs?: EvidenceReference[];
}

/** Sanitized evidence DTO. Sensitive collector fields are intentionally absent. */
export interface EvidenceReference {
  evidence_id: string;
  evidence_key?: string | null;
  relation?: EvidenceRelation;
  evidence_role?: EvidenceRelation;
  source_channel: EvidenceChannel;
  source_ref?: string | null;
  source_url?: string | null;
  start_sec?: number | null;
  end_sec?: number | null;
  quote?: string | null;
  quote_hash?: string | null;
  reliability?: number | null;
  quality?: number | null;
  model_version?: string | null;
  snapshot_hash?: string | null;
  status: EvidenceStatus;
  evidence_status?: EvidenceStatus;
  validity?: EvidenceValidity | EvidenceStatus;
  warning?: string | null;
}

export interface KnowledgeUnitDetail extends Partial<KnowledgeUnit> {
  unit_key: string;
  current_version?: number | null;
  versions?: KnowledgeUnit[];
  evidence?: EvidenceReference[];
  evidence_refs?: EvidenceReference[];
  superseded_by?: string | null;
  as_of?: string;
}

export interface EvidenceChain {
  strategy_id?: string | null;
  strategy_key?: string | null;
  strategy_version?: number | null;
  unit_id?: string | null;
  version?: number | null;
  items?: EvidenceReference[];
  evidence?: EvidenceReference[];
  conflicts?: EvidenceReference[];
  counts?: Record<string, number>;
  quality_warnings?: string[];
  as_of?: string;
}

export interface StrategyEvidenceBinding {
  evidence_id: string;
  relation: EvidenceRelation;
  rationale?: string;
  weight?: number;
  expected_version?: number;
}

export interface Strategy {
  strategy_id: string;
  name: string;
  version: number;
  status: 'draft' | 'active' | 'archived';
  supports_count: number;
  contradicts_count: number;
  context_count: number;
  updated_at: string;
}

export interface Job {
  /** Public job DTO; backend id is exposed as job_id by the API adapter. */
  job_id: string;
  stage: 'collection' | 'processing' | 'index' | string;
  state: string;
  attempt: number;
  error_code?: string | null;
  error?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  run_id?: string | null;
}

export interface Creator {
  creator_id: string;
  nickname: string;
  finder_id?: string | null;
  enabled: boolean;
  monitor_level: 'low' | 'normal' | 'high';
  topics: string[];
}

export interface TimelineEvent {
  event_id: string;
  event_type: string;
  title: string;
  occurred_at: string;
  target_id?: string | null;
  unit_id?: string | null;
}
