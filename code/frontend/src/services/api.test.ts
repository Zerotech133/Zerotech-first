import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';
import { api } from './api';

function errorResponse(detail: unknown, status = 503) {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeAll(async () => {
  await i18n.changeLanguage('zh');
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('api 错误文案', () => {
  it('qengine_sync_unavailable 使用中文文案而不是裸错误码', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        errorResponse({
          code: 'qengine_sync_unavailable',
          message: 'QENGINE_SYNC_DB_URL 未配置',
        }),
      ),
    );

    await expect(api('/strategy-center/snapshot')).rejects.toMatchObject({
      message: '策略中台测试库暂不可用，请联系管理员检查 qengine_sync 配置',
      status: 503,
      code: 'qengine_sync_unavailable',
    });
  });

  it('未翻译的错误码回退到后端 detail.message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        errorResponse({ code: 'maintenance_window', message: '维护窗口内暂不可用' }, 400),
      ),
    );

    await expect(api('/strategy-center/snapshot')).rejects.toMatchObject({
      message: '维护窗口内暂不可用',
      status: 400,
      code: 'maintenance_window',
    });
  });

  it('未知 503 不暴露技术错误体或裸 code', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        errorResponse({ code: 'unknown_dependency', message: 'postgres://internal:5432' }),
      ),
    );

    await expect(api('/strategy-center/snapshot')).rejects.toMatchObject({
      message: '服务暂不可用，请稍后重试',
      status: 503,
    });
  });
});
