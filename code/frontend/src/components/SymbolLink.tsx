import { useTranslation } from 'react-i18next';
import { useNavigate, useLocation } from 'react-router-dom';
import { useSecurityName } from '../hooks/useSecurityName';

/**
 * 可点击股票代码：跳转到 K 线行情页（/kline?symbol=xxx）。
 * 全站统一入口——任何展示股票代码的位置都应使用本组件。
 * 代码后自动附带证券名称（name prop 优先，否则 useSecurityName 全局缓存解析）。
 */
export default function SymbolLink({
  code,
  name: nameProp,
  bold = true,
  onClick: onClickProp,
}: {
  code: string;
  name?: string;
  bold?: boolean;
  onClick?: () => void;
}) {
  const { t } = useTranslation('pages');
  const navigate = useNavigate();
  const location = useLocation();
  const resolvedName = useSecurityName(code);
  const name = nameProp ?? resolvedName;
  if (!code) return <span>—</span>;

  // 从当前 URL 路径段提取市场（/:market/...），避免跳到硬编码的 /cn/
  const validMarkets = ['cn', 'us', 'hk', 'crypto'];
  const marketSeg = validMarkets.find(m => location.pathname.startsWith(`/${m}/`)) ?? 'cn';

  return (
    <span
      role="link"
      style={{
        color: '#5B6CF0',
        fontWeight: bold ? 700 : 500,
        fontFamily: 'monospace',
        cursor: 'pointer',
      }}
      title={t('symbolLink.title', { code, name: name ? ` ${name}` : '' })}
      onClick={(e) => {
        e.stopPropagation();
        if (onClickProp) onClickProp();
        else navigate(`/${marketSeg}/overview?tab=kline&symbol=${encodeURIComponent(code)}`);
      }}
      onMouseEnter={(e) => (e.currentTarget.style.textDecoration = 'underline')}
      onMouseLeave={(e) => (e.currentTarget.style.textDecoration = 'none')}
    >
      {code}
      {name && (
        <small style={{ color: 'var(--txt-dim)', fontSize: '0.85em', marginLeft: 4, fontFamily: 'inherit' }}>
          {name}
        </small>
      )}
    </span>
  );
}
