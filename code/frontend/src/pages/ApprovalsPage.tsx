import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, Switch, Tooltip, message } from 'antd';
import TextArea from 'antd/es/input/TextArea';
import { api } from '../services/api';
import { useAutoRefresh } from '../hooks/useAutoRefresh';
import SymbolLink from '../components/SymbolLink';
import { useRole } from '../hooks/useRole';

interface Approval {
  request_id: string;
  market: string;
  strategy_version_id: number;
  ts_code: string;
  name?: string;
  direction: string;
  qty: number;
  limit_price?: number;
  status: string;
}

const STATUS_COLORS: Record<string, string> = {
  PENDING: '#F6A609',
  APPROVED: '#12A150',
  REJECTED: '#F0524D',
  EXPIRED: '#8B93A7',
  CANCELLED: '#8B93A7',
};

/** value 为 i18n key（pages 命名空间），渲染处 t(key)；未命中回退原始枚举值 */
const STATUS_LABELS: Record<string, string> = {
  PENDING: 'approvals.statusPending',
  APPROVED: 'approvals.statusApproved',
  REJECTED: 'approvals.statusRejected',
  EXPIRED: 'approvals.statusExpired',
  CANCELLED: 'approvals.statusCancelled',
};

const DIRECTION_LABELS: Record<string, string> = {
  BUY: 'approvals.dirBuy',
  SELL: 'approvals.dirSell',
  LONG: 'approvals.dirLong',
  SHORT: 'approvals.dirShort',
};

const tag = (color: string): React.CSSProperties => ({
  display: 'inline-block', padding: '2px 8px', borderRadius: 4,
  fontSize: 11, fontWeight: 600, background: color, color: '#fff',
});

export default function ApprovalsPage() {
  const { t } = useTranslation('pages');
  const { canWrite } = useRole();  // VIEWER 只读：隐藏模拟开关/通过/驳回
  const [items, setItems] = useState<Approval[]>([]);
  const [acting, setActing] = useState<string | null>(null);

  // 模拟盘自动执行开关
  const [simEnabled, setSimEnabled] = useState(false);
  const [simLoading, setSimLoading] = useState(false);

  // 批准确认弹窗状态
  const [confirmTarget, setConfirmTarget] = useState<Approval | null>(null);
  const [confirmLoading, setConfirmLoading] = useState(false);

  // 拒绝弹窗状态
  const [rejectTarget, setRejectTarget] = useState<Approval | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const [rejectLoading, setRejectLoading] = useState(false);

  const load = () => {
    api<Approval[]>('/approvals').then(setItems).catch(console.error);
  };

  useEffect(() => {
    load();
    api<{ simulated_enabled: boolean }>('/brokers/simulated')
      .then(d => setSimEnabled(Boolean(d.simulated_enabled)))
      .catch(console.error);
  }, []);

  const toggleSim = async (enabled: boolean) => {
    setSimLoading(true);
    try {
      await api('/brokers/simulated', { method: 'POST', body: JSON.stringify({ enabled }) });
      setSimEnabled(enabled);
      message.success(enabled ? t('approvals.simEnabledOn') : t('approvals.simEnabledOff'));
    } catch {
      // api() 已统一 toast 错误，这里只回滚 UI 状态
    } finally {
      setSimLoading(false);
    }
  };

  // 盘中 30s 自动刷新
  useAutoRefresh(load, 30000, []);

  const approve = async (id: string) => {
    setActing(id);
    setConfirmLoading(true);
    try {
      await api(`/approvals/${encodeURIComponent(id)}/approve`, { method: 'POST', body: JSON.stringify({ actor: 'user' }) });
      message.success(t('approvals.approvedToast'));
      setConfirmTarget(null);
      load();
    } catch {
      message.error(t('approvals.actionFailed'));
    } finally {
      setActing(null);
      setConfirmLoading(false);
    }
  };

  const openConfirm = (a: Approval) => {
    setConfirmTarget(a);
  };

  const openReject = (a: Approval) => {
    setRejectReason('');
    setRejectTarget(a);
  };

  const confirmReject = async () => {
    const reason = rejectReason.trim();
    if (!reason) {
      message.warning(t('approvals.rejectReasonRequired'));
      return;
    }
    if (!rejectTarget) return;
    setRejectLoading(true);
    setActing(rejectTarget.request_id);
    try {
      await api(`/approvals/${encodeURIComponent(rejectTarget.request_id)}/reject`, {
        method: 'POST',
        body: JSON.stringify({ actor: 'user', reason }),
      });
      message.success(t('approvals.rejectedToast'));
      setRejectTarget(null);
      load();
    } finally {
      setRejectLoading(false);
      setActing(null);
    }
  };

  const pending = items.filter(a => a.status === 'PENDING');

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>{t('approvals.title')}</h1>
        {pending.length > 0 && (
          <span style={{ ...tag('#F6A609'), fontSize: 12 }}>{t('approvals.pendingBadge', { count: pending.length })}</span>
        )}
        {canWrite && (
          <Tooltip title={t('approvals.simTooltip')}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, marginLeft: 'auto', cursor: 'default' }}>
              {t('approvals.simAutoExec')}
              <Switch size="small" checked={simEnabled} loading={simLoading} onChange={toggleSim} />
            </span>
          </Tooltip>
        )}
        <button onClick={load} style={{ padding: '4px 12px', borderRadius: 4, border: '1px solid #ddd', background: '#fff', cursor: 'pointer', fontSize: 12 }}>{t('approvals.refresh')}</button>
      </div>

      <div style={{ marginBottom: 16, padding: '10px 14px', background: '#F0F5FF', border: '1px solid #ADC6FF', borderRadius: 8, fontSize: 12, color: '#1D3461', lineHeight: 1.7 }}>
        {t('approvals.banner')}
      </div>

      {items.length === 0 ? (
        <div style={{ color: '#8B93A7', fontSize: 13, padding: '32px 0', textAlign: 'center' }}>
          {t('approvals.empty')}
        </div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #E8EAF0' }}>
              {[t('approvals.colSymbol'), t('approvals.colMarket'), t('approvals.colDirection'), t('approvals.colQty'), t('approvals.colStatus'), t('approvals.colAction')].map(h => (
                <th key={h} style={{ textAlign: 'left', padding: '8px 10px', color: '#8B93A7', fontWeight: 500 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map(a => (
              <tr key={a.request_id} style={{ borderBottom: '1px solid #F2F3F7' }}>
                <td style={{ padding: '10px 10px' }}><SymbolLink code={a.ts_code} /></td>
                <td style={{ padding: '10px 10px' }}>{t(`common:market.${a.market}`, { defaultValue: a.market })}</td>
                <td style={{ padding: '10px 10px' }}>
                  <span style={tag(a.direction === 'BUY' ? '#12A150' : '#F0524D')}>{DIRECTION_LABELS[a.direction] ? t(DIRECTION_LABELS[a.direction]) : a.direction}</span>
                </td>
                <td style={{ padding: '10px 10px' }}>{a.qty}</td>
                <td style={{ padding: '10px 10px' }}>
                  <span style={tag(STATUS_COLORS[a.status] || '#8B93A7')}>{STATUS_LABELS[a.status] ? t(STATUS_LABELS[a.status]) : a.status}</span>
                </td>
                <td style={{ padding: '10px 10px' }}>
                  {canWrite && a.status === 'PENDING' && (
                    <span style={{ display: 'flex', gap: 6 }}>
                      <button
                        onClick={() => openConfirm(a)}
                        disabled={acting === a.request_id}
                        style={{ padding: '4px 10px', borderRadius: 4, background: '#12A150', color: '#fff', border: 'none', cursor: acting === a.request_id ? 'wait' : 'pointer', fontSize: 12, opacity: acting === a.request_id ? 0.6 : 1 }}
                      >{acting === a.request_id ? t('approvals.processing') : t('approvals.pass')}</button>
                      <button
                        onClick={() => openReject(a)}
                        disabled={acting === a.request_id}
                        style={{ padding: '4px 10px', borderRadius: 4, background: '#F0524D', color: '#fff', border: 'none', cursor: acting === a.request_id ? 'wait' : 'pointer', fontSize: 12, opacity: acting === a.request_id ? 0.6 : 1 }}
                      >{t('approvals.reject')}</button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* 批准确认弹窗 */}
      <Modal
        open={confirmTarget != null}
        title={t('approvals.confirmModalTitle')}
        okText={t('approvals.confirmModalOk')}
        cancelText={t('common:action.cancel')}
        okButtonProps={{ style: { background: '#12A150', borderColor: '#12A150' }, loading: confirmLoading }}
        onOk={() => confirmTarget && approve(confirmTarget.request_id)}
        onCancel={() => { if (!confirmLoading) setConfirmTarget(null); }}
        destroyOnHidden
      >
        {confirmTarget && (() => {
          const estimatedAmt = confirmTarget.limit_price != null
            ? confirmTarget.qty * confirmTarget.limit_price
            : null;
          return (
            <div style={{ fontSize: 13, lineHeight: 2 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '80px 1fr', rowGap: 4 }}>
                <span style={{ color: '#8B93A7' }}>{t('approvals.colDirection')}</span>
                <span>
                  <span style={tag(confirmTarget.direction === 'BUY' ? '#12A150' : '#F0524D')}>
                    {DIRECTION_LABELS[confirmTarget.direction] ? t(DIRECTION_LABELS[confirmTarget.direction]) : confirmTarget.direction}
                  </span>
                </span>
                <span style={{ color: '#8B93A7' }}>{t('approvals.colSymbol')}</span>
                <span>
                  {confirmTarget.ts_code}
                  {confirmTarget.name && <span style={{ color: '#8B93A7', marginLeft: 6 }}>{confirmTarget.name}</span>}
                </span>
                <span style={{ color: '#8B93A7' }}>{t('approvals.colQty')}</span>
                <span>{t('approvals.qtyLots', { qty: confirmTarget.qty.toLocaleString() })}</span>
                {confirmTarget.limit_price != null && (
                  <>
                    <span style={{ color: '#8B93A7' }}>{t('approvals.limitPrice')}</span>
                    <span>{confirmTarget.limit_price.toFixed(4)}</span>
                  </>
                )}
                {estimatedAmt != null && (
                  <>
                    <span style={{ color: '#8B93A7' }}>{t('approvals.estimatedAmount')}</span>
                    <span style={{ fontWeight: 600 }}>{estimatedAmt.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}</span>
                  </>
                )}
              </div>
            </div>
          );
        })()}
      </Modal>

      <Modal
        open={rejectTarget != null}
        title={rejectTarget ? t('approvals.rejectModalTitleWith', { code: rejectTarget.ts_code }) : t('approvals.rejectModalTitle')}
        okText={t('approvals.confirmReject')}
        cancelText={t('common:action.cancel')}
        okButtonProps={{ danger: true, loading: rejectLoading }}
        onOk={confirmReject}
        onCancel={() => { if (!rejectLoading) setRejectTarget(null); }}
        destroyOnHidden
      >
        <div style={{ fontSize: 12, color: '#8B93A7', marginBottom: 8 }}>{t('approvals.rejectReasonLabel')}</div>
        <TextArea
          rows={3}
          value={rejectReason}
          onChange={(e) => setRejectReason(e.target.value)}
          placeholder={t('approvals.rejectReasonPlaceholder')}
          maxLength={200}
          showCount
          disabled={rejectLoading}
        />
      </Modal>
    </div>
  );
}
