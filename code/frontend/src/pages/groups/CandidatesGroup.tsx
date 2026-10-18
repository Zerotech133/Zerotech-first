import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Tabs } from 'antd';
import PageHeader from '../../components/PageHeader';
import CandidatesPage from '../CandidatesPage';

/**
 * 候选池组容器（/cn/candidates）：单 Tab（/candidates 重定向默认落此）。
 * 保留 Tabs 结构而非直渲染：侧栏组与其它组行为一致，URL ?tab= 可显式指定。
 */
export default function CandidatesGroup() {
  const { t } = useTranslation('pages');
  const [sp, setSp] = useSearchParams();
  const tab = sp.get('tab') ?? 'candidates';
  return (
    <div>
      <PageHeader groupKey="candidates" title={t('candidatesGroup.title')} />
      <Tabs
        activeKey={tab}
        onChange={k => setSp({ tab: k }, { replace: true })}
        destroyOnHidden
        items={[{ key: 'candidates', label: t('candidatesGroup.title'), children: <CandidatesPage /> }]}
      />
    </div>
  );
}
