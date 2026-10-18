/**
 * 用户级通知偏好矩阵卡（Task 15：四行事件 × 双渠道列；Profile 内独立卡）。
 *
 * 与实例级 NotificationPreferenceForm（9 键开关组）是**不同键集**：四类渠道
 * 开关（signal/fill/morning/evening）+ market_reports_enabled（按市场）+
 * quiet_hours / timezone / locale。本组件为 props 驱动（容器
 * NotificationChannelSettings 一次加载两渠道偏好并传入，null=该渠道未 active）：
 * - 矩阵开关即时保存：每次切换只 PATCH 所改渠道的所改字段，不影响另一渠道；
 *   服务端应答回写（onSaved）后受控回显，不做本地乐观猜测；
 * - 未 active 渠道整列 disabled + 「请先绑定」（fail-closed）；
 * - 静音时段/时区/语言为渠道级配置：Segmented 选择渠道后整块表单随该渠道
 *   回显，保存也只 PATCH 所选渠道；市场报告 opt-in 区保留，并说明同时约束
 *   晨/晚报。实例级 9 键表单不在本组件。
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button,
  Card,
  Checkbox,
  Col,
  Divider,
  Form,
  Input,
  Row,
  Segmented,
  Select,
  Space,
  Switch,
  Typography,
  message,
} from 'antd';

import { MARKETS } from '../contexts/MarketContext';
import {
  patchMyNotificationPreferences,
  type NotificationChannel,
  type NotificationPreference,
  type NotificationPreferencePatch,
} from '../services/notificationsApi';

const HHMM_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

/** 矩阵事件行：后端开关键 → i18n key（顺序即展示顺序）。 */
const EVENT_ROWS = [
  ['signal_enabled', 'userNotif.signal'],
  ['fill_enabled', 'userNotif.fill'],
  ['morning_report_enabled', 'userNotif.morningReport'],
  ['evening_report_enabled', 'userNotif.eveningReport'],
] as const;

type EventField = (typeof EVENT_ROWS)[number][0];

/** 矩阵渠道列（微信在前）。 */
const CHANNEL_COLUMNS = [
  { key: 'wxpusher', labelKey: 'userNotif.channelWx' },
  { key: 'telegram', labelKey: 'userNotif.channelTelegram' },
] as const;

export interface UserNotificationPreferencesFormProps {
  /** null = 渠道未 active（整列 disabled + 请先绑定；回显 fail-closed 全关）。 */
  wxpusher: NotificationPreference | null;
  telegram: NotificationPreference | null;
  /** 任一渠道保存成功后回传服务端最新偏好（容器回写状态 → 矩阵/表单回显）。 */
  onSaved?: (channel: NotificationChannel, pref: NotificationPreference) => void;
}

interface FormValues {
  market_reports_enabled?: Record<string, boolean>;
  quiet_start?: string;
  quiet_end?: string;
  timezone?: string;
  locale?: 'zh' | 'en';
}

const CELL_STYLE: React.CSSProperties = { padding: '6px 12px', textAlign: 'left' };

export default function UserNotificationPreferencesForm({
  wxpusher,
  telegram,
  onSaved,
}: UserNotificationPreferencesFormProps) {
  const { t } = useTranslation('pages');
  const [toggling, setToggling] = useState<{ channel: NotificationChannel; field: EventField } | null>(
    null,
  );

  const channels: Record<NotificationChannel, NotificationPreference | null> = {
    wxpusher,
    telegram,
  };

  /** 矩阵开关即时保存：只 PATCH 所改渠道的所改字段（另一渠道绝不动）。 */
  async function onToggle(
    channel: NotificationChannel,
    field: EventField,
    next: boolean,
  ): Promise<void> {
    if (!channels[channel]) return;
    setToggling({ channel, field });
    try {
      const updated = await patchMyNotificationPreferences(channel, { [field]: next });
      message.success(t('userNotif.saved'));
      onSaved?.(channel, updated);
    } catch {
      /* api 层已 toast；不回写 → 开关保持服务端真相 */
    } finally {
      setToggling(null);
    }
  }

  return (
    <Card size="small" title={t('userNotif.title')} style={{ marginBottom: 20 }}>
      <div style={{ marginBottom: 8, fontSize: 12, color: '#8B93A7' }}>
        {t('userNotif.matrixHint')}
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 16 }}>
        <thead>
          <tr>
            <th scope="col" style={{ ...CELL_STYLE, borderBottom: '1px solid #e8e8e8' }}>
              {t('userNotif.event')}
            </th>
            {CHANNEL_COLUMNS.map((c) => (
              <th
                key={c.key}
                scope="col"
                style={{ ...CELL_STYLE, borderBottom: '1px solid #e8e8e8' }}
              >
                {t(c.labelKey)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {EVENT_ROWS.map(([field, labelKey]) => (
            <tr key={field}>
              <th scope="row" style={{ ...CELL_STYLE, fontWeight: 500 }}>
                {t(labelKey)}
              </th>
              {CHANNEL_COLUMNS.map((c) => {
                const pref = channels[c.key];
                const busy = toggling?.channel === c.key && toggling.field === field;
                return (
                  <td key={c.key} style={CELL_STYLE}>
                    <Space size={8}>
                      {/* accessible name = 渠道名 + 事件名（如「微信 策略信号」） */}
                      <Switch
                        aria-label={`${t(c.labelKey)} ${t(labelKey)}`}
                        checked={pref ? pref[field] : false}
                        disabled={!pref || busy}
                        loading={busy}
                        onChange={(next) => void onToggle(c.key, field, next)}
                      />
                      {!pref && (
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {t('userNotif.bindFirst')}
                        </Typography.Text>
                      )}
                    </Space>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>

      <ChannelConfigSection wxpusher={wxpusher} telegram={telegram} onSaved={onSaved} />
    </Card>
  );
}

/** 渠道级配置（静音时段/时区/推送语言/市场报告 opt-in）：Segmented 选渠道，
 *  单表单随所选渠道回显，保存只 PATCH 所选渠道。 */
function ChannelConfigSection({
  wxpusher,
  telegram,
  onSaved,
}: Omit<UserNotificationPreferencesFormProps, never>) {
  const { t } = useTranslation('pages');
  const [channel, setChannel] = useState<NotificationChannel>('wxpusher');
  const [form] = Form.useForm<FormValues>();
  const [saving, setSaving] = useState(false);

  const pref = channel === 'wxpusher' ? wxpusher : telegram;

  // 所选渠道或其偏好变化 → 回显服务端真相（保存应答/容器刷新都会走到这里）
  useEffect(() => {
    form.setFieldsValue({
      // 缺键市场回显为关（后端 {}=等价全关，fail-closed opt-in）
      market_reports_enabled: pref?.market_reports_enabled ?? {},
      quiet_start: pref?.quiet_hours?.start ?? '',
      quiet_end: pref?.quiet_hours?.end ?? '',
      timezone: pref?.timezone || 'Asia/Shanghai',
      locale: pref?.locale === 'en' ? 'en' : 'zh',
    });
  }, [channel, pref, form]);

  /** 静音时段联合校验：同时填/同时空、HH:MM、start!=end（跨午夜合法）。 */
  function validateQuiet(): Promise<void> {
    const s = (form.getFieldValue('quiet_start') as string | undefined) ?? '';
    const e = (form.getFieldValue('quiet_end') as string | undefined) ?? '';
    if (!s && !e) return Promise.resolve();
    if (!s || !e) return Promise.reject(new Error(t('userNotif.quietBoth')));
    if (!HHMM_RE.test(s) || !HHMM_RE.test(e)) {
      return Promise.reject(new Error(t('userNotif.quietInvalid')));
    }
    if (s === e) return Promise.reject(new Error(t('userNotif.quietSame')));
    return Promise.resolve();
  }

  async function onFinish(values: FormValues) {
    if (!pref) return;
    const patch: NotificationPreferencePatch = {
      // 显式提交全部启用市场（后端按键合并，全量集合幂等安全）
      market_reports_enabled: Object.fromEntries(
        MARKETS.map((m) => [m, values.market_reports_enabled?.[m] === true]),
      ),
      timezone: (values.timezone || 'Asia/Shanghai').trim(),
      locale: values.locale || 'zh',
    };
    if (values.quiet_start && values.quiet_end) {
      patch.quiet_hours = { start: values.quiet_start, end: values.quiet_end };
    }
    setSaving(true);
    try {
      const updated = await patchMyNotificationPreferences(channel, patch);
      message.success(t('userNotif.saved'));
      onSaved?.(channel, updated);
    } catch {
      /* api 层已 toast */
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <Divider titlePlacement="left" style={{ margin: '4px 0 16px' }}>
        {t('userNotif.channelConfig')}
      </Divider>
      <Segmented
        style={{ marginBottom: 16 }}
        value={channel}
        onChange={(v) => setChannel(v as NotificationChannel)}
        options={CHANNEL_COLUMNS.map((c) => ({ label: t(c.labelKey), value: c.key }))}
      />
      <Form
        form={form}
        name="user-channel-pref"
        layout="vertical"
        onFinish={onFinish}
        disabled={!pref}
      >
        <Form.Item label={t('userNotif.marketReports')} style={{ marginBottom: 4 }}>
          <Row gutter={[8, 8]}>
            {MARKETS.map((m) => (
              <Col key={m} span={8}>
                <Form.Item name={['market_reports_enabled', m]} valuePropName="checked" noStyle>
                  <Checkbox>{t(`userNotif.market.${m}`)}</Checkbox>
                </Form.Item>
              </Col>
            ))}
          </Row>
        </Form.Item>
        <div style={{ marginBottom: 12, fontSize: 12, color: '#8B93A7' }}>
          {t('userNotif.marketReportsHint')}
        </div>

        <Row gutter={16}>
          <Col span={8}>
            <Form.Item
              name="quiet_start"
              label={t('userNotif.quietStart')}
              dependencies={['quiet_end']}
              rules={[{ validator: validateQuiet }]}
              style={{ marginBottom: 8 }}
            >
              <Input />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item
              name="quiet_end"
              label={t('userNotif.quietEnd')}
              dependencies={['quiet_start']}
              rules={[{ validator: validateQuiet }]}
              style={{ marginBottom: 8 }}
            >
              <Input />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="locale" label={t('userNotif.locale')} style={{ marginBottom: 8 }}>
              <Select
                options={[
                  { value: 'zh', label: '中文' },
                  { value: 'en', label: 'English' },
                ]}
              />
            </Form.Item>
          </Col>
        </Row>
        <div style={{ marginBottom: 12, fontSize: 12, color: '#8B93A7' }}>
          {t('userNotif.quietHint')}
        </div>

        <Form.Item
          name="timezone"
          label={t('userNotif.timezone')}
          rules={[{ required: true }]}
          style={{ marginBottom: 12 }}
        >
          <Input style={{ maxWidth: 240 }} placeholder={t('userNotif.tzPlaceholder')} />
        </Form.Item>

        <Space>
          <Button type="primary" htmlType="submit" loading={saving}>
            {t('common:action.save')}
          </Button>
          {!pref && (
            <Typography.Text type="secondary">{t('userNotif.bindFirst')}</Typography.Text>
          )}
        </Space>
      </Form>
    </>
  );
}
