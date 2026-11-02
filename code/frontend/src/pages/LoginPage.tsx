import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import { MARKET_ROUTES } from '../config/navigation';
import { useLang } from '../contexts/LanguageContext';
import { isLang } from '../i18n';
import { api } from '../services/api';
import '../styles/auth.css';

interface LoginResp {
  token: string;
  username: string;
  role: string;
  locale: string;
  email?: string | null;
}

const MARKETS = MARKET_ROUTES.map((market) => market.toUpperCase());

export default function LoginPage() {
  const { t } = useTranslation('pages');
  const { lang, setLang } = useLang();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');
  const [err, setErr] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  const login = async () => {
    try {
      const data = await api<LoginResp>(`/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      localStorage.setItem('zhice_token', data.token);
      localStorage.setItem('zhice_role', data.role);
      // 登录跟随账户的语言偏好（user.locale）
      if (isLang(data.locale)) setLang(data.locale);
      setErr(false);
      setMessage(t('login.success', { username: data.username, role: data.role }));
      const from = (location.state as { from?: string } | null)?.from ?? '/';
      navigate(from, { replace: true });
    } catch (e) {
      setErr(true);
      setMessage(t('login.fail', { msg: e instanceof Error ? e.message : String(e) }));
    }
  };

  return (
    <div className="auth-wrap">
      <div className="bg-grid" />
      <div className="bg-glow g1" />
      <div className="bg-glow g2" />
      <div className="bg-glow g3" />

      <section className="auth-brand">
        <div style={{ fontSize: 38, fontWeight: 700, letterSpacing: 4, color: '#e8c27a' }}>司马徽研究院</div>
        <div className="auth-brand-sub">
          {t('login.brandSub', { markets: MARKETS.map((m) => t('market.' + m, { ns: 'common' })).join(' · ') })}
        </div>
        <div className="auth-brand-markets">
          {MARKETS.map((m) => (
            <span className="auth-chip" key={m}>
              <i className="dot" />
              {t('market.' + m, { ns: 'common' })}
            </span>
          ))}
        </div>
      </section>

      <section className="auth-panel">
        <div className="auth-card">
          <div className="auth-card-head">
            <span className="auth-card-title">{t('login.title')}</span>
            <span className="auth-link" onClick={() => setLang(lang === 'zh' ? 'en' : 'zh')}>
              {lang === 'zh' ? 'EN' : '中'}
            </span>
          </div>

          <div className="auth-field">
            <label>{t('login.username')}</label>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder={t('login.username')}
            />
          </div>
          <div className="auth-field">
            <label>{t('login.password')}</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={t('login.password')}
              onKeyDown={(e) => e.key === 'Enter' && login()}
            />
          </div>

          <button className="auth-btn" onClick={login}>
            {t('login.title')}
          </button>

          {message && <p className={`auth-msg ${err ? 'err' : 'ok'}`}>{message}</p>}

          <div className="auth-foot">
            <span>{t('login.noAccount')}</span>
            <Link className="auth-link" to="/register">
              {t('login.toRegister')}
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
