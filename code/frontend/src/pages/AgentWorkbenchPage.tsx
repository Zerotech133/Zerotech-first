import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Badge, Button, Card, Col, Empty, Input, InputNumber, List, Modal, Row, Segmented, Select,
  Space, Statistic, Table, Tag, Typography, message,
} from 'antd';
import {
  ArrowLeftOutlined, CheckCircleOutlined, ClockCircleOutlined, MessageOutlined, ReloadOutlined, SendOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useMarket } from '../contexts/MarketContext';
import { strategyWorkspaceApi } from '../services/strategyWorkspaceApi';
import { zhStatus } from '../utils/zhLabels';

const { Text, Title, Paragraph } = Typography;
const { TextArea } = Input;

type AnyRow = Record<string, any>;

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

function statusTag(s: string) {
  if (s === 'answered') return <Tag color="green">已归档</Tag>;
  if (s === 'failed') return <Tag color="red">失败</Tag>;
  if (s === 'claimed') return <Tag color="blue">处理中</Tag>;
  return <Tag color="gold">待办</Tag>;
}

export default function AgentWorkbenchPage() {
  const navigate = useNavigate();
  const { market } = useMarket();
  const [overview, setOverview] = useState<AnyRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [caps, setCaps] = useState<AnyRow[]>([]);
  const [handoffs, setHandoffs] = useState<AnyRow[]>([]);
  const [autopilot, setAutopilot] = useState<AnyRow | null>(null);
  const [job, setJob] = useState<AnyRow | null>(null);
  const [pool, setPool] = useState<AnyRow[]>([]);
  const [review, setReview] = useState<AnyRow[]>([]);
  const [candidatePool, setCandidatePool] = useState<AnyRow[]>([]);
  const [strategies, setStrategies] = useState<AnyRow[]>([]);
  const [selected, setSelected] = useState<AnyRow | null>(null);
  const [result, setResult] = useState<AnyRow | null>(null);
  const [running, setRunning] = useState('');
  const [tab, setTab] = useState('activity');
  const [question, setQuestion] = useState('');
  const [sending, setSending] = useState(false);
  const [reviewTarget, setReviewTarget] = useState<AnyRow | null>(null);
  const [reviewVerdict, setReviewVerdict] = useState('观察');
  const [reviewNote, setReviewNote] = useState('');
  const [reviewSaving, setReviewSaving] = useState(false);
  const [fbOpen, setFbOpen] = useState(false);
  const [fbCode, setFbCode] = useState('');
  const [fbDecision, setFbDecision] = useState('用');
  const [fbReason, setFbReason] = useState('');
  const [fbResult, setFbResult] = useState('待跟踪');
  const [fbSaving, setFbSaving] = useState(false);
  const [supOpen, setSupOpen] = useState(false);
  const [supStrategy, setSupStrategy] = useState('');
  const [supNote, setSupNote] = useState('');
  const [supScore, setSupScore] = useState<number | null>(null);
  const [supSaving, setSupSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [capR, handR, jobR, poolR, reviewR, strategyR, overviewR, ...desks] = await Promise.all([
        getJson('/hermes/capabilities').catch(() => null),
        getJson('/agent-handoff/my?limit=50').catch(() => null),
        getJson('/hermes/job-status').catch(() => null),
        getJson('/hermes/pool/today').catch(() => null),
        getJson('/hermes/review-queue').catch(() => null),
        getJson('/hermes/strategy-list').catch(() => null),
        getJson('/hermes/overview').catch(() => null),
        ...['CN', 'HK', 'US', 'CRYPTO'].map((m) => strategyWorkspaceApi.selectionDesk(m).catch(() => null)),
      ]);
      setOverview(overviewR);
      setCandidatePool((desks.filter(Boolean) as AnyRow[]).flatMap((d) => d.pool || []));
      setCaps(capR?.tools || []);
      setHandoffs(handR?.items || []);
      setJob(jobR?.job || null);
      setPool(poolR?.items || []);
      setReview(reviewR?.items || []);
      setStrategies(strategyR?.items || []);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    getJson('/hermes/autopilot-status').then(setAutopilot).catch(() => undefined);
  }, []);

  useEffect(() => {
    const t = setInterval(() => {
      getJson('/agent-handoff/my?limit=50').then((d) => {
        setHandoffs(d?.items || []);
        setSelected((cur) => (cur ? (d.items || []).find((x: AnyRow) => x.id === cur.id) || cur : cur));
      }).catch(() => undefined);
    }, 5000);
    return () => clearInterval(t);
  }, []);

  const cnName = useCallback((sid: string) => {
    const row = strategies.find((x) => x.strategy_id === sid);
    return row?.display_name || row?.manifest?.name || sid;
  }, [strategies]);
  const marketStrategies = useMemo(() => strategies.filter((s) => s.market === market), [strategies, market]);
  const marketPool = useMemo(() => pool.filter((r) => r.market === market), [pool, market]);
  const marketReview = useMemo(() => review.filter((r) => {
    const owner = strategies.find((s) => s.strategy_id === r.strategy_id);
    return owner ? owner.market === market : market === 'CN';
  }), [review, strategies, market]);

  const members = useMemo(() => {
    const ownerOfStrategy: Record<string, string> = {};
    marketStrategies.forEach((s) => { ownerOfStrategy[s.strategy_id] = s.research_owner || '未分配'; });
    const cand: Record<string, number> = {};
    const poolCnt: Record<string, number> = {};
    candidatePool.filter((r) => r.market === market).forEach((r) => {
      const raw = String(r.strategy_id || r.source_strategies || '').split(/[,，;；+\/|]/).map((x) => x.trim()).filter(Boolean)[0] || '';
      const owner = ownerOfStrategy[raw] || '未分配';
      cand[owner] = (cand[owner] || 0) + 1;
    });
    marketPool.forEach((r) => {
      const owner = ownerOfStrategy[r.strategy_id] || '未分配';
      poolCnt[owner] = (poolCnt[owner] || 0) + 1;
    });
    const stratCnt: Record<string, number> = {};
    marketStrategies.forEach((s) => { const o = s.research_owner || '未分配'; stratCnt[o] = (stratCnt[o] || 0) + 1; });
    const seats = [
      { id: 'ACAD1', name: '司马徽', role: '军团长 / 研究院院长', duty: '终审、资源、跨部门联评', metrics: `ACAD1 ${marketPool.length} · 复核 ${marketReview.length} · 评审 ${overview?.acad1_reviews ?? '—'}` },
      { id: 'STRAT1', name: '徐庶', role: '策略研究组负责人', duty: 'CN/HK 平台策略、qpzm/dk', metrics: `策略 ${stratCnt.STRAT1 || 0} · 候选 ${cand.STRAT1 || 0} · 入池 ${poolCnt.STRAT1 || 0}` },
      { id: 'ST1', name: '程昱', role: '缠论首席', duty: '缠论策略、结构/几何判读', metrics: `策略 ${stratCnt.ST1 || 0} · 候选 ${cand.ST1 || 0} · 入池 ${poolCnt.ST1 || 0}` },
      { id: 'STRAT2', name: '庞统', role: 'US/CRYPTO 策略研究员', duty: 'US/CRYPTO 平台策略', metrics: `策略 ${stratCnt.STRAT2 || 0} · 候选 ${cand.STRAT2 || 0} · 入池 ${poolCnt.STRAT2 || 0}` },
      { id: 'BACK1', name: '王粲', role: '独立验证', duty: '回测、样本外、成本/回撤', metrics: `验证策略 ${marketStrategies.filter((s) => s.validation_owner === 'BACK1').length || '—'}` },
      { id: 'DATA1', name: '张松', role: '数据治理', duty: '数据仓、PIT、数据契约', metrics: `数据日 ${overview?.data_date || '—'} · 数据 owner ${marketStrategies.filter((s) => s.data_owner === 'DATA1').length || '—'}` },
      { id: 'AI1', name: '诸葛亮', role: '运行/自动化', duty: '每日任务、状态、告警', metrics: `任务 ${(job?.steps || []).filter((x: AnyRow) => x.status === 'success').length}/${(job?.steps || []).length} · 运行 owner ${marketStrategies.filter((s) => s.ops_owner === 'AI1').length || '—'}` },
      { id: 'PROD1', name: '黄月英', role: '产品化', duty: '页面、Hermes、验收', metrics: `产品 owner ${marketStrategies.filter((s) => s.product_owner === 'PROD1').length || '—'}` },
      { id: 'COMM1', name: '马良', role: '商业化研究', duty: '客户、交付、成本', metrics: '—（暂无数据库产出）' },
      { id: 'INTEL1', name: '郭嘉', role: '竞争情报', duty: '行业对标、开源跟踪', metrics: '—（暂无数据库产出）' },
      { id: 'LUSU1', name: '阚泽', role: '下游反馈/入口', duty: '客户入口、反馈', metrics: `反馈 ${overview?.feedback ?? 0}/${overview?.feedback_total ?? 0} · 督导 ${overview?.supervision ?? 0}/${overview?.supervision_total ?? 0}` },
    ];
    return seats;
  }, [marketStrategies, candidatePool, marketPool, market, review, overview, job]);

  const todo = useMemo(() => handoffs.filter((h) => ['pending', 'claimed'].includes(h.status)), [handoffs]);
  const archive = useMemo(() => handoffs.filter((h) => ['answered', 'failed'].includes(h.status)), [handoffs]);
  const shouldDo = useMemo(() => {
    const items: AnyRow[] = [];
    if (job?.status !== 'success') items.push({ key: 'job', title: '每日任务未成功', detail: job?.error || job?.status || '未上报', owner: 'AI1' });
    if (marketReview.length) items.push({ key: 'review', title: `待复核 ${marketReview.length} 张`, detail: '需要 ACAD1/研究 owner 复核', owner: 'ACAD1' });
    if (!handoffs.some((h) => h.status === 'answered')) items.push({ key: 'handoff', title: 'Hermes 交接尚无回答', detail: '可创建一条问题测试闭环', owner: 'Hermes' });
    items.push({ key: 'coverage', title: `策略产数 ${marketStrategies.filter((s) => s.lifecycle_status !== 'research').length}/${marketStrategies.length}`, detail: '研究态策略不计产数', owner: 'STRAT1/ST2' });
    return items;
  }, [job, marketReview, handoffs, marketStrategies]);

  const runCapability = async (name: string) => {
    setRunning(name);
    setResult(null);
    try {
      let path = '';
      if (name === 'zhice_strategy_list') path = '/hermes/strategy-list';
      else if (name === 'zhice_pool_today') path = '/hermes/pool/today';
      else if (name === 'zhice_review_queue') path = '/hermes/review-queue';
      else if (name === 'zhice_job_status') path = '/hermes/job-status';
      else {
        setResult({ name, data: { note: '该工具由 Hermes Agent 在回答交接时调用，前端不直接触发。' } });
        return;
      }
      const data = await getJson(path);
      setResult({ name, data });
    } catch (e: any) {
      setResult({ name, data: { error: String(e?.message || e) } });
    } finally {
      setRunning('');
    }
  };

  const submit = async () => {
    if (!question.trim()) return;
    setSending(true);
    try {
      const r = await postJson('/agent-handoff', {
        question,
        context: { source: 'agent-workbench', job: job?.status, pool: pool.length, review: review.length },
        page: 'agent',
      });
      setQuestion('');
      await load();
      const created = { id: r.id, question, status: 'pending' };
      setSelected(created);
      setTab('activity');
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setSending(false);
    }
  };

  const submitReview = async () => {
    if (!reviewTarget) return;
    setReviewSaving(true);
    try {
      await strategyWorkspaceApi.stockResearchReview({
        pool_date: reviewTarget.pool_date,
        strategy_id: reviewTarget.strategy_id,
        code: reviewTarget.code,
        verdict: reviewVerdict,
        review_note: reviewNote || `ACAD1 在工作台标记：${reviewVerdict}`,
      });
      message.success(`已记录复核：${reviewVerdict}`);
      setReviewTarget(null);
      setReviewNote('');
      await load();
    } catch (e: any) {
      message.error(`复核失败：${String(e?.message || e)}`);
    } finally {
      setReviewSaving(false);
    }
  };

  const submitFeedback = async () => {
    if (!fbCode || !fbReason.trim()) { message.warning('请选择股票并填写理由'); return; }
    setFbSaving(true);
    try {
      await strategyWorkspaceApi.selectionFeedback({
        pool_date: overview?.data_date || job?.data_date,
        code: fbCode, consumer: '鲁肃', decision: fbDecision,
        reason: fbReason, result: fbResult || '待跟踪',
      });
      message.success('反馈已记录');
      setFbOpen(false); setFbCode(''); setFbReason(''); setFbResult('待跟踪');
      await load();
    } catch (e: any) { message.error(`反馈失败：${String(e?.message || e)}`); }
    finally { setFbSaving(false); }
  };

  const submitSupervision = async () => {
    if (!supStrategy || !supNote.trim()) { message.warning('请选择策略并填写督导意见'); return; }
    setSupSaving(true);
    try {
      await strategyWorkspaceApi.selectionSupervision({
        supervision_date: overview?.data_date || job?.data_date,
        strategy_id: supStrategy, note: supNote, score: supScore,
      });
      message.success('督导已记录');
      setSupOpen(false); setSupStrategy(''); setSupNote(''); setSupScore(null);
      await load();
    } catch (e: any) { message.error(`督导失败：${String(e?.message || e)}`); }
    finally { setSupSaving(false); }
  };

  const answerOrQuestion = selected?.answer || selected?.question;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
        <div>
          <Button size="small" icon={<ArrowLeftOutlined />} onClick={() => navigate(`/${market.toLowerCase()}/replay?tab=flow`)} style={{ marginBottom: 8 }}>
            返回指挥中心
          </Button>
          <Title level={3} style={{ margin: 0 }}>司马徽</Title>
          <Text type="secondary">军团智能体 · 能力调用 / 待办 / 应办 / 已归档，全部在当前页完成</Text>
          <div style={{ marginTop: 6 }}>
            <Tag color="green">只读协同</Tag>
            <Tag>能力 {caps.length}</Tag>
            <Tag>交接 {handoffs.length}</Tag>
            <Tag color="blue">当前市场 {market}</Tag>
          </div>
        </div>
        <Space wrap>
          <Segmented
            value={tab}
            onChange={(v) => setTab(String(v))}
            options={[
              { label: '活动框', value: 'activity' },
              { label: `待办 ${todo.length}`, value: 'todo' },
              { label: `已归档 ${archive.length}`, value: 'archive' },
              { label: '能力', value: 'caps' },
            ]}
          />
          <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>
        </Space>
      </div>

      {error && <Alert type="error" showIcon style={{ marginBottom: 12 }} title="加载失败" description={error} />}

      <Row gutter={10} style={{ marginBottom: 12 }}>
        <Col xs={12} md={4}><Card size="small"><Statistic title="Hermes 能力" value={caps.length} valueStyle={{ fontSize: 20 }} /></Card></Col>
        <Col xs={12} md={4}><Card size="small"><Statistic title="待办" value={todo.length} valueStyle={{ fontSize: 20 }} /></Card></Col>
        <Col xs={12} md={4}><Card size="small"><Statistic title="已归档" value={archive.length} valueStyle={{ fontSize: 20 }} /></Card></Col>
        <Col xs={12} md={4}><Card size="small"><Statistic title="ACAD1 池" value={marketPool.length} valueStyle={{ fontSize: 20 }} /></Card></Col>
        <Col xs={12} md={4}><Card size="small"><Statistic title="待复核" value={marketReview.length} valueStyle={{ fontSize: 20 }} /></Card></Col>
        <Col xs={12} md={4}><Card size="small"><Statistic title="任务" value={job?.status || '未上报'} valueStyle={{ fontSize: 18 }} /></Card></Col>
      </Row>

      <Card size="small" title="数据对账 · 全库 / 当前市场" style={{ marginBottom: 12 }}
        extra={overview ? <Tag color="green">已对齐</Tag> : <Tag color="red">未对齐</Tag>}>
        {overview ? (
          <div>
          <Space wrap size={18}>
            <Text>数据日 <b>{overview.data_date || '—'}</b></Text>
            <Text>全库策略 <b>{overview.strategies}</b></Text>
            <Text>全库 ACAD1 <b>{overview.pool}</b></Text>
            <Text>全库复核 <b>{overview.review_draft}</b></Text>
            <Text>反馈 <b>{overview.feedback}</b> 当日 / <b>{overview.feedback_total}</b> 全库</Text>
            <Text>督导 <b>{overview.supervision}</b> 当日 / <b>{overview.supervision_total}</b> 全库</Text>
            <Text>交接 <b>{overview.handoffs?.pending ?? 0}</b> 待办 / <b>{overview.handoffs?.archived ?? 0}</b> 归档</Text>
            <Text>本市场：策略 <b>{marketStrategies.length}</b> · ACAD1 <b>{marketPool.length}</b> · 复核 <b>{marketReview.length}</b></Text>
          </Space>
          <div style={{ marginTop: 8 }}>
            <Text type="secondary" style={{ fontSize: 11 }}>
              数据源：{Object.entries(overview.sources || {}).map(([k, v]) => `${k}=${v}`).join(' · ')}
            </Text>
          </div>
          </div>
        ) : <Text type="secondary">数据对账接口未返回</Text>}
      </Card>

      <Card size="small" title="指挥部成员 · 谁负责什么、产出了什么" style={{ marginBottom: 12 }}>
        <Table
          size="small"
          rowKey="id"
          dataSource={members}
          pagination={false}
          columns={[
            { title: '席位', dataIndex: 'id', width: 80, render: (v: string) => <Text code>{v}</Text> },
            { title: '姓名', dataIndex: 'name', width: 75, render: (v: string) => <Text strong>{v}</Text> },
            { title: '职责', dataIndex: 'role', width: 150 },
            { title: '负责什么', dataIndex: 'duty' },
            { title: '产出（真实数据）', dataIndex: 'metrics' },
          ]}
        />
        <Text type="secondary" style={{ fontSize: 11 }}>来源：v2_strategy_ownership / v2_acad1_pool / v2_stock_research / v2_selection_feedback / v2_agent_handoff / 每日任务状态；无数据库指标的席位如实标 —。</Text>
      </Card>

      {tab === 'activity' && (
        <Row gutter={12}>
          <Col xs={24} lg={14}>
            <Card size="small" title="智能体活动框" style={{ marginBottom: 12 }}>
              <Space direction="vertical" style={{ width: '100%' }} size={10}>
                <Alert
                  type={job?.status === 'success' ? 'success' : 'warning'}
                  showIcon
                  title={`每日任务：${job?.status || '未上报'}`}
                  description={`数据日 ${job?.data_date || '—'}；步骤 ${(job?.steps || []).filter((s: AnyRow) => s.status === 'success').length}/${(job?.steps || []).length}；来源 ${job?.source === 'schedule' ? '定时' : '手动'}`}
                />
                <Text strong>最近活动</Text>
                {handoffs.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有智能体交接" /> : (
                  <List
                    size="small"
                    dataSource={handoffs.slice(0, 6)}
                    renderItem={(h) => (
                      <List.Item onClick={() => setSelected(h)} style={{ cursor: 'pointer' }}>
                        <List.Item.Meta
                          avatar={<Badge status={h.status === 'answered' ? 'success' : h.status === 'failed' ? 'error' : 'processing'} />}
                          title={<Space><Text strong>#{h.id}</Text>{statusTag(h.status)}<Text type="secondary">{h.page}</Text></Space>}
                          description={<Text ellipsis>{h.question}</Text>}
                        />
                      </List.Item>
                    )}
                  />
                )}
              </Space>
            </Card>

            <Card
              size="small"
              title={`待复核队列（${marketReview.length}）`}
              extra={<Tag color="orange">人工复核</Tag>}
              style={{ marginBottom: 12 }}
            >
              {marketReview.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待复核卡" /> : (
                <Table
                  size="small"
                  rowKey={(r: AnyRow) => `${r.code}-${r.strategy_id}`}
                  dataSource={marketReview.slice(0, 6)}
                  pagination={false}
                  columns={[
                    { title: '代码', dataIndex: 'code', width: 95 },
                    { title: '策略', dataIndex: 'strategy_id', ellipsis: true, render: (v: string) => cnName(v) },
                    { title: '方向', dataIndex: 'direction', width: 75 },
                    { title: '', width: 62, render: (_: any, r: AnyRow) => (
                      <Button size="small" type="link" onClick={() => { setReviewTarget(r); setReviewVerdict('观察'); setReviewNote(''); }}>处理</Button>
                    ) },
                  ]}
                />
              )}
              <Text type="secondary" style={{ fontSize: 11 }}>复核是人工动作；不自动通过/否决。</Text>
            </Card>

            <Card size="small" title="自动巡检" extra={<Tag color="blue">自动</Tag>} style={{ marginBottom: 12 }}>
              {autopilot ? (
                <Space orientation="vertical" size={4} style={{ width: '100%' }}>
                  <Text style={{ fontSize: 12 }}>运行：{autopilot.run_at}</Text>
                  <Text style={{ fontSize: 12 }}>任务：{autopilot.pipeline?.status} · 数据日 {autopilot.pipeline?.data_date || '—'}</Text>
                  <Text style={{ fontSize: 12 }}>生成待办：{autopilot.tasks?.length ?? 0} · 阻塞席位：{(autopilot.seats || []).filter((x: AnyRow) => x.status === 'blocked').map((x: AnyRow) => x.name).join('、') || '无'}</Text>
                  <Text type="secondary" style={{ fontSize: 11 }}>L2：自动巡检 + 生成待办；复核/通过/否决/反馈仍人工。</Text>
                </Space>
              ) : <Text type="secondary">尚未运行</Text>}
            </Card>

            <Card size="small" title="反馈 / 督导" extra={<Tag color="gold">人工</Tag>} style={{ marginBottom: 12 }}>
              <Space wrap>
                <Button size="small" onClick={() => setFbOpen(true)}>录入反馈</Button>
                <Button size="small" onClick={() => setSupOpen(true)}>录入督导</Button>
              </Space>
              <div><Text type="secondary" style={{ fontSize: 11 }}>仅人工录入；不自动通过/否决，不生成投资建议。</Text></div>
            </Card>

            <Card size="small" title="新建交接" style={{ marginBottom: 12 }}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <TextArea rows={3} value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="问 Hermes：例如解释今天 ACAD1 池的异常、任务是否成功、策略 owner 是否齐全……" />
                <Button type="primary" icon={<SendOutlined />} loading={sending} onClick={submit}>创建交接</Button>
                <Text type="secondary">交接进入队列后由 Hermes MCP 拉取；回答自动归档在本页。</Text>
              </Space>
            </Card>
          </Col>

          <Col xs={24} lg={10}>
            <Card size="small" title="应办" style={{ marginBottom: 12 }}>
              <List
                size="small"
                dataSource={shouldDo}
                renderItem={(x) => (
                  <List.Item>
                    <List.Item.Meta
                      avatar={<ClockCircleOutlined style={{ color: '#d4a94f' }} />}
                      title={x.title}
                      description={<Space><Text type="secondary">{x.detail}</Text><Tag>{x.owner}</Tag></Space>}
                    />
                  </List.Item>
                )}
              />
            </Card>

            <Card size="small" title="选中交接" style={{ marginBottom: 12 }}>
              {!selected ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="点左侧活动或待办查看详情" /> : (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Space><Text strong>#{selected.id}</Text>{statusTag(selected.status)}<Tag>{selected.page}</Tag></Space>
                  <Card size="small" title={selected.answer ? '回答' : '问题'}>
                    <div style={{ whiteSpace: 'pre-wrap' }}>{answerOrQuestion}</div>
                  </Card>
                  <Text type="secondary">创建：{selected.created_at} · 回答：{selected.answered_at || '—'}</Text>
                </Space>
              )}
            </Card>
          </Col>
        </Row>
      )}

      {tab === 'todo' && (
        <Card size="small" title={`待办（${todo.length}）`}>
          {todo.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有待办交接" /> : (
            <Table
              size="small"
              rowKey="id"
              dataSource={todo}
              pagination={false}
              onRow={(r) => ({ onClick: () => { setSelected(r); setTab('activity'); }, style: { cursor: 'pointer' } })}
              columns={[
                { title: '#', dataIndex: 'id', width: 60 },
                { title: '状态', dataIndex: 'status', width: 90, render: statusTag },
                { title: '来源', dataIndex: 'page', width: 90 },
                { title: '问题', dataIndex: 'question', ellipsis: true },
                { title: '创建', dataIndex: 'created_at', width: 160 },
              ]}
            />
          )}
        </Card>
      )}

      {tab === 'archive' && (
        <Card size="small" title={`已归档（${archive.length}）`}>
          {archive.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已回答/失败交接" /> : (
            <Table
              size="small"
              rowKey="id"
              dataSource={archive}
              pagination={false}
              onRow={(r) => ({ onClick: () => { setSelected(r); setTab('activity'); }, style: { cursor: 'pointer' } })}
              columns={[
                { title: '#', dataIndex: 'id', width: 60 },
                { title: '状态', dataIndex: 'status', width: 90, render: statusTag },
                { title: '来源', dataIndex: 'page', width: 90 },
                { title: '问题', dataIndex: 'question', ellipsis: true },
                { title: '回答', dataIndex: 'answer', ellipsis: true },
                { title: '回答时间', dataIndex: 'answered_at', width: 160 },
              ]}
            />
          )}
        </Card>
      )}

      <Modal
        open={!!reviewTarget}
        onCancel={() => setReviewTarget(null)}
        onOk={submitReview}
        okText="保存复核"
        cancelText="取消"
        confirmLoading={reviewSaving}
        title={reviewTarget ? `复核 · ${reviewTarget.code} · ${reviewTarget.strategy_id}` : '复核'}
      >
        {reviewTarget && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Text type="secondary">池日 {reviewTarget.pool_date} · 当前方向 {reviewTarget.direction || '—'} · 当前状态 {reviewTarget.status}</Text>
            <Select
              value={reviewVerdict}
              onChange={setReviewVerdict}
              options={[
                { value: '通过', label: '通过（仅记录复核结论）' },
                { value: '观察', label: '观察（继续跟踪）' },
                { value: '否决', label: '否决（标记 rejected）' },
              ]}
              style={{ width: '100%' }}
            />
            <Input.TextArea rows={3} value={reviewNote} onChange={(e) => setReviewNote(e.target.value)} placeholder="复核备注（会写入 review_note）" />
            <Alert type="info" showIcon title="这是人工复核动作" description="只更新研究卡状态/备注；不自动交易、不影响 ACAD1 池。" />
          </Space>
        )}
      </Modal>

      {tab === 'caps' && (
        <Row gutter={12}>
          <Col xs={24} lg={15}>
            <Card size="small" title="Hermes 当前能力" extra={<Text type="secondary">点击运行只读查询</Text>}>
              <Row gutter={[10, 10]}>
                {caps.map((c) => (
                  <Col xs={24} md={12} key={c.name}>
                    <Card size="small" hoverable onClick={() => runCapability(c.name)} style={{ cursor: 'pointer' }}>
                      <Space direction="vertical" size={2} style={{ width: '100%' }}>
                        <Space><CheckCircleOutlined style={{ color: '#52c41a' }} /><Text strong>{c.label}</Text></Space>
                        <Text type="secondary">{c.description}</Text>
                        <Text code style={{ fontSize: 10 }}>{c.name}</Text>
                        {running === c.name && <Text type="secondary">运行中...</Text>}
                      </Space>
                    </Card>
                  </Col>
                ))}
              </Row>
            </Card>
          </Col>
          <Col xs={24} lg={9}>
            <Card size="small" title="运行结果">
              {!result ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="点击左侧能力查看结果" /> : (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Tag color="blue">{result.name}</Tag>
                  <Paragraph>
                    <pre style={{ maxHeight: 520, overflow: 'auto', fontSize: 11 }}>{JSON.stringify(result.data, null, 2)}</pre>
                  </Paragraph>
                </Space>
              )}
            </Card>
          </Col>
        </Row>
      )}
      <Modal open={fbOpen} onCancel={() => setFbOpen(false)} onOk={submitFeedback} okText="保存反馈" confirmLoading={fbSaving} title="录入下游反馈">
        <Space direction="vertical" style={{ width: '100%' }}>
          <Select showSearch value={fbCode} onChange={setFbCode} style={{ width: '100%' }} placeholder="选择池内股票"
            options={marketPool.map((r: AnyRow) => ({ value: r.code, label: `${r.market} ${r.code} ${r.name || ''}` }))} />
          <Select value={fbDecision} onChange={setFbDecision} options={[{ value: '用', label: '用' }, { value: '不用', label: '不用' }]} />
          <Input.TextArea rows={3} value={fbReason} onChange={(e) => setFbReason(e.target.value)} placeholder="理由（必填）" />
          <Input value={fbResult} onChange={(e) => setFbResult(e.target.value)} placeholder="结果（默认待跟踪）" />
        </Space>
      </Modal>

      <Modal open={supOpen} onCancel={() => setSupOpen(false)} onOk={submitSupervision} okText="保存督导" confirmLoading={supSaving} title="录入 ACAD1 督导">
        <Space direction="vertical" style={{ width: '100%' }}>
          <Select showSearch value={supStrategy} onChange={setSupStrategy} style={{ width: '100%' }} placeholder="选择策略"
            options={marketStrategies.map((r: AnyRow) => ({ value: r.strategy_id, label: `${r.display_name || r.manifest?.name}（${r.strategy_id}）` }))} />
          <Input.TextArea rows={3} value={supNote} onChange={(e) => setSupNote(e.target.value)} placeholder="督导意见（必填）" />
          <InputNumber value={supScore} onChange={setSupScore} placeholder="评分（可选）" style={{ width: '100%' }} />
        </Space>
      </Modal>

    </div>
  );
}
