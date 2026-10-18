/**
 * Task 15 UI 测试：UserNotificationPreferencesForm（四行事件 × 双渠道偏好矩阵）。
 *
 * 计划最小测试（语义一致；Task 15 起表单为 props 驱动——容器负责加载，表单
 * 不再自取数；null 渠道 = 未 active → 整列 disabled + 「请先绑定」）：
 * 1. renders four event rows and two independent channel columns
 *    （行：策略信号/成交提醒/晨报/晚报；columnheader：微信/Telegram）
 * 2. disables an unbound channel without changing the other（微信 null 整列
 *    disabled，Telegram 列不受影响）
 * 3. saves only the changed channel（点「微信 成交提醒」→ 恰
 *    patchMyNotificationPreferences('wxpusher', {fill_enabled:false})，
 *    telegram 不被调；服务端应答回写后开关翻转）
 * 另补：双渠道独立回显（矩阵勾选态 + 渠道级配置切换互不串扰）；静音/时区/语言
 * 经渠道选择器保存且仅 PATCH 所选渠道；en locale 矩阵无中文 fallback。
 *
 * 契约：矩阵开关即时保存、只提交所改渠道所改字段；静音/时区/语言为渠道级配置
 * （Segmented 选择渠道后整块表单随渠道回显）；市场报告 opt-in 区保留并说明
 * 同时约束晨/晚报；实例级 9 键表单不在本组件。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';

vi.mock('../services/notificationsApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/notificationsApi')>();
  return {
    ...actual,
    patchMyNotificationPreferences: vi.fn(),
  };
});

import '../i18n'; // 触发 i18n 初始化（默认 zh），否则 t() 返回 key 而非中文
import i18n from '../i18n';
import UserNotificationPreferencesForm from '../components/UserNotificationPreferencesForm';
import {
  patchMyNotificationPreferences,
  type NotificationChannel,
  type NotificationPreference,
  type NotificationPreferencePatch,
} from '../services/notificationsApi';

const mockPatch = vi.mocked(patchMyNotificationPreferences);

// ---------- 类型化样例（对齐 notificationsApi DTO） ----------

function pref(
  channel: NotificationChannel,
  over: Partial<NotificationPreference> = {},
): NotificationPreference {
  return {
    user_id: 'u-alice',
    channel,
    market_reports_enabled: { CN: true, US: false, HK: false, CRYPTO: false },
    signal_enabled: true,
    fill_enabled: true,
    morning_report_enabled: false,
    evening_report_enabled: false,
    quiet_hours: null,
    timezone: 'Asia/Shanghai',
    locale: 'zh',
    ...over,
  };
}

/** 微信渠道（已 active）：晨报开。 */
const activeWxPusher = () => pref('wxpusher', { morning_report_enabled: true });

/** Telegram 渠道（已 active）：信号关、US 市场开、柏林时区、英文推送、静音 23:00–07:00。 */
const activeTelegram = () =>
  pref('telegram', {
    signal_enabled: false,
    market_reports_enabled: { CN: false, US: true, HK: false, CRYPTO: false },
    quiet_hours: { start: '23:00', end: '07:00' },
    timezone: 'Europe/Berlin',
    locale: 'en',
  });

/** 有状态宿主：onSaved 用服务端应答回写渠道偏好 → 开关/表单回显服务端真相。 */
function renderPreferences(initial: {
  wxpusher: NotificationPreference | null;
  telegram: NotificationPreference | null;
}) {
  function Host() {
    const [channels, setChannels] = useState(initial);
    return (
      <UserNotificationPreferencesForm
        wxpusher={channels.wxpusher}
        telegram={channels.telegram}
        onSaved={(channel, p) => setChannels((s) => ({ ...s, [channel]: p }))}
      />
    );
  }
  return render(<Host />);
}

describe('UserNotificationPreferencesForm（四行双渠道矩阵）', () => {
  let user: ReturnType<typeof userEvent.setup>;

  beforeEach(() => {
    vi.clearAllMocks();
    user = userEvent.setup();
    // PATCH 通用应答：按渠道把 body 合并进当前偏好（模拟后端按键合并语义）
    mockPatch.mockImplementation(
      (channel?: NotificationChannel, body?: NotificationPreferencePatch) =>
        Promise.resolve(
          pref(channel ?? 'telegram', (body ?? {}) as Partial<NotificationPreference>),
        ),
    );
  });

  it('1. renders four event rows and two independent channel columns', () => {
    renderPreferences({ telegram: activeTelegram(), wxpusher: activeWxPusher() });
    for (const row of ['策略信号', '成交提醒', '晨报', '晚报']) {
      expect(screen.getByRole('row', { name: new RegExp(row) })).toBeInTheDocument();
    }
    expect(screen.getByRole('columnheader', { name: '微信' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Telegram' })).toBeInTheDocument();
  });

  it('2. disables an unbound channel without changing the other channel', () => {
    renderPreferences({ telegram: activeTelegram(), wxpusher: null });
    expect(screen.getByRole('switch', { name: '微信 策略信号' })).toBeDisabled();
    expect(screen.getByRole('switch', { name: '微信 成交提醒' })).toBeDisabled();
    expect(screen.getByRole('switch', { name: 'Telegram 策略信号' })).toBeEnabled();
    // 未 active 渠道整列提示「请先绑定」（矩阵 4 行各一处；渠道级配置区的
    // 「请先绑定」不计入——限定 table 作用域）
    expect(within(screen.getByRole('table')).getAllByText('请先绑定')).toHaveLength(4);
    expect(mockPatch).not.toHaveBeenCalled();
  });

  it('3. saves only the changed channel', async () => {
    renderPreferences({ telegram: activeTelegram(), wxpusher: activeWxPusher() });
    await user.click(screen.getByRole('switch', { name: '微信 成交提醒' }));

    expect(mockPatch).toHaveBeenCalledTimes(1);
    expect(mockPatch).toHaveBeenCalledWith('wxpusher', { fill_enabled: false });
    expect(mockPatch).not.toHaveBeenCalledWith('telegram', expect.anything());
    // 服务端应答回写后开关翻转为关（受控回显）；另一渠道不受影响
    await waitFor(() =>
      expect(screen.getByRole('switch', { name: '微信 成交提醒' })).not.toBeChecked(),
    );
    expect(screen.getByRole('switch', { name: 'Telegram 成交提醒' })).toBeChecked();
  });

  it('双渠道独立回显：矩阵勾选态与渠道级配置按渠道各自展示', async () => {
    renderPreferences({ telegram: activeTelegram(), wxpusher: activeWxPusher() });
    // 矩阵：微信信号开、Telegram 信号关（各自渠道的值）
    expect(screen.getByRole('switch', { name: '微信 策略信号' })).toBeChecked();
    expect(screen.getByRole('switch', { name: 'Telegram 策略信号' })).not.toBeChecked();
    // 渠道级配置默认微信：上海时区、无静音、CN 市场开
    expect(screen.getByLabelText('时区')).toHaveValue('Asia/Shanghai');
    expect(screen.getByLabelText('开始 (HH:MM)')).toHaveValue('');
    expect(screen.getByLabelText('中国市场报告')).toBeChecked();
    expect(screen.getByLabelText('美国市场报告')).not.toBeChecked();
    // 切到 Telegram：柏林时区 + 静音 23:00–07:00 + US 市场开（antd Segmented
    // 的隐藏 radio 带 pointer-events 样式，user-event 拒绝点击，用 fireEvent 直切）
    fireEvent.click(screen.getByRole('radio', { name: 'Telegram' }));
    expect(screen.getByLabelText('时区')).toHaveValue('Europe/Berlin');
    expect(screen.getByLabelText('开始 (HH:MM)')).toHaveValue('23:00');
    expect(screen.getByLabelText('结束 (HH:MM)')).toHaveValue('07:00');
    expect(screen.getByLabelText('中国市场报告')).not.toBeChecked();
    expect(screen.getByLabelText('美国市场报告')).toBeChecked();
    // 切回微信仍是上海（两渠道互不串扰）
    fireEvent.click(screen.getByRole('radio', { name: '微信' }));
    expect(screen.getByLabelText('时区')).toHaveValue('Asia/Shanghai');
  });

  it('静音/时区/语言仍可保存：仅 PATCH 所选渠道', async () => {
    renderPreferences({ telegram: activeTelegram(), wxpusher: activeWxPusher() });
    const tz = screen.getByLabelText('时区');
    await user.clear(tz);
    await user.type(tz, 'Asia/Tokyo');
    await user.type(screen.getByLabelText('开始 (HH:MM)'), '23:00');
    await user.type(screen.getByLabelText('结束 (HH:MM)'), '07:00');

    await user.click(screen.getByRole('button', { name: /保\s*存/ }));

    await waitFor(() => expect(mockPatch).toHaveBeenCalledTimes(1));
    const [channel, body] = mockPatch.mock.calls[0] as [
      NotificationChannel,
      NotificationPreferencePatch,
    ];
    expect(channel).toBe('wxpusher');
    expect(body).toMatchObject({
      timezone: 'Asia/Tokyo',
      quiet_hours: { start: '23:00', end: '07:00' },
      locale: 'zh',
    });
    // 市场开关全量提交（后端按键合并，全量集合幂等安全）
    expect(body.market_reports_enabled).toEqual({
      CN: true,
      US: false,
      HK: false,
      CRYPTO: false,
    });
    expect(mockPatch).not.toHaveBeenCalledWith('telegram', expect.anything());
  });

  it('静音留空不提交 quiet_hours（后端 None=不改，且不支持 PATCH 清空）', async () => {
    renderPreferences({ telegram: activeTelegram(), wxpusher: activeWxPusher() });

    await user.click(screen.getByRole('button', { name: /保\s*存/ }));

    await waitFor(() => expect(mockPatch).toHaveBeenCalledTimes(1));
    const [channel, body] = mockPatch.mock.calls[0] as [
      NotificationChannel,
      NotificationPreferencePatch,
    ];
    expect(channel).toBe('wxpusher');
    expect(body).not.toHaveProperty('quiet_hours');
  });

  it('静音校验：start==end 拒绝（不调 patch）；跨午夜 23:00–07:00 合法提交', async () => {
    renderPreferences({ telegram: activeTelegram(), wxpusher: activeWxPusher() });

    await user.type(screen.getByLabelText(/^开始/), '23:00');
    await user.type(screen.getByLabelText(/^结束/), '23:00');
    await user.click(screen.getByRole('button', { name: /保\s*存/ }));

    // 联合校验在开始/结束两个 Form.Item 上镜像渲染，同一文案出现两份
    await waitFor(() => {
      expect(
        document.querySelectorAll('.ant-form-item-explain-error').length,
      ).toBeGreaterThan(0);
    });
    expect(screen.getAllByText('开始与结束不能相同').length).toBeGreaterThan(0);
    expect(mockPatch).not.toHaveBeenCalled();

    // 改为 07:00（跨午夜合法）
    const end = screen.getByLabelText(/^结束/);
    await user.clear(end);
    await user.type(end, '07:00');
    await user.click(screen.getByRole('button', { name: /保\s*存/ }));

    await waitFor(() => expect(mockPatch).toHaveBeenCalledTimes(1));
    expect(mockPatch.mock.calls[0][1]).toEqual(
      expect.objectContaining({ quiet_hours: { start: '23:00', end: '07:00' } }),
    );
  });

  it('缺省市场键={} → 回显全关（fail-closed opt-in；Task 13 旧用例回填迁移）', () => {
    renderPreferences({
      telegram: activeTelegram(),
      wxpusher: pref('wxpusher', { market_reports_enabled: {} }),
    });
    for (const label of ['中国市场报告', '美国市场报告', '香港市场报告', '加密市场报告']) {
      expect(screen.getByLabelText(label)).not.toBeChecked();
    }
  });

  it('en locale：矩阵英文渲染，无中文 fallback', async () => {
    try {
      await i18n.changeLanguage('en');
      renderPreferences({ telegram: activeTelegram(), wxpusher: activeWxPusher() });
      expect(screen.getByRole('columnheader', { name: 'WeChat' })).toBeInTheDocument();
      expect(screen.getByRole('columnheader', { name: 'Telegram' })).toBeInTheDocument();
      for (const row of [
        'Strategy signals',
        'Fill notifications',
        'Morning report',
        'Evening report',
      ]) {
        expect(screen.getByRole('row', { name: new RegExp(row) })).toBeInTheDocument();
      }
      // accessible name = 渠道名 + 事件名（en 同样成立）
      expect(screen.getByRole('switch', { name: 'WeChat Strategy signals' })).toBeInTheDocument();
      expect(document.body.textContent).not.toContain('策略信号');
      expect(document.body.textContent).not.toContain('微信');
    } finally {
      await i18n.changeLanguage('zh');
    }
  });
});
