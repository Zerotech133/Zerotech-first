import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // 限定单测范围到 src，避免 vitest 误抓 e2e/*.spec.ts（那是 Playwright 用例）。
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
});
