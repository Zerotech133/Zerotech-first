/**
 * owner-scoped 通知客户端：渠道化偏好（/my/notification-preferences?channel=）+
 * Telegram deep-link 绑定三件套（challenge/status/unbind，/auth/me/telegram*）+
 * 渠道绑定总览与 WxPusher 绑定三件套（/auth/me/notification-bindings*）。
 *
 * 铁律：复用 api<T>()（token/JSON 头/401 跳登录/{code} 错误翻译），绝不直接
 * fetch；请求体绝不携带 user_id（owner 恒由后端从 JWT 取）；channel 只进
 * URL query，绝不进请求体。DTO 字段逐一对齐后端序列化
 * （my_notification_preferences.py / auth.py 绑定端点）。
 */
import { api } from './api';

/** 静音时段 {"start":"HH:MM","end":"HH:MM"}（跨午夜合法，start==end 非法）。 */
export interface QuietHours {
  start: string;
  end: string;
}

/** 通知渠道（后端枚举一致；缺省/历史调用方恒为 telegram）。 */
export type NotificationChannel = 'telegram' | 'wxpusher';

/** 我的通知偏好（后端 my_notification_preferences.py _serialize，按渠道）。 */
export interface NotificationPreference {
  user_id: string;
  channel: NotificationChannel;
  market_reports_enabled: Record<string, boolean>; // 按市场开关键合并
  signal_enabled: boolean;
  fill_enabled: boolean;
  morning_report_enabled: boolean;
  evening_report_enabled: boolean;
  quiet_hours: QuietHours | null;
  timezone: string; // IANA，如 Asia/Shanghai
  locale: 'zh' | 'en';
}

/** PATCH 偏好请求体（channel 只进 query 绝不进 body；后端 extra=forbid：
 *  未知字段 422；None/缺省=不改，且不支持经 PATCH 清空 quiet_hours）。 */
export interface NotificationPreferencePatch {
  market_reports_enabled?: Record<string, boolean>;
  signal_enabled?: boolean;
  fill_enabled?: boolean;
  morning_report_enabled?: boolean;
  evening_report_enabled?: boolean;
  quiet_hours?: QuietHours;
  timezone?: string;
  locale?: 'zh' | 'en';
}

/** 一次性 deep-link 绑定 challenge（url 含 token，仅本次响应返回，勿展示/存储）。 */
export interface TelegramChallenge {
  url: string; // https://t.me/<bot>?start=<token>
  expires_at: string; // ISO 8601
}

/** 绑定状态（chat_id 后端已脱敏，仅尾 4 位；bot_username 为配置的机器人名）。 */
export interface TelegramBindingStatus {
  bound: boolean;
  chat_id: string | null;
  bot_username: string | null;
}

export function getMyNotificationPreferences(
  channel: NotificationChannel = 'telegram',
): Promise<NotificationPreference> {
  return api<NotificationPreference>(`/my/notification-preferences?channel=${channel}`);
}

export function patchMyNotificationPreferences(
  channel: NotificationChannel = 'telegram',
  body: NotificationPreferencePatch,
): Promise<NotificationPreference> {
  return api<NotificationPreference>(`/my/notification-preferences?channel=${channel}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

export function createTelegramChallenge(): Promise<TelegramChallenge> {
  return api<TelegramChallenge>('/auth/me/telegram/challenge', { method: 'POST' });
}

export function getTelegramBindingStatus(): Promise<TelegramBindingStatus> {
  return api<TelegramBindingStatus>('/auth/me/telegram/status');
}

export function unbindTelegram(): Promise<{ ok: boolean; telegram_chat_id: null }> {
  return api<{ ok: boolean; telegram_chat_id: null }>('/auth/me/telegram', {
    method: 'DELETE',
  });
}

/** 手动绑定：直接提交自己的 Telegram chat_id（PATCH /auth/me/telegram）。
 *  仅在部署未启用 deep-link（telegram_binding_enabled=False）时可用；否则后端 409。 */
export function bindTelegramManual(
  chatId: string,
): Promise<{ ok: boolean; telegram_chat_id: string | null }> {
  return api<{ ok: boolean; telegram_chat_id: string | null }>('/auth/me/telegram', {
    method: 'PATCH',
    body: JSON.stringify({ chat_id: chatId }),
  });
}

/** POST /auth/me/telegram/test 结果（B7 A1：失败时带 reason 供人话映射）。 */
export interface TelegramTestResult {
  ok: boolean;
  /** 失败原因（透传后端 transport 稳定 error_code）：not_configured / network / http_401 / ... */
  reason?: string;
}

export function testTelegram(): Promise<TelegramTestResult> {
  return api<TelegramTestResult>('/auth/me/telegram/test', { method: 'POST' });
}

// ---------- 渠道绑定总览 + WxPusher 绑定三件套（Task 13） ----------

/** 绑定状态：active=当前生效绑定；revoked=已解绑/被覆盖（保留展示）。 */
export type BindingStatus = 'active' | 'revoked';

/** 渠道绑定视图（GET /auth/me/notification-bindings；recipient 后端已脱敏）。 */
export interface NotificationBinding {
  channel: NotificationChannel;
  status: BindingStatus;
  recipient_masked: string | null;
  bound_at: string | null; // ISO 8601
}

/** WxPusher 一次性绑定二维码 challenge（qr_image_url 由后端生成、不含 token，
 *  可安全展示；响应 cache-control no-store，过期即作废重取）。 */
export interface WxPusherChallenge {
  qr_image_url: string;
  expires_at: string; // ISO 8601
}

export const getNotificationBindings = () =>
  api<NotificationBinding[]>('/auth/me/notification-bindings');

export const createWxPusherChallenge = () =>
  api<WxPusherChallenge>('/auth/me/notification-bindings/wxpusher/challenge', {
    method: 'POST',
  });

export const unbindWxPusher = () =>
  api<{ ok: boolean }>('/auth/me/notification-bindings/wxpusher', { method: 'DELETE' });

/** 手动测试推送：body 可选且 user_id 被后端无视（owner 恒取自 JWT）。 */
export const testWxPusher = () =>
  api<{ ok: boolean; reason?: string }>('/auth/me/notification-bindings/wxpusher/test', {
    method: 'POST',
  });
