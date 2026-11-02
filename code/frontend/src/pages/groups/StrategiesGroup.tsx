import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Tabs } from 'antd';
import PageHeader from '../../components/PageHeader';
import StrategyCatalogPage from '../StrategyCatalogPage';
import MyStrategiesPage from '../MyStrategiesPage';
import MyStrategyInstancesPage from '../MyStrategyInstancesPage';
import MyAccountsPage from '../MyAccountsPage';
import StrategiesPage from '../StrategiesPage';
import StrategyPerformancePage from '../StrategyPerformancePage';
import ResearchPage from '../ResearchPage';
import BacktestPage from '../BacktestPage';
import ModelOpsPage from '../ModelOpsPage';
import FundamentalsPage from '../FundamentalsPage';
import RegimePage from '../RegimePage';
import StrategyDetailPage from '../StrategyDetailPage';
import StrategyWorkspacePage from '../StrategyWorkspacePage';
import { useRole } from '../../hooks/useRole';

/**
 * 策略管理组容器（/cn/strategies）：11 Tab（T3 增 catalog 策略广场；T4 增 mine
 * 我的策略；T5 增 running 运行实例 + accounts 我的账户，均仅 canManageOwn 可见）。
 * tab key 与 LEGACY_REDIRECTS 对齐：
 * /strategy-performance→perf、/research→ai、/backtest→backtest、
 * /model-ops→models、/fundamentals→fund、/regime→regime。
 * ?id= 存在时（/strategies/:id 重定向而来）整块替换 Tab 内容为策略详情页。
 * 默认 tab 仍为 strategies（收藏/深链兼容）。
 */
export default function StrategiesGroup() {
  const { t } = useTranslation('pages');
  const { isViewer, canManageOwn } = useRole();
  const [sp, setSp] = useSearchParams();
  const tab = sp.get('tab') ?? 'strategies';
  const strategyId = sp.get('id');

  if (strategyId) {
    return (
      <div>
        <PageHeader groupKey="strategies" title={t('strategiesGroup.title')} meta={t('strategiesGroup.detail', { id: strategyId })} />
        <StrategyDetailPage id={strategyId} />
      </div>
    );
  }

  const allItems = [
    // 策略广场（公共目录→启用向导）对所有角色只读可见；"使用策略"按钮按 canManageOwn 裁剪。
    { key: 'catalog', label: t('strategiesGroup.tabCatalog'), children: <StrategyCatalogPage /> },
    // 我的策略（Phase D T4）：本人私有策略（编辑/新版本/发布申请），仅 canManageOwn 可见。
    // 运行实例（Phase D T5）：启停/升级横幅/通知偏好，仅 canManageOwn 可见。
    // 我的账户（Phase D T5）：账户列表/可用资金/新建账户，仅 canManageOwn 可见。
    ...(canManageOwn
      ? [
          { key: 'mine', label: t('strategiesGroup.tabMine'), children: <MyStrategiesPage /> },
          { key: 'running', label: t('strategiesGroup.tabRunning'), children: <MyStrategyInstancesPage /> },
          { key: 'accounts', label: t('strategiesGroup.tabAccounts'), children: <MyAccountsPage /> },
        ]
      : []),
    { key: 'strategies', label: t('strategiesGroup.tabStrategies'), children: <StrategiesPage /> },
    { key: 'workspace', label: '策略工作台', children: <StrategyWorkspacePage /> },
    { key: 'perf', label: t('strategiesGroup.tabPerf'), children: <StrategyPerformancePage /> },
    { key: 'ai', label: t('strategiesGroup.tabAi'), children: <ResearchPage /> },
    { key: 'backtest', label: t('strategiesGroup.tabBacktest'), children: <BacktestPage /> },
    { key: 'models', label: t('strategiesGroup.tabModels'), children: <ModelOpsPage /> },
    { key: 'fund', label: t('strategiesGroup.tabFund'), children: <FundamentalsPage /> },
    { key: 'regime', label: t('strategiesGroup.tabRegime'), children: <RegimePage /> },
  ];
  // VIEWER 只读：只留 策略广场 + 策略列表 + 绩效，隐藏 ai/回测/模型/基本面/regime。
  const items = isViewer ? allItems.filter(i => ['catalog', 'strategies', 'perf'].includes(i.key)) : allItems;
  // 兜底：VIEWER/无 canManageOwn 直连隐藏 tab 时落到第一个可见 tab。
  const activeKey = items.some(i => i.key === tab) ? tab : items[0].key;

  return (
    <div>
      <PageHeader
        groupKey="strategies"
        title={t('strategiesGroup.title')}
        meta={t('strategiesGroup.meta')}
      />
      <Tabs
        activeKey={activeKey}
        onChange={k => setSp({ tab: k }, { replace: true })}
        destroyOnHidden
        items={items}
      />
    </div>
  );
}
