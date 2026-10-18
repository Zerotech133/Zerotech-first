# zhice-OS V2 系统完善计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 根据设计文档补齐 V2 系统与方案文档之间的五个关键差距：CORS 修复、行情实时推送、MACD/BOLL 指标、K 线图指标展示、前端四工作区导航重构。

**Architecture:** FastAPI 后端 + APScheduler 定期 WebSocket broadcast；纯 Python 指标函数（无依赖）；React 前端 SVG K 线图；4 工作区导航（交易/资产/研究/系统）。

**Tech Stack:** Python 3.12, FastAPI, asyncio, React, TypeScript, SVG

---

## 文件清单

### 后端修改
- `src/zhice_os_v2/api/main.py` — 修复 CORS 端口、注册 WebSocket 推送后台任务
- `src/zhice_os_v2/domain/strategy/indicators.py` — 新增 `macd()` 和 `bollinger_bands()`
- `src/zhice_os_v2/api/routers/market.py` — 修复 `_db_quotes` pct_change、新增 `/market/indicators/{ts_code}` 端点

### 前端修改
- `frontend/src/App.tsx` — 四工作区导航重构
- `frontend/src/styles.css` — 添加工作区导航样式
- `frontend/src/pages/KlinePage.tsx` — 新增 MACD/BOLL 图层、指标开关

### 测试
- `tests/test_indicators.py` — 新增 MACD/BOLL 单元测试 + API 测试
- `tests/test_realtime_push.py` — WebSocket broadcast 测试

---

## Task 1：修复 CORS + 端口配置

**Files:**
- Modify: `src/zhice_os_v2/api/main.py`

- [ ] **Step 1：确认当前 CORS 配置**

```bash
grep -n "allow_origins" src/zhice_os_v2/api/main.py
```

Expected output:
```
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
```

- [ ] **Step 2：扩展 CORS 允许来源**

在 `src/zhice_os_v2/api/main.py` 中找到：

```python
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
```

替换为：

```python
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost:3000",
    ],
```

- [ ] **Step 3：运行现有测试验证无破坏**

```bash
.venv/bin/python -m pytest tests/test_api.py -v
```

Expected: All tests PASS

- [ ] **Step 4：提交**

```bash
git add src/zhice_os_v2/api/main.py
git commit -m "fix: allow port 5174 in CORS origins"
```

---

## Task 2：行情 pct_change 修复

**Files:**
- Modify: `src/zhice_os_v2/api/routers/market.py`

当前 `_db_quotes()` 函数只查最近 1 根日K，无法计算涨跌幅。

- [ ] **Step 1：确认问题**

```bash
grep -n "pre_close\|pct_change" src/zhice_os_v2/api/routers/market.py | head -20
```

Expected: `pre_close: None` 在 `_db_quotes` 函数中

- [ ] **Step 2：修复 `_db_quotes` 函数**

找到 `_db_quotes` 函数，将其中查询部分替换为（查最近 2 根K线来计算涨跌幅）：

```python
def _db_quotes(limit: int = 100) -> list[dict]:
    try:
        session = get_session()
        instruments = session.query(
            orm.InstrumentModel.symbol,
            orm.InstrumentModel.name,
        ).limit(limit).all()
        out = []
        for symbol, name in instruments:
            bars = session.query(
                orm.BarDailyModel.close,
                orm.BarDailyModel.trade_date,
            ).filter(
                orm.BarDailyModel.symbol == symbol
            ).order_by(
                orm.BarDailyModel.trade_date.desc()
            ).limit(2).all()
            if not bars:
                continue
            price = float(bars[0][0] or 0)
            pre_close = float(bars[1][0] or 0) if len(bars) > 1 else 0.0
            pct_change = round((price - pre_close) / pre_close * 100, 2) if pre_close else None
            out.append({
                "ts_code": symbol,
                "symbol": symbol,
                "name": name or "",
                "price": price,
                "pre_close": pre_close,
                "pct_change": pct_change,
                "volume": 0,
                "turnover": 0,
                "snapshot_time": bars[0][1].isoformat(),
            })
        session.close()
        return out
    except Exception:
        return []
```

- [ ] **Step 3：运行测试**

```bash
.venv/bin/python -m pytest tests/test_api.py -v
```

Expected: PASS

- [ ] **Step 4：提交**

```bash
git add src/zhice_os_v2/api/routers/market.py
git commit -m "fix: compute pct_change in market quotes from last 2 daily bars"
```

---

## Task 3：MACD 指标（后端）

**Files:**
- Create: `tests/test_indicators.py`
- Modify: `src/zhice_os_v2/domain/strategy/indicators.py`

MACD = EMA12 - EMA26；Signal = EMA9(MACD)；Histogram = MACD - Signal

- [ ] **Step 1：写失败测试**

创建 `tests/test_indicators.py`：

```python
"""技术指标单元测试。"""
from zhice_os_v2.domain.strategy.indicators import macd, bollinger_bands


def test_macd_requires_enough_data():
    closes = [float(i) for i in range(40)]
    result = macd(closes)
    assert result is not None
    assert "macd" in result
    assert "signal" in result
    assert "histogram" in result


def test_macd_returns_none_for_short_data():
    closes = [1.0, 2.0, 3.0]
    result = macd(closes)
    assert result is None


def test_macd_values_are_floats():
    closes = [100.0 + i * 0.5 for i in range(40)]
    result = macd(closes)
    assert result is not None
    assert isinstance(result["macd"], float)
    assert isinstance(result["signal"], float)
    assert isinstance(result["histogram"], float)


def test_macd_series_requires_enough_data():
    closes = [float(i) for i in range(50)]
    result = macd(closes, return_series=True)
    assert result is not None
    assert "macd_series" in result
    assert "signal_series" in result
    assert "histogram_series" in result
    assert len(result["macd_series"]) == len(closes)
```

- [ ] **Step 2：运行测试（预期失败）**

```bash
.venv/bin/python -m pytest tests/test_indicators.py::test_macd_requires_enough_data -v
```

Expected: FAIL with `ImportError: cannot import name 'macd'`

- [ ] **Step 3：实现 EMA 和 MACD**

在 `src/zhice_os_v2/domain/strategy/indicators.py` 末尾追加：

```python
def ema(closes: list[float], window: int) -> list[float]:
    """指数移动平均，返回与输入等长的序列（前 window-1 个为 NaN）。"""
    if len(closes) < window:
        return [float("nan")] * len(closes)
    result: list[float] = []
    k = 2.0 / (window + 1)
    sma = sum(closes[:window]) / window
    result.append(sma)
    for price in closes[window:]:
        result.append(price * k + result[-1] * (1 - k))
    return [float("nan")] * (window - 1) + result


def macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    return_series: bool = False,
):
    """MACD 指标。return_series=False 返回最新点 dict，True 返回全序列。"""
    if len(closes) < slow + signal:
        return None

    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)

    offset = slow - 1
    macd_line: list[float] = []
    for i in range(offset, len(closes)):
        ef, es = ema_fast[i], ema_slow[i]
        macd_line.append(float("nan") if (ef != ef or es != es) else ef - es)

    valid_macd = [v for v in macd_line if v == v]
    if len(valid_macd) < signal:
        return None
    signal_ema = ema(valid_macd, signal)
    nan_prefix = len(macd_line) - len(valid_macd)
    sig_padded = [float("nan")] * nan_prefix + signal_ema

    histogram_line = [
        float("nan") if (m != m or s != s) else m - s
        for m, s in zip(macd_line, sig_padded)
    ]

    if return_series:
        full_nan = [float("nan")] * offset
        return {
            "macd_series": full_nan + macd_line,
            "signal_series": full_nan + sig_padded,
            "histogram_series": full_nan + histogram_line,
        }

    def _last_valid(seq: list[float]) -> float | None:
        for v in reversed(seq):
            if v == v:
                return v
        return None

    mv, sv, hv = _last_valid(macd_line), _last_valid(sig_padded), _last_valid(histogram_line)
    if mv is None or sv is None or hv is None:
        return None
    return {"macd": mv, "signal": sv, "histogram": hv}
```

- [ ] **Step 4：运行 MACD 测试**

```bash
.venv/bin/python -m pytest tests/test_indicators.py::test_macd_requires_enough_data tests/test_indicators.py::test_macd_returns_none_for_short_data tests/test_indicators.py::test_macd_values_are_floats tests/test_indicators.py::test_macd_series_requires_enough_data -v
```

Expected: All 4 PASS

- [ ] **Step 5：提交**

```bash
git add src/zhice_os_v2/domain/strategy/indicators.py tests/test_indicators.py
git commit -m "feat: add MACD indicator with EMA and series support"
```

---

## Task 4：布林带指标（后端）

**Files:**
- Modify: `tests/test_indicators.py`
- Modify: `src/zhice_os_v2/domain/strategy/indicators.py`

布林带 = 中轨 MA(N) ± K * std(N)

- [ ] **Step 1：在 `tests/test_indicators.py` 末尾追加布林带测试**

```python
def test_bollinger_bands_returns_three_lines():
    closes = [100.0 + (i % 5) for i in range(30)]
    result = bollinger_bands(closes)
    assert result is not None
    assert "upper" in result
    assert "middle" in result
    assert "lower" in result


def test_bollinger_bands_returns_none_for_short_data():
    result = bollinger_bands([1.0, 2.0])
    assert result is None


def test_bollinger_upper_above_lower():
    closes = [100.0 + (i % 7) * 0.3 for i in range(30)]
    result = bollinger_bands(closes)
    assert result is not None
    assert result["upper"] > result["lower"]
    assert result["upper"] > result["middle"] > result["lower"]


def test_bollinger_series():
    closes = [100.0 + (i % 5) * 0.5 for i in range(40)]
    result = bollinger_bands(closes, return_series=True)
    assert result is not None
    assert len(result["upper_series"]) == len(closes)
    assert len(result["middle_series"]) == len(closes)
    assert len(result["lower_series"]) == len(closes)
```

- [ ] **Step 2：运行测试（预期失败）**

```bash
.venv/bin/python -m pytest tests/test_indicators.py::test_bollinger_bands_returns_three_lines -v
```

Expected: FAIL with `ImportError: cannot import name 'bollinger_bands'`

- [ ] **Step 3：实现布林带**

在 `src/zhice_os_v2/domain/strategy/indicators.py` 末尾追加：

```python
def bollinger_bands(
    closes: list[float],
    window: int = 20,
    k: float = 2.0,
    return_series: bool = False,
):
    """布林带。middle = MA(window)，upper/lower = middle ± k * std(window)。"""
    import math as _math

    if len(closes) < window:
        return None

    if return_series:
        upper_s, middle_s, lower_s = [], [], []
        for i in range(len(closes)):
            if i < window - 1:
                upper_s.append(float("nan"))
                middle_s.append(float("nan"))
                lower_s.append(float("nan"))
            else:
                w = closes[i - window + 1 : i + 1]
                mid = sum(w) / window
                std = _math.sqrt(sum((v - mid) ** 2 for v in w) / window)
                upper_s.append(mid + k * std)
                middle_s.append(mid)
                lower_s.append(mid - k * std)
        return {"upper_series": upper_s, "middle_series": middle_s, "lower_series": lower_s}

    w = closes[-window:]
    mid = sum(w) / window
    std = _math.sqrt(sum((v - mid) ** 2 for v in w) / window)
    return {"upper": mid + k * std, "middle": mid, "lower": mid - k * std}
```

- [ ] **Step 4：运行全部指标测试**

```bash
.venv/bin/python -m pytest tests/test_indicators.py -v
```

Expected: All 8 tests PASS

- [ ] **Step 5：提交**

```bash
git add src/zhice_os_v2/domain/strategy/indicators.py tests/test_indicators.py
git commit -m "feat: add Bollinger Bands indicator with series support"
```

---

## Task 5：指标 API 端点

**Files:**
- Modify: `src/zhice_os_v2/api/routers/market.py`
- Modify: `tests/test_indicators.py`

新增 `GET /market/indicators/{ts_code}` 接口，返回最近一组 MACD 和布林带数值。

- [ ] **Step 1：在 `tests/test_indicators.py` 顶部添加 API 测试导入**

在文件最顶部（`from zhice_os_v2...` 行之前）追加：

```python
from fastapi.testclient import TestClient
from zhice_os_v2.api.main import app

client = TestClient(app)
```

在文件末尾追加 API 测试：

```python
def test_indicators_api_returns_structure():
    resp = client.get("/api/v1/market/indicators/600519.SH?period=day")
    assert resp.status_code == 200
    data = resp.json()
    assert "ts_code" in data
    assert "macd" in data
    assert "bollinger" in data


def test_indicators_api_macd_keys_when_present():
    resp = client.get("/api/v1/market/indicators/600519.SH?period=day")
    assert resp.status_code == 200
    macd_data = resp.json()["macd"]
    if macd_data is not None:
        assert "macd" in macd_data
        assert "signal" in macd_data
        assert "histogram" in macd_data
```

- [ ] **Step 2：运行测试（预期 404）**

```bash
.venv/bin/python -m pytest tests/test_indicators.py::test_indicators_api_returns_structure -v
```

Expected: FAIL with 404

- [ ] **Step 3：在 market.py 末尾（WebSocket 之前）添加端点**

```python
@router.get("/indicators/{ts_code}")
def get_indicators(
    ts_code: str,
    period: str = Query("day", pattern="^(day|week|1m|5m|15m|30m|60m)$"),
    limit: int = Query(120, ge=50, le=500),
):
    """返回最新一组 MACD 和布林带数值。"""
    from zhice_os_v2.domain.strategy.indicators import macd as calc_macd, bollinger_bands as calc_boll
    from zhice_os_v2.infrastructure.market_data.kline import fetch_db_bars, fetch_db_minute_bars

    if period in ("1m", "5m", "15m", "30m", "60m"):
        bars_raw = fetch_db_minute_bars(ts_code, period, limit=limit)
    else:
        bars_raw = fetch_db_bars(ts_code, period, limit=limit)

    closes: list[float] = []
    if bars_raw:
        closes = [float(b["close"]) for b in bars_raw if b.get("close") is not None]

    return {
        "ts_code": ts_code,
        "period": period,
        "bar_count": len(closes),
        "macd": calc_macd(closes) if len(closes) >= 35 else None,
        "bollinger": calc_boll(closes) if len(closes) >= 20 else None,
    }
```

- [ ] **Step 4：运行全部指标测试**

```bash
.venv/bin/python -m pytest tests/test_indicators.py -v
```

Expected: All 10 tests PASS

- [ ] **Step 5：提交**

```bash
git add src/zhice_os_v2/api/routers/market.py tests/test_indicators.py
git commit -m "feat: add /market/indicators/{ts_code} API endpoint"
```

---

## Task 6：K 线图加 MACD/BOLL 图层（前端）

**Files:**
- Modify: `frontend/src/pages/KlinePage.tsx`

在现有 SVG K 线图中：
- 布林带上下轨叠加在价格图区（半透明填充）
- MACD 独立子图（柱状 histogram + macd/signal 线）
- 三个开关按钮：MA / BOLL / MACD

- [ ] **Step 1：确认当前文件行数**

```bash
wc -l frontend/src/pages/KlinePage.tsx
```

Expected: ~120 行

- [ ] **Step 2：完整替换 KlinePage.tsx**

将 `frontend/src/pages/KlinePage.tsx` 全部内容替换为：

```tsx
import { useEffect, useState } from 'react';
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
  if (time.includes('T')) return time.slice(11, 16);
  return time.slice(5);
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
  if (data.length === 0) return <div style={{ color: '#8B93A7', padding: 20 }}>暂无K线数据</div>;

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

  // BOLL 填充路径
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
      {/* 网格 */}
      {yLabels.map(({ py }, i) => (
        <line key={i} x1={PAD.left} y1={py} x2={W - PAD.right} y2={py} stroke="#1A1F2E" />
      ))}
      {yLabels.map(({ v, py }) => (
        <text key={v} x={PAD.left - 4} y={py + 4} textAnchor="end" fontSize={9} fill="#4A5268">{v.toFixed(2)}</text>
      ))}

      {/* BOLL 填充 */}
      {boll && bollFill && <path d={bollFill} fill="#5B6CF0" fillOpacity={0.07} />}
      {boll && (
        <>
          <path d={linePath(boll.upper, x, y)} fill="none" stroke="#5B6CF0" strokeWidth={1} strokeDasharray="3 3" />
          <path d={linePath(boll.middle, x, y)} fill="none" stroke="#8B93A7" strokeWidth={1} strokeDasharray="2 2" />
          <path d={linePath(boll.lower, x, y)} fill="none" stroke="#5B6CF0" strokeWidth={1} strokeDasharray="3 3" />
        </>
      )}

      {/* 蜡烛 */}
      {data.map((b, i) => {
        const up = b.close >= b.open;
        const c = up ? '#12A150' : '#F0524D';
        return (
          <g key={b.time}>
            <line x1={x(i)} y1={y(b.high)} x2={x(i)} y2={y(b.low)} stroke={c} />
            <rect x={x(i) - bw / 2} y={y(Math.max(b.open, b.close))} width={bw}
              height={Math.max(1, y(Math.min(b.open, b.close)) - y(Math.max(b.open, b.close)))} fill={c} />
          </g>
        );
      })}

      {/* MA 线 */}
      {ma5 && <path d={linePath(ma5, x, y)} fill="none" stroke="#5B6CF0" strokeWidth={1.5} />}
      {ma10 && <path d={linePath(ma10, x, y)} fill="none" stroke="#F6A609" strokeWidth={1.5} />}

      {/* 成交量 */}
      {data.map((b, i) => {
        const up = b.close >= b.open;
        return (
          <rect key={`v${i}`} x={x(i) - bw / 2} y={volY(b.volume || 0)} width={bw}
            height={Math.max(1, volTop + volH - volY(b.volume || 0))}
            fill={up ? '#12A150' : '#F0524D'} fillOpacity={0.5} />
        );
      })}

      {/* MACD 子图 */}
      {showMacd && macdData && (
        <>
          <line x1={PAD.left} y1={macdZero} x2={W - PAD.right} y2={macdZero} stroke="#2A2F3E" />
          {data.map((_, i) => {
            const h = macdData.histogram[i];
            if (h == null) return null;
            const c = h >= 0 ? '#12A150' : '#F0524D';
            const top = h >= 0 ? my(h) : macdZero;
            return <rect key={`mh${i}`} x={x(i) - bw / 2} y={top} width={bw}
              height={Math.max(1, Math.abs(my(h) - macdZero))} fill={c} fillOpacity={0.7} />;
          })}
          <path d={linePath(macdData.macdLine, x, my)} fill="none" stroke="#5B6CF0" strokeWidth={1.5} />
          <path d={linePath(macdData.signalLine, x, my)} fill="none" stroke="#F6A609" strokeWidth={1.5} />
          <text x={PAD.left + 4} y={macdTop + 12} fontSize={9} fill="#4A5268">MACD(12,26,9)</text>
        </>
      )}

      {/* X 轴标签 */}
      {data.map((b, i) =>
        i % labelStep === 0 ? (
          <text key={`t${i}`} x={x(i)} y={H - 4} textAnchor="middle" fontSize={9} fill="#4A5268">
            {shortTime(b.time)}
          </text>
        ) : null,
      )}

      {/* 十字光标 */}
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
  const [symbol, setSymbol] = useState('600519.SH');
  const [period, setPeriod] = useState<(typeof PERIODS)[number]>('day');
  const [bars, setBars] = useState<Bar[]>([]);
  const [showMA, setShowMA] = useState(true);
  const [showBoll, setShowBoll] = useState(false);
  const [showMacd, setShowMacd] = useState(false);

  useEffect(() => {
    api<{ bars: Bar[] }>(`/market/kline/${encodeURIComponent(symbol)}?period=${period}&limit=200`)
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
      <h1 style={{ fontSize: 18, marginBottom: 12 }}>K线</h1>
      <div style={{ marginBottom: 10, display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 4 }}>
        <input value={symbol} onChange={e => setSymbol(e.target.value)}
          style={{ padding: '4px 8px', borderRadius: 4, background: '#1E2330', border: '1px solid #2A2F3E', color: '#C5CBD8', width: 120 }} />
        {PERIODS.map(p => (
          <button key={p} onClick={() => setPeriod(p)} style={btn(period === p)}>{p}</button>
        ))}
        <span style={{ marginLeft: 16, color: '#4A5268', fontSize: 12 }}>指标:</span>
        <button onClick={() => setShowMA(!showMA)} style={btn(showMA)}>MA</button>
        <button onClick={() => setShowBoll(!showBoll)} style={btn(showBoll)}>BOLL</button>
        <button onClick={() => setShowMacd(!showMacd)} style={btn(showMacd)}>MACD</button>
      </div>
      <CandleChart bars={bars} showMA={showMA} showBoll={showBoll} showMacd={showMacd} />
      <div style={{ marginTop: 6, fontSize: 11, color: '#4A5268', display: 'flex', gap: 16 }}>
        {showMA && <span><span style={{ color: '#5B6CF0' }}>■</span> MA5　<span style={{ color: '#F6A609' }}>■</span> MA10</span>}
        {showBoll && <span><span style={{ color: '#5B6CF0' }}>--</span> BOLL(20,2)</span>}
        {showMacd && <span><span style={{ color: '#5B6CF0' }}>—</span> MACD　<span style={{ color: '#F6A609' }}>—</span> Signal</span>}
      </div>
    </div>
  );
}
```

- [ ] **Step 3：构建验证**

```bash
cd /Users/join/Desktop/zhice/zhice-os-v2/frontend && npm run build 2>&1 | tail -15
```

Expected: Build succeeds，无 TypeScript 错误

- [ ] **Step 4：提交**

```bash
git add frontend/src/pages/KlinePage.tsx
git commit -m "feat: add MACD/BOLL chart layers with indicator toggle to KlinePage"
```

---

## Task 7：WebSocket 定期推送

**Files:**
- Modify: `src/zhice_os_v2/api/main.py`
- Create: `tests/test_realtime_push.py`

- [ ] **Step 1：写 WebSocket broadcast 测试**

创建 `tests/test_realtime_push.py`：

```python
"""WebSocket broadcast 测试。"""
import asyncio
import pytest
from zhice_os_v2.infrastructure.realtime.manager import ConnectionManager


@pytest.mark.asyncio
async def test_broadcast_sends_to_all_connections():
    manager = ConnectionManager()
    received = []

    class FakeWS:
        async def accept(self): pass
        async def send_json(self, msg): received.append(msg)

    ws1, ws2 = FakeWS(), FakeWS()
    await manager.connect(ws1)
    await manager.connect(ws2)
    await manager.broadcast({"type": "test", "value": 42})
    assert len(received) == 2
    assert received[0] == {"type": "test", "value": 42}


@pytest.mark.asyncio
async def test_broadcast_removes_dead_connections():
    manager = ConnectionManager()
    received = []

    class OkWS:
        async def accept(self): pass
        async def send_json(self, msg): received.append(msg)

    class DeadWS:
        async def accept(self): pass
        async def send_json(self, msg): raise RuntimeError("connection closed")

    ok, dead = OkWS(), DeadWS()
    await manager.connect(ok)
    await manager.connect(dead)
    await manager.broadcast({"type": "ping"})
    assert len(received) == 1
    assert len(manager._connections) == 1
```

- [ ] **Step 2：安装 pytest-asyncio（如未安装）**

```bash
.venv/bin/pip install pytest-asyncio -q && echo "OK"
```

在 `tests/` 目录下新建 `conftest.py`（如不存在）：

```python
import pytest

pytest_plugins = ['pytest_asyncio']
```

并在项目根目录的 `pyproject.toml` 或 `pytest.ini` 中确保有：

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 3：运行 WS 测试（预期通过——manager 逻辑已正确）**

```bash
.venv/bin/python -m pytest tests/test_realtime_push.py -v
```

Expected: Both tests PASS

- [ ] **Step 4：在 main.py 中添加定期推送后台 Task**

修改 `src/zhice_os_v2/api/main.py`，在文件顶部 import 区末尾添加：

```python
import asyncio
```

将 `lifespan` 函数替换为：

```python
@asynccontextmanager
async def lifespan(_: FastAPI):
    seed_demo_data(get_market_data())

    from zhice_os_v2.infrastructure.realtime.manager import manager as ws_manager
    from zhice_os_v2.api.routers.market import _db_quotes

    async def _push_quotes():
        while True:
            await asyncio.sleep(10)
            if not ws_manager._connections:
                continue
            quotes = _db_quotes(limit=100)
            if quotes:
                await ws_manager.broadcast({"type": "quotes", "data": quotes})

    task = asyncio.create_task(_push_quotes())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 5：运行全部测试**

```bash
.venv/bin/python -m pytest -v 2>&1 | tail -20
```

Expected: ≥55 tests PASS（原有 53 + 2 WS 新增）

- [ ] **Step 6：提交**

```bash
git add src/zhice_os_v2/api/main.py tests/test_realtime_push.py
git commit -m "feat: periodic WebSocket market broadcast every 10s via lifespan task"
```

---

## Task 8：前端四工作区导航重构

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1：确认当前 Link 数量**

```bash
grep -c "Link to=" frontend/src/App.tsx
```

Expected: ≥ 20

- [ ] **Step 2：完整替换 App.tsx**

将 `frontend/src/App.tsx` 全部内容替换为：

```tsx
import { useState } from 'react';
import { Link, Route, Routes, useLocation } from 'react-router-dom';
import ProtectedRoute from './components/ProtectedRoute';
import MarketPage from './pages/MarketPage';
import StrategiesPage from './pages/StrategiesPage';
import ApprovalsPage from './pages/ApprovalsPage';
import PositionsPage from './pages/PositionsPage';
import ReplayPage from './pages/ReplayPage';
import KlinePage from './pages/KlinePage';
import LoginPage from './pages/LoginPage';
import ResearchPage from './pages/ResearchPage';
import LearningReportPage from './pages/LearningReportPage';
import SystemStatusPage from './pages/SystemStatusPage';
import AlertsPage from './pages/AlertsPage';
import PortfolioPage from './pages/PortfolioPage';
import AccountsPage from './pages/AccountsPage';
import DailyReportPage from './pages/DailyReportPage';
import KnowledgePage from './pages/KnowledgePage';
import AutomationPage from './pages/AutomationPage';
import BacktestPage from './pages/BacktestPage';
import WorkflowPage from './pages/WorkflowPage';
import StrategyPerformancePage from './pages/StrategyPerformancePage';
import RegimePage from './pages/RegimePage';
import MonitorPage from './pages/MonitorPage';
import MarketOverviewPage from './pages/MarketOverviewPage';
import ExportsPage from './pages/ExportsPage';
import CalendarPage from './pages/CalendarPage';
import FundamentalsPage from './pages/FundamentalsPage';

const WORKSPACES = [
  {
    key: 'trade',
    label: '交易',
    links: [
      { to: '/market', label: '行情' },
      { to: '/kline', label: 'K线' },
      { to: '/market-overview', label: '市场概览' },
      { to: '/approvals', label: '授权中心' },
      { to: '/positions', label: '持仓' },
      { to: '/workflow', label: '工作流' },
    ],
  },
  {
    key: 'portfolio',
    label: '资产',
    links: [
      { to: '/portfolio', label: '组合总览' },
      { to: '/accounts', label: '账户' },
      { to: '/replay', label: '复盘' },
      { to: '/daily-report', label: '日报' },
      { to: '/exports', label: '导出' },
    ],
  },
  {
    key: 'research',
    label: '研究',
    links: [
      { to: '/strategies', label: '策略管理' },
      { to: '/backtest', label: '回测' },
      { to: '/research', label: 'AI 研究助手' },
      { to: '/strategy-performance', label: '策略绩效' },
      { to: '/regime', label: '市场状态' },
      { to: '/fundamentals', label: '基本面' },
      { to: '/learning-report', label: '学习报告' },
      { to: '/knowledge', label: '知识库' },
    ],
  },
  {
    key: 'system',
    label: '系统',
    links: [
      { to: '/system-status', label: '系统状态' },
      { to: '/monitor', label: '监控' },
      { to: '/alerts', label: '告警中心' },
      { to: '/automation', label: '自动化' },
      { to: '/calendar', label: '交易日历' },
      { to: '/login', label: '登录' },
    ],
  },
] as const;

function NavBar() {
  const [open, setOpen] = useState<string | null>(null);
  const location = useLocation();

  return (
    <nav className="nav">
      <Link to="/" className="brand">zhice-OS V2</Link>
      <div className="workspace-tabs">
        {WORKSPACES.map((ws) => {
          const isCurrent = ws.links.some(l => l.to === location.pathname);
          return (
            <div
              key={ws.key}
              className={`workspace-tab${open === ws.key ? ' open' : ''}`}
              onMouseEnter={() => setOpen(ws.key)}
              onMouseLeave={() => setOpen(null)}
            >
              <span className={`workspace-label${isCurrent ? ' current' : ''}`}>{ws.label}</span>
              {open === ws.key && (
                <div className="workspace-dropdown">
                  {ws.links.map(link => (
                    <Link
                      key={link.to}
                      to={link.to}
                      className={`dropdown-link${location.pathname === link.to ? ' active' : ''}`}
                      onClick={() => setOpen(null)}
                    >
                      {link.label}
                    </Link>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </nav>
  );
}

export default function App() {
  return (
    <div className="app">
      <NavBar />
      <main className="content">
        <Routes>
          <Route path="/" element={<MarketPage />} />
          <Route path="/market" element={<MarketPage />} />
          <Route path="/kline" element={<KlinePage />} />
          <Route path="/market-overview" element={<ProtectedRoute><MarketOverviewPage /></ProtectedRoute>} />
          <Route path="/strategies" element={<ProtectedRoute><StrategiesPage /></ProtectedRoute>} />
          <Route path="/approvals" element={<ProtectedRoute><ApprovalsPage /></ProtectedRoute>} />
          <Route path="/positions" element={<ProtectedRoute><PositionsPage /></ProtectedRoute>} />
          <Route path="/replay" element={<ProtectedRoute><ReplayPage /></ProtectedRoute>} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/research" element={<ProtectedRoute><ResearchPage /></ProtectedRoute>} />
          <Route path="/learning-report" element={<ProtectedRoute><LearningReportPage /></ProtectedRoute>} />
          <Route path="/system-status" element={<ProtectedRoute><SystemStatusPage /></ProtectedRoute>} />
          <Route path="/alerts" element={<ProtectedRoute><AlertsPage /></ProtectedRoute>} />
          <Route path="/portfolio" element={<ProtectedRoute><PortfolioPage /></ProtectedRoute>} />
          <Route path="/accounts" element={<ProtectedRoute><AccountsPage /></ProtectedRoute>} />
          <Route path="/daily-report" element={<ProtectedRoute><DailyReportPage /></ProtectedRoute>} />
          <Route path="/knowledge" element={<ProtectedRoute><KnowledgePage /></ProtectedRoute>} />
          <Route path="/automation" element={<ProtectedRoute><AutomationPage /></ProtectedRoute>} />
          <Route path="/backtest" element={<ProtectedRoute><BacktestPage /></ProtectedRoute>} />
          <Route path="/workflow" element={<ProtectedRoute><WorkflowPage /></ProtectedRoute>} />
          <Route path="/strategy-performance" element={<ProtectedRoute><StrategyPerformancePage /></ProtectedRoute>} />
          <Route path="/regime" element={<ProtectedRoute><RegimePage /></ProtectedRoute>} />
          <Route path="/monitor" element={<ProtectedRoute><MonitorPage /></ProtectedRoute>} />
          <Route path="/exports" element={<ProtectedRoute><ExportsPage /></ProtectedRoute>} />
          <Route path="/calendar" element={<ProtectedRoute><CalendarPage /></ProtectedRoute>} />
          <Route path="/fundamentals" element={<ProtectedRoute><FundamentalsPage /></ProtectedRoute>} />
        </Routes>
      </main>
    </div>
  );
}
```

- [ ] **Step 3：在 styles.css 末尾追加导航样式**

```css
/* ===== 四工作区导航 ===== */
.nav {
  display: flex;
  align-items: center;
  padding: 0 24px;
  height: 48px;
  background: #0F1117;
  border-bottom: 1px solid #1A1F2E;
  position: sticky;
  top: 0;
  z-index: 100;
}

.brand {
  font-size: 15px;
  font-weight: 700;
  color: #5B6CF0;
  text-decoration: none;
  margin-right: 28px;
  flex-shrink: 0;
}

.workspace-tabs {
  display: flex;
  align-items: stretch;
  height: 100%;
}

.workspace-tab {
  position: relative;
  display: flex;
  align-items: center;
}

.workspace-label {
  padding: 0 18px;
  font-size: 13px;
  color: #8B93A7;
  cursor: pointer;
  user-select: none;
  height: 100%;
  display: flex;
  align-items: center;
  transition: color 0.15s;
  border-bottom: 2px solid transparent;
}

.workspace-label:hover { color: #C5CBD8; }
.workspace-label.current { color: #C5CBD8; border-bottom-color: #5B6CF0; }
.workspace-tab.open .workspace-label { color: #5B6CF0; }

.workspace-dropdown {
  position: absolute;
  top: 48px;
  left: 0;
  min-width: 150px;
  background: #1A1F2E;
  border: 1px solid #252B3B;
  border-radius: 6px;
  padding: 6px 0;
  box-shadow: 0 8px 24px rgba(0,0,0,0.5);
  z-index: 200;
}

.dropdown-link {
  display: block;
  padding: 9px 18px;
  font-size: 13px;
  color: #8B93A7;
  text-decoration: none;
  transition: background 0.1s, color 0.1s;
  white-space: nowrap;
}

.dropdown-link:hover { background: #252B3B; color: #C5CBD8; }
.dropdown-link.active { color: #5B6CF0; background: #1C2244; }

.content {
  padding: 20px 24px;
  min-height: calc(100vh - 48px);
}
```

- [ ] **Step 4：构建验证**

```bash
cd /Users/join/Desktop/zhice/zhice-os-v2/frontend && npm run build 2>&1 | tail -10
```

Expected: Build succeeds

- [ ] **Step 5：提交**

```bash
git add frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat: restructure nav into 4 workspaces (交易/资产/研究/系统)"
```

---

## 最终验证

- [ ] **后端全量测试**

```bash
.venv/bin/python -m pytest -v 2>&1 | tail -10
```

Expected: ≥55 PASS, 0 FAIL

- [ ] **前端构建**

```bash
cd /Users/join/Desktop/zhice/zhice-os-v2/frontend && npm run build 2>&1 | grep -E "error|warning|dist"
```

Expected: `dist/` 生成，无 error

---

## 自检：规范覆盖

| 设计文档要求 | 对应任务 | 覆盖 |
|---|---|---|
| 允许 5174 端口（部署文档 17） | Task 1 | ✅ |
| 行情 pct_change 正确 | Task 2 | ✅ |
| MACD 指标（文档 10 待办） | Task 3 + 5 + 6 | ✅ |
| BOLL 指标（文档 10 待办） | Task 4 + 5 + 6 | ✅ |
| WebSocket 周期推送（文档 08） | Task 7 | ✅ |
| K 线指标开关 | Task 6 | ✅ |
| 4 工作区导航（文档 09） | Task 8 | ✅ |
