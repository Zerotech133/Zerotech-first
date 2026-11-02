import { useEffect, useState } from 'react';
import { Alert, Button, Card, Col, Input, Modal, Row, Select, Space, Statistic, Table, Tag, message } from 'antd';
import { strategyWorkspaceApi } from '../services/strategyWorkspaceApi';

interface QueueRow {
  pool_date: string; market: string; strategy_id: string; code: string; name: string; signal: string;
  selection_reason: string; data_date: string | null;
  verdict?: string | null; reasons?: string | null; position_hint?: string | null;
}

const VERDICT_COLOR: Record<string, string> = { '通过': 'green', '观察': 'gold', '否决': 'red' };

export default function LusuDeskPage() {
  const [rows, setRows] = useState<QueueRow[]>([]);
  const [market, setMarket] = useState('CN');
  const [day, setDay] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [cur, setCur] = useState<QueueRow | null>(null);
  const [verdict, setVerdict] = useState('观察');
  const [reasons, setReasons] = useState('');
  const [risk, setRisk] = useState('');
  const [pos, setPos] = useState('');
  const [busy, setBusy] = useState(false);

  const load = () => strategyWorkspaceApi.lusuQueue()
    .then(r => { setRows(r.rows || []); setDay(r.pool_date); })
    .catch(() => undefined);

  useEffect(() => { load(); }, []);

  const openFor = (row: QueueRow, v: string) => {
    setCur(row); setVerdict(v);
    setReasons(row.reasons || '');
    setRisk(''); setPos(row.position_hint || ''); setOpen(true);
  };

  const submit = async () => {
    if (!cur) return;
    if (!reasons.trim()) { message.warning('请填写判断理由'); return; }
    setBusy(true);
    try {
      await strategyWorkspaceApi.lusuDecide({
        pool_date: cur.pool_date, strategy_id: cur.strategy_id, code: cur.code,
        name: cur.name, verdict, reasons,
        risk_checks: risk.split('\n').map(x => x.trim()).filter(Boolean),
        position_hint: pos,
      });
      message.success(`已记录：${cur.code} ${verdict}`);
      setOpen(false); load();
    } catch (e) {
      message.error(`保存失败：${String(e)}`);
    } finally { setBusy(false); }
  };

  const marketRows = rows.filter(r => r.market === market);
  const decided = marketRows.filter(r => r.verdict).length;

  return (
    <div>
      <Alert
        type="info" showIcon style={{ marginBottom: 12 }}
        title="鲁肃军团判断台"
        description="只显示 ACAD1 定稿股票池；策略候选不能直接进入。判断仅为研究结论，不执行交易、不下单。"
      />
      <Select style={{ width: 160, marginBottom: 12 }} value={market} onChange={setMarket}
        options={[{ value: 'CN', label: 'A股' }, { value: 'HK', label: '港股' }, { value: 'US', label: '美股' }, { value: 'CRYPTO', label: '加密' }]} />
      <Row gutter={12} style={{ marginBottom: 12 }}>
        <Col span={6}><Card size="small"><Statistic title="ACAD1 池子" value={marketRows.length} suffix="只" /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="池子日期" value={day || '—'} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="已判断" value={decided} suffix="项" /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="待判断" value={marketRows.length - decided} suffix="项" /></Card></Col>
      </Row>

      <Card size="small" title="池内待判（ACAD1 定稿）">
        <Table
          size="small" rowKey={(r: QueueRow) => `${r.code}-${r.signal}`}
          dataSource={marketRows} pagination={{ pageSize: 20 }}
          columns={[
            { title: '市场', dataIndex: 'market', width: 70 },
            { title: '代码', dataIndex: 'code', width: 105 },
            { title: '名称', dataIndex: 'name', width: 120 },
            { title: '来源', dataIndex: 'strategy_id', width: 80 },
            { title: '信号', dataIndex: 'signal', width: 70 },
            { title: '选池理由', dataIndex: 'selection_reason' },
            {
              title: '判断', dataIndex: 'verdict', width: 90,
              render: (v: string) => v ? <Tag color={VERDICT_COLOR[v] || 'default'}>{v}</Tag> : <Tag>待判</Tag>,
            },
            {
              title: '操作', width: 230,
              render: (_: unknown, r: QueueRow) => (
                <Space size={4}>
                  <Button size="small" onClick={() => openFor(r, '通过')}>通过</Button>
                  <Button size="small" onClick={() => openFor(r, '观察')}>观察</Button>
                  <Button size="small" danger onClick={() => openFor(r, '否决')}>否决</Button>
                </Space>
              ),
            },
          ] as any}
          locale={{ emptyText: 'ACAD1 尚未定稿池子；鲁肃无票可判' }}
        />
      </Card>

      <Modal
        open={open} onCancel={() => setOpen(false)} onOk={submit} confirmLoading={busy}
        title={`${cur?.code || ''} ${cur?.name || ''} · ${verdict}`} okText="保存判断"
      >
        <div style={{ marginBottom: 8 }}>理由（必填）</div>
        <Input.TextArea rows={3} value={reasons} onChange={e => setReasons(e.target.value)}
          placeholder="为什么通过/观察/否决" />
        <div style={{ margin: '10px 0 8px' }}>风险检查（每行一条，可选）</div>
        <Input.TextArea rows={3} value={risk} onChange={e => setRisk(e.target.value)}
          placeholder="例如：数据日过期 / 流动性不足 / 环境闸门" />
        <div style={{ margin: '10px 0 8px' }}>仓位意见（可选，仅研究）</div>
        <Input value={pos} onChange={e => setPos(e.target.value)} placeholder="例如：观察，不配仓位" />
      </Modal>
    </div>
  );
}
