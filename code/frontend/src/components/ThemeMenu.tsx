import { Dropdown, type MenuProps } from 'antd';
import { CheckOutlined } from '@ant-design/icons';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { THEMES, useTheme, type ThemeKey } from '../themes/ThemeContext';

/** 菜单项 label：`carbon · 碳灰 专业终端`（当前项尾随 check） */
export function useThemeMenuItems(): Required<MenuProps>['items'] {
  const { theme } = useTheme();
  return useMemo(
    () =>
      THEMES.map((t) => ({
        key: t.key,
        label: (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <span>
              <span className="mono" style={{ color: 'var(--txt-dim)' }}>{t.key}</span>
              {' · '}
              {t.label}
              <span style={{ color: 'var(--txt-faint)', marginLeft: 8, fontSize: 12 }}>{t.desc}</span>
            </span>
            {theme === t.key && <CheckOutlined style={{ color: 'var(--cyan)' }} />}
          </span>
        ),
      })),
    [theme],
  );
}

interface ThemeMenuProps {
  /** 触发器内容（默认「◐ 主题」） */
  children?: React.ReactNode;
  /** 触发器 title 提示 */
  title?: string;
  /** 切换后回调（默认静默；如需 message 提示由此传入） */
  onSuccess?: (label: string) => void;
}

/**
 * 主题切换 Dropdown（WorkspaceLayout 顶栏 / HomeBigscreen 右上角共用）。
 * onSelect → useTheme().setTheme（CSS 变量切换 + localStorage 记忆）。
 */
export default function ThemeMenu({ children, title, onSuccess }: ThemeMenuProps) {
  const { t } = useTranslation('pages');
  const { theme, setTheme } = useTheme();
  const items = useThemeMenuItems();
  const resolvedTitle = title ?? t('themeMenu.switchTheme');

  const onClick: MenuProps['onClick'] = ({ key }) => {
    const th = THEMES.find((x) => x.key === key);
    if (!th || key === theme) return;
    setTheme(key as ThemeKey);
    onSuccess?.(th.label);
  };

  return (
    <Dropdown menu={{ items, onClick, selectedKeys: [theme] }} trigger={['click']} placement="bottomRight">
      {children ?? (
        <button className="tbtn" title={resolvedTitle}>
          ◐ {t('themeMenu.theme')}
        </button>
      )}
    </Dropdown>
  );
}
