// B3-7 Playwright E2E 配置。
// - 用系统 Chrome（channel:'chrome'），避免下载 Chromium（沙箱 CDN 受限）。
// - webServer 起独立 vite dev（端口 5199，避开 5173/5174 占用），/api 经 vite proxy 到真实后端 8102。
// - workers:1 / retries:0（serial，便于真实旅程与调试）；失败保留 trace/screenshot/video。
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  retries: 0,
  timeout: 30000,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5199',
    channel: 'chrome',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // video 需 ffmpeg 二进制（沙箱 CDN 受限未下载）；trace+screenshot 已足够诊断。
  },
  webServer: {
    command: 'npm run dev -- --port 5199 --host 127.0.0.1',
    url: 'http://127.0.0.1:5199',
    reuseExistingServer: false,
    timeout: 120000,
  },
});
