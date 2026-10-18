import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Tabs } from 'antd';
import PageHeader from '../../components/PageHeader';
import DashboardPage from '../DashboardPage';
import MarketPage from '../MarketPage';
import KlinePage from '../KlinePage';
import MarketOverviewPage from '../MarketOverviewPage';

/**
 * 总览组容器（/cn/overview）：dashboard / quotes / kline / overview 四 Tab。
 * tab key 与 LEGACY_REDIRECTS 对齐：/market→quotes、/kline→kline、/market-overview→overview。
 * KlinePage 自身已监听 searchParams.symbol（useEffect 依赖 [searchParams]），
 * SymbolLink 跳 /kline?symbol=x 经重定向到 ?tab=kline&symbol=x 后由其自行消费，无需转译。
 */
export default function OverviewGroup() {
  const { t } = useTranslation('pages');
  const [sp, setSp] = useSearchParams();
  const tab = sp.get('tab') ?? 'dashboard';
  return (
    <div>
      <PageHeader
        groupKey="overview"
        title={t('overviewGroup.title')}
        meta={t('overviewGroup.meta')}
      />
      <Tabs
        activeKey={tab}
        onChange={k => setSp({ tab: k }, { replace: true })}
        destroyOnHidden
        items={[
          { key: 'dashboard', label: t('overviewGroup.tabDashboard'), children: <DashboardPage /> },
          { key: 'quotes', label: t('overviewGroup.tabQuotes'), children: <MarketPage /> },
          { key: 'kline', label: t('overviewGroup.tabKline'), children: <KlinePage /> },
          { key: 'overview', label: t('overviewGroup.tabOverview'), children: <MarketOverviewPage /> },
        ]}
      />
    </div>
  );
}
