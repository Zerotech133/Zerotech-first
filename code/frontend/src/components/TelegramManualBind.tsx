/**
 * Telegram 手动绑定卡：用户直接填自己的 chat_id 绑定/解绑（PATCH/DELETE /auth/me/telegram）。
 * 适用于未启用 deep-link 的部署（telegram_binding_enabled=False）——每个账号绑自己的 chat_id。
 * 已绑定时后端仅回脱敏 chat_id（尾 4 位）。获取 chat_id 提示：在 Telegram 里给 @userinfobot 发消息。
 *
 * Task 15：可选 prop onBindingChanged——绑定/解绑成功（本卡自查落定）后回调，
 * 供父级容器（NotificationChannelSettings）统一刷新渠道总览；缺省不回调，
 * 独立使用行为不变。
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Card, Input, Space, Tag, Typography, message } from 'antd';

import {
  bindTelegramManual,
  getTelegramBindingStatus,
  unbindTelegram,
} from '../services/notificationsApi';

const { Text } = Typography;

export interface TelegramManualBindProps {
  /** 绑定/解绑成功后回调（父级刷新渠道总览；缺省=不回调）。 */
  onBindingChanged?: () => Promise<void> | void;
}

export default function TelegramManualBind({ onBindingChanged }: TelegramManualBindProps) {
  const { t } = useTranslation('pages');
  const [bound, setBound] = useState(false);
  const [maskedChatId, setMaskedChatId] = useState<string | null>(null);
  const [chatId, setChatId] = useState('');
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const s = await getTelegramBindingStatus();
      setBound(s.bound);
      setMaskedChatId(s.chat_id);
    } catch {
      /* api 层已 toast */
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function doBind() {
    if (!chatId.trim()) {
      message.error(t('telegram.chatIdRequired'));
      return;
    }
    setBusy(true);
    try {
      await bindTelegramManual(chatId.trim());
      message.success(t('telegram.bindOk'));
      setChatId('');
      await refresh();
      await onBindingChanged?.();
    } catch {
      /* api 层已 toast */
    } finally {
      setBusy(false);
    }
  }

  async function doUnbind() {
    setBusy(true);
    try {
      await unbindTelegram();
      message.success(t('telegram.unbindOk'));
      await refresh();
      await onBindingChanged?.();
    } catch {
      /* api 层已 toast */
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card size="small" title={t('telegram.title')} style={{ marginBottom: 20 }}>
      {bound ? (
        <Space orientation="vertical" style={{ width: '100%' }}>
          <span>
            <Tag color="green">{t('telegram.bound')}</Tag>
            <Text code>{maskedChatId ?? '—'}</Text>
          </span>
          <Button danger loading={busy} onClick={doUnbind}>
            {t('telegram.unbind')}
          </Button>
        </Space>
      ) : (
        <Space orientation="vertical" style={{ width: '100%' }}>
          <Text type="secondary">{t('telegram.hint')}</Text>
          <Space.Compact style={{ width: '100%' }}>
            <Input
              placeholder={t('telegram.chatIdPlaceholder')}
              value={chatId}
              onChange={(e) => setChatId(e.target.value)}
              onPressEnter={doBind}
              maxLength={32}
            />
            <Button type="primary" loading={busy} onClick={doBind}>
              {t('telegram.bind')}
            </Button>
          </Space.Compact>
        </Space>
      )}
    </Card>
  );
}
