import { useEffect, useRef } from 'react';

/**
 * 盘中定时自动刷新（V1 useAutoRefresh 对齐）。
 * 页面不可见（document.hidden）时暂停，避免后台空耗；依赖变化自动重启。
 */
export function useAutoRefresh(callback: () => void, intervalMs = 30000, deps: unknown[] = []) {
  const cbRef = useRef(callback);
  cbRef.current = callback;

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden) {
        cbRef.current();
      }
    }, intervalMs);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, ...deps]);
}
