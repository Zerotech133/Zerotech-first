import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Modal, Input, Spin, message, type InputRef } from 'antd';
import {
  SearchOutlined, ExportOutlined, SwapOutlined, LineChartOutlined, BgColorsOutlined,
} from '@ant-design/icons';
import { NAV_GROUPS, MARKET_ROUTES } from '../config/navigation';
import { useMarket, MARKETS, type Market } from '../contexts/MarketContext';
import { THEMES, useTheme, type ThemeKey } from '../themes/ThemeContext';
import { api } from '../services/api';

// ---------- 全局唤起（供导航栏搜索按钮等外部触发） ----------
let paletteOpenListener: (() => void) | null = null;
export function openCommandPalette() {
  paletteOpenListener?.();
}

// ---------- 类型 ----------
interface SecurityItem {
  ts_code: string;
  name: string;
  exchange?: string;
}

type Cmd =
  | { kind: 'nav'; id: string; label: string; sub: string; to: string }
  | { kind: 'market'; id: string; label: string; sub: string; market: Market }
  | { kind: 'theme'; id: string; label: string; sub: string; theme: ThemeKey; themeLabel: string }
  | { kind: 'symbol'; id: string; label: string; sub: string; symbol: string };

const GROUP_ORDER: Cmd['kind'][] = ['nav', 'market', 'theme', 'symbol'];

const isMac = typeof navigator !== 'undefined'
  && /Mac|iPod|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const MODK_HINT = isMac ? '⌘K' : 'Ctrl+K';

// ---------- 市场别名 ----------
const MARKET_ALIASES: Record<Market, string[]> = {
  CN: ['cn', 'a股', 'a', '沪', '深', '中国'],
  US: ['us', '美股', '美', '美利坚'],
  HK: ['hk', '港股', '港'],
  CRYPTO: ['crypto', '加密', '币', '比特币', 'btc'],
};

export default function CommandPalette() {
  const { t } = useTranslation('pages');
  const GROUP_LABEL = useMemo<Record<Cmd['kind'], string>>(() => ({
    nav: t('commandPalette.group.nav'),
    market: t('commandPalette.group.market'),
    theme: t('commandPalette.group.theme'),
    symbol: t('commandPalette.group.symbol'),
  }), [t]);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIdx, setActiveIdx] = useState(0);
  const [securities, setSecurities] = useState<SecurityItem[]>([]);
  const [searching, setSearching] = useState(false);
  const navigate = useNavigate();
  const { market, setMarket } = useMarket();
  const { theme, setTheme } = useTheme();
  const inputRef = useRef<InputRef>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const seqRef = useRef(0);

  // 注册全局唤起 + ⌘K / Ctrl+K 监听（再按一次关闭）
  useEffect(() => {
    paletteOpenListener = () => setOpen(true);
    return () => { paletteOpenListener = null; };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen(prev => !prev);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // 打开时重置并聚焦输入框
  useEffect(() => {
    if (open) {
      setQuery('');
      setActiveIdx(0);
      setSecurities([]);
      const t = setTimeout(() => inputRef.current?.focus(), 60);
      return () => clearTimeout(t);
    }
  }, [open]);

  // 防抖 300ms 标的搜索（输入 ≥2 字符）
  useEffect(() => {
    const q = query.trim();
    if (!open || q.length < 2) {
      setSecurities([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    const seq = ++seqRef.current;
    const timer = setTimeout(() => {
      api<SecurityItem[]>(`/market/securities?market=${encodeURIComponent(market)}&q=${encodeURIComponent(q)}&limit=8`)
        .then(data => {
          if (seqRef.current !== seq) return;
          setSecurities(Array.isArray(data) ? data : []);
        })
        .catch(() => {
          if (seqRef.current !== seq) return;
          setSecurities([]);
        })
        .finally(() => {
          if (seqRef.current === seq) setSearching(false);
        });
    }, 300);
    return () => clearTimeout(timer);
  }, [query, market, open]);

  // ---------- 混合检索：导航 / 市场 / 主题 / 标的 ----------
  const commands = useMemo<Cmd[]>(() => {
    const q = query.trim().toLowerCase();
    const out: Cmd[] = [];

    // a. 导航：大屏 / + 4 市场 × 8 组（NAV_GROUPS 单一来源，Task 5 弃用 WORKSPACES）
    const navEntries: Array<{ label: string; sub: string; to: string }> = [
      { label: t('commandPalette.bigscreen'), sub: t('commandPalette.bigscreenSub'), to: '/' },
    ];
    for (const m of MARKET_ROUTES) {
      const mk = m.toUpperCase() as Market;
      for (const g of NAV_GROUPS) {
        navEntries.push({ label: `${t('market.' + mk, { ns: 'common' })} · ${t(g.labelKey)}`, sub: `/${m}/${g.key}`, to: `/${m}/${g.key}` });
      }
    }
    for (const e of navEntries) {
      const hay = `${e.label} ${e.to}`.toLowerCase();
      if (!q || hay.includes(q)) {
        out.push({ kind: 'nav', id: `nav:${e.to}`, label: e.label, sub: e.sub, to: e.to });
      }
    }

    // b. 市场切换：A股/美股/港股/加密/CN/US...
    for (const m of MARKETS) {
      const label = t('market.' + m, { ns: 'common' });
      const aliases = [label.toLowerCase(), m.toLowerCase(), ...MARKET_ALIASES[m]];
      const hit = !q || aliases.some(a => a.includes(q) || q.includes(a));
      if (hit) {
        out.push({
          kind: 'market', id: `market:${m}`, label,
          sub: market === m ? t('commandPalette.currentMarket', { market: m }) : t('commandPalette.switchToMarket', { market: m }), market: m,
        });
      }
    }

    // c. 主题切换：碳灰/极光蓝/翡翠/琥珀/赛博紫（主题名保留，不翻译）
    for (const th of THEMES) {
      const hay = `${th.key} ${th.label} ${th.desc}`.toLowerCase();
      if (!q || hay.includes(q)) {
        out.push({
          kind: 'theme', id: `theme:${th.key}`, label: t('commandPalette.switchToTheme', { label: th.label }),
          sub: theme === th.key ? t('commandPalette.currentTheme', { desc: th.desc }) : t('commandPalette.themeOption', { key: th.key, desc: th.desc }),
          theme: th.key, themeLabel: th.label,
        });
      }
    }

    // d. 标的：接口返回结果
    for (const s of securities) {
      out.push({
        kind: 'symbol', id: `sym:${s.ts_code}`, label: s.name,
        sub: `${s.ts_code}${s.exchange ? ' · ' + s.exchange : ''}`, symbol: s.ts_code,
      });
    }

    // 分组排序：导航 → 市场 → 主题 → 标的
    out.sort((a, b) => GROUP_ORDER.indexOf(a.kind) - GROUP_ORDER.indexOf(b.kind));
    return out;
  }, [query, securities, market, theme, t]);

  // 分组行（组头 + 条目，条目带扁平索引供键盘导航）
  const grouped = useMemo(() => {
    const rows: Array<
      { type: 'header'; label: string; key: string } |
      { type: 'item'; cmd: Cmd; flatIdx: number; key: string }
    > = [];
    let lastKind: Cmd['kind'] | null = null;
    commands.forEach((cmd, idx) => {
      if (cmd.kind !== lastKind) {
        rows.push({ type: 'header', label: GROUP_LABEL[cmd.kind], key: `h:${cmd.kind}` });
        lastKind = cmd.kind;
      }
      rows.push({ type: 'item', cmd, flatIdx: idx, key: cmd.id });
    });
    return { rows, count: commands.length };
  }, [commands, GROUP_LABEL]);

  // 结果变化时重置选中
  useEffect(() => { setActiveIdx(0); }, [query, securities]);

  // 执行命令
  const run = useCallback((cmd: Cmd | undefined) => {
    if (!cmd) return;
    setOpen(false);
    if (cmd.kind === 'nav') {
      navigate(cmd.to);
    } else if (cmd.kind === 'market') {
      setMarket(cmd.market);
    } else if (cmd.kind === 'theme') {
      setTheme(cmd.theme);
      message.success(t('commandPalette.themeSwitched', { label: cmd.themeLabel }));
    } else {
      navigate(`/kline?symbol=${encodeURIComponent(cmd.symbol)}`);
    }
  }, [navigate, setMarket, setTheme, t]);

  // 键盘：↑↓ 选择 / Enter 执行 / Esc 关闭
  const onInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIdx(i => (grouped.count === 0 ? 0 : (i + 1) % grouped.count));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIdx(i => (grouped.count === 0 ? 0 : (i - 1 + grouped.count) % grouped.count));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      run(commands[activeIdx]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
    }
  };

  // 选中项滚动到可视区
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${activeIdx}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [activeIdx, grouped]);

  return (
    <Modal
      open={open}
      onCancel={() => setOpen(false)}
      footer={null}
      closable={false}
      width={620}
      style={{ top: 110 }}
      styles={{ body: { padding: 0 } }}
      title={null}
      destroyOnHidden
    >
      <div style={{ padding: '14px 16px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
        <Input
          ref={inputRef}
          size="large"
          variant="borderless"
          placeholder={t('commandPalette.placeholder')}
          prefix={<SearchOutlined style={{ color: '#8B93A7', fontSize: 18 }} />}
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={onInputKeyDown}
        />
      </div>

      <div ref={listRef} style={{ maxHeight: 420, overflowY: 'auto', padding: '8px 0' }}>
        {searching && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 20px', color: '#8B93A7', fontSize: 13 }}>
            <Spin size="small" /> {t('commandPalette.searching')}
          </div>
        )}

        {grouped.rows.map(row =>
          row.type === 'header' ? (
            <div key={row.key} style={{
              padding: '8px 20px 4px', fontSize: 11, fontWeight: 600,
              color: '#8B93A7', letterSpacing: 1,
            }}>
              {row.label}
            </div>
          ) : (
            <div
              key={row.key}
              data-idx={row.flatIdx}
              onMouseEnter={() => setActiveIdx(row.flatIdx)}
              onClick={() => run(row.cmd)}
              style={{
                display: 'flex', alignItems: 'center', gap: 12,
                padding: '9px 20px', cursor: 'pointer', fontSize: 14,
                background: row.flatIdx === activeIdx ? 'rgba(91,108,240,0.18)' : 'transparent',
                borderRadius: 6, margin: '0 8px',
              }}
            >
              <span style={{ color: '#5B6CF0', width: 18, display: 'inline-flex', justifyContent: 'center' }}>
                {row.cmd.kind === 'nav'
                  ? <ExportOutlined />
                  : row.cmd.kind === 'market'
                    ? <SwapOutlined />
                    : row.cmd.kind === 'theme'
                      ? <BgColorsOutlined />
                      : <LineChartOutlined />}
              </span>
              <span style={{ fontWeight: 500, flexShrink: 0 }}>{row.cmd.label}</span>
              <span style={{
                color: '#8B93A7', fontSize: 12, overflow: 'hidden',
                textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {row.cmd.sub}
              </span>
              {row.flatIdx === activeIdx && (
                <kbd style={{
                  marginLeft: 'auto', fontSize: 11, color: '#8B93A7',
                  border: '1px solid rgba(255,255,255,0.15)', borderRadius: 4,
                  padding: '1px 6px', fontFamily: 'inherit', flexShrink: 0,
                }}>↵</kbd>
              )}
            </div>
          ),
        )}

        {!searching && grouped.count === 0 && (
          <div style={{ padding: '32px 20px', textAlign: 'center', color: '#8B93A7', fontSize: 13 }}>
            {query.trim().length < 2
              ? t('commandPalette.emptyHint')
              : t('commandPalette.emptyQuery', { query: query.trim() })}
          </div>
        )}
      </div>

      <div style={{
        display: 'flex', gap: 16, alignItems: 'center', padding: '10px 20px',
        borderTop: '1px solid rgba(255,255,255,0.08)', color: '#8B93A7', fontSize: 12,
      }}>
        <span><Kbd>↑</Kbd><Kbd>↓</Kbd> {t('commandPalette.footerSelect')}</span>
        <span><Kbd>↵</Kbd> {t('commandPalette.footerOpen')}</span>
        <span><Kbd>Esc</Kbd> {t('commandPalette.footerClose')}</span>
        <span style={{ marginLeft: 'auto' }}>{t('commandPalette.footerMarket')}{t('market.' + market, { ns: 'common' })}</span>
      </div>
    </Modal>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd style={{
      border: '1px solid rgba(255,255,255,0.15)', borderRadius: 4,
      padding: '1px 5px', fontSize: 11, marginRight: 4, fontFamily: 'inherit',
    }}>
      {children}
    </kbd>
  );
}
