import { useEffect, useMemo, useState } from 'react';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { apiClient } from './api/client';
import type { DashboardCard, DashboardLevel, EvidenceReference, HealthState, KnowledgeUnit } from './types';
import EvidenceChain from './components/EvidenceChain';
import './styles/global.css';

type PageKey = 'overview' | 'links' | 'creators' | 'knowledge' | 'timeline' | 'strategies' | 'jobs';

const navigation: Array<{ key: PageKey; label: string; icon: string }> = [
  { key: 'overview', label: '总览', icon: '▦' },
  { key: 'links', label: '链接收件箱', icon: '↳' },
  { key: 'creators', label: '博主与兴趣', icon: '◎' },
  { key: 'knowledge', label: '知识单元', icon: '◇' },
  { key: 'timeline', label: '时间线', icon: '≋' },
  { key: 'strategies', label: '策略证据', icon: '◈' },
  { key: 'jobs', label: '任务运维', icon: '⚙' },
];

const fallbackCards: DashboardCard[] = [
  { key: 'pending', label: '待处理链接', value: '—', delta: null, level: 'info', as_of: new Date().toISOString(), description: '等待采集与解析' },
  { key: 'processing', label: '处理中', value: '—', delta: null, level: 'info', as_of: new Date().toISOString(), description: 'ASR / OCR 处理中' },
  { key: 'review', label: '待审核知识', value: '—', delta: null, level: 'warning', as_of: new Date().toISOString(), description: '候选与未审核单元' },
  { key: 'evidence', label: '证据完整率', value: '—', delta: null, level: 'normal', as_of: new Date().toISOString(), description: '已发布单元有效证据占比' },
  { key: 'index', label: '索引延迟', value: '—', delta: null, level: 'warning', as_of: new Date().toISOString(), description: '等待派生索引写入' },
  { key: 'dead', label: '死信 / 挂起', value: '—', delta: null, level: 'critical', as_of: new Date().toISOString(), description: '需要人工处置' },
];

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 15_000 } } });

function statusText(state: HealthState): string {
  return { healthy: '正常', degraded: '降级', unavailable: '不可用', unknown: '未知' }[state];
}

function AppShell(): JSX.Element {
  const [activePage, setActivePage] = useState<PageKey>('overview');
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());
  const [query, setQuery] = useState<string>('');
  const dashboard = useQuery({ queryKey: ['dashboard'], queryFn: () => apiClient.getDashboard(), refetchInterval: 30_000 });
  const cards = dashboard.data?.cards ?? fallbackCards;
  const health = dashboard.data?.health ?? { api: 'unknown' as HealthState, pg: 'unknown' as HealthState, qdrant: 'unknown' as HealthState, worker: 'unknown' as HealthState };
  const asOf = dashboard.data?.as_of ?? lastRefresh.toISOString();
  const pageLabel = useMemo(() => navigation.find((item) => item.key === activePage)?.label ?? '总览', [activePage]);

  useEffect(() => {
    const timer = window.setInterval(() => setLastRefresh(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const refresh = async (): Promise<void> => {
    setLastRefresh(new Date());
    await dashboard.refetch();
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">OS</span><div><strong>知识资源</strong><small>管理平台 V1</small></div></div>
        <div className="workspace-label">工作台</div>
        <nav aria-label="主导航">
          {navigation.map((item) => <button key={item.key} className={`nav-item ${activePage === item.key ? 'active' : ''}`} onClick={() => setActivePage(item.key)}><span className="nav-icon">{item.icon}</span>{item.label}{item.key === 'jobs' && <span className="nav-dot" />}</button>)}
        </nav>
        <div className="sidebar-footer"><div className="operator-avatar">OP</div><div><strong>运营人员</strong><small>单租户工作区</small></div><span className="more">•••</span></div>
      </aside>
      <main className="main-content">
        <header className="topbar"><div className="breadcrumbs"><span>管理平台</span><b>/</b><strong>{pageLabel}</strong></div><div className="top-actions"><label className="search"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索链接、知识、博主..." /></label><span className="utc">数据时间 UTC {new Date(asOf).toLocaleTimeString('zh-CN', { hour12: false })}</span><button className="refresh-button" onClick={() => void refresh()} aria-label="刷新">↻</button></div></header>
        <section className="health-strip"><span className="health-caption">系统状态</span>{(['api', 'pg', 'qdrant', 'worker'] as const).map((key) => <span className="health-item" key={key}><i className={`health-dot ${health[key]}`} />{key.toUpperCase()} <b>{statusText(health[key])}</b></span>)}<span className="health-time">上次刷新 {lastRefresh.toLocaleTimeString('zh-CN', { hour12: false })}</span></section>
        <div className="content-wrap"><div className="page-heading"><div><div className="eyebrow">PIPELINE CONTROL ROOM</div><h1>{pageLabel}</h1><p>以 PostgreSQL 为事实源的知识资源处理管线</p></div><div className="heading-meta"><span className="live-indicator"><i />短轮询已启用</span><span className="asof">AS OF {new Date(asOf).toLocaleString('zh-CN', { hour12: false })}</span></div></div>
          {activePage === 'overview' ? <Dashboard cards={cards} alerts={dashboard.data?.alerts ?? []} isLoading={dashboard.isLoading} /> : activePage === 'knowledge' ? <KnowledgePage search={query} /> : <OperationsPage page={activePage} label={pageLabel} search={query} />}
        </div>
      </main>
    </div>
  );
}

function Dashboard({ cards, alerts, isLoading }: { cards: DashboardCard[]; alerts: Array<{ id: string; title: string; detail: string; level: DashboardLevel; action?: string }>; isLoading: boolean }): JSX.Element {
  const [selectedCard, setSelectedCard] = useState<DashboardCard | null>(null);
  return <><div className="metric-grid">{cards.map((card) => <button className={`metric-card level-${card.level} metric-card-button`} key={card.key} onClick={() => setSelectedCard(card)}><div className="metric-top"><span>{card.label}</span><span className="metric-arrow">↗</span></div><div className="metric-value">{isLoading ? <span className="skeleton" /> : card.value}</div><div className="metric-foot"><span>{card.description}</span>{card.delta !== null && card.delta !== undefined && <b className="delta">{card.delta}</b>}</div><small>更新于 {new Date(card.as_of).toLocaleTimeString('zh-CN', { hour12: false })}</small></button>)}</div>{selectedCard && <DrilldownPanel card={selectedCard} onClose={() => setSelectedCard(null)} />}<div className="dashboard-grid"><section className="panel recent-panel"><div className="panel-heading"><div><h2>最近链接</h2><span>最新进入处理管线的任务</span></div><button>查看全部 <b>→</b></button></div><div className="empty-state"><span className="empty-icon">↳</span><strong>等待 API 数据</strong><p>链接收件箱数据将在接入后显示</p></div></section><section className="panel alert-panel"><div className="panel-heading"><div><h2>告警与动作</h2><span>需要运营关注的系统事件</span></div><span className="alert-count">{alerts.length}</span></div>{alerts.length === 0 ? <div className="empty-state compact"><span className="empty-icon">✓</span><strong>暂无待处理告警</strong><p>系统运行平稳</p></div> : <div className="alert-list">{alerts.map((alert) => <div className="alert-row" key={alert.id}><i className={`alert-dot ${alert.level}`} /><div><strong>{alert.title}</strong><p>{alert.detail}</p></div>{alert.action && <button>{alert.action}</button>}</div>)}</div>}</section></div><section className="panel trace-panel"><div className="panel-heading"><div><h2>溯源摘要</h2><span>博主兴趣信号与策略证据变更</span></div><button>进入溯源中心 <b>→</b></button></div><div className="trace-columns"><div className="trace-item"><span className="trace-icon blue">◎</span><div><strong>热门博主 / 兴趣信号</strong><p>按频次 × 时间衰减 × Evidence 可靠性排序</p></div><em>待接入</em></div><div className="trace-item"><span className="trace-icon purple">◈</span><div><strong>策略证据变更</strong><p>支持 / 反对关系与冲突项变更记录</p></div><em>待接入</em></div></div></section></>;
}

function DrilldownPanel({ card, onClose }: { card: DashboardCard; onClose: () => void }): JSX.Element {
  const stage = card.key === 'pending_links' ? 'collection' : card.key === 'processing' ? 'processing' : card.key === 'index_lag' ? 'index' : undefined;
  const state = card.key === 'dead_letters' ? 'dead' : undefined;
  const [actionMessage, setActionMessage] = useState('');
  const details = useQuery({ queryKey: ['drilldown', card.key], queryFn: () => apiClient.listDrilldown({ stage, state }), retry: false });
  const items = details.data?.items ?? [];
  const retry = async (id: string): Promise<void> => {
    setActionMessage('正在重新入队…');
    try { await apiClient.retryLink(id, 'dashboard_manual_retry', `dashboard-${id}-${Date.now()}`); setActionMessage(`任务 ${id} 已重新入队`); await details.refetch(); } catch (error) { setActionMessage(error instanceof Error ? error.message : '重试失败'); }
  };
  const reason = card.key === 'pending_links' ? '任务尚未进入下一处理阶段，通常需要等待采集 Worker 认领。' : card.key === 'processing' ? '任务已进入处理层，检查当前阶段和最近错误。' : card.key === 'index_lag' ? '知识已写入 PostgreSQL，但 Qdrant / KG 派生索引尚未完成。' : card.key === 'dead_letters' ? '任务超过重试上限或进入挂起状态，需要人工处理。' : card.description || '点击记录查看当前状态、阶段和错误原因。';
  return <section className="panel drilldown-panel"><div className="panel-heading"><div><h2>{card.label} · 明细</h2><span>当前数量：{card.value} · 事实源：PostgreSQL</span></div><div className="drilldown-actions"><span>{actionMessage}</span><button onClick={onClose}>收起 ×</button></div></div><div className="drilldown-summary"><strong>为什么是这个状态？</strong><p>{reason}</p>{details.isError && <p className="dialog-error">明细接口暂不可用：请确认后端和数据库连接。</p>}</div>{items.length === 0 ? <div className="empty-state compact"><span className="empty-icon">✓</span><strong>{details.isLoading ? '正在读取明细…' : '当前没有可展示的明细记录'}</strong><p>数量为 0 或暂无对应任务</p></div> : <div className="drilldown-list">{items.map((item) => <div className="drilldown-row" key={`${item.stage}-${item.id}`}><div><strong>{item.id}</strong><span>{item.stage} · 尝试 {item.attempt ?? 0} 次</span></div><span className={`status-pill ${item.state}`}>{item.state}</span><div className="drilldown-error">{item.error_code && <b>{item.error_code}</b>}{item.error || '暂无错误，等待后续处理'}</div><small>{item.updated_at || item.finished_at || item.started_at || '时间未知'}</small>{['failed', 'dead', 'quarantine', 'waiting_cookie'].includes(item.state) && item.stage === 'collection' && <button className="row-retry" onClick={() => void retry(item.id)}>重试</button>}</div>)}</div>}</section>;
}

function KnowledgePage({ search }: { search: string }): JSX.Element {
  const [selected, setSelected] = useState<KnowledgeUnit | null>(null);
  const [detail, setDetail] = useState<{ unit: KnowledgeUnit; evidence: EvidenceReference[] } | null>(null);
  const units = useQuery({ queryKey: ['knowledge', search], queryFn: () => apiClient.listKnowledge({ q: search || undefined, size: 30 }), retry: false });
  const openUnit = async (unit: KnowledgeUnit): Promise<void> => {
    setSelected(unit);
    try {
      // Detail routes are keyed by the stable unit_key, not the database UUID.
      const detailKey = unit.unit_key;
      const selectedVersion = unit.unit_version ?? unit.version;
      if (!detailKey) {
        setDetail({ unit, evidence: unit.evidence_refs ?? [] });
        return;
      }
      const data = await apiClient.getKnowledgeUnit(detailKey, selectedVersion);
      const current = data.versions?.find((version) => (version.unit_version ?? version.version) === selectedVersion) ?? unit;
      setDetail({ unit: { ...unit, ...current }, evidence: data.evidence ?? data.evidence_refs ?? [] });
    } catch {
      // A missing backend is a supported offline state; retain summary data.
      setDetail({ unit, evidence: unit.evidence_refs ?? [] });
    }
  };
  const list = units.data?.items ?? [];
  return <section className="knowledge-layout"><div className="panel knowledge-list"><div className="panel-heading"><div><h2>知识单元审核</h2><span>{units.isFetching ? '正在同步…' : `${list.length} 个单元 · PG 事实源`}</span></div><span className="panel-kicker">版本 / Evidence</span></div>{units.isError && <div className="inline-notice">API 暂不可用，显示本地空态；不会伪造数据</div>}{list.length === 0 ? <div className="empty-state"><span className="empty-icon">◇</span><strong>暂无知识单元</strong><p>接入 API 后，这里会显示版本与证据完整率</p></div> : <div className="unit-list">{list.map((unit) => <button className={`unit-row ${selected?.unit_id === unit.unit_id ? 'selected' : ''}`} key={unit.unit_id} onClick={() => void openUnit(unit)}><div><strong>{unit.title}</strong><p>{unit.unit_type} · {unit.creator_name || '未标注博主'}</p></div><div className="unit-row-meta"><span className={`status-pill ${unit.status}`}>{unit.status}</span><b>v{unit.unit_version ?? unit.version}</b><small>{unit.evidence_count} Evidence</small></div></button>)}</div>}</div>{detail && <aside className="panel unit-detail"><div className="panel-heading"><div><h2>{detail.unit.title}</h2><span>{detail.unit.unit_id} · v{detail.unit.unit_version ?? detail.unit.version}</span></div><button onClick={() => { setDetail(null); setSelected(null); }}>关闭 ×</button></div><div className="unit-summary"><div><span>正文摘要</span><p>{detail.unit.content || '暂无正文摘要'}</p></div><div className="summary-grid"><span>状态 <b>{detail.unit.status}</b></span><span>质量 <b>{detail.unit.quality === null || detail.unit.quality === undefined ? '—' : `${Math.round(detail.unit.quality * 100)}%`}</b></span><span>索引 <b>{detail.unit.index_status || 'unknown'}</b></span><span>更新时间 <b>{detail.unit.pg_updated_at ? new Date(detail.unit.pg_updated_at).toLocaleString('zh-CN') : '—'}</b></span></div></div><div className="detail-section"><div className="detail-section-head"><h3>EvidenceChain</h3><span>{detail.evidence.length} 条引用</span></div><EvidenceChain items={detail.evidence} onBind={(evidence) => window.alert(`策略引用入口：${evidence.evidence_id}`)} /></div></aside>}</section>;
}

const demoRows: Record<PageKey, Array<{ title: string; meta: string; state: string; tone: string }>> = {
  overview: [],
  links: [
    { title: 'https://weixin.qq.com/sph/A3YRTDEm9Y', meta: '猎人X的笔记 · 2026-10-14 09:20', state: '已解析', tone: 'ok' },
    { title: '待接收新的 sph 链接', meta: '粘贴链接后自动识别博主并入队', state: '监听中', tone: 'info' },
  ],
  creators: [
    { title: '猎人X的笔记', meta: 'wx_channels · 财经 / 大盘 / 股票', state: '观察级', tone: 'warn' },
    { title: '新增博主将从链接自动发现', meta: '链接价值信号 → 历史整理任务', state: '自动加入', tone: 'info' },
  ],
  knowledge: [],
  timeline: [
    { title: '周线必备四句口诀，稳抓主升', meta: '猎人X的笔记 · 2026-10-14', state: '已入库', tone: 'ok' },
    { title: 'EvidenceChain 快照已生成', meta: 'ASR · 20–35s · version 1', state: '可追溯', tone: 'ok' },
  ],
  strategies: [
    { title: '周线主升识别', meta: 'supports · Evidence 1 条', state: '支持', tone: 'up' },
    { title: '策略冲突检查', meta: 'contradicts / context 等待绑定', state: '待审', tone: 'warn' },
  ],
  jobs: [
    { title: 'LinkTask / 采集与解析', meta: 'D route · worker-01', state: '运行中', tone: 'info' },
    { title: 'Qdrant / KG 派生索引', meta: 'PG 事实已保留，等待重试', state: '降级', tone: 'warn' },
  ],
};

function OperationsPage({ page, label, search }: { page: PageKey; label: string; search: string }): JSX.Element {
  const [showLinkDialog, setShowLinkDialog] = useState(false);
  const [linkText, setLinkText] = useState('');
  const [submitState, setSubmitState] = useState<'idle' | 'submitting' | 'success' | 'error'>('idle');
  const [submitMessage, setSubmitMessage] = useState('');
  const rows = demoRows[page].filter((row) => !search || `${row.title} ${row.meta}`.toLowerCase().includes(search.toLowerCase()));
  const isLinksPage = page === 'links';
  const openDialog = (): void => {
    setSubmitState('idle');
    setSubmitMessage('');
    setShowLinkDialog(true);
  };
  const submitLinks = async (): Promise<void> => {
    const urls = linkText.split(/\\r?\\n|[,，]/).map((item) => item.trim()).filter(Boolean);
    if (urls.length === 0) {
      setSubmitState('error');
      setSubmitMessage('请先输入至少一个链接');
      return;
    }
    setSubmitState('submitting');
    setSubmitMessage('');
    try {
      const result = await apiClient.submitLinks({ urls, created_by: 'admin-ui', route: 'AUTO', priority: 5 });
      setSubmitState('success');
      setSubmitMessage(`已接收 ${result.accepted} 条，重复 ${result.duplicated} 条`);
      setLinkText('');
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
    } catch (error) {
      setSubmitState('error');
      setSubmitMessage(error instanceof Error ? error.message : '提交失败，请检查后端服务');
    }
  };
  const descriptions: Record<PageKey, string> = {
    overview: '', links: '投递一个公开 sph 链接，系统会解析作者、进入监控名单，并触发知识抽取。', creators: '管理由价值链接滚雪球发现的博主、主题和监控等级。', knowledge: '', timeline: '按博主、来源通道和时间段回看知识沉淀与证据变更。', strategies: '将 Evidence 绑定到策略，保留 supports / contradicts / context 关系。', jobs: '查看采集、抽取、知识化和派生索引任务的运行状态。',
  };
  return <><section className="operations-layout"><div className="panel operations-main"><div className="panel-heading"><div><h2>{label}</h2><span>{descriptions[page]}</span></div><button className="primary-action" onClick={isLinksPage ? openDialog : undefined}>{page === 'links' ? '+ 投递链接' : page === 'creators' ? '+ 添加目标' : '刷新数据'} <b>→</b></button></div><div className="ops-summary"><div><small>当前视图</small><strong>{rows.length || '—'}</strong><span>条记录</span></div><div><small>数据源</small><strong>PG</strong><span>事实库</span></div><div><small>状态</small><strong className="accent-text">LIVE</strong><span>短轮询</span></div></div>{rows.length === 0 ? <div className="empty-state"><span className="empty-icon">◇</span><strong>{search ? '没有匹配结果' : `${label}暂无更多数据`}</strong><p>{search ? '请调整搜索条件' : '真实数据接入后将在这里展示，当前不伪造业务记录'}</p></div> : <div className="ops-table">{rows.map((row) => <div className="ops-row" key={row.title}><div className="ops-glyph">{page === 'links' ? '↳' : page === 'creators' ? '◎' : page === 'strategies' ? '◈' : page === 'jobs' ? '⚙' : '◇'}</div><div className="ops-copy"><strong>{row.title}</strong><span>{row.meta}</span></div><span className={`status-pill ${row.tone}`}>{row.state}</span><button className="row-arrow">→</button></div>)}</div>}</div><aside className="panel operations-side"><div className="panel-heading"><div><h2>工作流提示</h2><span>平台当前的关键闭环</span></div></div><div className="flow-steps"><div><b>01</b><span>链接进入收件箱</span></div><i>↓</i><div><b>02</b><span>建立博主档案</span></div><i>↓</i><div><b>03</b><span>历史回溯与结构化</span></div><i>↓</i><div><b>04</b><span>生成博主总结</span></div><i>↓</i><div><b>05</b><span>每日扫描增量更新</span></div></div></aside></section>{isLinksPage && showLinkDialog && <div className="dialog-backdrop" role="presentation" onClick={() => setShowLinkDialog(false)}><div className="link-dialog" role="dialog" aria-modal="true" aria-labelledby="link-dialog-title" onClick={(event) => event.stopPropagation()}><div className="dialog-heading"><div><h2 id="link-dialog-title">投递视频链接</h2><p>每行一个链接，也支持逗号分隔。系统将自动识别博主并进入处理队列。</p></div><button className="dialog-close" onClick={() => setShowLinkDialog(false)} aria-label="关闭">×</button></div><textarea value={linkText} onChange={(event) => setLinkText(event.target.value)} placeholder="https://weixin.qq.com/sph/..." rows={6} autoFocus /><div className="dialog-foot"><span className={submitState === 'error' ? 'dialog-error' : submitState === 'success' ? 'dialog-success' : ''}>{submitMessage}</span><div><button className="dialog-secondary" onClick={() => setShowLinkDialog(false)}>取消</button><button className="dialog-primary" onClick={() => void submitLinks()} disabled={submitState === 'submitting'}>{submitState === 'submitting' ? '提交中…' : '确认投递'}</button></div></div></div></div>}</>;
}

export default function App(): JSX.Element { return <QueryClientProvider client={queryClient}><AppShell /></QueryClientProvider>; }
