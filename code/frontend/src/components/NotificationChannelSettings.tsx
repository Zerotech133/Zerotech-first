/**
 * 通知渠道设置容器（Task 15：Profile 页唯一 binding/preference 状态属主）。
 *
 * 职责：一次加载 getNotificationBindings() + 两渠道偏好，按 channel 下发：
 * - 微信卡：WxPusherBind（binding + onBindingChanged=refreshBindings）；
 * - Telegram 卡（境外可选）：manual/deep-link 模式按后端 status 决定——
 *   getTelegramBindingStatus().bot_username 已配置=deep-link 卡，未配置=手动
 *   chat_id 卡（保留现有组件能力，不强制切换生产部署方式；部署配置会话内
 *   不变，故仅初始判定一次）；两卡经 onBindingChanged 在绑定/解绑成功后
 *   通知本容器刷新（否则矩阵 TG 列会滞留旧绑定态）；
 * - 偏好矩阵卡：渠道 active 时才下发该渠道偏好（null → 整列 disabled +
 *   「请先绑定」，fail-closed）。
 * 绑定/解绑/保存成功统一经 refreshBindings()/onSaved 收敛回服务端真相，
 * 三个子组件不各自维护互不知晓的 binding 状态。
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Typography } from 'antd';

import TelegramDeepLinkBind from './TelegramDeepLinkBind';
import TelegramManualBind from './TelegramManualBind';
import UserNotificationPreferencesForm from './UserNotificationPreferencesForm';
import WxPusherBind from './WxPusherBind';
import {
  getMyNotificationPreferences,
  getNotificationBindings,
  getTelegramBindingStatus,
  type NotificationBinding,
  type NotificationChannel,
  type NotificationPreference,
} from '../services/notificationsApi';

const { Title } = Typography;

/** 两渠道偏好状态（null=未加载/加载失败，沿用旧值回显）。 */
type PrefsState = Record<NotificationChannel, NotificationPreference | null>;

export default function NotificationChannelSettings() {
  const { t } = useTranslation('pages');
  // null=绑定总览未加载（矩阵两列 fail-closed disabled，加载完成即放开）
  const [bindings, setBindings] = useState<NotificationBinding[] | null>(null);
  const [prefs, setPrefs] = useState<PrefsState>({ wxpusher: null, telegram: null });
  const [tgDeepLink, setTgDeepLink] = useState(false);

  /** 绑定/解绑后的统一刷新：绑定总览 + 两渠道偏好一次收敛（失败保留旧值）。 */
  const refreshBindings = useCallback(async (): Promise<void> => {
    const [list, wx, tg] = await Promise.all([
      getNotificationBindings().catch(() => null),
      getMyNotificationPreferences('wxpusher').catch(() => null),
      getMyNotificationPreferences('telegram').catch(() => null),
    ]);
    if (list) setBindings(list);
    setPrefs((p) => ({ wxpusher: wx ?? p.wxpusher, telegram: tg ?? p.telegram }));
  }, []);

  useEffect(() => {
    void refreshBindings();
    // Telegram 绑定模式按后端 status 决定（bot 已配置=deep-link；否则保留手动
    // chat_id 路径，与生产 telegram_binding_enabled=False 现状一致）；失败按手动。
    getTelegramBindingStatus()
      .then((s) => setTgDeepLink(Boolean(s.bot_username)))
      .catch(() => setTgDeepLink(false));
  }, [refreshBindings]);

  const wxBinding = bindings?.find((b) => b.channel === 'wxpusher') ?? null;
  const wxActive = wxBinding?.status === 'active';
  const tgActive = bindings?.some((b) => b.channel === 'telegram' && b.status === 'active');

  return (
    <div>
      <Title level={5} style={{ marginTop: 0 }}>
        {t('notifChannels.wxHeading')}
      </Title>
      <WxPusherBind binding={wxBinding} onBindingChanged={refreshBindings} />

      <Title level={5}>{t('notifChannels.tgHeading')}</Title>
      {tgDeepLink ? (
        <TelegramDeepLinkBind onBindingChanged={refreshBindings} />
      ) : (
        <TelegramManualBind onBindingChanged={refreshBindings} />
      )}

      <UserNotificationPreferencesForm
        wxpusher={wxActive ? prefs.wxpusher : null}
        telegram={tgActive ? prefs.telegram : null}
        onSaved={(channel, pref) => setPrefs((p) => ({ ...p, [channel]: pref }))}
      />
    </div>
  );
}
