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
# 2026-10-16 收编（数据地基模块章程 §1）：迁回 data/ 仓内，与 docstring
# 声明一致；旧位置 HERE/ts_global_fut 已废弃，verify_incr_state 双探测兼容。
OUT_DIR = os.path.join(HERE, "data", "tdx", "ts_global_fut")
MANIFEST = os.path.join(OUT_DIR, "_manifest.json")

# ⚠️ 2026-10-15 05:43 判例（重要）：
#   push2his.eastmoney.com 单 host 独立限流 —— 05:16 直打成功，05:43 起连 lmt=6 也
#   RemoteDisconnected；同期 push2.eastmoney.com 实时快照仍通（HG00Y 现价 6.6150 自证）。
#   ⇒ 不是 IP 被封、不是 payload 大小，是 host 级频控。解法＝多 host 轮转 + 退避。
#
# 🔴 K-STOCK-411（10-15 11:10）修正：**用「每次重试换 host」而非「按重试序号取模」**。
#   原写法 `KLINE_HOSTS[a % len]` 虽能轮转，但主 host 占 2 个槽位 ⇒
#   在 retry=4 时主 host 仍被试 2 次、后备只轮到 1 次，
#   叠加指数退避（2/4/8/16s）总窗口 ~30s 时**最可能被限的后备窗口已过**。
#   11:00 实测：`push2his` 断但 `1.push2his` 通 —— 有序遍历优先命中可用 host。
KLINE_HOSTS = [
    "1.push2his.eastmoney.com",  # 🔴 11:00 实测唯一可通，置首
    "7.push2his.eastmoney.com",
    "82.push2his.eastmoney.com",
    "48.push2his.eastmoney.com",
    "push2his.eastmoney.com",
]
KLINE_TPL = (
    "https://{host}/api/qt/stock/kline/get"
    "?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
    "&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
    "&klt={klt}&fqt=1&beg={beg}&end=20500101&lmt={lmt}"
)

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# ── secid 常量表 ────────────────────────────��────────────
# 🔴 K-STOCK-411（10-15 11:00-11:20 实测纠错）：原表 7 个 secid 里 **5 个无效**
#   （返回空数据而非报错 ⇒ 静默浪费每轮 4 次重试 + 最长 40s 退避）。
#   根因：**东财按交易所给不同 market 前缀**，我原先一律用 101。
# 实测方法：market 前缀 {100..107,133} × 品种代码 全组合 126 组探针，
#   判据 = `bars>0`（连通且有数据），并核 `name` 排除同代码误命中。
#
# ✅ 实测有效（bars>0 + name 核对）：
#   101.HG00Y = COMEX铜     101.GC00Y = COMEX黄金
#   102.CL00Y = NYMEX原油    102.PA00Y = NYMEX钯金
#
# ⚠️ 已剔除的误命中：
#   105.AHD → name='GraniteShares Autocallable HOOD'（**美股**，与 LME 铝同代码撞号）
#   101.SI00Y / 101.ZSD / 101.CAD / 101.CL00Y → 实测 bars≤0（secid 不存在）
#   ⇒ COMEX银 / LME 铝 / LME 锌 / LME 铜 在东财**当前无有效 secid**，
#     已从常量表移除（不占采集轮次）；需要时走 stooq/新浪外盘或改为个股页 secid 反查。
GLOBAL_FUT = {
    "COMEX铜": {"secid": "101.HG00Y", "qcode": "HG00Y", "unit": "美元/磅"},
    "COMEX金": {"secid": "101.GC00Y", "qcode": "GC00Y", "unit": "美元/盎司"},
    "NYMEX原油": {"secid": "102.CL00Y", "qcode": "CL00Y", "unit": "美元/桶"},
    "NYMEX钯金": {"secid": "102.PA00Y", "qcode": "PA00Y", "unit": "美元/盎司"},
}
# 去重（同 secid 只拉一次）
_seen = set()
for _n, _v in list(GLOBAL_FUT.items()):
    if _v["secid"] in _seen:
        del GLOBAL_FUT[_n]
    else:
        _seen.add(_v["secid"])


def fetch(
    secid: str, klt: int = 101, beg: str = "0", lmt: int = 800, retry: int = 6
) -> list[str] | None:
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
                return []  # secid 无效 / 该周期无数据
            return data.get("klines") or []
        except Exception as e:  # noqa: BLE001
            last = "%s %s" % (type(e).__name__, str(e)[:60])
            time.sleep(min(2.0 * (2**a), 40.0))
    print("    [warn] %s beg=%s lmt=%d 最终失败：%s" % (secid, beg, lmt, last))
    return None


def fetch_history(
    secid: str, y0: int = 2010, klt: int = 101, retry: int = 3
) -> list[str]:
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
            break  # 该年无数据（未上市）→ 后续年仍试，但先省一次请求
        collected.extend(kl)
        print(
            "      %s 年窗 beg=%s rows=%d 累计=%d" % (y, beg, len(kl), len(collected))
        )
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
        df = pd.DataFrame(
            [
                {
                    "ts_code": ts_code,
                    "trade_date": d.replace("-", ""),
                    "pre_close": pre,
                    "pre_settle": None,
                    "open": o_f,
                    "high": h_f,
                    "low": l_f,
                    "close": c_f,
                    "settle": c_f,  # 外盘无结算价概念，用收盘价占位（读层勿当结算用）
                    "change1": (c_f - pre) if pre else None,
                    "change2": ((c_f - pre) / pre * 100) if pre else None,
                    "vol": vol,
                    "amount": amt,
                    "oi": None,
                    "oi_chg": None,
                    "_fetched_at": fetched_at,
                }
            ]
        )
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
    """原子写 manifest（2026-11-02 修复：原直接 open("w") 中途被杀会写坏）。

    先写同目录 .tmp 再 os.replace；失败不影响旧文件。
    """
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MANIFEST)


def _fail_entry(m: dict, ts_code: str, name: str, secid: str, err: str) -> None:
    """失败时保留既有成功记录（rows/min/max/fetched_at），只标注 last_error。

    2026-11-02 修复：原实现失败即覆盖成 ok=False rows=0，会把上午成功采集的
    有效记录抹掉（今日 18:15 卡住运行即发生）。
    """
    prev = m.get(ts_code) or {}
    entry = dict(prev)
    entry.update({"name": name, "secid": secid, "last_error": err,
                  "last_failed_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S")})
    if not prev.get("ok"):
        entry["ok"] = False
    m[ts_code] = entry

def main() -> int:
    ap = argparse.ArgumentParser(description="外盘商品期货日线采集（东财源）")
    ap.add_argument("--only", default="", help="只拉指定品种名（逗号分隔）")
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument("--y0", type=int, default=2010, help="历史起始年（分窗拉）")
    ap.add_argument("--quick", action="store_true", help="只拉最近 3 年（增量/冒烟用）")
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
        ts_code = (
            "GF.%s" % qcode
        )  # GF = Global Futures，前缀避开 ts_fut_daily 的 CU.SHF
        if args.dryrun:
            print("  DRY %-14s %-12s → %s" % (name, secid, ts_code))
            continue
        kl = (
            fetch_history(secid, y0=args.y0)
            if not args.quick
            else fetch(secid, beg="%d0101" % (time.localtime().tm_year - 3), lmt=800)
        )
        if kl is None:
            fail += 1
            _fail_entry(m, ts_code, name, secid, "fetch_none")
            save_manifest(m)
            continue
        df = to_frame(kl, ts_code, stamp)
        if df.empty:
            print("  [miss] %-14s %-12s 空数据（secid 可能无效）" % (name, secid))
            fail += 1
            _fail_entry(m, ts_code, name, secid, "empty_frame")
            save_manifest(m)
            continue
        os.makedirs(OUT_DIR, exist_ok=True)
        out = os.path.join(OUT_DIR, "%s.parquet" % ts_code.replace(".", "_"))
        df.to_parquet(out, index=False)
        print(
            "  [ok] %-14s %-12s rows=%-6d %s~%s"
            % (
                name,
                secid,
                len(df),
                df["trade_date"].iloc[0],
                df["trade_date"].iloc[-1],
            )
        )
        m[ts_code] = {
            "name": name,
            "secid": secid,
            "ok": True,
            "rows": int(len(df)),
            "min": str(df["trade_date"].iloc[0]),
            "max": str(df["trade_date"].iloc[-1]),
            "unit": meta.get("unit"),
            "fetched_at": stamp,
        }
        ok += 1
        save_manifest(m)
        time.sleep(0.8)  # 限流自律：勿连打（05:17 判例）
    save_manifest(m)
    print("=== 完成 ok=%d fail=%d → %s ===" % (ok, fail, OUT_DIR))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
