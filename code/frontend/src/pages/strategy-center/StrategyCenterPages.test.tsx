import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type {
  StrategyCenterData,
  StrategyCenterDetail as StrategyCenterDetailPayload,
  StrategyCenterPick,
  StrategyCenterStrategy,
} from './strategyCenterData';
import StrategyCenterOverview from './StrategyCenterOverview';
import StrategyCenterShelf from './StrategyCenterShelf';
import StrategyCenterDetail from './StrategyCenterDetail';
import { fetchStrategyCenterDetail, useStrategyCenterSnapshot } from './strategyCenterApi';

vi.mock('./strategyCenterApi', () => ({
  useStrategyCenterSnapshot: vi.fn(),
  fetchStrategyCenterDetail: vi.fn(),
}));

const snapshotMock = vi.mocked(useStrategyCenterSnapshot);
const detailMock = vi.mocked(fetchStrategyCenterDetail);

const STRATEGY_A: StrategyCenterStrategy = {
  sid: 'LX20', name: '牛股三绝', grade: 'A', in_pool: true, enabled: 1,
  doc: '倍量阳柱后回踩不破实体下沿', latest_pick_count: 10,
  latest_top_pick: { ts_code: '600918.SH', name: '中泰证券' },
  n_total: 200, n_closed: 152, n_open: 48, win: 0.4013, avg: -0.004,
  exc: 2.26, first_sig: '20261008', last_sig: '20261104', n_issues: 1,
};

const STRATEGY_B: StrategyCenterStrategy = {
  sid: 'LX16', name: '平斜交叉', grade: 'B', in_pool: true, enabled: 1,
  doc: '站稳20日线 + 不破水平平衡线', latest_pick_count: 0,
  n_total: 200, n_closed: 150, n_open: 50, win: 0.3526, avg: 0.0,
  exc: 0.85, first_sig: '20261008', last_sig: '20261104', n_issues: 0,
};

const PICK: StrategyCenterPick = {
  signal_date: '20261105', sid: 'LX20', ts_code: '600918.SH', name: '中泰证券',
  industry: '证券', rank_in: 1, gap_pct: 3.08, stop_px: 6.25, ref_px: 6.44, score: 1,
};

const DATA: StrategyCenterData = {
  meta: {
    title: '策略中台', subtitle: '只读', asof: '20261105',
    generated_at: '2026-11-07 11:14:05', source_commit: 'a3cfad8',
    source_dirty: false, sync_id: 'qsync_test', database: 'zhice_qengine_sync_test',
    schema: 'qengine_sync', note: '只读', latest_generated_at: null,
  },
  counts: {
    strategy: 27, pool_strategy: 20, day: 21, pick: 3162, trade: 4856,
    pool_candidate: 76229, issue: 49, pending: 152, latest_picks: 122,
  },
  grade_counts: { A: 2, B: 4, C: 12, D: 2, 未评级: 7 },
  lx_count: 24,
  nav: [{ date: '20261105', nav: 1.016451, ret_pct: 1.676 }],
  latest_nav: { date: '20261105', nav: 1.016451, ret_pct: 1.676 },
  days: [{
    signal_date: '20261105', entry_date: null, n_signal: 10257, n_buy: 0,
    n_strategies: 15, gate_ok: 1, ret: null, excess: null,
  }],
  latest_day: {
    signal_date: '20261105', entry_date: null, n_signal: 10257, n_buy: 0,
    n_strategies: 15, gate_ok: 1, ret: null, excess: null,
  },
  pool: [],
  strategies: [STRATEGY_A, STRATEGY_B],
  latest_picks: [PICK],
  issues: [],
  detail: null,
};

const DETAIL: StrategyCenterDetailPayload = {
  sid: 'LX20',
  strategy: STRATEGY_A,
  days: [{
    signal_date: '20261105', n: 10, closed: 0, open: 10, win: 0.4,
    avg: -0.006, exc: 0.1, level: 'normal', verdict: 'pending',
  }],
  picks: [PICK],
  trades: [{
    track: 'primary', signal_date: '20261028', entry_date: '20261029',
    sid: 'LX20', ts_code: '000739.SZ', name: '普洛药业', status: 'closed',
    ret_net_pct: 13.06, exit_reason: '止盈', hold_days: 5,
  }],
  extremes: { best: null, worst: null },
  issues: [{
    issue_id: 1, scope: 'strategy', code: 'LX20', level: 'C', sev: 1,
    title: '极阴次阳在弱市不稳', status: '新增',
  }],
};

function snapshotReturn(data: StrategyCenterData | null, error = '') {
  return { data, error, loading: !data, reload: vi.fn() };
}

beforeEach(() => {
  vi.clearAllMocks();
  snapshotMock.mockReturnValue(snapshotReturn(DATA));
  detailMock.mockResolvedValue(DETAIL);
});

describe('策略中台组件', () => {
  it('Overview 渲染动态候选数并可重试失败态', () => {
    const { rerender } = render(<StrategyCenterOverview />);
    expect(screen.getByText('总览')).toBeInTheDocument();
    expect(screen.getAllByText('122').length).toBeGreaterThan(0);
    expect(screen.getByText('中泰证券')).toBeInTheDocument();

    const retry = vi.fn();
    snapshotMock.mockReturnValue({ data: null, error: 'API 暂不可用', loading: false, reload: retry });
    rerender(<StrategyCenterOverview />);
    expect(screen.getByText('API 暂不可用')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /重\s*试/ }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('Shelf 按评级过滤，并可打开详情', () => {
    const onOpen = vi.fn();
    render(<StrategyCenterShelf onOpenDetail={onOpen} />);
    expect(screen.getByText('LX20')).toBeInTheDocument();
    expect(screen.getByText('LX16')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^B1$/ }));
    expect(screen.queryByText('LX20')).not.toBeInTheDocument();
    expect(screen.getByText('LX16')).toBeInTheDocument();

    fireEvent.click(screen.getByText('LX16').closest('article') as HTMLElement);
    expect(onOpen).toHaveBeenCalledWith('LX16');
  });

  it('Detail 加载真实接口数据、返回货架', async () => {
    const onBack = vi.fn();
    render(<StrategyCenterDetail sid="LX20" onBack={onBack} />);
    expect(screen.getByText('正在读取 LX20 详情…')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('牛股三绝')).toBeInTheDocument());
    expect(detailMock).toHaveBeenCalledWith('LX20');
    expect(screen.getByText('极阴次阳在弱市不稳')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /返回策略货架/ }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it('Detail 无 sid 时不请求接口', () => {
    render(<StrategyCenterDetail sid={null} onBack={vi.fn()} />);
    expect(screen.getByText('从策略货架点击任意策略，查看只读详情。')).toBeInTheDocument();
    expect(detailMock).not.toHaveBeenCalled();
  });
});
