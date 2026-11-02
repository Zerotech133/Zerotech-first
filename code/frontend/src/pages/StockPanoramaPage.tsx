import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Alert, Button, Card, Col, Descriptions, Empty, List, Row, Space, Table, Tag, Typography } from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';
import { zhReason, zhSignal, zhStatus } from '../utils/zhLabels';

const { Text, Title } = Typography;

function token() {
  return localStorage.getItem('zhice_token') || '';
}

export default function StockPanoramaPage() {
  const { market = 'cn', code = '' } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    const t = token();
    fetch(`/api/v1/stock-panorama?market=${encodeURIComponent(market.toUpperCase())}&code=${encodeURIComponent(code)}`, {
      headers: t ? { Authorization: `Bearer ${t}` } : {},
    })
      .then(async (r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(setData)
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  };

  useEffect(load, [market, code]);

  if (loading) return <Card loading />;
  if (err) return <Alert type="error" showIcon title="个股全景加载失败" description={err} />;
  if (!data) return <Empty description="无数据" />;

  const q = data.sections?.quote?.data;
  const card = data.sections?.card?.data;
  const evidence = data.sections?.evidence?.data;
  const base = data.sections?.base_signal?.data;
  const val = data.sections?.valuation?.data;
  const fin = data.sections?.fina?.data;
  const inc = data.sections?.income?.data;
  const bal = data.sections?.balance?.data;
  const ind = data.sections?.industry?.data;
  const anns = data.sections?.announcements?.data;
  const flow = data.sections?.flow?.data;
  const margin = data.sections?.margin?.data;
  const cyq = data.sections?.cyq?.data;
  const research = data.sections?.research?.data;
  const limit = data.sections?.limit?.data;
  const name = data.identity?.name || q?.name || card?.name || data.sections?.acad1?.items?.[0]?.name || code;
  const covered = data.summary?.sections_covered || [];
  const missing = data.summary?.sections_missing || [];

  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(`/${market}/replay?tab=flow`)}>返回指挥中心</Button>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        <Tag color="blue">{market.toUpperCase()}</Tag>
        <Tag color="green">只读</Tag>
      </Space>
      <Title level={3} style={{ marginTop: 0 }}>{name && name !== code ? `${name} · ${code}` : code}</Title>
      {base && (
        <Space wrap style={{ marginBottom: 8 }}>
          <Tag color={base.base_state === 'BUY' ? 'red' : base.base_state === 'SELL' ? 'green' : base.base_state === 'AVOID' ? 'orange' : 'default'}>
            基础信号：{base.base_state === 'BUY' ? '买点' : base.base_state === 'SELL' ? '卖点' : base.base_state === 'AVOID' ? '回避' : '观察'}
          </Tag>
          <Text type="secondary" style={{ fontSize: 12 }}>{base.trigger_rule} · 触发价 {base.trigger_price ?? '—'} · 数据日 {base.data_date || base.trade_date || '—'}</Text>
        </Space>
      )}
      <Space wrap style={{ marginBottom: 12 }}>
        {covered.map((k: string) => <Tag key={k} color="green">{k} 已接入</Tag>)}
        {missing.map((k: string) => <Tag key={k} color="red">{k} 缺失</Tag>)}
      </Space>

      {data.note && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }} title="范围说明" description={data.note} />
      )}
      {data.summary?.gaps?.length > 0 && data.scope !== 'unknown' && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }} title="数据缺口" description={data.summary.gaps.join('；')} />
      )}

      <Row gutter={12}>
        <Col xs={24} lg={8}>
          <Card size="small" title="行情" style={{ marginBottom: 12 }}>
            {q ? <Descriptions size="small" column={1} items={[
              { key: 'date', label: '交易日', children: q.trade_date },
              { key: 'close', label: '收盘', children: q.close },
              { key: 'pre', label: '昨收', children: q.pre_close ?? '—' },
              { key: 'ohlc', label: '开/高/低', children: `${q.open ?? '—'} / ${q.high ?? '—'} / ${q.low ?? '—'}` },
              { key: 'vol', label: '成交量', children: q.volume ?? '—' },
              { key: 'turn', label: '成交额', children: q.turnover ?? '—' },
            ]} /> : <Empty description="行情缺失" />}
          </Card>
          <Card size="small" title="证据状态" style={{ marginBottom: 12 }}>
            <Space><Tag color={data.sections?.evidence?.status === 'covered' ? 'green' : 'red'}>{data.sections?.evidence?.status}</Tag><Text type="secondary">来源：ACAD1 证据包</Text></Space>
          </Card>
        </Col>

        <Col xs={24} lg={16}>
          <Card size="small" title="策略命中" style={{ marginBottom: 12 }}>
            <Table size="small" rowKey={(r: any, i?: number) => `${r.pool_date}-${r.code}-${i}`}
              dataSource={[...(data.sections?.selection_pool?.items || []), ...(data.sections?.picks?.items || [])]}
              pagination={false}
              columns={[
                { title: '日期', render: (_: any, r: any) => r.pool_date || r.report_date, width: 105 },
                { title: '策略', render: (_: any, r: any) => data.strategy_names?.[r.strategy_id || r.source_strategies] || r.strategy_id || r.source_strategies },
                { title: '信号', render: (_: any, r: any) => zhSignal(r.signal_type || r.signal_types) },
                { title: '分数', render: (_: any, r: any) => r.max_score ?? r.score ?? '—', width: 75 },
                { title: '理由', render: (_: any, r: any) => zhReason(r.reason) },
              ]} />
          </Card>

          <Card size="small" title="ACAD1 / 研究卡" style={{ marginBottom: 12 }}>
            {data.sections?.acad1?.items?.length ? (
              <Table size="small" rowKey={(r: any) => `${r.pool_date}-${r.code}`} dataSource={data.sections.acad1.items} pagination={false}
                columns={[
                  { title: '池日', dataIndex: 'pool_date', width: 105 },
                  { title: '策略', dataIndex: 'strategy_id' },
                  { title: '信号', dataIndex: 'signal' },
                  { title: '状态', dataIndex: 'status', width: 80, render: (v: string) => zhStatus(v) },
                ]} />
            ) : <Text type="secondary">未进 ACAD1 池</Text>}
            {card && (
              <Descriptions size="small" column={1} bordered style={{ marginTop: 10 }} items={[
                { key: 'dir', label: '卡方向/评级', children: `${card.direction || '—'} / ${card.rating || '—'}` },
                { key: 'thesis', label: '卡事实', children: card.thesis || '—' },
                { key: 'risk', label: '风险', children: card.risks || '—' },
                { key: 'inv', label: '失效条件', children: card.invalidation || '—' },
                { key: 'status', label: '卡状态', children: card.status || '—' },
              ]} />
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={12}>
        <Col xs={24} lg={12}>
          <Card size="small" title="候选命中" style={{ marginBottom: 12 }}>
            {data.sections?.candidate?.items?.length ? (
              <Table size="small" rowKey={(r: any, i?: number) => `${r.pool_id}-${r.ts_code}-${i}`} dataSource={data.sections.candidate.items} pagination={false}
                columns={[
                  { title: '池日', dataIndex: 'pool_date', width: 105 },
                  { title: '策略', render: (_: any, r: any) => data.strategy_names?.[r.strategy_id] || r.strategy_id || '—' },
                  { title: '分数', dataIndex: 'candidate_score', width: 75 },
                  { title: '理由', dataIndex: 'candidate_reason', ellipsis: true },
                ]} />
            ) : <Text type="secondary">候选资产无命中</Text>}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="反馈" style={{ marginBottom: 12 }}>
            {data.sections?.feedback?.items?.length ? (
              <Table size="small" rowKey={(r: any, i?: number) => `${r.pool_date}-${i}`} dataSource={data.sections.feedback.items} pagination={false}
                columns={[
                  { title: '池日', dataIndex: 'pool_date', width: 105 },
                  { title: '决策', dataIndex: 'decision', width: 65 },
                  { title: '理由', dataIndex: 'reason', ellipsis: true },
                  { title: '结果', dataIndex: 'result', width: 80 },
                ]} />
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无反馈" />}
          </Card>
        </Col>
      </Row>

      <Row gutter={12}>
        <Col xs={24} lg={12}>
          <Card size="small" title="估值 / 财务" style={{ marginBottom: 12 }}>
            <Descriptions size="small" column={2} items={[
              { key: 'pe', label: 'PE(TTM)', children: val?.pe_ttm ?? '—' },
              { key: 'pb', label: 'PB', children: val?.pb ?? '—' },
              { key: 'mv', label: '总市值', children: val?.total_mv ?? '—' },
              { key: 'roe', label: 'ROE', children: fin?.roe ?? '—' },
              { key: 'debt', label: '资产负债率', children: fin?.debt_to_assets ?? '—' },
              { key: 'rev', label: '营收', children: inc?.total_revenue ?? '—' },
              { key: 'np', label: '归母净利', children: inc?.n_income_attr_p ?? inc?.n_income ?? '—' },
              { key: 'eps', label: 'EPS', children: inc?.basic_eps ?? '—' },
              { key: 'assets', label: '总资产', children: bal?.total_assets ?? '—' },
              { key: 'liab', label: '总负债', children: bal?.total_liab ?? '—' },
            ]} />
            <Text type="secondary" style={{ fontSize: 11 }}>
              估值日 {val?.trade_date || '—'}；财务报告期 {inc?.end_date || fin?.end_date || '—'}；公告日 {inc?.ann_date || fin?.ann_date || '—'}
            </Text>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="行业 / 公告" style={{ marginBottom: 12 }}>
            <Descriptions size="small" column={1} items={[
              { key: 'ind', label: '申万一级', children: ind?.index_code || evidence?.industry?.display || '—' },
              { key: 'in', label: '纳入日期', children: ind?.in_date || '—' },
            ]} />
            {Array.isArray(anns) && anns.length > 0 ? (
              <List size="small" dataSource={anns.slice(0, 6)} renderItem={(a: any) => (
                <List.Item>
                  <Space orientation="vertical" size={0} style={{ width: '100%' }}>
                    <Text style={{ fontSize: 12 }} ellipsis>{a.title}</Text>
                    <Text type="secondary" style={{ fontSize: 11 }}>{a.ann_date} · {a.name}</Text>
                  </Space>
                </List.Item>
              )} />
            ) : <Text type="secondary">暂无公告</Text>}
          </Card>
        </Col>
      </Row>

      {(flow || margin || cyq || research?.length) && (
        <Card size="small" title="资金 / 筹码 / 研报" style={{ marginBottom: 12 }}>
          <Row gutter={12}>
            <Col xs={24} lg={12}>
              <Descriptions size="small" column={1} items={[
                { key: 'mf', label: '主力净流入', children: flow?.net_mf_amount ?? '—' },
                { key: 'margin', label: '两融余额', children: margin?.rzrqye ?? '—' },
                { key: 'win', label: '获利盘', children: cyq?.winner_rate != null ? `${cyq.winner_rate}%` : '—' },
                { key: 'cost', label: '50%成本', children: cyq?.cost_50pct ?? '—' },
                { key: 'limit', label: '最近涨跌停', children: limit ? `${limit.trade_date} ${limit.limit === 'U' ? '涨停' : limit.limit === 'D' ? '跌停' : limit.limit}` : '—' },
              ]} />
            </Col>
            <Col xs={24} lg={12}>
              <Text strong style={{ fontSize: 12 }}>研报</Text>
              {research?.length ? <List size="small" dataSource={research.slice(0, 5)} renderItem={(r: any) => (
                <List.Item>
                  <Text style={{ fontSize: 12 }} ellipsis>{r.title}</Text>
                  <Text type="secondary" style={{ fontSize: 11 }}>{r.trade_date} · {r.inst_csname || r.author || ''}</Text>
                </List.Item>
              )} /> : <Text type="secondary">暂无研报</Text>}
            </Col>
          </Row>
        </Card>
      )}

      {evidence && (
        <Card size="small" title="证据明细" style={{ marginBottom: 12 }}>
          <Descriptions size="small" column={1} bordered items={[
            { key: 'level', label: '证据等级', children: evidence.evidence_level },
            { key: 'ind', label: '行业', children: evidence.industry?.display || evidence.industry?.status || '—' },
            { key: 'ann', label: '公告', children: evidence.announcements?.display || evidence.announcements?.status || '—' },
            { key: 'news', label: '新闻', children: evidence.news?.display || evidence.news?.status || '—' },
            { key: 'gaps', label: '缺口', children: (evidence.gaps || []).map((g: any) => `${g.kind}:${g.field}`).join('；') || '无' },
          ]} />
        </Card>
      )}
    </div>
  );
}
