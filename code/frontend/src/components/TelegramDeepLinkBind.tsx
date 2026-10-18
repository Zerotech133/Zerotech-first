/**
 * Telegram deep-link 绑定卡（Phase D Task 6，替换旧手动输 chat_id 的 TelegramBind）。
 *
 * 安全铁律：
 * - challenge url 含一次性 token，只经 window.open(url, '_blank', 'noopener,noreferrer')
 *   打开，绝不渲染为可复制文本、绝不持久化（state 仅存内存，过期/解绑/重生成即弃）；
 * - 已绑定仅显示后端脱敏 chat_id（status 端点只回尾 4 位）；
 * - 轮询仅在挑战活跃期（phase=active）进行，绑定成功/过期/卸载立即停（防泄漏轮询）；
 * - 绑定未开启（status.bot_username=null / 409 telegram.binding_disabled）显示管理员
 *   引导文案，不抛错误堆栈；
 * - 无手动 chat_id 输入框（manual PATCH 路径前端已死）。
 *
 * Task 15：可选 prop onBindingChanged——轮询/立即查发现 bound 与解绑成功后
 * 回调，供父级容器（NotificationChannelSettings）统一刷新渠道总览；缺省不
 * 回调，独立使用行为不变。
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, Button, Card, Popconfirm, Space, message } from 'antd';

import {
  createTelegramChallenge,
  getTelegramBindingStatus,
  testTelegram,
  unbindTelegram,
} from '../services/notificationsApi';

export interface TelegramDeepLinkBindProps {
  /** 绑定/解绑成功后回调（父级刷新渠道总览；缺省=不回调）。 */
  onBindingChanged?: () => Promise<void> | void;
}

type Phase =
  | 'loading'
  | 'error' // status 加载失败（网络等），提供重试
  | 'unbound' // 未绑定且绑定可用（bot 已配置）
  | 'disabled' // 管理员未开启绑定（bot 未配置或 challenge 409 binding_disabled）
  | 'active' // 挑战活跃期：轮询 + 倒计时
  | 'expired' // 链接过期，可重新生成
  | 'bound'; // 已绑定：脱敏 chat_id + 解绑

const POLL_MS = 3000;
const TICK_MS = 1000;

/** 活跃挑战：url 仅 window.open 使用；expiresAt epoch ms（倒计时/过期判定）。 */
interface ActiveChallenge {
  url: string;
  expiresAt: number;
}

function mmss(totalSec: number): string {
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export default function TelegramDeepLinkBind({
  onBindingChanged,
}: TelegramDeepLinkBindProps) {
  const { t } = useTranslation('pages');
  // 识别 409 telegram.binding_disabled：api() 把 detail.code 翻译为
  // i18n.t(code, ns=errors) 后抛 Error——用同一 key 反查（key 缺失时 i18n.t
  // 原样返回 key，两侧恒等，判定与文案解耦且稳定）。
  const { t: tErr } = useTranslation('errors');

  const [phase, setPhase] = useState<Phase>('loading');
  const [challenge, setChallenge] = useState<ActiveChallenge | null>(null);
  const [remaining, setRemaining] = useState(0);
  const [botUsername, setBotUsername] = useState<string | null>(null);
  const [maskedChatId, setMaskedChatId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [checking, setChecking] = useState(false);
  const [unbinding, setUnbinding] = useState(false);
  // A1 可诊断化：测试消息失败原因的人话文案（reason → i18n），null=无失败
  const [testing, setTesting] = useState(false);
  const [testFailMsg, setTestFailMsg] = useState<string | null>(null);

  const applyStatus = useCallback(
    (s: { bound: boolean; chat_id: string | null; bot_username: string | null }) => {
      setBotUsername(s.bot_username);
      if (s.bound) {
        setMaskedChatId(s.chat_id);
        setPhase('bound');
      } else {
        setMaskedChatId(null);
        setChallenge(null);
        setPhase(s.bot_username ? 'unbound' : 'disabled');
      }
    },
    [],
  );

  const refresh = useCallback(() => getTelegramBindingStatus().then(applyStatus), [applyStatus]);

  useEffect(() => {
    refresh().catch(() => setPhase('error'));
  }, [refresh]);

  function onBound(chatId: string | null) {
    setMaskedChatId(chatId);
    setPhase('bound');
    setChallenge(null);
    message.success(t('telegram.bindSuccess'));
    void onBindingChanged?.();
  }

  async function startChallenge() {
    setCreating(true);
    try {
      const c = await createTelegramChallenge();
      const expiresAt = Date.parse(c.expires_at);
      setChallenge({ url: c.url, expiresAt }); // url 不进 DOM，仅闭包持有
      setRemaining(Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000)));
      setPhase('active');
    } catch (err) {
      // api 层已 toast；管理员未开启 → 持久引导态而非堆栈
      if (err instanceof Error && err.message === tErr('telegram.binding_disabled')) {
        setPhase('disabled');
      }
    } finally {
      setCreating(false);
    }
  }

  // 轮询治理：仅在 phase=active 挂 interval；bound/过期/卸载随 effect 清理即停
  useEffect(() => {
    if (phase !== 'active' || !challenge) return;
    let stopped = false;
    const iv = window.setInterval(() => {
      getTelegramBindingStatus()
        .then((s) => {
          if (!stopped && s.bound) onBound(s.chat_id);
        })
        .catch(() => {
          /* best-effort：下轮重试 */
        });
    }, POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(iv);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, challenge]);

  // 倒计时：每秒刷新剩余；归零转 expired（同时终结上方轮询 effect）
  useEffect(() => {
    if (phase !== 'active' || !challenge) return;
    const iv = window.setInterval(() => {
      const left = Math.ceil((challenge.expiresAt - Date.now()) / 1000);
      if (left <= 0) setPhase('expired');
      else setRemaining(left);
    }, TICK_MS);
    return () => window.clearInterval(iv);
  }, [phase, challenge]);

  /** "我已发送 /start"：立即查一次（未绑定则继续由轮询接管）。 */
  async function checkNow() {
    setChecking(true);
    try {
      const s = await getTelegramBindingStatus();
      if (s.bound) onBound(s.chat_id);
    } catch {
      /* api 层已 toast */
    } finally {
      setChecking(false);
    }
  }

  async function doUnbind() {
    setUnbinding(true);
    try {
      await unbindTelegram();
      message.success(t('telegram.unbindOk'));
      await refresh(); // 重新推导 unbound/disabled（bot 配置为准）
      await onBindingChanged?.();
    } catch {
      /* api 层已 toast */
    } finally {
      setUnbinding(false);
    }
  }

  /** A1：后端 reason（transport error_code）→ 人话提示。不改网络环境本身——
   * 死代理等部署配置问题只在文案里提示检查，不在前端"修复"。 */
  function testFailText(reason: string | undefined): string {
    if (reason === 'not_configured') return t('telegram.testFailNotConfigured');
    if (reason === 'network') return t('telegram.testFailNetwork');
    if (reason === 'http_401' || reason === 'http_403') return t('telegram.testFailToken');
    return t('telegram.testFailUnknown');
  }

  async function sendTest() {
    setTesting(true);
    setTestFailMsg(null);
    try {
      const r = await testTelegram();
      if (r.ok) {
        message.success(t('telegram.testOk'));
      } else {
        setTestFailMsg(testFailText(r.reason));
      }
    } catch {
      /* api 层已 toast（非 2xx 由 api() 统一处理） */
    } finally {
      setTesting(false);
    }
  }

  return (
    <Card size="small" title={t('telegram.title')} style={{ marginBottom: 20 }}>
      {phase === 'unbound' && (
        <>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title={t('telegram.howTitle')}
            description={t('telegram.howDesc')}
          />
          <Button type="primary" loading={creating} onClick={startChallenge}>
            {t('telegram.bind')}
          </Button>
        </>
      )}

      {phase === 'active' && challenge && (
        <>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title={t('telegram.pendingTitle')}
            description={t('telegram.pendingDesc', { bot: botUsername ?? '' })}
          />
          <Space wrap>
            {/* 安全：url 只作为点击跳转目标（noopener,noreferrer），绝不渲染为文本 */}
            <Button
              type="primary"
              onClick={() => window.open(challenge.url, '_blank', 'noopener,noreferrer')}
            >
              {t('telegram.openBot')}
            </Button>
            <Button loading={checking} onClick={checkNow}>
              {t('telegram.sent')}
            </Button>
            <span style={{ color: '#8B93A7' }}>
              {t('telegram.expiresIn', { time: mmss(remaining) })}
            </span>
          </Space>
        </>
      )}

      {phase === 'expired' && (
        <>
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            title={t('telegram.expired')}
          />
          <Button type="primary" loading={creating} onClick={startChallenge}>
            {t('telegram.regenerate')}
          </Button>
        </>
      )}

      {phase === 'bound' && (
        <>
          <Space wrap>
            <span>{t('telegram.bound', { id: maskedChatId ?? '' })}</span>
            <Button loading={testing} onClick={sendTest}>
              {t('telegram.test')}
            </Button>
            <Popconfirm
              title={t('telegram.unbindConfirm')}
              okText={t('common:action.confirm')}
              cancelText={t('common:action.cancel')}
              onConfirm={doUnbind}
            >
              <Button danger loading={unbinding}>
                {t('telegram.unbind')}
              </Button>
            </Popconfirm>
          </Space>
          {testFailMsg && (
            <Alert type="error" showIcon style={{ marginTop: 12 }} title={testFailMsg} />
          )}
        </>
      )}

      {phase === 'disabled' && (
        <Alert
          type="info"
          showIcon
          title={t('telegram.disabledTitle')}
          description={t('telegram.disabledDesc')}
        />
      )}

      {phase === 'error' && (
        <Alert
          type="error"
          showIcon
          title={t('telegram.loadFailed')}
          action={
            <Button size="small" onClick={() => refresh().catch(() => setPhase('error'))}>
              {t('common:action.retry')}
            </Button>
          }
        />
      )}
    </Card>
  );
}
