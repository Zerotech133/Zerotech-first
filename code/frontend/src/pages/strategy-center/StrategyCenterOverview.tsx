import type { CSSProperties } from 'react';
import {
  diverseLatestPicks,
  fmtDate,
  fmtNumber,
  fmtPct,
  fmtRatio,
  signedClass,
  type StrategyCenterData,
  type StrategyCenterStrategy,
} from './strategyCenterData';
import { useStrategyCenterSnapshot } from './strategyCenterApi';
import {
  GradeBadge,
  Metric,
  PreviewBanner,
  PreviewUnavailable,
  SectionHead,
  Sparkline,
  StrategyStatusBadge,
} from './strategyCenterShared';
import './strategyCenter.css';

function MatrixRow({ s }: { s: StrategyCenterStrategy }) {
  return (
    <div className="sc-matrix-row">
      <div className="sc-matrix-strategy">
        <GradeBadge grade={s.grade} />
        <span className="sc-matrix-sid">{s.sid}</span>
        <span className="sc-matrix-name">{s.name}</span>
      </div>
      <div><StrategyStatusBadge strategy={s} /></div>
      <div className="sc-num">{s.latest_pick_count ? s.latest_pick_count : '—'}</div>
      <div className={`sc-num ${signedClass(s.exc)}`}>{fmtPct(s.exc, 'pp')}</div>
      <div className="sc-num">{fmtPct(s.win)}</div>
    </div>
  );
}

function HealthBar({ grade, count, max }: { grade: string; count: number; max: number }) {
  const width = max > 0 ? Math.max(4, (count / max) * 100) : 0;
  const style = { '--sc-bar-width': `${width}%` } as CSSProperties;
  return (
    <div className="sc-health-row">
      <span className="sc-health-grade">{grade}</span>
      <div className="sc-health-track"><i className={`sc-health-fill ${grade === 'A' ? 'a' : grade === 'B' ? 'b' : grade === 'C' ? 'c' : grade === 'D' ? 'd' : 'na'}`} style={style} /></div>
      <span className="sc-health-count">{count}</span>
    </div>
  );
}

function OverviewContent({ data }: { data: StrategyCenterData }) {
  const d = data;
  const matrix = d.strategies.filter((s) => s.in_pool).slice(0, 10);
  const candidates = diverseLatestPicks(d, 8);
  const days = [...d.days].reverse();
  const latestNav = d.nav[d.nav.length - 1];
  const navValues = d.nav.map((n) => n.nav);
  const gradeEntries = Object.entries(d.grade_counts as Record<string, number>);
  const gradeMax = Math.max(...gradeEntries.map(([, n]) => n));
  const issues = d.issues.length;
  const pending = d.counts.latest_picks;
  const vetoed = Math.max(0, d.counts.pending - pending);

  return (
    <div className="sc-page">
      <PreviewBanner meta={d.meta} />

      <div className="sc-head">
        <div>
          <div className="sc-eyebrow">司马徽 / 策略中台</div>
          <h2>总览 <span className="sc-readonly">只读</span></h2>
          <p>策略管理、运行与治理的只读镜像；先看策略，再看今日候选，不把候选当股票池。</p>
        </div>
        <div className="sc-asof">
          <span>数据截至</span>
          <b>{fmtDate(d.meta.asof)}</b>
          <em>qengine {d.meta.source_commit} · 只读镜像</em>
        </div>
      </div>

      <div className="sc-kpis">
        <Metric label="注册策略" value={d.counts.strategy} note={`${d.lx_count} 个 LX 战法 · 含外部源`} />
        <Metric label="当前在池" value={d.counts.pool_strategy} note="可进入每日候选" />
        <Metric label="今日候选" value={d.counts.latest_picks} note={`来自 ${latestNav ? d.latest_day.n_strategies : 0} 个策略 · 待执行`} />
        <Metric label="累计交易" value={fmtNumber(d.counts.trade)} note={`${fmtNumber(d.counts.pick)} 条 pick · ${fmtNumber(d.counts.pool_candidate)} 条候选`} />
        <Metric
          label="组合净值"
          value={fmtRatio(latestNav?.nav)}
          note={latestNav ? `自 10-09 起 ${fmtPct(latestNav.ret_pct ?? 0)} · ${fmtDate(latestNav.date)}` : '—'}
          tone={latestNav && (latestNav.ret_pct ?? 0) > 0 ? 'up' : 'down'}
        />
      </div>

      <div className="sc-grid sc-grid-main">
        <div className="sc-panel">
          <SectionHead title="策略矩阵" extra={`当前在池 ${matrix.length} / ${d.counts.pool_strategy} · 按评级与累计超额`} />
          <div className="sc-matrix-head">
            <span>策略</span><span>状态</span><span>今日</span><span>累计超额</span><span>胜率</span>
          </div>
          <div className="sc-matrix">
            {matrix.map((s) => <MatrixRow key={s.sid} s={s} />)}
          </div>
          <div className="sc-panel-foot">只读展示；评级和统计来自 qengine_sync 测试库，不是 ACAD1 结论。</div>
        </div>

        <div className="sc-panel">
          <SectionHead title="今日候选" extra={`${d.counts.latest_picks} 条 · 来自 ${d.latest_day.n_strategies} 个策略 · 按策略/排名`} />
          <div className="sc-candidates">
            {candidates.map((p) => (
              <div className="sc-candidate" key={`${p.sid}-${p.ts_code}`}>
                <div className="sc-candidate-main">
                  <span className="sc-candidate-code">{p.ts_code}</span>
                  <span className="sc-candidate-name">{p.name}</span>
                </div>
                <div className="sc-candidate-meta">
                  <span>{p.sid}</span>
                  <span>止损 {p.stop_px ?? '—'}</span>
                  <span>参考 {p.ref_px ?? '—'}</span>
                </div>
                <div className="sc-candidate-side">
                  <span className={`sc-num ${signedClass(p.gap_pct)}`}>{fmtPct(p.gap_pct)}</span>
                  <span className="sc-rank">#{p.rank_in}</span>
                </div>
              </div>
            ))}
          </div>
          <div className="sc-panel-foot">仅展示前 8 条；全量 122 条与逐条明细待只读 API 接入。</div>
        </div>
      </div>

      <div className="sc-grid sc-grid-bottom">
        <div className="sc-panel">
          <SectionHead title="最近信号日" extra="日级闸门与建仓结果" />
          <div className="sc-days">
            <div className="sc-day-row sc-day-head">
              <span>信号日</span><span>信号</span><span>建仓</span><span>策略</span><span>状态</span>
            </div>
            {days.map((day) => (
              <div className="sc-day-row" key={day.signal_date}>
                <span className="sc-mono">{fmtDate(day.signal_date)}</span>
                <span className="sc-num">{fmtNumber(day.n_signal)}</span>
                <span className="sc-num">{day.n_buy ? fmtNumber(day.n_buy) : '0'}</span>
                <span className="sc-num">{day.n_strategies}</span>
                <span>{day.gate_ok ? <span className="sc-tag sc-tag-ok">闸门开</span> : <span className="sc-tag sc-tag-warn">闸门关</span>}</span>
              </div>
            ))}
          </div>
          <div className="sc-panel-foot">最新信号日 11-05 为 pending，尚未建仓；0 建仓不是空数据。</div>
        </div>

        <div className="sc-panel">
          <SectionHead title="库健康" extra="评级分布与账面" />
          <div className="sc-health-kpis">
            <div><span>未处理诊断</span><b>{issues}</b></div>
            <div><span>待执行 / 被否</span><b>{pending} <em>/ {vetoed}</em></b></div>
          </div>
          <div className="sc-health">
            {gradeEntries.map(([grade, count]) => (
              <HealthBar key={grade} grade={grade} count={count} max={gradeMax} />
            ))}
          </div>
          <div className="sc-nav-block">
            <div className="sc-nav-line">
              <span>组合净值</span>
              <b className={latestNav && (latestNav.ret_pct ?? 0) > 0 ? 'up' : 'down'}>{fmtRatio(latestNav?.nav)}</b>
            </div>
            <Sparkline values={navValues} ariaLabel="qengine 组合净值近期走势" />
            <div className="sc-nav-note">10-09 至 {fmtDate(d.meta.asof)} · {d.nav.length} 个净值点 · 非 ACAD1 实盘</div>
          </div>
        </div>
      </div>

      <div className="sc-boundary">
        <b>边界</b>
        <span>测试库只读镜像，不是生产；不自动改池、不自动批准、不构成交易建议。</span>
        <span>策略详情页待只读 API 接入后补齐。</span>
      </div>
    </div>
  );
}

export default function StrategyCenterOverview() {
  const { data, error, loading, reload } = useStrategyCenterSnapshot();
  if (loading && !data) {
    return <div className="sc-page"><div className="sc-panel sc-empty-panel">正在读取 qengine_sync…</div></div>;
  }
  if (!data) {
    return (
      <PreviewUnavailable
        message={error || '策略中台只读 API 暂不可用。'}
        onRetry={reload}
      />
    );
  }
  return <OverviewContent data={data} />;
}
