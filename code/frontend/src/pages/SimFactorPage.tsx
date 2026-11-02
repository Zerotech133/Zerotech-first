import { useEffect, useState } from 'react';
import { Alert, Button, Card, Col, Input, InputNumber, Modal, Row, Select, Space, Statistic, Table, Tabs, Tag, message } from 'antd';
import { strategyWorkspaceApi } from '../services/strategyWorkspaceApi';

export default function SimFactorPage() {
  const [accounts, setAccounts] = useState<any[]>([]);
  const [sid, setSid] = useState('qpzm');
  const [pf, setPf] = useState<any>(null);
  const [factors, setFactors] = useState<any[]>([]);
  const [orderOpen, setOrderOpen] = useState(false);
  const [side, setSide] = useState('buy');
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [price, setPrice] = useState<number | null>(null);
  const [qty, setQty] = useState<number | null>(1000);
  const [reason, setReason] = useState('');
  const [factorOpen, setFactorOpen] = useState(false);
  const [fTitle, setFTitle] = useState('');
  const [fLogic, setFLogic] = useState('');
  const [fSource, setFSource] = useState('');
  const [fac, setFac] = useState<any | null>(null);
  const [fcMode, setFcMode] = useState<'consensus' | 'paper'>('consensus');
  const [fcStrategist, setFcStrategist] = useState('缠论');
  const [fcStance, setFcStance] = useState('同意');
  const [fcNote, setFcNote] = useState('');
  const [fpCode, setFpCode] = useState('');
  const [fpPrice, setFpPrice] = useState<number | null>(null);
  const [fpQty, setFpQty] = useState<number | null>(1000);
  const [fpPnl, setFpPnl] = useState<number | null>(null);

  const loadAccounts = () => strategyWorkspaceApi.simAccounts().then(r => {
    setAccounts(r.rows || []);
    if (r.rows?.length && !r.rows.find((x: any) => x.strategy_id === sid)) setSid(r.rows[0].strategy_id);
  }).catch(() => undefined);
  const loadPortfolio = (id: string) => strategyWorkspaceApi.simPortfolio(id).then(setPf).catch(() => setPf(null));
  const loadFactors = () => strategyWorkspaceApi.factors().then(r => setFactors(r.rows || [])).catch(() => undefined);
  useEffect(() => { loadAccounts(); loadFactors(); }, []);
  useEffect(() => { loadPortfolio(sid); }, [sid]);

  const submitOrder = async () => {
    if (!code || !price || !qty) { message.warning('代码/价格/数量必填'); return; }
    try {
      await strategyWorkspaceApi.simOrder({ strategy_id: sid, trade_date: new Date().toISOString().slice(0, 10), code, name, side, price, qty, reason });
      message.success('模拟委托已成交');
      setOrderOpen(false); loadPortfolio(sid);
    } catch (e) { message.error(`失败：${String(e)}`); }
  };
  const autoBuild = async () => {
    try {
      await strategyWorkspaceApi.simAutoBuild({ strategy_id: sid, position_pct: 40, top_n: 10, clear: false });
      message.success('Top10 模拟建仓完成'); loadPortfolio(sid);
    } catch (e) { message.error(`建仓失败：${String(e)}`); }
  };
  const submitFactor = async () => {
    if (!fTitle) { message.warning('因子标题必填'); return; }
    try {
      await strategyWorkspaceApi.createFactor({ title: fTitle, source: fSource, logic: fLogic, data_ref: '' });
      message.success('因子已登记'); setFactorOpen(false); setFTitle(''); setFLogic(''); loadFactors();
    } catch (e) { message.error(`失败：${String(e)}`); }
  };

  const submitConsensus = async () => {
    if (!fac) return;
    try {
      await strategyWorkspaceApi.factorConsensus({ factor_id: fac.id, strategist: fcStrategist, stance: fcStance, note: fcNote });
      message.success('共识已记录'); setFac(null); setFcNote(''); loadFactors();
    } catch (e) { message.error(`失败：${String(e)}`); }
  };
  const submitPaper = async () => {
    if (!fac || !fpCode || !fpPrice || !fpQty) { message.warning('代码/价格/数量必填'); return; }
    try {
      await strategyWorkspaceApi.factorPaper({ factor_id: fac.id, trade_date: new Date().toISOString().slice(0,10), code: fpCode, name: '', side: 'buy', price: fpPrice, qty: fpQty, pnl: fpPnl || 0, note: fcNote });
      message.success('小仓验证已记录'); setFac(null); setFpCode(''); loadFactors();
    } catch (e) { message.error(`失败：${String(e)}`); }
  };

  const account = pf?.account;
  const pos = pf?.positions || [];
  return (
    <div>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
        title="策略模拟盘 + ACAD1 因子验证台（全部模拟，不接真实资金）"
        description="策略默认总仓位 40%，前 10 平铺；可主动加仓。ACAD1 挖因子、与策略师共识后做小仓模拟验证。" />
      <Tabs items={[
        {
          key: 'sim', label: '策略模拟盘',
          children: (
            <>
              <Space style={{ marginBottom: 12 }}>
                <span>策略：</span>
                <Select style={{ width: 220 }} value={sid} onChange={setSid}
                  options={accounts.map((a: any) => ({ value: a.strategy_id, label: a.strategy_id }))} />
                <Button onClick={() => loadPortfolio(sid)}>刷新</Button>
                <Button type="primary" onClick={() => setOrderOpen(true)}>模拟下单</Button>
                <Button onClick={autoBuild}>自动建仓 40%</Button>
              </Space>
              <Row gutter={12} style={{ marginBottom: 12 }}>
                <Col span={6}><Card size="small"><Statistic title="初始资金" value={account?.initial_capital || 0} precision={0} /></Card></Col>
                <Col span={6}><Card size="small"><Statistic title="可用现金" value={account?.cash || 0} precision={0} /></Card></Col>
                <Col span={6}><Card size="small"><Statistic title="当前仓位" value={pf?.position_pct || 0} suffix="%" /></Card></Col>
                <Col span={6}><Card size="small"><Statistic title="默认目标" value={account?.base_position_pct || 40} suffix="%" /></Card></Col>
              </Row>
              <Card size="small" title="持仓" style={{ marginBottom: 12 }}>
                <Table size="small" rowKey={(r: any) => r.code} dataSource={pos} pagination={false}
                  columns={[
                    { title: '代码', dataIndex: 'code', width: 110 },
                    { title: '名称', dataIndex: 'name', width: 130 },
                    { title: '数量', dataIndex: 'qty', width: 90 },
                    { title: '成本', dataIndex: 'avg_cost', width: 90 },
                    { title: '最新价', dataIndex: 'last_price', width: 90 },
                    { title: '市值', dataIndex: 'market_value' },
                  ] as any} />
              </Card>
              <Card size="small" title="模拟委托">
                <Table size="small" rowKey={(r: any) => `${r.trade_date}-${r.code}-${r.side}-${r.price}-${r.qty}`}
                  dataSource={pf?.orders || []} pagination={{ pageSize: 10 }}
                  columns={[
                    { title: '日期', dataIndex: 'trade_date', width: 105 },
                    { title: '代码', dataIndex: 'code', width: 100 },
                    { title: '方向', dataIndex: 'side', width: 70, render: (v: string) => <Tag>{v}</Tag> },
                    { title: '价格', dataIndex: 'price', width: 80 },
                    { title: '数量', dataIndex: 'qty', width: 80 },
                    { title: '金额', dataIndex: 'amount' },
                    { title: '理由', dataIndex: 'reason' },
                  ] as any} />
              </Card>
            </>
          ),
        },
        {
          key: 'factor', label: 'ACAD1 因子验证',
          children: (
            <>
              <Button type="primary" onClick={() => setFactorOpen(true)} style={{ marginBottom: 12 }}>登记新因子</Button>
              <Table size="small" rowKey={(r: any) => r.id} dataSource={factors} pagination={false}
                columns={[
                  { title: '因子', dataIndex: 'title' },
                  { title: '来源', dataIndex: 'source', width: 140 },
                  { title: '逻辑', dataIndex: 'logic' },
                  { title: '共识数', dataIndex: 'consensus_n', width: 80 },
                  { title: '验证笔数', dataIndex: 'paper_n', width: 90 },
                  { title: '状态', dataIndex: 'status', width: 90, render: (v: string) => <Tag>{v}</Tag> },
                  {
                    title: '操作', width: 160,
                    render: (_: unknown, r: any) => (
                      <Space size={4}>
                        <Button size="small" onClick={() => { setFac(r); setFcMode('consensus'); }}>共识</Button>
                        <Button size="small" onClick={() => { setFac(r); setFcMode('paper'); }}>小仓验证</Button>
                      </Space>
                    ),
                  },
                ] as any} />
            </>
          ),
        },
        {
          key: 'tools', label: '回测工具',
          children: <Card size="small"><a href="/cn/strategies?tab=workspace">打开策略工作台（选股/观察/回测证据）</a></Card>,
        },
      ]} />
      <Modal open={orderOpen} onCancel={() => setOrderOpen(false)} onOk={submitOrder} title={`${sid} 模拟下单`}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          <div>方向<Select style={{ width: '100%' }} value={side} onChange={setSide}
            options={[{ value: 'buy', label: '买入' }, { value: 'sell', label: '卖出' }]} /></div>
          <div>代码<Input value={code} onChange={e => setCode(e.target.value)} /></div>
          <div>名称<Input value={name} onChange={e => setName(e.target.value)} /></div>
          <div>价格<InputNumber style={{ width: '100%' }} value={price} onChange={setPrice} /></div>
          <div>数量<InputNumber style={{ width: '100%' }} value={qty} onChange={setQty} /></div>
          <div>理由<Input value={reason} onChange={e => setReason(e.target.value)} /></div>
        </div>
      </Modal>
      <Modal open={factorOpen} onCancel={() => setFactorOpen(false)} onOk={submitFactor} title="登记新因子">
        <div style={{ marginBottom: 8 }}>标题<Input value={fTitle} onChange={e => setFTitle(e.target.value)} /></div>
        <div style={{ marginBottom: 8 }}>来源<Input value={fSource} onChange={e => setFSource(e.target.value)} /></div>
        <div>逻辑<Input.TextArea rows={3} value={fLogic} onChange={e => setFLogic(e.target.value)} /></div>
      </Modal>
      <Modal open={!!fac && fcMode === 'consensus'} onCancel={() => setFac(null)} onOk={submitConsensus} title={`${fac?.title || ''} · 策略师共识`}>
        <div style={{ marginBottom: 8 }}>策略师<Select style={{ width: '100%' }} value={fcStrategist} onChange={setFcStrategist}
          options={['缠论', '青萍', '量子双刀', '九维'].map(v => ({ value: v, label: v }))} /></div>
        <div style={{ marginBottom: 8 }}>态度<Select style={{ width: '100%' }} value={fcStance} onChange={setFcStance}
          options={['同意', '反对', '待议'].map(v => ({ value: v, label: v }))} /></div>
        <div>说明<Input.TextArea rows={2} value={fcNote} onChange={e => setFcNote(e.target.value)} /></div>
      </Modal>
      <Modal open={!!fac && fcMode === 'paper'} onCancel={() => setFac(null)} onOk={submitPaper} title={`${fac?.title || ''} · 小仓验证`}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          <div>代码<Input value={fpCode} onChange={e => setFpCode(e.target.value)} /></div>
          <div>价格<InputNumber style={{ width: '100%' }} value={fpPrice} onChange={setFpPrice} /></div>
          <div>数量<InputNumber style={{ width: '100%' }} value={fpQty} onChange={setFpQty} /></div>
          <div>盈亏<InputNumber style={{ width: '100%' }} value={fpPnl} onChange={setFpPnl} /></div>
          <div style={{ gridColumn: '1 / span 2' }}>说明<Input value={fcNote} onChange={e => setFcNote(e.target.value)} /></div>
        </div>
      </Modal>
    </div>
  );
}
