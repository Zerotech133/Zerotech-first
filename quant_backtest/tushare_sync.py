# -*- coding: utf-8 -*-
"""Tushare Pro 数据中台同步器（token=.env.local XT_DATA_KEY）。

落库布局：
  data/tushare/<table>/part_NNNN.parquet            浅表全量分片
  data/tushare/<table>/<code>_<ym>.parquet          分钟表按 (代码,月) 命名
  data/tushare/<table>/<code>.parquet               逐码表（财报/股本）
  data/tushare/<table>/d_YYYYMMDD.parquet           按日表（公告/竞价）
  data/tushare/_manifest.json                        批次进度

断点续传：逐码/逐月/逐日表以文件存在为 checkpoint，重跑自动跳过。

用法：
  python tushare_sync.py --batch shallow   # 批1 浅表快拉
  python tushare_sync.py --batch mins      # 批2 分钟表（约6-7h，可后台）
  python tushare_sync.py --batch deep      # 批3 大浅表（约3-4h，可后台）
  python tushare_sync.py --status
"""
import argparse
import calendar
import datetime as dt
import glob
import json
import os
import time

import pandas as pd
import requests
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "tushare")
MANIFEST = os.path.join(OUT, "_manifest.json")
API = "https://api.tushare.pro"
SLEEP = 0.35          # 常规间隔；限频错误自动退避 61s

# 美股财报族（us_income/us_balancesheet/us_cashflow/us_fina_indicator）同步开关
# （2026-10-05 定性收口，INTEL_RD_PLAN K112/K120）：
# ① tushare 美股财报接口必传 ts_code，period-only 全市场调用 100% 被拒
#   （报错「必填参数, ts_code」，与 A 股财报同款，见批3第9步注释）——
#   增量链步骤8 自上线夜夜 3表×3季=9 连 FAIL，p_*.parquet 零产出；
# ② 四表产物全站零消费方（datamart/council/portfolio 读层只用 us_daily，
#   build_catalog 未登记，data_health 水位监控不含）；
# ③ 盘上存量=09-28 旧版 per_code 回退写入的 2.2万+ 逐股长表文件，保留不清理。
# 若未来启用：须改 per_code 逐股模式（24280 码×4 表积分代价大，参照批3
# 第9步 A 股先例），勿恢复 period-only。
US_FINA_SYNC = False

# ── token ────────────────────────────────────────────────────
TOKEN = ""
_env = os.path.join(HERE, ".env.local")
if os.path.exists(_env):
    for ln in open(_env, encoding="utf-8"):
        if ln.startswith("XT_DATA_KEY="):
            TOKEN = ln.split("=", 1)[1].strip()
if not TOKEN:
    raise SystemExit("TOKEN 缺失（.env.local XT_DATA_KEY）")

SESS = requests.Session()


# ── 底层调用 ─────────────────────────────────────────────────
def ts_call(api, params, retry=4, timeout=60):
    last = ""
    for i in range(retry):
        try:
            r = SESS.post(API, json={"api_name": api, "token": TOKEN,
                                     "params": params, "fields": ""},
                          timeout=timeout)
            d = r.json()
        except Exception as e:
            last = str(e)[:80]
            time.sleep(2 * (i + 1))
            continue
        if d.get("code") == 0:
            return d.get("data") or {}
        last = str(d.get("msg", ""))[:100]
        # 限频文案变体全匹配：cb_daily 实测报"频率超限(500次/分钟)"，
        # 不含"每分钟"字样，未匹配会直接 raise 而非退避（2026-09-28 教训）
        if "每分钟" in last or "频率超限" in last or "次/分钟" in last:
            time.sleep(61)
            continue
        if "每小时" in last or "次/小时" in last:
            time.sleep(301)
            continue
        raise RuntimeError("%s: %s" % (api, last))
    raise RuntimeError("%s %s: %s" % (api, params, last))


def pull_paged(api, params, cap, max_off=None):
    """offset 翻页。cap=接口单次上限；max_off 限制总行数（探针用）。

    防失控（anns_d 死循环教训 2026-09-27：服务端 offset 翻页无进展时
    len(items)==cap 永不 break，rows 无限累积至 5.3GB）：
    · 连续两页 items 完全相同 → 视为 offset 无效，break；
    · rows 超过 50 万行 → break 兜底。
    """
    rows, fields, off = [], None, 0
    last_items = None
    while True:
        p = dict(params)
        p["offset"] = off
        data = ts_call(api, p)
        if fields is None:
            fields = data.get("fields") or []
        items = data.get("items") or []
        if not items:
            break
        if items == last_items:
            break
        last_items = items
        rows.extend(items)
        off += len(items)
        if len(items) < cap or (max_off and off >= max_off) \
                or len(rows) >= 500000:
            break
        time.sleep(SLEEP)
    return pd.DataFrame(rows, columns=fields)


# ── 落盘 ─────────────────────────────────────────────────────
def tdir(tbl):
    d = os.path.join(OUT, tbl)
    os.makedirs(d, exist_ok=True)
    return d


def part_path(tbl, idx):
    return os.path.join(tdir(tbl), "part_%04d.parquet" % idx)


def next_part(tbl):
    return len(glob.glob(os.path.join(OUT, tbl, "part_*.parquet")))


def load_manifest():
    if os.path.exists(MANIFEST):
        return json.load(open(MANIFEST, encoding="utf-8"))
    return {}


def save_manifest(m):
    tmp = MANIFEST + ".tmp"
    json.dump(m, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, MANIFEST)


def save_full(tbl, df, m, note=""):
    if df is None or df.empty:
        m[tbl] = {"done": True, "rows": 0, "note": note}
    else:
        f = part_path(tbl, next_part(tbl))
        df.to_parquet(f, index=False)
        m[tbl] = {"done": True, "rows": len(df), "file": os.path.basename(f)}
    save_manifest(m)
    print("%-20s rows=%d %s" % (tbl, 0 if df is None else len(df), note))


def month_windows(y0, m0, y1, m1):
    out = []
    y, mo = y0, m0
    while (y, mo) <= (y1, m1):
        last = calendar.monthrange(y, mo)[1]
        out.append(("%04d%02d" % (y, mo), "%04d%02d01" % (y, mo),
                    "%04d%02d%02d" % (y, mo, last)))
        mo += 1
        if mo > 12:
            y, mo = y + 1, 1
    return out


def half_windows(ym):
    y, mo = int(ym[:4]), int(ym[4:])
    last = calendar.monthrange(y, mo)[1]
    # 返回完整 YYYYMMDD（此前只回 MMDD，上层再拼年月会产出 10 位非法日期）
    return [("%04d%02d01" % (y, mo), "%04d%02d15" % (y, mo)),
            ("%04d%02d16" % (y, mo), "%04d%02d%02d" % (y, mo, last))]


def mins_path(api, code, ym):
    return os.path.join(tdir(api), "%s_%s.parquet" % (code.replace(".", "_"), ym))


def pull_mins_month(api, code, ym, cap, half=False, end_h="15:00:00"):
    """单 code 单月 1min 窗口。文件存在即跳过（返回-1）。空月落空文件防重查。"""
    f = mins_path(api, code, ym)
    if os.path.exists(f):
        return -1
    y, mo = int(ym[:4]), int(ym[4:])
    if half:
        wins = half_windows(ym)
    else:
        last = calendar.monthrange(y, mo)[1]
        wins = [("%04d%02d01" % (y, mo), "%04d%02d%02d" % (y, mo, last))]
    rows, fields = [], None
    for s, e in wins:
        off = 0
        while True:
            p = {"ts_code": code, "freq": "1min",
                 "start_date": s + " 09:00:00", "end_date": e + " " + end_h,
                 "offset": off}
            data = ts_call(api, p)
            if fields is None:
                fields = data.get("fields") or []
            items = data.get("items") or []
            if not items:
                break
            rows.extend(items)
            off += len(items)
            if len(items) < cap:
                break
            time.sleep(SLEEP)
    df = pd.DataFrame(rows, columns=fields or ["ts_code", "trade_time"])
    df.to_parquet(f, index=False)
    return len(df)


def batch_mins_codes(api, codes, y0, m0, cap, half=False, end_h="15:00:00",
                     m=None, tag=""):
    y1, m1 = dt.date.today().year, dt.date.today().month
    months = month_windows(y0, m0, y1, m1)
    total = len(codes) * len(months)
    done = skip = 0
    t0 = time.time()
    for i, code in enumerate(codes):
        for ym, _, _ in months:
            n = pull_mins_month(api, code, ym, cap, half, end_h)
            if n == -1:
                skip += 1
            else:
                done += 1
        if m is not None and (i + 1) % 25 == 0:
            m[api] = {"done": False, "codes_done": i + 1, "codes_total": len(codes)}
            save_manifest(m)
        if (i + 1) % 10 == 0 or i + 1 == len(codes):
            rate = (done + skip) / max(time.time() - t0, 1)
            eta = (total - done - skip) / max(rate, 0.01) / 60
            print("%s %s %d/%d codes done=%d skip=%d ETA=%.0fmin"
                  % (tag, api, i + 1, len(codes), done, skip, eta))
    if m is not None:
        m[api] = {"done": True, "codes": len(codes), "months": len(months)}
        save_manifest(m)


# ── 批1：浅表 ────────────────────────────────────────────────
def codes_of(tbl, col="ts_code"):
    parts = sorted(glob.glob(os.path.join(OUT, tbl, "part_*.parquet")))
    if not parts:
        return []
    return sorted(pd.concat([pd.read_parquet(p, columns=[col]) for p in parts])
                  [col].dropna().unique().tolist())


def batch_shallow(m):
    print("=== 批1 浅表 ===")
    t0 = time.time()

    def run(tbl, api, params, cap, note=""):
        try:
            df = pull_paged(api, params, cap)
            save_full(tbl, df, m, note)
        except Exception as e:
            print("%-20s FAIL %s" % (tbl, str(e)[:70]))
            m[tbl] = {"done": False, "error": str(e)[:120]}
            save_manifest(m)

    today = dt.date.today()
    cal = pull_paged("trade_cal", {"exchange": "SSE", "start_date": "19910101",
                                   "end_date": "20301231"}, cap=120000)
    save_full("trade_cal", cal, m)
    open_days = sorted(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())
    past = [d for d in open_days if d <= today.strftime("%Y%m%d")]
    last_open = past[-1] if past else today.strftime("%Y%m%d")

    run("stock_basic", "stock_basic", {"list_status": "L"}, 10000)
    # 上市公司详细信息（经营范围/主营/高管/员工数等18列，6294行一次拉完）
    run("stock_company", "stock_company", {}, 10000)
    run("cb_basic", "cb_basic", {}, 2000)
    run("us_basic", "us_basic", {}, 6000)
    run("hk_basic", "hk_basic", {}, 3000)
    run("sw_classify_l1", "index_classify", {"src": "SW2021", "lv": "L1"}, 5000)
    run("npr", "npr", {}, 500)
    # monetary_policy 被 tushare 调整权限（2026-09-28 实测无权限，宏观组
    # cn_gdp/cn_cpi/cn_m 均正常，系单接口收紧）；改用 shibor_lpr（LPR 利率）
    run("shibor_lpr", "shibor_lpr", {}, 2000)
    # 2026-10-26 K-DATA-20261026-SHIBOR：上游 shibor_lpr(LPR) 停更于 2026-08-20，
    # 补 shibor 日频（银行间拆借 on/1w/.../1y，实测最新到 2026-10-23）作为利率新鲜源。
    run("shibor", "shibor", {}, 5000)
    # 社融月度（当月增量/累计/存量）；tushare 无 cn_sf 接口名，实为 sf_month
    run("sf_month", "sf_month", {}, 2000)
    # 滚动信息流（历史深度有限，先收口近期）
    for tbl, api, cap in [("news", "news", 1500), ("major_news", "major_news", 1500),
                          ("research_report", "research_report", 1000)]:
        try:
            df = pull_paged(api, {}, cap, max_off=6000)
            save_full(tbl, df, m, "recent-only")
        except Exception as e:
            print("%-20s FAIL %s" % (tbl, str(e)[:70]))
            m[tbl] = {"done": False, "error": str(e)[:120]}
            save_manifest(m)
    # 单日探针落库（按日/按码参数验证 + 当日快照）
    probes = [("anns_d", "anns_d", {"trade_date": last_open}, 6000),
              ("stk_auction_c", "stk_auction_c", {"trade_date": last_open}, 10000),
              ("stk_auction_o", "stk_auction_o", {"trade_date": last_open}, 10000),
              ("stk_auction", "stk_auction", {"trade_date": last_open}, 10000),
              ("etf_auction", "etf_auction", {"trade_date": last_open}, 3000),
              ("irm_qa_sh", "irm_qa_sh", {}, 5000),
              ("irm_qa_sz", "irm_qa_sz", {}, 5000)]
    for tbl, api, params, cap in probes:
        try:
            df = pull_paged(api, params, cap, max_off=10000)
            save_full(tbl + "_probe", df, m, "probe %s" % (params or "default"))
        except Exception as e:
            print("%-20s probe FAIL %s" % (tbl, str(e)[:60]))
            m[tbl] = {"done": False, "error": str(e)[:120]}
            save_manifest(m)
    print("批1 完成 %.0fs" % (time.time() - t0))


# ── 批1.5：G5 高管层（按码全量，快照幂等） ────────────────────
def batch_stk_managers(m):
    """G5 高管层：stk_managers 按码全量拉（历史任期）。

    无参调用只返回近期公告（实测 ~4000 行），全量须按 ts_code 逐码
    拉（单码单页，000001.SZ 实测 265 行含 1994 起历史）。幂等：重跑
    先清旧 part（快照语义，非追加）。高管变动低频，月度/手动重跑即可。
    """
    print("=== G5 高管层 stk_managers（按码全量） ===")
    t0 = time.time()
    codes = codes_of("stock_company")
    print("codes: %d（stock_company 全体）" % len(codes))
    rows, fail = [], 0
    for i, code in enumerate(codes):
        try:
            df = pull_paged("stk_managers", {"ts_code": code}, 10000)
            rows.append(df)
        except Exception as e:
            fail += 1
            print("  %s FAIL %s" % (code, str(e)[:60]))
        if (i + 1) % 500 == 0:
            print("  … %d/%d rows=%d fail=%d (%.0fs)"
                  % (i + 1, len(codes), sum(len(r) for r in rows), fail,
                     time.time() - t0))
        time.sleep(SLEEP)
    for f in glob.glob(os.path.join(OUT, "stk_managers", "part_*.parquet")):
        os.remove(f)
    save_full("stk_managers", pd.concat(rows) if rows else pd.DataFrame(),
              m, "per-code full history")
    print("G5 高管层完成 rows=%d fail=%d %.0fs"
          % (sum(len(r) for r in rows), fail, time.time() - t0))


# ── 批2：分钟表 ──────────────────────────────────────────────
IDX_CORE = ["000001.SH", "000016.SH", "000300.SH", "000905.SH", "000852.SH",
            "000688.SH", "399001.SZ", "399006.SZ", "399106.SZ", "899050.BJ"]
ETF_CORE = ["510300.SH", "510050.SH", "510500.SH", "512100.SH", "588000.SH",
            "159915.SZ", "159949.SZ", "512880.SH", "512690.SH", "515790.SH",
            "512480.SH", "516160.SH", "515030.SH", "512010.SH", "512800.SH",
            "510880.SH", "513100.SH", "513050.SH", "518880.SH", "159920.SZ"]


def batch_mins(m):
    print("=== 批2 分钟表 ===")
    t0 = time.time()
    today = dt.date.today()

    # 1) 指数分钟 2010 起
    print("idx_mins: %d 指数 × 2010起" % len(IDX_CORE))
    batch_mins_codes("idx_mins", IDX_CORE, 2010, 1, 8000, m=m, tag="[1/5]")

    # 2) 申万一级分钟 2021 起（cap=5000 → 半月窗口；分类表含 L1/L2/L3，只取 L1）
    sw = []
    parts = sorted(glob.glob(os.path.join(OUT, "sw_classify_l1", "part_*.parquet")))
    if parts:
        df = pd.concat([pd.read_parquet(p) for p in parts])
        if "level" in df.columns:
            sw = sorted(df.loc[df["level"] == "L1", "index_code"].unique().tolist())
    if not sw:
        df = pull_paged("index_classify", {"src": "SW2021"}, 5000)
        sw = sorted(df.loc[df["level"] == "L1", "index_code"].unique().tolist())
    print("sw_mins: %d 个申万一级 × 2021起(半月)" % len(sw))
    batch_mins_codes("sw_mins", sw, 2021, 1, 5000, half=True, m=m, tag="[2/5]")

    # 3) ETF 分钟 近3年
    print("etf_mins: %d 只 × 近3年" % len(ETF_CORE))
    batch_mins_codes("etf_mins", ETF_CORE, today.year - 3, today.month, 8000,
                     m=m, tag="[3/5]")

    # 4) A股分钟：中证800 成分近1年（本地 ts_index_weight 优先，API 月度补充）
    codes = set()
    loc = sorted(glob.glob(os.path.join(HERE, "data", "tdx", "ts_index_weight",
                                        "part_*.parquet")))
    if loc:
        df = pd.concat([pd.read_parquet(p) for p in loc])
        if "index_code" in df.columns and "con_code" in df.columns:
            sub = df[df["index_code"].isin(["000906.SH", "000906"])]
            codes.update(sub["con_code"].dropna().astype(str).tolist())
    for ym, s, e in month_windows(today.year - 1, today.month,
                                  today.year, today.month):
        try:
            df = pull_paged("index_weight", {"index_code": "000906.SH",
                                             "start_date": s, "end_date": e}, 7000)
            if not df.empty:
                codes.update(df["con_code"].dropna().astype(str).tolist())
        except Exception as ex:
            print("  index_weight %s FAIL %s" % (ym, str(ex)[:50]))
    codes = sorted(codes)
    if len(codes) < 100:
        print("!! 中证800成分仅 %d 只，stk_mins 跳过（成分源待核）" % len(codes))
        m["stk_mins"] = {"done": False,
                         "error": "constituents<100 (%d)" % len(codes)}
        save_manifest(m)
    else:
        print("stk_mins: 中证800成分 %d 只 × 近1年" % len(codes))
        batch_mins_codes("stk_mins", codes, today.year - 1, today.month, 8000,
                         m=m, tag="[4/5]")

    # 5) 港股分钟：港股通成分（南向持股近20交易日并集）近3年
    hk = set()
    cal_parts = sorted(glob.glob(os.path.join(OUT, "trade_cal", "part_*.parquet")))
    open_days = sorted(pd.concat([pd.read_parquet(p, columns=["cal_date", "is_open"])
                                  for p in cal_parts])
                       .query("is_open == 1")["cal_date"].astype(str).tolist())
    for d in open_days[-20:-1]:
        try:
            df = pull_paged("hk_hold", {"trade_date": d}, 4200)
            if not df.empty:
                hk.update(df["ts_code"].dropna().tolist())
        except Exception as ex:
            print("  hk_hold %s FAIL %s" % (d, str(ex)[:50]))
    hk = sorted(hk)
    print("hk_mins: 港股通成分 %d 只 × 近3年" % len(hk))
    batch_mins_codes("hk_mins", hk, today.year - 3, today.month, 8000,
                     end_h="16:00:00", m=m, tag="[5/5]")
    print("批2 完成 %.0fs" % (time.time() - t0))


# ── 批3：大浅表 ──────────────────────────────────────────────
def batch_deep(m):
    print("=== 批3 大浅表 ===")
    t0 = time.time()

    def per_code(api, codes, cap, tag, end_h=None):
        ok = fail = 0
        for i, code in enumerate(codes):
            f = os.path.join(tdir(api), "%s.parquet" % code.replace(".", "_"))
            if os.path.exists(f):
                continue
            try:
                df = pull_paged(api, {"ts_code": code}, cap)
                df.to_parquet(f, index=False)
                ok += 1
            except Exception as e:
                fail += 1
                if fail <= 5:
                    print("%s %s FAIL %s" % (tag, code, str(e)[:50]))
            if (i + 1) % 50 == 0:
                print("%s %s %d/%d ok=%d fail=%d"
                      % (tag, api, i + 1, len(codes), ok, fail))
        print("%s %s 完成 ok=%d fail=%d" % (tag, api, ok, fail))

    def per_date(api, days, cap, param="trade_date", tag=""):
        for i, d in enumerate(days):
            f = os.path.join(tdir(api), "d_%s.parquet" % d)
            if os.path.exists(f):
                continue
            try:
                # max_off=cap*20：单日数据硬上限 20 页（anns_d 死循环教训）
                df = pull_paged(api, {param: d}, cap, max_off=cap * 20)
                df.to_parquet(f, index=False)
            except Exception as e:
                if i < 3:
                    print("%s %s %s FAIL %s" % (tag, api, d, str(e)[:50]))
            if (i + 1) % 20 == 0:
                print("%s %s %d/%d" % (tag, api, i + 1, len(days)), flush=True)

    # 交易日序列
    calp = sorted(glob.glob(os.path.join(OUT, "trade_cal", "part_*.parquet")))
    cal = pd.concat([pd.read_parquet(p, columns=["cal_date", "is_open"])
                     for p in calp])
    open_days = sorted(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())
    today = dt.date.today()

    # 1) 可转债日线 逐码
    cb = codes_of("cb_basic")
    print("cb_daily: %d 转债逐码" % len(cb))
    per_code("cb_daily", cb, 2000, "[1/8]")

    # 2) 盘前股本 逐码（全市场）
    stk = codes_of("stock_basic")
    print("bak_basic: %d 股逐码" % len(stk))
    per_code("bak_basic", stk, 7000, "[2/8]")

    # 3) 公告 按日（2019起，仅过去交易日）
    today_s = today.strftime("%Y%m%d")
    days = [d for d in open_days if "20190101" <= d <= today_s]
    print("anns_d: %d 交易日" % len(days))
    # anns_d 接口按公告日过滤用 ann_date（trade_date 不是其参数，会被
    # 服务端忽略→返回全量最新公告→单日 12 万行脏数据/翻页死循环根源）
    per_date("anns_d", days, 6000, param="ann_date", tag="[3/8]")

    # 4) 董秘问答 逐码（SH→irm_qa_sh，SZ/BJ→irm_qa_sz；集合竞价系列仅当日快照不回填）
    sh = [c for c in stk if c.endswith(".SH")]
    szbj = [c for c in stk if not c.endswith(".SH")]
    print("irm_qa_sh: %d 码" % len(sh))
    per_code("irm_qa_sh", sh, 5000, "[4/8]")
    print("irm_qa_sz: %d 码" % len(szbj))
    per_code("irm_qa_sz", szbj, 5000, "[4/8]")

    # 5) 美股日线 逐码
    us = codes_of("us_basic")
    print("us_daily: %d 美股逐码" % len(us))
    per_code("us_daily", us, 8000, "[5/8]")

    # 6) 美股财报 按季（2009Q1起 × 3表）——已停（US_FINA_SYNC=False，定性见文件头）
    periods = []
    for y in range(2009, today.year + 1):
        for q in (1, 2, 3, 4):
            if y == today.year and q > (today.month - 1) // 3 + 1:
                break
            periods.append("%dQ%d" % (y, q))
    for api in (("us_income", "us_balancesheet", "us_cashflow")
                if US_FINA_SYNC else ()):
        ok = fail = 0
        for i, per in enumerate(periods):
            f = os.path.join(tdir(api), "p_%s.parquet" % per)
            if os.path.exists(f):
                continue
            try:
                df = pull_paged(api, {"period": per}, 10000)
                df.to_parquet(f, index=False)
                ok += 1
            except Exception as e:
                fail += 1
                if fail <= 3:
                    print("%s %s FAIL %s" % (api, per, str(e)[:50]))
            if (i + 1) % 20 == 0:
                print("%s %d/%d ok=%d fail=%d" % (api, i + 1, len(periods), ok, fail))
        print("%s 完成 ok=%d fail=%d" % (api, ok, fail))
        if fail >= len(periods) // 2:
            print("%s period 模式大面积失败，改逐码" % api)
            per_code(api, us, 10000, "[6/8]")

    # 7) 美股财务指标 逐码——已停（US_FINA_SYNC=False，定性见文件头）
    if US_FINA_SYNC:
        print("us_fina_indicator: %d 码" % len(us))
        per_code("us_fina_indicator", us, 200, "[7/8]")

    # 8) 港股财报 逐码 ×4
    hk = codes_of("hk_basic")
    for api, cap in [("hk_income", 10000), ("hk_balancesheet", 10000),
                     ("hk_cashflow", 10000), ("hk_fina_indicator", 200)]:
        print("%s: %d 港股逐码" % (api, len(hk)))
        per_code(api, hk, cap, "[8/9]")

    # 9) A股财报 全字段逐码 ×4（知识图谱公司质量因子来源）
    # income(85列)/balancesheet(152列)/cashflow(97列)/fina_indicator(108列)
    # tushare A股财报 API 必传 ts_code（不支持纯 period 拉全市场），只能逐码。
    # 5908股×4表≈23632次调用，SLEEP=0.35 → 约2.3h（per_code 有断点续传）
    stk = codes_of("stock_basic")
    for api in ("income", "balancesheet", "cashflow", "fina_indicator"):
        print("%s: %d A股逐码（全字段）" % (api, len(stk)))
        per_code(api, stk, 10000, "[9/9]")

    # 10) 中证800权重 全历史（月度）
    print("index_weight 000906.SH 2015起")
    for ym, s, e in month_windows(2015, 1, today.year, today.month):
        f = os.path.join(tdir("index_weight"), "000906_%s.parquet" % ym)
        if os.path.exists(f):
            continue
        try:
            df = pull_paged("index_weight", {"index_code": "000906.SH",
                                             "start_date": s, "end_date": e}, 7000)
            df.to_parquet(f, index=False)
        except Exception as ex:
            print("  index_weight %s FAIL %s" % (ym, str(ex)[:50]))
    print("批3 完成 %.0fs" % (time.time() - t0))


# ── 增量同步 ─────────────────────────────────────────────────
def _last_local_date(f, date_col="trade_date"):
    """读现有 parquet 文件最新日期。无文件/无列返回 None。"""
    if not os.path.exists(f):
        return None
    try:
        df = pd.read_parquet(f, columns=[date_col])
        if df.empty:
            return None
        return str(df[date_col].max())
    except Exception:
        return None


def incr_per_code_daily(api, codes, cap, m, dryrun=False):
    """逐码日线增量：读最新日 → start_date=最新日 → 拉新 → 合并去重写回。
    无现有文件的码跳过（增量只续拉已有，新码留 deep 批补）。
    """
    ok = fail = skip = 0
    for i, code in enumerate(codes):
        f = os.path.join(tdir(api), "%s.parquet" % code.replace(".", "_"))
        last = _last_local_date(f)
        if last is None:
            skip += 1
            continue
        if dryrun:
            print("  DRY %s %s last=%s" % (api, code, last))
            continue
        try:
            df_new = pull_paged(api, {"ts_code": code, "start_date": last}, cap)
            if not df_new.empty:
                df_old = pd.read_parquet(f)
                df = pd.concat([df_old, df_new], ignore_index=True)
                # 去重键优先 trade_date，否则 end_date，否则全列
                key = "trade_date" if "trade_date" in df.columns else \
                      ("end_date" if "end_date" in df.columns else None)
                if key:
                    df = df.drop_duplicates(subset=[key], keep="last")
                df.to_parquet(f, index=False)
                ok += 1
            else:
                skip += 1
        except Exception as e:
            fail += 1
            if fail <= 5:
                print("%s incr %s FAIL %s" % (api, code, str(e)[:60]))
        if (i + 1) % 100 == 0:
            print("%s incr %d/%d ok=%d fail=%d skip=%d"
                  % (api, i + 1, len(codes), ok, fail, skip), flush=True)
    print("%s incr 完成 ok=%d fail=%d skip=%d" % (api, ok, fail, skip))


def incr_mins(m, market, dryrun=False):
    """分钟表增量：删当月+上月文件 → 重拉。market∈{all,cn,hk}。"""
    today = dt.date.today()
    y, mo = today.year, today.month
    y0, m0 = (y - 1, 12) if mo == 1 else (y, mo - 1)
    months_ym = []
    for yy, mm in [(y0, m0), (y, mo)]:
        months_ym.append("%04d%02d" % (yy, mm))

    tables = []
    if market in ("all", "cn"):
        tables.append(("idx_mins", IDX_CORE, 8000, False, "15:00:00"))
        tables.append(("etf_mins", ETF_CORE, 8000, False, "15:00:00"))
        sw = []
        parts = sorted(glob.glob(os.path.join(OUT, "sw_classify_l1", "part_*.parquet")))
        if parts:
            df = pd.concat([pd.read_parquet(p) for p in parts])
            if "level" in df.columns:
                sw = sorted(df.loc[df["level"] == "L1", "index_code"].unique().tolist())
        if sw:
            tables.append(("sw_mins", sw, 5000, True, "15:00:00"))
        codes = set()
        loc = sorted(glob.glob(os.path.join(HERE, "data", "tdx", "ts_index_weight",
                                            "part_*.parquet")))
        if loc:
            df = pd.concat([pd.read_parquet(p) for p in loc])
            if "index_code" in df.columns and "con_code" in df.columns:
                sub = df[df["index_code"].isin(["000906.SH", "000906"])]
                codes.update(sub["con_code"].dropna().astype(str).tolist())
        if len(codes) < 100:
            # 本地权重表缺中证800（TdxSync 仅拉 000300）→ 直接向 tushare
            # 拉最新一期 000906 成分兜底，避免 stk_mins 被静默跳过
            try:
                w = pull_paged("index_weight", {"index_code": "000906.SH"}, 8000)
                if not w.empty and "con_code" in w.columns:
                    latest_d = w["trade_date"].max()
                    got = w.loc[w["trade_date"] == latest_d,
                                "con_code"].dropna().astype(str).tolist()
                    codes.update(got)
                    print("stk_mins: 本地权重缺 000906，tushare 兜底 %s 期 %d 只"
                          % (latest_d, len(got)))
            except Exception as e:
                print("stk_mins: 000906 兜底拉取 FAIL %s" % str(e)[:70])
        if len(codes) >= 100:
            tables.append(("stk_mins", sorted(codes), 8000, False, "15:00:00"))
    if market in ("all", "hk"):
        hk = set()
        cal_parts = sorted(glob.glob(os.path.join(OUT, "trade_cal", "part_*.parquet")))
        if cal_parts:
            cal = pd.concat([pd.read_parquet(p, columns=["cal_date", "is_open"])
                             for p in cal_parts])
            open_days = sorted(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())
            for d in open_days[-20:-1]:
                try:
                    df = pull_paged("hk_hold", {"trade_date": d}, 4200)
                    if not df.empty:
                        hk.update(df["ts_code"].dropna().tolist())
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tushare_sync.py", exc)
        if hk:
            tables.append(("hk_mins", sorted(hk), 8000, False, "16:00:00"))

    for api, codes, cap, half, end_h in tables:
        if dryrun:
            print("  DRY %s %d codes × %d months" % (api, len(codes), len(months_ym)))
            continue
        # 删当月+上月文件
        for code in codes:
            for ym in months_ym:
                fp = mins_path(api, code, ym)
                if os.path.exists(fp):
                    os.remove(fp)
        batch_mins_codes(api, codes, y0, m0, cap, half=half, end_h=end_h,
                         m=m, tag="[incr]")


def incremental(m, market="cn", dryrun=False):
    """增量同步：高频表日级续拉，低频财报留 deep 批。

    market: cn（A股+港股通分钟）/ hk（仅港股通分钟）/ us（美股日+财报近3季度）/ all
    """
    print("=== 增量同步 market=%s dryrun=%s ===" % (market, dryrun))
    t0 = time.time()
    today = dt.date.today()
    today_s = today.strftime("%Y%m%d")

    # 1) trade_cal 重拉（覆盖，必做）
    if not dryrun:
        cal = pull_paged("trade_cal", {"exchange": "SSE", "start_date": "19910101",
                                       "end_date": "20301231"}, cap=120000)
        save_full("trade_cal", cal, m)
        open_days = sorted(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())
    else:
        load_manifest()
        calp = sorted(glob.glob(os.path.join(OUT, "trade_cal", "part_*.parquet")))
        cal = pd.concat([pd.read_parquet(p, columns=["cal_date", "is_open"])
                         for p in calp]) if calp else pd.DataFrame()
        open_days = sorted(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist()) \
            if not cal.empty else []
    past = [d for d in open_days if d <= today_s]

    # 2) 浅表全量覆盖（数据小）
    shallow_tbls = [
        ("stock_basic", "stock_basic", {"list_status": "L"}, 10000),
        # 上市公司详细信息（经营范围/主营/高管/员工数；知识图谱公司实体来源）
        ("stock_company", "stock_company", {}, 10000),
        ("cb_basic", "cb_basic", {}, 2000),
        ("hk_basic", "hk_basic", {}, 3000),
        ("npr", "npr", {}, 500),
        # monetary_policy 权限被收紧（2026-09-28），替换为 LPR 利率
        ("shibor_lpr", "shibor_lpr", {}, 2000),
        # 2026-10-31 OS-FIX-SHIBOR：shibor 日频只进了 batch_shallow（:288），
        # 漏进日增量清单 → 10-26 后停在 20261023（8 天断更）；此处补齐。
        ("shibor", "shibor", {}, 5000),
        # 社融月度（tushare 接口名为 sf_month，cn_sf 不存在）
        ("sf_month", "sf_month", {}, 2000),
    ]
    if market in ("all", "us"):
        shallow_tbls.append(("us_basic", "us_basic", {}, 6000))
    shallow_tbls.append(("sw_classify_l1", "index_classify",
                         {"src": "SW2021", "lv": "L1"}, 5000))
    for tbl, api, params, cap in shallow_tbls:
        if dryrun:
            print("  DRY shallow %s full-refresh" % tbl)
            continue
        try:
            df = pull_paged(api, params, cap)
            save_full(tbl, df, m, "incr full")
        except Exception as e:
            print("%-20s FAIL %s" % (tbl, str(e)[:70]))

    # 3) 滚动信息流（recent-only）
    for tbl, api, cap in [("news", "news", 1500),
                          ("major_news", "major_news", 1500),
                          ("research_report", "research_report", 1000)]:
        if dryrun:
            print("  DRY %s recent-refresh" % tbl)
            continue
        try:
            df = pull_paged(api, {}, cap, max_off=6000)
            save_full(tbl, df, m, "incr recent")
        except Exception as e:
            print("%-20s FAIL %s" % (tbl, str(e)[:70]))

    # 4) anns_d 按日：重拉近 7 天 d_YYYYMMDD 覆盖
    if market in ("all", "cn"):
        recent_days = past[-7:] if len(past) >= 7 else past
        if dryrun:
            print("  DRY anns_d %d days: %s..%s"
                  % (len(recent_days), recent_days[0] if recent_days else "-",
                     recent_days[-1] if recent_days else "-"))
        else:
            for d in recent_days:
                f = os.path.join(tdir("anns_d"), "d_%s.parquet" % d)
                try:
                    df = pull_paged("anns_d", {"ann_date": d}, 6000,
                                    max_off=6000 * 20)
                    df.to_parquet(f, index=False)
                except Exception as e:
                    print("anns_d %s FAIL %s" % (d, str(e)[:60]))

    # 4b) top_list 按日：龙虎榜近 7 天 d_YYYYMMDD 覆盖（TDX ts_top_list
    #     断流时的 tushare 兜底源；premarket 赚钱效应"游资上榜"维度用）
    if market in ("all", "cn"):
        if dryrun:
            print("  DRY top_list %d days" % len(recent_days))
        else:
            for d in recent_days:
                f = os.path.join(tdir("top_list"), "d_%s.parquet" % d)
                try:
                    df = pull_paged("top_list", {"trade_date": d}, 1000)
                    df.to_parquet(f, index=False)
                except Exception as e:
                    print("top_list %s FAIL %s" % (d, str(e)[:60]))

    # 5) 分钟表增量：删当月+上月 → 重拉
    if market in ("all", "cn", "hk"):
        incr_mins(m, market, dryrun=dryrun)

    # 6) 逐码日线增量：cb_daily / bak_basic
    if market in ("all", "cn"):
        cb = codes_of("cb_basic")
        incr_per_code_daily("cb_daily", cb, 2000, m, dryrun=dryrun)
        stk = codes_of("stock_basic")
        incr_per_code_daily("bak_basic", stk, 7000, m, dryrun=dryrun)

    # 7) us_daily 逐码增量
    if market in ("all", "us"):
        us = codes_of("us_basic")
        incr_per_code_daily("us_daily", us, 8000, m, dryrun=dryrun)

    # 8) 美股财报 period 增量——已停（US_FINA_SYNC=False，定性见文件头）：
    #    tushare 接口必传 ts_code，period-only 调用 100% FAIL（曾夜夜 9 连），
    #    产物零消费方；us_daily（步骤7）不受影响照常增量
    if US_FINA_SYNC and market in ("all", "us"):
        periods_recent = []
        q_curr = (today.month - 1) // 3 + 1
        for offset in (0, 1, 2):
            q = q_curr - offset
            y = today.year
            if q <= 0:
                y -= 1
                q += 4
            periods_recent.append("%dQ%d" % (y, q))
        for api in ("us_income", "us_balancesheet", "us_cashflow"):
            if dryrun:
                print("  DRY %s periods=%s" % (api, periods_recent))
                continue
            for per in periods_recent:
                f = os.path.join(tdir(api), "p_%s.parquet" % per)
                try:
                    df = pull_paged(api, {"period": per}, 10000)
                    df.to_parquet(f, index=False)
                except Exception as e:
                    print("%s %s FAIL %s" % (api, per, str(e)[:60]))

    # 9) index_weight 中证800 最近 2 月
    if market in ("all", "cn"):
        months = []
        for offset in (1, 0):
            mm = today.month - offset
            yy = today.year
            if mm < 1:
                yy -= 1
                mm += 12
            last = calendar.monthrange(yy, mm)[1]
            months.append(("%04d%02d" % (yy, mm),
                           "%04d%02d01" % (yy, mm),
                           "%04d%02d%02d" % (yy, mm, last)))
        for ym, s, e in months:
            f = os.path.join(tdir("index_weight"), "000906_%s.parquet" % ym)
            if dryrun:
                print("  DRY index_weight %s" % ym)
                continue
            try:
                df = pull_paged("index_weight", {"index_code": "000906.SH",
                                                 "start_date": s, "end_date": e}, 7000)
                df.to_parquet(f, index=False)
            except Exception as ex:
                print("index_weight %s FAIL %s" % (ym, str(ex)[:60]))

    print("增量完成 %.0fs" % (time.time() - t0))


# ── status ───────────────────────────────────────────────────
def status():
    m = load_manifest()
    print("%-22s %-7s %10s %8s" % ("table", "done", "rows", "note"))
    for tbl in sorted(set(list(m.keys()) +
                          [os.path.basename(p) for p in
                           glob.glob(os.path.join(OUT, "*")) if os.path.isdir(p)])):
        st = m.get(tbl, {})
        n = len(glob.glob(os.path.join(OUT, tbl, "*.parquet")))
        rows = st.get("rows", "")
        print("%-22s %-7s %10s %8s files=%d" % (
            tbl, st.get("done", "-"), rows, st.get("note", "")[:8], n))


def batch_bclass(m):
    """B 类四表：报告期表按 p_<period>.parquet 分片、limit_list_d 按日
    d_<date>.parquet 分片，文件存在即 checkpoint 断点续传。"""
    print("=== 批4 B类（机构持仓/股东户数/涨跌停名单）===")
    t0 = time.time()
    today = dt.date.today()

    # 1) 报告期表：近 9 期（含未出数的最新期，空数据落空片占位防重拉）
    def report_periods(n=9):
        ps = []
        y, mo = today.year, today.month
        for _ in range(n):
            q = {3: "0331", 6: "0630", 9: "0930", 12: "1231"}
            key = None
            for mm in (3, 6, 9, 12):
                if mo >= mm:
                    key = "%04d%s" % (y, q[mm])
            # mo<3 时取上年 1231
            ps.append(key or "%04d1231" % (y - 1))
            y, mo = (y - 1, 12) if mo <= 3 else (y, mo - 3)
        return sorted(set(ps))

    for tbl in ("top10_holders", "top10_floatholders"):
        cap = 5000
        for p in report_periods():
            f = os.path.join(tdir(tbl), "p_%s.parquet" % p)
            if os.path.exists(f):
                continue
            try:
                # 按报告期全市场翻页（offset 无效时 pull_paged 自防失控）
                df = pull_paged(tbl, {"period": p}, cap, max_off=cap * 30)
                df.to_parquet(f, index=False)
                print("  %-20s %s rows=%d" % (tbl, p, len(df)), flush=True)
            except Exception as e:
                print("  %-20s %s FAIL %s" % (tbl, p, str(e)[:60]))
                break  # 积分/接口问题：同表后续期不再试

    # 1b) stk_holdernumber：period 参数无效（end_date 是半月披露日，按期
    # 拉只回最新快照）→ 改 per_code 逐股全历史（deep 批 per_code 同模式）
    bparts = sorted(glob.glob(os.path.join(OUT, "stock_basic",
                                           "part_*.parquet")))
    codes = []
    if bparts:
        codes = pd.concat([pd.read_parquet(p, columns=["ts_code"])
                           for p in bparts])["ts_code"].drop_duplicates() \
            .tolist()
    hok = hfail = 0
    for i, code in enumerate(codes):
        f = os.path.join(tdir("stk_holdernumber"),
                         "%s.parquet" % code.replace(".", "_"))
        if os.path.exists(f):
            continue
        try:
            df = pull_paged("stk_holdernumber", {"ts_code": code}, 500)
            df.to_parquet(f, index=False)
            hok += 1
        except Exception as e:
            hfail += 1
            if hfail <= 3:
                print("  stk_holdernumber %s FAIL %s" % (code, str(e)[:60]))
        if (i + 1) % 200 == 0:
            print("  stk_holdernumber %d/%d ok=%d fail=%d"
                  % (i + 1, len(codes), hok, hfail), flush=True)
    print("stk_holdernumber 逐股完成 ok=%d fail=%d" % (hok, hfail))

    # 2) limit_list_d：202509 起逐日（对齐 candle 池窗口）
    calp = sorted(glob.glob(os.path.join(OUT, "trade_cal", "part_*.parquet")))
    cal = pd.concat([pd.read_parquet(p, columns=["cal_date", "is_open"])
                     for p in calp])
    days = sorted(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())
    days = [d for d in days if "20250901" <= d <= today.strftime("%Y%m%d")]
    done = 0
    miss = 0
    for d in days:
        f = os.path.join(tdir("limit_list_d"), "d_%s.parquet" % d)
        if os.path.exists(f):
            continue
        try:
            df = pull_paged("limit_list_d", {"trade_date": d}, 1000)
            df.to_parquet(f, index=False)
            done += 1
        except Exception as e:
            miss += 1
            if miss <= 3:
                print("  limit_list_d %s FAIL %s" % (d, str(e)[:60]))
        if (done + miss) % 50 == 0 and done + miss > 0:
            print("  limit_list_d %d/%d" % (done + miss, len(days)),
                  flush=True)
    print("limit_list_d 新拉 %d 天（fail %d）" % (done, miss))

    # 3) cyq_perf 筹码分布：202509 起逐日全市场（单日 ~5.5k 行单次可返；
    #    探针 2026-09-30 实测 5,559 行无翻页）。全历史 ~1170 万行太重，
    #    按"密度考虑"原议只留近窗口；0 行=数据未出（T+1 延迟源）不落片，
    #    留待下次重拉——落空片会永久占位断流（OPS 教训 2026-10-09）
    cdone = cmiss = 0
    for d in days:
        f = os.path.join(tdir("cyq_perf"), "d_%s.parquet" % d)
        if os.path.exists(f):
            try:
                if len(pd.read_parquet(f)) > 0:
                    continue
            except Exception:
                continue   # 读不了的片不冒险覆盖
        try:
            df = pull_paged("cyq_perf", {"trade_date": d}, 6000)
            if df.empty:
                continue
            df.to_parquet(f, index=False)
            cdone += 1
        except Exception as e:
            cmiss += 1
            if cmiss <= 3:
                print("  cyq_perf %s FAIL %s" % (d, str(e)[:60]))
        if (cdone + cmiss) % 50 == 0 and cdone + cmiss > 0:
            print("  cyq_perf %d/%d" % (cdone + cmiss, len(days)),
                  flush=True)
    print("cyq_perf 新拉 %d 天（fail %d）" % (cdone, cmiss))
    print("批4 B类完成 %.0fs" % (time.time() - t0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch",
                    choices=["shallow", "mins", "deep", "managers", "bclass"])
    ap.add_argument("--incremental", action="store_true",
                    help="增量同步（高频表日级续拉）")
    ap.add_argument("--market", choices=["all", "cn", "hk", "us"], default="cn",
                    help="增量市场范围（cn=A股+港股通分钟，us=美股日+财报，all=全部）")
    ap.add_argument("--dryrun", action="store_true",
                    help="增量空跑：打印计划不写盘不调 API（trade_cal/mins 元数据除外）")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--only", choices=["shibor"],
                    help="只同步单表（运维修补用；2026-10-26 SHIBOR 单新增）")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    m = load_manifest()
    if a.status or (not a.batch and not a.incremental and not a.only):
        status()
        return
    if a.only:
        # 单表运维修补：不触碰其他表、不跑批；写入原始层后由整理层下次重建派生。
        # 注意：run() 是 batch_shallow 的内嵌函数，main 里不可见——这里直接用
        # 模块级 pull_paged/save_full/save_manifest 复刻同样的落盘语义。
        ONLY = {"shibor": ("shibor", "shibor", {}, 5000)}
        tbl, api, params, cap = ONLY[a.only]
        try:
            df = pull_paged(api, params, cap)
            save_full(tbl, df, m, "only")
            m[tbl] = {"done": True, "rows": int(len(df)), "source": "only"}
            save_manifest(m)
            print("%-20s rows=%d only-sync ok" % (tbl, len(df)))
        except Exception as e:
            print("%-20s FAIL %s" % (tbl, str(e)[:70]))
            m[tbl] = {"done": False, "error": str(e)[:120]}
            save_manifest(m)
            return 1
        return 0
    if a.incremental:
        incremental(m, market=a.market, dryrun=a.dryrun)
        return
    {"shallow": batch_shallow, "mins": batch_mins, "deep": batch_deep,
     "managers": batch_stk_managers, "bclass": batch_bclass}[a.batch](m)


if __name__ == "__main__":
    main()
