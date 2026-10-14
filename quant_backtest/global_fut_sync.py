# -*- coding: utf-8 -*-
"""外盘商品期货日线采集器（387 C轴源头层）。

源：东方财富 push2his kline（匿名免 token）。
  secid 常量表 → 固化直打，不做分页发现（clist 分页必被限流，见 05:17 实证）。

落库：quant_backtest/data/tdx/ts_global_fut/
  schema 对齐 ts_fut_daily：ts_code / trade_date / pre_close / pre_settle /
                            open / high / low / close / settle / change1 /
                            change2 / vol / amount / _fetched_at
  —— change1/settle/oi 等东财不提供的列留 NaN，读层按可选列消费。

纪律：只写自己的新表，不碰任何既有生产表。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "ts_global_fut")
MANIFEST = os.path.join(OUT_DIR, "_manifest.json")

# ⚠️ 2026-10-15 05:43 判例（重要）：
#   push2his.eastmoney.com 单 host 独立限流 —— 05:16 直打成功，05:43 起连 lmt=6 也
#   RemoteDisconnected；同期 push2.eastmoney.com 实时快照仍通（HG00Y 现价 6.6150 自证）。
#   ⇒ 不是 IP 被封、不是 payload 大小，是 host 级频控。解法＝多 host 轮转 + 退避。
KLINE_HOSTS = [
    "push2his.eastmoney.com",
    "push2his.eastmoney.com",   # 主 host 权重最高：先试它
    "1.push2his.eastmoney.com",
    "7.push2his.eastmoney.com",
    "82.push2his.eastmoney.com",
    "48.push2his.eastmoney.com",
]
KLINE_TPL = ("https://{host}/api/qt/stock/kline/get"
             "?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
             "&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
             "&klt={klt}&fqt=1&beg={beg}&end=20500101&lmt={lmt}")

_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://quote.eastmoney.com/",
}
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# ── secid 常量表（东财市场 101 = 外盘期货）────────────────────
# 实测 05:16 PASS：101.HG00Y = COMEX 铜连续，日线 461 根 2025-01-02~2026-10-14
GLOBAL_FUT = {
    "COMEX铜":      {"secid": "101.HG00Y",  "qcode": "HG00Y", "unit": "美元/磅"},
    "COMEX金":      {"secid": "101.GC00Y",  "qcode": "GC00Y", "unit": "美元/盎司"},
    "COMEX银":      {"secid": "101.SI00Y",  "qcode": "SI00Y", "unit": "美元/盎司"},
    "LME铝":        {"secid": "101.AHD",    "qcode": "AHD",   "unit": "美元/吨"},
    "LME锌":        {"secid": "101.ZSD",    "qcode": "ZSD",   "unit": "美元/吨"},
    "LME铜":        {"secid": "101.CAD",    "qcode": "CAD",   "unit": "美元/吨"},
    "NYMEX原油":    {"secid": "101.CL00Y",  "qcode": "CL00Y", "unit": "美元/桶"},
    "COMEX黄金连续": {"secid": "101.GC00Y",  "qcode": "GC00Y", "unit": "美元/盎司"},
}
# 去重（同 secid 只拉一次）
_seen = set()
for _n, _v in list(GLOBAL_FUT.items()):
    if _v["secid"] in _seen:
        del GLOBAL_FUT[_n]
    else:
        _seen.add(_v["secid"])


def fetch(secid: str, klt: int = 101, beg: str = "0",
          lmt: int = 800, retry: int = 6) -> list[str] | None:
    """返回 klines 原始行列表；失败返回 None（None=网络失败，[]=secid 无效/无数据）。

    退避策略：轮转 KLINE_HOSTS + 指数退避（2/4/8/16/32s，上限 40s）。
    """
    last = None
    for a in range(retry):
        host = KLINE_HOSTS[a % len(KLINE_HOSTS)]
        url = KLINE_TPL.format(host=host, secid=secid, klt=klt, beg=beg, lmt=lmt)
        try:
            req = urllib.request.Request(url, headers=_UA)
            with _opener.open(req, timeout=40) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            data = j.get("data")
            if not data:
                return []          # secid 无效 / 该周期无数据
            return data.get("klines") or []
        except Exception as e:      # noqa: BLE001 - 网络层一律退避重试
            last = "%s %s" % (type(e).__name__, str(e)[:60])
            time.sleep(min(2.0 * (2 ** a), 40.0))
    print("    [warn] %s beg=%s lmt=%d 最终失败：%s" % (secid, beg, lmt, last))
    return None


def fetch_history(secid: str, y0: int = 2010, klt: int = 101,
                  retry: int = 3) -> list[str]:
    """逐年分窗拉全历史（判例：大 lmt 必被断连，年份窗稳定）。

    每窗 lmt=800（约 3 年交易日），逐年推进；任一窗失败不清空已得数据。
    """
    import datetime as _dt
    collected: list[str] = []
    y1 = _dt.date.today().year + 1
    for y in range(y0, y1 + 1):
        beg = "%d0101" % y
        kl = fetch(secid, klt=klt, beg=beg, lmt=800, retry=retry)
        if kl is None:
            continue
        if not kl:
            break                # 该年无数据（未上市）→ 后续年仍试，但先省一次请求
        collected.extend(kl)
        print("      %s 年窗 beg=%s rows=%d 累计=%d" % (y, beg, len(kl), len(collected)))
        time.sleep(0.7)
    # 去重（按首字段日期）并按日期升序
    seen, uniq = set(), []
    for line in collected:
        d = line.split(",")[0]
        if d not in seen:
            seen.add(d)
            uniq.append(line)
    uniq.sort(key=lambda s: s.split(",")[0])
    return uniq


def to_frame(klines: list[str], ts_code: str, fetched_at: str) -> pd.DataFrame:
    """东财 kline 行：日期,开,收,高,低,量,额,振幅 → 对齐 ts_fut_daily schema。"""
    rows = []
    for line in klines:
        p = line.split(",")
        if len(p) < 7:
            continue
        d, o, c, h, l = p[0], p[1], p[2], p[3], p[4]
        vol = float(p[5] or 0)
        amt = float(p[6] or 0)
        try:
            o_f, c_f, h_f, l_f = float(o), float(c), float(h), float(l)
        except ValueError:
            continue
        pre = rows[-1]["close"] if rows else None
        df = pd.DataFrame([{
            "ts_code": ts_code,
            "trade_date": d.replace("-", ""),
            "pre_close": pre,
            "pre_settle": None,
            "open": o_f,
            "high": h_f,
            "low": l_f,
            "close": c_f,
            "settle": c_f,          # 外盘无结算价概念，用收盘价占位（读层勿当结算用）
            "change1": (c_f - pre) if pre else None,
            "change2": ((c_f - pre) / pre * 100) if pre else None,
            "vol": vol,
            "amount": amt,
            "oi": None,
            "oi_chg": None,
            "_fetched_at": fetched_at,
        }])
        rows.append(df.to_dict("records")[0])
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def load_manifest() -> dict:
    if os.path.exists(MANIFEST):
        try:
            return json.load(open(MANIFEST, "r", encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_manifest(m: dict) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser(description="外盘商品期货日线采集（东财源）")
    ap.add_argument("--only", default="", help="只拉指定品种名（逗号分隔）")
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument("--y0", type=int, default=2010, help="历史起始年（分窗拉）")
    ap.add_argument("--quick", action="store_true",
                    help="只拉最近 3 年（增量/冒烟用）")
    args = ap.parse_args()

    targets = GLOBAL_FUT
    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        targets = {k: v for k, v in GLOBAL_FUT.items() if k in want}
        if not targets:
            print("无匹配品种，可选：%s" % ", ".join(GLOBAL_FUT))
            return 2

    m = load_manifest()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print("=== 外盘商品采集 · %d 个品种 · %s ===" % (len(targets), stamp))
    ok = fail = 0
    for name, meta in targets.items():
        secid, qcode = meta["secid"], meta["qcode"]
        ts_code = "GF.%s" % qcode            # GF = Global Futures，前缀避开 ts_fut_daily 的 CU.SHF
        if args.dryrun:
            print("  DRY %-14s %-12s → %s" % (name, secid, ts_code))
            continue
        kl = fetch_history(secid, y0=args.y0) if not args.quick \
            else fetch(secid, beg="%d0101" % (time.localtime().tm_year - 3), lmt=800)
        if kl is None:
            fail += 1
            m[ts_code] = {"name": name, "secid": secid, "ok": False}
            save_manifest(m)
            continue
        df = to_frame(kl, ts_code, stamp)
        if df.empty:
            print("  [miss] %-14s %-12s 空数据（secid 可能无效）" % (name, secid))
            fail += 1
            m[ts_code] = {"name": name, "secid": secid, "ok": False, "rows": 0}
            save_manifest(m)
            continue
        os.makedirs(OUT_DIR, exist_ok=True)
        out = os.path.join(OUT_DIR, "%s.parquet" % ts_code.replace(".", "_"))
        df.to_parquet(out, index=False)
        print("  [ok] %-14s %-12s rows=%-6d %s~%s"
              % (name, secid, len(df), df["trade_date"].iloc[0],
                 df["trade_date"].iloc[-1]))
        m[ts_code] = {"name": name, "secid": secid, "ok": True,
                      "rows": int(len(df)),
                      "min": str(df["trade_date"].iloc[0]),
                      "max": str(df["trade_date"].iloc[-1]),
                      "unit": meta.get("unit"), "fetched_at": stamp}
        ok += 1
        save_manifest(m)
        time.sleep(0.8)          # 限流自律：勿连打（05:17 判例）
    save_manifest(m)
    print("=== 完成 ok=%d fail=%d → %s ===" % (ok, fail, OUT_DIR))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())