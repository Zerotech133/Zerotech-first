import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, Card, Row, Col, InputNumber, Button, Tag, message, Spin } from 'antd';
import { CheckCircleFilled } from '@ant-design/icons';
import { api } from '../services/api';
import { THEMES, useTheme } from '../themes/ThemeContext';
import { useRole } from '../hooks/useRole';

interface RiskParams {
  max_single_position_pct: number;
  max_total_position_pct: number;
  max_daily_loss_pct: number;
  stop_loss_pct: number;
}

interface SettingsState {
  trade_mode: 'manual' | 'semi_auto' | 'full_auto';
  risk_params: RiskParams;
}

/** 三档交易模式配色（V1 SettingsPage 语义；文案见 settings.modes.* / settings.modeTag.*） */
const MODE_KEYS: SettingsState['trade_mode'][] = ['manual', 'semi_auto', 'full_auto'];
const MODE_TAG_COLOR: Record<SettingsState['trade_mode'], string> = {
  manual: 'default',
  semi_auto: 'blue',
  full_auto: 'red',
};

const RISK_FIELD_KEYS: (keyof RiskParams)[] = [
  'max_single_position_pct',
  'max_total_position_pct',
  'max_daily_loss_pct',
  'stop_loss_pct',
];

/** 界面主题预览卡（Task 5）：主题名 + 4 色卡条（--bg-deep/--cyan/--violet-deep/--red）+ 当前选中描边 */
function ThemeCard() {
  const { t: tr } = useTranslation('pages');
  const { theme, setTheme } = useTheme();
  return (
    <Card size="small" title={tr('settings.themeTitle')} extra={<span style={{ fontSize: 12, color: '#8B93A7' }}>{tr('settings.themeHint')}</span>} style={{ marginBottom: 20 }}>
      <Row gutter={16}>
        {THEMES.map((t) => {
          const active = theme === t.key;
          return (
            <Col span={Math.floor(24 / THEMES.length)} key={t.key} style={{ minWidth: 150 }}>
              <div
                onClick={() => {
                  if (active) return;
                  setTheme(t.key);
                  message.success(tr('settings.themeSwitched', { name: t.label }));
                }}
                style={{
                  cursor: 'pointer',
                  border: active ? '2px solid #5B6CF0' : '1px solid #E5E7F0',
                  borderRadius: 10,
                  padding: '12px 14px',
                  background: '#FAFBFD',
                  position: 'relative',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                  <b style={{ fontSize: 14 }}>{t.label}</b>
                  <span style={{ fontSize: 11, color: '#8B93A7' }}>{t.desc}</span>
                  {active && <CheckCircleFilled style={{ color: '#5B6CF0', marginLeft: 'auto' }} />}
                </div>
                {/* 色卡条：bg-deep / cyan / violet-deep / red（取值与 themes/tokens.css 一致） */}
                <div style={{ display: 'flex', height: 22, borderRadius: 6, overflow: 'hidden', border: '1px solid rgba(0,0,0,.08)' }}>
                  {[t.colors.bgDeep, t.colors.cyan, t.colors.violetDeep, t.colors.red].map((c, i) => (
                    <span key={i} style={{ flex: 1, background: c }} />
                  ))}
                </div>
                <div style={{ marginTop: 6, fontSize: 10, color: '#8B93A7', fontFamily: 'monospace' }}>{t.key}</div>
              </div>
            </Col>
          );
        })}
      </Row>
    </Card>
  );
}

export default function SettingsPageV2() {
  const { t } = useTranslation('pages');
  const { canWrite } = useRole();  // VIEWER 只读：交易模式/风控参数只读，主题切换保留
  const MODES = MODE_KEYS.map((key) => ({
    key,
    label: t(`settings.modes.${key}.label`),
    desc: t(`settings.modes.${key}.desc`),
  }));
  const RISK_FIELDS = RISK_FIELD_KEYS.map((key) => ({
    key,
    label: t(`settings.riskFields.${key}.label`),
    hint: t(`settings.riskFields.${key}.hint`),
  }));
  const MODE_TAG: Record<SettingsState['trade_mode'], { color: string; text: string }> = {
    manual: { color: MODE_TAG_COLOR.manual, text: t('settings.modeTag.manual') },
    semi_auto: { color: MODE_TAG_COLOR.semi_auto, text: t('settings.modeTag.semi_auto') },
    full_auto: { color: MODE_TAG_COLOR.full_auto, text: t('settings.modeTag.full_auto') },
  };
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<SettingsState['trade_mode']>('semi_auto');
  const [params, setParams] = useState<RiskParams | null>(null);
  const [savingParams, setSavingParams] = useState(false);

  useEffect(() => {
    api<SettingsState>('/settings')
      .then((s) => {
        setMode(s.trade_mode);
        setParams(s.risk_params);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  async function switchMode(next: SettingsState['trade_mode']) {
    if (next === mode) return;
    try {
      const s = await api<SettingsState>('/settings/trade-mode', {
        method: 'PUT',
        body: JSON.stringify({ mode: next }),
      });
      setMode(s.trade_mode);
      message.success(t('settings.modeSwitched', { mode: MODES.find(m => m.key === next)?.label ?? next }));
    } catch {
      /* api 层已 toast 错误 */
    }
  }

  function updateParam(key: keyof RiskParams, value: number | null) {
    if (params && value !== null) {
      setParams({ ...params, [key]: value });
    }
  }

  async function saveParams() {
    if (!params) return;
    const invalid = RISK_FIELDS.find(f => {
      const v = params[f.key];
      return v == null || v < 0 || v > 100;
    });
    if (invalid) {
      message.warning(t('settings.rangeWarn', { field: invalid.label }));
      return;
    }
    setSavingParams(true);
    try {
      await api<SettingsState>('/settings/risk-params', {
        method: 'PUT',
        body: JSON.stringify(params),
      });
      message.success(t('settings.paramsSaved'));
    } catch {
      /* api 层已 toast 错误 */
    } finally {
      setSavingParams(false);
    }
  }

  if (loading) {
    return <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>;
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, margin: 0 }}>{t('settings.title')}</h1>
        <span style={{ color: '#8B93A7', fontSize: 13 }}>
          {t('settings.currentModePrefix')} <Tag color={MODE_TAG[mode].color}>{MODE_TAG[mode].text}</Tag> {t('settings.resetHint')}
        </span>
      </div>

      {/* 界面主题（Task 5：5 张预览卡） */}
      <ThemeCard />

      {/* 交易模式三档 */}
      <Card size="small" title={t('settings.modeTitle')} style={{ marginBottom: 20 }}>
        <Row gutter={16}>
          {MODES.map((m) => {
            const active = mode === m.key;
            return (
              <Col span={8} key={m.key}>
                <div
                  onClick={canWrite ? () => switchMode(m.key) : undefined}
                  style={{
                    cursor: canWrite ? 'pointer' : 'default',
                    border: active ? '2px solid #5B6CF0' : '1px solid #E5E7F0',
                    borderRadius: 10,
                    padding: '14px 16px',
                    background: active ? '#F5F6FE' : '#FAFBFD',
                    position: 'relative',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <b style={{ fontSize: 15 }}>{m.label}</b>
                    {m.key === 'semi_auto' && <Tag color="blue">{t('settings.recommended')}</Tag>}
                    {active && <CheckCircleFilled style={{ color: '#5B6CF0', marginLeft: 'auto' }} />}
                  </div>
                  <div style={{ fontSize: 13, color: '#4A5268', lineHeight: 1.6 }}>{m.desc}</div>
                </div>
              </Col>
            );
          })}
        </Row>
        <div style={{ marginTop: 12, padding: '8px 12px', background: '#FFFBE6', border: '1px solid #FFE58F', borderRadius: 8, color: '#614700', fontSize: 12 }}>
          {t('settings.modeWarning')}
        </div>
        {mode === 'full_auto' && (
          <div style={{ marginTop: 8, padding: '8px 12px', background: '#FFF1F0', border: '1px solid #FFA39E', borderRadius: 8, color: '#A8071A', fontSize: 13 }}>
            {t('settings.fullAutoWarning')}
          </div>
        )}
      </Card>

      {/* 风控参数四项 */}
      <Card
        size="small"
        title={t('settings.riskTitle')}
        extra={
          canWrite ? (
            <Button type="primary" size="small" loading={savingParams} onClick={saveParams}>
              {t('common:action.save')}
            </Button>
          ) : null
        }
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message={t('settings.riskDevTitle')}
          description={t('settings.riskDevDesc')}
        />
        <Row gutter={[16, 20]}>
          {RISK_FIELDS.map((f) => (
            <Col span={12} key={f.key}>
              <div style={{ marginBottom: 6 }}>
                <span style={{ fontSize: 13, fontWeight: 600 }}>{f.label}</span>
                <span style={{ fontSize: 12, color: '#8B93A7', marginLeft: 8 }}>{f.hint}</span>
              </div>
              <InputNumber
                style={{ width: 220 }}
                min={0}
                max={100}
                step={0.5}
                addonAfter="%"
                disabled={!canWrite}
                value={params?.[f.key] ?? 0}
                onChange={(v) => updateParam(f.key, v)}
                status={
                  params && (params[f.key] == null || params[f.key] < 0 || params[f.key] > 100)
                    ? 'error'
                    : undefined
                }
              />
            </Col>
          ))}
        </Row>
      </Card>
    </div>
  );
}
