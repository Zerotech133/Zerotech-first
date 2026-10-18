import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import {
  Button,
  Card,
  Descriptions,
  InputNumber,
  Modal,
  QRCode,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';

import { strategyLabel } from '../i18n/strategyName';
import {
  createOrder,
  getMembership,
  getOrder,
  getPlans,
  listBuiltinStrategies,
  listSubscriptions,
  type BillingPlans,
  type CheckoutView,
  type MembershipView,
  type PaymentOrderView,
  type SubscriptionView,
} from '../services/billingApi';
import type { CatalogStrategy } from '../services/myStrategiesApi';

const { Paragraph, Text } = Typography;

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function BillingPage() {
  const { t } = useTranslation('pages');
  const navigate = useNavigate();

  const [membership, setMembership] = useState<MembershipView | null>(null);
  const [subs, setSubs] = useState<SubscriptionView[]>([]);
  const [plans, setPlans] = useState<BillingPlans | null>(null);
  const [builtins, setBuiltins] = useState<CatalogStrategy[]>([]);
  const [memberMonths, setMemberMonths] = useState(1);
  const [subMonths, setSubMonths] = useState<Record<string, number>>({});
  const [creating, setCreating] = useState<string | null>(null); // 正在下单的产品键

  // 收银台弹窗 + 轮询
  const [checkout, setCheckout] = useState<CheckoutView | null>(null);
  const [orderStatus, setOrderStatus] = useState<string>('pending');
  const [grantStatus, setGrantStatus] = useState<string>('pending'); // C-6：发放态
  const pollRef = useRef<number | null>(null);

  const reload = useCallback(async () => {
    const [m, s, p, b] = await Promise.all([
      getMembership().catch(() => null),
      listSubscriptions().catch(() => ({ items: [] })),
      getPlans().catch(() => null),
      listBuiltinStrategies().catch(() => []),
    ]);
    if (m) setMembership(m);
    setSubs(s.items ?? []);
    if (p) setPlans(p);
    setBuiltins(b);
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  // 轮询订单状态：弹窗打开且未达终态时每 4s 查一次。
  // 终态 = 订单失败（expired/canceled）或 已支付且发放已定局（granted/failed）；
  // 已支付但发放中（pending/processing/retrying）继续轮询，直到到账或异常。
  useEffect(() => {
    if (!checkout) return;
    const settled =
      (orderStatus !== 'pending' && orderStatus !== 'paid') ||
      (orderStatus === 'paid' &&
        (grantStatus === 'granted' || grantStatus === 'failed'));
    if (settled) return;
    pollRef.current = window.setInterval(async () => {
      try {
        const o: PaymentOrderView = await getOrder(checkout.order_id);
        setOrderStatus(o.status);
        setGrantStatus(o.grant_status ?? 'pending');
        if (o.status === 'paid' && o.grant_status === 'granted') {
          message.success(t('billing.paidOk'));
          reload();
        }
      } catch {
        /* api 层已 toast */
      }
    }, 4000);
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [checkout, orderStatus, grantStatus, reload, t]);

  const priceOf = (sid: string): number => {
    if (!plans) return 0;
    return plans.strategy_overrides?.[sid] ?? plans.strategy_default_monthly_usdt;
  };

  async function order(
    productKey: string,
    productType: 'membership' | 'subscription',
    months: number,
    productRef?: string,
  ) {
    setCreating(productKey);
    try {
      const co = await createOrder({
        product_type: productType,
        product_ref: productRef ?? null,
        months,
      });
      setCheckout(co);
      setOrderStatus(co.status || 'pending');
      setGrantStatus(co.grant_status ?? 'pending');
    } catch {
      /* api 层已 toast */
    } finally {
      setCreating(null);
    }
  }

  return (
    <div style={{ minHeight: '100vh', background: '#f0f2f5', padding: '28px 32px' }}>
      <div style={{ maxWidth: 860, margin: '0 auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <Button onClick={() => navigate('/')}>{t('billing.back')}</Button>
          <h1 style={{ fontSize: 22, margin: 0 }}>{t('billing.title')}</h1>
        </div>

        {/* 当前会员态 */}
        <Card size="small" title={t('billing.currentStatus')} style={{ marginBottom: 20 }}>
          <Descriptions column={1} size="small">
            <Descriptions.Item label={t('billing.tier')}>
              <Tag color={membership?.is_member ? 'gold' : 'default'}>
                {membership?.is_member ? t('billing.member') : t('billing.free')}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label={t('billing.validUntil')}>
              {membership?.is_permanent ? t('billing.permanent') : fmtDate(membership?.valid_until)}
            </Descriptions.Item>
          </Descriptions>
        </Card>

        {/* 会员套餐 */}
        <Card size="small" title={t('billing.membershipPlan')} style={{ marginBottom: 20 }}>
          <Space align="center" wrap>
            <Text strong style={{ fontSize: 18 }}>
              {plans?.membership_monthly_usdt ?? '—'} USDT
            </Text>
            <Text type="secondary">{t('billing.perMonth')}</Text>
            <span style={{ marginLeft: 12 }}>{t('billing.months')}</span>
            <InputNumber
              min={1}
              max={120}
              value={memberMonths}
              onChange={(v) => setMemberMonths(Number(v) || 1)}
            />
            <Button
              type="primary"
              loading={creating === 'membership'}
              onClick={() => order('membership', 'membership', memberMonths)}
            >
              {t('billing.buy')}
            </Button>
          </Space>
        </Card>

        {/* 内置策略订阅 */}
        <Card size="small" title={t('billing.subscribePlan')} style={{ marginBottom: 20 }}>
          {builtins.length === 0 && <Text type="secondary">{t('billing.noStrategies')}</Text>}
          <Space orientation="vertical" style={{ width: '100%' }} size={12}>
            {builtins.map((s) => {
              const active = subs.find((x) => x.strategy_id === s.strategy_id && x.active);
              return (
                <div
                  key={s.strategy_id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                    flexWrap: 'wrap',
                    borderBottom: '1px solid #eee',
                    paddingBottom: 10,
                  }}
                >
                  <Text strong style={{ minWidth: 160 }}>
                    {strategyLabel(s.strategy_name)}
                  </Text>
                  <Text>{priceOf(s.strategy_id)} USDT</Text>
                  <Text type="secondary">{t('billing.perMonth')}</Text>
                  {active && (
                    <Tag color="green">
                      {active.is_permanent
                        ? t('billing.permanent')
                        : t('billing.subscribedUntil', { date: fmtDate(active.valid_until) })}
                    </Tag>
                  )}
                  <span style={{ marginLeft: 'auto' }}>{t('billing.months')}</span>
                  <InputNumber
                    min={1}
                    max={120}
                    value={subMonths[s.strategy_id] ?? 1}
                    onChange={(v) =>
                      setSubMonths((m) => ({ ...m, [s.strategy_id]: Number(v) || 1 }))
                    }
                  />
                  <Button
                    loading={creating === `sub:${s.strategy_id}`}
                    onClick={() =>
                      order(
                        `sub:${s.strategy_id}`,
                        'subscription',
                        subMonths[s.strategy_id] ?? 1,
                        s.strategy_id,
                      )
                    }
                  >
                    {active ? t('billing.renew') : t('billing.subscribe')}
                  </Button>
                </div>
              );
            })}
          </Space>
        </Card>
      </div>

      {/* 收银台弹窗 */}
      <Modal
        open={!!checkout}
        title={t('billing.checkout')}
        onCancel={() => {
          setCheckout(null);
          setOrderStatus('pending');
          setGrantStatus('pending');
        }}
        footer={null}
      >
        {checkout && (
          <Space orientation="vertical" style={{ width: '100%' }} size={12}>
            {orderStatus === 'paid' ? (
              grantStatus === 'granted' ? (
                <Tag color="green" style={{ fontSize: 14, padding: '4px 10px' }}>
                  {t('billing.grantGranted')}
                </Tag>
              ) : grantStatus === 'failed' ? (
                <Tag color="red" style={{ fontSize: 14, padding: '4px 10px' }}>
                  {t('billing.grantFailed')}
                </Tag>
              ) : (
                <Tag color="blue" style={{ fontSize: 14, padding: '4px 10px' }}>
                  {t('billing.grantPending')}
                </Tag>
              )
            ) : (
              <Tag color="orange">{t('billing.awaitingPayment')}</Tag>
            )}
            <Descriptions column={1} size="small">
              <Descriptions.Item label={t('billing.amountDue')}>
                <Text strong>{checkout.amount_usdt} USDT-TRC20</Text>
              </Descriptions.Item>
              <Descriptions.Item label={t('billing.payAddress')}>
                <Paragraph copyable style={{ margin: 0 }}>
                  {checkout.pay_address || '—'}
                </Paragraph>
              </Descriptions.Item>
              <Descriptions.Item label={t('billing.orderId')}>
                <Text code>{checkout.order_id}</Text>
              </Descriptions.Item>
            </Descriptions>
            {checkout.pay_address && orderStatus !== 'paid' && (
              <div style={{ textAlign: 'center', padding: '8px 0' }}>
                <QRCode value={checkout.pay_address} size={180} />
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {t('billing.scanToPay')}
                  </Text>
                </div>
              </div>
            )}
            {checkout.payment_url && orderStatus !== 'paid' && (
              <Button
                type="primary"
                block
                href={checkout.payment_url}
                target="_blank"
                rel="noreferrer"
              >
                {t('billing.openGateway')}
              </Button>
            )}
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('billing.pollHint')}
            </Text>
          </Space>
        )}
      </Modal>
    </div>
  );
}
