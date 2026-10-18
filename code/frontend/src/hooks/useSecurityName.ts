import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../services/api';

/**
 * 代码 → 名称 全局解析 hook。
 *
 * - 模块级缓存 Map<ts_code, name>：全站共享，命中即零请求。
 * - 未命中的 code 进入待查集合，由模块级 debounce 批量请求器（300ms 合并）统一
 *   调 GET /market/securities?market={市场}&q={code}&limit=5 查询；
 *   从结果里精确匹配 ts_code === code 取 name，查不到缓存 ''（静默降级）。
 * - 等待名称的组件通过 useSyncExternalStore 订阅缓存版本号刷新。
 */

interface SecurityItem {
  ts_code: string;
  name?: string | null;
}

/** 代码 → 市场推断：.SH/.SZ/.BJ → CN；含 '/'（BTC/USDT）→ CRYPTO；
 *  5 位数字且首位 0-3 → HK；纯字母 → US；兜底 CN */
export function inferMarket(code: string): string {
  const c = (code || '').trim().toUpperCase();
  if (!c) return 'CN';
  if (/\.(SH|SZ|BJ)$/.test(c)) return 'CN';
  if (c.includes('/')) return 'CRYPTO';
  if (/^\d{5}$/.test(c)) return 'HK';
  if (/^[A-Z]+$/.test(c)) return 'US';
  return 'CN';
}

/* ── 模块级缓存与批量请求器 ── */

const nameCache = new Map<string, string>(); // ts_code -> name（'' = 已查但未知）
const pendingCodes = new Set<string>();
const failedCodes = new Set<string>(); // 网络失败标记，避免同帧重试风暴
const subscribers = new Set<() => void>();
let cacheVersion = 0;
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function notify() {
  cacheVersion += 1;
  subscribers.forEach((fn) => fn());
}

function subscribe(fn: () => void): () => void {
  subscribers.add(fn);
  return () => {
    subscribers.delete(fn);
  };
}

function getSnapshotVersion(): number {
  return cacheVersion;
}

function scheduleLookup(code: string) {
  pendingCodes.add(code);
  if (flushTimer != null) clearTimeout(flushTimer);
  flushTimer = setTimeout(flushPending, 300);
}

function flushPending() {
  flushTimer = null;
  const codes = Array.from(pendingCodes).filter((c) => !failedCodes.has(c));
  pendingCodes.clear();
  if (codes.length === 0) return;

  // 按推断市场分组，逐 code 一次精确查询（limit=5 足够 ts_code 精确匹配）
  const byMarket = new Map<string, string[]>();
  for (const code of codes) {
    const mk = inferMarket(code);
    const list = byMarket.get(mk) ?? [];
    list.push(code);
    byMarket.set(mk, list);
  }

  for (const [mk, mkCodes] of byMarket) {
    // allSettled：某市场失败不影响其它市场写入缓存
    Promise.allSettled(
      mkCodes.map((code) =>
        api<SecurityItem[]>(
          `/market/securities?market=${encodeURIComponent(mk)}&q=${encodeURIComponent(code)}&limit=5`,
        ),
      ),
    ).then((results) => {
      let changed = false;
      results.forEach((res, i) => {
        const code = mkCodes[i];
        if (nameCache.has(code)) return; // 已被并发请求写入
        if (res.status === 'fulfilled') {
          const list = res.value;
          const exact = Array.isArray(list) ? list.find((s) => s.ts_code === code) : undefined;
          nameCache.set(code, exact?.name || '');
          changed = true;
        } else {
          failedCodes.add(code); // 失败不写缓存，下次挂载仍可重试一次
        }
      });
      if (changed) notify();
    });
  }
}

/* ── Hook ── */

/**
 * 返回 code 对应的证券名称（'' = 未知/查询中/查不到）。
 * 首个使用该 code 的渲染触发查询，300ms 内多个 code 合并发出。
 */
export function useSecurityName(code: string): string {
  const version = useSyncExternalStore(subscribe, getSnapshotVersion);

  useEffect(() => {
    const c = (code || '').trim();
    if (!c || nameCache.has(c) || pendingCodes.has(c)) return;
    scheduleLookup(c);
  }, [code, version]);

  return nameCache.get((code || '').trim()) ?? '';
}
