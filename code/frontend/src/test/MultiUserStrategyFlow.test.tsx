/**
 * Task 3 UI 测试：策略广场（StrategyCatalogPage）+ 启用向导（EnableStrategyWizard）
 * + StrategiesGroup catalog tab 集成（Phase D 多用户前端）。
 *
 * 计划红测试语义（docs/superpowers/plans/2026-09-06-multi-user-frontend-migration-rollout.md
 * Task 3 Step 1，照 RTL 适配）：
 * - USER 点"使用策略"→ 向导默认"独立策略子账户"已选 → 填初始资金 100000 → 走完六步
 *   → createMyInstance 收到 {account_mode:'NEW_DEDICATED', initial_capital:100000}
 *   → 确认后 enableMyInstance 被调（create disabled → enable 两步提交）；
 * - 共享账户分支：选"绑定已有共享账户"→ 下拉选本人账户 → submit 收到 account_id；
 * - 校验：初始资金空/负禁止下一步；max_position_ratio 越界拒绝；
 * - VIEWER：无"使用策略"按钮（canManageOwn=false）。
 *
 * 安全铁律：createMyInstance 请求体绝不携带 user_id/owner_user_id（owner 恒取自 JWT）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../components/PageHeader', () => ({ default: () => <div>策略管理</div> }));
// StrategiesGroup 集成测试：mock 掉重页面，catalog/mine 页留真组件
vi.mock('../pages/StrategiesPage', () => ({ default: () => null }));
vi.mock('../pages/StrategyPerformancePage', () => ({ default: () => null }));
vi.mock('../pages/ResearchPage', () => ({ default: () => null }));
vi.mock('../pages/BacktestPage', () => ({ default: () => null }));
vi.mock('../pages/ModelOpsPage', () => ({ default: () => null }));
vi.mock('../pages/FundamentalsPage', () => ({ default: () => null }));
vi.mock('../pages/RegimePage', () => ({ default: () => null }));
vi.mock('../pages/StrategyDetailPage', () => ({ default: () => null }));
// API 客户端 mock：目录加载 + 向导两步提交（create disabled → enable）+ 我的私有策略面（T4）
vi.mock('../services/myStrategiesApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/myStrategiesApi')>();
  return {
    ...actual,
    listCatalog: vi.fn(),
    createMyInstance: vi.fn(),
    enableMyInstance: vi.fn(),
    listMyStrategies: vi.fn(),
    updatePrivateStrategy: vi.fn(),
    requestPublication: vi.fn(),
    createMyStrategyVersion: vi.fn(),
    getMyStrategySpec: vi.fn(),
  };
});
vi.mock('../services/myAccountsApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/myAccountsApi')>();
  return { ...actual, listMyAccounts: vi.fn() };
});

import '../i18n'; // 触发 i18n 初始化（默认 zh），否则 t() 返回 key 而非中文
import StrategyCatalogPage from '../pages/StrategyCatalogPage';
import MyStrategiesPage from '../pages/MyStrategiesPage';
import StrategiesGroup from '../pages/groups/StrategiesGroup';
import { MarketProvider } from '../contexts/MarketContext';
import {
  listCatalog,
  createMyInstance,
  enableMyInstance,
  listMyStrategies,
  updatePrivateStrategy,
  requestPublication,
  createMyStrategyVersion,
  getMyStrategySpec,
  type CatalogStrategy,
  type MyStrategy,
  type MyStrategyInstance,
} from '../services/myStrategiesApi';
import { listMyAccounts, type MyAccount } from '../services/myAccountsApi';

const mockListCatalog = vi.mocked(listCatalog);
const mockCreate = vi.mocked(createMyInstance);
const mockEnable = vi.mocked(enableMyInstance);
const mockListAccounts = vi.mocked(listMyAccounts);
const mockListMy = vi.mocked(listMyStrategies);
const mockUpdate = vi.mocked(updatePrivateStrategy);
const mockPublish = vi.mocked(requestPublication);
const mockCreateVersion = vi.mocked(createMyStrategyVersion);
const mockGetSpec = vi.mocked(getMyStrategySpec);

// ---------- 类型化样例（对齐 myApis.test.ts 契约样例） ----------

const catalogRow: CatalogStrategy = {
  strategy_id: 'st-macd',
  strategy_name: 'MACD 趋势',
  description: '公开趋势策略',
  market: 'CN',
  strategy_type: 'MACD',
  status: 'ACTIVE',
  is_builtin: true,
  owner_user_id: null,
  version_count: 2,
  // 真形态：version_no 带 v 前缀；版本状态 STAGING/ACTIVE（对齐后端
  // _RUNNABLE_VERSION_STATUSES，StrategyStatus 枚举无 PUBLISHED）
  versions: [
    { strategy_version_id: 'sv-macd-1', version_no: 'v1', status: 'ACTIVE' },
    { strategy_version_id: 'sv-macd-2', version_no: 'v2', status: 'STAGING' },
  ],
};

const createdInstance: MyStrategyInstance = {
  user_strategy_id: 'usi-1',
  user_id: 'u-alice',
  strategy_version_id: 'sv-macd-2',
  account_id: 'acc-new-1',
  enabled: false, // 后端 create 恒建 disabled 实例，enable 是独立一步
  auto_execute: false,
  execution_mode: 'MANUAL',
  capital_quota: null,
  max_position_ratio: '0.1',
  risk_parameters: {},
  strategy_parameters: {},
  notification_preferences: {},
};

const enabledInstance: MyStrategyInstance = { ...createdInstance, enabled: true };

const sharedAccount: MyAccount = {
  account_id: 'acc-1',
  user_id: 'u-alice',
  market: 'CN',
  currency: 'CNY',
  account_type: 'SHARED_PORTFOLIO',
  initial_capital: '500000',
  cash_balance: '450000',
  reserved_cash: '0',
  status: 'ACTIVE',
};

// ---------- 渲染与向导走步助手 ----------

function renderCatalog(path = '/cn/strategies') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <MarketProvider>
        <StrategyCatalogPage />
      </MarketProvider>
    </MemoryRouter>,
  );
}

/** 渲染目录并点"使用策略"打开向导，停在步骤 1（版本确认）。 */
async function openWizard(user: ReturnType<typeof userEvent.setup>) {
  mockListCatalog.mockResolvedValue([catalogRow]);
  renderCatalog();
  await screen.findByText('MACD 趋势');
  await user.click(screen.getByRole('button', { name: '使用策略' }));
  await screen.findByText('版本确认');
}

/**
 * 向导内联校验错误（antd Alert）。
 * ⚠️ 必须限定在 .ant-modal 内：antd 静态 message.success（向导成功 toast）与
 * 表格分页等全局节点也挂在 document.body，跨测试残留会污染 getByRole('alert')。
 */
function wizardAlert(): HTMLElement {
  const modal = document.querySelector('.ant-modal');
  if (!modal) throw new Error('wizard modal not mounted');
  return within(modal as HTMLElement).getByRole('alert');
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('zhice_role', 'USER'); // 默认 USER（canManageOwn）
});

// ---------- 主流程：默认独立子账户 ----------

describe('策略广场与启用向导（USER）', () => {
  it('默认独立子账户 + 初始资金 100000：createMyInstance(NEW_DEDICATED) → enableMyInstance 两步提交', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue(createdInstance);
    mockEnable.mockResolvedValue(enabledInstance);
    await openWizard(user);

    // 版本单选渲染真形态 "v2"（而非 "vv2"，Minor #5）
    const radioV2 = screen.getByRole('radio', { name: /v2/ }).closest('label');
    expect(radioV2?.textContent).not.toMatch(/vv/);
    expect(radioV2?.textContent).toContain('STAGING');

    // 步骤 1 版本确认：默认最新可运行版本（v2），直接下一步
    await user.click(screen.getByRole('button', { name: '下一步' }));

    // 步骤 2 账户模式：默认"独立策略子账户"单选已选
    expect(screen.getByLabelText('独立策略子账户')).toBeChecked();
    await user.click(screen.getByRole('button', { name: '下一步' }));

    // 步骤 3 资金与风险：填初始资金 100000（默认最大持仓比例 0.1 合法）
    await user.type(screen.getByLabelText('初始资金'), '100000');
    await user.click(screen.getByRole('button', { name: '下一步' }));

    // 步骤 4 执行模式：默认 MANUAL
    await user.click(screen.getByRole('button', { name: '下一步' }));

    // 步骤 5 通知偏好：默认 9 键全开（DEFAULT_NOTIFICATION_PREFERENCES 语义）
    await user.click(screen.getByRole('button', { name: '下一步' }));

    // 步骤 6 确认：确认启用 → create(disabled) → enable 两步
    await user.click(screen.getByRole('button', { name: '确认启用' }));

    await waitFor(() => {
      expect(mockEnable).toHaveBeenCalledWith('usi-1');
    });
    expect(mockCreate).toHaveBeenCalledTimes(1);
    const body = mockCreate.mock.calls[0][0];
    expect(body).toEqual(
      expect.objectContaining({
        strategy_version_id: 'sv-macd-2',
        account_mode: 'NEW_DEDICATED',
        initial_capital: 100000,
        max_position_ratio: 0.1,
        execution_mode: 'MANUAL',
      }),
    );
    // 通知偏好：后端 DEFAULT_NOTIFICATION_PREFERENCES 同源 9 键全开
    const prefs = body.notification_preferences ?? {};
    expect(Object.keys(prefs)).toHaveLength(9);
    expect(prefs).toEqual(
      expect.objectContaining({ signal: true, fill: true, runtime_error: true }),
    );
    // 安全铁律：请求体绝不携带 owner 键
    expect(body).not.toHaveProperty('user_id');
    expect(body).not.toHaveProperty('owner_user_id');
  });

  it('共享账户分支：绑定已有共享账户 → 下拉选本人账户 → submit 收到 account_id 与配额', async () => {
    const user = userEvent.setup();
    mockCreate.mockResolvedValue({
      ...createdInstance,
      account_id: 'acc-1',
      capital_quota: '50000',
    });
    mockEnable.mockResolvedValue(enabledInstance);
    mockListAccounts.mockResolvedValue([sharedAccount]);
    await openWizard(user);

    await user.click(screen.getByRole('button', { name: '下一步' }));

    // 步骤 2：切到"绑定已有共享账户"，出现本人账户下拉
    await user.click(screen.getByLabelText('绑定已有共享账户'));
    expect(screen.getByLabelText('独立策略子账户')).not.toBeChecked();
    // Select 经 label（htmlFor=wz-account）定位（表格分页也会渲染 combobox）
    fireEvent.mouseDown(screen.getByLabelText('绑定账户'));
    // rc-select 可能渲染多份文本（虚拟列表/选中态），取真正的下拉 option 节点
    const optionNodes = await screen.findAllByText(/acc-1/);
    const option = optionNodes.find((el) => el.closest('.ant-select-item-option'));
    expect(option).toBeTruthy();
    fireEvent.click(option!);

    await user.click(screen.getByRole('button', { name: '下一步' }));

    // 步骤 3：共享账户无"初始资金"，改为"资金配额"（enable 时必填）
    expect(screen.queryByLabelText('初始资金')).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('资金配额'), '50000');
    await user.click(screen.getByRole('button', { name: '下一步' }));

    await user.click(screen.getByRole('button', { name: '下一步' })); // 执行模式
    await user.click(screen.getByRole('button', { name: '下一步' })); // 通知偏好
    await user.click(screen.getByRole('button', { name: '确认启用' }));

    await waitFor(() => {
      expect(mockEnable).toHaveBeenCalledWith('usi-1');
    });
    const body = mockCreate.mock.calls[0][0];
    expect(body).toEqual(
      expect.objectContaining({
        account_mode: 'EXISTING_SHARED',
        account_id: 'acc-1',
        capital_quota: 50000,
      }),
    );
    // 绑定既有账户时不存在"新建独立子账户的初始资金"字段
    expect(body).not.toHaveProperty('initial_capital');
    expect(body).not.toHaveProperty('user_id');
  });

  // ---------- 校验 ----------

  it('校验：初始资金为空/负数 → 禁止下一步（留在资金步骤并报错）', async () => {
    const user = userEvent.setup();
    await openWizard(user);
    await user.click(screen.getByRole('button', { name: '下一步' })); // → 账户模式
    await user.click(screen.getByRole('button', { name: '下一步' })); // → 资金与风险（初始资金为空）

    await user.click(screen.getByRole('button', { name: '下一步' }));
    expect(wizardAlert()).toHaveTextContent('初始资金');
    expect(screen.getByLabelText('初始资金')).toBeInTheDocument(); // 未前进

    // 负数同样拒绝
    await user.type(screen.getByLabelText('初始资金'), '-5');
    await user.click(screen.getByRole('button', { name: '下一步' }));
    expect(wizardAlert()).toHaveTextContent('初始资金');
  });

  it('校验：max_position_ratio 越界（>1）拒绝', async () => {
    const user = userEvent.setup();
    await openWizard(user);
    await user.click(screen.getByRole('button', { name: '下一步' })); // → 账户模式
    await user.click(screen.getByRole('button', { name: '下一步' })); // → 资金与风险

    await user.type(screen.getByLabelText('初始资金'), '100000');
    // InputNumber 追加式输入会与默认值 0.1 拼成非法串；整体替换为越界值 1.5
    fireEvent.change(screen.getByLabelText('最大持仓比例'), { target: { value: '1.5' } });
    await user.click(screen.getByRole('button', { name: '下一步' }));
    expect(wizardAlert()).toHaveTextContent('最大持仓比例');
    expect(screen.getByLabelText('初始资金')).toBeInTheDocument(); // 未前进
  });

  // ---------- 角色可见性 ----------

  it('VIEWER：目录可见但无"使用策略"按钮', async () => {
    localStorage.setItem('zhice_role', 'VIEWER');
    mockListCatalog.mockResolvedValue([catalogRow]);
    renderCatalog();
    await screen.findByText('MACD 趋势');
    expect(screen.queryByRole('button', { name: '使用策略' })).not.toBeInTheDocument();
  });
});

// ---------- StrategiesGroup 集成 ----------

describe('StrategiesGroup 集成（?tab=catalog）', () => {
  it('/:market/strategies?tab=catalog 挂策略广场，现有"策略版本" tab 保留', async () => {
    mockListCatalog.mockResolvedValue([catalogRow]);
    render(
      <MemoryRouter initialEntries={['/cn/strategies?tab=catalog']}>
        <MarketProvider>
          <StrategiesGroup />
        </MarketProvider>
      </MemoryRouter>,
    );
    expect(screen.getByRole('tab', { name: '策略广场' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '策略版本' })).toBeInTheDocument();
    // catalog tab 激活并渲染目录数据
    await screen.findByText('MACD 趋势');
    expect(mockListCatalog).toHaveBeenCalledWith('CN');
  });
});

// ---------- Task 4：我的私有策略 / 发布申请 / 版本升级（策略侧） ----------

/**
 * 计划红测试语义（Task 4 Step 1，照 RTL 适配）：
 * - Alice 只看到本人私有行（/my/strategies 返回 PUBLIC+PRIVATE 混合 → 前端过滤
 *   visibility=PRIVATE，公共行不可见即无编辑/删除入口）；
 * - spec 不可变：编辑参数入口弹"创建新版本 vN"确认 → 调 createMyStrategyVersion，
 *   绝不 PUT 旧版本；
 * - 发布申请不能重复提交：PENDING_REVIEW 禁用+文案；409 already_published 文案；
 * - 升级边界（策略侧）：多版本行显示"有新版本"提示；实例升级按钮归 T5（running 页）。
 */

const myPrivateRow: MyStrategy = {
  strategy_id: 'st-alpha',
  strategy_name: 'alpha 私有',
  market: 'CN',
  visibility: 'PRIVATE',
  publication_status: 'PRIVATE',
  owner_user_id: 'u-alice',
  status: 'ACTIVE',
  description: '我的 alpha 策略',
  version_count: 2,
  latest_version_no: 'v2',
  versions: [
    { strategy_version_id: 'st-alpha-v1', version_no: 'v1', status: 'ACTIVE' },
    { strategy_version_id: 'st-alpha-v2-0a1b', version_no: 'v2', status: 'STAGING' },
  ],
};

const singlePrivateRow: MyStrategy = {
  strategy_id: 'st-gamma',
  strategy_name: 'gamma 单版',
  market: 'CN',
  visibility: 'PRIVATE',
  publication_status: 'PRIVATE',
  owner_user_id: 'u-alice',
  status: 'DRAFT',
  description: '',
  version_count: 1,
  latest_version_no: 'v1',
  versions: [{ strategy_version_id: 'st-gamma-v1', version_no: 'v1', status: 'DRAFT' }],
};

const pendingReviewRow: MyStrategy = {
  ...singlePrivateRow,
  strategy_id: 'st-beta',
  strategy_name: 'beta 待审',
  publication_status: 'PENDING_REVIEW',
};

const publicMyRow: MyStrategy = {
  strategy_id: 'st-macd-pub',
  strategy_name: 'MACD 公共',
  market: 'CN',
  visibility: 'PUBLIC',
  publication_status: 'PUBLISHED',
  owner_user_id: null,
  status: 'ACTIVE',
  description: '平台公共策略',
  version_count: 3,
  latest_version_no: 'v3',
  versions: [
    { strategy_version_id: 'sv-m1', version_no: 'v1', status: 'ACTIVE' },
    { strategy_version_id: 'sv-m2', version_no: 'v2', status: 'ACTIVE' },
    { strategy_version_id: 'sv-m3', version_no: 'v3', status: 'ACTIVE' },
  ],
};

function renderMine(withGroup = false) {
  return render(
    <MemoryRouter initialEntries={['/cn/strategies?tab=mine']}>
      <MarketProvider>{withGroup ? <StrategiesGroup /> : <MyStrategiesPage />}</MarketProvider>
    </MemoryRouter>,
  );
}

describe('我的策略（MyStrategiesPage，?tab=mine）', () => {
  it('私有列表只显示本人私有行：PUBLIC+PRIVATE 混合 → 仅渲染 PRIVATE（公共行不可见即无编辑/删除）', async () => {
    mockListMy.mockResolvedValue([publicMyRow, myPrivateRow]);
    renderMine();
    await screen.findByText('alpha 私有');
    expect(mockListMy).toHaveBeenCalledWith('CN');
    // 公共行被过滤：既无行也无任何操作入口
    expect(screen.queryByText('MACD 公共')).not.toBeInTheDocument();
    expect(screen.queryByText('删除')).not.toBeInTheDocument();
  });

  it('私有行操作矩阵：编辑（名称保存走 updatePrivateStrategy，不带 spec）', async () => {
    const user = userEvent.setup();
    mockListMy.mockResolvedValue([myPrivateRow]);
    mockUpdate.mockResolvedValue({ ...myPrivateRow, strategy_name: '新名字' });
    renderMine();
    await screen.findByText('alpha 私有');

    await user.click(screen.getByText('编辑'));
    const nameInput = screen.getByLabelText('策略名称');
    expect(nameInput).toHaveValue('alpha 私有');
    await user.clear(nameInput);
    await user.type(nameInput, '新名字');
    // antd Button 对两个中文字符自动插空格（保存→"保 存"），用正则兼容
    await user.click(screen.getByRole('button', { name: /保\s*存/ }));

    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalledWith('st-alpha', expect.objectContaining({ name: '新名字' }));
    });
    // 编辑元信息绝不携带 spec（spec 变更只能走"新版本"）
    expect(mockUpdate.mock.calls[0][1]).not.toHaveProperty('spec');
  });

  it('spec 编辑入口=创建新版本确认弹窗：文案"将创建新版本 v3（旧版本保持不变）"，调 createMyStrategyVersion 而非改旧版本', async () => {
    const user = userEvent.setup();
    mockListMy.mockResolvedValue([myPrivateRow]);
    const currentSpec = { spec_version: 1, market: 'CN', universe: { type: 'all' } };
    mockGetSpec.mockResolvedValue({
      kind: 'spec',
      editable: true,
      content: JSON.stringify(currentSpec, null, 2),
      strategy_type: 'V2_EXTERNAL',
      strategy_version_id: 'st-alpha-v2-0a1b',
    });
    mockCreateVersion.mockResolvedValue({
      strategy_version_id: 'st-alpha-v3-9f8e',
      strategy_id: 'st-alpha',
      version_no: 'v3',
      status: 'STAGING',
    });
    renderMine();
    await screen.findByText('alpha 私有');

    await user.click(screen.getByText('新版本'));
    // ⚠️ 前序用例的 antd Modal 关闭动画在 jsdom 不触发会残留 .ant-modal，
    // 按标题文本锁定本用例的弹窗再 scope。
    const modal = Array.from(document.querySelectorAll('.ant-modal')).find(
      (m) => m.textContent?.includes('创建新版本'),
    ) as HTMLElement;
    expect(modal).toBeTruthy();
    // 确认文案：下一版本号 = 最新 v2 + 1 = v3；旧版本不可变提示
    expect(within(modal).getByText(/将创建新版本 v3/)).toBeInTheDocument();
    expect(within(modal).getByText(/旧版本保持不变/)).toBeInTheDocument();
    // spec 预填当前版本内容（getMyStrategySpec）
    expect(mockGetSpec).toHaveBeenCalledWith('st-alpha');
    expect(within(modal).getByLabelText(/spec/)).toHaveValue(JSON.stringify(currentSpec, null, 2));

    await user.click(within(modal).getByRole('button', { name: '创建新版本' }));
    await waitFor(() => {
      expect(mockCreateVersion).toHaveBeenCalledTimes(1);
    });
    expect(mockCreateVersion).toHaveBeenCalledWith('st-alpha', currentSpec);
    // 不可变：绝不 PUT 更新旧策略 spec
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it('PENDING_REVIEW 行：发布申请按钮禁用并提示审核中（不能重复提交）', async () => {
    mockListMy.mockResolvedValue([pendingReviewRow]);
    renderMine();
    await screen.findByText('beta 待审');
    expect(screen.getByRole('button', { name: '申请发布' })).toBeDisabled();
    expect(screen.getByText('审核中，请等待管理员审核后再操作')).toBeInTheDocument();
    expect(mockPublish).not.toHaveBeenCalled();
  });

  it('409 already_published：展示"已发布过公共版本"文案（api() 抛出的 detail code 映射）', async () => {
    const user = userEvent.setup();
    mockListMy.mockResolvedValue([singlePrivateRow]);
    mockPublish.mockRejectedValue(new Error('{"detail":"strategy.already_published"}'));
    renderMine();
    await screen.findByText('gamma 单版');

    await user.click(screen.getByRole('button', { name: '申请发布' }));
    // ⚠️ 不用 findByRole('alert')：antd message toast 也是 role=alert 且跨用例残留
    const err = await screen.findByText('该策略已发布过公共版本，不能重复申请');
    expect(err.closest('.ant-alert')).toBeTruthy(); // 确系页面 Alert 而非 toast
  });

  it('升级边界（策略侧）：多版本行显示"有新版本"提示，单版本行不显示（实例升级按钮归 T5 running 页）', async () => {
    mockListMy.mockResolvedValue([myPrivateRow, singlePrivateRow]);
    renderMine();
    await screen.findByText('alpha 私有');
    await screen.findByText('gamma 单版');
    expect(screen.getAllByText('有新版本')).toHaveLength(1); // 仅 alpha（v2>v1）
    // mine 页不含实例区/升级按钮（upgrade_available/pending 横幅在 T5 运行实例页）
    expect(screen.queryByText(/旧持仓平仓后升级/)).not.toBeInTheDocument();
  });
});

describe('StrategiesGroup 集成（?tab=mine）', () => {
  it('USER（canManageOwn）：策略广场之后挂"我的策略" tab 并渲染本人私有数据', async () => {
    mockListMy.mockResolvedValue([myPrivateRow]);
    renderMine(true);
    expect(screen.getByRole('tab', { name: '我的策略' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '策略广场' })).toBeInTheDocument();
    await screen.findByText('alpha 私有');
    expect(mockListMy).toHaveBeenCalledWith('CN');
  });

  it('VIEWER（无 canManageOwn）："我的策略" tab 不可见，直连 ?tab=mine 落到第一个可见 tab', () => {
    localStorage.setItem('zhice_role', 'VIEWER');
    mockListCatalog.mockResolvedValue([]);
    renderMine(true);
    expect(screen.queryByRole('tab', { name: '我的策略' })).not.toBeInTheDocument();
    expect(screen.queryByText('alpha 私有')).not.toBeInTheDocument();
    expect(mockListMy).not.toHaveBeenCalled();
  });
});
