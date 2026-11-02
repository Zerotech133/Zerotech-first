import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, Card, Col, Input, InputNumber, Modal, Row, Select, Space, Statistic, Table, Tabs, Tag, message } from 'antd';
import { strategyWorkspaceApi } from '../services/strategyWorkspaceApi';
import { zhReason, zhSignal, zhStatus } from '../utils/zhLabels';

export default function SelectionDeskPage() {
  const navigate = useNavigate();
  const [data, setData] = useState<any>({ reports: [], pool: [], feedback: [], supervision: [] });
  const [market, setMarket] = useState('CN');
  const [loading, setLoading] = useState(true);
  const [fb, setFb] = useState<any | null>(null);
  const [decision, setDecision] = useState('用');
  const [consumer, setConsumer] = useState('鲁肃');
  const [fbReason, setFbReason] = useState('');
  const [fbResult, setFbResult] = useState('');
  const [sup, setSup] = useState<any | null>(null);
  const [supNote, setSupNote] = useState('');
  const [supScore, setSupScore] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);

  // P2-2：日报/池子按市场独立，切换市场 tab 时按 market 重新拉取（后端不再塞 MULTI）。
  const load = (m: string = market) => {
    setLoading(true);
    return strategyWorkspaceApi.selectionDesk(m)
      .then(setData).catch(() => undefined).finally(() => setLoading(false));
  };
  useEffect(() => { load(market); }, [market]);

  const reports = data.reports || [];
  const pool = data.pool || [];
  const poolDetail = data.pool_detail || [];
  const coverage = data.coverage || [];
  const reportedCount = new Set(reports.map((r: any) => r.strategy_id)).size;
  const feedback = data.feedback || [];
  const used = feedback.filter((x: any) => x.decision === '用').length;
  const rejected = feedback.filter((x: any) => x.decision === '不用').length;

  const submitFeedback = async () => {
    if (!fb) return;
    setSaving(true);
    try {
      await strategyWorkspaceApi.selectionFeedback({
        // 池子口径日可能晚于本市场日报日（如美股数据日滞后），反馈挂在池日上。
        pool_date: data.pool_date || data.date, code: fb.code, consumer, decision,
        reason: fbReason, result: fbResult,
      });
      message.success('反馈已记录');
      setFb(null); setFbReason(''); setFbResult(''); load();
    } catch (e) { message.error(`保存失败：${String(e)}`); }
    finally { setSaving(false); }
  };

  const openSup = (r: any) => {
    const cur = (data.supervision || []).find((x: any) => x.strategy_id === r.strategy_id);
    setSup(r); setSupNote(cur?.note || ''); setSupScore(cur?.score ?? null);
  };
  const submitSup = async () => {
    if (!sup) return;
    setSaving(true);
    try {
      await strategyWorkspaceApi.selectionSupervision({
        supervision_date: data.date, strategy_id: sup.strategy_id,
        note: supNote, score: supScore,
      });
      message.success('督导意见已保存');
      setSup(null); load();
    } catch (e) { message.error(`保存失败：${String(e)}`); }
    finally { setSaving(false); }
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
        <button
          onClick={() => navigate(`/${market.toLowerCase()}/replay?tab=flow`)}
          style={{ padding: '6px 12px', borderRadius: 6, border: '1px solid #d4a94f', background: 'transparent', color: '#d4a94f', cursor: 'pointer' }}
        >
          打开全景动线 · 问 Hermes
        </button>
      </div>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        title="每日选股中台：各策略盘前交日报/选股 → 统一池 → 下游反馈 → ACAD1 督导。"
        description={`${market} 市场 · 数据日：${data.date || '—'}；策略覆盖：已报 ${reportedCount}/${coverage.length}（平台策略无当日数据时显示为未报）；平台不替策略选股。`} />
      <Tabs size="small" activeKey={market} onChange={setMarket}
        items={[
          { key: 'CN', label: 'A股' },
          { key: 'HK', label: '港股' },
          { key: 'US', label: '美股' },
          { key: 'CRYPTO', label: '加密' },
        ]} style={{ marginBottom: 4 }} />
      <Row gutter={12} style={{ marginBottom: 12 }}>
        <Col span={6}><Card size="small"><Statistic title="已报分析师" value={reports.length} suffix="位" /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="今日选股" value={pool.length} suffix="只" /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="下游使用" value={used} suffix="只" /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="下游不用" value={rejected} suffix="只" /></Card></Col>
      </Row>
      <Tabs items={[
        {
          key: 'reports', label: '今日日报',
          children: (
            <Card size="small">
              <Table size="small" loading={loading} rowKey={(r: any) => `${r.report_date}-${r.strategy_id}-${r.market}`}
                dataSource={reports} pagination={false}
                columns={[
                  { title: '策略', dataIndex: 'strategy_id', width: 110 },
                  { title: '市场', dataIndex: 'market', width: 80, render: (v: string) => <Tag color="blue">{v}</Tag> },
                  { title: '分析师', dataIndex: 'analyst', width: 100 },
                  { title: '今日观点', dataIndex: 'market_view' },
                  { title: '昨日复盘', dataIndex: 'yesterday_review', width: 180 },
                  { title: '选股数', dataIndex: 'picks', width: 80 },
                  { title: '状态', dataIndex: 'status', width: 90, render: (v: string) => <Tag>{zhStatus(v)}</Tag> },
                ] as any} />
            </Card>
          ),
        },
        {
          key: 'trace', label: '候选池溯源',
          children: (
            <>
            <Card size="small" title={`策略覆盖（${coverage.length}）`} style={{ marginBottom: 12 }}>
              <Table size="small" rowKey={(r: any) => r.strategy_id} dataSource={coverage} pagination={false}
                columns={[
                  { title: '策略', dataIndex: 'strategy_id', width: 180 },
                  { title: '名称', dataIndex: 'name' },
                  { title: '来源', dataIndex: 'source', width: 90 },
                  { title: '报告日', dataIndex: 'report_date', width: 110 },
                  { title: '选股数', dataIndex: 'picks', width: 80 },
                  { title: '状态', width: 90, render: (_: unknown, r: any) => r.report_date ? <Tag color="green">已接入</Tag> : <Tag>待接入</Tag> },
                ] as any} />
            </Card>
            <Card size="small">
              <Alert type="info" showIcon style={{ marginBottom: 8 }}
                title="每只股票可溯源到：何时、哪个策略、什么信号/评分、什么原因；同一股票被多个策略选中即为多维度共振。" />
              <Table size="small" loading={loading} rowKey={(r: any) => r.code}
                dataSource={poolDetail} pagination={{ pageSize: 20 }}
                expandable={{
                  expandedRowRender: (r: any) => (
                    <div>
                      {(r.sources || []).map((x: any, i: number) => (
                        <div key={i} style={{ padding: '2px 0' }}>
                          <Tag color="blue">{x.strategy_id}</Tag>
                          <span>{x.report_date} · {zhSignal(x.signal_type)} · 分 {x.score ?? '—'} · {zhReason(x.reason)}</span>
                        </div>
                      ))}
                    </div>
                  ),
                }}
                columns={[
                  { title: '代码', dataIndex: 'code', width: 110 },
                  { title: '名称', dataIndex: 'name', width: 130 },
                  { title: '来源策略', dataIndex: 'source_strategies', width: 150 },
                  { title: '信号类型', dataIndex: 'signal_types', render: (v: string) => zhReason(v) },
                  { title: '最高分', dataIndex: 'max_score', width: 90 },
                  { title: '共振', width: 90, render: (_: unknown, r: any) => {
                    const n = (r.sources || []).length;
                    return <Tag color={n >= 2 ? 'green' : 'default'}>{n} 维</Tag>;
                  } },
                ] as any} />
            </Card>
            </>
          ),
        },
        {
          key: 'pool', label: '统一池 + 反馈',
          children: (
            <>
              <Card size="small" title="统一股票池（下游反馈）" style={{ marginBottom: 12 }}>
                <Table size="small" loading={loading} rowKey={(r: any) => r.code}
                  dataSource={pool} pagination={{ pageSize: 20 }}
                  columns={[
                    { title: '代码', dataIndex: 'code', width: 110 },
                    { title: '名称', dataIndex: 'name', width: 130 },
                    { title: '来源策略', dataIndex: 'source_strategies', width: 150 },
                    { title: '信号类型', dataIndex: 'signal_types', render: (v: string) => zhReason(v) },
                    { title: '最高分', dataIndex: 'max_score', width: 90 },
                    {
                      title: '操作', width: 210,
                      render: (_: unknown, r: any) => (
                        <Space size={4}>
                          <Button size="small" onClick={() => { setFb(r); setDecision('用'); }}>用</Button>
                          <Button size="small" danger onClick={() => { setFb(r); setDecision('不用'); }}>不用</Button>
                        </Space>
                      ),
                    },
                  ] as any} />
              </Card>
              <Card size="small" title="已录入反馈">
                <Table size="small" rowKey={(r: any, i?: number) => `${r.code}-${r.created_at || i}`}
                  dataSource={feedback} pagination={{ pageSize: 10 }}
                  columns={[
                    { title: '代码', dataIndex: 'code', width: 110 },
                    { title: '消费方', dataIndex: 'consumer', width: 90 },
                    { title: '结论', dataIndex: 'decision', width: 80 },
                    { title: '原因', dataIndex: 'reason' },
                    { title: '结果', dataIndex: 'result' },
                  ] as any} />
              </Card>
            </>
          ),
        },
        {
          key: 'boss', label: 'ACAD1老板视图',
          children: (
            <>
              <Card size="small" title="日报经验与督导" style={{ marginBottom: 12 }}>
                <Table size="small" loading={loading} rowKey={(r: any) => `${r.report_date}-${r.strategy_id}-${r.market}`}
                  dataSource={reports} pagination={false}
                  columns={[
                    { title: '策略', dataIndex: 'strategy_id', width: 110 },
                    { title: '市场', dataIndex: 'market', width: 80, render: (v: string) => <Tag color="blue">{v}</Tag> },
                    { title: '昨日复盘', dataIndex: 'yesterday_review' },
                    { title: '经验/教训', dataIndex: 'lessons' },
                    { title: '今日观点', dataIndex: 'market_view' },
                    { title: '督导', width: 90, render: (_: unknown, r: any) => <Button size="small" onClick={() => openSup(r)}>写督导</Button> },
                  ] as any} />
              </Card>
              <Card size="small" title="督导记录">
                <Table size="small" rowKey={(r: any) => `${r.supervision_date}-${r.strategy_id}`}
                  dataSource={data.supervision || []} pagination={false}
                  columns={[
                    { title: '日期', dataIndex: 'supervision_date', width: 110 },
                    { title: '策略', dataIndex: 'strategy_id', width: 110 },
                    { title: '督导意见', dataIndex: 'note' },
                    { title: '评分', dataIndex: 'score', width: 80 },
                    { title: '督导人', dataIndex: 'supervisor', width: 90 },
                  ] as any} />
              </Card>
            </>
          ),
        },
      ]} />
      <Modal open={!!fb} onCancel={() => setFb(null)} onOk={submitFeedback}
        confirmLoading={saving} title={`${fb?.code || ''} ${fb?.name || ''} · 下游反馈`}>
        <div style={{ marginBottom: 8 }}>消费方
          <Select style={{ width: '100%' }} value={consumer} onChange={setConsumer}
            options={['鲁肃', '盯盘', '交易'].map(v => ({ value: v, label: v }))} /></div>
        <div style={{ marginBottom: 8 }}>结论
          <Select style={{ width: '100%' }} value={decision} onChange={setDecision}
            options={['用', '不用'].map(v => ({ value: v, label: v }))} /></div>
        <div style={{ marginBottom: 8 }}>原因<Input.TextArea rows={2} value={fbReason} onChange={e => setFbReason(e.target.value)} /></div>
        <div style={{ marginBottom: 8 }}>结果<Input.TextArea rows={2} value={fbResult} onChange={e => setFbResult(e.target.value)} /></div>
      </Modal>
      <Modal open={!!sup} onCancel={() => setSup(null)} onOk={submitSup}
        confirmLoading={saving} title={`${sup?.strategy_id || ''} · ACAD1督导`}>
        <div style={{ marginBottom: 8 }}>督导意见<Input.TextArea rows={3} value={supNote} onChange={e => setSupNote(e.target.value)} /></div>
        <div>策略逻辑评分<InputNumber style={{ width: '100%' }} value={supScore} onChange={setSupScore} /></div>
      </Modal>
    </div>
  );
}
