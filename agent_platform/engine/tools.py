# -*- coding: utf-8 -*-
"""数据工具层 v1（K-STOCK-004）：腾讯免费行情接口，零 token 依赖

- kline_daily：日 K 线（前复权，web.ifzq.gtimg.cn）
- quote_snapshot：实时快照（qt.gtimg.cn，含 PE/PB/市值/换手）
- make_context：为投研部五角色组装数据上下文（analysis_type → 数据段）
- 情绪面 v1 用 K 线派生代理指标（近5日涨幅/量比/20日位置）；jev 事件库接入留 v2
- 2026-10-31 WO-INTEL-N1AB：v2 接线以 `INTEL_TO_N1` feature flag A/B 接入
  （默认 off；off 时本文件输出与旧版逐字一致；on 时 news_event 优先用
  agent_platform/intel_context.py 的 reviewed JEV，无可用复核证据则回退 price_proxy）
- Tushare token 在 jev-eval/rotate_tushare_token.ps1 体系内，接入后可切主源
"""

import glob
import json
import logging
import os
import urllib.request

_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕系统代理直连

# ---- INTEL_TO_N1 A/B（2026-10-31 WO-INTEL-N1AB；默认 off）----
# off：完全不进入下方接线分支 → make_context 输出与旧版逐字一致（测试锁定）；
# on ：news_event 优先 reviewed JEV（agent_platform/intel_context.py），
#      无可用复核证据时保留旧 price_proxy 文本（不伪造“已核验”）。
_INTEL_TO_N1_ENV = "INTEL_TO_N1"
_INTEL_TO_N1_ON = ("1", "true", "yes", "on", "y")


def _intel_to_n1_enabled() -> bool:
    return str(os.environ.get(_INTEL_TO_N1_ENV) or "").strip().lower() \
        in _INTEL_TO_N1_ON


def _intel_context_module():
    """导入 agent_platform/intel_context.py（兼容包/扁平/脚本三种上下文）。"""
    try:
        from agent_platform import intel_context as mod
        return mod
    except ImportError:
        pass
    try:
        import intel_context as mod  # agent_platform 目录在 sys.path（run_shadow 脚本态）
        return mod
    except ImportError:
        pass
    import importlib.util
    from pathlib import Path as _Path
    path = _Path(__file__).resolve().parents[1] / "intel_context.py"
    spec = importlib.util.spec_from_file_location("intel_context_n1", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _apply_intel_to_n1(out: dict, code) -> dict:
    """flag on：reviewed JEV 可用则替换 news_event；否则保持 price_proxy 文本。

    返回 intel_context 结果（含 source/reason/event_ids/review_ids/bad_rows），
    供 A/B 日志归因；任何异常由调用方兜底，不改变旧链路。
    """
    ev = _intel_context_module().reviewed_news_event(code)
    if ev.get("source") == "reviewed_jev" and ev.get("text"):
        out["news_event"] = ev["text"]
        print("[intel_to_n1] source=reviewed_jev code=%s events=%d rejected=%s "
              "bad_rows=%d" % (ev.get("code") or code,
                               len(ev.get("event_ids") or []),
                               ev.get("rejected") or 0,
                               len(ev.get("bad_rows") or [])))
    else:
        print("[intel_to_n1] source=price_proxy reason=%s code=%s bad_rows=%d"
              % (ev.get("reason") or "unknown", ev.get("code") or code,
                 len(ev.get("bad_rows") or [])))
    return ev


def norm_symbol(code):
    """600519→sh600519；000001/300750→sz000001；920378/8xxxxx/4xxxxx→bj920378"""
    code = str(code or "").strip().lower()
    for p in ("sh", "sz", "bj", "."):
        code = code.replace(p, "")
    if code.startswith(("4", "8", "92")):
        return "bj" + code
    return ("sh" if code[0] == "6" else "sz") + code


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCAL_KLINE_DIR = os.path.join(REPO_ROOT, "quant_backtest", "data", "ashare")


def _local_kline(code, n=30):
    """本地 ashare parquet 回退（Tencent 501 时用）；返回与 kline_daily 同形。"""
    try:
        import pandas as pd
    except ImportError:
        return []
    bare = str(code or "").strip().lower()
    for p in ("sh", "sz", "bj", "."):
        bare = bare.replace(p, "")
    suffix = "SH" if bare.startswith("6") else ("BJ" if bare.startswith(("4", "8", "92")) else "SZ")
    files = glob.glob(os.path.join(LOCAL_KLINE_DIR, "%s_%s.parquet" % (bare, suffix)))
    if not files:
        return []
    try:
        df = pd.read_parquet(files[0])
        df = df.sort_values("trade_date").tail(int(n))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for _, r in df.iterrows():
        d = str(r["trade_date"])
        if len(d) == 8:
            d = "%s-%s-%s" % (d[:4], d[4:6], d[6:])
        out.append([d, float(r["open"]), float(r["close"]), float(r["high"]),
                    float(r["low"]), float(r["volume"])])
    return out


def kline_daily(code, n=30):
    """前复权日 K：[[date, open, close, high, low, vol, ...], ...]"""
    s = norm_symbol(code)
    u = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq" % (
        s,
        n,
    )
    try:
        with _op.open(u, timeout=10) as r:
            d = json.load(r)
        node = d["data"][s]
        bars = node.get("qfqday") or node.get("day") or []
        if bars:
            return bars
    except Exception:  # noqa: BLE001
        pass
    return _local_kline(code, n)

def quote_snapshot(code):
    """快照：name/price/change_pct/turnover/PE/PB/总市值(亿)"""
    s = norm_symbol(code)
    with _op.open("https://qt.gtimg.cn/q=" + s, timeout=10) as r:
        f = r.read().decode("gbk", "ignore").split("~")

    def _f(i, default=0.0):
        try:
            return float(f[i])
        except (ValueError, IndexError):
            return default

    return {
        "name": f[1] if len(f) > 1 else code,
        "price": _f(3),
        "change_pct": _f(32),
        "turnover_pct": _f(38),
        "pe": _f(39),
        "pb": _f(46),
        "mktcap_yi": round(
            _f(45), 1
        ),  # f[45] 单位已是亿（实测茅台 15657.27 = 1.57 万亿）
    }


def _ma(closes, n):
    return round(sum(closes[-n:]) / min(n, len(closes)), 2) if closes else 0.0


def make_context(code):
    """返回 {analysis_type: 数据文本段}，注入各角色 prompt（防 LLM 臆造数据）"""
    bars = kline_daily(code, 30)
    if not bars:
        # 2026-10-30 WO-INV-BJ-EMPTY：北交所/无K线标的此前 bars[0] 裸 IndexError；
        # 改为 fail-visible，让 run_shadow 记 failed 而非整链异常。
        raise ValueError("KLINE_EMPTY: %s（无可用日K数据）" % code)
    q = quote_snapshot(code)
    closes = [float(b[2]) for b in bars]
    vols = [float(b[5]) for b in bars] if len(bars[0]) > 5 else []
    chg5 = round((closes[-1] / closes[-6] - 1) * 100, 2) if len(closes) >= 6 else None
    vol_ratio = round(vols[-1] / (sum(vols[-6:-1]) / 5), 2) if len(vols) >= 6 else None
    ma5, ma10, ma20 = _ma(closes, 5), _ma(closes, 10), _ma(closes, 20)
    arrange = (
        "多头排列"
        if ma5 > ma10 > ma20
        else ("空头排列" if ma5 < ma10 < ma20 else "纠缠")
    )
    pos20 = round((closes[-1] / max(closes[-20:]) - 1) * 100, 2)

    recent = "\n".join(
        "%s 收%s 高%s 低%s 量%s" % (b[0], b[2], b[3], b[4], b[5] if len(b) > 5 else "?")
        for b in bars[-8:]
    )
    market = (
        "近8日K线（前复权）:\n%s\n均线: MA5=%s MA10=%s MA20=%s（%s）\n"
        "现价距20日高点 %s%%，近5日涨跌 %s%%，量比(对5日均量) %s"
        % (recent, ma5, ma10, ma20, arrange, pos20, chg5, vol_ratio)
    )
    fundamental = (
        "快照: 名称=%s 现价=%s 涨跌=%s%% PE=%s PB=%s 总市值=%s亿 换手=%s%%"
        % (
            q["name"],
            q["price"],
            q["change_pct"],
            q["pe"],
            q["pb"],
            q["mktcap_yi"],
            q["turnover_pct"],
        )
    )
    sentiment = (
        "情绪代理指标(v1，事件库jev接入v2): 近5日涨跌=%s%%，量比=%s，现价距20日高点=%s%%，"
        "换手率=%s%%。解读提示：放量上涨偏暖、缩量下跌观望、高位放量警惕。"
        % (chg5, vol_ratio, pos20, q["turnover_pct"])
    )
    final = (
        "基本面快照: %s\n技术面: MA5=%s MA10=%s MA20=%s（%s），近5日=%s%%\n情绪代理: 近5日=%s%% 量比=%s"
        % (
            fundamental.split(": ", 1)[1],
            ma5,
            ma10,
            ma20,
            arrange,
            chg5,
            chg5,
            vol_ratio,
        )
    )
    out = {
        "market_technical": market,
        "fundamental": fundamental,
        "news_event": sentiment,
        "final_decision": final,
        "name": q["name"],
    }
    # 2026-10-31 WO-INTEL-N1AB：INTEL_TO_N1 A/B（默认 off）。
    # off 时本分支不执行 → 输出与旧版逐字一致；on 时 reviewed JEV 优先，
    # 无可用复核证据则保持上面的 price_proxy 文本（不伪造“已核验”）。
    if _intel_to_n1_enabled():
        try:
            _apply_intel_to_n1(out, code)
        except Exception as exc:  # noqa: BLE001 —— 接线失败不得打崩主链
            logging.getLogger(__name__).warning(
                "INTEL_TO_N1 接入失败，保持 price_proxy：%s", exc)
    # 2026-10-30 WO-D1-HERMES-BRIDGE：追加 Hermes stock-data 只读补充；
    # fail-open、带缓存、D1_HERMES_BRIDGE=0 可关闭，不参与编排/裁决。
    try:
        from agent_platform.hermes_bridge import context_text as _hermes_text
        extra = _hermes_text(code)
        if extra:
            for _k in ("market_technical", "fundamental", "news_event", "final_decision"):
                out[_k] = out[_k] + "\n\n[Hermes stock-data 只读补充]\n" + extra
    except Exception:  # noqa: BLE001 数据桥失败不影响主链
        pass
    return out


if __name__ == "__main__":
    import sys

    ctx = make_context(sys.argv[1] if len(sys.argv) > 1 else "600519")
    for k, v in ctx.items():
        print("== %s ==" % k)
        print(v)
