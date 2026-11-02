import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Alert, Button, Card, Segmented, Space, Table, Tag, Typography } from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';

const { Text, Title } = Typography;

export default function SelectedStocksPage() {
  const { market = 'cn' } = useParams();
  const navigate = useNavigate();
  const [scope, setScope] = useState<'acad1' | 'picks'>('acad1');
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    const t = localStorage.getItem('zhice_token') || '';
    fetch(`/api/v1/stock-panorama/selected?market=${market.toUpperCase()}&scope=${scope}`, {
      headers: t ? { Authorization: `Bearer ${t}` } : {},
    })
      .then(async (r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(setData)
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  };

  useEffect(load, [market, scope]);

  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(`/${market}/replay?tab=flow`)}>返回指挥中心</Button>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        <Tag color="blue">{market.toUpperCase()}</Tag>
        <Segmented
          value={scope}
          onChange={(v) => setScope(v as any)}
          options={[{ label: 'ACAD1 池', value: 'acad1' }, { label: '发布池', value: 'picks' }]}
        />
      </Space>
      <Title level={3} style={{ marginTop: 0 }}>选出来的股票 · 多维数据</Title>
      <Text type="secondary">数据仓估值/财务/行业/公告 + 策略 + 证据；点击行打开个股全景。缺数据显示 —，不补假值。</Text>

      {err && <Alert type="error" showIcon style={{ margin: '12px 0' }} title="加载失败" description={err} />}
      {data && (
        <Space wrap style={{ margin: '12px 0' }}>
          <Tag color="blue">池日 {data.pool_date || '—'}</Tag>
          <Tag color="green">共 {data.coverage?.total ?? 0}</Tag>
          <Tag color="cyan">数据仓覆盖 {data.coverage?.dw_covered ?? 0}</Tag>
          <Tag color={data.coverage?.dw_missing ? 'orange' : 'default'}>缺失 {data.coverage?.dw_missing ?? 0}</Tag>
        </Space>
      )}

      <Card size="small">
        <Table
          size="small"
          rowKey="code"
          loading={loading}
          dataSource={data?.stocks || []}
          pagination={false}
          onRow={(r: any) => ({ onClick: () => navigate(r.panorama_path), style: { cursor: 'pointer' } })}
          columns={[
            { title: '代码', dataIndex: 'code', width: 110, fixed: 'left' },
            { title: '名称', dataIndex: 'name', width: 110, ellipsis: true },
            { title: '策略来源', dataIndex: 'strategy_name', width: 150, ellipsis: true, render: (v: string, r: any) => <span title={r.strategy_id}>{v || r.strategy_id}</span> },
            { title: '信号', dataIndex: 'signal', width: 90, ellipsis: true },
            { title: '更新时间', dataIndex: 'selected_at', width: 140, render: (v: string) => v ? String(v).slice(0, 16).replace('T', ' ') : '—' },
            { title: '数据日', dataIndex: 'data_date', width: 100, render: (v: string) => v || '—' },
            { title: '评分', dataIndex: 'score', width: 65 },
            { title: 'PE(TTM)', dataIndex: 'pe_ttm', width: 80, render: (v: any) => v ?? '—' },
            { title: 'PB', dataIndex: 'pb', width: 65, render: (v: any) => v ?? '—' },
            { title: 'ROE', dataIndex: 'roe', width: 70, render: (v: any) => v ?? '—' },
            { title: '负债率', dataIndex: 'debt_to_assets', width: 80, render: (v: any) => v ?? '—' },
            { title: '营收', dataIndex: 'revenue', width: 110, render: (v: any) => v == null ? '—' : Number(v).toLocaleString() },
            { title: '主力净流入', dataIndex: 'net_mf_amount', width: 100, render: (v: any) => v == null ? '—' : Number(v).toLocaleString() },
            { title: '两融余额', dataIndex: 'margin_balance', width: 105, render: (v: any) => v == null ? '—' : Number(v).toLocaleString() },
            { title: '获利盘', dataIndex: 'winner_rate', width: 72, render: (v: any) => v == null ? '—' : `${v}%` },
            { title: '研报', dataIndex: 'research_count', width: 55 },
            { title: '行业', dataIndex: 'industry_code', width: 95, render: (v: any) => v ?? '—' },
            { title: '公告', dataIndex: 'announcement_count', width: 60 },
            { title: '证据', dataIndex: 'evidence_level', width: 70, render: (v: any) => v ? <Tag color={v === 'full' ? 'green' : 'orange'}>{v}</Tag> : '—' },
            { title: '缺口', dataIndex: 'missing_dimensions', width: 160, render: (v: string[]) => (v && v.length) ? v.map((x) => <Tag key={x} color="orange">{x}</Tag>) : <Tag color="green">完整</Tag> },
            { title: '全景', width: 60, render: () => <Text type="secondary">打开 →</Text> },
          ]}
          scroll={{ x: 1300 }}
        />
      </Card>
    </div>
  );
}
