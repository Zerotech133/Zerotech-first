import { useEffect, useRef } from 'react';

/**
 * 行情 WebSocket 推送 hook（/market/ws/market，MarketPage 与 PositionsPage 共用）。
 *
 * 两处历史坑的修复（deep-test B8 A1/A2）：
 *
 * 1. token 必须在「建连时」读取 localStorage——此前 WS_URL 是模块级常量，
 *    SPA 登录（navigate 跳转）后模块不会重新求值，固化为空 token → 后端
 *    未 accept 即 close(4001)，uvicorn 以 HTTP 403 拒绝握手。现在每次连接
 *    前现读；token 为空则不连（两页均有 REST 轮询降级，不损失数据）。
 *
 * 2. 开发模式 React.StrictMode 双挂载会立刻销毁首轮 effect 建立的连接，
 *    若 WebSocket 尚未完成握手就被 close()，浏览器报
 *    "WebSocket is closed before the connection is established"（间歇出现，
 *    生产无 StrictMode 不受影响）。延迟 200ms 建连，挂载→卸载间隔内
 *    定时器被取消，不再产生孤儿连接。
 *
 * 服务端行为：握手成功后立即推送一次 {type:'quotes'}，之后 ~10s 广播快照；
 * 后端要求 ?token=<JWT>，无效则 4001 关闭（不 accept → 403 握手拒绝）。
 */

/** 每次调用时读最新 token 构造 WS URL（勿提为模块级常量，见头部注释 1） */
function buildWsUrl(): string {
  const token = localStorage.getItem('zhice_token') ?? '';
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/v1/market/ws/market?token=${encodeURIComponent(token)}`;
}

/**
 * @returns aliveRef —— 当前 WS 是否活跃收到过推送（收到首条 quotes 即 true，
 *   断连/卸载置 false）。调用方可用它决定是否启用 REST 轮询兜底
 *   （PositionsPage 30s 轮询仅在 WS 不活跃时跑）。
 */
export function useMarketQuoteSocket(onQuotes: () => void, deps: unknown[] = []) {
  const aliveRef = useRef(false);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/exhaustive-deps
    const handler = () => onQuotes();
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const token = localStorage.getItem('zhice_token');
    if (!token) {
      // 未登录：不发起 WS（避免必然的 403 握手失败噪音），由调用方 REST 轮询兜底
      return;
    }

    // 延迟建连：规避 StrictMode 双挂载对未完成握手连接的 close（见头部注释 2）
    timer = setTimeout(() => {
      timer = null;
      try {
        ws = new WebSocket(buildWsUrl());
        ws.onmessage = (event) => {
          try {
            const msg = JSON.parse(event.data);
            if (msg?.type === 'quotes') {
              aliveRef.current = true;
              handler();
            }
          } catch {
            /* 忽略非 JSON 心跳 */
          }
        };
        ws.onclose = () => { aliveRef.current = false; };
        ws.onerror = () => { aliveRef.current = false; };
      } catch {
        /* WS 构造失败（极端环境）忽略，REST 轮询兜底 */
      }
    }, 200);

    return () => {
      aliveRef.current = false;
      if (timer != null) clearTimeout(timer);
      ws?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return aliveRef;
}
