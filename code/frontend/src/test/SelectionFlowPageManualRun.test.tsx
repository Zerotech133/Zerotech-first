// task-57：SelectionFlowPage「手动跑一轮」入口组件测试（受控 API mock，不写后端）
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../services/strategyWorkspaceApi', () => ({
  strategyWorkspaceApi: {
    acad1Pool: vi.fn().mockResolvedValue({ rows: [], pool_date: '2026-10-31' }),
    acad1Stats: vi.fn().mockResolvedValue({ pool_total: 0, pool_date: '2026-10-31' }),
    selectionDesk: vi.fn().mockResolvedValue({}),
  },
}));

vi.mock('../services/selectionRunApi', () => ({
  runSelection: vi.fn(),
}));

vi.mock('../contexts/MarketContext', () => ({
  useMarket: () => ({
    market: 'CN',
    setMarket: vi.fn(),
    currency: 'CNY',
    currencySymbol: '¥',
  }),
}));

import SelectionFlowPage from '../pages/SelectionFlowPage';
import { runSelection } from '../services/selectionRunApi';

const mRunSelection = vi.mocked(runSelection);

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/cn/replay?tab=flow']}>
      <SelectionFlowPage />
    </MemoryRouter>,
  );
}

async function renderReady() {
  renderPage();
  // 等首屏加载完成（标题渲染即页面可交互；load() 的请求均已 mock 为 {}）
  await screen.findByText('选股全景动线');
}

async function clickRunAndConfirm() {
  fireEvent.click(await screen.findByTestId('manual-run-button'));
  expect(await screen.findByText('确认手动跑一轮')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '确认执行' }));
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    json: async () => ({}),
  })) as unknown as typeof fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('SelectionFlowPage 手动跑一轮入口', () => {
  it('USER 角色默认隐藏入口', async () => {
    localStorage.setItem('zhice_role', 'USER');
    await renderReady();
    expect(screen.queryByTestId('manual-run-button')).not.toBeInTheDocument();
  });

  it('ADMIN 可见入口；二次确认后才调用 POST /selection/run 并展示 run_id', async () => {
    localStorage.setItem('zhice_role', 'ADMIN');
    mRunSelection.mockResolvedValue({
      run_id: 'selrun-20261102-001',
      status: 'succeeded',
      market: 'CN',
      trade_date: '2026-11-02',
      duration_sec: 12.3,
      result_summary: { candidates: 10 },
    });
    await renderReady();

    // 仅点击按钮不应直接调用（需二次确认）
    fireEvent.click(screen.getByTestId('manual-run-button'));
    expect(await screen.findByText('确认手动跑一轮')).toBeInTheDocument();
    expect(mRunSelection).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '确认执行' }));
    await waitFor(() => expect(mRunSelection).toHaveBeenCalledTimes(1));
    expect(mRunSelection.mock.calls[0][0]).toMatchObject({
      market: 'CN',
      strategy_type: 'TREND',
      max_candidates: 10,
    });
    // 幂等键随请求下发（网络重试可安全回放）
    expect(mRunSelection.mock.calls[0][1]).toBeTruthy();

    expect(await screen.findByText('手动选股 succeeded')).toBeInTheDocument();
    expect(screen.getByText(/selrun-20261102-001/)).toBeInTheDocument();
  });

  it('TRADER 可见入口', async () => {
    localStorage.setItem('zhice_role', 'TRADER');
    await renderReady();
    expect(screen.getByTestId('manual-run-button')).toBeInTheDocument();
  });

  it('取消二次确认不调用接口', async () => {
    localStorage.setItem('zhice_role', 'ADMIN');
    await renderReady();
    fireEvent.click(screen.getByTestId('manual-run-button'));
    expect(await screen.findByText('确认手动跑一轮')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /取\s*消/ }));
    // antd Modal 未设 destroyOnClose 时关闭后节点仍留在 DOM（隐藏），以“未调用接口”为断言。
    await waitFor(() => expect(mRunSelection).not.toHaveBeenCalled());
  });

  it('flag 关闭（503 selection_run.disabled）→ 隐藏入口并提示未开启', async () => {
    localStorage.setItem('zhice_role', 'ADMIN');
    mRunSelection.mockRejectedValue(Object.assign(new Error('flag off'), {
      status: 503,
      code: 'selection_run.disabled',
    }));
    await renderReady();

    await clickRunAndConfirm();
    await waitFor(() => expect(mRunSelection).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('手动跑一轮未开启')).toBeInTheDocument();
    expect(screen.queryByTestId('manual-run-button')).not.toBeInTheDocument();
  });

  it('接口未发布（404）同样隐藏入口', async () => {
    localStorage.setItem('zhice_role', 'ADMIN');
    mRunSelection.mockRejectedValue(Object.assign(new Error('HTTP 404'), {
      status: 404,
      code: 'not_found',
    }));
    await renderReady();
    await clickRunAndConfirm();
    await waitFor(() => expect(mRunSelection).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('手动跑一轮未开启')).toBeInTheDocument();
    expect(screen.queryByTestId('manual-run-button')).not.toBeInTheDocument();
  });

  it('并发锁 409 → 保留入口并展示错误', async () => {
    localStorage.setItem('zhice_role', 'ADMIN');
    mRunSelection.mockRejectedValue(Object.assign(
      new Error('已有手动选股正在执行，请稍后重试'),
      { status: 409, code: 'selection.run_in_progress' },
    ));
    await renderReady();
    await clickRunAndConfirm();
    expect(await screen.findByText('手动选股失败')).toBeInTheDocument();
    expect(screen.getByText(/已有手动选股正在执行/)).toBeInTheDocument();
    expect(screen.getByTestId('manual-run-button')).toBeInTheDocument();
  });
});
