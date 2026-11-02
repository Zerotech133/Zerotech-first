/** ACAD1 证据包只读 API（P3-8 / P3-7）。
 *
 * 后端：GET /api/v1/acad1-research/evidence（只读读取 C:\Temp\acad1_evidence_bundle.json；
 * 文件不存在返回 404 = 「证据包未生成」）。
 * 本服务只做 GET：不写 DB、不改研究卡、不产生通过/否决动作。
 */
import { api } from './api';

/** 证据缺口（kind 原样透传，缺口如实标注，不推断、不补造）。 */
export interface Acad1EvidenceGap {
  field: string;
  field_label?: string;
  kind: string;
  kind_label?: string;
  reason?: string | null;
  source?: string;
  true_missing?: boolean;
  sources?: string[];
}

/** 研究卡快照字段（上游机器事实草稿，原样引用，不解读）。 */
export interface Acad1CardFields {
  id?: number;
  pool_date?: string;
  strategy_id?: string;
  code?: string;
  name?: string;
  direction?: string;
  score?: number | null;
  rating?: string;
  thesis?: string;
  catalysts?: string;
  risks?: string;
  executable?: string;
  exec_note?: string;
  invalidation?: string;
  status?: string;
  reviewer?: string;
  review_note?: string;
  data_date?: string;
  updated_at?: string;
}

export interface Acad1EvidenceCard {
  matched: boolean;
  matched_by?: string;
  source?: string;
  completeness?: string;
  snapshot_card_pool_date?: string | null;
  fields?: Acad1CardFields;
  drift_from_preview?: Record<string, unknown>;
}

/** 行业/公告/新闻的通用只读字段：covered / not_applicable / no_match / missing … */
export interface Acad1EvidenceField {
  status?: string;
  display?: string | null;
  reason?: string | null;
  pit_lag_days?: number | null;
  value?: Record<string, unknown>;
  window?: Record<string, unknown> | null;
  history_check?: Record<string, unknown>;
}

export interface Acad1NewsLatest {
  time?: string;
  title?: string;
  source?: string;
}

export interface Acad1NewsPerSource {
  available?: boolean;
  count?: number;
  latest?: Acad1NewsLatest | null;
}

export interface Acad1EvidenceNews extends Acad1EvidenceField {
  per_source?: Record<string, Acad1NewsPerSource>;
  match_method?: string;
  caveat?: string;
}

/** KB 方法引用（P2-6 只读 enrichment；仅作方法参考，非个股结论）。 */
export interface Acad1MethodRef {
  title?: string;
  video_id?: string;
  source?: string;
  snippet?: string;
  score?: number;
  topic?: string;
  topic_label?: string;
  lang?: string;
  query?: string;
}

export interface Acad1EvidenceItem {
  seq: string;
  market: string;
  code: string;
  pool_code?: string;
  dw_code?: string;
  name: string;
  strategy_id?: string;
  strategy_name?: string;
  signal?: string;
  score?: number | null;
  pool?: Record<string, unknown>;
  card?: Acad1EvidenceCard;
  industry?: Acad1EvidenceField;
  announcements?: Acad1EvidenceField;
  news?: Acad1EvidenceNews;
  method_refs?: Acad1MethodRef[];
  method_coverage?: string;
  kb_refs_generated_at?: string;
  /** 证据完整度：full / none（沿用 preview 口径：CN 适用行业+公告+新闻，非 CN 仅适用新闻）。 */
  evidence_level?: string;
  evidence_level_source?: string;
  evidence_level_applicable_fields?: string[];
  gaps?: Acad1EvidenceGap[];
  gap_count?: number;
  no_conclusion?: boolean;
  sources?: Record<string, unknown>;
}

export interface Acad1EvidenceSummary {
  pool?: {
    source?: string;
    pool_date?: string | null;
    rows_requested?: number;
    bundle_items?: number;
    by_market?: Record<string, number>;
  };
  matched?: Record<string, number>;
  research_cards_snapshot?: Record<string, unknown>;
  evidence_level?: Record<string, number>;
  gaps?: {
    items_with_gaps?: number;
    total_entries?: number;
    by_kind?: Record<string, number>;
    by_kind_label?: Record<string, string>;
    by_field?: Record<string, number>;
    true_missing_entries?: number;
    legend?: Record<string, string>;
  };
  news?: Record<string, number>;
  industry?: Record<string, number>;
  announcements?: Record<string, number>;
  method_refs?: Record<string, unknown>;
  cross_source?: Record<string, unknown>;
  [key: string]: unknown;
}

/** 证据包响应：后端返回 schema/generated_at/pool_date/read_only/source/item_count/summary/items。 */
export interface Acad1EvidenceResponse {
  schema?: string;
  generated_at?: string;
  pool_date?: string | null;
  read_only?: boolean;
  source?: { path?: string; bytes?: number; read_only?: boolean };
  item_count?: number;
  pool?: string;
  summary?: Acad1EvidenceSummary;
  read_only_proof?: Record<string, unknown>;
  items: Acad1EvidenceItem[];
  notes?: string[];
  disclaimer?: string;
}

export const acad1ResearchApi = {
  /** 只读证据包。404 = 证据包未生成（页面按空态展示；不重试、不写库）。 */
  evidence: (signal?: AbortSignal) =>
    api<Acad1EvidenceResponse>('/acad1-research/evidence', { signal }),
};
