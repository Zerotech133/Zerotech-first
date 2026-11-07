import { Alert, Tabs } from 'antd';
import { useState } from 'react';
import StrategyCenterDetail from './StrategyCenterDetail';
import StrategyCenterOverview from './StrategyCenterOverview';
import StrategyCenterShelf from './StrategyCenterShelf';
import './strategyCenter.css';

type View = 'overview' | 'shelf' | 'detail';

/**
 * 策略中台（待校正）入口。
 *
 * 归属“策略管理”，不放进左侧第 9 个导航；校正完成前只作为只读研究预览，
 * 不提供启停用、选股、建仓、批准等任何写操作。
 */
export default function StrategyCenterPage() {
  const [view, setView] = useState<View>('overview');
  const [sid, setSid] = useState<string | null>('LX20');

  const openDetail = (nextSid: string) => {
    setSid(nextSid);
    setView('detail');
  };

  return (
    <div className="sc-page">
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 12 }}
        title="策略中台 · 待校正"
        description="当前为研究只读预览，未正式上架；不触发选股、建仓、启停用或批准。校正与验收通过后再进入正式策略管理流程。"
      />
      <Tabs
        activeKey={view}
        onChange={(key) => setView(key as View)}
        items={[
          { key: 'overview', label: '策略中台总览', children: <StrategyCenterOverview /> },
          {
            key: 'shelf',
            label: '策略货架',
            children: <StrategyCenterShelf onOpenDetail={openDetail} />,
          },
          {
            key: 'detail',
            label: '策略详情',
            children: <StrategyCenterDetail sid={sid} onBack={() => setView('shelf')} />,
          },
        ]}
      />
    </div>
  );
}
