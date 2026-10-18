import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Tabs } from 'antd';
import PageHeader from '../../components/PageHeader';
import ReplayPage from '../ReplayPage';
import PnlCalendarPage from '../PnlCalendarPage';
import KnowledgePage from '../KnowledgePage';
import LearningReportPage from '../LearningReportPage';
import DailyReportPage from '../DailyReportPage';
import { useRole } from '../../hooks/useRole';

/**
 * 复盘研究组容器（/cn/replay）：replay / calendar / knowledge / learning / report 五 Tab。
 * tab key 与 LEGACY_REDIRECTS 对齐：/pnl-calendar→calendar、/knowledge→knowledge、
 * /learning-report→learning、/daily-report→report。
 */
export default function ReplayGroup() {
  const { t } = useTranslation('pages');
  const { isViewer } = useRole();
  const [sp, setSp] = useSearchParams();
  const tab = sp.get('tab') ?? 'replay';
  const allItems = [
    { key: 'replay', label: t('replayGroup.tabReplay'), children: <ReplayPage /> },
    { key: 'calendar', label: t('replayGroup.tabCalendar'), children: <PnlCalendarPage /> },
    { key: 'knowledge', label: t('replayGroup.tabKnowledge'), children: <KnowledgePage /> },
    { key: 'learning', label: t('replayGroup.tabLearning'), children: <LearningReportPage /> },
    { key: 'report', label: t('replayGroup.tabReport'), children: <DailyReportPage /> },
  ];
  // VIEWER 只读：只留 盈亏日历 + 复盘报告，隐藏 复盘/知识库/学习报告。
  const items = isViewer ? allItems.filter(i => ['calendar', 'report'].includes(i.key)) : allItems;
  // 兜底：VIEWER 直连隐藏 tab 时落到第一个可见 tab。
  const activeKey = items.some(i => i.key === tab) ? tab : items[0].key;
  return (
    <div>
      <PageHeader groupKey="replay" title={t('replayGroup.title')} meta={t('replayGroup.meta')} />
      <Tabs
        activeKey={activeKey}
        onChange={k => setSp({ tab: k }, { replace: true })}
        destroyOnHidden
        items={items}
      />
    </div>
  );
}
