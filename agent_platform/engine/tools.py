# -*- coding: utf-8 -*-
"""数据工具层 v1（K-STOCK-004）：腾讯免费行情接口，零 token 依赖

- kline_daily：日 K 线（前复权，web.ifzq.gtimg.cn）
- quote_snapshot：实时快照（qt.gtimg.cn，含 PE/PB/市值/换手）
- make_context：为投研部五角色组装数据上下文（analysis_type → 数据段）
- 情绪面 v1 用 K 线派生代理指标（近5日涨幅/量比/20日位置）；jev 事件库接入留 v2
- Tushare token 在 jev-eval/rotate_tushare_token.ps1 体系内，接入后可切主源
"""

import json
import urllib.request

_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕系统代理直连


def norm_symbol(code):
    """600519→sh600519；000001/300750→sz000001；920378/8xxxxx/4xxxxx→bj920378"""
    code = str(code or "").strip().lower()
    for p in ("sh", "sz", "bj", "."):
        code = code.replace(p, "")
    if code.startswith(("4", "8", "92")):
        return "bj" + code
    return ("sh" if code[0] == "6" else "sz") + code


def kline_daily(code, n=30):
    """前复权日 K：[[date, open, close, high, low, vol, ...], ...]"""
    s = norm_symbol(code)
    u = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq" % (
        s,
        n,
    )
    with _op.open(u, timeout=10) as r:
        d = json.load(r)
    node = d["data"][s]
    return node.get("qfqday") or node.get("day") or []


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
    return {
        "market_technical": market,
        "fundamental": fundamental,
        "news_event": sentiment,
        "final_decision": final,
        "name": q["name"],
    }


if __name__ == "__main__":
    import sys

    ctx = make_context(sys.argv[1] if len(sys.argv) > 1 else "600519")
    for k, v in ctx.items():
        print("== %s ==" % k)
        print(v)
