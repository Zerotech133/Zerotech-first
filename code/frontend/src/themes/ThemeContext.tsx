import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

/**
 * colors：设置页预览卡色卡条取值（与 themes/tokens.css 各主题变量一致，
 * 静态值以便脱离当前主题渲染预览）。
 */
export const THEMES = [
  {
    key: 'carbon', label: '碳灰', desc: '专业终端',
    colors: { bgDeep: '#0A0A0C', cyan: '#E8E8E8', violetDeep: '#8E94A3', red: '#FF5A48' },
  },
  {
    key: 'aurora', label: '极光蓝', desc: '科技感',
    colors: { bgDeep: '#030711', cyan: '#22D3EE', violetDeep: '#5B6CF0', red: '#FF4D5E' },
  },
  {
    key: 'emerald', label: '翡翠', desc: '金融绿',
    colors: { bgDeep: '#020C08', cyan: '#34D399', violetDeep: '#10B981', red: '#F87171' },
  },
  {
    key: 'amber', label: '琥珀', desc: '暖色',
    colors: { bgDeep: '#0C0803', cyan: '#FBBF24', violetDeep: '#D97706', red: '#FB7185' },
  },
  {
    key: 'cyber', label: '赛博紫', desc: '炫彩',
    colors: { bgDeep: '#050014', cyan: '#E879F9', violetDeep: '#9333EA', red: '#FB7185' },
  },
] as const;

export type ThemeKey = (typeof THEMES)[number]['key'];
export const DEFAULT_THEME: ThemeKey = 'carbon';
const STORAGE_KEY = 'zhice_theme';

const THEME_KEYS = new Set<string>(THEMES.map((t) => t.key));

function readStoredTheme(): ThemeKey {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v && THEME_KEYS.has(v)) return v as ThemeKey;
  } catch {
    /* localStorage 不可用时回落默认 */
  }
  return DEFAULT_THEME;
}

interface ThemeContextValue {
  theme: ThemeKey;
  setTheme: (t: ThemeKey) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeKey>(readStoredTheme);

  // 初始 + 变化时同步到 body 属性（CSS 变量按 body[data-theme] 生效）
  useEffect(() => {
    document.body.setAttribute('data-theme', theme);
  }, [theme]);

  const setTheme = useCallback((t: ThemeKey) => {
    setThemeState(t);
    try {
      localStorage.setItem(STORAGE_KEY, t);
    } catch {
      /* 忽略持久化失败（隐私模式等） */
    }
  }, []);

  const value = useMemo(() => ({ theme, setTheme }), [theme, setTheme]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}
