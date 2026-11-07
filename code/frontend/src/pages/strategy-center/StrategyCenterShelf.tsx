import { Empty } from 'antd';
import { useMemo, useState } from 'react';
import {
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
  PreviewBanner,
  PreviewUnavailable,
  StrategyStatusBadge,
} from './strategyCenterShared';
import './strategyCenter.css';

type GradeFilter = '全部' | 'A' | 'B' | 'C' | 'D' | '未评级';
type PoolFilter = '全部' | '在池' | '池外';

const GRADE_FILTERS: GradeFilter[] = ['全部', 'A', 'B', 'C', 'D', '未评级'];
const POOL_FILTERS: PoolFilter[] = ['全部', '在池', '池外'];

function cardTopPick(s: StrategyCenterStrategy) {
  const p = s.latest_top_pick as { ts_code?: string; name?: string } | null;
  if (!p?.ts_code) return null;
  return p;
}

function StrategyCard({
  s,
  onOpenDetail,
}: {
  s: StrategyCenterStrategy;
  onOpenDetail?: (sid: string) => void;
}) {
  const top = cardTopPick(s);
  const empty = !s.latest_pick_count;
  const disabled = s.in_pool && !s.enabled;
  return (
    <article
      className={`sc-card sc-card-clickable${empty ? ' is-empty' : ''}${disabled ? ' is-disabled' : ''}`}
      role={onOpenDetail ? 'button' : undefined}
      tabIndex={onOpenDetail ? 0 : undefined}
      title={onOpenDetail ? `查看 ${s.sid} 详情` : undefined}
      onClick={() => onOpenDetail?.(s.sid)}
      onKeyDown={(e) => {
        if (!onOpenDetail) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpenDetail(s.sid);
        }
      }}
    >
      <div className="sc-card-top">
        <div className="sc-card-id">
          <GradeBadge grade={s.grade} />
          <span className="sc-mono">{s.sid}</span>
        </div>
        <StrategyStatusBadge strategy={s} />
      </div>
      <h3 className="sc-card-name">{s.name}</h3>
      <p className="sc-card-doc">{s.doc || '暂无一句话逻辑'}</p>
      <div className="sc-card-stats">
        <div>
          <span>今日候选</span>
          <b className={empty ? 'muted' : ''}>{empty ? '—' : s.latest_pick_count}</b>
        </div>
        <div>
          <span>累计样本</span>
          <b>{fmtNumber(s.n_total)}</b>
        </div>
        <div>
          <span>累计超额</span>
          <b className={signedClass(s.exc)}>{fmtPct(s.exc, 'pp')}</b>
        </div>
      </div>
      <div className="sc-card-foot">
        <span className="sc-card-pick">
          {top ? (
            <>
              <em>今日</em>
              <span className="sc-mono">{top.ts_code}</span>
              <span>{top.name}</span>
            </>
          ) : (
            <span className="muted">今日无候选</span>
          )}
        </span>
        <span className="sc-card-win">胜率 {fmtPct(s.win)}</span>
      </div>
    </article>
  );
}

function ShelfContent({
  data,
  onOpenDetail,
}: {
  data: StrategyCenterData;
  onOpenDetail?: (sid: string) => void;
}) {
  const [grade, setGrade] = useState<GradeFilter>('全部');
  const [pool, setPool] = useState<PoolFilter>('全部');
  const [onlyPick, setOnlyPick] = useState(false);

  const counts = useMemo(() => {
    const list = data.strategies;
    return {
      all: list.length,
      pool: list.filter((s) => s.in_pool).length,
      picks: list.filter((s) => (s.latest_pick_count ?? 0) > 0).length,
      grades: Object.fromEntries(
        GRADE_FILTERS.map((g) => [g, g === '全部' ? list.length : list.filter((s) => (s.grade || '未评级') === g).length]),
      ) as Record<GradeFilter, number>,
    };
  }, [data]);

  const filtered = useMemo(() => {
    return data.strategies.filter((s) => {
      if (grade !== '全部' && (s.grade || '未评级') !== grade) return false;
      if (pool === '在池' && !s.in_pool) return false;
      if (pool === '池外' && s.in_pool) return false;
      if (onlyPick && !s.latest_pick_count) return false;
      return true;
    });
  }, [data, grade, pool, onlyPick]);

  return (
    <div className="sc-page">
      <PreviewBanner meta={data.meta} />

      <div className="sc-head">
        <div>
          <div className="sc-eyebrow">司马徽 / 策略中台</div>
          <h2>策略货架 <span className="sc-readonly">只读</span></h2>
          <p>一个策略一张卡；有票高亮，无票置灰不隐藏。当前 {counts.all} 个注册策略，其中 {counts.pool} 个在池。</p>
        </div>
        <div className="sc-shelf-summary">
          <span>净值 {fmtRatio(data.nav[data.nav.length - 1]?.nav)}</span>
          <span>{fmtNumber(data.counts.latest_picks)} 条最新候选</span>
          <span>评级只看策略质量，不等于买入建议</span>
        </div>
      </div>

      <div className="sc-filters">
        <div className="sc-filter-group">
          <span className="sc-filter-label">评级</span>
          {GRADE_FILTERS.map((g) => (
            <button
              key={g}
              type="button"
              className={`sc-chip${grade === g ? ' active' : ''}`}
              aria-pressed={grade === g}
              onClick={() => setGrade(g)}
            >
              {g === '全部' ? '全部' : g}
              <em>{counts.grades[g]}</em>
            </button>
          ))}
        </div>
        <div className="sc-filter-group">
          <span className="sc-filter-label">池状态</span>
          {POOL_FILTERS.map((p) => (
            <button
              key={p}
              type="button"
              className={`sc-chip${pool === p ? ' active' : ''}`}
              aria-pressed={pool === p}
              onClick={() => setPool(p)}
            >
              {p}
              <em>{p === '全部' ? counts.all : p === '在池' ? counts.pool : counts.all - counts.pool}</em>
            </button>
          ))}
        </div>
        <div className="sc-filter-group sc-filter-right">
          <button
            type="button"
            className={`sc-chip${onlyPick ? ' active' : ''}`}
            aria-pressed={onlyPick}
            onClick={() => setOnlyPick((v) => !v)}
          >
            只看有票 <em>{counts.picks}</em>
          </button>
        </div>
      </div>

      {filtered.length ? (
        <div className="sc-cards">
          {filtered.map((s) => <StrategyCard key={s.sid} s={s} onOpenDetail={onOpenDetail} />)}
        </div>
      ) : (
        <div className="sc-panel sc-empty-panel">
          <Empty description="当前筛选没有策略；清空筛选后再看。" />
        </div>
      )}

      <div className="sc-boundary">
        <b>边界</b>
        <span>只读测试库镜像，不是生产；评级/收益来自 qengine 历史样本，不构成交易建议。</span>
        <span>点击卡片查看只读详情。</span>
      </div>
    </div>
  );
}

export default function StrategyCenterShelf({
  onOpenDetail,
}: {
  onOpenDetail?: (sid: string) => void;
}) {
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
  return <ShelfContent data={data} onOpenDetail={onOpenDetail} />;
}
