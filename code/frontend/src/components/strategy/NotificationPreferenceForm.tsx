/**
 * 通知偏好 9 键开关组（向导步骤 5 内容；Phase D Task 3）。
 *
 * 键集合与默认值由 wizardModel NOTIFICATION_KEYS / DEFAULT_NOTIFICATION_PREFS
 * 唯一定义（后端 DEFAULT_NOTIFICATION_PREFERENCES 同源，默认全开）。
 * Task 6 将复用本组件做运行实例级偏好编辑。
 */
import { Checkbox, Col, Row } from 'antd';
import { useTranslation } from 'react-i18next';
import { NOTIFICATION_KEYS } from './wizardModel';

interface Props {
  value: Record<string, boolean>;
  onChange: (next: Record<string, boolean>) => void;
}

export default function NotificationPreferenceForm({ value, onChange }: Props) {
  const { t } = useTranslation('pages');
  return (
    <Row gutter={[8, 12]}>
      {NOTIFICATION_KEYS.map((k) => (
        <Col key={k} span={8}>
          <Checkbox
            checked={value[k] !== false}
            onChange={(e) => onChange({ ...value, [k]: e.target.checked })}
          >
            {t(`enableWizard.notif.${k}`)}
          </Checkbox>
        </Col>
      ))}
    </Row>
  );
}
