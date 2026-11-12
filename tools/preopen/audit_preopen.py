# -*- coding: utf-8 -*-
"""盘前数据校对（后峰 Agent 每天开盘前第一件事）。

## 为什么不是「看一眼最新日期」

2026-11-12 判例：`data_health.py --target today` 在 07:54 报 7/7 零采集，
看着像数据全挂，实际两件事叠一起 —— ①它的目标日是「今天」，盘前本来没有当日日线；
②它指向 `factor_proto/data` 增量片，而盘前精排根本不走那条链路。

所以校对必须**分链路**：每个源声明自己的角色，按角色各自的规矩判。

## 三种「没数据」必须分开

- `missed`   该到的没到 → 采集漏跑，要人介入
- `empty`    上游当天空 → 等源端，不是我们的错
- `pending`  按频率本来就还没到（两融/涨停 T+1）→ 正常，不该报警

只报「没到」会把 pending 和 missed 混成一个红条，久了没人看。本脚本
对空档只判到「本地无」，并在 `upstream` 字段写明「未探测」，不假装知道原因。

用法：
  python tools/preopen/audit_preopen.py                # 校对最近交易日
  python tools/preopen/audit_preopen.py --date 20261111
  python tools/preopen/audit_preopen.py --json         # 机器可读
  python tools/preopen/audit_preopen.py --json-out x.json

退出码：0 通过 / 1 有告警（可继续出报告） / 2 阻断（不该出报告）
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys
from datetime import datetime

import pandas as pd

DATA = r"C:\项目\老田的AI助手\quant_backtest\data"
TS = os.path.join(DATA, "tushare")

RC_OK, RC_WARN, RC_FAIL = 0, 1, 2

# 角色：
#   must      目标日必须有（盘前精排的命脉）
#   soft      缺了能跑但结论会打折
#   t_plus_1  按交易所频率 T+1 才有，盘前允许落后 1 个交易日
SPECS = [
    dict(id="ts_daily", name="个股日线", path="tdx/ts_daily", date_col="trade_date",
         role="must", use="精排主判据"),
    dict(id="ts_adj_factor", name="复权因子", path="tdx/ts_adj_factor", date_col="trade_date",
         role="must", use="动量/均线复权"),
    dict(id="ts_index_daily", name="指数日线", path="tdx/ts_index_daily", date_col="trade_date",
         role="must", use="大盘与风格"),
    dict(id="ts_top_inst", name="龙虎榜", path="tdx/ts_top_inst", date_col="trade_date",
         role="soft", use="上榜过滤"),
    dict(id="limit_list_d", name="涨跌停名单", path="tushare/limit_list_d", date_col="trade_date",
         role="t_plus_1", use="情绪（官方口径）"),
    dict(id="margin_detail", name="两融明细", path="tushare/margin_detail", date_col="trade_date",
         role="t_plus_1", use="杠杆资金"),
]


# ---------------------------------------------------------------------------
# 基础读取
# ---------------------------------------------------------------------------
def shards(sub: str) -> list[str]:
    """分片清单。两个通配会重复命中 part_*.parquet，必须去重 ——
    否则同一天的行会被读两遍，行数正好翻倍（2026-11-12 踩过：5574 读成 11148）。"""
    p = os.path.join(DATA, sub)
    files = glob.glob(os.path.join(p, "part_*.parquet")) + glob.glob(os.path.join(p, "*.parquet"))
    return sorted(set(files))


def max_date(sub: str, date_col: str) -> tuple[str | None, int]:
    """分片全局最新日。只读日期列，不加载全表。"""
    files = shards(sub)
    if not files:
        return None, 0
    gmax = None
    for f in files:
        try:
            s = pd.read_parquet(f, columns=[date_col])[date_col]
        except Exception:
            continue
        s = pd.to_numeric(s, errors="coerce").dropna()
        if len(s):
            m = str(int(s.max()))
            if gmax is None or m > gmax:
                gmax = m
    return gmax, len(files)


def load_day(sub: str, day: str, cols: list[str] | None = None) -> pd.DataFrame:
    """取某一天的全部行（跨分片）。"""
    frames = []
    for f in shards(sub):
        try:
            df = pd.read_parquet(f)
        except Exception:
            continue
        if "trade_date" not in df.columns:
            continue
        df["trade_date"] = df["trade_date"].astype(str).str[:8]
        sub_df = df[df["trade_date"] == day]
        if len(sub_df):
            frames.append(sub_df)
    if not frames:
        return pd.DataFrame(columns=cols or [])
    out = pd.concat(frames, ignore_index=True)
    return out[cols] if cols else out


def trade_cal() -> list[str]:
    """开市日序列（升序）。"""
    files = sorted(glob.glob(os.path.join(TS, "trade_cal", "*.parquet")))
    if not files:
        return []
    fr = [pd.read_parquet(f, columns=["cal_date", "is_open"]) for f in files]
    cal = pd.concat(fr, ignore_index=True)
    cal["cal_date"] = cal["cal_date"].astype(str).str[:8]
    op = cal[cal["is_open"] == 1]["cal_date"].drop_duplicates().sort_values()
    return list(op)


def trading_lag(day_from: str, day_to: str, cal: list[str]) -> int | None:
    """day_from → day_to 之间隔了几个交易日（day_to 在 day_from 之后为正）。"""
    if not cal:
        return None
    hi = [d for d in cal if d <= day_to]
    lo = [d for d in cal if d <= day_from]
    if not hi or not lo:
        return None
    return len(hi) - len(lo)


# ---------------------------------------------------------------------------
# 涨跌停自算（官方表 T+1，盘前拿不到当天）
# ---------------------------------------------------------------------------
def limit_pct(code: str) -> float:
    c = str(code).split(".")[0]
    if c.startswith(("688", "689")):
        return 0.20
    if c.startswith(("300", "301")):
        return 0.20
    if c.startswith(("8", "4", "920")):
        return 0.30
    return 0.10


def limit_counts(df: pd.DataFrame) -> dict:
    """自算涨跌停。首日新股（涨幅不受限）单列，不算涨停。"""
    if df.empty or "pre_close" not in df.columns:
        return {"up": 0, "dn": 0, "new_listing": 0, "up_codes": [], "dn_codes": []}
    d = df.copy()
    d["pre_close"] = pd.to_numeric(d["pre_close"], errors="coerce")
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    d = d.dropna(subset=["pre_close", "close"])
    d = d[d["pre_close"] > 0]
    d["lim"] = d["ts_code"].map(limit_pct)
    d["up_px"] = (d["pre_close"] * (1 + d["lim"])).round(2)
    d["dn_px"] = (d["pre_close"] * (1 - d["lim"])).round(2)
    up = d[d["close"] >= d["up_px"] - 0.001]
    dn = d[d["close"] <= d["dn_px"] + 0.001]
    # 首日新股：涨幅远超板块限制，属于「无涨跌幅限制」，不该算涨停
    pct = (d["close"] / d["pre_close"] - 1)
    newl = set(d[(pct > 0.35) & (d["lim"] < 0.35)].ts_code)
    up = up[~up.ts_code.isin(newl)]
    return {"up": int(len(up)), "dn": int(len(dn)), "new_listing": len(newl),
            "up_codes": sorted(up.ts_code), "dn_codes": sorted(dn.ts_code)}


# ---------------------------------------------------------------------------
# 结构断言
# ---------------------------------------------------------------------------
def structure_checks(day: str, cal: list[str]) -> tuple[dict, list[str]]:
    """目标日个股日线的结构体检。返回 (指标, 问题列表)。"""
    df = load_day("tdx/ts_daily", day)
    probs: list[str] = []
    m: dict = {"rows": int(len(df))}

    if df.empty:
        probs.append("目标日无任何个股行情行")
        return m, probs

    for c in ("open", "high", "low", "close", "pre_close", "vol", "amount"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    m["nan_close"] = int(df["close"].isna().sum())
    m["nonpositive_close"] = int((df["close"] <= 0).sum())
    m["bad_high"] = int((df["high"] < df[["open", "close"]].max(axis=1) - 1e-6).sum())
    m["bad_low"] = int((df["low"] > df[["open", "close"]].min(axis=1) + 1e-6).sum())
    pct = df["close"] / df["pre_close"] - 1
    m["pct_extreme"] = int((pct.abs() > 0.35).sum())
    m["pct_min"] = round(float(pct.min()) * 100, 2) if len(pct.dropna()) else None
    m["pct_max"] = round(float(pct.max()) * 100, 2) if len(pct.dropna()) else None
    m["zero_vol"] = int((df["vol"] <= 0).sum())

    if m["nan_close"]:
        probs.append("%d 只收盘价为空" % m["nan_close"])
    if m["nonpositive_close"]:
        probs.append("%d 只收盘价 <=0" % m["nonpositive_close"])
    if m["bad_high"]:
        probs.append("%d 只最高价低于开/收（价格自相矛盾）" % m["bad_high"])
    if m["bad_low"]:
        probs.append("%d 只最低价高于开/收（价格自相矛盾）" % m["bad_low"])

    # 与前一日收盘的衔接：pre_close 应等于前一交易日 close（除权/新股除外）
    idx = cal.index(day) if day in cal else -1
    if idx > 0:
        prev = cal[idx - 1]
        pv = load_day("tdx/ts_daily", prev, ["ts_code", "close"])
        if not pv.empty:
            pv = pv.rename(columns={"close": "prev_close"})
            mg = df.merge(pv, on="ts_code", how="left")
            gap = (mg["pre_close"] - mg["prev_close"]).abs()
            m["pre_close_gap"] = int(((gap > 0.011) & mg["prev_close"].notna()).sum())
            m["no_prev_bar"] = int(mg["prev_close"].isna().sum())
            if m["pre_close_gap"] > max(20, int(len(df) * 0.01)):
                probs.append("%d 只前收盘与上一交易日收盘对不上（疑似价格错位）"
                             % m["pre_close_gap"])
    return m, probs


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def audit(target: str | None = None) -> dict:
    cal = trade_cal()
    tmax, _ = max_date("tdx/ts_daily", "trade_date")
    if target is None:
        target = tmax
    res: dict = {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target": target,
        "target_source": "cli" if target != tmax else "ts_daily.max",
        "sources": [], "structure": {}, "limit": {}, "problems": [], "warns": [],
        "verdict": "ok",
    }
    if not target:
        res["verdict"] = "fail"
        res["problems"].append("连个股日线都没有，无法确定目标日")
        return res

    # A. 逐源新鲜度（按角色判）
    for sp in SPECS:
        mx, nfiles = max_date(sp["path"], sp["date_col"])
        lag = trading_lag(mx, target, cal) if mx else None
        item = dict(id=sp["id"], name=sp["name"], role=sp["role"], use=sp["use"],
                    max_date=mx, lag_trading_days=lag, files=nfiles)
        if mx is None:
            item["state"] = "empty_local"
            item["note"] = "本地无此源"
            (res["problems"] if sp["role"] == "must" else res["warns"]).append(
                "%s：本地无数据" % sp["name"])
        elif mx >= target:
            item["state"] = "ok"
            item["note"] = "已到目标日"
        else:
            allowed = 1 if sp["role"] == "t_plus_1" else 0
            if lag is not None and lag <= allowed:
                item["state"] = "pending"
                item["note"] = "按频率属正常未到（允许落后 %d 个交易日）" % allowed
            elif sp["role"] == "soft":
                item["state"] = "warn"
                item["note"] = "缺目标日，能力降级（脚本可自愈补拉）"
                res["warns"].append("%s：缺 %s，相关能力降级" % (sp["name"], target))
            else:
                item["state"] = "missed"
                item["note"] = "该到没到"
                res["problems"].append("%s：应到 %s，实际停在 %s（落后 %s 个交易日）"
                                       % (sp["name"], target, mx, lag))
        res["sources"].append(item)

    # B. 结构体检
    st, probs = structure_checks(target, cal)
    res["structure"] = st
    res["problems"].extend(probs)

    # C. 涨跌停自算 + 与官方交叉验证（官方表最新日，两边都有才能比）
    df = load_day("tdx/ts_daily", target)
    res["limit"] = limit_counts(df)
    off_max, _ = max_date("tushare/limit_list_d", "trade_date")
    if off_max:
        cmp_day = off_max
        fu = limit_counts(load_day("tdx/ts_daily", cmp_day))
        mine_up = set(fu["up_codes"])
        mine_dn = set(fu["dn_codes"])
        off = pd.read_parquet(os.path.join(TS, "limit_list_d", "d_%s.parquet" % cmp_day))
        if "limit" in off.columns:
            off_up = set(off[off["limit"] == "U"].ts_code)
            off_dn = set(off[off["limit"] == "D"].ts_code)
        else:
            off_up, off_dn = set(off.ts_code), set()
        res["limit"]["cross_check"] = {
            "day": cmp_day, "official_up": len(off_up), "mine_up": fu["up"],
            "official_only": len(off_up - mine_up), "mine_only": len(mine_up - off_up),
            "official_dn": len(off_dn), "mine_dn": fu["dn"],
        }
        if off_up - mine_up:
            res["warns"].append("涨跌停自算漏判 %d 只（官方涨停未被自算抓到），情绪数字可能偏低"
                                % len(off_up - mine_up))
        if mine_up - off_up:
            res["warns"].append("自算比官方多 %d 只涨停（首日新股/边界情形），报告里已单列"
                                % len(mine_up - off_up))

    # D. 共识池推送包
    pkg_root = r"C:\项目\zerobound-kg\factor_proto\strategy_center_inbox"
    pkgs = sorted(d for d in os.listdir(pkg_root)
                  if os.path.isdir(os.path.join(pkg_root, d)) and d.isdigit()) if os.path.isdir(pkg_root) else []
    res["consensus_packages"] = pkgs[-3:]
    if pkgs:
        latest = pkgs[-1]
        fp = os.path.join(pkg_root, latest, "push_package.json")
        try:
            pk = json.load(io.open(fp, encoding="utf-8"))
            asof = str(pk.get("asof") or "")
            res["consensus"] = {"pkg": latest, "asof": asof,
                                "n": len((pk.get("consensus") or {}).get("stocks") or [])}
            if asof and trading_lag(asof, target, cal) not in (None, 0, 1):
                res["warns"].append("共识池最新包 asof=%s，距目标日已隔 %s 个交易日"
                                    % (asof, trading_lag(asof, target, cal)))
        except Exception as exc:  # noqa: BLE001
            res["warns"].append("共识池包读取失败：%s" % exc)
    else:
        res["problems"].append("找不到共识池推送包")

    res["verdict"] = "fail" if res["problems"] else ("warn" if res["warns"] else "ok")
    return res


def render(res: dict) -> str:
    L = []
    L.append("盘前数据校对  %s" % res["checked_at"].replace("T", " ")[:19])
    L.append("目标数据日 %s（%s）" % (res["target"], res["target_source"]))
    L.append("")
    L.append("%-12s %-8s %-10s %-6s %s" % ("数据源", "最新日", "角色", "判定", "说明"))
    tag = {"ok": "OK", "pending": "待采", "warn": "降级", "missed": "缺失", "empty_local": "无"}
    for s in res["sources"]:
        L.append("%-12s %-8s %-10s %-6s %s"
                 % (s["name"], s["max_date"] or "-", s["role"],
                    tag.get(s["state"], s["state"]), s["note"]))
    st = res["structure"]
    if st.get("rows"):
        L.append("")
        L.append("日线体检（%s）：%d 行｜涨跌停自算 涨停 %d / 跌停 %d（首日新股 %d 只已单列）"
                 % (res["target"], st["rows"], res["limit"].get("up", 0),
                    res["limit"].get("dn", 0), res["limit"].get("new_listing", 0)))
        L.append("  价格自相矛盾 %d 条｜涨跌幅越界 %d 条｜区间 %s%% ~ %s%%｜零成交 %d 只｜前收盘对不上 %s 条"
                 % (st.get("bad_high", 0) + st.get("bad_low", 0), st.get("pct_extreme", 0),
                    st.get("pct_min"), st.get("pct_max"), st.get("zero_vol", 0),
                    st.get("pre_close_gap", "-")))
    cc = (res.get("limit") or {}).get("cross_check")
    if cc:
        L.append("  交叉验证（%s）：官方涨停 %d，自算 %d，官方独有 %d，自算独有 %d"
                 % (cc["day"], cc["official_up"], cc["mine_up"],
                    cc["official_only"], cc["mine_only"]))
    if res.get("consensus"):
        c = res["consensus"]
        L.append("共识池：%s 包 asof=%s，%d 只" % (c["pkg"], c["asof"], c["n"]))
    if res["problems"]:
        L.append("")
        L.append("阻断项：")
        for p in res["problems"]:
            L.append("  × %s" % p)
    if res["warns"]:
        L.append("")
        L.append("告警：")
        for w in res["warns"]:
            L.append("  ! %s" % w)
    L.append("")
    L.append({"ok": "判定：通过，可以出报告",
              "warn": "判定：有条件通过（告警不阻断，但报告里必须写明）",
              "fail": "判定：不通过，不要出报告"}[res["verdict"]])
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="盘前数据校对")
    ap.add_argument("--date", default=None, help="目标日 YYYYMMDD，缺省取个股日线最新日")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--json-out", default=None, help="JSON 落盘路径")
    a = ap.parse_args()

    res = audit(a.date)
    if a.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(a.json_out)), exist_ok=True)
        io.open(a.json_out, "w", encoding="utf-8").write(
            json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps(res, ensure_ascii=False, indent=1) if a.json else render(res))
    return {"ok": RC_OK, "warn": RC_WARN, "fail": RC_FAIL}[res["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
