import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Result, Tabs } from 'antd';
import PageHeader from '../../components/PageHeader';
import SystemStatusPage from '../SystemStatusPage';
import MonitorPage from '../MonitorPage';
import AlertsPage from '../AlertsPage';
import AutomationPage from '../AutomationPage';
import GovernanceBoardPage from '../GovernanceBoardPage';
import MultiStrategyPage from '../MultiStrategyPage';
import CalendarPage from '../CalendarPage';
import SettingsPageV2 from '../SettingsPageV2';
import ExportsPage from '../ExportsPage';
import UsersPage from '../UsersPage';
import PaymentGrantsPage from '../PaymentGrantsPage';
import { useRole } from '../../hooks/useRole';

/**
 * 系统与设置组容器（/cn/system）：9 Tab。
 * tab key 与 LEGACY_REDIRECTS 对齐：/alerts→alerts、/automation→automation、
 * /governance-board→governance、/multi-strategy→multi、/calendar→calendar、
 * /settings→settings、/exports→exports。
 * monitor 不在重定向表（旧导航有该入口），按缺的补 tab：/cn/system?tab=monitor 直达。
 */
export default function SystemGroup() {
  const { t } = useTranslation('pages');
  const [sp, setSp] = useSearchParams();
  const tab = sp.get('tab') ?? 'system';
  const isAdmin = localStorage.getItem('zhice_role') === 'ADMIN';
  const { isViewer } = useRole();
  // deep-test 4c：VIEWER 直连 /:market/system 此前不拦——系统组含审计流/trace/
  // 调度状态等运维敏感页。与下方 users tab 守卫同风格整组 403（侧栏本就不显示
  // system 组，此处挡 URL 直达）；后端 deny_viewer 为权威拦截，前端仅挡入口。
  if (isViewer) {
    return <Result status="403" title={t('systemGroup.noPermTitle')} subTitle={t('systemGroup.viewerNoPermSub')} />;
  }
  const items = [
    { key: 'system', label: t('systemGroup.tabSystem'), children: <SystemStatusPage /> },
    { key: 'monitor', label: t('systemGroup.tabMonitor'), children: <MonitorPage /> },
    { key: 'alerts', label: t('systemGroup.tabAlerts'), children: <AlertsPage /> },
    { key: 'automation', label: t('systemGroup.tabAutomation'), children: <AutomationPage /> },
    { key: 'governance', label: t('systemGroup.tabGovernance'), children: <GovernanceBoardPage /> },
    { key: 'multi', label: t('systemGroup.tabMulti'), children: <MultiStrategyPage /> },
    { key: 'calendar', label: t('systemGroup.tabCalendar'), children: <CalendarPage /> },
    { key: 'settings', label: t('systemGroup.tabSettings'), children: <SettingsPageV2 /> },
    { key: 'exports', label: t('systemGroup.tabExports'), children: <ExportsPage /> },
    ...(isAdmin ? [{ key: 'users', label: t('systemGroup.tabUsers'), children: <UsersPage /> }] : []),
    ...(isAdmin ? [{ key: 'payment-grants', label: t('systemGroup.tabPaymentGrants'), children: <PaymentGrantsPage /> }] : []),
  ];

  if ((tab === 'users' || tab === 'payment-grants') && !isAdmin) {
    return <Result status="403" title={t('systemGroup.noPermTitle')} subTitle={t('systemGroup.noPermSub')} />;
  }

  return (
    <div>
      <PageHeader
        groupKey="system"
        title={t('systemGroup.title')}
        meta={t('systemGroup.meta')}
      />
      <Tabs
        activeKey={tab}
        onChange={k => setSp({ tab: k }, { replace: true })}
        destroyOnHidden
        items={items}
      />
    </div>
  );
}
