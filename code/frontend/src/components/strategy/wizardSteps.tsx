/**
 * 启用向导分步内容组件（一步一组件，纯受控展示；Phase D Task 3）。
 *
 * 状态与校验集中在 EnableStrategyWizard（经 wizardModel 纯函数），本文件只做
 * 受控表单渲染：props { state, update } 。label 全部显式 htmlFor/id 关联，
 * 保证 getByLabelText 可达（无障碍 + RTL 测试同源）。
 */
import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert, Descriptions, InputNumber, Radio, Select, Space, Switch, Tag, Typography,
} from 'antd';
import type { CatalogStrategy } from '../../services/myStrategiesApi';
import { listMyAccounts, type MyAccount } from '../../services/myAccountsApi';
import { strategyLabel } from '../../i18n/strategyName';
import {
  EXECUTION_MODES,
  formatVersionNo,
  type WizardExecutionMode,
  type WizardState,
} from './wizardModel';
import NotificationPreferenceForm from './NotificationPreferenceForm';

export interface StepProps {
  state: WizardState;
  update: (patch: Partial<WizardState>) => void;
}

const { Text } = Typography;

/** 显式 label + 控件的一行表单（htmlFor/id 关联，RTL/读屏可达）。 */
function Field({
  htmlFor, label, hint, children,
}: { htmlFor: string; label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <label
        htmlFor={htmlFor}
        style={{ display: 'block', marginBottom: 6, fontWeight: 500 }}
      >
        {label}
      </label>
      {children}
      {hint && (
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 4 }}>
          {hint}
        </Text>
      )}
    </div>
  );
}

// ---------- 步骤 1：版本确认 ----------

export function VersionStep({ strategy, state, update }: StepProps & { strategy: CatalogStrategy }) {
  const { t } = useTranslation('pages');
  return (
    <div>
      <Descriptions size="small" column={1} style={{ marginBottom: 16 }}>
        <Descriptions.Item label={t('enableWizard.strategyLabel')}>
          <b>{strategyLabel(strategy.strategy_name)}</b>
          {strategy.is_builtin && <Tag color="purple" style={{ marginLeft: 8 }}>{t('strategies.builtin')}</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label={t('strategies.colMarket')}>
          {t(`market.${strategy.market}`, { ns: 'common' })}（{strategy.market}）
        </Descriptions.Item>
        <Descriptions.Item label={t('enableWizard.typeLabel')}>
          {strategy.strategy_type}
        </Descriptions.Item>
        <Descriptions.Item label={t('strategies.colDesc')}>
          {strategy.description || <Text type="secondary">—</Text>}
        </Descriptions.Item>
      </Descriptions>
      <label style={{ display: 'block', marginBottom: 6, fontWeight: 500 }}>
        {t('enableWizard.versionLabel')}
      </label>
      <Radio.Group
        value={state.strategyVersionId}
        onChange={(e) => update({ strategyVersionId: e.target.value as string })}
      >
        <Space orientation="vertical">
          {strategy.versions.map((v) => (
            <Radio key={v.strategy_version_id} value={v.strategy_version_id}>
              {formatVersionNo(v.version_no)}
              <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>{v.status}</Text>
            </Radio>
          ))}
        </Space>
      </Radio.Group>
      <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
        {t('enableWizard.versionHint')}
      </Text>
    </div>
  );
}

// ---------- 步骤 2：账户模式 ----------

export function AccountStep({ market, state, update }: StepProps & { market: string }) {
  const { t } = useTranslation('pages');
  const [accounts, setAccounts] = useState<MyAccount[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (state.accountMode !== 'EXISTING_SHARED') return;
    setLoading(true);
    // 仅可绑定本人共享组合账户（SHARED_PORTFOLIO）；STRATEGY_DEDICATED 单实例，
    // 绑上去 enable 必撞 dedicated_account_single_instance。
    listMyAccounts(market)
      .then((rows) => setAccounts(rows.filter((a) => a.account_type === 'SHARED_PORTFOLIO' && a.status === 'ACTIVE')))
      .catch(() => setAccounts([]))
      .finally(() => setLoading(false));
  }, [state.accountMode, market]);

  return (
    <div>
      <Radio.Group
        value={state.accountMode}
        onChange={(e) => update({ accountMode: e.target.value })}
        style={{ width: '100%' }}
      >
        <Space orientation="vertical" style={{ width: '100%', alignItems: 'stretch' }}>
          <div>
            <Radio value="NEW_DEDICATED">{t('enableWizard.accountModeDedicated')}</Radio>
            <Text type="secondary" style={{ display: 'block', fontSize: 12, marginLeft: 24 }}>
              {t('enableWizard.dedicatedHint')}
            </Text>
          </div>
          <div>
            <Radio value="EXISTING_SHARED">{t('enableWizard.accountModeShared')}</Radio>
            <Text type="secondary" style={{ display: 'block', fontSize: 12, marginLeft: 24 }}>
              {t('enableWizard.sharedHint')}
            </Text>
          </div>
        </Space>
      </Radio.Group>

      {state.accountMode === 'EXISTING_SHARED' && (
        <div style={{ marginTop: 16 }}>
          <Field htmlFor="wz-account" label={t('enableWizard.accountSelectLabel')}>
            <Select
              id="wz-account"
              style={{ width: '100%' }}
              loading={loading}
              placeholder={t('enableWizard.accountSelectPlaceholder')}
              value={state.accountId ?? undefined}
              onChange={(v) => update({ accountId: v as string })}
              options={accounts.map((a) => ({
                value: a.account_id,
                label: `${a.account_id} · ${t('enableWizard.accountCash')}${a.cash_balance} ${a.currency}`,
              }))}
              notFoundContent={<Text type="secondary">{t('enableWizard.noSharedAccount')}</Text>}
            />
          </Field>
        </div>
      )}
    </div>
  );
}

// ---------- 步骤 3：资金/配额 + 风险参数 ----------

export function CapitalRiskStep({ state, update }: StepProps) {
  const { t } = useTranslation('pages');
  return (
    <div>
      {state.accountMode === 'NEW_DEDICATED' ? (
        <Field
          htmlFor="wz-initial-capital"
          label={t('enableWizard.initialCapitalLabel')}
          hint={t('enableWizard.initialCapitalHint')}
        >
          <InputNumber
            id="wz-initial-capital"
            style={{ width: '100%' }}
            min={0}
            step={10000}
            value={state.initialCapital}
            onChange={(v) => update({ initialCapital: v })}
            placeholder="100000"
          />
        </Field>
      ) : (
        <Field
          htmlFor="wz-capital-quota"
          label={t('enableWizard.capitalQuotaLabel')}
          hint={t('enableWizard.capitalQuotaHint')}
        >
          <InputNumber
            id="wz-capital-quota"
            style={{ width: '100%' }}
            min={0}
            step={10000}
            value={state.capitalQuota}
            onChange={(v) => update({ capitalQuota: v })}
            placeholder="50000"
          />
        </Field>
      )}
      <Field
        htmlFor="wz-max-ratio"
        label={t('enableWizard.maxPositionRatioLabel')}
        hint={t('enableWizard.maxPositionRatioHint')}
      >
        {/* 不设 min/max 钳制：越界值原样进 state，由 validateWizardStep + 后端
            （0 < ratio <= 1）统一拒绝，避免组件层吞值造成"看似合法"假象。 */}
        <InputNumber
          id="wz-max-ratio"
          style={{ width: '100%' }}
          step={0.01}
          value={state.maxPositionRatio}
          onChange={(v) => update({ maxPositionRatio: v ?? 0 })}
        />
      </Field>
    </div>
  );
}

// ---------- 步骤 4：执行模式 ----------

const EXEC_LABEL_KEY: Record<WizardExecutionMode, string> = {
  MANUAL: 'enableWizard.executionModeManual',
  SEMI_AUTO: 'enableWizard.executionModeSemiAuto',
  FULL_AUTO: 'enableWizard.executionModeFullAuto',
};
const EXEC_HINT_KEY: Record<WizardExecutionMode, string> = {
  MANUAL: 'enableWizard.execManualHint',
  SEMI_AUTO: 'enableWizard.execSemiAutoHint',
  FULL_AUTO: 'enableWizard.execFullAutoHint',
};

export function ExecutionStep({ state, update }: StepProps) {
  const { t } = useTranslation('pages');
  return (
    <div>
      <Radio.Group
        value={state.executionMode}
        onChange={(e) => update({ executionMode: e.target.value as WizardExecutionMode })}
        style={{ width: '100%' }}
      >
        <Space orientation="vertical" style={{ width: '100%', alignItems: 'stretch' }}>
          {EXECUTION_MODES.map((m) => (
            <Radio key={m} value={m}>
              {t(EXEC_LABEL_KEY[m])}
              <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                {t(EXEC_HINT_KEY[m])}
              </Text>
            </Radio>
          ))}
        </Space>
      </Radio.Group>
      <div style={{ marginTop: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
        <Switch
          id="wz-auto-execute"
          checked={state.executionMode !== 'MANUAL' && state.autoExecute}
          disabled={state.executionMode === 'MANUAL'}
          onChange={(v) => update({ autoExecute: v })}
        />
        <label htmlFor="wz-auto-execute">{t('enableWizard.autoExecuteLabel')}</label>
        {state.executionMode === 'MANUAL' && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('enableWizard.autoExecuteManualHint')}
          </Text>
        )}
      </div>
    </div>
  );
}

// ---------- 步骤 5：通知偏好 ----------

export function NotificationsStep({ state, update }: StepProps) {
  const { t } = useTranslation('pages');
  return (
    <div>
      <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
        {t('enableWizard.notifTitle')}
      </Text>
      <NotificationPreferenceForm
        value={state.notificationPrefs}
        onChange={(next) => update({ notificationPrefs: next as WizardState['notificationPrefs'] })}
      />
    </div>
  );
}

// ---------- 步骤 6：确认（摘要 + 提交） ----------

export function ConfirmStep({
  strategy, state, error,
}: { strategy: CatalogStrategy; state: WizardState; error: string | null }) {
  const { t } = useTranslation('pages');
  const enabledCount = Object.values(state.notificationPrefs).filter(Boolean).length;
  return (
    <div>
      {error && (
        <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />
      )}
      <Descriptions size="small" column={1} bordered>
        <Descriptions.Item label={t('enableWizard.strategyLabel')}>
          {strategyLabel(strategy.strategy_name)}
        </Descriptions.Item>
        <Descriptions.Item label={t('enableWizard.versionLabel')}>
          <Text code>{state.strategyVersionId}</Text>
        </Descriptions.Item>
        <Descriptions.Item label={t('enableWizard.stepAccount')}>
          {state.accountMode === 'NEW_DEDICATED'
            ? t('enableWizard.accountModeDedicated')
            : `${t('enableWizard.accountModeShared')}（${state.accountId ?? '—'}）`}
        </Descriptions.Item>
        {state.accountMode === 'NEW_DEDICATED' ? (
          <Descriptions.Item label={t('enableWizard.initialCapitalLabel')}>
            {state.initialCapital}
          </Descriptions.Item>
        ) : (
          <Descriptions.Item label={t('enableWizard.capitalQuotaLabel')}>
            {state.capitalQuota}
          </Descriptions.Item>
        )}
        <Descriptions.Item label={t('enableWizard.maxPositionRatioLabel')}>
          {state.maxPositionRatio}
        </Descriptions.Item>
        <Descriptions.Item label={t('enableWizard.stepExecution')}>
          {t(EXEC_LABEL_KEY[state.executionMode])}
          {state.executionMode !== 'MANUAL' && state.autoExecute
            ? ` · ${t('enableWizard.autoExecuteOn')}`
            : ''}
        </Descriptions.Item>
        <Descriptions.Item label={t('enableWizard.stepNotifications')}>
          {t('enableWizard.summaryNotifications', { count: enabledCount })}
        </Descriptions.Item>
      </Descriptions>
      <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 12 }}>
        {t('enableWizard.confirmHint')}
      </Text>
    </div>
  );
}
