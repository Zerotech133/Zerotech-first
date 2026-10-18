/**
 * Task 5 UI 测试：我的账户（MyAccountsPage ?tab=accounts）+ 运行实例
 * （MyStrategyInstancesPage ?tab=running）+ StrategiesGroup 集成（Phase D 多用户前端）。
 *
 * 计划红测试语义（docs/superpowers/plans/2026-09-06-multi-user-frontend-migration-rollout.md
 * Task 5 Step 1，照 RTL 适配）：
 * - 账户：dedicated/shared 徽章；可用资金 = 现金余额 − 冻结中（前端计算展示，
 *   mock 100000/30000 → 70,000）；新建校验（市场/类型必选、初始资金>0）；
 *   共享账户展开=实例分组+配额占用合计；独立账户展开=单一实例提示；
 * - 实例：启用开关调 enable/disable；升级横幅两态（available→"有新版本可用"+
 *   确认按钮；pending→"待旧持仓平仓后自动切换"文案+时间）；确认升级调
 *   requestUpgrade(target)；实例级通知偏好（复用 NotificationPreferenceForm 9 键）
 *   → updateMyInstance({notification_preferences:{...,fill:false}})；
 * - VIEWER：运行实例/我的账户两 tab 均不可见。
 *
 * 安全铁律：createMyAccount 请求体绝不携带 user_id（owner 恒取自 JWT）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../components/PageHeader', () => ({ default: () => <div>策略管理</div> }));
// StrategiesGroup 集成测试：mock 掉重页面，running/accounts 页留真组件
vi.mock('../pages/StrategiesPage', () => ({ default: () => null }));
vi.mock('../pages/StrategyPerformancePage', () => ({ default: () => null }));
vi.mock('../pages/ResearchPage', () => ({ default: () => null }));
vi.mock('../pages/BacktestPage', () => ({ default: () => null }));
vi.mock('../pages/ModelOpsPage', () => ({ default: () => null }));
vi.mock('../pages/FundamentalsPage', () => ({ default: () => null }));
vi.mock('../pages/RegimePage', () => ({ default: () => null }));
vi.mock('../pages/StrategyDetailPage', () => ({ default: () => null }));
vi.mock('../pages/StrategyCatalogPage', () => ({ default: () => null }));
vi.mock('../pages/MyStrategiesPage', () => ({ default: () => null }));
// API 客户端 mock：账户/实例读写全走 owner-scoped typed 客户端
vi.mock('../services/myStrategiesApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/myStrategiesApi')>();
  return {
    ...actual,
    listMyStrategies: vi.fn(),
    listMyInstances: vi.fn(),
    enableMyInstance: vi.fn(),
    disableMyInstance: vi.fn(),
    updateMyInstance: vi.fn(),
    requestUpgrade: vi.fn(),
  };
});
vi.mock('../services/myAccountsApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/myAccountsApi')>();
  return { ...actual, listMyAccounts: vi.fn(), createMyAccount: vi.fn() };
});

import '../i18n'; // 触发 i18n 初始化（默认 zh），否则 t() 返回 key 而非中文
import MyAccountsPage from '../pages/MyAccountsPage';
import MyStrategyInstancesPage from '../pages/MyStrategyInstancesPage';
import StrategiesGroup from '../pages/groups/StrategiesGroup';
import { MarketProvider } from '../contexts/MarketContext';
import {
  listMyStrategies,
  listMyInstances,
  enableMyInstance,
  disableMyInstance,
  updateMyInstance,
  requestUpgrade,
  type MyStrategy,
  type MyStrategyInstance,
} from '../services/myStrategiesApi';
import { listMyAccounts, createMyAccount, type MyAccount } from '../services/myAccountsApi';

const mockListAccounts = vi.mocked(listMyAccounts);
const mockCreateAccount = vi.mocked(createMyAccount);
const mockListMy = vi.mocked(listMyStrategies);
const mockListInstances = vi.mocked(listMyInstances);
const mockEnable = vi.mocked(enableMyInstance);
const mockDisable = vi.mocked(disableMyInstance);
const mockUpdateInstance = vi.mocked(updateMyInstance);
const mockUpgrade = vi.mocked(requestUpgrade);

// ---------- 类型化样例（对齐 myApis.test.ts / MultiUserStrategyFlow.test.tsx 契约样例） ----------

const sharedAccount: MyAccount = {
  account_id: 'acc-shared',
  user_id: 'u-alice',
  market: 'CN',
  currency: 'CNY',
  account_type: 'SHARED_PORTFOLIO',
  initial_capital: '500000',
  cash_balance: '100000',
  reserved_cash: '30000',
  status: 'ACTIVE',
};

const dedicatedAccount: MyAccount = {
  ...sharedAccount,
  account_id: 'acc-ded',
  account_type: 'STRATEGY_DEDICATED',
  initial_capital: '100000',
  cash_balance: '80000',
  reserved_cash: '0',
};

const macdStrategy: MyStrategy = {
  strategy_id: 'st-macd',
  strategy_name: 'MACD 趋势',
  market: 'CN',
  visibility: 'PUBLIC',
  publication_status: 'PUBLISHED',
  owner_user_id: null,
  status: 'ACTIVE',
  description: '公开趋势策略',
  version_count: 3,
  latest_version_no: 'v3',
  versions: [
    { strategy_version_id: 'sv-macd-1', version_no: 'v1', status: 'ACTIVE' },
    { strategy_version_id: 'sv-macd-2', version_no: 'v2', status: 'ACTIVE' },
    { strategy_version_id: 'sv-macd-3', version_no: 'v3', status: 'ACTIVE' },
  ],
};

const gammaStrategy: MyStrategy = {
  strategy_id: 'st-gamma',
  strategy_name: 'gamma 单版',
  market: 'CN',
  visibility: 'PRIVATE',
  publication_status: 'PRIVATE',
  owner_user_id: 'u-alice',
  status: 'ACTIVE',
  description: '',
  version_count: 1,
  latest_version_no: 'v1',
  versions: [{ strategy_version_id: 'st-gamma-v1', version_no: 'v1', status: 'ACTIVE' }],
};

/** 共享账户上的启用实例（配额 40000）。 */
const sharedInstance: MyStrategyInstance = {
  user_strategy_id: 'usi-shared',
  user_id: 'u-alice',
  strategy_version_id: 'sv-macd-2',
  account_id: 'acc-shared',
  enabled: true,
  auto_execute: false,
  execution_mode: 'MANUAL',
  capital_quota: '40000',
  max_position_ratio: '0.1',
  risk_parameters: {},
  strategy_parameters: {},
  notification_preferences: {},
};

/** 独立子账户上的停用实例（无配额）。 */
const dedicatedInstance: MyStrategyInstance = {
  ...sharedInstance,
  user_strategy_id: 'usi-ded',
  strategy_version_id: 'st-gamma-v1',
  account_id: 'acc-ded',
  enabled: false,
  capital_quota: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('zhice_role', 'USER'); // 默认 USER（canManageOwn）
});

// ---------- 渲染与交互助手 ----------

function renderWithRouter(ui: React.ReactElement, path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <MarketProvider>{ui}</MarketProvider>
    </MemoryRouter>,
  );
}

function renderAccounts(withGroup = false) {
  return renderWithRouter(
    withGroup ? <StrategiesGroup /> : <MyAccountsPage />,
    '/cn/strategies?tab=accounts',
  );
}

function renderRunning(withGroup = false) {
  return renderWithRouter(
    withGroup ? <StrategiesGroup /> : <MyStrategyInstancesPage />,
    '/cn/strategies?tab=running',
  );
}

/** 展开含指定文本的表格行（antd 展开图标挂在行首 .ant-table-row-expand-icon）。 */
function expandRowContaining(text: string) {
  const row = screen.getByText(text).closest('tr');
  const icon = row?.querySelector('.ant-table-row-expand-icon');
  expect(icon).toBeTruthy();
  fireEvent.click(icon as HTMLElement);
}

/** 行定位：含指定文本的 <tr>（实例行需在 tbody 中，排除表头）。 */
function rowContaining(text: string): HTMLElement {
  const el = screen.getByText(text);
  const row = el.closest('tr');
  if (!row) throw new Error(`row containing "${text}" not found`);
  return row as HTMLElement;
}

/** 在 antd Select 下拉浮层中点选匹配文本的 option。 */
async function selectOption(user: ReturnType<typeof userEvent.setup>, label: RegExp) {
  const option = await screen.findAllByText(label).then((nodes) => {
    const opt = nodes.find((el) => el.closest('.ant-select-item-option'));
    if (!opt) throw new Error(`option ${label} not found`);
    return opt;
  });
  await user.click(option);
}

// ---------- Task 5：我的账户 ----------

describe('我的账户（MyAccountsPage，?tab=accounts）', () => {
  it('类型徽章：共享组合 / 独立策略子账户；可用资金 = 现金余额 − 冻结中（100000−30000 → 70,000）', async () => {
    mockListAccounts.mockResolvedValue([sharedAccount, dedicatedAccount]);
    mockListInstances.mockResolvedValue([]);
    renderAccounts();

    await screen.findByText('acc-shared');
    // 类型徽章
    expect(screen.getByText('共享组合')).toBeInTheDocument();
    expect(screen.getByText('独立策略子账户')).toBeInTheDocument();
    // 可用资金前端计算展示：100000 − 30000 = 70,000
    expect(screen.getByText('70,000')).toBeInTheDocument();
    expect(mockListAccounts).toHaveBeenCalledWith('CN');
  });

  it('新建账户校验：市场/类型必选、初始资金>0；合法提交 createMyAccount（不带 user_id）', async () => {
    const user = userEvent.setup();
    mockListAccounts.mockResolvedValue([]);
    mockListInstances.mockResolvedValue([]);
    mockCreateAccount.mockResolvedValue(sharedAccount);
    renderAccounts();
    // 空表格即可：等新建按钮出现（加载完成后页面稳定）
    await screen.findByRole('button', { name: /新建账户/ });

    await user.click(screen.getByRole('button', { name: /新建账户/ }));

    // 空提交：三项校验全部触发（断言限定 antd 表单错误提示节点，
    // 避免与 Select 占位符"请选择市场/请选择账户类型"同名文本多命中）
    const formErrors = async () => {
      await waitFor(() => {
        expect(
          document.querySelectorAll('.ant-form-item-explain-error').length,
        ).toBeGreaterThan(0);
      });
      return Array.from(document.querySelectorAll('.ant-form-item-explain-error')).map(
        (e) => e.textContent ?? '',
      );
    };
    await user.click(screen.getByRole('button', { name: /创\s*建/ }));
    expect(await formErrors()).toEqual(
      expect.arrayContaining(['请选择市场', '请选择账户类型', '请输入大于 0 的初始资金']),
    );
    expect(mockCreateAccount).not.toHaveBeenCalled();

    // 初始资金为负同样拒绝
    await user.type(screen.getByLabelText('初始资金'), '-5');
    await user.click(screen.getByRole('button', { name: /创\s*建/ }));
    expect(await formErrors()).toContain('请输入大于 0 的初始资金');
    expect(mockCreateAccount).not.toHaveBeenCalled();

    // 合法提交：市场 CN + 共享组合 + 200000
    await user.clear(screen.getByLabelText('初始资金'));
    await user.type(screen.getByLabelText('初始资金'), '200000');
    fireEvent.mouseDown(screen.getByLabelText('市场'));
    await selectOption(user, /CN/);
    fireEvent.mouseDown(screen.getByLabelText('账户类型'));
    await selectOption(user, /共享组合/);
    await user.click(screen.getByRole('button', { name: /创\s*建/ }));

    await waitFor(() => {
      expect(mockCreateAccount).toHaveBeenCalledTimes(1);
    });
    expect(mockCreateAccount).toHaveBeenCalledWith({
      market: 'CN',
      account_type: 'SHARED_PORTFOLIO',
      initial_capital: 200000,
    });
    // 安全铁律：请求体绝不携带 user_id（owner 恒由后端从 JWT 取）
    expect(mockCreateAccount.mock.calls[0][0]).not.toHaveProperty('user_id');
  });

  it('共享账户展开：该账户下实例分组 + 配额占用合计；独立账户展开：单一实例提示', async () => {
    mockListAccounts.mockResolvedValue([sharedAccount, dedicatedAccount]);
    mockListInstances.mockResolvedValue([sharedInstance, dedicatedInstance]);
    renderAccounts();
    await screen.findByText('acc-shared');

    // 共享账户行展开：实例分组（usi-shared + 配额 40,000）+ 配额占用合计
    expandRowContaining('acc-shared');
    expect(await screen.findByText('usi-shared')).toBeInTheDocument();
    expect(screen.getAllByText('40,000').length).toBeGreaterThan(0);
    expect(screen.getByText(/配额占用合计/)).toBeInTheDocument();

    // 独立账户行展开：单一实例提示
    expandRowContaining('acc-ded');
    expect(await screen.findByText(/仅绑定一个运行实例/)).toBeInTheDocument();
  });
});

// ---------- Task 5：运行实例 ----------

describe('运行实例（MyStrategyInstancesPage，?tab=running）', () => {
  function primeMocks(instances: MyStrategyInstance[]) {
    mockListInstances.mockResolvedValue(instances);
    mockListAccounts.mockResolvedValue([sharedAccount, dedicatedAccount]);
    mockListMy.mockResolvedValue([macdStrategy, gammaStrategy]);
  }

  it('实例表格：策略/账户解析 + 启用开关调 enable/disable', async () => {
    const user = userEvent.setup();
    primeMocks([sharedInstance, dedicatedInstance]);
    mockDisable.mockResolvedValue({ ...sharedInstance, enabled: false });
    mockEnable.mockResolvedValue({ ...dedicatedInstance, enabled: true });
    renderRunning();

    // 策略版本解析：strategy_version_id → 策略名（+版本号）
    await screen.findByText('MACD 趋势');
    expect(screen.getByText('gamma 单版')).toBeInTheDocument();
    // 账户解析：账户 ID 可见
    expect(screen.getByText('acc-shared')).toBeInTheDocument();
    expect(mockListInstances).toHaveBeenCalledWith('CN');

    // enabled 实例拨到 off → disableMyInstance
    const sharedRow = rowContaining('MACD 趋势');
    await user.click(within(sharedRow).getByRole('switch'));
    await waitFor(() => {
      expect(mockDisable).toHaveBeenCalledWith('usi-shared');
    });

    // disabled 实例拨到 on → enableMyInstance
    const dedRow = rowContaining('gamma 单版');
    await user.click(within(dedRow).getByRole('switch'));
    await waitFor(() => {
      expect(mockEnable).toHaveBeenCalledWith('usi-ded');
    });
  });

  it('升级横幅 available 态："有新版本可用" + 确认升级按钮；默认目标 = upgrade_available_version_id', async () => {
    const user = userEvent.setup();
    primeMocks([{ ...sharedInstance, upgrade_available_version_id: 'sv-macd-3' }]);
    mockUpgrade.mockResolvedValue({
      ...sharedInstance,
      pending_upgrade_version_id: 'sv-macd-3',
    });
    renderRunning();

    await screen.findByText('MACD 趋势');
    expect(screen.getByText('有新版本可用')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '确认升级' }));
    // 确认弹窗：目标版本默认 = upgrade_available_version_id（sv-macd-3 → v3）
    const modal = await screen
      .findByText('目标版本')
      .then((el) => el.closest('.ant-modal') as HTMLElement);
    expect(modal).toBeTruthy();
    expect(within(modal).getByText(/v3/)).toBeInTheDocument();

    await user.click(within(modal).getByRole('button', { name: '确认升级' }));
    await waitFor(() => {
      expect(mockUpgrade).toHaveBeenCalledWith('usi-shared', 'sv-macd-3');
    });
  });

  it('升级横幅 pending 态："待旧持仓平仓后自动切换"文案 + 申请时间；无确认按钮', async () => {
    primeMocks([{
      ...sharedInstance,
      upgrade_available_version_id: 'sv-macd-3',
      pending_upgrade_version_id: 'sv-macd-3',
      upgrade_requested_at: '2026-09-06T10:00:00+00:00',
    }]);
    renderRunning();

    await screen.findByText('MACD 趋势');
    // 断言限定 .ant-alert：前序用例的成功 toast 挂在 body 且跨用例残留
    const banner = Array.from(document.querySelectorAll('.ant-alert')).find((a) =>
      a.textContent?.includes('升级已确认，待旧持仓平仓后自动切换'),
    );
    expect(banner).toBeTruthy();
    expect(banner?.textContent).toContain('2026-09-06 10:00');
    // pending 态不再提供确认升级按钮
    expect(screen.queryByRole('button', { name: '确认升级' })).not.toBeInTheDocument();
    expect(mockUpgrade).not.toHaveBeenCalled();
  });

  it('升级横幅兜底（Important #4）：无 upgrade_available_version_id 但存在更高可运行版本 → 横幅出现且确认走 requestUpgrade（默认目标=最新候选）', async () => {
    const user = userEvent.setup();
    // upgrade_available_version_id 全仓无写入方（横幅死代码）：页面侧以
    // 同策略"最新可运行版本 > 当前"兜底。sharedInstance 挂 sv-macd-v2，
    // macdStrategy 另有 v3 ACTIVE → 候选非空。
    primeMocks([sharedInstance]);
    mockUpgrade.mockResolvedValue({
      ...sharedInstance,
      pending_upgrade_version_id: 'sv-macd-3',
    });
    renderRunning();

    await screen.findByText('MACD 趋势');
    expect(screen.getByText('有新版本可用')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '确认升级' }));
    const modal = await screen
      .findByText('目标版本')
      .then((el) => el.closest('.ant-modal') as HTMLElement);
    expect(modal).toBeTruthy();
    // 默认目标 = 候选最高版本（后端字段缺失时取页面计算的最新候选）
    expect(within(modal).getByText(/v3/)).toBeInTheDocument();

    await user.click(within(modal).getByRole('button', { name: '确认升级' }));
    await waitFor(() => {
      expect(mockUpgrade).toHaveBeenCalledWith('usi-shared', 'sv-macd-3');
    });
  });

  it('实例通知偏好：取消"成交回报"（fill）→ 保存调 updateMyInstance({notification_preferences:{...,fill:false}})', async () => {
    const user = userEvent.setup();
    primeMocks([sharedInstance]);
    mockUpdateInstance.mockResolvedValue(sharedInstance);
    renderRunning();

    await screen.findByText('MACD 趋势');
    const row = rowContaining('MACD 趋势');
    await user.click(within(row).getByRole('button', { name: '通知偏好' }));

    const modal = await screen
      .findByText(/编辑通知偏好/)
      .then((el) => el.closest('.ant-modal') as HTMLElement);
    expect(modal).toBeTruthy();
    // 9 键开关组渲染（复用 NotificationPreferenceForm）
    expect(within(modal).getByLabelText('交易信号')).toBeChecked();
    // 取消勾选"成交回报"（fill）
    await user.click(within(modal).getByLabelText('成交回报'));

    await user.click(within(modal).getByRole('button', { name: /保\s*存/ }));
    await waitFor(() => {
      expect(mockUpdateInstance).toHaveBeenCalledWith('usi-shared', {
        notification_preferences: expect.objectContaining({ fill: false, signal: true }),
      });
    });
  });
});

// ---------- StrategiesGroup 集成（?tab=running / ?tab=accounts） ----------

describe('StrategiesGroup 集成（?tab=running / ?tab=accounts）', () => {
  it('USER（canManageOwn）："运行实例"与"我的账户" tab 可见并挂载对应页面', async () => {
    mockListInstances.mockResolvedValue([sharedInstance]);
    mockListAccounts.mockResolvedValue([sharedAccount]);
    mockListMy.mockResolvedValue([macdStrategy, gammaStrategy]);
    renderRunning(true);
    expect(screen.getByRole('tab', { name: '运行实例' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '我的账户' })).toBeInTheDocument();
    await screen.findByText('MACD 趋势');
    expect(mockListInstances).toHaveBeenCalledWith('CN');

    // render() 返回的查询绑定 document.body：第二次渲染须 scope 到自身 container
    const accountsView = renderAccounts(true);
    expect(within(accountsView.container).getByRole('tab', { name: '我的账户' })).toBeInTheDocument();
    await within(accountsView.container).findByText('acc-shared');
    expect(mockListAccounts).toHaveBeenCalledWith('CN');
  });

  it('VIEWER（无 canManageOwn）：运行实例/我的账户两 tab 均不可见，直连 ?tab=running 落到第一个可见 tab', () => {
    localStorage.setItem('zhice_role', 'VIEWER');
    renderRunning(true);
    expect(screen.queryByRole('tab', { name: '运行实例' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '我的账户' })).not.toBeInTheDocument();
    // 不发起实例/账户读请求（页面未挂载）
    expect(mockListInstances).not.toHaveBeenCalled();
    expect(mockListAccounts).not.toHaveBeenCalled();
  });
});
