// 语言切换 + 持久化：i18next.changeLanguage + localStorage + 登录后同步 user.locale。
// antd 组件文案联动通过 main.tsx 的 ConfigProvider locale={ANTD_LOCALE[lang]} 实现。
import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';

import i18n, { getInitialLang, STORAGE_KEY, type Lang } from '../i18n';
import { api } from '../services/api';

interface LangContextValue {
  lang: Lang;
  setLang: (l: Lang) => void;
}

const LanguageContext = createContext<LangContextValue | null>(null);

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(getInitialLang());

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    void i18n.changeLanguage(l);
    localStorage.setItem(STORAGE_KEY, l);
    // 已登录则同步到账户（best-effort，失败不影响切换）
    if (localStorage.getItem('zhice_token')) {
      api('/auth/me', { method: 'PATCH', body: JSON.stringify({ locale: l }) }).catch(() => {});
    }
  }, []);

  return <LanguageContext.Provider value={{ lang, setLang }}>{children}</LanguageContext.Provider>;
}

export function useLang(): LangContextValue {
  const ctx = useContext(LanguageContext);
  if (!ctx) throw new Error('useLang must be used within LanguageProvider');
  return ctx;
}
