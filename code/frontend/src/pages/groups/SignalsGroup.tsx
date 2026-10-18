import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Tabs } from 'antd';
import PageHeader from '../../components/PageHeader';
import PlansPage from '../PlansPage';
import ApprovalsPage from '../ApprovalsPage';
import PipelinePage from '../PipelinePage';

/**
 * 计划与信号组容器（/cn/signals）：plans / approvals / pipeline 三 Tab。
 * tab key 与 LEGACY_REDIRECTS 对齐：/approvals→approvals、/pipeline→pipeline。
 */
export default function SignalsGroup() {
  const { t } = useTranslation('pages');
  const [sp, setSp] = useSearchParams();
  const tab = sp.get('tab') ?? 'plans';
  return (
    <div>
      <PageHeader groupKey="signals" title={t('signalsGroup.title')} meta={t('signalsGroup.meta')} />
      <Tabs
        activeKey={tab}
        onChange={k => setSp({ tab: k }, { replace: true })}
        destroyOnHidden
        items={[
          { key: 'plans', label: t('signalsGroup.tabPlans'), children: <PlansPage /> },
          { key: 'approvals', label: t('signalsGroup.tabApprovals'), children: <ApprovalsPage /> },
          { key: 'pipeline', label: t('signalsGroup.tabPipeline'), children: <PipelinePage /> },
        ]}
      />
    </div>
  );
}
