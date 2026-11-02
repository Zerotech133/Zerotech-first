import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Avatar, Button, Card, Descriptions, Drawer, Empty, Input, List, Space, Spin, Table, Tag, Typography,
} from 'antd';
import {
  ArrowLeftOutlined, CloseOutlined, MessageOutlined, ReloadOutlined, SendOutlined,
} from '@ant-design/icons';
import { useLocation, useNavigate } from 'react-router-dom';
import { strategyWorkspaceApi } from '../services/strategyWorkspaceApi';
import { useMarket } from '../contexts/MarketContext';
import { zhStatus } from '../utils/zhLabels';

const { Text, Title } = Typography;
const { TextArea } = Input;

type AnyRow = Record<string, any>;
type View = 'chat' | 'pool' | 'review' | 'strategies' | 'job';

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

async function postJson(path: string, body: AnyRow): Promise<any> {
  const token = getToken();
  const resp = await fetch('/api/v1' + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

function stageColor(s: string) {
  if (s === 'answered') return 'green';
  if (s === 'failed') return 'red';
  if (s === 'claimed') return 'blue';
  return 'gold';
}

export default function FloatingAgent() {
  const location = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<View>('chat');
  const [loading, setLoading] = useState(false);
  const [overview, setOverview] = useState<AnyRow | null>(null);
  const [strategies, setStrategies] = useState<AnyRow[]>([]);
  const [pool, setPool] = useState<AnyRow[]>([]);
  const [review, setReview] = useState<AnyRow[]>([]);
  const [job, setJob] = useState<AnyRow | null>(null);
  const [messages, setMessages] = useState<AnyRow[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [handoffId, setHandoffId] = useState<number | null>(null);
  const [detail, setDetail] = useState<AnyRow | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const { market } = useMarket();
  const inAgentPage = location.pathname.includes('/agent');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ov, st, pl, rv, jb] = await Promise.all([
        getJson('/hermes/overview').catch(() => null),
        getJson('/hermes/strategy-list').catch(() => null),
        getJson('/hermes/pool/today').catch(() => null),
        getJson('/hermes/review-queue').catch(() => null),
        getJson('/hermes/job-status').catch(() => null),
      ]);
      setOverview(ov);
      setStrategies(st?.items || []);
      setPool(pl?.items || []);
      setReview(rv?.items || []);
      setJob(jb?.job || null);
      if (!messages.length) {
        setMessages([{
          role: 'assistant',
          text: `我是司马徽。当前数据日 ${ov?.data_date || '—'}，策略 ${ov?.strategies ?? '—'} 条，ACAD1 ${marketPool.length} 只，待复核 ${marketReview.length} 张。你可以问我今日池、任务、策略或复核情况。`,
        }]);
      }
    } finally {
      setLoading(false);
    }
  }, [messages.length]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  useEffect(() => {
    if (!open) return;
    if (!handoffId) return;
    const timer = setInterval(async () => {
      try {
        const token = getToken();
        const resp = await fetch(`/api/v1/agent-handoff/${handoffId}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!resp.ok) return;
        const d = await resp.json();
        if (d.status === 'answered' || d.status === 'failed') {
          setMessages((prev) => prev.map((m) => m.handoffId === handoffId ? { ...m, pending: false } : m)
            .concat([{ role: 'assistant', text: d.answer || '（无回答）', handoffId: d.id }]));
          setHandoffId(null);
          clearInterval(timer);
        }
      } catch { /* keep */ }
    }, 3000);
    return () => clearInterval(timer);
  }, [open, handoffId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, view]);

  const byId = useMemo(() => {
    const m: Record<string, AnyRow> = {};
    strategies.forEach((s) => { m[s.strategy_id] = s; });
    return m;
  }, [strategies]);

  const cnName = useCallback((sid: string) => {
    const s = byId[sid];
    return s?.display_name || s?.manifest?.name || sid;
  }, [byId]);

  const marketStrategies = useMemo(() => strategies.filter((s) => s.market === market), [strategies, market]);
  const marketPool = useMemo(() => pool.filter((r) => r.market === market), [pool, market]);
  const marketReview = useMemo(() => review.filter((r) => {
    const owner = strategies.find((s) => s.strategy_id === r.strategy_id);
    return owner ? owner.market === market : market === 'CN';
  }), [review, strategies, market]);

  const send = async (text?: string) => {
    const q = (text ?? input).trim();
    if (!q || sending) return;
    setSending(true);
    setInput('');
    setMessages((prev) => prev.concat([{ role: 'user', text: q }]));
    try {
      const r = await postJson('/agent-handoff', {
        question: q,
        context: { source: 'floating-agent', market, overview, page: location.pathname },
        page: 'floating',
      });
      setHandoffId(r.id);
      setMessages((prev) => prev.concat([{ role: 'assistant', text: '已进入交接队列，司马徽正在处理…', pending: true, handoffId: r.id }]));
      setView('chat');
    } catch (e: any) {
      setMessages((prev) => prev.concat([{ role: 'assistant', text: `交接失败：${String(e?.message || e)}` }]));
    } finally {
      setSending(false);
    }
  };

  const quickAsk = (label: string) => {
    const prompts: Record<string, string> = {
      pool: '今天 ACAD1 池有哪些？请按市场中文列出，并指出异常。',
      review: '今天待复核的研究卡有哪些？请指出优先处理项。',
      task: '今天的每日任务成功了吗？数据日、步骤、失败点是什么？',
      strategies: '军团 12 条策略的负责人、生命周期和产出分别是什么？用中文名称说明。',
    };
    send(prompts[label] || label);
  };

  if (inAgentPage) return null;

  return (
    <>
      {!open && (
        <button
          onClick={() => setOpen(true)}
          title="司马徽"
          style={{
            position: 'fixed', right: 22, bottom: 34, zIndex: 1200,
            width: 64, height: 64, borderRadius: '50%',
            border: '1px solid rgba(212,169,79,.65)', background: 'radial-gradient(circle at 35% 30%, #3a3122, #16130d)',
            color: '#e8c27a', cursor: 'pointer', boxShadow: '0 8px 30px rgba(0,0,0,.45)',
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 1,
          }}
        >
          <MessageOutlined style={{ fontSize: 20 }} />
          <span style={{ fontSize: 10 }}>司马徽</span>
        </button>
      )}

      <Drawer
        open={open}
        onClose={() => setOpen(false)}
        placement="right"
        size={430}
        closable={false}
        styles={{ body: { padding: 0, height: '100%', display: 'flex', flexDirection: 'column' } }}
      >
        <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <Avatar style={{ background: '#3a3122', color: '#e8c27a' }}>徽</Avatar>
          <div style={{ flex: 1 }}>
            <Text strong style={{ fontSize: 15 }}>司马徽</Text>
            <div><Text type="secondary" style={{ fontSize: 11 }}>军团智能体 · 在线 · 数据日 {overview?.data_date || '—'}</Text></div>
          </div>
          <Button type="text" size="small" icon={<ReloadOutlined />} onClick={load} />
          <Button type="text" size="small" icon={<CloseOutlined />} onClick={() => setOpen(false)} />
        </div>

        {view === 'chat' ? (
          <>
            <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
              <Space wrap size={6} style={{ marginBottom: 8 }}>
                <Tag color="blue" style={{ cursor: 'pointer' }} onClick={() => quickAsk('pool')}>今日池</Tag>
                <Tag color="orange" style={{ cursor: 'pointer' }} onClick={() => quickAsk('review')}>待复核 {marketReview.length}</Tag>
                <Tag color="purple" style={{ cursor: 'pointer' }} onClick={() => quickAsk('strategies')}>策略 {marketStrategies.length}</Tag>
                <Tag color={job?.status === 'success' ? 'green' : 'red'} style={{ cursor: 'pointer' }} onClick={() => quickAsk('task')}>任务 {job?.status || '未上报'}</Tag>
              </Space>
              <Space wrap size={6} style={{ marginBottom: 10 }}>
                <Button size="small" onClick={() => setView('pool')}>池明细</Button>
                <Button size="small" onClick={() => setView('review')}>复核队列</Button>
                <Button size="small" onClick={() => setView('strategies')}>策略中文名</Button>
                <Button size="small" onClick={() => setView('job')}>任务步骤</Button>
              </Space>
              {loading ? <Spin /> : messages.map((m, i) => (
                <div key={i} style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: 10 }}>
                  <div style={{
                    maxWidth: '86%', padding: '8px 10px', borderRadius: 10,
                    background: m.role === 'user' ? 'rgba(212,169,79,.16)' : 'rgba(255,255,255,.05)',
                    border: '1px solid var(--line)', whiteSpace: 'pre-wrap', fontSize: 13,
                  }}>
                    {m.text}
                    {m.pending && <div style={{ marginTop: 4 }}><Text type="secondary" style={{ fontSize: 10 }}>待回答…</Text></div>}
                  </div>
                </div>
              ))}
              {overview && (
                <Card size="small" style={{ marginTop: 8 }}>
                  <Space wrap size={10}>
                    <Text type="secondary">策略 {overview.strategies}</Text>
                    <Text type="secondary">ACAD1 {overview.pool}</Text>
                    <Text type="secondary">复核 {overview.review_draft}</Text>
                    <Text type="secondary">反馈 {overview.feedback}/{overview.feedback_total}</Text>
                  </Space>
                </Card>
              )}
              <div ref={bottomRef} />
            </div>

            <div style={{ padding: 10, borderTop: '1px solid var(--line)' }}>
              <TextArea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onPressEnter={(e) => { if (!e.shiftKey) { e.preventDefault(); send(); } }}
                rows={2}
                placeholder="问司马徽：今天池子、任务、策略、复核…"
              />
              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6 }}>
                <Button size="small" type="link" onClick={() => navigate(`/${location.pathname.split('/').filter(Boolean)[0] || 'cn'}/agent`)}>
                  完整工作台
                </Button>
                <Button size="small" type="primary" icon={<SendOutlined />} loading={sending} onClick={() => send()}>发送</Button>
              </div>
            </div>
          </>
        ) : (
          <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
            <Button size="small" icon={<ArrowLeftOutlined />} onClick={() => { setView('chat'); setDetail(null); }} style={{ marginBottom: 8 }}>返回对话</Button>
            {detail ? (
              <DetailView detail={detail} cnName={cnName} onBack={() => setDetail(null)} />
            ) : (
              <>
                {view === 'pool' && <PoolView rows={marketPool} cnName={cnName} onRow={setDetail} />}
                {view === 'review' && <ReviewView rows={marketReview} cnName={cnName} onRow={setDetail} />}
                {view === 'strategies' && <StrategyView rows={marketStrategies} onRow={setDetail} />}
                {view === 'job' && <JobView job={job} />}
              </>
            )}
          </div>
        )}
      </Drawer>
    </>
  );
}

function DetailView({ detail, cnName, onBack }: { detail: AnyRow; cnName: (s: string) => string; onBack: () => void }) {
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Button size="small" icon={<ArrowLeftOutlined />} onClick={onBack}>返回列表</Button>
      <Card size="small" title={detail.name || detail.code || detail.strategy_id || '详情'}>
        <Descriptions size="small" column={1} bordered items={Object.entries(detail).slice(0, 14).map(([k, v]) => ({
          key: k, label: k, children: typeof v === 'object' ? JSON.stringify(v) : String(v ?? '—'),
        }))} />
      </Card>
      {detail.strategy_id && <Text type="secondary">策略中文名：{cnName(detail.strategy_id)}</Text>}
    </Space>
  );
}

function PoolView({ rows, cnName, onRow }: { rows: AnyRow[]; cnName: (s: string) => string; onRow: (r: AnyRow) => void }) {
  return (
    <Table
      size="small"
      rowKey={(r: AnyRow) => `${r.market}-${r.code}`}
      dataSource={rows}
      pagination={{ pageSize: 8 }}
      onRow={(r) => ({ onClick: () => onRow(r), style: { cursor: 'pointer' } })}
      columns={[
        { title: '市场', dataIndex: 'market', width: 70 },
        { title: '代码', dataIndex: 'code', width: 95 },
        { title: '名称', dataIndex: 'name', ellipsis: true },
        { title: '策略', dataIndex: 'strategy_id', render: (v: string) => <Text>{cnName(v)}</Text> },
        { title: '信号', dataIndex: 'signal', width: 80, ellipsis: true },
      ]}
    />
  );
}

function ReviewView({ rows, cnName, onRow }: { rows: AnyRow[]; cnName: (s: string) => string; onRow: (r: AnyRow) => void }) {
  return (
    <Table
      size="small"
      rowKey={(r: AnyRow) => `${r.code}-${r.strategy_id}`}
      dataSource={rows}
      pagination={{ pageSize: 8 }}
      onRow={(r) => ({ onClick: () => onRow(r), style: { cursor: 'pointer' } })}
      columns={[
        { title: '代码', dataIndex: 'code', width: 95 },
        { title: '策略', dataIndex: 'strategy_id', render: (v: string) => <Text>{cnName(v)}</Text> },
        { title: '方向', dataIndex: 'direction', width: 70 },
        { title: '状态', dataIndex: 'status', width: 70, render: (v: string) => zhStatus(v) },
      ]}
    />
  );
}

function StrategyView({ rows, onRow }: { rows: AnyRow[]; onRow: (r: AnyRow) => void }) {
  return (
    <Table
      size="small"
      rowKey="strategy_id"
      dataSource={rows}
      pagination={{ pageSize: 8 }}
      onRow={(r) => ({ onClick: () => onRow(r), style: { cursor: 'pointer' } })}
      columns={[
        { title: '策略中文名', render: (_: any, r: AnyRow) => <Text>{r.display_name || r.manifest?.name}</Text> },
        { title: 'ID', dataIndex: 'strategy_id', width: 115, render: (v: string) => <Text code style={{ fontSize: 10 }}>{v}</Text> },
        { title: '负责人', dataIndex: 'research_owner', width: 85 },
        { title: '生命周期', dataIndex: 'lifecycle_status', width: 80 },
      ]}
    />
  );
}

function JobView({ job }: { job: AnyRow | null }) {
  if (!job) return <Empty description="暂无任务状态" />;
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Alert type={job.status === 'success' ? 'success' : 'error'} showIcon title={`任务：${job.status}`} description={`数据日 ${job.data_date}；来源 ${job.source}`} />
      <List
        size="small"
        dataSource={job.steps || []}
        renderItem={(s: AnyRow) => (
          <List.Item>
            <Space><Tag color={s.status === 'success' ? 'green' : 'red'}>{s.status}</Tag><Text>{s.name}</Text></Space>
          </List.Item>
        )}
      />
    </Space>
  );
}
