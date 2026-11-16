# -*- coding: utf-8 -*-
"""量能画像：从 TDX 合作伙伴 API 取日线/分钟量能，回答 T1 反复问的「量能确认」（2026-11-16 WO-VOLUME-PROFILE）。

## 为什么

四席位里 T1 每次都说「缺量能确认」，而 xsec_profile 只给了一个相对值 `vol20_vs_prev20`（1.47），
既没有绝对量级、也没有方向、也没有日内结构——所以多头永远只能「条件性看多」。

## 参数语义（**实测确认，非文档**）

| 参数 | 语义 |
|---|---|
| `code` | **仅单只**；`code=a,b` → 0 行；`codes=` → **被静默忽略并返回全市场**（危险，禁用） |
| `limit` / `offset` | 条数 / 偏移；默认按日期**降序** |
| `start_date` / `end_date` | `YYYYMMDD`，对 `ts_daily` 与 `ts_stk_mins` **都生效** |

`ts_stk_mins` 实测：单日返回 **241 行**（09:30–11:30 + 13:00–15:00），`freq=1min`。

## 只做客观计算，不给买卖建议

    python -X utf8 tools/volume_profile.py 300476.SZ --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# 2026-11-16：原为硬编码的本机私有区绝对路径，命中公开面 VAULT_PATH 规则
# （路径会随 push 暴露本机目录结构）。改为配置化，与其余 9 个 TDX 调用方同一套：
#   环境变量 TDX_API_KEY > 仓库根 .env.local 的同名项（.gitignore 已忽略）
#   兜底才是本机约定文件（用 TDX_API_KEY_FILE 指定，仍不入库）
BASE = "http://rookie.xtcloud-tech.com:48088"
DAY_LOOKBACK = 30          # 日线回看交易日数

# ── 判据实测表现（2026-11-16 回放：200 只全市场随机 × 250 日，无偏样本）──────────────
# 存在的意义：字段名本身在暗示"这是买点"。不把实测表现一并交给席位，它会拿已证伪的判据当证据。
SIGNAL_VALIDITY = {
    "breakout_with_volume": {
        "n": 1389, "r5_mean_pct": -0.91, "r5_win_rate": 40.5,
        "verdict": "已证伪：5 日平均 -0.91%，低于基准 -0.09%；不可作为买入依据，仅作当日量能描述",
    },
    "shrink_pullback": {
        "n": 10597, "r5_mean_pct": -0.11, "r5_win_rate": 45.0,
        "verdict": "无区分度：与基准几乎相同；不构成利好也不构成利空",
    },
    "gap_up": {
        "n": 537, "r5_mean_pct": 2.97, "r5_win_rate": 51.5,
        "verdict": "存活但弱：胜率仅 51.5%，均值靠少数大赢家拉动；不可单独作为买入依据",
    },
}
BASELINE_R5 = -0.09        # 同期全样本基准
VALIDITY_SOURCE = "200只全市场随机×250交易日 回放（2026-11-16，seed 20261116）"


def _key() -> str:
    """TDX 服务 API Key（2026-11-16 起服务端 auth_mode=enforce）。

    顺序：环境变量 TDX_API_KEY > 仓库根 .env.local 的同名项
          > 本机 vault 约定文件（TDX_API_KEY_FILE 指定）。
    缺 Key 立刻抛错 —— 不让请求静默 401 后被当成「这只票没数据」。
    """
    k = (os.environ.get("TDX_API_KEY") or "").strip()
    if k:
        return k
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        envf = os.path.join(d, ".env.local")
        if os.path.isfile(envf):
            try:
                with open(envf, encoding="utf-8-sig") as fh:
                    for raw in fh:
                        raw = raw.strip()
                        if raw.startswith("TDX_API_KEY="):
                            v = raw.split("=", 1)[1].strip().strip('"').strip("'")
                            if v:
                                return v
            except OSError:
                pass
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    legacy = os.environ.get("TDX_API_KEY_FILE")
    if legacy and os.path.isfile(legacy):
        return Path(legacy).read_text(encoding="utf-8").strip()
    raise RuntimeError(
        "未配置 TDX_API_KEY —— 2026-11-16 起 TDX 服务强制鉴权。"
        "请在环境变量或仓库根 .env.local 设置 TDX_API_KEY=<tdx_...>")


def _query(tbl: str, params: dict, timeout: int = 60) -> dict:
    url = "%s/api/data/table/%s?%s" % (BASE, tbl, urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={"X-API-Key": _key()})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _rows(doc: dict) -> list[dict]:
    cols = doc.get("columns") or []
    return [dict(zip(cols, r)) for r in (doc.get("rows") or [])]


def _f(v):
    try:
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def daily_volume(code: str, days: int = DAY_LOOKBACK) -> dict:
    """日线量能：均量、量比、放量突破/缩量回调判定（全部可复算）。"""
    doc = _query("ts_daily", {"code": code, "limit": days})
    rows = _rows(doc)
    if not rows:
        return {"available": False, "reason": "ts_daily 无记录"}
    # 接口默认降序 → 升序排列便于算近端
    rows = sorted(rows, key=lambda r: str(r.get("trade_date")))
    vols = [_f(r.get("vol")) or 0.0 for r in rows]
    closes = [_f(r.get("close")) or 0.0 for r in rows]
    pcts = [_f(r.get("pct_chg")) or 0.0 for r in rows]
    if len(rows) < 6:
        return {"available": False, "reason": "日线样本不足（%d 条）" % len(rows)}
    last = rows[-1]
    v = vols[-1]

    def avg(n: int) -> float:
        """前 n 日均量（**不含当日**）——量比的常规口径是「今日量 / 前 N 日均量」。"""
        prev = vols[:-1]
        tail = prev[-n:] if len(prev) >= n else prev
        return sum(tail) / len(tail) if tail else 0.0

    ma5, ma10, ma20 = avg(5), avg(10), avg(20)
    ratio20 = round(v / ma20, 2) if ma20 else None
    high20 = max(closes[-21:-1]) if len(closes) >= 21 else max(closes[:-1])
    breakout = bool(
        (_f(last.get("pct_chg")) or 0) > 3 and (ratio20 or 0) >= 1.5
        and (_f(last.get("close")) or 0) > high20)
    shrink_pullback = bool(
        (_f(last.get("pct_chg")) or 0) < 0 and (ratio20 or 9) <= 0.8)

    # 量价配合（当日）
    pct = _f(last.get("pct_chg")) or 0.0
    if pct > 0 and (ratio20 or 0) >= 1.2:
        vp = "价涨量增"
    elif pct > 0 and (ratio20 or 9) < 0.8:
        vp = "价涨量缩"
    elif pct < 0 and (ratio20 or 0) >= 1.2:
        vp = "价跌量增"
    elif pct < 0 and (ratio20 or 9) < 0.8:
        vp = "价跌量缩"
    else:
        vp = "量价中性"

    return {
        "available": True,
        "last_date": str(last.get("trade_date")),
        "close": _f(last.get("close")),
        "pct_chg": pct,
        "vol_unit": "手（日线口径）",
        "vol_today": round(v, 1),
        "vol_ma5": round(ma5, 1), "vol_ma10": round(ma10, 1), "vol_ma20": round(ma20, 1),
        "vol_ratio_20": ratio20,                       # 量比（相对 20 日均量）
        "high20": round(high20, 2),
        "breakout_with_volume": breakout,              # 放量突破：涨>3% + 量比≥1.5 + 破 20 日高
        "shrink_pullback": shrink_pullback,            # 缩量回调：跌 + 量比≤0.8
        "volume_price": vp,                            # 价涨量增/价涨量缩/价跌量增/价跌量缩/中性
        "recent": [{"date": str(r.get("trade_date")), "close": _f(r.get("close")),
                    "pct": _f(r.get("pct_chg")), "vol": round(_f(r.get("vol")) or 0, 1)}
                   for r in rows[-5:]],
    }


def intraday_volume(code: str, day: str | None = None) -> dict:
    """分钟量能：开盘/尾盘占比、上午下午分布、最大单分钟量（日内结构）。"""
    day = (day or dt.date.today().isoformat()).replace("-", "")
    doc = _query("ts_stk_mins", {"code": code, "start_date": day, "end_date": day, "limit": 5000})
    rows = _rows(doc)
    if not rows:
        return {"available": False, "reason": "ts_stk_mins 当日无记录（可能非交易日或未采集）"}
    rows = sorted(rows, key=lambda r: str(r.get("trade_time")))
    pts = [(str(r.get("trade_time")), _f(r.get("vol")) or 0.0, _f(r.get("close")) or 0.0)
           for r in rows]
    total = sum(v for _, v, _ in pts)
    if total <= 0:
        return {"available": False, "reason": "当日分钟量为 0"}
    hm = [t[11:16] for t, _, _ in pts]
    open30 = sum(v for (t, v, _), h in zip(pts, hm) if "09:30" <= h < "10:00")
    close30 = sum(v for (t, v, _), h in zip(pts, hm) if "14:30" <= h <= "15:00")
    am = sum(v for (t, v, _), h in zip(pts, hm) if h < "11:31")
    pm = total - am
    peak = max(pts, key=lambda x: x[1])
    return {
        "available": True,
        "date": day,
        "bars": len(pts),
        "first_bar": hm[0], "last_bar": hm[-1],
        "vol_unit": "股（分钟线口径，与日线差 100 倍，勿直接相加/比较）",
        "vol_total": round(total, 1),
        "open30_share": round(open30 / total * 100, 1),
        "close30_share": round(close30 / total * 100, 1),
        "am_share": round(am / total * 100, 1),
        "pm_share": round(pm / total * 100, 1),
        "peak_time": peak[0][11:16], "peak_vol": round(peak[1], 1),
        "close_first": pts[0][2], "close_last": pts[-1][2],
    }


def profile(code: str, day: str | None = None) -> dict:
    out = {"code": code, "source": "tdx_partner_api",
           "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M")}
    warns: list[str] = []
    try:
        dv = daily_volume(code)
    except Exception as exc:  # noqa: BLE001
        dv = {"available": False, "reason": "ts_daily 调用失败：%s" % type(exc).__name__}
    if dv.get("available"):
        out["daily"] = dv
    else:
        warns.append(dv.get("reason") or "日线量能不可用")
    try:
        iv = intraday_volume(code, day)
    except Exception as exc:  # noqa: BLE001
        iv = {"available": False, "reason": "ts_stk_mins 调用失败：%s" % type(exc).__name__}
    if iv.get("available"):
        out["intraday"] = iv
    else:
        warns.append(iv.get("reason") or "分钟量能不可用")
    # 单位口径交叉校验：日线为「手」、分钟线为「股」，差 100 倍；写进结果避免误读量级
    if out.get("daily") and out.get("intraday"):
        try:
            dv_sum = float(out["daily"]["vol_today"]) * 100.0
            iv_sum = float(out["intraday"]["vol_total"])
            if iv_sum and abs(dv_sum - iv_sum) / iv_sum < 0.02:
                out["unit_check"] = "日线(手)×100 ≈ 分钟线(股)，一致"
            else:
                out["unit_check"] = "日线(手)×100=%.0f ≠ 分钟线(股)=%.0f，口径待核" % (dv_sum, iv_sum)
        except (KeyError, TypeError, ValueError):
            pass
    out["available"] = bool(out.get("daily") or out.get("intraday"))
    # 把判据的历史实测表现一并交给席位——避免把已证伪的字段名当买点
    out["signal_validity"] = {
        "source": VALIDITY_SOURCE,
        "baseline_r5_mean_pct": BASELINE_R5,
        "note": "这些字段只描述当日量能特征，**不是买入信号**；下列为无偏样本回放结果",
        "judgements": SIGNAL_VALIDITY,
    }
    out["warnings"] = warns
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="量能画像（TDX 合作伙伴 API）")
    ap.add_argument("code")
    ap.add_argument("--day", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    p = profile(args.code, args.day)
    print(json.dumps(p, ensure_ascii=False, indent=1))
    return 0 if p.get("available") else 2


if __name__ == "__main__":
    raise SystemExit(main())
