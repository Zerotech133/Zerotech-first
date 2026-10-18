/**
 * 启用向导纯模型：状态/默认值/分步校验/请求体组装（Phase D Task 3）。
 *
 * 全部为纯函数/纯常量（无 React 依赖），便于单测；UI 交互见 wizardSteps.tsx。
 * 语义与后端对齐：
 * - DEFAULT_NOTIFICATION_PREFS 与后端 domain UserStrategyInstance
 *   .DEFAULT_NOTIFICATION_PREFERENCES 同源（9 键默认全开）；
 * - 校验边界与后端 service 对齐：initial_capital>0（NEW_DEDICATED 必填）、
 *   共享账户 capital_quota>0（enable 时 user_strategy.shared_quota_required）、
 *   0 < max_position_ratio <= 1（user_strategy.invalid_max_position_ratio）；
 * - buildCreateBody 绝不携带 user_id/owner_user_id（owner 恒由后端从 JWT 取）。
 */
import type { CreateMyInstanceBody } from '../../services/myStrategiesApi';

export type WizardAccountMode = 'NEW_DEDICATED' | 'EXISTING_SHARED';
export type WizardExecutionMode = 'MANUAL' | 'SEMI_AUTO' | 'FULL_AUTO';

/** 与后端 ExecutionMode 枚举一一对应。 */
export const EXECUTION_MODES: readonly WizardExecutionMode[] = [
  'MANUAL',
  'SEMI_AUTO',
  'FULL_AUTO',
];

/** 9 键通知开关（后端 DEFAULT_NOTIFICATION_PREFERENCES 同源）。 */
export const NOTIFICATION_KEYS = [
  'signal',
  'plan',
  'approval',
  'risk',
  'order',
  'fill',
  'stop_loss',
  'take_profit',
  'runtime_error',
] as const;

export type NotificationKey = (typeof NOTIFICATION_KEYS)[number];

export const DEFAULT_NOTIFICATION_PREFS: Record<NotificationKey, boolean> =
  Object.fromEntries(NOTIFICATION_KEYS.map((k) => [k, true])) as Record<
    NotificationKey,
    boolean
  >;

export interface WizardVersion {
  strategy_version_id: string;
  version_no: string;
  status: string;
}

/**
 * version_no → 数值：剥 v/V 前缀再 parseInt（后端真形态 "v1"/"v2"，裸数字兼容）。
 * 不可解析 → NaN。共享解析（MyStrategyInstancesPage 升级候选/此处默认版本同口径，
 * 对齐后端 StrategyCatalogService._version_sort_key 的 int(vn.lstrip("vV"))）。
 */
export function versionNoNum(v: string | undefined): number {
  if (!v) return NaN;
  const n = Number.parseInt(v.replace(/^v/i, ''), 10);
  return Number.isFinite(n) ? n : NaN;
}

/**
 * 渲染层规范化：version_no 已含 v/V 前缀则原样显示，裸数字才补 `v`
 * （防止真形态 "v2" 被模板 `v{version_no}` 渲染成 "vv2"，Minor #5）。
 */
export function formatVersionNo(v: string): string {
  return /^v/i.test(v) ? v : `v${v}`;
}

/**
 * 可作为默认/升级目标的版本状态（对齐后端 user_strategy_service
 * ._RUNNABLE_VERSION_STATUSES；StrategyStatus 枚举无 PUBLISHED 态——
 * 此前按 PUBLISHED 过滤是永不命中的死分支）。
 */
export const RUNNABLE_VERSION_STATUSES: ReadonlySet<string> = new Set([
  'STAGING',
  'ACTIVE',
]);

/**
 * 选默认版本：优先最高 version_no 的可运行版本（STAGING/ACTIVE），无则回退
 * 最高 version_no。比较经 versionNoNum（剥 v/V 前缀），"v3" > "v10" 的
 * 字典序/NaN 恒取首项两类错误都不再发生。
 */
export function pickDefaultVersion(versions: readonly WizardVersion[]): string | null {
  if (!versions.length) return null;
  const runnable = versions.filter((v) => RUNNABLE_VERSION_STATUSES.has(v.status));
  const pool = runnable.length ? runnable : versions;
  return pool.reduce((best, v) =>
    versionNoNum(v.version_no) > versionNoNum(best.version_no) ? v : best,
  ).strategy_version_id;
}

export interface WizardState {
  strategyVersionId: string;
  accountMode: WizardAccountMode; // 默认 NEW_DEDICATED（向导产品语义）
  accountId: string | null; // EXISTING_SHARED 必填
  initialCapital: number | null; // NEW_DEDICATED 必填（>0）
  capitalQuota: number | null; // EXISTING_SHARED 必填（>0，enable 校验）
  maxPositionRatio: number; // 0 < x <= 1，默认 0.1（后端同默认）
  executionMode: WizardExecutionMode; // 默认 MANUAL（后端同默认）
  autoExecute: boolean; // 默认 false
  notificationPrefs: Record<NotificationKey, boolean>;
}

export function initialWizardState(strategyVersionId: string): WizardState {
  return {
    strategyVersionId,
    accountMode: 'NEW_DEDICATED',
    accountId: null,
    initialCapital: null,
    capitalQuota: null,
    maxPositionRatio: 0.1,
    executionMode: 'MANUAL',
    autoExecute: false,
    notificationPrefs: { ...DEFAULT_NOTIFICATION_PREFS },
  };
}

/**
 * 分步校验：返回错误文案 i18n key（null=通过）。
 * 仅校验与后端硬约束同源的分步必填/边界，避免前后端口径漂移。
 */
export function validateWizardStep(step: number, s: WizardState): string | null {
  if (step === 1 && s.accountMode === 'EXISTING_SHARED' && !s.accountId) {
    return 'enableWizard.errAccountRequired';
  }
  if (step === 2) {
    if (s.accountMode === 'NEW_DEDICATED') {
      if (s.initialCapital === null || !(s.initialCapital > 0)) {
        return 'enableWizard.errCapitalRequired';
      }
    } else {
      if (s.capitalQuota === null || !(s.capitalQuota > 0)) {
        return 'enableWizard.errQuotaRequired';
      }
    }
    if (!(s.maxPositionRatio > 0 && s.maxPositionRatio <= 1)) {
      return 'enableWizard.errRatioRange';
    }
  }
  return null;
}

/**
 * 组装 createMyInstance 请求体：
 * - NEW_DEDICATED：initial_capital（不传 account_id）；
 * - EXISTING_SHARED：account_id + capital_quota（不传 initial_capital——绑定既有
 *   账户时后端根本不读该字段，传了反而语义混乱）；
 * - MANUAL 模式强制 auto_execute=false（人工模式自动下单自相矛盾）。
 */
export function buildCreateBody(s: WizardState): CreateMyInstanceBody {
  const body: CreateMyInstanceBody = {
    strategy_version_id: s.strategyVersionId,
    account_mode: s.accountMode,
    execution_mode: s.executionMode,
    auto_execute: s.executionMode === 'MANUAL' ? false : s.autoExecute,
    max_position_ratio: s.maxPositionRatio,
    notification_preferences: { ...s.notificationPrefs },
  };
  if (s.accountMode === 'NEW_DEDICATED') {
    body.initial_capital = s.initialCapital ?? undefined;
  } else {
    body.account_id = s.accountId ?? undefined;
    body.capital_quota = s.capitalQuota ?? undefined;
  }
  return body;
}
