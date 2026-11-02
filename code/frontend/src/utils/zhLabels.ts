/** 前端中文化统一词表：把策略/信号/理由/状态中的英文短码转为中文。 */

const TOKEN_MAP: Record<string, string> = {
  tech: '技术面',
  technical: '技术面',
  rps: '相对强度',
  env: '市场环境',
  environment: '市场环境',
  jev: '事件驱动',
  event: '事件驱动',
  chan: '缠论',
  chanlun: '缠论',
  trend: '趋势',
  momentum: '动量',
  mom: '动量',
  rsi: 'RSI',
  vol: '量能',
  volume: '量能',
  fund: '基本面',
  fundamental: '基本面',
  news: '新闻',
  sentiment: '情绪',
  qpzm: '青萍之末',
  dk: '量子双刀',
  vibe: 'Vibe',
  mean_reversion: '均值回归',
  meanreversion: '均值回归',
  breakout: '突破',
  pullback: '回踩',
  long: '做多',
  short: '做空',
  buy: '买入',
  sell: '卖出',
  hold: '持有',
  observe: '观察',
  avoid: '回避',
};

const STATUS_MAP: Record<string, string> = {
  submitted: '已提交',
  selected: '已入池',
  draft: '草稿',
  reviewed: '已复核',
  rejected: '已否决',
  pending: '待处理',
  claimed: '处理中',
  answered: '已回答',
  failed: '失败',
  success: '成功',
  active: '启用',
  ACTIVE: '启用',
  STAGING: '灰度',
  DRAFT: '草稿',
  PENDING_REVIEW: '待评审',
  REJECTED: '已否决',
  DISABLED: '已停用',
  shadow: '影子',
  live: '实盘',
  research: '研究态',
  running: '运行中',
  skipped: '已跳过',
  green: '正常',
  yellow: '降级',
  red: '异常',
  no_task: '未运行',
};

function translateToken(raw: string): string {
  const key = raw.trim();
  if (!key) return '';
  if (/[\u4e00-\u9fff]/.test(key)) return key;
  const mapKey = key.toLowerCase();
  return TOKEN_MAP[mapKey] || key;
}

/** 把 "tech+rps+env" 这类组合翻译为 "技术面 + 相对强度 + 市场环境" */
export function zhReason(value: unknown): string {
  if (value == null || value === '') return '—';
  return String(value)
    .split(/([+,，;；/|、])/)
    .map((part) => (/^[+,，;；/|、]$/.test(part) ? ' + ' : translateToken(part)))
    .join('')
    .replace(/(\s*\+\s*)+/g, ' + ')
    .trim();
}

/** 单个信号/状态短码翻译；已是中文则原样返回 */
export function zhStatus(value: unknown): string {
  if (value == null || value === '') return '—';
  return STATUS_MAP[String(value)] || String(value);
}

/** 信号类型翻译（支持 "1买"/"2买"/"3买" 等中文，英文映射） */
export function zhSignal(value: unknown): string {
  if (value == null || value === '') return '—';
  const s = String(value);
  return STATUS_MAP[s] || TOKEN_MAP[s.toLowerCase()] || s;
}
