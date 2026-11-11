#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 product_registry.yaml 实测每个逻辑产物的新鲜度。

设计要点（与 catalog.freshness 的区别）：
  - lag 现场算，**不读也不写任何缓存值**。登记表只存契约，不存状态。
  - 一个逻辑产物可以由多片拼成，取各片 max(date) 的最大值，而不是看某一张表。
  - 成功判定是「数据前进了一天」，不是「进程退出 0」。

用法：
  python check_products.py                # 全量，人类可读
  python check_products.py --json         # 机器可读
  python check_products.py -v             # 打印每片的实测细节
  python check_products.py --only limit_up,sector_daily

退出码：0 全部在承诺内；1 有落后但仍在承诺内；2 有超出承诺的违约。
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys
import traceback
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
CFG = HERE / "product_registry.yaml"
STATE = HERE / "registry_state" / "latest.json"


# ── 基础 ────────────────────────────────────────────────────────
def d8(x) -> str:
    """任意日期表示 → 8 位字符串。"""
    s = str(x).strip()
    s = s.replace("-", "").replace("/", "").replace(" ", "")
    s = s.split(".")[0]
    return s[:8] if len(s) >= 8 and s[:8].isdigit() else ""


def wid(s: str) -> int:
    """显示宽度：中日韩全角算 2。"""
    w = 0
    for ch in str(s):
        w += 2 if ord(ch) > 0x2E80 else 1
    return w


def pad(s, n) -> str:
    s = str(s)
    return s + " " * max(0, n - wid(s))


def cut(s, n) -> str:
    """按显示宽度截断。按字符数截会把中文列顶歪（1 个汉字占 2 格）。"""
    s = str(s)
    out, w = "", 0
    for ch in s:
        cw = 2 if ord(ch) > 0x2E80 else 1
        if w + cw > n:
            return out + "…"
        out += ch
        w += cw
    return out


def resolve_extra() -> str | None:
    """外部数据根：环境变量 → 同目录 lens_paths.json → None。"""
    e = os.environ.get("ZQ_LENS_DATA")
    if e and os.path.isdir(e):
        return e
    cfg = HERE / "lens_paths.json"
    if cfg.exists():
        try:
            p = json.load(io.open(cfg, encoding="utf-8")).get("extra_data")
            if p and os.path.isdir(str(p)):
                return str(p)
        except Exception:
            pass
    return None


def expand(tmpl: str, roots: dict) -> str:
    """展开 ${extra} / ${project}，并解析相对路径。"""
    s = tmpl
    for k, v in roots.items():
        s = s.replace("${%s}" % k, v or "")
    if "${" in s:
        return ""          # 还有没解析的占位符 = 根目录未配置
    if not os.path.isabs(s):
        s = os.path.abspath(os.path.join(str(HERE.parent.parent), s))
    return s


def nrows(path: str) -> int:
    try:
        import pyarrow.parquet as pq
        return pq.ParquetFile(path).metadata.num_rows
    except Exception:
        try:
            import pandas as pd
            return len(pd.read_parquet(path))
        except Exception:
            return -1


# ── 单片探测 ─────────────────────────────────────────────────────
def probe_slice(pat: str, date_from: str, min_rows: int = 0, verbose=False) -> dict:
    """返回 {path, max, rows_at_max, n_files, err}。"""
    r = {"path": pat, "max": None, "rows_at_max": 0, "n_files": 0, "err": None}
    if not pat:
        r["err"] = "root-not-configured"
        return r
    mode, _, arg = str(date_from).partition(":")
    files = sorted(glob.glob(pat)) if any(c in pat for c in "*?[") else ([pat] if os.path.exists(pat) else [])

    if mode == "filename":
        if not files:
            r["err"] = "no-file"
            return r
        r["n_files"] = len(files)
        ds = []
        for f in files:
            c = "".join(ch for ch in os.path.basename(f) if ch.isdigit())[:8]
            if len(c) == 8:
                ds.append((c, f))
        if not ds:
            r["err"] = "no-date-in-filename"
            return r
        ds.sort()
        for d, f in reversed(ds):
            n = nrows(f)
            if n >= min_rows:
                r["max"], r["rows_at_max"] = d, n
                return r
            if verbose:
                print("    [skip] %s rows=%d < min_rows=%d" % (d, n, min_rows))
        r["err"] = "all-below-min-rows"
        r["max"], r["rows_at_max"] = ds[-1][0], nrows(ds[-1][1])
        return r

    if mode == "column":
        if not files:
            r["err"] = "no-file"
            return r
        r["n_files"] = len(files)
        import pandas as pd
        best, bestn = "", 0
        for f in files:
            df = pd.read_parquet(f, columns=[arg])
            v = df[arg].map(d8)
            v = v[v.str.len() == 8]
            if v.empty:
                continue
            mx = v.max()
            if mx > best:
                best, bestn = mx, int((v == mx).sum())
        if not best:
            r["err"] = "no-valid-date"
            return r
        r["max"], r["rows_at_max"] = best, bestn
        if min_rows and bestn < min_rows:
            r["err"] = "below-min-rows"
        return r

    if mode == "json":
        if not files:
            r["err"] = "no-file"
            return r
        r["n_files"] = len(files)
        best, cnt = "", 0
        for f in files:
            try:
                o = json.load(io.open(f, encoding="utf-8"))
            except Exception:
                continue
            v = d8(o.get(arg, ""))
            if not v:
                continue
            if v > best:
                best, cnt = v, 1
            elif v == best:
                cnt += 1
        if not best:
            r["err"] = "no-valid-date"
            return r
        r["max"], r["rows_at_max"] = best, cnt
        return r

    r["err"] = "bad-date_from:%s" % date_from
    return r


# ── 日历 ────────────────────────────────────────────────────────
def build_calendar(prod: dict, roots: dict) -> list[str]:
    """用 clock 产物的文件名全集当日历：行情有哪些日就是哪些交易日。"""
    ds = set()
    for sl in prod.get("compose") or []:
        if str(sl.get("date_from")) != "filename":
            continue
        p = expand(sl["path"], roots)
        for f in glob.glob(p) if any(c in p for c in "*?[") else []:
            c = "".join(ch for ch in os.path.basename(f) if ch.isdigit())[:8]
            if len(c) == 8:
                ds.add(c)
    return sorted(ds)


# ── 自检（变异检验） ────────────────────────────────────────────
def self_test(cfg: dict, roots: dict) -> int:
    """把「多片合并」拆掉，断言判定必须变红。

    拆掉后判定不变，说明合并逻辑只是装饰、没真正起作用——那这层登记就白建了。
    2026-11-12 判例：只看 full 片 lag=20 判违约；三片合并 lag=1 判 OK。
    """
    prods = {p["id"]: p for p in cfg["products"]}
    cal = build_calendar(prods["quote_daily"], roots)
    if not cal:
        print("自检无法进行：clock 产物取不到日期")
        return 2
    asof = cal[-1]
    p = prods["limit_up"]

    def eval_slices(slices):
        got = [probe_slice(expand(sl["path"], roots), sl.get("date_from", "")) for sl in slices]
        good = [s for s in got if s["max"]]
        if not good:
            return None, None
        b = max(good, key=lambda s: s["max"])
        return b["max"], cal.index(asof) - cal.index(b["max"]) if b["max"] in cal else None

    print("自检（变异检验）：数据日 %s" % asof)
    checks = []
    full_only = [sl for sl in p["compose"] if "full" in sl["path"]]
    if full_only:
        d1, l1 = eval_slices(full_only)
        checks.append(("拆掉增量片，只看历史底", d1, l1,
                       l1 is not None and l1 >= 10, "应判违约（落后 ≥10）"))
    d2, l2 = eval_slices(p["compose"])
    checks.append(("三片合并（当前配置）", d2, l2,
                   l2 is not None and l2 <= 1, "应判 OK（落后 ≤1）"))

    ok = True
    for name, d, lag, cond, expect in checks:
        if not cond:
            ok = False
        print("  [%s] %-24s 实测 %s 落后 %s —— %s" % ("PASS" if cond else "FAIL", name, d, lag, expect))
    if not ok:
        print("自检失败：多片合并没有产生预期差异，compose 配置需复核")
        return 2
    print("自检通过：拆掉增量片后判定由 OK 变违约，证明合并逻辑真的在起作用")
    return 0


# ── 主流程 ───────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--self-test", action="store_true", dest="self_test")
    a = ap.parse_args()

    cfg = yaml.safe_load(io.open(CFG, encoding="utf-8"))
    extra = resolve_extra()
    roots = {"project": "", "extra": extra or ""}
    if a.self_test:
        return self_test(cfg, roots)
    prods = {p["id"]: p for p in cfg["products"]}

    clock_id = cfg.get("clock")
    if clock_id not in prods:
        print("registry 错误：clock=%r 不在 products 里" % clock_id)
        return 2
    cal = build_calendar(prods[clock_id], roots)
    if not cal:
        print("registry 错误：clock 产物 %s 取不到任何日期，无法算 lag" % clock_id)
        return 2
    asof = cal[-1]

    only = {x.strip() for x in a.only.split(",") if x.strip()}
    rows, breaches, lags = [], [], []
    for pid, p in prods.items():
        if only and pid not in only:
            continue
        try:
            slices = []
            for sl in p.get("compose") or []:
                path = expand(sl["path"], roots)
                s = probe_slice(path, sl.get("date_from", ""),
                                int(sl.get("min_rows_per_day") or 0), a.verbose)
                s["role"] = sl.get("role", "")
                slices.append(s)
                if a.verbose:
                    print("  [slice] %-52s max=%-10s rows=%-7s n=%-4s err=%s"
                          % (os.path.basename(s["path"]) or s["path"][:50],
                             s["max"], s["rows_at_max"], s["n_files"], s["err"]))
            good = [s for s in slices if s["max"]]
            if not good:
                rows.append({"id": pid, "name": p["name"], "date": None, "lag": None,
                             "promise": p["promise"], "status": "missing",
                             "slices": slices, "writer": p.get("writer")})
                continue
            best = max(good, key=lambda s: s["max"])
            date = best["max"]
            lag = (cal.index(asof) - cal.index(date)) if date in cal else None
            ok_lag = int(p["promise"].get("lag_ok") or 0)
            if date == asof or (lag is not None and 0 <= lag <= ok_lag):
                st = "ok"
            elif lag is None:
                st = "breach"
            else:
                st = "lag" if lag <= ok_lag + 5 else "breach"
            if st == "lag":
                lags.append(pid)
            if st == "breach":
                breaches.append(pid)
            rows.append({"id": pid, "name": p["name"], "date": date, "lag": lag,
                         "promise": p["promise"], "status": st,
                         "from": best["path"], "role": best["role"],
                         "slices": slices, "writer": p.get("writer"),
                         "success": p.get("success"), "history": p.get("history")})
        except Exception as e:
            rows.append({"id": pid, "name": p["name"], "date": None, "lag": None,
                         "promise": p.get("promise") or {}, "status": "error",
                         "err": "%s: %s" % (type(e).__name__, str(e)[:80]),
                         "detail": traceback.format_exc()[-400:]})

    # ── 落盘（供页面/巡检消费；只存本次实测，不存历史结论） ──
    payload = {"asof": asof, "generated_at": None, "extra_configured": bool(extra),
               "rows": [{k: v for k, v in r.items() if k != "slices"} for r in rows],
               "breaches": breaches}
    try:
        import datetime
        payload["generated_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass

    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 2 if breaches else (1 if lags else 0)

    # ── 人类可读 ──
    CADE = {"daily": "每日", "daily_t1": "T+1", "weekly": "每周", "oneoff": "一次性"}
    STX = {"ok": "OK", "lag": "落后", "breach": "违约", "missing": "缺失", "error": "异常"}
    print("=" * 78)
    print("逻辑产物新鲜度  数据日 %s（clock=%s，交易日 %d 个）" % (asof, clock_id, len(cal)))
    if not extra:
        print("注意：外部数据根未配置（ZQ_LENS_DATA / lens_paths.json），extra 相关产物会缺片")
    print("=" * 78)
    print("%s%s%s%s%s%s" % (pad("产物", 14), pad("承诺", 7), pad("实测", 11),
                            pad("落后", 6), pad("判定", 6), "写方"))
    print("-" * 78)
    for r in rows:
        if r["status"] in ("missing", "error"):
            print("%s%s%s%s%s%s" % (pad(r["name"], 14), pad(CADE.get(r["promise"].get("cadence"), "?"), 7),
                                    pad("—", 11), pad("—", 6), pad(STX[r["status"]], 6),
                                    cut(r.get("err") or "所有片都取不到日期", 40)))
            continue
        lagtxt = "—" if r["lag"] is None else str(r["lag"])
        print("%s%s%s%s%s%s" % (pad(r["name"], 14),
                                pad(CADE.get(r["promise"].get("cadence"), "?"), 7),
                                pad(r["date"], 11), pad(lagtxt, 6),
                                pad(STX[r["status"]], 6), cut(r.get("writer") or "", 34)))
    print("-" * 78)
    print("合计 %d 个产物：OK %d / 落后 %d / 违约 %d / 缺失或异常 %d" % (
        len(rows), sum(1 for r in rows if r["status"] == "ok"), len(lags), len(breaches),
        sum(1 for r in rows if r["status"] in ("missing", "error"))))

    if breaches:
        print()
        print("违约明细（超出承诺节奏，需处置）：")
        for r in rows:
            if r["status"] != "breach":
                continue
            print("  · %s  实测 %s，落后 %s 个交易日，承诺落后 ≤%s 个交易日" % (
                r["name"], r["date"], r["lag"], r["promise"].get("lag_ok")))
            print("    写方：%s" % (r.get("writer") or "未登记"))
            if r.get("success"):
                print("    成功口径：%s" % r["success"])
            if r.get("history"):
                print("    已知成因：%s" % " ".join(str(r["history"]).split())[:150])
    if a.verbose:
        print()
        print("每片细节：")
        for r in rows:
            if "slices" not in r:
                continue
            print("  %s" % r["name"])
            for s in r["slices"]:
                print("    %-50s max=%-10s rows=%-8s files=%-4s %s" % (
                    os.path.basename(s["path"]) or "(未解析)",
                    s["max"], s["rows_at_max"], s["n_files"], s["err"] or ""))
    print()
    print("状态已写：%s" % STATE)
    return 2 if breaches else (1 if lags else 0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
