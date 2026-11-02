/**
 * owner-scoped 策略客户端：公共目录 + 我的私有策略 + 我的运行实例。
 *
 * 铁律（Phase D 多用户）：
 * - 全部复用 services/api 的 api<T>()（BASE=/api/v1、Bearer token、JSON 头、
 *   401 跳登录、HTTPException {code} 错误翻译），绝不直接 fetch；
 * - 请求体绝不携带 user_id/owner_user_id——owner 恒由后端从 JWT 取；
 * - DTO 字段逐一对齐后端 _serialize（金额 Decimal→string；请求体金额是 number）。
 *
 * 实例（strategy-instances）放在本文件而非 myAccountsApi：实例生命周期
 * （启用向导/启停/升级）围绕 strategy_version_id 与策略目录展开，属策略域；
 * myAccountsApi 保持纯账户读写。
 */
import { api } from './api';

// ---------- DTO（响应，对齐后端序列化） ----------

/** 公共目录版本项（后端 strategy.py list_strategies）。 */
export interface CatalogVersion {
  strategy_version_id: string;
  version_no: string; // 后端 domain 为 str
  status: string;
}

/** 公共目录策略项（后端 strategy.py list_strategies；公共目录无 visibility 字段）。 */
export interface CatalogStrategy {
  strategy_id: string;
  strategy_name: string;
  description: string;
  market: string;
  strategy_type: string;
  status: string;
  is_builtin: boolean;
  owner_user_id: string | null; // 平台策略为 null（可订阅）
  research_owner?: string | null;
  validation_owner?: string | null;
  data_owner?: string | null;
  ops_owner?: string | null;
  lifecycle_status?: string | null;
  next_review?: string | null;
  version_count: number;
  versions: CatalogVersion[];
  is_sidecar?: boolean;
}

/** 我的策略项（后端 my_strategies.py _serialize；公共+本人私有）。 */
export interface MyStrategy {
  strategy_id: string;
  strategy_name: string;
  market: string;
  visibility: string; // PUBLIC / PRIVATE
  publication_status: string;
  owner_user_id: string | null; // 平台公共策略为 null
  status: string;
  /** Phase D T4 起后端补充（version_no 数值升序）；旧响应缺省时 UI 兜底。 */
  description?: string;
  version_count?: number;
  latest_version_no?: string | null;
  versions?: MyStrategyVersionBrief[];
}

/** 列表行内嵌版本项（后端 _serialize 的 versions 数组）。 */
export interface MyStrategyVersionBrief {
  strategy_version_id: string;
  version_no: string;
  status: string;
}

/** POST /my/strategies/{id}/versions 响应（新建版本回执）。 */
export interface MyStrategyVersion extends MyStrategyVersionBrief {
  strategy_id: string;
}

/** 策略"代码"（GET /strategies/{id}/code）：外部类型→spec JSON（新版本编辑器预填）。 */
export interface StrategyCode {
  kind: 'python' | 'spec';
  editable: boolean;
  content: string;
  strategy_type: string;
  strategy_version_id: string | null;
}

/** 我的运行实例（后端 my_strategy_instances.py _serialize）。 */
export interface MyStrategyInstance {
  user_strategy_id: string;
  user_id: string;
  strategy_version_id: string;
  account_id: string;
  enabled: boolean;
  auto_execute: boolean;
  execution_mode: string; // MANUAL / AUTO ...
  capital_quota: string | null; // Decimal→str
  max_position_ratio: string; // Decimal→str
  risk_parameters: Record<string, unknown>;
  strategy_parameters: Record<string, unknown>;
  notification_preferences: Record<string, unknown>;
  /** 升级意图三元组（后端 d0d3a8f 起恒返回；实例侧 UI 归 T5 running 页）。 */
  upgrade_available_version_id?: string | null;
  pending_upgrade_version_id?: string | null;
  upgrade_requested_at?: string | null;
}

// ---------- DTO（请求体；金额为 number，后端 pydantic float） ----------

export interface CreatePrivateStrategyBody {
  strategy_name: string;
  market: string;
  spec?: Record<string, unknown>;
}

export interface UpdatePrivateStrategyBody {
  name?: string;
  description?: string;
}

export interface CreateMyInstanceBody {
  strategy_version_id: string;
  /** 不传 account_id 时后端按 NEW_DEDICATED 建独立子账户（向导默认）。 */
  account_mode?: string;
  account_id?: string;
  initial_capital?: number;
  execution_mode?: string;
  auto_execute?: boolean;
  capital_quota?: number;
  max_position_ratio?: number;
  risk_parameters?: Record<string, unknown>;
  strategy_parameters?: Record<string, unknown>;
  notification_preferences?: Record<string, unknown>;
}

export interface UpdateMyInstanceBody {
  auto_execute?: boolean;
  execution_mode?: string;
  capital_quota?: number;
  max_position_ratio?: number;
  risk_parameters?: Record<string, unknown>;
  strategy_parameters?: Record<string, unknown>;
  notification_preferences?: Record<string, unknown>;
}

/** 发布申请回执（POST /my/strategies/{id}/publish）。 */
export interface PublicationRequest {
  request_id: string;
  source_strategy_id: string;
  status: string;
}

// ---------- 公共目录（唯一非 /my/ 路径，公共读） ----------

export function listCatalog(market?: string): Promise<CatalogStrategy[]> {
  const qs = market ? `?market=${encodeURIComponent(market)}` : '';
  return api<CatalogStrategy[]>(`/strategies${qs}`);
}

// ---------- 我的私有策略 ----------

export function listMyStrategies(market?: string): Promise<MyStrategy[]> {
  const qs = market ? `?market=${encodeURIComponent(market)}` : '';
  return api<MyStrategy[]>(`/my/strategies${qs}`);
}

export function createPrivateStrategy(
  body: CreatePrivateStrategyBody,
): Promise<MyStrategy> {
  return api<MyStrategy>('/my/strategies', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function updatePrivateStrategy(
  strategyId: string,
  body: UpdatePrivateStrategyBody,
): Promise<MyStrategy> {
  return api<MyStrategy>(`/my/strategies/${encodeURIComponent(strategyId)}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

/**
 * 删除本人私有策略（B5 生命周期收口）：策略+版本连带。
 * 后端 DELETE /my/strategies/{id}：仍被运行实例绑定 → 409
 * {code: strategy.bound_by_instances, instances}（api() 按 errors.json 翻译，
 * 引导先删实例）；有 OPEN 持仓 → 409；他人私有/不存在 → 404。
 */
export function deleteMyStrategy(strategyId: string): Promise<{ deleted: string }> {
  return api<{ deleted: string }>(`/my/strategies/${encodeURIComponent(strategyId)}`, {
    method: 'DELETE',
  });
}

export function requestPublication(strategyId: string): Promise<PublicationRequest> {
  return api<PublicationRequest>(
    `/my/strategies/${encodeURIComponent(strategyId)}/publish`,
    { method: 'POST' },
  );
}

/**
 * 创建新版本 vN+1（版本不可变：编辑 spec=新建版本，绝不改旧版本行）。
 * 后端 POST /my/strategies/{id}/versions（Phase D T4 补齐）：owner 在本人私有
 * 策略上新建；他人私有/不存在 404，非法 spec 400，公共策略 USER 403。
 */
export function createMyStrategyVersion(
  strategyId: string,
  spec: Record<string, unknown>,
): Promise<MyStrategyVersion> {
  return api<MyStrategyVersion>(
    `/my/strategies/${encodeURIComponent(strategyId)}/versions`,
    { method: 'POST', body: JSON.stringify({ spec }) },
  );
}

/**
 * 取策略"代码"（外部类型=spec JSON）：新版本编辑器的当前 spec 预填。
 * 非 /my/ 公共读路径（与 listCatalog 同类），仅对本人私有行调用。
 */
export function getMyStrategySpec(strategyId: string): Promise<StrategyCode> {
  return api<StrategyCode>(`/strategies/${encodeURIComponent(strategyId)}/code`);
}

// ---------- 我的运行实例 ----------

export function listMyInstances(
  market?: string,
  enabled?: boolean,
): Promise<MyStrategyInstance[]> {
  const params: string[] = [];
  if (market !== undefined) params.push(`market=${encodeURIComponent(market)}`);
  if (enabled !== undefined) params.push(`enabled=${enabled}`);
  const qs = params.length ? `?${params.join('&')}` : '';
  return api<MyStrategyInstance[]>(`/my/strategy-instances${qs}`);
}

export function createMyInstance(body: CreateMyInstanceBody): Promise<MyStrategyInstance> {
  return api<MyStrategyInstance>('/my/strategy-instances', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function updateMyInstance(
  userStrategyId: string,
  body: UpdateMyInstanceBody,
): Promise<MyStrategyInstance> {
  return api<MyStrategyInstance>(
    `/my/strategy-instances/${encodeURIComponent(userStrategyId)}`,
    { method: 'PATCH', body: JSON.stringify(body) },
  );
}

export function enableMyInstance(userStrategyId: string): Promise<MyStrategyInstance> {
  return api<MyStrategyInstance>(
    `/my/strategy-instances/${encodeURIComponent(userStrategyId)}/enable`,
    { method: 'PATCH' },
  );
}

export function disableMyInstance(userStrategyId: string): Promise<MyStrategyInstance> {
  return api<MyStrategyInstance>(
    `/my/strategy-instances/${encodeURIComponent(userStrategyId)}/disable`,
    { method: 'PATCH' },
  );
}

/**
 * 删除本人运行实例（B5 运营死锁解锁）：需先停用且无 OPEN 持仓。
 * 后端 DELETE /my/strategy-instances/{id}：409 {code: instance.not_disabled /
 * instance.has_open_positions}（api() 按 errors.json 翻译）；非 owner/不存在 404。
 */
export function deleteMyInstance(
  userStrategyId: string,
): Promise<{ deleted: string; account_id: string | null }> {
  return api<{ deleted: string; account_id: string | null }>(
    `/my/strategy-instances/${encodeURIComponent(userStrategyId)}`,
    { method: 'DELETE' },
  );
}

/**
 * 申请升级实例到目标版本（仅记录意图；旧持仓平仓后由后续流程切换）。
 * 后端端点 PATCH /my/strategy-instances/{id}/upgrade 已落地（d0d3a8f）；
 * 实例侧升级横幅/按钮 UI 归 Phase D T5（running 页）。
 */
export function requestUpgrade(
  userStrategyId: string,
  targetVersionId: string,
): Promise<MyStrategyInstance> {
  return api<MyStrategyInstance>(
    `/my/strategy-instances/${encodeURIComponent(userStrategyId)}/upgrade`,
    { method: 'PATCH', body: JSON.stringify({ target_version_id: targetVersionId }) },
  );
}
