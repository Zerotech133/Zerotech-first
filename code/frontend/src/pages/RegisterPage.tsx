import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router-dom';

import { MARKET_ROUTES } from '../config/navigation';
import { useLang } from '../contexts/LanguageContext';
import { isLang } from '../i18n';
import { api } from '../services/api';
import '../styles/auth.css';

interface RegisterResp {
  token: string;
  username: string;
  role: string;
  locale: string;
  email?: string | null;
}

const MARKETS = MARKET_ROUTES.map((market) => market.toUpperCase());

export default function RegisterPage() {
  const { t } = useTranslation('pages');
  const { lang, setLang } = useLang();
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [message, setMessage] = useState('');
  const [err, setErr] = useState(false);
  const navigate = useNavigate();

  const submit = async () => {
    if (password !== confirm) {
      setErr(true);
      setMessage(t('register.mismatch'));
      return;
    }
    try {
      const data = await api<RegisterResp>(`/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, email, password }),
      });
      // 注册即登录
      localStorage.setItem('zhice_token', data.token);
      localStorage.setItem('zhice_role', data.role);
      if (isLang(data.locale)) setLang(data.locale);
      setErr(false);
      setMessage(t('register.success'));
      navigate('/cn/overview', { replace: true });
    } catch (e) {
      setErr(true);
      setMessage(t('register.fail', { msg: e instanceof Error ? e.message : String(e) }));
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
            <span className="auth-card-title">{t('register.title')}</span>
            <span className="auth-link" onClick={() => setLang(lang === 'zh' ? 'en' : 'zh')}>
              {lang === 'zh' ? 'EN' : '中'}
            </span>
          </div>

          <div className="auth-field">
            <label>{t('register.username')}</label>
            <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder={t('register.username')} />
          </div>
          <div className="auth-field">
            <label>{t('register.email')}</label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder={t('register.email')} />
          </div>
          <div className="auth-field">
            <label>{t('register.password')}</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder={t('register.password')} />
          </div>
          <div className="auth-field">
            <label>{t('register.confirmPassword')}</label>
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder={t('register.confirmPassword')}
              onKeyDown={(e) => e.key === 'Enter' && submit()}
            />
          </div>

          <button className="auth-btn" onClick={submit}>
            {t('register.submit')}
          </button>

          {message && <p className={`auth-msg ${err ? 'err' : 'ok'}`}>{message}</p>}

          <div className="auth-foot">
            <span>{t('register.haveAccount')}</span>
            <Link className="auth-link" to="/login">
              {t('register.toLogin')}
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
