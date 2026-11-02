import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { Alert, Button, Card, Segmented, Space, Table, Tag, Typography } from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';

const { Text, Title } = Typography;

const STATE_LABEL: Record<string, string> = { BUY: '买点', SELL: '卖点', AVOID: '回避', NEUTRAL: '观察' };
const STATE_COLOR: Record<string, string> = { BUY: 'red', SELL: 'green', AVOID: 'orange', NEUTRAL: 'default' };

export default function BaseSignalsPage() {
  const { market = 'cn' } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [state, setState] = useState<string>(() => new URLSearchParams(location.search).get('state') || 'BUY');
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    const t = localStorage.getItem('zhice_token') || '';
    fetch(`/api/v1/hermes/base-signals?market=${market.toUpperCase()}&state=${state}&limit=300`, {
      headers: t ? { Authorization: `Bearer ${t}` } : {},
    })
      .then(async (r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(setData)
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  };

  useEffect(load, [market, state]);

  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(`/${market}/replay?tab=flow`)}>返回指挥中心</Button>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        <Tag color="blue">{market.toUpperCase()}</Tag>
        <Tag color="orange">A股 T+1</Tag>
        <Segmented value={state} onChange={(v) => setState(String(v))}
          options={[{ label: '买点', value: 'BUY' }, { label: '卖点', value: 'SELL' }, { label: '回避', value: 'AVOID' }, { label: '观察', value: 'NEUTRAL' }]} />
      </Space>
      <Title level={3} style={{ marginTop: 0 }}>基础信号 / 持仓卖出</Title>
      <Text type="secondary">买入预警：全市场 MA 买点；卖出预警：只对当前 OPEN 持仓（实盘/模拟同一口径），不对未持仓股票发卖出。A股卖出严格遵循 T+1：买入当日不生成卖出预警。先基础，后增强，不自动交易。</Text>

      {err && <Alert type="error" showIcon style={{ margin: '12px 0' }} title="加载失败" description={err} />}
      <Card size="small" style={{ marginTop: 12 }}>
        <Table
          size="small"
          rowKey={(r: any) => `${r.market}-${r.code}-${r.trade_date}`}
          loading={loading}
          dataSource={data?.items || []}
          pagination={{ pageSize: 50, showSizeChanger: false }}
          onRow={(r: any) => ({ onClick: () => navigate(`/${market}/stock/${encodeURIComponent(r.code)}`), style: { cursor: 'pointer' } })}
          columns={[
            { title: '状态', dataIndex: 'base_state', width: 75, render: (v: string) => <Tag color={STATE_COLOR[v]}>{STATE_LABEL[v] || v}</Tag> },
            { title: '代码', dataIndex: 'code', width: 110, render: (v: string) => <Text code>{v}</Text> },
            { title: '名称', dataIndex: 'name', width: 110, ellipsis: true, render: (v: string) => v || '—' },
            { title: '触发规则', dataIndex: 'trigger_rule', ellipsis: true },
            { title: '触发价', dataIndex: 'trigger_price', width: 90, render: (v: any) => v ?? '—' },
            { title: '入场区间', width: 120, render: (_: any, r: any) => (r.entry_low != null ? `${r.entry_low}~${r.entry_high}` : '—') },
            { title: '止损', dataIndex: 'stop_price', width: 80, render: (v: any) => v ?? '—' },
            { title: '数据日', dataIndex: 'data_date', width: 100, render: (v: any) => v || '—' },
            { title: '来源', dataIndex: 'source', width: 100 },
          ]}
        />
      </Card>
      <Text type="secondary" style={{ fontSize: 11 }}>仅展示前 300 条；完整信号在 v2_base_signal，按市场/代码可查。</Text>
    </div>
  );
}
