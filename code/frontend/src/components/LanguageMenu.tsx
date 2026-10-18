import { CheckOutlined } from '@ant-design/icons';
import { Dropdown, type MenuProps } from 'antd';
import { useTranslation } from 'react-i18next';

import { useLang } from '../contexts/LanguageContext';
import { SUPPORTED, type Lang } from '../i18n';

const LANG_LABEL: Record<Lang, string> = { zh: '中文', en: 'English' };

/** 语言切换 Dropdown（顶栏，与 ThemeMenu 并列）。切换 → useLang().setLang（i18n+localStorage+PATCH）。 */
export default function LanguageMenu() {
  const { t } = useTranslation();
  const { lang, setLang } = useLang();

  const items: MenuProps['items'] = SUPPORTED.map((l) => ({
    key: l,
    label: (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        {LANG_LABEL[l]}
        {lang === l && <CheckOutlined style={{ color: 'var(--cyan)' }} />}
      </span>
    ),
  }));

  const onClick: MenuProps['onClick'] = ({ key }) => {
    if (key !== lang) setLang(key as Lang);
  };

  return (
    <Dropdown menu={{ items, onClick, selectedKeys: [lang] }} trigger={['click']} placement="bottomRight">
      <button className="tbtn" title={t('lang.label')}>
        🌐 {LANG_LABEL[lang]}
      </button>
    </Dropdown>
  );
}
