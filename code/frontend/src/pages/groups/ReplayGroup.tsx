import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Tabs } from 'antd';
import PageHeader from '../../components/PageHeader';
import JobStatusBar from '../../components/JobStatusBar';
import ReplayPage from '../ReplayPage';
import PnlCalendarPage from '../PnlCalendarPage';
import KnowledgePage from '../KnowledgePage';
import LearningReportPage from '../LearningReportPage';
import DailyReportPage from '../DailyReportPage';
import Acad1ResearchPage from '../Acad1ResearchPage';
import LusuDeskPage from '../LusuDeskPage';
import SelectionDeskPage from '../SelectionDeskPage';
import SimFactorPage from '../SimFactorPage';
import SelectionFlowPage from '../SelectionFlowPage';
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
  const tab = sp.get('tab') ?? 'flow';
  const allItems = [
    { key: 'flow', label: '全景动线', children: <SelectionFlowPage /> },
    { key: 'replay', label: t('replayGroup.tabReplay'), children: <ReplayPage /> },
    { key: 'calendar', label: t('replayGroup.tabCalendar'), children: <PnlCalendarPage /> },
    { key: 'knowledge', label: t('replayGroup.tabKnowledge'), children: <KnowledgePage /> },
    { key: 'learning', label: t('replayGroup.tabLearning'), children: <LearningReportPage /> },
    { key: 'report', label: t('replayGroup.tabReport'), children: <DailyReportPage /> },
    { key: 'acad1', label: '司马徽研究院', children: <Acad1ResearchPage /> },
    { key: 'selection', label: '选股中台', children: <SelectionDeskPage /> },
    { key: 'sim', label: '模拟盘/因子', children: <SimFactorPage /> },
    { key: 'lusu', label: '鲁肃台', children: <LusuDeskPage /> },
  ];
  // VIEWER 只读：只留 盈亏日历 + 复盘报告，隐藏 复盘/知识库/学习报告。
  const items = isViewer ? allItems.filter(i => ['calendar', 'report', 'flow'].includes(i.key)) : allItems;
  // 兜底：VIEWER 直连隐藏 tab 时落到第一个可见 tab。
  const activeKey = items.some(i => i.key === tab) ? tab : items[0].key;
  return (
    <div>
      <PageHeader
        groupKey={activeKey === 'flow' ? 'overview' : 'replay'}
        title={activeKey === 'flow' ? '首页 · 选股指挥中心' : t('replayGroup.title')}
        meta={activeKey === 'flow' ? '触发 · 日报 · 候选池 · ACAD1 · 证据 · 智能体' : t('replayGroup.meta')}
      />
      {/* P4-3：每日任务运行态状态条（只读，接入顶部；不改导航/Tab） */}
      <JobStatusBar />
      <Tabs
        activeKey={activeKey}
        onChange={k => setSp({ tab: k }, { replace: true })}
        destroyOnHidden
        items={items}
      />
    </div>
  );
}
