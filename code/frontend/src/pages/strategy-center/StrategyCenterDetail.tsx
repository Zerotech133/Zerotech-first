import { Button, Empty, Table, Tag } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import {
  fmtDate,
  fmtNumber,
  fmtPct,
  fmtRatio,
  gradeClass,
  signedClass,
  strategyStatus,
  type StrategyCenterDetail,
  type StrategyCenterDetailPick,
  type StrategyCenterStrategyDay,
  type StrategyCenterTrade,
} from './strategyCenterData';
import { fetchStrategyCenterDetail, useStrategyCenterSnapshot } from './strategyCenterApi';
import {
  GradeBadge,
  Metric,
  PreviewBanner,
  PreviewUnavailable,
  SectionHead,
  StrategyStatusBadge,
} from './strategyCenterShared';
import './strategyCenter.css';

function Calendar({ days }: { days: StrategyCenterStrategyDay[] }) {
  if (!days.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无信号日" />;
  return (
    <div className="sc-detail-calendar">
      {days.map((d) => {
        const positive = Number(d.avg || 0) >= 0;
        return (
          <div
            key={d.signal_date}
            className={`sc-detail-day${positive ? ' is-up' : ' is-down'}`}
            title={`${fmtDate(d.signal_date)} · ${d.n} 样本 · 平均 ${fmtPct(d.avg)}`}
          >
            <span>{fmtDate(d.signal_date).slice(5)}</span>
            <b>{d.n} 样本</b>
            <em>{fmtPct(d.avg)}</em>
          </div>
        );
      })}
    </div>
  );
}

function TradeTable({ rows }: { rows: StrategyCenterTrade[] }) {
  return (
    <Table
      size="small"
      rowKey={(r) => `${r.track || 'primary'}-${r.signal_date}-${r.ts_code}`}
      pagination={false}
      dataSource={rows}
      columns={[
        { title: '信号日', dataIndex: 'signal_date', render: fmtDate, width: 100 },
        { title: '代码', dataIndex: 'ts_code', width: 110 },
        { title: '名称', dataIndex: 'name', width: 100 },
        {
          title: '收益',
          dataIndex: 'ret_net_pct',
          width: 80,
          render: (v: number | null) => <span className={signedClass(v)}>{fmtPct(v)}</span>,
        },
        { title: '持有', dataIndex: 'hold_days', width: 70, render: (v: number | null) => (v == null ? '—' : `${v}天`) },
        { title: '状态', dataIndex: 'status', width: 80 },
      ]}
    />
  );
}

function PickTable({ rows }: { rows: StrategyCenterDetailPick[] }) {
  return (
    <Table
      size="small"
      rowKey={(r) => `${r.signal_date}-${r.ts_code}-${r.rank_in ?? ''}`}
      pagination={false}
      dataSource={rows}
      columns={[
        { title: '信号日', dataIndex: 'signal_date', render: fmtDate, width: 100 },
        { title: '代码', dataIndex: 'ts_code', width: 110 },
        { title: '名称', dataIndex: 'name', width: 100 },
        { title: '排名', dataIndex: 'rank_in', width: 60 },
        {
          title: '收益',
          dataIndex: 'ret_net_pct',
          width: 80,
          render: (v: number | null) => <span className={signedClass(v)}>{fmtPct(v)}</span>,
        },
        { title: '状态', dataIndex: 'status', width: 80 },
      ]}
    />
  );
}

export default function StrategyCenterDetail({
  sid,
  onBack,
}: {
  sid: string | null;
  onBack: () => void;
}) {
  const [detail, setDetail] = useState<StrategyCenterDetail | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { data: snapshot } = useStrategyCenterSnapshot();

  useEffect(() => {
    if (!sid) {
      setDetail(null);
      return;
    }
    let alive = true;
    setLoading(true);
    setError('');
    fetchStrategyCenterDetail(sid)
      .then((next) => {
        if (alive) setDetail(next);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [sid]);

  const strategy = detail?.strategy;
  const status = strategy ? strategyStatus(strategy) : null;
  const issues = detail?.issues || [];
  const trades = detail?.trades || [];
  const picks = detail?.picks || [];
  const best = detail?.extremes.best;
  const worst = detail?.extremes.worst;
  const positiveDays = useMemo(
    () => (detail?.days || []).filter((d) => Number(d.avg || 0) > 0).length,
    [detail],
  );

  if (!sid) {
    return (
      <div className="sc-page">
        <div className="sc-panel sc-empty-panel">
          <Empty description="从策略货架点击任意策略，查看只读详情。" />
          <Button type="link" onClick={onBack}>返回策略货架</Button>
        </div>
      </div>
    );
  }

  if (loading && !detail) {
    return <div className="sc-page"><div className="sc-panel sc-empty-panel">正在读取 {sid} 详情…</div></div>;
  }

  if (error || !detail || !strategy) {
    return (
      <PreviewUnavailable
        message={error || `策略 ${sid} 暂无详情`}
        onRetry={() => {
          setLoading(true);
          setError('');
          fetchStrategyCenterDetail(sid)
            .then(setDetail)
            .catch((err: unknown) => setError(String(err)))
            .finally(() => setLoading(false));
        }}
      />
    );
  }

  return (
    <div className="sc-page">
      {snapshot?.meta ? <PreviewBanner meta={snapshot.meta} /> : null}
      <div className="sc-detail-top">
        <Button size="small" onClick={onBack}>← 返回策略货架</Button>
        <span className="sc-readonly">只读</span>
      </div>

      <div className="sc-detail-head">
        <div>
          <div className="sc-detail-sid">
            <GradeBadge grade={strategy.grade} />
            <span className="sc-mono">{strategy.sid}</span>
            <span>{strategy.name}</span>
            {status ? (
              <span className={`sc-status sc-status-${status.tone}`}>{status.label}</span>
            ) : null}
          </div>
          <p>{strategy.doc || '暂无一句话逻辑'}</p>
          <div className="sc-detail-sub">
            {strategy.ekind || '—'} · {strategy.tiers || '—'} · 首次 {fmtDate(strategy.first_sig)} · 最近 {fmtDate(strategy.last_sig)}
          </div>
        </div>
        <div className="sc-detail-badges">
          <Tag color="default">{strategy.system || 'qengine'}</Tag>
          {strategy.enabled_from ? <Tag color="default">启用 {fmtDate(strategy.enabled_from)}</Tag> : null}
        </div>
      </div>

      <div className="sc-detail-kpis">
        <Metric label="累计样本" value={fmtNumber(strategy.n_total)} note={`${fmtNumber(strategy.n_closed)} 已平 / ${fmtNumber(strategy.n_open)} 在场`} />
        <Metric label="胜率" value={fmtPct(strategy.win)} note="单笔口径" tone={Number(strategy.win || 0) > 0 ? 'up' : undefined} />
        <Metric label="笔均收益" value={fmtPct(strategy.avg)} note="未扣基准" tone={Number(strategy.avg || 0) >= 0 ? 'up' : 'down'} />
        <Metric label="累计超额" value={fmtPct(strategy.exc, 'pp')} note="对同日随机" tone={Number(strategy.exc || 0) >= 0 ? 'up' : 'down'} />
        <Metric label="评级" value={<span className={`sc-grade ${gradeClass(strategy.grade)}`}>{strategy.grade || '未评级'}</span>} note={strategy.grade_why || '—'} />
        <Metric label="诊断" value={issues.length} note={`${issues.filter((i) => i.status === '新增').length} 新增 / ${issues.filter((i) => i.status === '已关闭').length} 已关闭`} />
      </div>

      <div className="sc-detail-grid">
        <div className="sc-panel">
          <SectionHead title="信号日历" extra={`${detail.days.length} 个信号日 · ${positiveDays} 日平均为正`} />
          <Calendar days={detail.days} />
          <div className="sc-detail-legend">
            <span><i className="sc-up-dot" />平均收益为正</span>
            <span><i className="sc-down-dot" />平均收益为负</span>
            <span>单位：单笔平均收益</span>
          </div>
        </div>

        <div className="sc-detail-side">
          <div className="sc-panel">
            <SectionHead title="极值样本" extra="已平仓单笔净收益" />
            <div className="sc-extremes">
              <div>
                <span>最佳样本</span>
                <b className="sc-mono">{best?.ts_code || '—'}</b>
                <span>{best?.name || '—'}</span>
                <strong className="up">{fmtPct(best?.ret_net_pct)}</strong>
                <em>{best ? `${fmtDate(best.signal_date)} · 持有 ${best.hold_days ?? '—'} 天 · 超额 ${fmtPct(best.excess, 'pp')}` : '—'}</em>
              </div>
              <div>
                <span>最差样本</span>
                <b className="sc-mono">{worst?.ts_code || '—'}</b>
                <span>{worst?.name || '—'}</span>
                <strong className="down">{fmtPct(worst?.ret_net_pct)}</strong>
                <em>{worst ? `${fmtDate(worst.signal_date)} · 持有 ${worst.hold_days ?? '—'} 天 · 超额 ${fmtPct(worst.excess, 'pp')}` : '—'}</em>
              </div>
            </div>
          </div>
          <div className="sc-panel">
            <SectionHead title="诊断" extra="策略质量与声明" />
            <div className="sc-issue-list">
              {issues.length ? issues.map((i) => (
                <div className="sc-issue" key={`${i.issue_id}`}>
                  <span>{i.title}</span>
                  <Tag color={i.status === '新增' ? 'red' : 'default'}>{i.status || '—'}</Tag>
                </div>
              )) : <span className="muted">暂无诊断</span>}
            </div>
          </div>
        </div>
      </div>

      <div className="sc-detail-tables">
        <div className="sc-panel">
          <SectionHead title="最近交易" extra={`${trades.length} 条样例`} />
          <TradeTable rows={trades} />
        </div>
        <div className="sc-panel">
          <SectionHead title="候选样本" extra="按信号日倒序" />
          <PickTable rows={picks} />
        </div>
      </div>

      <div className="sc-boundary">
        <b>边界</b>
        <span>qengine_sync 测试库只读镜像，不是生产；评级/收益来自 qengine 历史样本，不构成交易建议。</span>
        <span>本页不执行策略动作；管理动作另批接入。</span>
      </div>
    </div>
  );
}
