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
import bisect
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


def dperiod(x) -> str:
    """期次表示 → 定长可比较串。月=202608、季=2026Q3、年=2026。

    周期类表（CPI/PMI/GDP/财政面板）的日期列不是「哪一天」，是「哪一期」。
    用 d8() 去解 202608 会得到空串，于是被判成「取不到日期」——
    第一版 11 条里 6 条是这么错的。
    """
    s = str(x).strip().upper()
    if not s or s in ("NAN", "NONE", "NAT", ""):
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if "Q" in s and len(digits) >= 5:            # 2026Q3 / 2026 Q3
        y, q = digits[:4], digits[4:]
        return "%sQ%s" % (y, q[0]) if q else ""
    if len(digits) == 8:                          # 20260831 → 归到月
        return digits[:6]
    if len(digits) == 6:                          # 202608
        return digits
    if len(digits) == 4:                          # 2026
        return digits
    if len(digits) == 5:                          # 20263 → 2026 年第 3 期
        return "%s%s" % (digits[:4], digits[4:])
    return ""


def period_lag(latest: str, asof: str, cadence: str) -> int | None:
    """期次落后几期。单位由**承诺节奏**决定，不由日期字符串长相决定。

    同一个 20260930，在三季报表里是「季度期次」，在日频表里就只是普通日期。
    第一版按字符串长度推单位，季度财报会被算成月频（差 3 倍）。
    """
    if not latest or not asof:
        return None
    s = str(latest).upper()
    d = "".join(ch for ch in s if ch.isdigit())
    ay = int(asof[:4])
    am = int(asof[4:6])
    cad = (cadence or "monthly")[:1].upper()

    if cad == "Y":
        y = int(d[:4]) if len(d) >= 4 else None
        return None if y is None else ay - y

    if "Q" in s and len(d) >= 5:                 # 2026Q3
        y, q = int(d[:4]), int(d[4])
    elif len(d) == 8:                            # 20260930 → 2026 年 3 季
        y, q = int(d[:4]), (int(d[4:6]) - 1) // 3 + 1
    elif len(d) == 6:                            # 202609
        y, q = int(d[:4]), (int(d[4:6]) - 1) // 3 + 1
    elif len(d) == 4:                            # 2026
        y, q = int(d), 4
    else:
        return None

    if cad == "Q":
        return (ay - y) * 4 + ((am - 1) // 3 + 1 - q)
    if cad == "S":
        # 半年频：港股只有半年报/年报，拿季度去卡必然常年「落后 2 季」。
        ah = 1 if am <= 6 else 2
        lh = 1 if q <= 2 else 2
        return (ay - y) * 2 + (ah - lh)
    return (ay - y) * 12 + (am - q * 3)           # M：q*3 是该季末月


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


def today8() -> str:
    import datetime
    return datetime.date.today().strftime("%Y%m%d")


def resolve_ack(p: dict) -> tuple[bool, str]:
    """已知违约登记是否仍在有效期内。

    为什么要有这个（2026-11-12 立）：板块上游断供是既成事实、已有处置在推进，
    但如果它每天都记「违约 → 告警」，告警就会变成背景噪声，真正的新违约会被淹掉。
    所以允许登记，但**必须带到期日**——到期后自动恢复成硬违约。
    不带到期日的登记等于永久静音，一律拒绝（返回 False 并说明）。
    """
    a = p.get("ack") or {}
    if not a:
        return False, ""
    until = d8(a.get("until"))
    if not until:
        return False, "ack 缺 until（必须带到期日，否则等于永久静音）"
    if until < today8():
        return False, "登记已于 %s 到期，恢复硬违约" % until
    return True, "已登记至 %s（%s）" % (until, a.get("reason") or "")


def lag_ok_trading(p: dict, asof: str, cal: list[str]) -> int:
    """把承诺的允许落后统一折成「交易日」，避免两种单位混在同一张表里。

    derived 那层的 lag_ok 来自 sources.yaml 的 freshness_sla_days，单位是**自然日**
    （D:3 W:10 M:35 Q:150）。若直接跟交易日 lag 比，同一行里分子分母单位不同，
    Q 表会凭空多出 1.4 倍余量（150 自然日 ≈ 103 交易日）。
    所以按日历折算：从 asof 往回数 N 个自然日，落在窗口内的交易日个数就是阈值。
    """
    pr = p.get("promise") or {}
    ok = int(pr.get("lag_ok") or 0)
    if str(pr.get("basis") or "trading") != "calendar":
        return ok
    import datetime
    try:
        cut = (datetime.date(int(asof[:4]), int(asof[4:6]), int(asof[6:8]))
               - datetime.timedelta(days=ok)).strftime("%Y%m%d")
    except Exception:
        return ok
    return sum(1 for x in cal if x > cut)


def load_products(cfg: dict, scope: str) -> dict:
    """curated = 手写契约层（6 条）；all = 再加 catalog 生成的物理表层（215 条）。

    两层分开加载、分层报告：契约层逐行看，物理表层只列异常。
    原因是 259 行逐行看等于没看，人会直接跳到末尾那一行合计。
    """
    prods = {p["id"]: p for p in cfg["products"]}
    if scope not in ("all", "catalog"):
        return prods
    cf = HERE / "product_registry_catalog.yaml"
    if not cf.exists():
        print("提示：product_registry_catalog.yaml 不存在，先跑 gen_registry_catalog.py")
        return prods
    d = yaml.safe_load(io.open(cf, encoding="utf-8")) or {}
    for p in d.get("products") or []:
        prods.setdefault(p["id"], p)   # 契约层优先，生成的不得覆盖手写的
    return prods


def parquet_maxdate(path: str, col: str) -> str | None:
    """从 parquet 行组统计里直接取该列最大值，不读数据。

    为什么不直接 read_parquet：catalog 那层 215 个产物里有 193 个是 part 分片，
    有的表 55 片、单片几十万行。逐片读日期列实测把一次全量检查拖到十几分钟以上，
    nightly 任务等不起。行组 statistics 有 min/max，元数据就能给答案。
    有任何一片缺统计（或统计不含该列）就返回 None，调用方回退到真读——不猜。
    """
    try:
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(path)
        idx = None
        for i, name in enumerate(pf.schema_arrow.names):
            if name == col:
                idx = i
                break
        if idx is None:
            return None
        best = ""
        md = pf.metadata
        for g in range(md.num_row_groups):
            st = md.row_group(g).column(idx).statistics
            if st is None or st.max is None:
                return None
            v = d8(st.max)
            if v and v > best:
                best = v
        return best or None
    except Exception:
        return None


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
def classify_missing(slices: list) -> str:
    """「取不到日期」要分成五种，处置完全不同。

    第一版一律叫 missing，16 条里 12 条其实是我自己的口径错
    （周期列解析不了、目录空、格式换了、列名对不上），看着像数据没了。
    """
    errs = [str(s.get("err") or "") for s in slices]
    if any(e.startswith("format-mismatch") for e in errs):
        return "format"          # 目录里有文件，但不是契约写的格式
    if any(e == "empty-store" for e in errs):
        return "empty"           # 目录在、0 个文件：写方从未落盘
    if any(e.startswith("col-not-in-file") for e in errs):
        return "colmissing"      # 文件在，但 date_col 不在里面
    if all(e == "root-not-configured" for e in errs) and errs:
        return "missing"         # 外部数据根没配
    return "missing"


def first_err(slices: list) -> str:
    for s in slices:
        if s.get("err"):
            return str(s["err"])
    return ""


def probe_slice(pat: str, date_from: str, min_rows: int = 0, verbose=False,
                role: str = "observation") -> dict:
    """返回 {path, max, rows_at_max, n_files, err}。

    role 决定按什么口径解析日期：period 用 dperiod（202608 / 2026Q3），
    其余用 d8。第一版不分口径，11 条周期表被判成「取不到日期」。
    """
    r = {"path": pat, "max": None, "rows_at_max": 0, "n_files": 0, "err": None}
    if not pat:
        r["err"] = "root-not-configured"
        return r
    parse = dperiod if role == "period" else d8
    mode, _, arg = str(date_from).partition(":")
    files = sorted(glob.glob(pat)) if any(c in pat for c in "*?[") else ([pat] if os.path.exists(pat) else [])

    # 「一个文件都没有」要分三种，它们的处置完全不同：
    #   empty-store   目录在但空 → 写方从未落盘，是真问题
    #   format-mismatch 目录里有东西但不是这个格式 → 契约写错或产物换格式，不是没数据
    #   no-file       目录都不在 → 路径错 / 外部数据根没配
    if not files:
        d = os.path.dirname(pat)
        if os.path.isdir(d):
            got = sorted(os.listdir(d))
            if got:
                r["err"] = "format-mismatch:%s" % ",".join(got[:3])
            else:
                r["err"] = "empty-store"
        else:
            r["err"] = "no-file"
        return r

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
        # 快路径：只读行组统计取 max，不读数据（物理表层 193 个分片表全靠它）
        if not min_rows:
            best, nostat = "", 0
            if len(files) <= 8:
                for f in files:
                    v = parquet_maxdate(f, arg)
                    if v and v > best:
                        best = v
                    elif not v:
                        nostat += 1
            else:
                # by_code 一类是「一只票一个文件」，实测有的表 5569 个文件。
                # 串行开 5569 次元数据 ≈ 5 秒/表 × 27 张 = 两三分钟，nightly 等不起。
                # 元数据读取是 IO 密集且互相独立，开 8 线程即可，判定口径不变
                # （仍然是「每个文件都看」，不是抽样——抽样会把落后读成正常）。
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=8) as ex:
                    for v in ex.map(lambda f: parquet_maxdate(f, arg), files):
                        if v and v > best:
                            best = v
                        elif not v:
                            nostat += 1
            if best:
                # 期次口径要把 8 位日折回期次（财报的 end_date 20260930 → 2026 三季报）。
                # 折的是「最大值本身」，不是把每行都转型，所以不影响「取最大」的正确性。
                r["max"], r["rows_at_max"] = (dperiod(best) if role == "period" else best), -1
                # 有分片取不到统计就必须说出来：静默跳过 = 变相抽样，
                # 正是「只看一片 max(date)」的反面错误。
                if nostat:
                    r["skipped"] = ["%d 片无行组统计" % nostat]
                return r
        # 慢路径：需要行数门槛（契约层那两条），或统计不可用，只能真读
        import pandas as pd
        best, bestn, skip = "", 0, []
        for f in files:
            try:
                df = pd.read_parquet(f, columns=[arg])
            except Exception as e:
                # 单个分片缺列不等于这张表没数据。实测 czyx 的 6 片里
                # part_0005 没有 year 列（26 列 vs 其他 17~35 列），
                # 原先是「读到第一个异常就整片作废」，整张表被判成「取不到日期」。
                # 正确语义：跳过坏片、用其余片给答案，并如实记下跳了几片。
                skip.append("%s(%s)" % (os.path.basename(f), type(e).__name__))
                continue
            v = df[arg].map(parse)
            v = v[v.str.len() > 0]
            if v.empty:
                continue
            mx = v.max()
            if mx > best:
                best, bestn = mx, int((v == mx).sum())
        if not best:
            # 全片都失败才算「取不到日期」；部分失败只降级为提示，判定照常给。
            r["err"] = ("col-not-in-file:%d/%d 片缺列" % (len(skip), len(files))) if skip \
                else "no-valid-date"
            return r
        r["max"], r["rows_at_max"] = best, bestn
        if skip:
            r["skipped"] = skip
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

    # ack 变异检验：登记的到期日必须真的能「失效」。
    # 只验「现在是 acked」不够——那可能只是因为 ack 字段存在而恒真。
    # 必须证伪：把 until 挪到过去，判定必须从「已登记」落回「硬违约」。
    ack_live, _ = resolve_ack({"ack": {"until": "29991231", "reason": "自检"}})
    ack_dead, note_dead = resolve_ack({"ack": {"until": "20200101", "reason": "自检"}})
    ack_none, note_none = resolve_ack({"ack": {"reason": "缺 until"}})
    checks2 = [
        ("ack 未来日期 → 生效", ack_live is True, "应 True"),
        ("ack 过期 → 失效", ack_dead is False and "到期" in note_dead, "应 False 并说明到期"),
        ("ack 缺 until → 拒绝", ack_none is False and "until" in note_none, "应 False 并说明缺 until"),
    ]
    for name, cond, expect in checks2:
        if not cond:
            ok = False
        print("  [%s] %-24s %s" % ("PASS" if cond else "FAIL", name, expect))
    if not ok:
        print("自检失败：上面的预期没有全部成立，compose / ack 逻辑需复核")
        return 2
    print("自检通过：拆掉增量片后判定由 OK 变违约（合并逻辑真在起作用）；"
          "ack 到期后能落回硬违约（登记不是永久静音）")
    return 0


# ── 主流程 ───────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--self-test", action="store_true", dest="self_test")
    ap.add_argument("--scope", default="curated", choices=["curated", "all", "catalog"],
                    help="curated=手写契约层；all=再加 catalog 生成的物理表层；catalog=只跑物理表层")
    ap.add_argument("--all-rows", action="store_true", dest="all_rows",
                    help="物理表层默认只列异常行，加此参数强制全列")
    a = ap.parse_args()

    cfg = yaml.safe_load(io.open(CFG, encoding="utf-8"))
    extra = resolve_extra()
    roots = {"project": "", "extra": extra or ""}
    if a.self_test:
        return self_test(cfg, roots)
    prods = load_products(cfg, a.scope)
    if a.scope == "catalog":
        prods = {k: v for k, v in prods.items() if v.get("derived")}

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
    rows, breaches, lags, acks = [], [], [], []
    for pid, p in prods.items():
        if only and pid not in only:
            continue
        try:
            slices = []
            for sl in p.get("compose") or []:
                path = expand(sl["path"], roots)
                role = sl.get("date_role") or "observation"
                s = probe_slice(path, sl.get("date_from", ""),
                                int(sl.get("min_rows_per_day") or 0), a.verbose, role)
                s["role"] = sl.get("role", "")
                s["date_role"] = role
                slices.append(s)
                if a.verbose:
                    print("  [slice] %-52s max=%-10s rows=%-7s n=%-4s role=%-12s err=%s"
                          % (os.path.basename(s["path"]) or s["path"][:50],
                             s["max"], s["rows_at_max"], s["n_files"], role, s["err"]))
            good = [s for s in slices if s["max"]]
            if not good:
                rows.append({"id": pid, "name": p["name"], "date": None, "lag": None,
                             "promise": p["promise"], "status": classify_missing(slices),
                             "err": first_err(slices), "slices": slices, "writer": p.get("writer")})
                continue
            best = max(good, key=lambda s: s["max"])
            date = best["max"]
            drole = best.get("date_role") or "observation"
            ok_lag = lag_ok_trading(p, asof, cal)
            # 不能用「date 必须在日历里」来算 lag：周线/月线的日期是周六或月末，
            # 永远不在交易日历里，于是 lag=None，全被读成违约且打印「落后 None 天」。
            # 换成「日历里比它晚的交易日有几个」，对任何日期都成立。
            lag = (len(cal) - bisect.bisect_right(cal, date)) if date else None

            if drole in ("schedule", "horizon"):
                # 计划端点 / 覆盖未来的日历：本身允许落在未来，判新鲜度没有意义。
                st, lag = "na", None
            elif drole == "period":
                # 期次落后按期数算。月度 lag=1（10 月数据在 11 月拿到）是正常节奏。
                # 承诺也换算成期数：lag_ok 是自然日（M:35/Q:150/Y:400），
                # 直接拿去和「落后 3 期」并列会印出「承诺落后 ≤266 期」这种荒唐话。
                lag = period_lag(date, asof, str(p["promise"].get("cadence") or "monthly"))
                okp = max(1, int(ok_lag // {"M": 30, "Q": 91, "S": 182, "Y": 365}.get(
                    str(p["promise"].get("cadence", "monthly")).upper()[:1], 30)))
                ok_lag = okp
                if lag is None:
                    st = "breach"
                elif lag <= okp:
                    st = "ok"
                elif lag <= okp + 2:
                    st = "lag"
                else:
                    st = "breach"
            elif date == asof or (lag is not None and 0 <= lag <= ok_lag):
                st = "ok"
            elif date and asof and date > asof:
                # 数据日期晚于行情钟（实测 bak_basic 到 20261112，而 daily 只到 20261111）。
                # 这不是违约：有些接口当日就能给，行情钟还没跟上。
                # 但也不能算 ok —— 它说明「两张表不同步」，要单独看见。
                st = "ahead"
            elif lag is None:
                st = "breach"
            else:
                st = "lag" if lag <= ok_lag + 5 else "breach"
            if drole == "period":
                lag_txt = lag
            elif date and asof and date > asof:
                lag_txt = None
            else:
                lag_txt = lag

            # 违约但已登记（带到期日）→ acked：仍然显示，但不再每天告警。
            # 到期或登记不合规（缺 until）→ 落回 breach，照常告警。
            note = ""
            if st == "breach":
                acked, note = resolve_ack(p)
                if acked:
                    st = "acked"
                    acks.append(pid)
                else:
                    breaches.append(pid)
            if st == "lag":
                lags.append(pid)
            rows.append({"id": pid, "name": p["name"], "date": date, "lag": lag_txt,
                         "promise": p["promise"], "status": st, "date_role": drole,
                         "from": best["path"], "role": best["role"],
                         "slices": slices, "writer": p.get("writer"),
                         "success": p.get("success"), "history": p.get("history"),
                         "ok_lag": ok_lag, "derived": bool(p.get("derived")),
                         "ack_note": note})
        except Exception as e:
            rows.append({"id": pid, "name": p["name"], "date": None, "lag": None,
                         "promise": p.get("promise") or {}, "status": "error",
                         "err": "%s: %s" % (type(e).__name__, str(e)[:80]),
                         "detail": traceback.format_exc()[-400:]})

    # ── 落盘（供页面/巡检消费；只存本次实测，不存历史结论） ──
    payload = {"asof": asof, "generated_at": None, "extra_configured": bool(extra),
               "scope": a.scope,
               "rows": [{k: v for k, v in r.items() if k != "slices"} for r in rows],
               "breaches": breaches, "acked": acks}
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
    CADE = {"daily": "每日", "daily_t1": "T+1", "weekly": "每周", "monthly": "每月",
            "semiannual": "每半年",
            "quarterly": "每季", "yearly": "每年", "oneoff": "一次性"}
    STX = {"ok": "OK", "lag": "落后", "breach": "违约", "acked": "已登记",
           "ahead": "超前", "missing": "缺失", "error": "异常",
           "empty": "空目录", "format": "格式不符", "colmissing": "无日期列",
           "na": "不判定"}
    NODATE = ("missing", "error", "empty", "format", "colmissing")
    # 物理表层 200+ 行逐行看等于没看，默认只列异常；契约层永远全列。
    show = rows
    hidden = 0
    if a.scope != "curated" and not a.all_rows:
        show = [r for r in rows if r["status"] != "ok" or not r.get("derived")]
        hidden = len(rows) - len(show)
    print("=" * 78)
    print("逻辑产物新鲜度  数据日 %s（clock=%s，交易日 %d 个，范围 %s）"
          % (asof, clock_id, len(cal), a.scope))
    if not extra:
        print("注意：外部数据根未配置（ZQ_LENS_DATA / lens_paths.json），extra 相关产物会缺片")
    print("=" * 78)
    print("%s%s%s%s%s%s" % (pad("产物", 14), pad("承诺", 7), pad("实测", 11),
                            pad("落后", 6), pad("判定", 8), "写方"))
    print("-" * 80)
    for r in show:
        if r["status"] in NODATE:
            print("%s%s%s%s%s%s" % (pad(r["name"], 14), pad(CADE.get(r["promise"].get("cadence"), "?"), 7),
                                    pad("—", 11), pad("—", 6), pad(STX[r["status"]], 8),
                                    cut(r.get("err") or "所有片都取不到日期", 40)))
            continue
        lagtxt = "—" if r["lag"] is None else str(r["lag"])
        print("%s%s%s%s%s%s" % (pad(r["name"], 14),
                                pad(CADE.get(r["promise"].get("cadence"), "?"), 7),
                                pad(r["date"], 11), pad(lagtxt, 6),
                                pad(STX[r["status"]], 8), cut(r.get("writer") or "", 34)))
    print("-" * 80)
    print("合计 %d 个产物：OK %d / 落后 %d / 违约 %d / 已登记 %d / 超前 %d / "
          "缺失异常 %d / 不判定 %d" % (
        len(rows), sum(1 for r in rows if r["status"] == "ok"), len(lags), len(breaches),
        len(acks), sum(1 for r in rows if r["status"] == "ahead"),
        sum(1 for r in rows if r["status"] in NODATE),
        sum(1 for r in rows if r["status"] == "na")))
    if hidden:
        print("（另有 %d 行物理表层产物在承诺内，未逐行列出；--all-rows 可全列）" % hidden)

    if breaches:
        print()
        print("违约明细（超出承诺节奏，需处置）：")
        for r in rows:
            if r["status"] != "breach":
                continue
            unit = "期" if r.get("date_role") == "period" else "个交易日"
            print("  · %s  实测 %s，落后 %s %s，承诺落后 ≤%s %s" % (
                r["name"], r["date"], r["lag"], unit, r.get("ok_lag"),
                "期" if r.get("date_role") == "period" else "个交易日"))
            print("    写方：%s" % (r.get("writer") or "未登记"))
            if r.get("success"):
                print("    成功口径：%s" % r["success"])
            if r.get("history"):
                print("    已知成因：%s" % " ".join(str(r["history"]).split())[:150])
    if acks:
        print()
        print("已登记违约（有处置在推进，到期自动恢复告警；不是已解决）：")
        for r in rows:
            if r["status"] != "acked":
                continue
            print("  · %s  实测 %s，落后 %s %s —— %s" % (
                r["name"], r["date"], r["lag"],
                "期" if r.get("date_role") == "period" else "个交易日",
                r.get("ack_note") or ""))
    if a.verbose:
        print()
        print("每片细节：")
        for r in rows:
            if "slices" not in r:
                continue
            print("  %s" % r["name"])
            for s in r["slices"]:
                print("    %-50s max=%-10s rows=%-8s files=%-4s %s %s" % (
                    os.path.basename(s["path"]) or "(未解析)",
                    s["max"], s["rows_at_max"], s["n_files"], s["err"] or "",
                    ("跳过：" + "、".join(map(str, s["skipped"]))) if s.get("skipped") else ""))
    print()
    print("状态已写：%s" % STATE)
    return 2 if breaches else (1 if lags else 0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
