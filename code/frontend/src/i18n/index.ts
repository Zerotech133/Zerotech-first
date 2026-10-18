// i18n 初始化：react-i18next + 4 命名空间(common/pages/errors/domain)，中英双语。
// 语言持久化：localStorage(zhice_lang) + 登录后跟随 user.locale。antd 组件文案联动见 ANTD_LOCALE。
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import enCommon from './locales/en/common.json';
import enDomain from './locales/en/domain.json';
import enErrors from './locales/en/errors.json';
import enPages from './locales/en/pages.json';
import enPOps from './locales/en/p_ops.json';
import enPPages2 from './locales/en/p_pages2.json';
import enPShell from './locales/en/p_shell.json';
import enPStrategy from './locales/en/p_strategy.json';
import enPTrading from './locales/en/p_trading.json';
import zhCommon from './locales/zh/common.json';
import zhDomain from './locales/zh/domain.json';
import zhErrors from './locales/zh/errors.json';
import zhPOps from './locales/zh/p_ops.json';
import zhPPages2 from './locales/zh/p_pages2.json';
import zhPShell from './locales/zh/p_shell.json';
import zhPStrategy from './locales/zh/p_strategy.json';
import zhPTrading from './locales/zh/p_trading.json';
import zhPages from './locales/zh/pages.json';

export const SUPPORTED = ['zh', 'en'] as const;
export type Lang = (typeof SUPPORTED)[number];
export const STORAGE_KEY = 'zhice_lang';

// antd 组件级文案(日期选择/分页/表格空态等)随语言联动
export const ANTD_LOCALE: Record<Lang, typeof zhCN> = { zh: zhCN, en: enUS };

export function isLang(x: unknown): x is Lang {
  return x === 'zh' || x === 'en';
}

export function getInitialLang(): Lang {
  const saved = localStorage.getItem(STORAGE_KEY);
  return isLang(saved) ? saved : 'zh';
}

void i18n.use(initReactI18next).init({
  resources: {
    zh: {
      common: zhCommon,
      pages: { ...zhPages, ...zhPTrading, ...zhPOps, ...zhPStrategy, ...zhPPages2, ...zhPShell },
      errors: zhErrors,
      domain: zhDomain,
    },
    en: {
      common: enCommon,
      pages: { ...enPages, ...enPTrading, ...enPOps, ...enPStrategy, ...enPPages2, ...enPShell },
      errors: enErrors,
      domain: enDomain,
    },
  },
  lng: getInitialLang(),
  fallbackLng: 'zh',
  ns: ['common', 'pages', 'errors', 'domain'],
  defaultNS: 'common',
  interpolation: { escapeValue: false },
  returnNull: false,
});

export default i18n;
