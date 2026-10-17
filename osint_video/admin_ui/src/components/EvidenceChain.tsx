import { useMemo, useState } from 'react';
import type { EvidenceReference } from '../types';

interface EvidenceChainProps {
  items: EvidenceReference[];
  onBind?: (evidence: EvidenceReference) => void;
}

function formatTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—';
  const whole = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(whole / 60);
  const secs = whole % 60;
  return `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function formatRange(item: EvidenceReference): string {
  return `${formatTime(item.start_sec)} – ${formatTime(item.end_sec)}`;
}

function displayStatus(item: EvidenceReference): string {
  if (typeof item.validity === 'object' && item.validity !== null) return item.validity.status;
  return item.validity ?? item.evidence_status ?? item.status ?? 'unknown';
}

function isBindable(item: EvidenceReference, status: string): boolean {
  if (typeof item.validity === 'object' && item.validity !== null) {
    return item.validity.is_valid === true && status === 'valid';
  }
  const fallbackStatus = item.evidence_status ?? item.status;
  return status === 'valid' && fallbackStatus === 'valid';
}

function shortHash(value: string | null | undefined): string {
  if (!value) return '—';
  return value.length > 20 ? `${value.slice(0, 10)}…${value.slice(-8)}` : value;
}

function reliability(item: EvidenceReference): string {
  const value = item.reliability ?? item.quality;
  return value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`;
}

/** Dense, sanitized evidence trail for a knowledge unit or strategy. */
export function EvidenceChain({ items, onBind }: EvidenceChainProps): JSX.Element {
  const [copied, setCopied] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const ordered = useMemo(() => [...items].sort((a, b) => (a.start_sec ?? 0) - (b.start_sec ?? 0)), [items]);

  const copy = async (value: string, key: string): Promise<void> => {
    if (!value || !navigator.clipboard) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(key);
      window.setTimeout(() => setCopied((current) => current === key ? null : current), 1300);
    } catch { setCopied(null); }
  };

  if (ordered.length === 0) {
    return <div className="evidence-empty"><span className="empty-icon">◇</span><strong>暂无 Evidence</strong><p>发布前至少需要一条有效证据</p></div>;
  }

  return <div className="evidence-chain">{ordered.map((item) => {
    const status = displayStatus(item);
    const relation = item.relation ?? item.evidence_role ?? 'context';
    const hash = item.snapshot_hash ?? item.quote_hash;
    const isExpanded = expanded === item.evidence_id;
    const warning = item.warning ?? (!item.quote ? '缺少引用文本' : undefined);
    return <article className={`evidence-card status-${status}`} key={item.evidence_id}>
      <div className="evidence-card-head"><div className="evidence-title"><span className={`relation relation-${relation}`}>{relation}</span><strong>{item.source_channel || 'unknown'}</strong><code>{item.evidence_id}</code></div><span className={`evidence-status ${status}`}>{status}</span></div>
      <div className="evidence-meta"><button className="timecode" onClick={() => void copy(formatRange(item), `${item.evidence_id}-time`)} title="复制时间码">◷ {formatRange(item)} {copied === `${item.evidence_id}-time` ? '✓' : '⧉'}</button><span>可靠性 <b>{reliability(item)}</b></span>{item.model_version && <span>模型 <b>{item.model_version}</b></span>}</div>
      <blockquote className={isExpanded ? 'expanded' : ''}>{item.quote || '未提供引用文本'}</blockquote>
      {warning && <div className="evidence-warning">! {warning}</div>}
      <div className="evidence-card-foot"><span className="hash-label">snapshot_hash <code title={hash ?? undefined}>{shortHash(hash)}</code></span>{hash && <button className="copy-button" onClick={() => void copy(hash, `${item.evidence_id}-hash`)}>{copied === `${item.evidence_id}-hash` ? '已复制' : '复制 hash'}</button>}<button className="detail-button" onClick={() => setExpanded(isExpanded ? null : item.evidence_id)}>{isExpanded ? '收起' : '详情'}</button>{onBind && isBindable(item, status) && <button className="bind-button" onClick={() => onBind(item)}>引用到策略</button>}</div>
    </article>;
  })}</div>;
}

export default EvidenceChain;
