import { showToast } from '../components/Toast';
import i18n from '../i18n';

const BASE = import.meta.env.VITE_API_BASE || '/api/v1';

/**
 * 401 不触发全局登出跳转的认证端点（deep-test C2）：
 * 这些端点的 401 是"凭证错误"业务响应而非"会话过期"——必须走通用错误分支
 * （toast + errors 命名空间 i18n 翻译 + throw 给页面显示红字）。
 * 旧逻辑对登录页 401 也执行"清 token + window.location 整页刷新"，把红字
 * 一起刷掉，用户输错密码只看到页面闪一下、无任何提示。
 */
const AUTH_CREDENTIAL_PATHS = ['/auth/login', '/auth/register'];

export function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('zhice_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// 从 localStorage 读当前语言（不 import i18n，避免循环依赖）；后端据此按语言返回文案/错误码翻译
export function langHeader(): Record<string, string> {
  return { 'Accept-Language': localStorage.getItem('zhice_lang') === 'en' ? 'en' : 'zh' };
}

function getErrorMessage(status: number, text: string): string {
  if (status === 422) return '参数错误，请检查输入';
  if (status === 404) return '资源不存在';
  if (status === 500) return '服务器内部错误';
  if (text) return text;
  return `操作失败（HTTP ${status}）`;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  try {
    // headers 合并顺序：调用方自定义头（如 Idempotency-Key）先并入，但 Authorization/
    // Accept-Language 由 api() 强制置后——调用方无法覆盖身份与语言（B3-1 加固）。
    // 注意 ...init 放最前以透传 method/body/signal，再用显式 headers 覆盖其 init.headers。
    const resp = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(init?.headers as Record<string, string> | undefined),
        ...authHeaders(),
        ...langHeader(),
      },
    });
    if (!resp.ok) {
      // 全局 401 处理：会话失效 → 清除 token + role 并跳转登录页。
      // 例外：登录/注册的 401 是凭证错误业务响应，落通用错误分支（见常量注释）。
      if (
        resp.status === 401 &&
        !AUTH_CREDENTIAL_PATHS.some(p => path.startsWith(p))
      ) {
        localStorage.removeItem('zhice_token');
        localStorage.removeItem('zhice_role');
        window.location.href = '/login';
        return Promise.reject(new Error('未授权，请重新登录'));
      }
      const text = await resp.text();
      let errorMsg = getErrorMessage(resp.status, text);
      let code: string | undefined;
      // 方案A：后端返回 {detail:{code}} 时按当前语言翻译（errors 命名空间）
      try {
        const parsed = (JSON.parse(text) as { detail?: { code?: string } })?.detail?.code;
        if (typeof parsed === 'string') {
          code = parsed;
          errorMsg = i18n.t(parsed, { ns: 'errors' });
        }
      } catch {
        /* 非 JSON code，保留默认消息 */
      }
      showToast(errorMsg, 'error');
      // 附 status/code 供上层（如 adminEntitlementsApi）归一化分类；任何 HTTP 状态
      // 均为「已知失败」，不可重试（retryable 仅用于下方网络/超时分支）。
      const err = new Error(errorMsg) as Error & { status?: number; code?: string };
      err.status = resp.status;
      if (code) err.code = code;
      throw err;
    }
    return resp.json() as Promise<T>;
  } catch (err) {
    if (err instanceof TypeError) {
      // 网络错误/超时 = 「未知结果」：标记 retryable，允许写操作用同幂等键安全重试。
      showToast('网络连接失败', 'error');
      const e = new Error('网络连接失败') as Error & { retryable?: boolean };
      e.retryable = true;
      throw e;
    }
    throw err;
  }
}
