/**
 * 启用策略向导（Phase D Task 3）：公共目录策略 → 本人运行实例。
 *
 * 六步：版本确认 → 账户模式（默认独立策略子账户）→ 资金/配额+风险 →
 * 执行模式 → 通知偏好（9 键默认全开）→ 确认摘要 + 提交。
 *
 * 提交顺序（按 T2 客户端已有方法组合，说明）：
 *   1) createMyInstance —— 后端 create 恒建 enabled=false 实例（安全默认）；
 *   2) enableMyInstance —— enable 是独立串行化事务（行锁内做配额/单实例/
 *      账户 ACTIVE 校验），失败时实例停在"已创建未启用"，可修复后重试，
 *      不会出现"未建账户就启用"的半态。
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, Button, Modal, Space, Steps, message } from 'antd';
import type { CatalogStrategy } from '../../services/myStrategiesApi';
import { createMyInstance, enableMyInstance } from '../../services/myStrategiesApi';
import {
  buildCreateBody,
  initialWizardState,
  pickDefaultVersion,
  validateWizardStep,
  type WizardState,
} from './wizardModel';
import {
  AccountStep,
  CapitalRiskStep,
  ConfirmStep,
  ExecutionStep,
  NotificationsStep,
  VersionStep,
} from './wizardSteps';

interface Props {
  open: boolean;
  strategy: CatalogStrategy;
  onClose: () => void;
  /** 启用成功后回调（目录页刷新实例态）。 */
  onDone: () => void;
}

const LAST_STEP = 5;

export default function EnableStrategyWizard({ open, strategy, onClose, onDone }: Props) {
  const { t } = useTranslation('pages');
  const [step, setStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [state, setState] = useState<WizardState>(() =>
    initialWizardState(pickDefaultVersion(strategy.versions) ?? ''),
  );

  const update = (patch: Partial<WizardState>) =>
    setState((s) => ({ ...s, ...patch }));

  const next = () => {
    const errKey = validateWizardStep(step, state);
    if (errKey) {
      setError(t(errKey));
      return;
    }
    setError(null);
    setStep((s) => Math.min(s + 1, LAST_STEP));
  };

  const prev = () => {
    setError(null);
    setStep((s) => Math.max(s - 1, 0));
  };

  const submit = async () => {
    setSubmitting(true);
    try {
      const created = await createMyInstance(buildCreateBody(state));
      await enableMyInstance(created.user_strategy_id);
      message.success(t('strategyCatalog.msgEnabled'));
      onDone();
      onClose();
    } catch {
      // api() 已按 errors.json 统一 toast {code}；向导停在确认步展示内联错误供重试。
      setError(t('enableWizard.submitFailed'));
    } finally {
      setSubmitting(false);
    }
  };

  const stepItems = [
    { title: t('enableWizard.stepVersion') },
    { title: t('enableWizard.stepAccount') },
    { title: t('enableWizard.stepCapital') },
    { title: t('enableWizard.stepExecution') },
    { title: t('enableWizard.stepNotifications') },
    { title: t('enableWizard.stepConfirm') },
  ];

  return (
    <Modal
      title={`${t('enableWizard.title')} · ${strategy.strategy_name}`}
      open={open}
      onCancel={onClose}
      footer={null}
      width={640}
      destroyOnHidden
    >
      <Steps
        size="small"
        current={step}
        items={stepItems}
        style={{ margin: '16px 0 20px' }}
      />
      {error && step !== LAST_STEP && (
        <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />
      )}

      {step === 0 && <VersionStep strategy={strategy} state={state} update={update} />}
      {step === 1 && (
        <AccountStep market={strategy.market} state={state} update={update} />
      )}
      {step === 2 && <CapitalRiskStep state={state} update={update} />}
      {step === 3 && <ExecutionStep state={state} update={update} />}
      {step === 4 && <NotificationsStep state={state} update={update} />}
      {step === LAST_STEP && <ConfirmStep strategy={strategy} state={state} error={error} />}

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 24 }}>
        <Space>
          <Button onClick={onClose}>{t('common:action.cancel')}</Button>
          {step > 0 && <Button onClick={prev}>{t('enableWizard.prev')}</Button>}
          {step < LAST_STEP && (
            <Button type="primary" onClick={next}>
              {t('enableWizard.next')}
            </Button>
          )}
          {step === LAST_STEP && (
            <Button type="primary" loading={submitting} onClick={submit}>
              {t('enableWizard.submit')}
            </Button>
          )}
        </Space>
      </div>
    </Modal>
  );
}
