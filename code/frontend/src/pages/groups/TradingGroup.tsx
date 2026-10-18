import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Tabs } from 'antd';
import PageHeader from '../../components/PageHeader';
import PositionsPage from '../PositionsPage';
import AccountsPage from '../AccountsPage';
import PortfolioPage from '../PortfolioPage';
import WorkflowPage from '../WorkflowPage';
import { useRole } from '../../hooks/useRole';

/**
 * 交易组容器（/cn/trading）：positions / accounts / portfolio / workflow 四 Tab。
 * tab key 与 LEGACY_REDIRECTS 对齐：/accounts→accounts、/portfolio→portfolio、/workflow→workflow。
 */
export default function TradingGroup() {
  const { t } = useTranslation('pages');
  const { isViewer } = useRole();
  const [sp, setSp] = useSearchParams();
  const tab = sp.get('tab') ?? 'positions';
  const allItems = [
    { key: 'positions', label: t('tradingGroup.tabPositions'), children: <PositionsPage /> },
    { key: 'accounts', label: t('tradingGroup.tabAccounts'), children: <AccountsPage /> },
    { key: 'portfolio', label: t('tradingGroup.tabPortfolio'), children: <PortfolioPage /> },
    { key: 'workflow', label: t('tradingGroup.tabWorkflow'), children: <WorkflowPage /> },
  ];
  // VIEWER 只读：只留 组合，隐藏 持仓明细/工作流/账户。
  // deep-test 4a：账户 tab（/accounts/stats* 总权益+他人私有策略账户余额）对
  // VIEWER 后端已 403，前端入口同步剥掉，避免进入即报错。
  const items = isViewer ? allItems.filter(i => ['portfolio'].includes(i.key)) : allItems;
  // 兜底：VIEWER 直连隐藏 tab 时落到第一个可见 tab。
  const activeKey = items.some(i => i.key === tab) ? tab : items[0].key;
  return (
    <div>
      <PageHeader groupKey="trading" title={t('tradingGroup.title')} meta={t('tradingGroup.meta')} />
      <Tabs
        activeKey={activeKey}
        onChange={k => setSp({ tab: k }, { replace: true })}
        destroyOnHidden
        items={items}
      />
    </div>
  );
}
