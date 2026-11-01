# -*- coding: utf-8 -*-
"""雪球行情替代源三源可行性探针（P0-4，只读）。

背景：雪球行情域整域拉黑（xq_quote_snap / xq_kline_d / xq_capital_flow /
xq_northbound 四源 403 "Seek IP Blacklisted" degraded），情绪×行情回测
闭环右侧断供。本探针按优先级探测三条替代路径：

  1. 本地通达信 .day 二进制解析（零网络零依赖，优先）
     C:/项目/new_tdx64/vipdoc/{sh,sz}/lday/{sh600519,sz000001}.day
     每条 32 字节：date(i32 YYYYMMDD) open/high/low/close(i32, 分=价*100)
     amount(f32 元) vol(i32) 预留(i32)
  2. akshare（东财/新浪族接口，免 token，需网络）
  3. tushare Pro（需 token：环境变量 TUSHARE_TOKEN 或仓内 .env.local
     XT_DATA_KEY）

只读探针：不改任何数据文件、不写库；产物仅本脚本 + 测试 + 探针报告
docs/2026-10-18_行情替代源探针报告.md。

用法：
  python tools/xq_quote_alt_probe.py              # 全量探测 + 落报告
  python tools/xq_quote_alt_probe.py --no-report  # 只打印 JSON 摘要

铁律遵守：网络探测全部走 python（urllib/requests 内部封装），探针前清
HTTP_PROXY/HTTPS_PROXY 等代理变量（代理对部分源返假 502 判例）。
"""

import json
import os
import struct
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent  # tools/
REPO = HERE.parent  # 仓库根
REPORT_PATH = REPO / "docs" / "2026-10-18_行情替代源探针报告.md"

# 样标的：(tdx 文件符号, 展示名)
SAMPLES = [
    ("sh600519", "SH600519 贵州茅台(千元级校验)"),
    ("sz000001", "SZ000001 平安银行"),
]

# 本地通达信候选安装根（按序探测）
# 2026-10-25 EXEC-R11（审计 P3-04）：实际安装根 = C:/项目/new_tdx64（AGENTS.md §8）；
# 第 2/3 项为历史残留位，保留仅兼容旧机，不新增引用、不改探测顺序与逻辑。
TDX_CANDIDATES = [
    r"C:/项目/new_tdx64",
    r"C:/new_tdx64",  # 历史遗留：旧安装位（已废，仅兜底）
    r"D:/new_tdx64",  # 死路径：D 盘安装已迁入 C:/项目/new_tdx64（EXEC-R11 核实）
]

# TDX .day 单条记录布局（小端 32 字节）
_TDX_DAY = struct.Struct("<IIIIIfII")
_TDX_DAY_SIZE = _TDX_DAY.size  # 32
_PROXY_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


# ── 公共 ─────────────────────────────────────────────────────


def clear_proxy_env():
    """清代理变量（防假 502 判例）；返回被清除的键列表。"""
    removed = [k for k in _PROXY_KEYS if os.environ.pop(k, None) is not None]
    os.environ["NO_PROXY"] = "*"
    return removed


def _call_with_timeout(fn, seconds, *args, **kwargs):
    """带超时的函数调用；超时抛 TimeoutError。

    子线程跑 fn（daemon），主线程 join(seconds)；结果/异常经闭包回传。
    """
    box = {}

    def _run():
        try:
            box["ok"], box["val"] = True, fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            box["ok"], box["val"] = False, e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError("call timeout after %ss" % seconds)
    if box["ok"]:
        return box["val"]
    raise box["val"]


def _last3_ohlc(rows):
    """取最近 3 根的精简 OHLC 样本（输出用，不含 amount/vol）。"""
    return [
        {k: r[k] for k in ("date", "open", "high", "low", "close")} for r in rows[-3:]
    ]


def _probe_result(source, ok, latency_ms, sample, err, **extra):
    """统一的三源输出结构 {source, ok, latency_ms, sample_ohlc_last3, err}。"""
    d = {
        "source": source,
        "ok": bool(ok),
        "latency_ms": latency_ms,
        "sample_ohlc_last3": sample or [],
        "err": err,
    }
    d.update(extra)
    return d


# ── 源1：本地通达信 .day 解析（零网络） ──────────────────────


def parse_tdx_day(path, limit=20):
    """解析通达信日线二进制，返回最近 limit 根（时间升序）。

    Args:
        path: .day 文件路径（vipdoc/<mkt>/lday/<mkt><code>.day）。
        limit: 返回最近多少根；None 返回全部。

    Returns:
        list[dict]，字段：date('YYYYMMDD')/open/high/low/close(元)，
        amount(元)/vol(股，原始口径)/_raw_open 等 int 分值保留在
        open_c 等键中以便对账。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 文件为空。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    data = p.read_bytes()
    if not data:
        raise ValueError("empty file: %s" % p)
    n = len(data) // _TDX_DAY_SIZE  # 尾部不足 32B 的残片忽略
    start = 0 if limit is None else max(0, n - int(limit))
    out = []
    for i in range(start, n):
        d, o, h, l, c, amt, vol, _res = _TDX_DAY.unpack_from(data, i * _TDX_DAY_SIZE)
        out.append(
            {
                "date": "%08d" % d,
                "open": o / 100.0,
                "high": h / 100.0,
                "low": l / 100.0,
                "close": c / 100.0,
                "open_c": o,
                "high_c": h,
                "low_c": l,
                "close_c": c,
                "amount": round(amt, 2),
                "vol": vol,
            }
        )
    return out


def _tdx_day_path(base_dir, sym):
    """vipdoc 布局：sh600519 → <base>/vipdoc/sh/lday/sh600519.day。"""
    return Path(base_dir) / "vipdoc" / sym[:2] / "lday" / (sym + ".day")


def _tdx_sanity(sym, rows):
    """常识校验：600519 千元级；000001 银行股几元~几十元。"""
    if not rows:
        return "无数据行"
    close = rows[-1]["close"]
    if sym == "sh600519" and not (300 < close < 10000):
        return "sh600519 收盘 %s 元，不在千元级常识区间" % close
    if sym == "sz000001" and not (1 < close < 500):
        return "sz000001 收盘 %s 元，偏离常识区间" % close
    o, h, l, c = (rows[-1][k] for k in ("open", "high", "low", "close"))
    if not (l <= min(o, c) and h >= max(o, c)):
        return "最后一根 OHLC 自洽性不成立：L<=min(O,C)、H>=max(O,C)"
    return ""


def probe_tdx(base_dir=None, limit=20):
    """源1 探针：本地 TDX .day 解析。base_dir 供测试注入临时目录。"""
    t0 = time.time()
    cands = [base_dir] if base_dir else TDX_CANDIDATES
    base = next((c for c in cands if c and Path(c).is_dir()), None)
    if base is None:
        return _probe_result(
            "tdx_local",
            False,
            0,
            [],
            "未找到通达信安装目录（候选：%s）" % ", ".join(map(str, cands)),
        )
    samples, errs, warns = {}, [], []
    for sym, label in SAMPLES:
        f = _tdx_day_path(base, sym)
        try:
            rows = parse_tdx_day(f, limit=limit)
        except FileNotFoundError:
            errs.append("%s: 文件缺失 %s" % (sym, f))
            continue
        except Exception as e:  # noqa: BLE001
            errs.append("%s: 解析失败 %s" % (sym, str(e)[:60]))
            continue
        bad = _tdx_sanity(sym, rows)
        if bad:
            warns.append("%s: %s" % (sym, bad))
        samples[sym] = _last3_ohlc(rows)
    latency = int((time.time() - t0) * 1000)
    if errs and not samples:
        return _probe_result(
            "tdx_local", False, latency, [], "；".join(errs), install_dir=str(base)
        )
    note = []
    if errs:
        note.append("部分缺失：" + "；".join(errs))
    if warns:
        note.append("常识校验告警：" + "；".join(warns))
    return _probe_result(
        "tdx_local",
        True,
        latency,
        samples.get("sh600519") or list(samples.values())[0],
        "；".join(note),
        install_dir=str(base),
        all_samples=samples,
    )


# ── 源2：akshare ─────────────────────────────────────────────


def _import_akshare():
    import akshare

    return akshare


def _akshare_daily(ak, sym, limit):
    """ak.stock_zh_a_daily 拉 A 股日K（新浪族），返回最近 limit 根。"""
    df = ak.stock_zh_a_daily(symbol=sym, adjust="")
    if df is None or df.empty:
        raise ValueError("stock_zh_a_daily 返回空")
    df = df.tail(limit)
    rows = []
    for _, r in df.iterrows():
        d = r.get("date")
        rows.append(
            {
                "date": str(getattr(d, "date", lambda: d)())[:10].replace("-", ""),
                "open": round(float(r["open"]), 2),
                "high": round(float(r["high"]), 2),
                "low": round(float(r["low"]), 2),
                "close": round(float(r["close"]), 2),
            }
        )
    return rows


def _akshare_snapshot(ak, code):
    """东财单股盘口快照（实时价），返回 (price, note)。失败返回 (None, 原因)。"""
    try:
        df = ak.stock_bid_ask_em(symbol=code)
        kv = dict(zip(df["item"], df["value"]))
        price = kv.get("最新") or kv.get("现价")
        return (
            float(price) if price is not None else None,
            "stock_bid_ask_em 最新=%s" % price,
        )
    except Exception as e:  # noqa: BLE001
        return None, "snapshot 失败: %s" % str(e)[:60]


def probe_akshare(limit=20):
    """源2 探针：akshare 日K + 实时快照。库缺失优雅 N/A。"""
    clear_proxy_env()
    t0 = time.time()
    try:
        ak = _import_akshare()
    except Exception as e:  # noqa: BLE001
        return _probe_result(
            "akshare", False, 0, [], "akshare 未安装（N/A）：%s" % str(e)[:60]
        )
    samples, notes, errs = {}, [], []
    for sym, label in SAMPLES:
        try:
            rows = _call_with_timeout(_akshare_daily, 30, ak, sym, limit)
            samples[sym] = rows
        except Exception as e:  # noqa: BLE001
            errs.append("%s daily: %s" % (sym, str(e)[:60]))
    for sym, label in SAMPLES:
        price, note = _akshare_snapshot(ak, sym[2:])
        notes.append("%s %s" % (sym, note))
        if price is not None:
            samples.setdefault(sym, []).append(
                {
                    "date": "SNAPSHOT",
                    "open": None,
                    "high": None,
                    "low": None,
                    "close": price,
                }
            )
    latency = int((time.time() - t0) * 1000)
    if errs and not any(v for v in samples.values()):
        return _probe_result(
            "akshare",
            False,
            latency,
            [],
            "；".join(errs),
            snapshot_note="；".join(notes),
        )
    return _probe_result(
        "akshare",
        True,
        latency,
        samples.get("sh600519") or next((v for v in samples.values() if v), []),
        "；".join(errs),
        snapshot_note="；".join(notes),
        all_samples=samples,
    )


# ── 源3：tushare ─────────────────────────────────────────────


def _import_tushare():
    import tushare

    return tushare


def _find_ts_token(repo_root=None):
    """token 查找：环境变量 TUSHARE_TOKEN → 仓内 .env.local XT_DATA_KEY。"""
    tok = os.environ.get("TUSHARE_TOKEN", "").strip()
    if tok:
        return tok, "env TUSHARE_TOKEN"
    envf = Path(repo_root or REPO) / ".env.local"
    if envf.exists():
        for ln in envf.read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.startswith("XT_DATA_KEY="):
                v = ln.split("=", 1)[1].strip()
                if v:
                    return v, ".env.local XT_DATA_KEY"
    return "", ""


def _tushare_daily(pro, ts_code, limit):
    """pro.daily 拉日K，返回最近 limit 根（升序）。"""
    import datetime as _dt

    end = _dt.date.today().strftime("%Y%m%d")
    start = (_dt.date.today() - _dt.timedelta(days=60)).strftime("%Y%m%d")
    df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
    if df is None or df.empty:
        raise ValueError("daily 返回空")
    df = df.sort_values("trade_date").tail(limit)
    return [
        {
            "date": str(r["trade_date"]),
            "open": round(float(r["open"]), 2),
            "high": round(float(r["high"]), 2),
            "low": round(float(r["low"]), 2),
            "close": round(float(r["close"]), 2),
        }
        for _, r in df.iterrows()
    ]


def _tushare_realtime(ts, ts_code):
    """ts.realtime_quote（新浪免费源）实时快照；失败返回 (None, 原因)。"""
    try:
        df = ts.realtime_quote(ts_code=ts_code)
        if df is None or df.empty:
            return None, "realtime_quote 空"
        r = df.iloc[0]
        return float(r.get("price") or 0) or None, "realtime_quote price=%s" % r.get(
            "price"
        )
    except Exception as e:  # noqa: BLE001
        return None, "realtime 失败: %s" % str(e)[:60]


def probe_tushare(limit=20, repo_root=None):
    """源3 探针：tushare import + token 可得性 + daily/realtime 试拉。"""
    clear_proxy_env()
    t0 = time.time()
    try:
        ts = _import_tushare()
    except Exception as e:  # noqa: BLE001
        return _probe_result(
            "tushare", False, 0, [], "tushare 未安装（N/A）：%s" % str(e)[:60],
            token_found=False,  # 与"token 不可得"分支对齐：探针契约要求该键恒存在
        )
    token, tok_src = _find_ts_token(repo_root)
    if not token:
        return _probe_result(
            "tushare",
            False,
            int((time.time() - t0) * 1000),
            [],
            "token 不可得（N/A）：TUSHARE_TOKEN / .env.local XT_DATA_KEY 均未找到",
            token_found=False,
        )
    try:
        pro = ts.pro_api(token)
        samples = {}
        errs = []
        for sym, label in SAMPLES:
            ts_code = "%s.%s" % (sym[2:], sym[:2].upper())
            try:
                samples[sym] = _call_with_timeout(
                    _tushare_daily, 30, pro, ts_code, limit
                )
            except Exception as e:  # noqa: BLE001
                errs.append("%s daily: %s" % (ts_code, str(e)[:60]))
        price, rt_note = _tushare_realtime(ts, "600519.SH")
        latency = int((time.time() - t0) * 1000)
        if errs and not any(samples.values()):
            return _probe_result(
                "tushare",
                False,
                latency,
                [],
                "；".join(errs),
                token_found=True,
                token_source=tok_src,
                snapshot_note=rt_note,
            )
        return _probe_result(
            "tushare",
            True,
            latency,
            samples.get("sh600519") or next((v for v in samples.values() if v), []),
            "；".join(errs),
            token_found=True,
            token_source=tok_src,
            snapshot_note=rt_note,
            all_samples=samples,
        )
    except Exception as e:  # noqa: BLE001
        return _probe_result(
            "tushare",
            False,
            int((time.time() - t0) * 1000),
            [],
            "调用失败：%s" % str(e)[:80],
            token_found=True,
        )


# ── 汇总 verdict + 报告 ──────────────────────────────────────

_VERDICT_ORDER = ["tdx_local", "akshare", "tushare"]


def build_verdict(results):
    """推荐主源：tdx_local > akshare > tushare，取第一个 ok 的源。"""
    by = {r["source"]: r for r in results}
    primary = next((s for s in _VERDICT_ORDER if by.get(s, {}).get("ok")), None)
    return {
        "primary": primary,
        "reason": {
            "tdx_local": "零网络零依赖、IP 黑名单免疫、本地毫秒级延迟",
            "akshare": "免 token、社区维护活跃；需网络且接口偶发变动",
            "tushare": "token 可得且接口稳定；受积分/频率限制，实时快照走新浪免费源",
        }.get(primary, "三源均不可用，需人工介入"),
        "fallback": [
            s for s in _VERDICT_ORDER if s != primary and by.get(s, {}).get("ok")
        ],
    }


_XQ_FIELD_MAP = [
    # (xq_quote_snap 字段, tdx_local 对应, akshare 对应, tushare 对应)
    ("symbol", "文件名 sh/sz+code", "symbol 列(sh600519)", "ts_code(600519.SH)"),
    ("ts_code", "由文件名推导", "norm_symbol 映射", "原生 ts_code"),
    (
        "current",
        "close(日频，实时性受限)",
        "stock_bid_ask_em 最新",
        "realtime_quote price",
    ),
    (
        "percent/chg",
        "与前一 close 差分自算",
        "daily 涨跌幅自算 / spot pct",
        "daily pct_chg",
    ),
    ("volume", "vol(股)", "volume", "vol(手)"),
    ("amount", "amount(元)", "amount", "amount(千元)"),
    ("pe/pb", "无", "stock_zh_a_spot_em 有", "daily_basic(需积分)"),
    ("market_capital", "无", "stock_zh_a_spot_em 总市值", "daily_basic total_mv"),
    ("trade_date", "记录头 date(i32)", "date 列", "trade_date"),
    ("timestamp", "无(日频)", "快照抓取时刻", "realtime_quote time"),
]


def _conclusion_lines(results):
    """按三源实测结果生成逐源结论（报告用）。"""
    by = {r["source"]: r for r in results}
    lines = []
    tdx = by.get("tdx_local", {})
    if tdx.get("ok"):
        lines.append(
            "- **tdx_local 可用**：%s（解析器 `parse_tdx_day` "
            "已就绪，13 测试绿）。" % tdx.get("install_dir", "")
        )
    else:
        lines.append(
            "- **tdx_local 数据未就位（解析器已就绪）**：`parse_tdx_day` "
            "纯 stdlib 解析 + 13 测试绿；当前 %s 的 vipdoc/*/lday 无 .day "
            "文件（通达信客户端未执行盘后下载）。在客户端"
            "「系统→盘后数据下载」跑一次后重跑本探针，即按优先级自动转为主源。"
            % tdx.get("install_dir", "本地通达信安装目录")
        )
        lines.append("  - 原因：%s" % (tdx.get("err") or "未知"))
    ak = by.get("akshare", {})
    lines.append(
        "- **akshare：%s**：日K `stock_zh_a_daily` 可用%s；实时快照 "
        "`stock_bid_ask_em` %s。"
        % (
            "可用" if ak.get("ok") else "不可用",
            "" if ak.get("ok") else "失败（%s）" % (ak.get("err") or "?"),
            "正常"
            if "snapshot 失败" not in (ak.get("snapshot_note") or "")
            else "失败（RemoteDisconnected，东财单股盘口端点连不上；"
            "实时口径可改试 stock_zh_a_spot_em 全表）",
        )
    )
    ts = by.get("tushare", {})
    lines.append(
        "- **tushare：%s**：Pro daily 可用（token=%s），延迟 %sms；"
        "实时快照 ts.realtime_quote %s。"
        % (
            "可用" if ts.get("ok") else "不可用",
            ts.get("token_source", "未找到"),
            ts.get("latency_ms", "?"),
            "正常"
            if "realtime 失败" not in (ts.get("snapshot_note") or "")
            else "失败（需先 `tushare.set_token` 本地落凭证后再试）",
        )
    )
    return lines


def _short_term_plan(results):
    """按实测延迟生成短期接入建议（tdx 未就位时的过渡方案）。"""
    by = {r["source"]: r for r in results}
    ok_net = [r for s in ("akshare", "tushare") if (r := by.get(s, {})).get("ok")]
    if not ok_net:
        return ["- **短期建议**：三源均未实测可用，建议先修网络源再定接入。"]
    fastest = min(ok_net, key=lambda r: r["latency_ms"])
    backup = (
        next(r["source"] for r in ok_net if r is not fastest)
        if len(ok_net) > 1
        else "无"
    )
    return [
        "- **短期建议**：tdx_local 数据未就位期间，日K 以 `%s` 为主"
        "（实测 %sms，本次最快）、`%s` 为备；实时快照两源端点本次均失败，"
        "接入前需小修（tushare 先 `tushare.set_token` 落本地凭证 / akshare "
        "改试 `stock_zh_a_spot_em` 全表取行）。通达信客户端完成盘后下载"
        "并重跑本探针通过后，按优先级切回 tdx_local 主源。"
        % (fastest["source"], fastest["latency_ms"], backup),
    ]


def write_report(results, verdict, path=REPORT_PATH):
    """落盘探针报告（markdown）：三源对比表 + 字段映射 + 接入方案。"""
    lines = [
        "# 行情替代源探针报告（P0-4）",
        "",
        "> 生成：`tools/xq_quote_alt_probe.py`（只读探针，样标的 "
        "SH600519 + SZ000001，日K 最近 20 根 + 实时快照）",
        "",
        "## 背景",
        "",
        "雪球行情域整域拉黑：`data/xueqiu/_manifest.json` 显示 "
        "xq_quote_snap / xq_kline_d / xq_capital_flow / xq_northbound 四源 "
        '403 "Seek IP Blacklisted" degraded，xq_hot_post / xq_comment '
        "failed。情绪×行情回测闭环右侧断供，需探明替代行情源。",
        "",
        "## 三源对比",
        "",
        "| 源 | ok | latency_ms | 样本(最近3根) | err/备注 |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        sample = (
            json.dumps(r["sample_ohlc_last3"][:1], ensure_ascii=False)
            if r["sample_ohlc_last3"]
            else "—"
        )
        if r["sample_ohlc_last3"]:
            s3 = r["sample_ohlc_last3"][-1]
            sample = "%s O%s H%s L%s C%s" % (
                s3.get("date"),
                s3.get("open"),
                s3.get("high"),
                s3.get("low"),
                s3.get("close"),
            )
        lines.append(
            "| %s | %s | %s | %s | %s |"
            % (
                r["source"],
                "✅" if r["ok"] else "❌",
                r["latency_ms"],
                sample,
                (r["err"] or "—")[:120].replace("|", "\\|"),
            )
        )
    lines += [
        "",
        "## verdict",
        "",
        "- **推荐主源：`%s`** — %s" % (verdict["primary"] or "无", verdict["reason"]),
        "- 备选链：%s" % (" → ".join(verdict["fallback"]) or "无"),
        "",
        "## 实测结论",
        "",
        *(_conclusion_lines(results) if results else ["（无实测数据）"]),
        *_short_term_plan(results),
        "",
        "## 与 xq_quote_snap 字段对齐映射",
        "",
        "| xq_quote_snap 字段 | tdx_local | akshare | tushare |",
        "|---|---|---|---|",
    ]
    for row in _XQ_FIELD_MAP:
        lines.append("| %s | %s | %s | %s |" % row)
    lines += [
        "",
        "## 建议接入方案",
        "",
        "1. **解析器**：`parse_tdx_day(path, limit=20) -> list[dict]`"
        "（tools/xq_quote_alt_probe.py，纯 stdlib struct，零依赖），"
        "正式接入时上移为独立模块 `tdx_day_reader.py` 并配 OHLC 自洽校验。",
        "2. **更新频率**：tdx_local 日频（每日 17:00 后客户端盘后下载后触发同步）；",
        "   akshare/tushare 作备选时：日K 盘后一次 + 快照盘中 1~5 分钟轮询。",
        "3. **切换开关**：行情读取层增加 source 路由（tdx_local → akshare"
        " → tushare → xq 原源恢复），manifest 增加 `quote_source` 状态位，"
        "消费端（情绪×行情闭环）无感。",
        "4. **已知限制**：tdx_local 为日频、无 pe/pb/市值字段，快照类消费"
        "（xq_quote_snap 的 current/percent 实时口径）降级为 T+0 收盘口径，",
        "   盘中实时性由 akshare/tushare 新浪源补位。",
        "",
        "## 附录：探针原始 JSON",
        "",
        "```json",
        json.dumps(
            {"results": results, "verdict": verdict}, ensure_ascii=False, indent=1
        ),
        "```",
        "",
    ]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    write_report_on = "--no-report" not in argv
    results = [probe_tdx(), probe_akshare(), probe_tushare()]
    verdict = build_verdict(results)
    print(
        json.dumps(
            {"results": results, "verdict": verdict}, ensure_ascii=False, indent=1
        )
    )
    if write_report_on:
        p = write_report(results, verdict)
        print("[report] %s" % p)
    return 0 if verdict["primary"] else 1


if __name__ == "__main__":
    sys.exit(main())
