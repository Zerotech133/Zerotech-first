/**
 * WxPusher 微信扫码绑定卡（Task 14；Profile 集成与偏好矩阵在 Task 15）。
 *
 * 契约（props 固定）：
 * - binding：父级从 /auth/me/notification-bindings 取出的 wxpusher 绑定视图，
 *   null=未加载/无绑定（均按未绑定提供扫码入口）；
 * - onBindingChanged：绑定成功（扫码确认）/解绑成功后统一回调（父级 refresh）。
 *
 * 状态机：loading（解绑后等父级刷新）/ unbound（无绑定；含「扫码中」子态=
 * challenge 在手，展示二维码+倒计时+轮询）/ active（bound，prop active 或轮询
 * 发现）/ expired（二维码过期）/ revoked（prop 已解绑）/ error（challenge 创建
 * 失败，best-effort 文案不崩）。
 *
 * 安全铁律：
 * - 二维码只用后端返回的 qr_image_url 直接 <img src>（后端生成、不含 token），
 *   绝不用 antd QRCode 二次编码；绝不渲染 token/provider_code（后端本就不返回，
 *   前端也只保留 URL 与过期时刻）；
 * - 临时 URL/倒计时只存 React state（内存），过期/绑定成功/卸载即弃，不写
 *   localStorage、不打 console.log；
 * - 轮询仅扫码期进行（生成后立即首查 + 每 3s），发现 active/过期/卸载立即停。
 */
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, Button, Card, Popconfirm, Space, Spin, message } from 'antd';

import {
  createWxPusherChallenge,
  getNotificationBindings,
  testWxPusher,
  unbindWxPusher,
  type NotificationBinding,
} from '../services/notificationsApi';

export interface WxPusherBindProps {
  /** 当前 wxpusher binding（null=未加载/无）。 */
  binding: NotificationBinding | null;
  /** 绑定/解绑成功后统一回调（父级 refresh）。 */
  onBindingChanged: () => Promise<void> | void;
}

type Phase = 'loading' | 'unbound' | 'active' | 'expired' | 'revoked' | 'error';

const POLL_MS = 3000;
const TICK_MS = 1000;

/** 展示中的挑战：仅服务端二维码 URL + 过期时刻（无 token 可存）。 */
interface ActiveChallenge {
  qrImageUrl: string;
  expiresAt: number; // epoch ms
}

/** active 视图数据：脱敏 UID + 绑定时间（来自 prop 或轮询发现）。 */
interface BoundInfo {
  recipient: string | null;
  boundAt: string | null;
}

function mmss(totalSec: number): string {
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function phaseFromBinding(b: NotificationBinding | null): Phase {
  if (b?.status === 'active') return 'active';
  if (b?.status === 'revoked') return 'revoked';
  return 'unbound'; // null=未加载/无 → 扫码入口可用
}

export default function WxPusherBind({ binding, onBindingChanged }: WxPusherBindProps) {
  const { t } = useTranslation('pages');

  const [phase, setPhase] = useState<Phase>(() => phaseFromBinding(binding));
  const [challenge, setChallenge] = useState<ActiveChallenge | null>(null);
  const [remaining, setRemaining] = useState(0);
  const [bound, setBound] = useState<BoundInfo | null>(
    binding?.status === 'active'
      ? { recipient: binding.recipient_masked, boundAt: binding.bound_at }
      : null,
  );
  const [creating, setCreating] = useState(false);
  const [testing, setTesting] = useState(false);
  const [unbinding, setUnbinding] = useState(false);
  const [testFailMsg, setTestFailMsg] = useState<string | null>(null);

  // 回调经 ref 持有：轮询发现绑定时直接调用，不因父级内联回调变化重启 interval
  const onChangedRef = useRef(onBindingChanged);
  onChangedRef.current = onBindingChanged;
  const bindingRef = useRef(binding);
  bindingRef.current = binding;

  // 父级绑定数据变化 → 重新推导视图（扫码挑战进行中不打断，除非已成 active）
  useEffect(() => {
    if (binding?.status === 'active') {
      setBound({ recipient: binding.recipient_masked, boundAt: binding.bound_at });
      setChallenge(null);
      setTestFailMsg(null);
      setPhase('active');
      return;
    }
    if (!challenge) {
      setBound(null);
      setPhase(phaseFromBinding(binding));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [binding]);

  async function startChallenge() {
    setCreating(true);
    try {
      const c = await createWxPusherChallenge();
      const expiresAt = Date.parse(c.expires_at);
      setChallenge({ qrImageUrl: c.qr_image_url, expiresAt });
      setRemaining(Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000)));
      setPhase('unbound'); // 未绑定视图的「扫码中」子态（revoked/expired 重新进入亦然）
    } catch {
      setChallenge(null);
      setPhase('error'); // best-effort：i18n 错误文案，不崩组件（api 层已 toast）
    } finally {
      setCreating(false);
    }
  }

  /** 轮询发现 active：立即停轮询/倒计时（两 effect 随 phase 变化清理）并通知父级。 */
  function onDiscovered(b: NotificationBinding) {
    setBound({ recipient: b.recipient_masked, boundAt: b.bound_at });
    setChallenge(null);
    setPhase('active');
    message.success(t('wxpusher.bindSuccess'));
    void onChangedRef.current();
  }

  // 轮询治理：仅扫码期（unbound 且有挑战）进行；生成后立即首查，此后每 3s；
  // 发现 active / 过期 / 卸载随 effect 清理即停
  useEffect(() => {
    if (phase !== 'unbound' || !challenge) return;
    let stopped = false;
    const check = () => {
      getNotificationBindings()
        .then((list) => {
          const b = list.find((x) => x.channel === 'wxpusher' && x.status === 'active');
          if (!stopped && b) onDiscovered(b);
        })
        .catch(() => {
          /* best-effort：下轮重试 */
        });
    };
    check(); // 立即首查，缩短扫码确认后的可见延迟
    const iv = window.setInterval(check, POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(iv);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, challenge]);

  // 倒计时：每秒刷新剩余；归零清空二维码状态转 expired（上方轮询随清理停止）
  useEffect(() => {
    if (phase !== 'unbound' || !challenge) return;
    const iv = window.setInterval(() => {
      const left = Math.ceil((challenge.expiresAt - Date.now()) / 1000);
      if (left <= 0) {
        setChallenge(null);
        setPhase('expired');
      } else {
        setRemaining(left);
      }
    }, TICK_MS);
    return () => window.clearInterval(iv);
  }, [phase, challenge]);

  async function sendTest() {
    setTesting(true);
    setTestFailMsg(null);
    try {
      const r = await testWxPusher();
      if (r.ok) message.success(t('wxpusher.testOk'));
      else setTestFailMsg(t('wxpusher.testFail', { reason: r.reason ?? '-' }));
    } catch {
      /* api 层已 toast（非 2xx 由 api() 统一处理） */
    } finally {
      setTesting(false);
    }
  }

  async function doUnbind() {
    setUnbinding(true);
    try {
      await unbindWxPusher();
      message.success(t('wxpusher.unbindOk'));
      setBound(null);
      setChallenge(null);
      setPhase('loading'); // 等父级 onBindingChanged 刷新出最新绑定
      await onChangedRef.current();
      // 父级以同引用传入、未触发上方 effect 时，本地兜底重推导
      setPhase(phaseFromBinding(bindingRef.current));
    } catch {
      /* api 层已 toast */
    } finally {
      setUnbinding(false);
    }
  }

  return (
    <Card size="small" title={t('wxpusher.title')} style={{ marginBottom: 20 }}>
      {phase === 'unbound' && !challenge && (
        <>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title={t('wxpusher.howTitle')}
            description={t('wxpusher.howDesc')}
          />
          <Button type="primary" loading={creating} onClick={startChallenge}>
            {t('wxpusher.bind')}
          </Button>
        </>
      )}

      {phase === 'unbound' && challenge && (
        <>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title={t('wxpusher.pendingTitle')}
            description={t('wxpusher.pendingDesc')}
          />
          {/* 安全：仅服务端二维码 URL 直出 <img>（后端生成、不含 token），不做二次编码 */}
          <div style={{ marginBottom: 8 }}>
            <img src={challenge.qrImageUrl} alt={t('wxpusher.qrAlt')} width={200} height={200} />
          </div>
          <span style={{ color: '#8B93A7' }}>
            {t('wxpusher.expiresIn', { time: mmss(remaining) })}
          </span>
        </>
      )}

      {phase === 'expired' && (
        <>
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            title={t('wxpusher.expired')}
          />
          <Button type="primary" loading={creating} onClick={startChallenge}>
            {t('wxpusher.regenerate')}
          </Button>
        </>
      )}

      {phase === 'active' && bound && (
        <>
          <Space wrap>
            <span>{t('wxpusher.bound', { id: bound.recipient ?? '' })}</span>
            {bound.boundAt && (
              <span style={{ color: '#8B93A7' }}>
                {t('wxpusher.boundAt', { time: new Date(bound.boundAt).toLocaleString() })}
              </span>
            )}
            <Button loading={testing} onClick={sendTest}>
              {t('wxpusher.test')}
            </Button>
            <Popconfirm
              title={t('wxpusher.unbindConfirm')}
              okText={t('common:action.confirm')}
              cancelText={t('common:action.cancel')}
              onConfirm={doUnbind}
            >
              <Button danger loading={unbinding}>
                {t('wxpusher.unbind')}
              </Button>
            </Popconfirm>
          </Space>
          {testFailMsg && (
            <Alert type="error" showIcon style={{ marginTop: 12 }} title={testFailMsg} />
          )}
        </>
      )}

      {phase === 'revoked' && (
        <>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title={t('wxpusher.revokedTitle')}
            description={t('wxpusher.revokedDesc')}
          />
          <Button type="primary" loading={creating} onClick={startChallenge}>
            {t('wxpusher.rebind')}
          </Button>
        </>
      )}

      {phase === 'loading' && (
        <div style={{ padding: '12px 0', textAlign: 'center' }}>
          <Spin />
        </div>
      )}

      {phase === 'error' && (
        <Alert
          type="error"
          showIcon
          title={t('wxpusher.loadFailed')}
          action={
            <Button size="small" onClick={startChallenge}>
              {t('common:action.retry')}
            </Button>
          }
        />
      )}
    </Card>
  );
}
