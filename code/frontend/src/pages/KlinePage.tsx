import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router-dom';
import { api } from '../services/api';

interface Bar {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

const PERIODS = ['1m', '5m', '15m', '30m', '60m', 'day', 'week'] as const;

function shortTime(time: string): string {
  if (time.includes('T')) {
    const timePart = time.slice(11, 16);
    if (timePart === '00:00') return time.slice(5, 10); // MM-DD
    return timePart; // HH:MM（分钟K线）
  }
  return time.slice(5); // YYYY-MM-DD → MM-DD
}

function movingAverage(data: Bar[], window: number): (number | null)[] {
  return data.map((_, i) => {
    if (i < window - 1) return null;
    const slice = data.slice(i - window + 1, i + 1);
    return slice.reduce((sum, b) => sum + b.close, 0) / window;
  });
}

function calcEma(values: number[], window: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (values.length < window) return out;
  const k = 2 / (window + 1);
  let ema = values.slice(0, window).reduce((a, b) => a + b, 0) / window;
  out[window - 1] = ema;
  for (let i = window; i < values.length; i++) {
    ema = values[i] * k + ema * (1 - k);
    out[i] = ema;
  }
  return out;
}

function calcMacd(values: number[], fast = 12, slow = 26, signal = 9) {
  const ema12 = calcEma(values, fast);
  const ema26 = calcEma(values, slow);
  const macdLine: (number | null)[] = values.map((_, i) => {
    const f = ema12[i], s = ema26[i];
    return f != null && s != null ? f - s : null;
  });
  const validMacd = macdLine.filter((v): v is number => v != null);
  const signalEma = calcEma(validMacd, signal);
  let vi = 0;
  const signalLine: (number | null)[] = macdLine.map((v) => {
    if (v == null) return null;
    return signalEma[vi++] ?? null;
  });
  const histogram: (number | null)[] = macdLine.map((m, i) => {
    const s = signalLine[i];
    return m != null && s != null ? m - s : null;
  });
  return { macdLine, signalLine, histogram };
}

function calcBoll(data: Bar[], window = 20, k = 2) {
  const upper: (number | null)[] = [], middle: (number | null)[] = [], lower: (number | null)[] = [];
  for (let i = 0; i < data.length; i++) {
    if (i < window - 1) { upper.push(null); middle.push(null); lower.push(null); continue; }
    const slice = data.slice(i - window + 1, i + 1).map(b => b.close);
    const mid = slice.reduce((a, b) => a + b, 0) / window;
    const std = Math.sqrt(slice.reduce((a, b) => a + (b - mid) ** 2, 0) / window);
    upper.push(mid + k * std);
    middle.push(mid);
    lower.push(mid - k * std);
  }
  return { upper, middle, lower };
}

function linePath(values: (number | null)[], x: (i: number) => number, y: (v: number) => number): string {
  let d = '', started = false;
  values.forEach((v, i) => {
    if (v == null || isNaN(v as number)) { started = false; return; }
    d += `${started ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    started = true;
  });
  return d;
}

function CandleChart({ bars, showMA, showBoll, showMacd }: {
  bars: Bar[]; showMA: boolean; showBoll: boolean; showMacd: boolean;
}) {
  const { t } = useTranslation('pages');
  const W = 800;
  const priceH = 220;
  const volH = 50;
  const macdH = showMacd ? 80 : 0;
  const PAD = { top: 14, left: 56, right: 16, bottom: 20 };
  const volTop = PAD.top + priceH + 8;
  const macdTop = volTop + volH + 8;
  const H = macdTop + macdH + PAD.bottom;
  const [hover, setHover] = useState<number | null>(null);

  const data = bars.filter(b => [b.open, b.high, b.low, b.close].every(Number.isFinite));
  if (data.length === 0) return <div style={{ color: '#8B93A7', padding: 20 }}>{t('kline.empty')}</div>;

  const innerW = W - PAD.left - PAD.right;
  const slot = innerW / data.length;
  const bw = Math.max(1, Math.min(slot * 0.7, 18));
  const x = (i: number) => PAD.left + i * slot + slot / 2;

  const boll = showBoll ? calcBoll(data) : null;
  const bollValid = (vs: (number | null)[]) => vs.filter((v): v is number => v != null);
  const yMin = boll
    ? Math.min(...data.map(b => b.low), ...bollValid(boll.lower))
    : Math.min(...data.map(b => b.low));
  const yMax = boll
    ? Math.max(...data.map(b => b.high), ...bollValid(boll.upper))
    : Math.max(...data.map(b => b.high));
  const ySpan = yMax - yMin || 1;
  const y = (v: number) => PAD.top + (1 - (v - yMin) / ySpan) * priceH;

  const maxVol = Math.max(1, ...data.map(b => b.volume || 0));
  const volY = (v: number) => volTop + (1 - v / maxVol) * volH;

  const closes = data.map(b => b.close);
  const ma5 = showMA ? movingAverage(data, 5) : null;
  const ma10 = showMA ? movingAverage(data, 10) : null;

  const macdData = showMacd ? calcMacd(closes) : null;
  const macdVals = macdData
    ? [
        ...macdData.macdLine.filter((v): v is number => v != null),
        ...macdData.signalLine.filter((v): v is number => v != null),
      ]
    : [];
  const macdMin = macdVals.length ? Math.min(...macdVals) : -1;
  const macdMax = macdVals.length ? Math.max(...macdVals) : 1;
  const macdSpan = macdMax - macdMin || 1;
  const my = (v: number) => macdTop + (1 - (v - macdMin) / macdSpan) * macdH;
  const macdZero = my(0);

  const labelStep = Math.ceil(data.length / 8);
  const yLabels = [0, 0.25, 0.5, 0.75, 1].map(t => ({ v: yMin + t * ySpan, py: y(yMin + t * ySpan) }));

  let bollFill = '';
  if (boll) {
    boll.upper.forEach((u, i) => {
      if (u == null) return;
      bollFill += (i === 0 || boll.upper[i - 1] == null ? 'M' : 'L') + `${x(i).toFixed(1)},${y(u).toFixed(1)}`;
    });
    for (let i = data.length - 1; i >= 0; i--) {
      const l = boll.lower[i];
      if (l != null) bollFill += `L${x(i).toFixed(1)},${y(l).toFixed(1)}`;
    }
    if (bollFill) bollFill += 'Z';
  }

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      style={{ width: '100%', height: 'auto', cursor: 'crosshair', background: '#0F1117', borderRadius: 6 }}
      onMouseMove={e => {
        const rect = e.currentTarget.getBoundingClientRect();
        const mx = (e.clientX - rect.left) * (W / rect.width);
        setHover(Math.min(data.length - 1, Math.max(0, Math.floor((mx - PAD.left) / slot))));
      }}
      onMouseLeave={() => setHover(null)}
    >
      {yLabels.map(({ py }, i) => (
        <line key={i} x1={PAD.left} y1={py} x2={W - PAD.right} y2={py} stroke="#1A1F2E" />
      ))}
      {yLabels.map(({ v, py }) => (
        <text key={v} x={PAD.left - 4} y={py + 4} textAnchor="end" fontSize={9} fill="#4A5268">{v.toFixed(2)}</text>
      ))}

      {boll && bollFill && <path d={bollFill} fill="#5B6CF0" fillOpacity={0.07} />}
      {boll && (
        <>
          <path d={linePath(boll.upper, x, y)} fill="none" stroke="#5B6CF0" strokeWidth={1} strokeDasharray="3 3" />
          <path d={linePath(boll.middle, x, y)} fill="none" stroke="#8B93A7" strokeWidth={1} strokeDasharray="2 2" />
          <path d={linePath(boll.lower, x, y)} fill="none" stroke="#5B6CF0" strokeWidth={1} strokeDasharray="3 3" />
        </>
      )}

      {data.map((b, i) => {
        const up = b.close >= b.open;
        const c = up ? '#F0524D' : '#12A150';
        return (
          <g key={b.time}>
            <line x1={x(i)} y1={y(b.high)} x2={x(i)} y2={y(b.low)} stroke={c} />
            <rect x={x(i) - bw / 2} y={y(Math.max(b.open, b.close))} width={bw}
              height={Math.max(1, y(Math.min(b.open, b.close)) - y(Math.max(b.open, b.close)))} fill={c} />
          </g>
        );
      })}

      {ma5 && <path d={linePath(ma5, x, y)} fill="none" stroke="#5B6CF0" strokeWidth={1.5} />}
      {ma10 && <path d={linePath(ma10, x, y)} fill="none" stroke="#F6A609" strokeWidth={1.5} />}

      {data.map((b, i) => {
        const up = b.close >= b.open;
        return (
          <rect key={`v${i}`} x={x(i) - bw / 2} y={volY(b.volume || 0)} width={bw}
            height={Math.max(1, volTop + volH - volY(b.volume || 0))}
            fill={up ? '#F0524D' : '#12A150'} fillOpacity={0.5} />
        );
      })}

      {showMacd && macdData && (
        <>
          <line x1={PAD.left} y1={macdZero} x2={W - PAD.right} y2={macdZero} stroke="#2A2F3E" />
          {data.map((_, i) => {
            const h = macdData.histogram[i];
            if (h == null) return null;
            const c = h >= 0 ? '#F0524D' : '#12A150';
            const top = h >= 0 ? my(h) : macdZero;
            return <rect key={`mh${i}`} x={x(i) - bw / 2} y={top} width={bw}
              height={Math.max(1, Math.abs(my(h) - macdZero))} fill={c} fillOpacity={0.7} />;
          })}
          <path d={linePath(macdData.macdLine, x, my)} fill="none" stroke="#5B6CF0" strokeWidth={1.5} />
          <path d={linePath(macdData.signalLine, x, my)} fill="none" stroke="#F6A609" strokeWidth={1.5} />
          <text x={PAD.left + 4} y={macdTop + 12} fontSize={9} fill="#4A5268">MACD(12,26,9)</text>
        </>
      )}

      {data.map((b, i) =>
        i % labelStep === 0 ? (
          <text key={`t${i}`} x={x(i)} y={H - 4} textAnchor="middle" fontSize={9} fill="#4A5268">
            {shortTime(b.time)}
          </text>
        ) : null,
      )}

      {hover != null && data[hover] && (
        <>
          <line x1={x(hover)} y1={PAD.top} x2={x(hover)} y2={macdTop + macdH} stroke="#4A5268" strokeDasharray="4 4" />
          <text x={PAD.left + 4} y={PAD.top + 11} fontSize={10} fill="#C5CBD8">
            {`${data[hover].time.slice(0, 10)}  O:${data[hover].open.toFixed(2)}  H:${data[hover].high.toFixed(2)}  L:${data[hover].low.toFixed(2)}  C:${data[hover].close.toFixed(2)}`}
          </text>
        </>
      )}
    </svg>
  );
}

export default function KlinePage() {
  const { t } = useTranslation('pages');
  const [searchParams] = useSearchParams();
  const [symbol, setSymbol] = useState(searchParams.get('symbol') || '600519.SH');

  // URL 参数变化时同步标的（页面已挂载时再次跳转也能切换）
  useEffect(() => {
    const s = searchParams.get('symbol');
    if (s && s !== symbol) setSymbol(s);
  }, [searchParams]);
  const [period, setPeriod] = useState<(typeof PERIODS)[number]>('day');
  const [bars, setBars] = useState<Bar[]>([]);
  const [showMA, setShowMA] = useState(true);
  const [showBoll, setShowBoll] = useState(false);
  const [showMacd, setShowMacd] = useState(false);

  useEffect(() => {
    // CRYPTO ts_code 含斜杠（如 BTC/USDT），拼进 path 会被路由 404，须走 query 形态
    api<{ bars: Bar[] }>(`/market/kline?symbol=${encodeURIComponent(symbol)}&period=${period}&limit=200`)
      .then(d => setBars(d.bars))
      .catch(console.error);
  }, [symbol, period]);

  const btn = (active: boolean): React.CSSProperties => ({
    padding: '3px 10px', borderRadius: 4, border: 'none', cursor: 'pointer',
    fontSize: 12, marginLeft: 6,
    background: active ? '#5B6CF0' : '#1E2330',
    color: active ? '#fff' : '#8B93A7',
  });

  return (
    <div>
      <h1 style={{ fontSize: 18, marginBottom: 12 }}>{t('kline.title')}</h1>
      <div style={{ marginBottom: 10, display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 4 }}>
        <input value={symbol} onChange={e => setSymbol(e.target.value)}
          style={{ padding: '4px 8px', borderRadius: 4, background: '#1E2330', border: '1px solid #2A2F3E', color: '#C5CBD8', width: 120 }} />
        {PERIODS.map(p => (
          <button key={p} onClick={() => setPeriod(p)} style={btn(period === p)}>{p}</button>
        ))}
        <span style={{ marginLeft: 16, color: '#4A5268', fontSize: 12 }}>{t('kline.indicators')}</span>
        <button onClick={() => setShowMA(!showMA)} style={btn(showMA)}>MA</button>
        <button onClick={() => setShowBoll(!showBoll)} style={btn(showBoll)}>BOLL</button>
        <button onClick={() => setShowMacd(!showMacd)} style={btn(showMacd)}>MACD</button>
      </div>
      <CandleChart bars={bars} showMA={showMA} showBoll={showBoll} showMacd={showMacd} />
      <div style={{ marginTop: 6, fontSize: 11, color: '#4A5268', display: 'flex', gap: 16 }}>
        {showMA && <span><span style={{ color: '#5B6CF0' }}>■</span> MA5　<span style={{ color: '#F6A609' }}>■</span> MA10</span>}
        {showBoll && <span><span style={{ color: '#5B6CF0' }}>--</span> BOLL(20,2)</span>}
        {showMacd && <span><span style={{ color: '#5B6CF0' }}>—</span> MACD　<span style={{ color: '#F6A609' }}>—</span> {t('kline.signal')}</span>}
      </div>
    </div>
  );
}
