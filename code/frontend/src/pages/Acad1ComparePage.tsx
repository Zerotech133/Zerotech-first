import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Alert, Button, Card, Space, Table, Tag, Typography } from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';

const { Text, Title } = Typography;

const EV_COLOR: Record<string, string> = { full: 'green', partial: 'orange', none: 'red', unknown: 'default' };

export default function Acad1ComparePage() {
  const { market = 'cn' } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    const t = localStorage.getItem('zhice_token') || '';
    fetch(`/api/v1/hermes/acad1-comparison?market=${market.toUpperCase()}&limit=80`, {
      headers: t ? { Authorization: `Bearer ${t}` } : {},
    })
      .then(async (r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(setData)
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  };

  useEffect(load, [market]);

  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(`/${market}/replay?tab=flow`)}>返回指挥中心</Button>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        <Tag color="blue">{market.toUpperCase()}</Tag>
      </Space>
      <Title level={3} style={{ marginTop: 0 }}>ACAD1 候选横向比较</Title>
      <Text type="secondary">先基础 BUY，再按“策略共振 → 证据 → 评分”排序；只展示为什么入池/为什么没入配额，不自动决策。</Text>

      {err && <Alert type="error" showIcon style={{ margin: '12px 0' }} title="加载失败" description={err} />}
      {data && (
        <Space wrap style={{ margin: '12px 0' }}>
          <Tag color="blue">池日 {data.pool_date || '—'}</Tag>
          <Tag>基础 BUY 候选 {data.total ?? 0}</Tag>
          <Tag color="green">ACAD1 已入池 {data.selected ?? 0}</Tag>
          <Tag color="blue">配额 {data.quota ?? '—'} / 应入 {data.expected_top_n ?? '—'}</Tag>
          <Tag color={data.quota_shortfall ? 'red' : 'default'}>配额缺口 {data.quota_shortfall ?? 0}</Tag>
          <Tag color="orange">未入配额 {(data.total ?? 0) - (data.selected ?? 0)}</Tag>
        </Space>
      )}

      <Card size="small">
        <Table
          size="small"
          rowKey="code"
          loading={loading}
          dataSource={data?.items || []}
          pagination={{ pageSize: 30, showSizeChanger: false }}
          onRow={(r: any) => ({ onClick: () => navigate(`/${market}/stock/${encodeURIComponent(r.code)}`), style: { cursor: 'pointer' } })}
          columns={[
            { title: '排名', dataIndex: 'rank', width: 60 },
            { title: 'ACAD1', dataIndex: 'selected', width: 80, render: (v: boolean) => v ? <Tag color="green">已入池</Tag> : <Tag>未入</Tag> },
            { title: '代码', dataIndex: 'code', width: 110, render: (v: string) => <Text code>{v}</Text> },
            { title: '名称', dataIndex: 'name', width: 100, ellipsis: true },
            { title: '策略来源', dataIndex: 'strategy', width: 150, ellipsis: true },
            { title: '基础', dataIndex: 'base_state', width: 60, render: () => <Tag color="red">买点</Tag> },
            { title: '共振', dataIndex: 'resonance', width: 60 },
            { title: '证据', dataIndex: 'evidence', width: 75, render: (v: string) => <Tag color={EV_COLOR[v]}>{v}</Tag> },
            { title: '评分', dataIndex: 'score', width: 65 },
            { title: '入池/未入逻辑', dataIndex: 'reason', ellipsis: true },
          ]}
        />
      </Card>
    </div>
  );
}
