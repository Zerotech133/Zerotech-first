import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Tabs } from 'antd';
import PageHeader from '../../components/PageHeader';
import RiskPage from '../RiskPage';
import AutomationPage from '../AutomationPage';

/**
 * 风控与执行组容器（/cn/risk）：risk / automation 两 Tab。
 * /risk 重定向默认落 risk；automation 为本组自有入口
 * （/automation 旧链接按重定向表落 system 组，熔断器位于自动化页内，两处入口共用同一组件）。
 */
export default function RiskGroup() {
  const { t } = useTranslation('pages');
  const [sp, setSp] = useSearchParams();
  const tab = sp.get('tab') ?? 'risk';
  return (
    <div>
      <PageHeader groupKey="risk" title={t('riskGroup.title')} meta={t('riskGroup.meta')} />
      <Tabs
        activeKey={tab}
        onChange={k => setSp({ tab: k }, { replace: true })}
        destroyOnHidden
        items={[
          { key: 'risk', label: t('riskGroup.tabRisk'), children: <RiskPage /> },
          { key: 'automation', label: t('riskGroup.tabAutomation'), children: <AutomationPage /> },
        ]}
      />
    </div>
  );
}
