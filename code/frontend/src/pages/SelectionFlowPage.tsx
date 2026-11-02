import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Badge, Button, Card, Col, Descriptions, Divider, Drawer, Empty, Input, Modal, Progress,
  Row, Segmented, Space, Statistic, Table, Tag, Timeline, Typography,
} from 'antd';
import {
  DownloadOutlined, MessageOutlined, PlayCircleOutlined, ReloadOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { strategyWorkspaceApi } from '../services/strategyWorkspaceApi';
import { runSelection } from '../services/selectionRunApi';
import type { SelectionRunResult } from '../services/selectionRunApi';
import { useRole } from '../hooks/useRole';
import { zhReason, zhSignal, zhStatus } from '../utils/zhLabels';
import { useMarket } from '../contexts/MarketContext';

const { Text, Title } = Typography;

type AnyRow = Record<string, any>;
const MARKETS = ['ALL', 'CN', 'HK', 'US', 'CRYPTO'] as const;
type MarketKey = (typeof MARKETS)[number];

function getToken() {
  return localStorage.getItem('zhice_token') || '';
}

async function getJson(path: string): Promise<any> {
  const token = getToken();
  const resp = await fetch('/api/v1' + path, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

async function createHandoff(question: string, context: AnyRow, page: string): Promise<number> {
  const token = getToken();
  const resp = await fetch('/api/v1/agent-handoff', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ question, context, page }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  return data.id;
}

function hermesLink(prompt: string) {
  const origin = typeof window !== 'undefined'
    ? `${window.location.protocol}//${window.location.hostname}:8734`
    : 'http://127.0.0.1:8734';
  return `${origin}/hermes/?q=${encodeURIComponent(prompt)}`;
}

function marketColor(m: string) {
  return m === 'CN' ? 'red' : m === 'HK' ? 'gold' : m === 'US' ? 'blue' : 'purple';
}

function lifecycleColor(v: string) {
  if (v === 'active' || v === 'live') return 'green';
  if (v === 'shadow') return 'blue';
  if (v === 'research') return 'red';
  return 'default';
}

export default function SelectionFlowPage() {
  const navigate = useNavigate();
  const { market: currentMarket } = useMarket();
  const routeMarket = currentMarket.toLowerCase();
  const quickLinks = [
    { label: '智能体工作台', desc: 'Hermes 能力 / 待办 / 归档', path: '/agent' },
    { label: '去复盘', desc: '盘后复盘 / 盈亏日历', path: '/replay?tab=replay' },
    { label: '去回测', desc: '回测工具 / 样本外', path: '/strategies?tab=backtest' },
    { label: '看策略逻辑', desc: '策略工作台 / 运行逻辑', path: '/strategies?tab=workspace' },
    { label: '领域研究', desc: 'AI 研究 / 基本面', path: '/strategies?tab=ai' },
    { label: '去候选池', desc: '候选池溯源 / 全市场', path: '/candidates' },
  ];
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [job, setJob] = useState<AnyRow | null>(null);
  const [strategies, setStrategies] = useState<AnyRow[]>([]);
  const [deskByMarket, setDeskByMarket] = useState<Record<string, AnyRow>>({});
  const [pool, setPool] = useState<AnyRow[]>([]);
  const [stats, setStats] = useState<AnyRow | null>(null);
  const [overview, setOverview] = useState<AnyRow | null>(null);
  const [reviewQueue, setReviewQueue] = useState<AnyRow[]>([]);
  const [evidence, setEvidence] = useState<AnyRow | null>(null);
  const [market, setMarket] = useState<MarketKey>(currentMarket as MarketKey);
  const [stage, setStage] = useState('');
  const [kpi, setKpi] = useState<string | null>(null);
  const [seatDetail, setSeatDetail] = useState<AnyRow | null>(null);
  const [dataInventory, setDataInventory] = useState<AnyRow | null>(null);
  const [dataOpen, setDataOpen] = useState(false);
  const [strategySearch, setStrategySearch] = useState('');
  const [poolSearch, setPoolSearch] = useState('');
  const [strategyDetail, setStrategyDetail] = useState<AnyRow | null>(null);
  const [stockDetail, setStockDetail] = useState<AnyRow | null>(null);
  const [handoffOpen, setHandoffOpen] = useState(false);
  const [handoffBusy, setHandoffBusy] = useState(false);
  const [handoff, setHandoff] = useState<AnyRow | null>(null);
  const [handoffAnswer, setHandoffAnswer] = useState<AnyRow | null>(null);
  const [handoffId, setHandoffId] = useState<number | null>(null);
  // 手动跑一轮（受控，默认隐藏）：仅 ADMIN/TRADER 可见，后端 flag 关闭时隐藏入口。
  const { role } = useRole();
  const canManualRun = role === 'ADMIN' || role === 'TRADER';
  const [runOpen, setRunOpen] = useState(false);
  const [runBusy, setRunBusy] = useState(false);
  const [runResult, setRunResult] = useState<SelectionRunResult | null>(null);
  const [runError, setRunError] = useState('');
  const [runUnavailable, setRunUnavailable] = useState(false);
  const runMarket = market === 'ALL' ? currentMarket : market;

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [jobR, listR, poolR, statsR, queueR, evidenceR, overviewR, invR, ...desks] = await Promise.all([
        getJson('/hermes/job-status').catch(() => null),
        getJson('/hermes/strategy-list').catch(() => null),
        strategyWorkspaceApi.acad1Pool().catch(() => null),
        strategyWorkspaceApi.acad1Stats().catch(() => null),
        getJson('/hermes/review-queue').catch(() => null),
        getJson('/acad1-research/evidence').catch(() => null),
        getJson('/hermes/overview').catch(() => null),
        getJson('/hermes/data-inventory').catch(() => null),
        ...MARKETS.slice(1).map((m) => strategyWorkspaceApi.selectionDesk(m).catch(() => null)),
      ]);
      setJob(jobR?.job || null);
      setStrategies(listR?.items || []);
      setPool(poolR?.rows || []);
      setStats(statsR || null);
      setReviewQueue(queueR?.items || []);
      setEvidence(evidenceR || null);
      setOverview(overviewR);
      setDataInventory(invR);
      const map: Record<string, AnyRow> = {};
      MARKETS.slice(1).forEach((m, i) => { if (desks[i]) map[m] = desks[i] as AnyRow; });
      setDeskByMarket(map);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!handoffId) return;
    const timer = setInterval(async () => {
      try {
        const token = getToken();
        const resp = await fetch(`/api/v1/agent-handoff/${handoffId}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!resp.ok) return;
        const data = await resp.json();
        setHandoff(data);
        if (data.status === 'answered' || data.status === 'failed') {
          setHandoffAnswer(data);
          clearInterval(timer);
        }
      } catch { /* keep polling */ }
    }, 3000);
    return () => clearInterval(timer);
  }, [handoffId]);

  const currentDesk = deskByMarket[market] || {};
  const reports = useMemo(() => currentDesk.reports || [], [currentDesk]);
  const candidatePool = useMemo(() => currentDesk.pool || [], [currentDesk]);
  const feedback = useMemo(() => currentDesk.feedback || [], [currentDesk]);
  const supervision = useMemo(() => currentDesk.supervision || [], [currentDesk]);
  const evidenceItems = useMemo(() => (evidence?.items || []) as AnyRow[], [evidence]);
  const evidenceByCode = useMemo(() => {
    const m: Record<string, AnyRow> = {};
    evidenceItems.forEach((it) => {
      m[`${it.market}:${it.code}`] = it;
      m[`${it.market}:${it.pool_code}`] = it;
    });
    return m;
  }, [evidenceItems]);

  const dataDate = job?.data_date || stats?.pool_date || '—';
  const marketStrategies = useMemo(() => strategies.filter((s: AnyRow) => s.market === market), [strategies, market]);
  const marketPool = useMemo(() => pool.filter((r: AnyRow) => r.market === market), [pool, market]);
  const marketEvidence = useMemo(() => evidenceItems.filter((it: AnyRow) => it.market === market), [evidenceItems, market]);
  const candidateCount = overview?.candidate_assets?.[market] ?? overview?.candidates?.[market] ?? candidatePool.length;
  const latestPicks = overview?.latest_picks?.[market] ?? candidatePool.length;
  const publishedPool = overview?.published_pool?.[market] ?? candidatePool.length;
  const marketReview = useMemo(() => reviewQueue.filter((r: AnyRow) => {
    const owner = strategies.find((s: AnyRow) => s.strategy_id === r.strategy_id);
    return owner ? owner.market === market : market === 'CN';
  }), [reviewQueue, strategies, market]);
  const strategyName = (raw: string) => {
    const ids = String(raw || '').split(/[,，;；+\/|]/).map((x) => x.trim()).filter(Boolean);
    return ids.map((id) => {
      const s = marketStrategies.find((x: AnyRow) => x.strategy_id === id);
      return s ? `${s.display_name || s.manifest?.name || id}` : id;
    }).join(' / ') || '—';
  };
  const registered = marketStrategies.length;
  const producing = marketStrategies.filter((s: AnyRow) => s.lifecycle_status !== 'research' && (s.output?.report_date || s.output?.reports)).length;
  const activeStrategies = marketStrategies.filter((s: AnyRow) => ['active', 'live'].includes(s.lifecycle_status)).length;
  const reviewCount = marketReview.length;
  const feedbackCount = feedback.length;
  const supervisionCount = supervision.length;
  const okSteps = (job?.steps || []).filter((s: AnyRow) => s.status === 'success').length;
  const stepTotal = (job?.steps || []).length;

  const members = useMemo(() => {
    const byStrategy = new Map<string, string>();
    marketStrategies.forEach((s: AnyRow) => byStrategy.set(s.strategy_id, s.research_owner || '未分配'));
    const map: Record<string, AnyRow> = {};
    const bump = (owner: string, key: string, n = 1) => {
      if (!map[owner]) map[owner] = { owner, strategies: 0, candidate: 0, pool: 0, reports: 0 };
      map[owner][key] += n;
    };
    marketStrategies.forEach((s: AnyRow) => bump(s.research_owner || '未分配', 'strategies'));
    const ownerOf = (r: AnyRow) => {
      const raw = r.strategy_id || r.source_strategies || '';
      const sid = String(raw).split(/[,，;；+\/|]/).map((x) => x.trim()).filter(Boolean)[0] || '';
      return byStrategy.get(sid) || '未分配';
    };
    candidatePool.forEach((r: AnyRow) => bump(ownerOf(r), 'candidate'));
    marketPool.forEach((r: AnyRow) => bump(ownerOf(r), 'pool'));
    reports.forEach((r: AnyRow) => bump(ownerOf(r), 'reports'));
    return Object.values(map).sort((a, b) => b.pool - a.pool || b.candidate - a.candidate);
  }, [marketStrategies, candidatePool, marketPool, reports]);
  const memberCount = members.filter((m) => m.strategies > 0).length;
  const commandSeatCount = new Set([
    ...members.map((m) => m.owner),
    'ACAD1', 'BACK1', 'DATA1', 'AI1', 'PROD1', 'COMM1', 'INTEL1', 'LUSU1',
  ]).size;

  const commandSeats = useMemo(() => {
    const byOwner: Record<string, AnyRow> = {};
    members.forEach((m) => { byOwner[m.owner] = m; });
    const st = (o: string) => byOwner[o] || { strategies: 0, candidate: 0, pool: 0, reports: 0 };
    const countOwner = (field: string) => marketStrategies.filter((s: AnyRow) => s[field] === 'BACK1').length;
    const ownerSeat = (id: string, name: string, role: string, skills: string, logic: string) => {
      const x = st(id);
      return {
        id, name, role, skills, logic,
        current: `在研 ${x.strategies} 条策略；最新日报 ${reports.filter((r: AnyRow) => x.strategies > 0 && r.strategy_id && marketStrategies.some((s: AnyRow) => s.strategy_id === r.strategy_id && s.research_owner === id)).length || 0} 条`,
        done: `候选 ${x.candidate} · 入池 ${x.pool} · 日报 ${x.reports}`,
        metrics: `策略 ${x.strategies} · 候选 ${x.candidate} · 入池 ${x.pool}`,
      };
    };
    return [
      {
        id: 'ACAD1', name: '司马徽', role: '军团长 / 研究院院长',
        skills: '终审、方法论评审、跨部门协调、资源调度',
        logic: '证据优先、No-Go 可执行；无 owner 不进池；终审不代替研究 owner。',
        current: `当前复核 ${marketReview.length} 张 draft、ACAD1 ${marketPool.length} 只`,
        done: `已评审 ${overview?.acad1_reviews ?? '—'} 条；反馈 ${feedbackCount}；督导 ${supervisionCount}`,
        metrics: `ACAD1 ${marketPool.length} · 复核 ${marketReview.length} · 评审 ${overview?.acad1_reviews ?? '—'}`,
      },
      ownerSeat('STRAT1', '徐庶', '策略研究组负责人（CN/HK）', '策略假设、因子设计、CN/HK 平台策略、研究台账', '假设 → 因子 → 日报 → 交 BACK1 独立验证；不自己验证自己。'),
      ownerSeat('ST1', '程昱', '缠论首席', '缠论结构、分型/笔/中枢、三类买卖点、几何判读', '结构 → 中枢 → 三买 → 几何门；失效即回收。'),
      ownerSeat('STRAT2', '庞统', 'US/CRYPTO 策略研究员', '美股趋势、加密趋势/RSI、跨市场研究', '跨市场趋势/波动 → 日报 → BACK1 验证。'),
      {
        id: 'BACK1', name: '王粲', role: '独立验证',
        skills: '回测、样本外、成本/滑点、回撤、No-Go 证据',
        logic: '研究与验证分离；样本外/成本/回撤不过 → No-Go。',
        current: `当前验证 ${countOwner('validation_owner')} 条策略`,
        done: `已覆盖 ${countOwner('validation_owner')} 条策略的验证职责`,
        metrics: `验证策略 ${countOwner('validation_owner')} 条`,
      },
      {
        id: 'DATA1', name: '张松', role: '数据治理',
        skills: '数据仓、PIT、数据契约、目录/血缘、质量检查',
        logic: '数据冻结 → PIT → 契约 → 质量门；缺数据如实标缺口。',
        current: `当前数据日 ${overview?.data_date || dataDate}，保障 ${marketStrategies.filter((s: AnyRow) => s.data_owner === 'DATA1').length} 条策略数据`,
        done: `已登记数据日 ${overview?.data_date || dataDate}；反馈全库 ${overview?.feedback_total ?? '—'}、督导全库 ${overview?.supervision_total ?? '—'}`,
        metrics: `数据日 ${overview?.data_date || dataDate} · 数据 owner ${marketStrategies.filter((s: AnyRow) => s.data_owner === 'DATA1').length} 条`,
      },
      {
        id: 'AI1', name: '诸葛亮', role: '运行/自动化',
        skills: '任务编排、监控告警、Hermes/Agent、自动化',
        logic: '编排 → 执行 → 监控 → 告警 → 可回滚；运行不替代策略决策。',
        current: `当前任务 ${okSteps}/${stepTotal} 步成功`,
        done: `已运行数据日 ${overview?.data_date || dataDate} 全链路；运行 owner ${marketStrategies.filter((s: AnyRow) => s.ops_owner === 'AI1').length} 条`,
        metrics: `任务 ${okSteps}/${stepTotal} · 运行 owner ${marketStrategies.filter((s: AnyRow) => s.ops_owner === 'AI1').length} 条`,
      },
      {
        id: 'PROD1', name: '黄月英', role: '产品化',
        skills: '页面设计、交互、验收标准、商业化交付',
        logic: '研究结果 → 页面/交互 → 验收 → 可交付；不改变研究结论。',
        current: `当前维护产品面：指挥中心/司马徽/证据/复核`,
        done: `产品 owner ${marketStrategies.filter((s: AnyRow) => s.product_owner === 'PROD1').length} 条`,
        metrics: `产品 owner ${marketStrategies.filter((s: AnyRow) => s.product_owner === 'PROD1').length} 条`,
      },
      {
        id: 'COMM1', name: '马良', role: '商业化研究',
        skills: '商业模式、客户场景、成本收益、交付边界',
        logic: '客户 → 场景 → 交付 → 成本 → 可持续收入；不编造需求。',
        current: '当前无数据库产出（研究席）', done: '—（暂无数据库产出）', metrics: '—',
      },
      {
        id: 'INTEL1', name: '郭嘉', role: '竞争情报',
        skills: '行业对标、开源跟踪、竞争路线、证据分级',
        logic: '事实/推断/未知分级；未核验只作线索。',
        current: '当前无数据库产出（研究席）', done: '—（暂无数据库产出）', metrics: '—',
      },
      {
        id: 'LUSU1', name: '阚泽', role: '下游反馈/入口',
        skills: '客户入口、路由、客服、反馈采集',
        logic: '路由 → 反馈 → 回写策略 owner；不越权裁决。',
        current: `当前反馈 ${feedbackCount}、督导 ${supervisionCount}`,
        done: `全库反馈 ${overview?.feedback_total ?? '—'}、督导 ${overview?.supervision_total ?? '—'}`,
        metrics: `反馈 ${feedbackCount} · 督导 ${supervisionCount}`,
      },
    ];
  }, [members, marketStrategies, marketPool, marketReview, overview, dataDate, okSteps, stepTotal, feedbackCount, supervisionCount, reports]);

  const marketFilteredPool = useMemo(
    () => pool.filter((r: AnyRow) => r.market === market),
    [pool, market],
  );
  const marketFilteredStrategies = useMemo(
    () => strategies.filter((s) => {
      if (market !== 'ALL' && s.market !== market) return false;
      if (!strategySearch.trim()) return true;
      const q = strategySearch.toLowerCase();
      return String(s.strategy_id).toLowerCase().includes(q)
        || String(s.display_name || '').toLowerCase().includes(q)
        || String(s.research_owner || '').toLowerCase().includes(q);
    }),
    [strategies, market, strategySearch],
  );
  const poolRows = useMemo(() => marketFilteredPool.filter((r) => {
    if (!poolSearch.trim()) return true;
    const q = poolSearch.toLowerCase();
    return String(r.code).toLowerCase().includes(q)
      || String(r.name || '').toLowerCase().includes(q)
      || String(r.strategy_id).toLowerCase().includes(q);
  }), [marketFilteredPool, poolSearch]);

  const stageCards = [
    { key: 'job', no: 1, title: '任务触发', value: job?.status === 'success' ? '成功' : (job?.status || '未上报'), sub: `${job?.source === 'schedule' ? '定时' : '手动'} · ${dataDate}`, color: job?.status === 'success' ? 'green' : 'red' },
    { key: 'report', no: 2, title: '策略日报', value: `${new Set(reports.map((r: AnyRow) => r.strategy_id)).size} / ${registered}`, sub: '各策略当日日报', color: 'blue' },
    { key: 'pool', no: 3, title: '候选池', value: `${candidateCount}`, sub: `最新选股 ${latestPicks} · 发布池 ${publishedPool}`, color: 'blue' },
    { key: 'acad1', no: 4, title: 'ACAD1 池', value: `${marketPool.length}`, sub: `上限 30 · ${stats?.pool_date || dataDate}`, color: 'gold' },
    { key: 'evidence', no: 5, title: '证据包', value: `${marketEvidence.length || '—'}`, sub: '卡事实/公告/新闻/KB', color: 'cyan' },
    { key: 'review', no: 6, title: '复核队列', value: `${reviewCount}`, sub: 'draft 研究卡', color: 'orange' },
    { key: 'feedback', no: 7, title: '下游反馈', value: `${feedbackCount}`, sub: '用/不用 + 结果', color: 'purple' },
    { key: 'supervision', no: 8, title: '督导', value: `${supervisionCount}`, sub: 'ACAD1 督导意见', color: 'magenta' },
  ];

  const ask = async (question: string, context: AnyRow, page = 'flow') => {
    setHandoffBusy(true);
    setHandoffAnswer(null);
    try {
      const id = await createHandoff(question, context, page);
      setHandoffId(id);
      setHandoff({ id, question, status: 'pending' });
      setHandoffOpen(true);
    } catch (e: any) {
      setHandoffAnswer({ status: 'failed', answer: `交接失败：${String(e?.message || e)}` });
      setHandoffOpen(true);
    } finally {
      setHandoffBusy(false);
    }
  };

  const askFlow = () => ask(
    `请解释司马徽今天的选股动线：任务=${job?.status || '未上报'}，数据日=${dataDate}，策略=${producing}/${registered}，候选池=${candidatePool.length}，ACAD1=${pool.length}，证据=${evidenceItems.length}，复核=${reviewCount}，反馈=${feedbackCount}，督导=${supervisionCount}。请指出异常和下一步。`,
    { job: job?.status, data_date: dataDate, registered, producing, candidate_pool: candidatePool.length, acad1: pool.length, evidence: evidenceItems.length, review: reviewCount, feedback: feedbackCount, supervision: supervisionCount },
  );

  const askStrategy = (r: AnyRow) => ask(
    `请解释策略 ${r.strategy_id}：名称=${r.display_name || r.manifest?.name}，研究 owner=${r.research_owner || '—'}，生命周期=${r.lifecycle_status || '—'}，最新日报=${r.output?.report_date || '无'}，版本=${r.manifest?.version || '—'}。请结合 manifest 判断是否正常，并给出下一步建议。`,
    { strategy_id: r.strategy_id, research_owner: r.research_owner, lifecycle: r.lifecycle_status, version: r.manifest?.version },
    'strategy',
  );

  const askStock = (r: AnyRow) => ask(
    `请解释 ${r.market} ${r.code} 为什么在 ACAD1 池内：名称=${r.name}，策略=${r.strategy_id}，信号=${r.signal}，状态=${r.status}，池日=${r.pool_date}。请引用证据包（卡事实/公告/新闻/KB），不要给投资建议，也不要自动通过或否决。`,
    { market: r.market, code: r.code, strategy_id: r.strategy_id, signal: r.signal, pool_date: r.pool_date },
    'acad1',
  );

  const exportView = () => {
    const blob = new Blob([JSON.stringify({ job, strategies, pool, evidence: evidenceItems, feedback, supervision, reviewQueue }, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `selection-flow-${dataDate}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const openManualRun = () => {
    if (!canManualRun || runBusy) return;
    setRunError('');
    setRunOpen(true);
  };

  const submitManualRun = async () => {
    if (runBusy) return;
    setRunBusy(true);
    setRunError('');
    try {
      // 每次点击生成新的幂等键：网络重试可复用，重复确认不会产生第二个 run。
      const idem = (globalThis.crypto?.randomUUID?.()
        || `manual-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`);
      const data = await runSelection(
        { market: runMarket, strategy_type: 'TREND', max_candidates: 10 },
        idem,
      );
      setRunResult(data);
      setRunOpen(false);
    } catch (e: any) {
      const status = Number(e?.status || 0);
      const code = String(e?.code || '');
      // flag 关闭（503 selection_run.disabled）或路由未发布（404）→ 隐藏入口，不再重复尝试。
      if (status === 503 || status === 404 || code === 'selection_run.disabled') {
        setRunUnavailable(true);
        setRunOpen(false);
      } else {
        setRunError(String(e?.message || e || '手动选股失败'));
      }
    } finally {
      setRunBusy(false);
    }
  };

  const runSummary = (r: SelectionRunResult) => {
    const parts = [
      `run_id：${r.run_id}`,
      `状态：${r.status}`,
      r.market ? `市场：${r.market}` : '',
      r.trade_date ? `交易日：${r.trade_date}` : '',
      r.duration_sec != null ? `耗时：${r.duration_sec}s` : '',
      r.replay ? '（幂等回放）' : '',
    ].filter(Boolean);
    const summary = r.result_summary ? ` · 结果：${JSON.stringify(r.result_summary).slice(0, 160)}` : '';
    return parts.join(' · ') + summary;
  };

  const stageDetail = (() => {
    if (!stage) return <Text type="secondary">点击上方任一环节，展开详情；不跳转页面。</Text>;
    if (stage === 'job') {
      return (
        <Space orientation="vertical" style={{ width: '100%' }}>
          <Progress percent={stepTotal ? Math.round((okSteps / stepTotal) * 100) : 0} status={job?.status === 'failed' ? 'exception' : undefined} />
          <Text type="secondary">{okSteps}/{stepTotal} steps · {job?.source === 'schedule' ? '定时' : '手动'} · {job?.finished_at || '未完成'}</Text>
          <Timeline items={(job?.steps || []).map((s: AnyRow) => ({
            color: s.status === 'success' ? 'green' : s.status === 'failed' ? 'red' : 'gray',
            children: <Space><Text>{s.name}</Text><Tag>{s.status}</Tag><Text type="secondary">{s.finished_at || ''}</Text></Space>,
          }))} />
        </Space>
      );
    }
    if (stage === 'report') {
      return (
        <Table size="small" rowKey="strategy_id" dataSource={reports} pagination={false}
          columns={[
            { title: '策略', dataIndex: 'strategy_id', width: 140 },
            { title: '市场', dataIndex: 'market', width: 70, render: (v: string) => <Tag color={marketColor(v)}>{v}</Tag> },
            { title: '分析师', dataIndex: 'analyst', width: 100 },
            { title: '观点', dataIndex: 'market_view', ellipsis: true },
            { title: '选股', dataIndex: 'picks', width: 60 },
          ]} />
      );
    }
    if (stage === 'pool') return <PoolMini rows={candidatePool} />;
    if (stage === 'acad1') return <PoolMini rows={marketFilteredPool} />;
    if (stage === 'evidence') {
      return (
        <Space orientation="vertical">
          <Text>证据条目 {marketEvidence.length}，覆盖 {marketEvidence.filter((x: AnyRow) => x.evidence_level === 'full').length}，无覆盖 {marketEvidence.filter((x: AnyRow) => x.evidence_level === 'none').length}，缺口 {evidence?.summary?.gaps ?? '—'}。</Text>
          <Text type="secondary">证据包生成：{evidence?.generated_at || '—'}；只读，不构成投资结论。</Text>
        </Space>
      );
    }
    if (stage === 'review') return <ReviewMini rows={marketReview.slice(0, 8)} />;
    if (stage === 'feedback') {
      return feedback.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无下游反馈；到选股中台录入" /> : <PoolMini rows={feedback} />;
    }
    return supervision.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无督导记录" /> : <PoolMini rows={supervision} />;
  })();

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>选股全景动线</Title>
          <Text type="secondary">触发 → 日报 → 候选池 → ACAD1 → 证据 → 复核 → 反馈 → 督导</Text>
          <div style={{ marginTop: 6 }}>
            <Tag color="green">只读</Tag>
            <Tag color="blue">当前市场 {market}</Tag>
            <Tag>数据日 {dataDate}</Tag>
            <Tag color={job?.status === 'success' ? 'green' : 'red'}>任务 {job?.status || '未上报'}</Tag>
          </div>
        </div>
        <Space wrap>
          <Input.Search
            placeholder="输入代码看个股全景"
            allowClear
            style={{ width: 180 }}
            onSearch={(v) => { const c = v.trim(); if (c) navigate(`/${routeMarket}/stock/${encodeURIComponent(c)}`); }}
          />
          {canManualRun && !runUnavailable && (
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              loading={runBusy}
              onClick={openManualRun}
              data-testid="manual-run-button"
            >
              手动跑一轮
            </Button>
          )}
          <Button type="primary" ghost icon={<MessageOutlined />} onClick={() => navigate(`/${routeMarket}/agent`)}>智能体</Button>
          <Button ghost onClick={() => navigate(`/${routeMarket}/selected`)}>选股多维</Button>
          <Segmented
            value={market}
            options={MARKETS.slice(1).map((m) => ({ label: m, value: m }))}
            onChange={(v) => navigate(`/${String(v).toLowerCase()}/replay?tab=flow`)}
          />
          <Button icon={<DownloadOutlined />} onClick={exportView}>导出视图</Button>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>
        </Space>
      </div>

      {runUnavailable && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          title="手动跑一轮未开启"
          description="后端 feature flag SELECTION_RUN_ENABLED 未开启（或接口未发布），入口已隐藏；开启后仅 ADMIN/TRADER 可见。"
        />
      )}
      {runResult && (
        <Alert
          type={runResult.status === 'succeeded' ? 'success' : 'warning'}
          showIcon
          closable
          onClose={() => setRunResult(null)}
          style={{ marginBottom: 12 }}
          title={`手动选股 ${runResult.status || '已完成'}`}
          description={runSummary(runResult)}
        />
      )}
      {runError && (
        <Alert
          type="error"
          showIcon
          closable
          onClose={() => setRunError('')}
          style={{ marginBottom: 12 }}
          title="手动选股失败"
          description={runError}
        />
      )}

      <Row gutter={8} style={{ marginBottom: 12 }}>
        {quickLinks.map((q) => (
          <Col key={q.label} xs={12} sm={8} md={4}>
            <Card size="small" hoverable onClick={() => navigate(`/${routeMarket}${q.path}`)} style={{ cursor: 'pointer' }}>
              <Space direction="vertical" size={0} style={{ width: '100%' }}>
                <Text strong>{q.label}</Text>
                <Text type="secondary" style={{ fontSize: 11 }}>{q.desc}</Text>
              </Space>
            </Card>
          </Col>
        ))}
      </Row>

      <Card size="small" title="数据底座" extra={<Button size="small" onClick={() => setDataOpen(true)}>查看清单</Button>} style={{ marginBottom: 12 }}>
        <Space wrap size={18}>
          <Text>数据表 <b>{dataInventory?.summary?.tables ?? '—'}</b></Text>
          <Text>总行数 <b>{dataInventory?.summary?.rows != null ? Number(dataInventory.summary.rows).toLocaleString() : '—'}</b></Text>
          <Text>数据日 <b>{dataInventory?.tables?.find((x: AnyRow) => x.table === 'v2_bar_daily')?.latest?.slice(0, 10) || dataDate}</b></Text>
          <Text type="secondary">DB + 外部证据/数据仓；缺口在清单中显示</Text>
        </Space>
      </Card>

      {error && <Alert type="error" showIcon style={{ marginBottom: 12 }} title="部分数据加载失败" description={error} />}
      <Row gutter={10} style={{ marginBottom: 12 }}>
        <Col xs={12} md={3}><Card size="small" hoverable onClick={() => setKpi('members')} style={{ cursor: 'pointer' }}><Statistic title="军团成员" value={commandSeatCount} suffix="席" styles={{ content: { fontSize: 20 } }} /><Text type="secondary" style={{ fontSize: 10 }}>研究 owner {memberCount}</Text></Card></Col>
        <Col xs={12} md={3}><Card size="small" hoverable onClick={() => setKpi('candidate')} style={{ cursor: 'pointer' }}><Statistic title="候选/选股" value={`${candidateCount}/${latestPicks}`} styles={{ content: { fontSize: 20 } }} /><Text type="secondary" style={{ fontSize: 10 }}>候选池/最新选股</Text></Card></Col>
        <Col xs={12} md={3}><Card size="small" hoverable onClick={() => setKpi('pool')} style={{ cursor: 'pointer' }}><Statistic title="ACAD1 入池" value={marketPool.length} suffix="只" styles={{ content: { fontSize: 20 } }} /><Text type="secondary" style={{ fontSize: 10 }}>上限 30</Text></Card></Col>
        <Col xs={12} md={3}><Card size="small" hoverable onClick={() => setKpi('strategy')} style={{ cursor: 'pointer' }}><Statistic title="策略" value={`${producing}/${registered}`} suffix="产数" styles={{ content: { fontSize: 20 } }} /><Text type="secondary" style={{ fontSize: 10 }}>研究态不计产数</Text></Card></Col>
        <Col xs={12} md={3}><Card size="small" hoverable onClick={() => setKpi('evidence')} style={{ cursor: 'pointer' }}><Statistic title="证据" value={marketEvidence.length || '—'} suffix="只" styles={{ content: { fontSize: 20 } }} /><Text type="secondary" style={{ fontSize: 10 }}>证据包条目</Text></Card></Col>
        <Col xs={12} md={3}><Card size="small" hoverable onClick={() => setKpi('review')} style={{ cursor: 'pointer' }}><Statistic title="复核" value={reviewCount} styles={{ content: { fontSize: 20 } }} /><Text type="secondary" style={{ fontSize: 10 }}>draft 卡</Text></Card></Col>
        <Col xs={12} md={3}><Card size="small" hoverable onClick={() => setKpi('feedback')} style={{ cursor: 'pointer' }}><Statistic title="反馈" value={feedbackCount} styles={{ content: { fontSize: 20 } }} /><Text type="secondary" style={{ fontSize: 10 }}>当日</Text></Card></Col>
        <Col xs={12} md={3}><Card size="small" hoverable onClick={() => setKpi('supervision')} style={{ cursor: 'pointer' }}><Statistic title="督导" value={supervisionCount} styles={{ content: { fontSize: 20 } }} /><Text type="secondary" style={{ fontSize: 10 }}>当日</Text></Card></Col>
      </Row>

      <Card size="small" title="全链路 · 点击环节看详情" style={{ marginBottom: 12 }}>
        <Row gutter={[8, 8]}>
          {stageCards.map((s, i) => (
            <Col key={s.key} xs={12} sm={8} md={6} lg={3}>
              <Card
                size="small"
                hoverable
                onClick={() => setStage(s.key)}
                style={{ borderColor: stage === s.key ? '#d4a94f' : undefined, cursor: 'pointer' }}
              >
                <Space orientation="vertical" size={2} style={{ width: '100%' }}>
                  <Badge color={s.color} text={<Text strong>{s.no}. {s.title}</Text>} />
                  <Text style={{ fontSize: 22, fontWeight: 700 }}>{s.value}</Text>
                  <Text type="secondary" style={{ fontSize: 11 }}>{s.sub}</Text>
                  {i < stageCards.length - 1 && <div style={{ textAlign: 'right', opacity: 0.35 }}>→</div>}
                </Space>
              </Card>
            </Col>
          ))}
        </Row>
        <Divider style={{ margin: '10px 0' }} />
        {stageDetail}
      </Card>

      <Row gutter={12}>
        <Col xs={24} lg={14}>
          <Card size="small" title="指挥部 11 席" extra={<Text type="secondary" style={{ fontSize: 11 }}>点击查看正在干什么/已经干了什么/技能/底层逻辑</Text>} style={{ marginBottom: 12 }}>
            <Table
              size="small"
              rowKey="id"
              dataSource={commandSeats}
              pagination={false}
              onRow={(r) => ({ onClick: () => setSeatDetail(r), style: { cursor: 'pointer' } })}
              columns={[
                { title: '席位', dataIndex: 'id', width: 70, render: (v: string) => <Text code>{v}</Text> },
                { title: '姓名', dataIndex: 'name', width: 65, render: (v: string) => <Text strong>{v}</Text> },
                { title: '正在干什么', dataIndex: 'current' },
                { title: '已经干了什么', dataIndex: 'done' },
              ]}
            />
          </Card>

          <Card size="small" title={`军团成员产出（${memberCount}）`} style={{ marginBottom: 12 }}>
            <Table
              size="small"
              rowKey="owner"
              dataSource={members}
              pagination={false}
              columns={[
                { title: '成员 / 研究 owner', dataIndex: 'owner', width: 140, render: (v: string) => <Tag color="purple">{v}</Tag> },
                { title: '负责策略', dataIndex: 'strategies', width: 90 },
                { title: '候选产出', dataIndex: 'candidate', width: 90 },
                { title: '入 ACAD1', dataIndex: 'pool', width: 90 },
                { title: '日报', dataIndex: 'reports', width: 70 },
                { title: '产出率', render: (_: any, r: AnyRow) => <Text>{r.candidate ? `${Math.round((r.pool / r.candidate) * 100)}%` : '—'}</Text> },
              ]}
            />
            <Text type="secondary" style={{ fontSize: 11 }}>按 research_owner 聚合；研究态策略单独计 0 产出。</Text>
          </Card>

          <Card
            size="small"
            title={`策略归属（${registered}）`}
            extra={<Input size="small" allowClear placeholder="搜索策略/owner" value={strategySearch} onChange={(e) => setStrategySearch(e.target.value)} style={{ width: 180 }} />}
            style={{ marginBottom: 12 }}
          >
            <Table
              size="small"
              rowKey="strategy_id"
              dataSource={marketFilteredStrategies}
              loading={loading}
              pagination={{ pageSize: 8, size: 'small' }}
              onRow={(r) => ({ onClick: () => setStrategyDetail(r), style: { cursor: 'pointer' } })}
              columns={[
                { title: '策略（中文）', width: 150, render: (_: any, r: AnyRow) => <Text strong>{r.display_name || r.manifest?.name || r.strategy_id}</Text> },
                { title: 'ID', dataIndex: 'strategy_id', width: 120, render: (v: string) => <Text code style={{ fontSize: 10 }}>{v}</Text> },
                { title: '研究 owner', dataIndex: 'research_owner', width: 95, render: (v: string) => <Tag color="purple">{v || '—'}</Tag> },
                { title: '生命周期', dataIndex: 'lifecycle_status', width: 90, render: (v: string) => <Tag color={lifecycleColor(v)}>{v}</Tag> },
                { title: '最新日报', width: 95, render: (_: any, r: AnyRow) => r.output?.report_date || <Text type="secondary">—</Text> },

              ]}
            />
          </Card>

          <Card
            size="small"
            title={`ACAD1 池（${marketFilteredPool.length}）`}
            extra={<Input size="small" allowClear placeholder="搜索代码/名称/策略" value={poolSearch} onChange={(e) => setPoolSearch(e.target.value)} style={{ width: 200 }} />}
          >
            <Table
              size="small"
              rowKey={(r: AnyRow) => `${r.market}-${r.code}`}
              dataSource={poolRows}
              loading={loading}
              pagination={{ pageSize: 8, size: 'small' }}
              onRow={(r) => ({ onClick: () => setStockDetail(r), style: { cursor: 'pointer' } })}
              columns={[
                { title: '市场', dataIndex: 'market', width: 72, render: (v: string) => <Tag color={marketColor(v)}>{v}</Tag> },
                { title: '代码', dataIndex: 'code', width: 105, render: (v: string) => <Text code style={{ fontSize: 11 }}>{v}</Text> },
                { title: '名称', dataIndex: 'name', ellipsis: true },
                { title: '基础信号', dataIndex: 'base_state', width: 82, render: (v: string) => {
                  const color = v === 'BUY' ? 'red' : v === 'SELL' ? 'green' : v === 'AVOID' ? 'orange' : 'default';
                  const label = v === 'BUY' ? '买点' : v === 'SELL' ? '卖点' : v === 'AVOID' ? '回避' : v === 'NEUTRAL' ? '观察' : '—';
                  return <Tag color={color}>{label}</Tag>;
                } },
                { title: '策略来源', dataIndex: 'strategy_id', width: 170, ellipsis: true, render: (v: string) => <span title={v}>{strategyName(v)}</span> },
                { title: '入池逻辑', dataIndex: 'selection_reason', width: 280, ellipsis: true, render: (v: string) => <span title={v}>{v || '—'}</span> },
                { title: '信号', dataIndex: 'signal', width: 105, ellipsis: true, render: (v: string) => zhSignal(v) },
                { title: '更新时间', dataIndex: 'selected_at', width: 150, render: (v: string) => v ? String(v).slice(0, 16).replace('T', ' ') : dataDate },
                { title: '数据日', dataIndex: 'data_date', width: 100, render: (v: string) => v || dataDate },
                { title: '状态', dataIndex: 'status', width: 75, render: (v: string) => <Tag>{zhStatus(v)}</Tag> },

              ]}
            />
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <Card
            size="small"
            title="运行态/质量"
            extra={<Tag color={job?.status === 'success' ? 'green' : 'red'}>{job?.status || '未上报'}</Tag>}
            style={{ marginBottom: 12 }}
          >
            <Space orientation="vertical" size={10} style={{ width: '100%' }}>
              <div>
                <Text type="secondary">步骤完成度</Text>
                <Progress percent={stepTotal ? Math.round((okSteps / stepTotal) * 100) : 0} status={job?.status === 'failed' ? 'exception' : undefined} />
                <Text type="secondary" style={{ fontSize: 11 }}>{okSteps}/{stepTotal} · {job?.source === 'schedule' ? '定时' : '手动'} · {job?.finished_at || '未完成'}</Text>
              </div>
              <Row gutter={8}>
                <Col span={8}><Statistic title="活跃策略" value={activeStrategies} styles={{ content: { fontSize: 18 } }} /></Col>
                <Col span={8}><Statistic title="复核" value={reviewCount} styles={{ content: { fontSize: 18 } }} /></Col>
                <Col span={8}><Statistic title="反馈" value={feedbackCount} styles={{ content: { fontSize: 18 } }} /></Col>
              </Row>
              {job?.status === 'failed' && <Alert type="error" showIcon title="任务失败" description={job?.error || '查看任务日志'} />}
              {job?.status !== 'success' && job?.status !== 'failed' && <Alert type="warning" showIcon title="任务未完成" description="等待每日任务上报" />}
            </Space>
          </Card>

          <Card
            size="small"
            title={`待复核队列（${marketReview.length}）`}
            extra={<Button size="small" onClick={() => setStage('review')}>在链路上看</Button>}
            style={{ marginBottom: 12 }}
          >
            {marketReview.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待复核卡" /> : <ReviewMini rows={marketReview.slice(0, 10)} />}
          </Card>

          <Card size="small" title="智能体模块" extra={<Tag color="blue">独立入口</Tag>}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <Text type="secondary">智能体已拆成独立模块，不混在选股动线里。能力调用、待办、应办、已归档都在独立工作台完成。</Text>
              <Button type="primary" icon={<MessageOutlined />} onClick={() => navigate(`/${routeMarket}/agent`)}>打开智能体工作台</Button>
            </Space>
          </Card>
        </Col>
      </Row>

      <Card size="small" title="口径" style={{ marginTop: 12 }}>
        <Text type="secondary">
          注册 {registered} · 产数 {producing} · 候选池 {candidateCount} / 最新选股 {latestPicks} / 发布池 {publishedPool} · ACAD1 入池 {marketPool.length} · 证据 {marketEvidence.length || '—'} · 复核 {reviewCount} · 反馈 {feedbackCount} · 督导 {supervisionCount}
          {market !== 'ALL' ? ` · 当前市场 ${market}` : ''}
        </Text>
      </Card>

      <Drawer open={dataOpen} onClose={() => setDataOpen(false)} size={1000} title="数据底座清单">
        <Table
          size="small"
          rowKey="table"
          dataSource={dataInventory?.tables || []}
          pagination={false}
          columns={[
            { title: '数据表', dataIndex: 'table', width: 210, render: (v: string) => <Text code style={{ fontSize: 11 }}>{v}</Text> },
            { title: '说明', dataIndex: 'label', width: 140 },
            { title: '行数', dataIndex: 'rows', width: 100, render: (v: number) => Number(v || 0).toLocaleString() },
            { title: '最新日期', dataIndex: 'latest', width: 190, render: (v: string) => v || '—' },
            { title: '市场分布', dataIndex: 'markets', render: (v: AnyRow) => Object.entries(v || {}).map(([k, n]) => <Tag key={k}>{k} {String(n)}</Tag>) },
          ]}
        />
      </Drawer>

      <Drawer open={!!seatDetail && !kpi} onClose={() => setSeatDetail(null)} size={900} title={seatDetail ? `${seatDetail.name} · ${seatDetail.id}` : '席位详情'}>
        {seatDetail && <SeatDetail seat={seatDetail} onBack={() => setSeatDetail(null)} />}
      </Drawer>

      <Drawer open={!!kpi} onClose={() => { setKpi(null); setSeatDetail(null); }} size={900}
        title={kpi === 'members' ? '军团成员' : kpi === 'candidate' ? '候选产出' : kpi === 'pool' ? 'ACAD1 入池' : kpi === 'strategy' ? '策略产数' : kpi === 'evidence' ? '证据包' : kpi === 'review' ? '复核队列' : kpi === 'feedback' ? '反馈' : '督导'}>
        {kpi === 'members' && (seatDetail ? (
          <SeatDetail seat={seatDetail} onBack={() => setSeatDetail(null)} />
        ) : (
          <Table size="small" rowKey="id" dataSource={commandSeats} pagination={false}
            onRow={(r) => ({ onClick: () => setSeatDetail(r), style: { cursor: 'pointer' } })}
            columns={[
              { title: '席位', dataIndex: 'id', width: 70, render: (v: string) => <Text code>{v}</Text> },
              { title: '姓名', dataIndex: 'name', width: 65, render: (v: string) => <Text strong>{v}</Text> },
              { title: '正在干什么', dataIndex: 'current' },
              { title: '已经干了什么', dataIndex: 'done' },
              { title: '能力/技能', dataIndex: 'skills', ellipsis: true },
            ]} />
        ))}
        {kpi === 'candidate' && (
          <>
          <Alert type="info" showIcon style={{ marginBottom: 8 }}
            title={`候选池 ${candidateCount} 只（资产去重 ${overview?.candidate_pool_date || ''}） / 最新选股 ${latestPicks} 条（${overview?.latest_pick_date?.[market] || ''}） / 发布池 ${publishedPool} 条`}
            description="三个口径分开：候选池按 v2_candidate_asset 去重；最新选股按各市场最新日报 picks；发布池按 v2_selection_pool 最新 pool_date。不混用。" />
          <Table size="small" rowKey={(r: AnyRow, i?: number) => `${r.code}-${i}`} dataSource={candidatePool} pagination={{ pageSize: 12 }}
            columns={[
              { title: '代码', dataIndex: 'code', width: 110 },
              { title: '名称', dataIndex: 'name', ellipsis: true },
              { title: '策略', dataIndex: 'source_strategies', ellipsis: true },
              { title: '信号', dataIndex: 'signal_types', width: 100 },
            ]} />
          </>
        )}
        {kpi === 'pool' && (
          <Table size="small" rowKey={(r: AnyRow) => `${r.market}-${r.code}`} dataSource={marketPool} pagination={false}
            columns={[
              { title: '市场', dataIndex: 'market', width: 70 },
              { title: '代码', dataIndex: 'code', width: 105 },
              { title: '名称', dataIndex: 'name', ellipsis: true },
              { title: '策略', dataIndex: 'strategy_id', ellipsis: true },
              { title: '信号', dataIndex: 'signal' },
            ]} />
        )}
        {kpi === 'strategy' && (
          <Table size="small" rowKey="strategy_id" dataSource={marketStrategies} pagination={false}
            columns={[
              { title: '策略（中文）', render: (_: any, r: AnyRow) => r.display_name || r.manifest?.name },
              { title: 'ID', dataIndex: 'strategy_id', width: 130 },
              { title: '负责人', dataIndex: 'research_owner', width: 90 },
              { title: '生命周期', dataIndex: 'lifecycle_status', width: 90 },
              { title: '最新日报', width: 100, render: (_: any, r: AnyRow) => r.output?.report_date || '—' },
            ]} />
        )}
        {kpi === 'evidence' && (
          <Table size="small" rowKey="code" dataSource={marketEvidence} pagination={{ pageSize: 12 }}
            columns={[
              { title: '代码', dataIndex: 'code', width: 110 },
              { title: '名称', dataIndex: 'name', ellipsis: true },
              { title: '证据等级', dataIndex: 'evidence_level', width: 100 },
              { title: '缺口', dataIndex: 'gap_count', width: 70 },
            ]} />
        )}
        {kpi === 'review' && <ReviewMini rows={marketReview} />}
        {(kpi === 'feedback' || kpi === 'supervision') && (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`当前市场当日${kpi === 'feedback' ? '反馈' : '督导'}为 0；历史数据在选股中台查看`} />
        )}
      </Drawer>

      <Drawer open={!!strategyDetail} onClose={() => setStrategyDetail(null)} size={620}
        title={strategyDetail ? `策略 · ${strategyDetail.strategy_id}` : ''}>
        {strategyDetail && <StrategyDetail row={strategyDetail} />}
      </Drawer>

      <Drawer open={!!stockDetail} onClose={() => setStockDetail(null)} size={720}
        title={stockDetail ? `${stockDetail.market} · ${stockDetail.code}` : ''}>
        {stockDetail && (
          <>
            <StockDetail
              row={stockDetail}
              evidence={evidenceByCode[`${stockDetail.market}:${stockDetail.code}`] || evidenceByCode[`${stockDetail.market}:${stockDetail.pool_code}`]}
            />
            <Button type="primary" style={{ marginTop: 12 }} onClick={() => navigate(`/${routeMarket}/stock/${encodeURIComponent(stockDetail.code)}`)}>打开个股全景</Button>
          </>
        )}
      </Drawer>

      <Modal
        open={runOpen}
        title="确认手动跑一轮"
        okText="确认执行"
        cancelText="取消"
        confirmLoading={runBusy}
        maskClosable={!runBusy}
        closable={!runBusy}
        onOk={submitManualRun}
        onCancel={() => { if (!runBusy) setRunOpen(false); }}
      >
        <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            type="warning"
            showIcon
            title="将立即触发一轮手动选股（不受定时链影响）"
            description="执行会重新计算候选/选股结果并写入审计；并发锁期间不可重复触发；非交易日会被后端拒绝。"
          />
          <Descriptions size="small" column={1} bordered items={[
            { key: 'market', label: '市场', children: runMarket },
            { key: 'trade_date', label: '交易日', children: '默认=市场时区今天（后端校验交易日）' },
            { key: 'strategy', label: '策略类型', children: 'TREND' },
            { key: 'max', label: '候选上限', children: '10' },
            { key: 'role', label: '触发角色', children: role || '—' },
          ]} />
          <Text type="secondary">仅 ADMIN/TRADER 可触发；普通用户不会看到该入口。</Text>
        </Space>
      </Modal>

    </div>
  );
}

function SeatDetail({ seat, onBack }: { seat: AnyRow; onBack: () => void }) {
  return (
    <Space orientation="vertical" size={12} style={{ width: '100%' }}>
      <Button size="small" onClick={onBack}>← 返回席位列表</Button>
      <Title level={4} style={{ margin: 0 }}>{seat.name} · {seat.id}</Title>
      <Descriptions size="small" column={1} bordered items={[
        { key: 'role', label: '职责', children: seat.role },
        { key: 'now', label: '正在干什么', children: seat.current },
        { key: 'done', label: '已经干了什么', children: seat.done },
        { key: 'skills', label: '基本能力 / 技能', children: seat.skills },
        { key: 'logic', label: '底层逻辑 / 方法', children: seat.logic },
        { key: 'metric', label: '当前真实产出', children: seat.metrics },
      ]} />
    </Space>
  );
}

function PoolMini({ rows }: { rows: AnyRow[] }) {
  return (
    <Table
      size="small"
      rowKey={(r: AnyRow, i?: number) => `${r.market}-${r.code}-${i}`}
      dataSource={rows.slice(0, 8)}
      pagination={false}
      columns={[
        { title: '市场', dataIndex: 'market', width: 70, render: (v: string) => <Tag color={marketColor(v)}>{v}</Tag> },
        { title: '代码', dataIndex: 'code', width: 100 },
        { title: '名称', dataIndex: 'name', ellipsis: true },
        { title: '策略', dataIndex: 'strategy_id', ellipsis: true },
        { title: '信号', dataIndex: 'signal', ellipsis: true },
        { title: '状态', dataIndex: 'status', width: 70, render: (v: string) => zhStatus(v) },
      ]}
    />
  );
}

function ReviewMini({ rows }: { rows: AnyRow[] }) {
  return (
    <Table
      size="small"
      rowKey={(r: AnyRow, i?: number) => `${r.code}-${r.strategy_id}-${i}`}
      dataSource={rows}
      pagination={false}
      columns={[
        { title: '代码', dataIndex: 'code', width: 95 },
        { title: '策略', dataIndex: 'strategy_id', ellipsis: true },
        { title: '方向', dataIndex: 'direction', width: 70 },
        { title: '状态', dataIndex: 'status', width: 70, render: (v: string) => <Tag>{v}</Tag> },
      ]}
    />
  );
}

function StrategyDetail({ row }: { row: AnyRow }) {
  const m = row.manifest || {};
  return (
    <Space orientation="vertical" size={12} style={{ width: '100%' }}>
      <Descriptions size="small" column={2} bordered items={[
        { key: 'id', label: '策略 ID', children: row.strategy_id },
        { key: 'name', label: '名称', children: row.display_name || m.name },
        { key: 'owner', label: '研究 owner', children: <Tag color="purple">{row.research_owner || '—'}</Tag> },
        { key: 'life', label: '生命周期', children: <Tag color={lifecycleColor(row.lifecycle_status)}>{row.lifecycle_status || '—'}</Tag> },
        { key: 'v', label: '版本', children: m.version || '—' },
        { key: 'vs', label: '版本状态', children: m.version_status || '—' },
        { key: 'report', label: '最新日报', children: row.output?.report_date || '—' },
        { key: 'review', label: '下次评审', children: row.next_review || '—' },
      ]} />
      <Card size="small" title="假设">{m.hypothesis || '—'}</Card>
      <Card size="small" title="数据依赖">{(m.data_deps || []).map((d: string) => <Tag key={d}>{d}</Tag>)}</Card>
      <Card size="small" title="Kill switch">{m.kill_switch || '—'}</Card>
    </Space>
  );
}

function StockDetail({ row, evidence }: { row: AnyRow; evidence?: AnyRow }) {
  const card = evidence?.card?.fields || {};
  return (
    <Space orientation="vertical" size={12} style={{ width: '100%' }}>
      <Descriptions size="small" column={2} bordered items={[
        { key: 'm', label: '市场', children: <Tag color={marketColor(row.market)}>{row.market}</Tag> },
        { key: 'c', label: '代码', children: row.code },
        { key: 'n', label: '名称', children: row.name },
        { key: 's', label: '策略', children: row.strategy_id },
        { key: 'sig', label: '信号', children: row.signal },
        { key: 'st', label: '池状态', children: row.status },
        { key: 'pd', label: '池日', children: row.pool_date },
        { key: 'dd', label: '数据日', children: row.data_date },
      ]} />
      <Card size="small" title="选池理由">{row.selection_reason || '—'}</Card>
      {evidence ? (
        <>
          <Card size="small" title="证据等级">
            <Space><Tag color={evidence.evidence_level === 'full' ? 'green' : 'red'}>{evidence.evidence_level}</Tag><Text type="secondary">缺口 {evidence.gap_count ?? 0}</Text></Space>
          </Card>
          <Descriptions size="small" column={1} bordered items={[
            { key: 'ind', label: '行业', children: evidence.industry?.display || evidence.industry?.status || '—' },
            { key: 'ann', label: '公告', children: evidence.announcements?.display || evidence.announcements?.status || '—' },
            { key: 'news', label: '新闻', children: evidence.news?.display || evidence.news?.status || '—' },
          ]} />
          <Card size="small" title="卡事实 · thesis">{card.thesis || '—'}</Card>
          <Card size="small" title="卡事实 · 风险">{card.risks || '—'}</Card>
          <Card size="small" title="KB 方法引用">
            {(evidence.method_refs || []).map((r: AnyRow) => (
              <div key={r.video_id} style={{ marginBottom: 6 }}>
                <Text strong>{r.title}</Text>
                <div><Text type="secondary">{r.video_id} · {r.source}</Text></div>
              </div>
            ))}
          </Card>
          <Card size="small" title="缺口">
            {(evidence.gaps || []).length ? evidence.gaps.map((g: AnyRow, i: number) => (
              <div key={i}><Tag color="orange">{g.kind}</Tag>{g.field} · {g.reason}</div>
            )) : <Text type="secondary">无缺口</Text>}
          </Card>
        </>
      ) : <Alert type="warning" showIcon title="未匹配到证据包条目" description="该票可能在证据包生成后变化，或证据缺失。" />}
    </Space>
  );
}
