import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const target = process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8105';

export default defineConfig({
  base: process.env.VITE_BASE || '/',
  plugins: [react()],
  server: {
    // 固定 5175 且禁止漂移：5173/5174 已被本机其他项目的 Docker 容器长期占用
    // （founderos_ontology-frontend / whotalk-web）。vite 默认端口冲突会静默顺延，
    // 本项目因此漂到 5174；该容器随后抢占 5174 后，浏览器旧页面的登录请求被转发到
    // 别人的后端，返回 422（missing email）。strictPort 让冲突直接启动失败而非串台。
    port: 5175,
    strictPort: true,
    // 监听所有网卡：门户/局域网设备经 http://<主机IP>:5175 可达（默认仅 ::1）
    host: true,
    proxy: {
      '/api': {
        target,
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
