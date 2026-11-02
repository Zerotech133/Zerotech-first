import { ConfigProvider, theme } from 'antd';
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, HashRouter } from 'react-router-dom';

import App from './App';
import { LanguageProvider, useLang } from './contexts/LanguageContext';
import { MarketProvider } from './contexts/MarketContext';
import { ANTD_LOCALE } from './i18n'; // import 即执行 i18next.init（副作用）
import { ThemeProvider } from './themes/ThemeContext';
import './themes/tokens.css';
import './styles.css';

// ConfigProvider 的 locale 跟随语言（antd 组件级文案联动），故抽成组件以用 useLang
function AppShell() {
  const { lang } = useLang();
  return (
    <ConfigProvider
      locale={ANTD_LOCALE[lang]}
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: '#8E94A3',
          colorBgBase: '#101014',
          colorBgContainer: '#16161A',
          colorBorder: 'rgba(255,255,255,.12)',
          borderRadius: 6,
          fontSize: 13,
        },
      }}
    >
      {import.meta.env.VITE_ROUTER === 'hash' ? (
      <HashRouter>
        <MarketProvider>
          <App />
        </MarketProvider>
      </HashRouter>
    ) : (
    <BrowserRouter basename={(import.meta.env.BASE_URL || '/').replace(/\/$/, '') || '/'}>
        {/* MarketProvider 需在 Router 内（market 由 useParams 派生） */}
        <MarketProvider>
          <App />
        </MarketProvider>
      </BrowserRouter>
    )}
    </ConfigProvider>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <LanguageProvider>
        <AppShell />
      </LanguageProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
