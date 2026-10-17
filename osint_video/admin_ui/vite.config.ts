import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import process from 'node:process';

/** Vite configuration for the management workbench. */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: process.env.OSINT_API_TARGET || 'http://127.0.0.1:8303',
        changeOrigin: true,
      },
    },
  },
});
