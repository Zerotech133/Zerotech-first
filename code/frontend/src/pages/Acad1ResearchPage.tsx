import { useEffect, useState } from 'react';
import { Alert, Button, Card, Col, Descriptions, Empty, Input, InputNumber, Modal, Row, Select, Space, Statistic, Table, Tabs, Tag, Typography, message } from 'antd';
import { acad1ResearchApi, type Acad1EvidenceField, type Acad1EvidenceItem, type Acad1EvidenceResponse } from '../services/acad1ResearchApi';
import { strategyWorkspaceApi, type DailyRow, type PoolRow, type ReviewRow, type Sidecar } from '../services/strategyWorkspaceApi';

const { Text, Paragraph } = Typography;

interface Candidate extends DailyRow { strategy_id: string; strategy_name: string; }

const HEALTH: Record<string, { color: string; text: string }> = {
  green: { color: 'green', text: '正常' },
  yellow: { color: 'gold', text: '降级' },
  red: { color: 'red', text: '异常' },
  no_task: { color: 'default', text: '未运行' },
  unknown: { color: 'default', text: '待接入' },
};

export default function Acad1ResearchPage() {
  const [sidecars, setSidecars] = useState<Sidecar[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [pool, setPool] = useState<PoolRow[]>([]);
  const [reviews, setReviews] = useState<ReviewRow[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [decisions, setDecisions] = useState<any[]>([]);
  const [calendar, setCalendar] = useState<any[]>([]);
  const [research, setResearch] = useState<any[]>([]);
  const [editing, setEditing] = useState<any | null>(null);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  // P3-8：证据包只读页签（只 GET，不写 DB、不含通过/否决动作）
  const [evidence, setEvidence] = useState<Acad1EvidenceResponse | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [evidenceError, setEvidenceError] = useState('');

  useEffect(() => {
    strategyWorkspaceApi.list()
      .then(async r => {
        const s = (r.items || []).filter(x => x.source_kind !== 'platform');
        setSidecars(s);
        const daily = await Promise.all(s.map(async x => {
          const d = await strategyWorkspaceApi.daily(x.strategy_id);
          return (d.rows || []).map((row: DailyRow) => ({
            ...row, strategy_id: x.strategy_id, strategy_name: x.display_name,
          }));
        }));
        setCandidates(daily.flat());
      })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    Promise.allSettled([
      strategyWorkspaceApi.acad1Pool(),
      strategyWorkspaceApi.acad1Reviews(),
      strategyWorkspaceApi.acad1Stats(),
      strategyWorkspaceApi.acad1Decisions(),
      strategyWorkspaceApi.acad1Calendar(),
      strategyWorkspaceApi.stockResearchList(),
    ]).then(([p, r, s, d, cal, res]) => {
      if (p.status === 'fulfilled') setPool(p.value.rows || []);
      if (r.status === 'fulfilled') setReviews(r.value.rows || []);
      if (s.status === 'fulfilled') setStats(s.value);
      if (d.status === 'fulfilled') setDecisions(d.value.rows || []);
      if (cal.status === 'fulfilled') setCalendar(cal.value.rows || []);
      if (res.status === 'fulfilled') setResearch(res.value.rows || []);
    });
  }, []);

  const bad = sidecars.filter(x => !['green'].includes(x.health)).length;

  const refreshResearch = () => strategyWorkspaceApi.stockResearchList()
    .then(r => setResearch(r.rows || [])).catch(() => undefined);

  // P3-8：证据包只读加载；404 = 证据包未生成（空态提示，不重试、不写库）
  const loadEvidence = () => {
    setEvidenceLoading(true);
    setEvidenceError('');
    acad1ResearchApi.evidence()
      .then(d => setEvidence(d))
      .catch((e: any) => {
        const status = e && typeof e === 'object' ? (e as { status?: number }).status : undefined;
        if (status === 404) {
          setEvidenceError('证据包未生成（404）：后端未找到证据包文件，通常表示尚未生成；本页只读，不写库。');
        } else {
          setEvidenceError(`证据包读取失败：${String(e && e.message ? e.message : e)}`);
        }
      })
      .finally(() => setEvidenceLoading(false));
  };

  // 证据包按需加载：首次打开「证据包」页签时拉取（只读 GET），避免页面初始加载就弹错误。
  const onTabChange = (key: string) => {
    if (key === 'evidence' && !evidence && !evidenceLoading && !evidenceError) loadEvidence();
  };

  const saveCard = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      await strategyWorkspaceApi.stockResearchSave(editing);
      message.success('投研卡已保存');
      setEditing(null); refreshResearch();
    } catch (e) { message.error(`保存失败：${String(e)}`); }
    finally { setSaving(false); }
  };

  const reviewPass = async (r: any) => {
    try {
      await strategyWorkspaceApi.stockResearchReview({
        pool_date: r.pool_date, strategy_id: r.strategy_id, code: r.code,
        verdict: '通过', review_note: 'ACAD1 审核通过',
      });
      message.success('已审核'); refreshResearch();
    } catch (e) { message.error(`审核失败：${String(e)}`); }
  };

  const handoff = async (r: any) => {
    try {
      await strategyWorkspaceApi.stockResearchHandoff({
        pool_date: r.pool_date, strategy_id: r.strategy_id, code: r.code,
      });
      message.success('已交接盯盘'); refreshResearch();
    } catch (e) { message.error(`交接失败：${String(e)}`); }
  };

  // ── P3-8 证据包（只读）渲染辅助 ─────────────────────────────────────────
  const evidenceItems = evidence?.items || [];
  const evidenceLevelCount = (level: string): number =>
    evidence?.summary?.evidence_level?.[level]
      ?? evidenceItems.filter(x => x.evidence_level === level).length;
  const evidenceGapTotal = evidence?.summary?.gaps?.total_entries
    ?? evidenceItems.reduce((a, x) => a + (x.gap_count || 0), 0);
  const evidenceTrueMissing = evidence?.summary?.gaps?.true_missing_entries ?? 0;
  const evidenceMarkets: Record<string, number> = evidence?.summary?.pool?.by_market
    || evidenceItems.reduce((acc, x) => {
      acc[x.market] = (acc[x.market] || 0) + 1;
      return acc;
    }, {} as Record<string, number>);
  const evidenceMarketText = Object.entries(evidenceMarkets)
    .map(([m, n]) => `${m} ${n}`).join(' / ');
  const evidenceKbTotal = evidenceItems.reduce((a, x) => a + (x.method_refs?.length || 0), 0);

  const evidenceFieldTag = (f?: Acad1EvidenceField) => {
    if (!f) return <Text type="secondary">—</Text>;
    const status = f.status || 'unknown';
    const color = status === 'covered' ? 'green'
      : status === 'not_applicable' ? 'default'
        : status === 'no_match' ? 'gold' : 'red';
    return (
      <Space size={4} wrap>
        <Tag color={color}>{status}</Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>{f.display || f.reason || '—'}</Text>
      </Space>
    );
  };

  const evidenceAnnCount = (r: Acad1EvidenceItem): number =>
    Number((r.announcements?.value?.count as number | undefined) ?? 0);
  const evidenceNewsCount = (r: Acad1EvidenceItem): number =>
    Number((r.news?.value?.count as number | undefined) ?? 0);

  const renderEvidenceDetail = (r: Acad1EvidenceItem) => {
    const f = r.card?.fields;
    const refs = r.method_refs || [];
    const gaps = r.gaps || [];
    const newsSources = Object.entries(r.news?.per_source || {});
    const textStyle = { fontSize: 12 } as const;
    return (
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <Card size="small" title="卡事实（上游快照，原样引用，不解读）">
          {f ? (
            <Descriptions
              size="small" column={1} colon={false}
              items={[
                { key: 'dir', label: '方向/评级', children: `${f.direction ?? '—'} / ${f.rating ?? '—'}（评分 ${f.score ?? '—'}，状态 ${f.status ?? '—'}）` },
                { key: 'exe', label: '可执行', children: `${f.executable ?? '—'}${f.exec_note ? ` · ${f.exec_note}` : ''}` },
                { key: 'thesis', label: '核心逻辑', children: <Paragraph style={{ marginBottom: 0 }} ellipsis={{ rows: 3, tooltip: f.thesis || '' }}>{f.thesis || '—'}</Paragraph> },
                { key: 'cat', label: '催化事实', children: <Paragraph style={{ marginBottom: 0 }} ellipsis={{ rows: 2, tooltip: f.catalysts || '' }}>{f.catalysts || '—'}</Paragraph> },
                { key: 'risk', label: '风险事实', children: <Paragraph style={{ marginBottom: 0 }} ellipsis={{ rows: 2, tooltip: f.risks || '' }}>{f.risks || '—'}</Paragraph> },
                { key: 'inv', label: '失效条件', children: f.invalidation || '—' },
                { key: 'review', label: '复核备注', children: f.review_note || '—' },
              ]}
            />
          ) : <Text type="secondary">无匹配研究卡</Text>}
        </Card>
        <Card size="small" title="行业 / 公告 / 新闻（缺口如实标注）">
          <div style={{ marginBottom: 8 }}>
            <Text strong>行业：</Text>{evidenceFieldTag(r.industry)}
            {r.industry?.pit_lag_days != null && (
              <Text type="secondary" style={textStyle}>（PIT 滞后 {r.industry.pit_lag_days} 天）</Text>
            )}
          </div>
          <div style={{ marginBottom: 8 }}>
            <Text strong>公告：</Text>{evidenceFieldTag(r.announcements)}
          </div>
          <div style={{ marginBottom: 8 }}>
            <Text strong>新闻：</Text>{evidenceFieldTag(r.news)}
            {newsSources.length > 0 && (
              <div style={{ marginTop: 4 }}>
                {newsSources.map(([source, meta]) => (
                  <Tag key={source}>{source} {meta.count ?? 0} 条{meta.latest?.time ? ` · ${meta.latest.time}` : ''}</Tag>
                ))}
              </div>
            )}
            {r.news?.caveat && <div><Text type="secondary" style={textStyle}>{r.news.caveat}</Text></div>}
          </div>
          {r.pool?.selection_reason != null && (
            <div><Text strong>选池理由：</Text><Text type="secondary" style={textStyle}>{String(r.pool.selection_reason)}</Text></div>
          )}
        </Card>
        <Card size="small" title={`KB 方法引用（${refs.length}）${r.method_coverage === 'en_only' ? ' · 仅英文命中，仅作方法参考' : ''}`}>
          {refs.length === 0 ? <Text type="secondary">无引用</Text> : refs.map((ref, i) => (
            <div key={`${ref.video_id || ref.source || 'ref'}-${i}`} style={{ marginBottom: 8 }}>
              <Text strong style={textStyle}>{ref.title || ref.video_id || '（无标题）'}</Text>
              <Text type="secondary" style={textStyle}>
                ：{ref.topic_label || ref.topic || '方法'}{ref.lang ? ` · ${ref.lang}` : ''}{ref.score != null ? ` · score ${ref.score}` : ''}
              </Text>
              <Paragraph style={{ marginBottom: 0 }} ellipsis={{ rows: 2, tooltip: ref.snippet || '' }}>{ref.snippet || '—'}</Paragraph>
            </div>
          ))}
        </Card>
        <Card size="small" title={`缺口（${gaps.length}）`}>
          {gaps.length === 0 ? <Text type="secondary">无缺口</Text> : gaps.map((g, i) => (
            <div key={`${g.field}-${g.kind}-${i}`} style={{ marginBottom: 8 }}>
              <Tag color={g.kind === 'not_applicable' ? 'default' : g.kind === 'no_match' ? 'gold' : 'red'}>
                {g.field_label || g.field} · {g.kind_label || g.kind}
              </Tag>
              <div><Text type="secondary" style={textStyle}>{g.reason || '—'}</Text></div>
            </div>
          ))}
        </Card>
      </div>
    );
  };

  return (
    <div>
      <Alert
        type="warning" showIcon style={{ marginBottom: 12 }}
        title="司马徽研究院 · 研究台"
        description={`已接入今日选池 ${pool.length} 只与评审记录 ${reviews.length} 条；候选仍需 ACAD1 审核，候选 ≠ 股票池，页面不造假。`}
      />
      <Row gutter={12} style={{ marginBottom: 12 }}>
        <Col span={4}><Card size="small"><Statistic title="候选参考" value={candidates.length} suffix="条" /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="今日已选池" value={pool.length} suffix="只" /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="评审记录" value={reviews.length} suffix="项" /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="T20完成率" value={stats ? Math.round((stats.obs_done || 0) / Math.max(1, stats.obs_total || 0) * 100) : 0} suffix="%" /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="回测证据" value={stats?.bt_total || 0} suffix="条" /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="健康异常/未运行" value={bad} suffix="条" /></Card></Col>
      </Row>

      <Tabs
        onChange={onTabChange}
        items={[
          {
            key: 'pool', label: '今日选池',
            children: (
              <Card size="small">
                <Table
                  size="small" rowKey={(r: PoolRow) => `${r.pool_date}-${r.strategy_id}-${r.code}`}
                  dataSource={pool} pagination={{ pageSize: 20 }}
                  columns={[
                    { title: '池子日期', dataIndex: 'pool_date', width: 110 },
                    { title: '市场', dataIndex: 'market', width: 70 },
                    { title: '来源策略', dataIndex: 'strategy_id', width: 90 },
                    { title: '代码', dataIndex: 'code', width: 110 },
                    { title: '名称', dataIndex: 'name', width: 120 },
                    { title: '信号', dataIndex: 'signal', width: 80 },
                    { title: '选池理由', dataIndex: 'selection_reason' },
                    { title: '选中人', dataIndex: 'selected_by', width: 80 },
                    { title: '数据日', dataIndex: 'data_date', width: 105 },
                  ] as any}
                  locale={{ emptyText: 'ACAD1 今日尚未选池' }}
                />
              </Card>
            ),
          },
          {
            key: 'research', label: '投研卡',
            children: (
              <Card size="small">
                <Alert type="info" showIcon style={{ marginBottom: 8 }}
                  title="池内股票 → 投研 → 审核 → 交接盯盘；不管理策略、不下单。" />
                <Table size="small" rowKey={(r: any) => `${r.pool_date}-${r.strategy_id}-${r.code}`}
                  dataSource={research} pagination={{ pageSize: 20 }}
                  columns={[
                    { title: '市场', dataIndex: 'market', width: 70 },
                    { title: '代码', dataIndex: 'code', width: 105 },
                    { title: '名称', dataIndex: 'name', width: 110 },
                    { title: '多空', dataIndex: 'direction', width: 80 },
                    { title: '评分', dataIndex: 'score', width: 70 },
                    { title: '评级', dataIndex: 'rating', width: 60 },
                    { title: '可执行', dataIndex: 'executable', width: 90 },
                    { title: '状态', dataIndex: 'status', width: 90,
                      render: (v: string) => <Tag>{v}</Tag> },
                    {
                      title: '操作', width: 260,
                      render: (_: unknown, r: any) => (
                        <Space size={4}>
                          <Button size="small" onClick={() => setEditing({ ...r, catalysts: r.catalysts || '', invalidation: r.invalidation || '', exec_note: r.exec_note || '', thesis: r.thesis || '', risks: r.risks || '' })}>编辑</Button>
                          <Button size="small" onClick={() => reviewPass(r)}>审核通过</Button>
                          <Button size="small" type="primary" onClick={() => handoff(r)}>交接盯盘</Button>
                        </Space>
                      ),
                    },
                  ] as any}
                  locale={{ emptyText: '池内暂无投研卡，点编辑开始' }}
                />
              </Card>
            ),
          },
          {
            key: 'evidence', label: '证据包',
            children: (
              <Card
                size="small"
                title="证据包（只读）"
                extra={<Button size="small" onClick={loadEvidence} loading={evidenceLoading}>刷新只读数据</Button>}
              >
                <Alert
                  type="info" showIcon style={{ marginBottom: 8 }}
                  title="只读证据包 · 不是投资结论"
                  description={`逐项展示卡事实/行业/公告/新闻/KB 方法引用/缺口；缺口如实标注，不推断、不补造；本页不含通过/否决按钮，不写 DB。${evidence?.generated_at ? ` 生成时间：${evidence.generated_at}。` : ''}`}
                />
                {evidenceError ? (
                  <Empty description={evidenceError} />
                ) : (
                  <>
                    <Row gutter={12} style={{ marginBottom: 8 }}>
                      <Col span={4}><Card size="small"><Statistic title="证据条数" value={evidence?.item_count ?? evidenceItems.length} suffix="只" /></Card></Col>
                      <Col span={4}><Card size="small"><Statistic title="池日期" value={evidence?.summary?.pool?.pool_date || evidence?.pool_date || '—'} /></Card></Col>
                      <Col span={4}><Card size="small"><Statistic title="市场覆盖" value={evidenceMarketText || '—'} styles={{ content: { fontSize: 13 } }} /></Card></Col>
                      <Col span={4}><Card size="small"><Statistic title="证据完整度" value={`${evidenceLevelCount('full')} 完整 / ${evidenceLevelCount('none')} 无覆盖`} styles={{ content: { fontSize: 13 } }} /></Card></Col>
                      <Col span={4}><Card size="small"><Statistic title="缺口条目" value={evidenceGapTotal} suffix={`条（真缺失 ${evidenceTrueMissing}）`} styles={{ content: { fontSize: 18 } }} /></Card></Col>
                      <Col span={4}><Card size="small"><Statistic title="KB 方法引用" value={evidenceKbTotal} suffix="条" /></Card></Col>
                    </Row>
                    <div style={{ marginBottom: 8 }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        数据源：{evidence?.source?.path || 'C:\\Temp\\acad1_evidence_bundle.json'}（只读）
                        {evidence?.generated_at ? ` · 生成时间 ${evidence.generated_at}` : ''}
                        {' '}· 本页仅 GET，无写 DB、无通过/否决按钮。
                      </Text>
                    </div>
                    <Table
                      size="small" loading={evidenceLoading}
                      rowKey={(r: Acad1EvidenceItem) => `${r.seq}-${r.market}-${r.code}`}
                      dataSource={evidenceItems}
                      pagination={{ pageSize: 30, hideOnSinglePage: true }}
                      scroll={{ x: 1500 }}
                      expandable={{ expandedRowRender: renderEvidenceDetail }}
                      columns={[
                        { title: '序号', dataIndex: 'seq', width: 70 },
                        {
                          title: '市场', dataIndex: 'market', width: 80,
                          render: (v: string) => {
                            const color = v === 'CN' ? 'red' : v === 'HK' ? 'gold' : v === 'US' ? 'blue' : 'purple';
                            return <Tag color={color}>{v}</Tag>;
                          },
                        },
                        { title: '代码', dataIndex: 'code', width: 110 },
                        { title: '名称', dataIndex: 'name', width: 110 },
                        { title: '来源策略', dataIndex: 'strategy_name', width: 100 },
                        { title: '信号', dataIndex: 'signal', width: 150 },
                        {
                          title: '证据等级', dataIndex: 'evidence_level', width: 90,
                          render: (v: string) => <Tag color={v === 'full' ? 'green' : v === 'none' ? 'red' : 'gold'}>{v || 'unknown'}</Tag>,
                        },
                        {
                          title: '缺口', dataIndex: 'gap_count', width: 80,
                          render: (v: number) => <Tag color={v ? 'gold' : 'green'}>{v || 0}</Tag>,
                        },
                        {
                          title: '卡事实', width: 190,
                          render: (_: unknown, r: Acad1EvidenceItem) => (
                            <Space size={4} wrap>
                              <Tag>{r.card?.fields?.direction || '—'}</Tag>
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                {r.card?.fields?.rating || '—'} · {r.card?.fields?.executable || '—'}
                              </Text>
                            </Space>
                          ),
                        },
                        {
                          title: '行业', width: 130,
                          render: (_: unknown, r: Acad1EvidenceItem) => (
                            <Space size={4}>
                              <Tag color={r.industry?.status === 'covered' ? 'green' : 'default'}>{r.industry?.status || '—'}</Tag>
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                {String((r.industry?.value?.sw_l1_name as string | undefined) || '')}
                              </Text>
                            </Space>
                          ),
                        },
                        {
                          title: '公告', width: 110,
                          render: (_: unknown, r: Acad1EvidenceItem) => (
                            <Space size={4}>
                              <Tag color={r.announcements?.status === 'covered' ? 'green' : 'default'}>{r.announcements?.status || '—'}</Tag>
                              <Text type="secondary" style={{ fontSize: 12 }}>{evidenceAnnCount(r)} 条</Text>
                            </Space>
                          ),
                        },
                        {
                          title: '新闻', width: 110,
                          render: (_: unknown, r: Acad1EvidenceItem) => (
                            <Space size={4}>
                              <Tag color={r.news?.status === 'covered' ? 'green' : r.news?.status === 'no_match' ? 'gold' : 'default'}>{r.news?.status || '—'}</Tag>
                              <Text type="secondary" style={{ fontSize: 12 }}>{evidenceNewsCount(r)} 条</Text>
                            </Space>
                          ),
                        },
                        {
                          title: 'KB 引用', width: 110,
                          render: (_: unknown, r: Acad1EvidenceItem) => (
                            <Space size={4}>
                              <Tag color={r.method_refs?.length ? 'blue' : 'default'}>{r.method_refs?.length || 0} 条</Tag>
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                {r.method_coverage === 'en_only' ? '仅英文' : (r.method_coverage || '')}
                              </Text>
                            </Space>
                          ),
                        },
                      ] as any}
                      locale={{ emptyText: '证据包暂无条目' }}
                    />
                  </>
                )}
              </Card>
            ),
          },
          {
            key: 'candidates', label: '候选参考',
            children: (
              <Card size="small">
                <Alert type="info" showIcon style={{ marginBottom: 8 }}
                  title="策略候选，不是 ACAD1 股票池；候选进入池子必须由 ACAD1 审核。" />
                <Table
                  size="small" loading={loading}
                  rowKey={(r: Candidate) => `${r.strategy_id}-${r.trade_date}-${r.code}-${r.signal}`}
                  dataSource={candidates}
                  pagination={{ pageSize: 20 }}
                  columns={[
                    { title: '来源策略', dataIndex: 'strategy_name', width: 150 },
                    { title: '市场', dataIndex: 'market', width: 70 },
                    { title: '代码', dataIndex: 'code', width: 110 },
                    { title: '名称', dataIndex: 'name', width: 130 },
                    { title: '信号', dataIndex: 'signal', width: 120 },
                    { title: '分数', dataIndex: 'score', width: 70 },
                    { title: '数据日', dataIndex: 'data_date', width: 105 },
                  ] as any}
                  locale={{ emptyText: '暂无候选' }}
                />
              </Card>
            ),
          },
          {
            key: 'review', label: '评审/No-Go',
            children: (
              <Card size="small">
                <Table
                  size="small" rowKey={(r: ReviewRow) => `${r.review_date}-${r.topic}`}
                  dataSource={reviews} pagination={{ pageSize: 10 }}
                  columns={[
                    { title: '评审日', dataIndex: 'review_date', width: 105 },
                    { title: '主题', dataIndex: 'topic', width: 220 },
                    { title: '结论', dataIndex: 'verdict', width: 80 },
                    { title: '摘要', dataIndex: 'summary' },
                    { title: '评审人', dataIndex: 'reviewer', width: 80 },
                  ] as any}
                  locale={{ emptyText: '暂无评审记录' }}
                />
              </Card>
            ),
          },
          {
            key: 'decisions', label: '鲁肃判断',
            children: (
              <Card size="small">
                <Table
                  size="small" rowKey={(r: any) => `${r.pool_date}-${r.code}-${r.decided_at}`}
                  dataSource={decisions} pagination={{ pageSize: 10 }}
                  columns={[
                    { title: '池日期', dataIndex: 'pool_date', width: 105 },
                    { title: '代码', dataIndex: 'code', width: 105 },
                    { title: '名称', dataIndex: 'name', width: 110 },
                    { title: '判断', dataIndex: 'verdict', width: 80 },
                    { title: '理由', dataIndex: 'reasons' },
                    { title: '判断人', dataIndex: 'decided_by', width: 90 },
                  ] as any}
                  locale={{ emptyText: '鲁肃尚未判断' }}
                />
              </Card>
            ),
          },
          {
            key: 'calendar', label: '池日历',
            children: (
              <Card size="small">
                <Table
                  size="small" rowKey={(r: any) => r.pool_date} dataSource={calendar}
                  pagination={{ pageSize: 12 }}
                  columns={[
                    { title: '选池日', dataIndex: 'pool_date', width: 140 },
                    { title: '选中数', dataIndex: 'total', width: 100 },
                    { title: '来源策略', dataIndex: 'strategies' },
                  ] as any}
                  locale={{ emptyText: '暂无 ACAD1 选池记录' }}
                />
              </Card>
            ),
          },
          {
            key: 'health', label: '策略健康',
            children: (
              <Card size="small">
                <Table
                  size="small" rowKey="strategy_id" dataSource={sidecars}
                  pagination={false}
                  columns={[
                    { title: '策略', dataIndex: 'display_name' },
                    { title: '状态', dataIndex: 'status' },
                    { title: '研究 owner', dataIndex: 'owner' },
                    { title: '运行支持', dataIndex: 'ops_support' },
                    {
                      title: '健康', dataIndex: 'health',
                      render: (v: string) => {
                        const h = HEALTH[v] || HEALTH.unknown;
                        return <Tag color={h.color}>{h.text}</Tag>;
                      },
                    },
                    { title: '数据日', dataIndex: 'data_date' },
                  ] as any}
                />
              </Card>
            ),
          },
        ]}
      />
      <Modal open={!!editing} onCancel={() => setEditing(null)} onOk={saveCard}
        confirmLoading={saving} width={720} title={editing ? `${editing.code} ${editing.name}` : '投研卡'}>
        {editing && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <div>多空<Select style={{ width: '100%' }} value={editing.direction}
              onChange={v => setEditing({ ...editing, direction: v })}
              options={['看多', '中性', '看空'].map(v => ({ value: v, label: v }))} /></div>
            <div>可执行<Select style={{ width: '100%' }} value={editing.executable}
              onChange={v => setEditing({ ...editing, executable: v })}
              options={['可买', '观察', '买不到', '不可买'].map(v => ({ value: v, label: v }))} /></div>
            <div>评分<InputNumber style={{ width: '100%' }} value={editing.score}
              onChange={v => setEditing({ ...editing, score: v })} /></div>
            <div>评级<Select style={{ width: '100%' }} value={editing.rating}
              onChange={v => setEditing({ ...editing, rating: v })}
              options={['A', 'B', 'C', 'D'].map(v => ({ value: v, label: v }))} /></div>
            <div style={{ gridColumn: '1 / span 2' }}>核心逻辑<Input.TextArea rows={2} value={editing.thesis || ''}
              onChange={e => setEditing({ ...editing, thesis: e.target.value })} /></div>
            <div style={{ gridColumn: '1 / span 2' }}>风险<Input.TextArea rows={2} value={editing.risks || ''}
              onChange={e => setEditing({ ...editing, risks: e.target.value })} /></div>
            <div>买区下沿<InputNumber style={{ width: '100%' }} value={editing.buy_zone_low}
              onChange={v => setEditing({ ...editing, buy_zone_low: v })} /></div>
            <div>买区上沿<InputNumber style={{ width: '100%' }} value={editing.buy_zone_high}
              onChange={v => setEditing({ ...editing, buy_zone_high: v })} /></div>
            <div>目标价<InputNumber style={{ width: '100%' }} value={editing.target_price}
              onChange={v => setEditing({ ...editing, target_price: v })} /></div>
            <div>止损价<InputNumber style={{ width: '100%' }} value={editing.stop_price}
              onChange={v => setEditing({ ...editing, stop_price: v })} /></div>
            <div style={{ gridColumn: '1 / span 2' }}>失效条件<Input value={editing.invalidation || ''}
              onChange={e => setEditing({ ...editing, invalidation: e.target.value })} /></div>
          </div>
        )}
      </Modal>
    </div>
  );
}
