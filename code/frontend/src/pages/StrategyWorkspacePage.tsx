import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Card, Col, Row, Select, Statistic, Table, Tabs, Tag } from 'antd';
import { strategyWorkspaceApi, type DailyRow, type Sidecar } from '../services/strategyWorkspaceApi';
import { zhReason, zhSignal } from '../utils/zhLabels';

const HEALTH: Record<string, { color: string; text: string }> = {
  green: { color: 'green', text: '正常' },
  yellow: { color: 'gold', text: '降级' },
  red: { color: 'red', text: '异常' },
  no_task: { color: 'default', text: '未运行' },
  unknown: { color: 'default', text: '待接入/未知' },
};

const dailyCols = [
  { title: '市场', dataIndex: 'market', width: 70 },
  { title: '代码', dataIndex: 'code', width: 120 },
  { title: '名称', dataIndex: 'name', width: 140 },
  { title: '信号', dataIndex: 'signal', width: 140, render: (v: string) => zhSignal(v) },
  { title: '分数', dataIndex: 'score', width: 80 },
  { title: '理由', dataIndex: 'reason', render: (v: string) => zhReason(v) },
  { title: '数据日', dataIndex: 'data_date', width: 110 },
];

export default function StrategyWorkspacePage() {
  const { market: routeMarket } = useParams();
  const market = (routeMarket || 'cn').toUpperCase();
  const [list, setList] = useState<Sidecar[]>([]);
  const [sid, setSid] = useState('qpzm');
  const [profile, setProfile] = useState<Sidecar | null>(null);
  const [daily, setDaily] = useState<DailyRow[]>([]);
  const [obs, setObs] = useState<any[]>([]);
  const [bt, setBt] = useState<any[]>([]);
  const [err, setErr] = useState('');

  useEffect(() => {
    strategyWorkspaceApi.list()
      .then(r => setList(r.items || []))
      .catch(e => setErr(String(e)));
  }, []);

  useEffect(() => {
    if (!sid) return;
    setErr('');
    Promise.all([
      strategyWorkspaceApi.profile(sid),
      strategyWorkspaceApi.daily(sid, market),
      strategyWorkspaceApi.observations(sid),
      strategyWorkspaceApi.backtest(sid),
    ]).then(([p, d, o, b]) => {
      setProfile(p); setDaily(d.rows || []); setObs(o.rows || []); setBt(b.rows || []);
    }).catch(e => setErr(String(e)));
  }, [sid, market]);

  const h = profile ? (HEALTH[profile.health] || HEALTH.unknown) : HEALTH.unknown;

  return (
    <div>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Row gutter={12} align="middle">
          <Col>
            <span>策略：</span>
            <Select
              style={{ width: 240 }}
              value={sid}
              onChange={setSid}
              options={list.map(x => ({ value: x.strategy_id, label: `${x.display_name}（${x.strategy_id}）${x.source_kind === 'platform' ? ' · 平台' : ' · 研究院'}` }))}
            />
          </Col>
          {profile && (
            <>
              <Col><Tag color={h.color}>{h.text}</Tag></Col>
              <Col>状态：{profile.status}</Col>
              <Col>研究 owner：{profile.owner}</Col>
              <Col>运行支持：{profile.ops_support || '—'}</Col>
              <Col>数据日：{profile.data_date || '—'}</Col>
            </>
          )}
          <Col flex="auto" />
          <Col><span style={{ color: '#8aa0b8' }}>策略自治 · 不等同 ACAD1 股票池</span></Col>
        </Row>
      </Card>

      {err && <Card size="small" style={{ marginBottom: 12, color: '#ff7b72' }}>加载失败：{err}</Card>}

      {profile && (
        <Row gutter={12} style={{ marginBottom: 12 }}>
          <Col span={6}><Card size="small"><Statistic title="今日/最新选股" value={daily.length} suffix="只" /></Card></Col>
          <Col span={6}><Card size="small"><Statistic title="观察记录" value={obs.length} suffix="条" /></Card></Col>
          <Col span={6}><Card size="small"><Statistic title="回测证据" value={bt.length} suffix="条" /></Card></Col>
          <Col span={6}><Card size="small"><Statistic title="数据日" value={profile.data_date || '—'} /></Card></Col>
        </Row>
      )}

      <Tabs
        items={[
          {
            key: 'daily',
            label: '选股',
            children: (
              <Table
                size="small" rowKey={(r: DailyRow) => `${r.trade_date}-${r.code}-${r.signal}`}
                columns={dailyCols as any} dataSource={daily} pagination={{ pageSize: 20 }}
                locale={{ emptyText: '暂无选股记录（未运行/无数据）' }}
              />
            ),
          },
          {
            key: 'obs',
            label: '观察',
            children: <Table size="small" rowKey={(r: any) => `${r.signal_date}-${r.code}`}
              columns={[
                { title: '信号日', dataIndex: 'signal_date' },
                { title: '代码', dataIndex: 'code' },
                { title: '名称', dataIndex: 'name' },
                { title: 'T+1', dataIndex: 'ret_t1' },
                { title: 'T+5', dataIndex: 'ret_t5' },
                { title: 'T+10', dataIndex: 'ret_t10' },
                { title: 'T+20', dataIndex: 'ret_t20' },
                { title: '状态', dataIndex: 'status' },
              ] as any} dataSource={obs} pagination={{ pageSize: 20 }}
              locale={{ emptyText: '暂无观察记录（待观察账本计算）' }} />,
          },
          {
            key: 'bt',
            label: '回测',
            children: <Table size="small" rowKey={(r: any) => `${r.window}-${r.cost_model}`}
              columns={[
                { title: '窗口', dataIndex: 'window' },
                { title: '成本口径', dataIndex: 'cost_model' },
                { title: '指标', dataIndex: 'metrics', render: (v: any) => JSON.stringify(v) },
                { title: 'Manifest', dataIndex: 'manifest_sha' },
              ] as any} dataSource={bt} pagination={{ pageSize: 10 }}
              locale={{ emptyText: '暂无回测产物（待接 backtest API）' }} />,
          },
        ]}
      />
    </div>
  );
}
