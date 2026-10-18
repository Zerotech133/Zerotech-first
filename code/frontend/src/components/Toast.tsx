import { useEffect, useState } from 'react';

export interface ToastMessage {
  id: number;
  message: string;
  type: 'error' | 'success' | 'info';
}

let listeners: ((msg: ToastMessage) => void)[] = [];
let counter = 0;

export function showToast(message: string, type: ToastMessage['type'] = 'error') {
  const msg = { id: ++counter, message, type };
  listeners.forEach(fn => fn(msg));
}

export function Toast() {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  useEffect(() => {
    const fn = (msg: ToastMessage) => {
      setToasts(prev => [...prev, msg]);
      setTimeout(() => {
        setToasts(prev => prev.filter(t => t.id !== msg.id));
      }, 4000);
    };
    listeners.push(fn);
    return () => { listeners = listeners.filter(l => l !== fn); };
  }, []);

  if (toasts.length === 0) return null;

  return (
    <div style={{
      position: 'fixed', bottom: 24, right: 24,
      display: 'flex', flexDirection: 'column', gap: 8, zIndex: 9999,
    }}>
      {toasts.map(t => (
        <div key={t.id} style={{
          padding: '10px 16px',
          borderRadius: 6,
          fontSize: 13,
          fontWeight: 500,
          color: '#fff',
          background: t.type === 'error' ? '#F0524D' : t.type === 'success' ? '#12A150' : '#5B6CF0',
          boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
          maxWidth: 360,
          animation: 'slideIn 0.2s ease',
        }}>
          {t.type === 'error' ? '⚠ ' : t.type === 'success' ? '✓ ' : 'ℹ '}{t.message}
        </div>
      ))}
    </div>
  );
}
