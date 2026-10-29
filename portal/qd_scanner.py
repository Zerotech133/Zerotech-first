# -*- coding: utf-8 -*-
"""量子双刀全市场扫描器（研究预览版，K-STOCK-022）

用法（须 venv 解释器，需 numpy/pandas）：
    <venv>/envs/default/Scripts/python.exe portal/qd_scanner.py [--top N]
    （venv 解释器由调用方解析——portal_api.py 已改环境探测，勿写死用户目录）

数据源：quant_backtest/data/ashare/*.parquet（tushare 口径，amount=千元——
与引擎 strict 阈值 3e6（30 亿）直接吻合，无需换算）
输出：portal/qd_scan_cache.json（ts/耗时/扫描数/信号池/regime 分布/近5日信号）
性质：只读特征湖 + 只 import engine 信号模块；零影子链路触碰；纯研究预览，
      未回测验证（T-20261009-009 结论前不构成选股依据）。
"""

import argparse
import glob
import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "agent_platform" / "engine"))

from quant_double_knife import quant_double_knife  # noqa: E402
from tools import norm_symbol, quote_snapshot, make_context  # noqa: E402

ASHARE = ROOT / "quant_backtest" / "data" / "ashare"
CACHE = Path(__file__).parent / "qd_scan_cache.json"

_op_qd = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕系统代理直连

_LAKE_AMOUNT = {}  # W1-2 任务B：{code: {td8: amount千元}} 进程内缓存


def lake_amounts(code):
    """湖 amount 序列（准确千元口径，W1-2 任务B）。缺失/坏行 → None。

    坑: 湖是 tushare daily 不复权口径，但 amount 是实际成交额，与复权
    无关——可与腾讯 qfq 实时 bar 直接拼接。"""
    import pandas as pd  # noqa: F401

    if code in _LAKE_AMOUNT:
        return _LAKE_AMOUNT[code]
    out = None
    fp = ASHARE / (code.replace(".", "_") + ".parquet")
    try:
        df = pd.read_parquet(fp, columns=["trade_date", "amount"])
        if len(df) and df["amount"].notna().any():
            out = {
                str(t)[:8]: float(a)
                for t, a in zip(df["trade_date"], df["amount"])
                if a == a
            }
    except Exception:
        out = None
    _LAKE_AMOUNT[code] = out
    return out


def merge_amount(d, lake):
    """构造 amount 序列（千元，W1-2 任务B）：湖口径优先——历史根逐日
    用湖 amount（准确），仅当日实时 bar 用 vol*close/1000 估算增量
    （占比 ~1/20，临界票误判从 ~5% 降到 ~0.3%）。
    降级：湖缺失 / 湖末根错日（≠实时序列前一日）/ 历史日期未全覆盖
    → 全序列退回近似（旧行为）。
    返回 (pd.Series, used_lake: bool)。"""
    import pandas as pd  # noqa: F401

    approx = d["vol"] * d["close"] / 1000.0
    if not lake:
        return approx, False
    dates = [str(x).replace("-", "") for x in d["date"]]
    hist = dates[:-1]
    # 判例: 湖末根必须等于实时序列的历史末根（昨日），否则湖断更/口径
    # 错位，20 日窗口混入错位数据比全近似更糟 → 整体退回
    if max(lake) != hist[-1]:
        return approx, False
    if not all(x in lake for x in hist):
        return approx, False
    vals = [lake[x] for x in hist] + [float(approx.iloc[-1])]
    return pd.Series(vals, index=d.index), True


# ---- W3-5b 任务B：市况宽度影子注记（只标注不拦截，不改信号/过滤/推送行为）----


def _load_cfg():
    """qd_scanner 配置（最小机制）：portal/qd_scanner_config.json。
    文件缺失/坏 JSON → {}（所有开关默认关 = 零改动回退）。"""
    fp = Path(__file__).parent / "qd_scanner_config.json"
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def breadth_bucket(pct):
    """宽度三档分桶（参考 cross/qpzm 环境闸门 40% 档）：<40 / 40-50 / ≥50"""
    if pct is None:
        return ""
    if pct < 40:
        return "<40"
    if pct < 50:
        return "40-50"
    return ">=50"


def gate_would_block(pct):
    """按 40% 档门控假设本单是否会被拦（只标注不拦截）：宽度<40 → True"""
    return pct is not None and pct < 40


def regime_majority(regime_now):
    """市况摘要用：当日全市场 regime 分布多数派（空分布返回空串）"""
    if not regime_now:
        return ""
    return max(regime_now.items(), key=lambda kv: kv[1])[0]


def market_summary_line(breadth_pct, bucket, regime):
    """市况摘要行，如：市况: breadth=43.2% [40-50] regime=range"""
    if breadth_pct is None:
        return ""
    return "市况: breadth=%.1f%% [%s] regime=%s" % (breadth_pct, bucket, regime)


def lake_breadth(lake_dir=None):
    """湖锚 T-1 全市场宽度：close>MA20 占比（湖内最后交易日锚定）。

    14:40 时点当日湖未更新 → 湖末根即 T-1，注记 breadth_ref=T-1；
    与回测当日宽度口径存在已知差异（留痕 docs/changes/20261014_shadow_dev_changes.md）。
    计入口径对齐 qpzm_backtest.env_gate：≥20 根 bar 才计入，MA20 为含锚日
    20 根收盘均值。锚日取湖末根众数（个别断更票自动剔除）。返回 dict 或 None。
    """
    import pandas as pd  # 顶部不 import：与全文件惯例一致
    from collections import Counter

    lake_dir = Path(lake_dir) if lake_dir else ASHARE
    rows = []
    for fp in sorted(glob.glob(str(lake_dir / "*.parquet"))):
        try:
            df = pd.read_parquet(fp, columns=["trade_date", "close"])
        except Exception:
            continue
        if len(df) < 20:
            continue
        c = pd.to_numeric(df["close"], errors="coerce")
        close = float(c.iloc[-1])
        ma20 = float(c.iloc[-20:].mean())
        if close != close or ma20 != ma20:  # NaN 守卫
            continue
        rows.append((str(df["trade_date"].iloc[-1])[:8], close > ma20))
    if not rows:
        return None
    anchor = Counter(d for d, _ in rows).most_common(1)[0][0]
    valid = [a for d, a in rows if d == anchor]
    n_above = sum(1 for a in valid if a)
    n_total = len(valid)
    pct = round(100.0 * n_above / n_total, 1) if n_total else None
    return {
        "breadth": pct,
        "bucket": breadth_bucket(pct),
        "breadth_ref": "T-1",
        "ref_date": anchor,
        "n_above": n_above,
        "n_total": n_total,
    }


def annotate_breadth(signals, note):
    """给信号 dict 附加宽度注记（全部为新增键，下游 qd_view/qd_adapter
    用 .get() 读缺失键不炸；不触碰任何信号判定字段）"""
    for info in signals:
        info["breadth"] = note["breadth"]
        info["breadth_bucket"] = note["bucket"]
        info["breadth_ref"] = note["breadth_ref"]
        info["gate_would_block"] = gate_would_block(note["breadth"])


def apply_breadth_note(sig_now, recent, tail_signals, regime_now, enabled):
    """W3-5b 注记主入口。enabled=False → 原样返回 (None, "")，零改动回退。
    返回 (breadth_note, market_summary)。"""
    if not enabled:
        return None, ""
    note = lake_breadth()
    if not note:
        return None, ""
    annotate_breadth(sig_now, note)
    annotate_breadth(recent, note)
    annotate_breadth(tail_signals, note)
    summary = market_summary_line(
        note["breadth"], note["bucket"], regime_majority(regime_now)
    )
    note["market_summary"] = summary
    note["note"] = (
        "breadth_ref=T-1：14:40 时点当日湖未更新，用 T-1 锚宽度，"
        "与回测当日口径存在已知差异"
    )
    return note, summary


# ---- [20261014_p1_tail] 双刀多源降级（腾讯→新浪→东财，判例 W3-4 末根错日）----
# 背景：实时行情原单依赖腾讯源，W3-4 曾因腾讯源末根错日整段不可信。
# 原则：每源取回后先做末根时间戳校验，错日=该源失败（宁可降级不假绿）；
#       全源失败必须显式返回，调用方跳过并留痕，绝不允许拿错日数据继续算。


def _http_text(url, headers=None, timeout=10, encoding="utf-8"):
    req = urllib.request.Request(url, headers=headers or {})
    with _op_qd.open(req, timeout=timeout) as r:
        return r.read().decode(encoding, "ignore")


def _http_json(url, headers=None, timeout=10):
    return json.loads(_http_text(url, headers=headers, timeout=timeout))


def _secid(plain):
    """东财 secid 前缀：sh→1，sz→0（与 tools.norm_symbol 同规则）"""
    return ("1." if plain[0] == "6" else "0.") + plain


def _kline_tencent(plain, n):
    """腾讯日K（前复权）：[[date, open, close, high, low, vol], ...]"""
    from tools import kline_daily

    return kline_daily(plain, n=n)


def _kline_sina(plain, n):
    """新浪日K → 映射为腾讯行格式。坑: 新浪 volume 单位是股、腾讯是手
    → ÷100 对齐量纲（量比/均量类特征跨源才可比）。"""
    u = (
        "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService"
        ".getKLineData?symbol=%s&scale=240&ma=no&datalen=%d" % (norm_symbol(plain), n)
    )
    rows = _http_json(u) or []
    return [
        [
            d["day"],
            d["open"],
            d["close"],
            d["high"],
            d["low"],
            float(d["volume"]) / 100.0,
        ]
        for d in rows
    ]


def _kline_east(plain, n):
    """东财日K（fqt=1 前复权）→ 映射为腾讯行格式。
    fields2=f51..f56 依次 date,open,close,high,low,vol(手)。"""
    u = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
        "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56"
        "&klt=101&fqt=1&end=20500101&lmt=%d" % (_secid(plain), n)
    )
    kl = ((_http_json(u) or {}).get("data") or {}).get("klines") or []
    return [row.split(",")[:6] for row in kl]


def _kline_last_day(rows):
    """末根交易日 YYYYMMDD（错日判定用；坏结构返回空串=失败）"""
    try:
        return str(rows[-1][0]).replace("-", "")
    except Exception:
        return ""


def kline_multisource(plain, n=150, expect_day=None, enabled=None):
    """多源日K降级链（腾讯→新浪→东财）。返回 (rows|None, source, note)。

    - enabled 显式 False → 仅腾讯（config qd_multi_source=false 回退旧行为）；
      None → 读 config qd_multi_source（缺省 true）
    - 每源末根时间戳必须 == expect_day（错日=该源失败，判例 W3-4）
    - 全源失败 → (None, "all_failed", 降级链留痕)；source 命名：主源命中=
      tencent，备选命中=sina_fallback / east_fallback（落产物 note 用）
    """
    if enabled is None:
        enabled = bool(_load_cfg().get("qd_multi_source", True))
    chain = [("tencent", _kline_tencent)]
    if enabled:
        chain += [("sina", _kline_sina), ("east", _kline_east)]
    notes = []
    for name, fn in chain:
        try:
            rows = fn(plain, n)
        except Exception as e:
            notes.append("%s:%s" % (name, type(e).__name__))
            continue
        if not rows:
            notes.append("%s:empty" % name)
            continue
        if expect_day and _kline_last_day(rows) != expect_day:
            notes.append("%s:last=%s" % (name, _kline_last_day(rows)))
            continue
        return (
            rows,
            (name if name == "tencent" else name + "_fallback"),
            ";".join(notes),
        )
    return None, "all_failed", ";".join(notes) or "no_source"


def _quote_tencent(plain):
    """腾讯快照（tools.quote_snapshot，含 PE/PB/市值/换手——仅主源全字段）"""
    return quote_snapshot(plain)


def _quote_sina(plain):
    """新浪快照 → 统一字段映射：价格/涨跌幅/成交量/时间戳。
    坑: hq.sinajs.cn 要求 Referer，否则 407；volume 单位股→手。"""
    txt = _http_text(
        "https://hq.sinajs.cn/list=" + norm_symbol(plain),
        headers={"Referer": "https://finance.sina.com.cn"},
        encoding="gbk",
    )
    f = txt.split('"')[1].split(",")
    price, preclose = float(f[3]), float(f[2])
    return {
        "name": f[0],
        "price": price,
        "change_pct": round((price / preclose - 1) * 100, 2) if preclose else None,
        "volume": float(f[8]) / 100.0,
        "ts": ("%s %s" % (f[30], f[31])).strip(),
    }


def _quote_east(plain):
    """东财快照 → 统一字段映射。fltt=2 返回已除算浮点，免手工缩放；
    f43=价格 f47=量(手) f60=昨收 f86=时间戳(秒) f170=涨跌幅。"""
    d = (
        _http_json(
            "https://push2.eastmoney.com/api/qt/stock/get?secid=%s"
            "&invt=2&fltt=2&fields=f43,f47,f57,f58,f60,f86,f170" % _secid(plain)
        )
        or {}
    ).get("data") or {}
    ts = d.get("f86")
    try:
        ts_s = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        ts_s = ""
    return {
        "name": d.get("f58") or plain,
        "price": d.get("f43"),
        "change_pct": d.get("f170"),
        "volume": d.get("f47"),
        "ts": ts_s,
    }


def quote_multisource(plain, enabled=None):
    """多源快照降级（同 kline_multisource 纪律）。返回 (dict|None, source, note)。
    备选源只保证 name/price/change_pct/volume/ts 五键，换手/市值等
    主源专有字段缺失即缺失（调用方按 .get 容错，不造默认值）。"""
    if enabled is None:
        enabled = bool(_load_cfg().get("qd_multi_source", True))
    chain = [("tencent", _quote_tencent)]
    if enabled:
        chain += [("sina", _quote_sina), ("east", _quote_east)]
    notes = []
    for name, fn in chain:
        try:
            q = fn(plain)
        except Exception as e:
            notes.append("%s:%s" % (name, type(e).__name__))
            continue
        if not q or not q.get("price"):
            notes.append("%s:empty" % name)
            continue
        return q, (name if name == "tencent" else name + "_fallback"), ";".join(notes)
    return None, "all_failed", ";".join(notes) or "no_source"


def scan_one(fp):
    """单票扫描：返回 (code, dict) 或 None（缺列/数据不足/无信号时摘要返回）"""
    code = os.path.basename(fp).replace(".parquet", "").replace("_", ".")
    df = pd.read_parquet(
        fp, columns=["trade_date", "open", "high", "low", "close", "amount"]
    )
    if len(df) < 100:  # 次新股/数据不足：MA54+TSI60 warmup 不够
        return code, None
    df = df.rename(columns={"trade_date": "date"}).set_index("date")
    out = quant_double_knife(df, profile="strict")
    hits = out[out["double_knife_signal"]]
    last = out.iloc[-1]
    recent_days = sorted(hits.index[-5:]) if len(hits) else []
    info = {
        "code": code,
        "signal_now": bool(last["double_knife_signal"]),
        "regime": str(last["regime_label"]),
        "close": round(float(last.name and df["close"].iloc[-1]), 2),
        "recent_signals": recent_days,
        "n_recent": int(out["double_knife_signal"].tail(5).sum()),
        # 尾盘两段式候选（K-STOCK-025）：末行为单日十字星（双刀第一刀已落，
        # 今日/次日或成第二刀）→ 14:40 盘中用实时 bar 复核
        "star_yesterday": bool(last["cross_star"]),
    }
    return code, info


def intraday_recheck(candidates):
    """尾盘两段式复核（K-STOCK-025）：候选票拉日 K（盘中最后一根=当日
    实时 bar）→ 完整八重重算 → 当日触发即尾盘买信号。
    量级：候选=昨日单日十字星票（通常几十~几百只），逐票 1 请求可承受。
    [20261014_p1_tail] 行情走多源降级链（腾讯→新浪→东财），每源末根
    时间戳校验，错日=切下家；全源失败显式跳过留 note（不假绿）。
    返回 (tail_signals, tail_checked, tail_note)
    """
    import pandas as pd  # noqa: F401

    today = datetime.now().strftime("%Y%m%d")
    multi = bool(_load_cfg().get("qd_multi_source", True))
    tail_signals, checked = [], 0
    note = ""
    for code in candidates:
        plain = code.split(".")[0]
        kl, src, src_note = kline_multisource(
            plain, n=150, expect_day=today, enabled=multi
        )
        if kl is None:
            # 全源失败/错日：该候选显式跳过（checked 不计入，note 留痕）
            note = "全源失败/错日（%s），候选 %s 跳过（不假绿）" % (src_note, code)
            continue
        if len(kl) < 100:
            continue
        checked += 1
        d = pd.DataFrame(kl).iloc[:, :6]
        d.columns = ["date", "open", "close", "high", "low", "vol"]
        for c in ("open", "close", "high", "low", "vol"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
        # W1-2 任务B 量纲修复：湖口径优先——历史 19 根用湖 amount（准确
        # 千元），仅当日实时 bar 估算增量；湖缺失/末根错日退回全近似并在
        # note 标注 amount_approx（不炸、不误报，量纲语义透明）
        lake = lake_amounts(code)
        d["amount"], used_lake = merge_amount(d, lake)
        note = "盘中实时 bar 复核通过 · 14:55 执行窗口 · source=%s" % src
        if src_note:
            note += "（降级链：%s）" % src_note
        if not used_lake:
            note += " · amount_approx（湖缺失/末根错日，20日均额为近似口径）"
        try:
            out = quant_double_knife(d.set_index("date"), profile="strict")
        except Exception:
            continue
        last = out.iloc[-1]
        if bool(last["double_knife_signal"]):
            tail_signals.append(
                {
                    "code": code,
                    "close": round(float(d["close"].iloc[-1]), 2),
                    "regime": str(last["regime_label"]),
                    "note": note,
                }
            )
    return tail_signals, checked, note


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--recent-window", type=int, default=5, help="近 N 日信号窗口（默认 5）"
    )
    ap.add_argument(
        "--intraday",
        action="store_true",
        help="尾盘模式（14:40）：候选池+当日实时 bar 复核，"
        "产出 14:55 执行窗口的尾盘买信号",
    )
    args = ap.parse_args()
    intraday = args.intraday

    t0 = time.time()
    files = sorted(glob.glob(str(ASHARE / "*.parquet")))
    sig_now, recent, regime_now, skipped = [], [], {}, 0
    star_pool = []  # 昨日单日十字星全量候选池（尾盘两段式第一段）
    for i, fp in enumerate(files):
        try:
            code, info = scan_one(fp)
        except Exception:
            skipped += 1
            continue
        if info is not None and info.get("star_yesterday"):
            star_pool.append(info["code"])
        if info is None:
            skipped += 1
            continue
        regime_now[info["regime"]] = regime_now.get(info["regime"], 0) + 1
        if info["signal_now"]:
            sig_now.append(info)
        elif info["n_recent"] > 0:
            recent.append(info)
        if (i + 1) % 1000 == 0:
            print(
                "[scan] %d/%d 已扫 %.0fs" % (i + 1, len(files), time.time() - t0),
                flush=True,
            )

    # 尾盘两段式（K-STOCK-025）：14:40 盘中触发时，用「昨日单日十字星」
    # 候选池 + 当日实时 bar 复核，产出今日尾盘买信号（14:55 执行窗口）。
    # 非盘中（腾讯末根≠当日）自动退化为空并留 note，不误报。
    tail_signals, tail_checked, tail_note = [], 0, ""
    star_candidates = [
        info["code"] for info in recent + sig_now if info.get("star_yesterday")
    ]
    # 候选池不足时补扫一遍湖里昨日单日十字星票（recent 仅含已触发票）
    if intraday and star_pool:
        star_candidates = sorted(set(star_candidates) | set(star_pool))
    if intraday:
        tail_signals, tail_checked, tail_note = intraday_recheck(star_candidates)
    # W3-5b 任务B：市况宽度影子注记（qd_breadth_note 开关，关=零改动回退）
    breadth_note, market_summary = apply_breadth_note(
        sig_now,
        recent,
        tail_signals,
        regime_now,
        bool(_load_cfg().get("qd_breadth_note", False)),
    )
    cache = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 1),
        "scanned": len(files) - skipped,
        "skipped": skipped,
        "signals_now": sig_now,
        "signals_recent": recent,
        "regime_now": regime_now,
        "star_pool_n": len(star_pool),
        "intraday_mode": intraday,
        "tail_signals": tail_signals,
        "tail_checked": tail_checked,
        "tail_note": tail_note,
        "disclaimer": "研究态：未回测验证（T-20261009-009 结论前不构成选股依据）",
    }
    # W3-5b 注记键：仅开关开启且湖宽度可用时新增（向后兼容，下游 .get()）
    if breadth_note:
        cache["breadth_note"] = breadth_note
        cache["market_summary"] = market_summary
    # 信号票补详情（K-STOCK-026 老田反馈：名称/涨跌/市值/换手）
    # [20261014_p1_tail] 快照同样走多源降级；备选源缺换手/市值字段时
    # 不写该键（保持缺失语义，下游 .get 容错），不造默认值
    quote_multi = bool(_load_cfg().get("qd_multi_source", True))
    for info in sig_now + recent:
        try:
            # norm_symbol 不认带点后缀（600887.SZ→sh600887. 坏 URL），先剥
            plain = info["code"].split(".")[0]
            q, qsrc, _qn = quote_multisource(plain, enabled=quote_multi)
        except Exception:
            q, qsrc = None, "tencent"
        if q:
            info["name"] = q["name"]
            for k in ("change_pct", "turnover_pct", "mktcap_yi"):
                if q.get(k) is not None:
                    info[k] = q[k]
            if qsrc != "tencent":
                info["quote_source"] = qsrc  # 降级留痕（产物可见）
        else:
            info["name"] = info.get("name", "")
    # 当日信号票加技术状态摘要（近5日票不拉，省请求）
    for info in sig_now:
        try:
            ctx = make_context(info["code"].split(".")[0])
            mt = ctx.get("market_technical", "")
            import re as _re

            arr = _re.search(r"(多头排列|空头排列|纠缠)", mt)
            c5 = _re.search(r"近5日涨跌 ([+\-]?[\d.]+)%", mt)
            vr = _re.search(r"量比\(对5日均量\) ([\d.]+)", mt)
            p20 = _re.search(r"现价距20日高点 ([+\-]?[\d.]+)%", mt)
            parts = []
            if arr:
                parts.append(arr.group(1))
            if c5:
                parts.append("近5日%s%%" % c5.group(1))
            if vr:
                parts.append("量比%s" % vr.group(1))
            if p20:
                parts.append("距20日高%s%%" % p20.group(1))
            info["tech"] = " · ".join(parts)
        except Exception:
            info["tech"] = ""

    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        "[done] 扫描 %d 只（跳过 %d）：当日信号 %d，近 %d 日有信号 %d，"
        "尾盘候选池 %d，尾盘复核 %d 只→信号 %d %s，耗时 %.0fs → %s%s"
        % (
            cache["scanned"],
            skipped,
            len(sig_now),
            args.recent_window,
            len(recent),
            len(star_pool),
            tail_checked,
            len(tail_signals),
            ("(%s)" % tail_note) if tail_note else "",
            cache["elapsed_s"],
            CACHE,
            (" · " + market_summary) if market_summary else "",
        )
    )


if __name__ == "__main__":
    import pandas as pd  # noqa: E402

    main()
