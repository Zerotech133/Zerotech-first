import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Card, Descriptions, Button, Input, message, Space, Tag, Segmented } from 'antd';

import NotificationChannelSettings from '../components/NotificationChannelSettings';
import { useLang } from '../contexts/LanguageContext';
import { api } from '../services/api';
import { THEMES, useTheme, type ThemeKey } from '../themes/ThemeContext';
import type { Lang } from '../i18n';

interface Me {
  user_id: string;
  username: string;
  role: string;
  email?: string | null;
}

export default function ProfilePage() {
  const { t } = useTranslation('pages');
  const navigate = useNavigate();
  const { lang, setLang } = useLang();
  const { theme, setTheme } = useTheme();
  const [me, setMe] = useState<Me | null>(null);
  const [oldPw, setOldPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [savingPw, setSavingPw] = useState(false);

  useEffect(() => {
    api<Me>('/auth/me').then(setMe).catch(() => {});
  }, []);

  async function changePw() {
    if (!oldPw) {
      message.warning(t('profile.oldPwRequired'));
      return;
    }
    if (newPw.length < 6) {
      message.warning(t('profile.pwTooShort'));
      return;
    }
    if (newPw !== confirmPw) {
      message.warning(t('profile.pwMismatch'));
      return;
    }
    if (!me) return;
    setSavingPw(true);
    try {
      // 3b：本人改密必须验当前密码（后端同样强制，此处仅前置校验）
      await api(`/auth/users/${me.user_id}/password`, {
        method: 'PUT',
        body: JSON.stringify({ old_password: oldPw, new_password: newPw }),
      });
      message.success(t('profile.pwSaved'));
      setOldPw('');
      setNewPw('');
      setConfirmPw('');
      // 3c：改密后旧 token 已失效——清除本地会话引导重新登录
      localStorage.removeItem('zhice_token');
      localStorage.removeItem('zhice_role');
      window.setTimeout(() => navigate('/login'), 1200);
    } catch {
      /* api 层已 toast */
    } finally {
      setSavingPw(false);
    }
  }

  return (
    <div style={{ minHeight: '100vh', background: '#f0f2f5', padding: '28px 32px' }}>
      <div style={{ maxWidth: 720, margin: '0 auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <Button onClick={() => navigate('/')}>{t('profile.back')}</Button>
          <h1 style={{ fontSize: 22, margin: 0 }}>{t('profile.title')}</h1>
        </div>

      {/* 账户信息 */}
      <Card size="small" title={t('profile.account')} style={{ marginBottom: 20 }}>
        <Descriptions column={1} size="small">
          <Descriptions.Item label={t('profile.username')}>{me?.username ?? '—'}</Descriptions.Item>
          <Descriptions.Item label={t('profile.role')}>
            <Tag>{me?.role ?? '—'}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label={t('profile.email')}>{me?.email || '—'}</Descriptions.Item>
        </Descriptions>
      </Card>

      {/* 通知渠道设置（Task 15 容器：微信绑定 + Telegram（境外可选）绑定 +
          双渠道偏好矩阵；binding/preference 状态统一由容器属主） */}
      <NotificationChannelSettings />

      {/* 偏好：语言 + 主题 */}
      <Card size="small" title={t('profile.prefs')} style={{ marginBottom: 20 }}>
        <div style={{ marginBottom: 14 }}>
          <span style={{ marginRight: 12, color: '#8B93A7' }}>{t('profile.language')}</span>
          <Segmented
            value={lang}
            onChange={(v) => setLang(v as Lang)}
            options={[{ label: '中文', value: 'zh' }, { label: 'English', value: 'en' }]}
          />
        </div>
        <div>
          <span style={{ marginRight: 12, color: '#8B93A7' }}>{t('profile.theme')}</span>
          <Segmented
            value={theme}
            onChange={(v) => setTheme(v as ThemeKey)}
            options={THEMES.map((th) => ({ label: th.label, value: th.key }))}
          />
        </div>
      </Card>

      {/* 修改密码 */}
      <Card size="small" title={t('profile.changePw')} style={{ marginBottom: 20 }}>
        <Space orientation="vertical" style={{ width: 320 }}>
          <Input.Password placeholder={t('profile.currentPw')} value={oldPw} onChange={(e) => setOldPw(e.target.value)} />
          <Input.Password placeholder={t('profile.newPw')} value={newPw} onChange={(e) => setNewPw(e.target.value)} />
          <Input.Password placeholder={t('profile.confirmPw')} value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} />
          <Button type="primary" loading={savingPw} onClick={changePw}>
            {t('common:action.save')}
          </Button>
        </Space>
      </Card>
      </div>
    </div>
  );
}
