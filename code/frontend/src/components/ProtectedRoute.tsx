import { useState, useEffect } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';

export default function ProtectedRoute({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<'checking' | 'valid' | 'invalid'>('checking');
  const location = useLocation();
  const token = localStorage.getItem('zhice_token');

  useEffect(() => {
    if (!token) {
      setStatus('invalid');
      return;
    }
    // 向后端验证 token 真实性
    fetch('/api/v1/auth/me', {
      headers: { 'Authorization': `Bearer ${token}` },
    })
      .then(async res => {
        if (res.ok) {
          if (typeof res.json === 'function') {
            const currentUser = await res.json() as { role?: string };
            if (currentUser.role) localStorage.setItem('zhice_role', currentUser.role);
          }
          setStatus('valid');
        } else {
          // token 无效或已过期，清除本地缓存并跳转登录
          localStorage.removeItem('zhice_token');
          localStorage.removeItem('zhice_role');
          setStatus('invalid');
        }
      })
      .catch(() => {
        // 网络故障时放行，避免因网络抖动强制退出
        setStatus('valid');
      });
  }, [token]);

  if (status === 'checking') return null; // 验证期间不渲染，避免闪跳
  if (status === 'invalid') {
    // 将来源路径传给登录页，登录成功后可跳回
    return <Navigate to="/login" state={{ from: location.pathname + location.search }} replace />;
  }
  return <>{children}</>;
}
