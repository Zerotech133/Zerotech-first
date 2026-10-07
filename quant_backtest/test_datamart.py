# -*- coding: utf-8 -*-
"""datamart + build_catalog 单元测试（零网络，临时目录）。"""
import json
import os
import sys
import tempfile

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_catalog as bc            # noqa: E402
import datamart as dm                 # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    print(("PASS  " if cond else "FAIL  ") + name)
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


def _mk(root, tbl, files, manifest=None):
    d = os.path.join(root, "data", "tushare", tbl)
    os.makedirs(d, exist_ok=True)
    for name, df in files.items():
        df.to_parquet(os.path.join(d, name), index=False)
    return manifest


def make_fixture(root):
    """构造 5 张测试表 + manifest。"""
    cal = pd.DataFrame({"exchange": ["SSE"] * 6,
                        "cal_date": ["2026010%d" % i for i in range(1, 7)],
                        "is_open": [1, 1, 1, 0, 1, 1]})
    day1 = pd.DataFrame({"ts_code": ["600519.SH"] * 3,
                         "trade_date": ["20260105", "20260106", "20260107"],
                         "close": [100.0, 101.0, 102.0]})
    day2 = pd.DataFrame({"ts_code": ["000001.SZ"] * 2,
                         "trade_date": ["20260105", "20260106"],
                         "close": [10.0, 10.1]})
    mins = pd.DataFrame({
        "ts_code": ["600519.SH"] * 4,
        "trade_time": ["2026-01-05 09:30:00", "2026-01-05 09:31:00",
                       "2026-01-05 09:59:00", "2026-01-05 10:00:00"],
        "open": [1.0, 1.1, 1.2, 1.3], "high": [1.2, 1.3, 1.3, 1.4],
        "low": [0.9, 1.0, 1.1, 1.2], "close": [1.1, 1.2, 1.25, 1.35],
        "vol": [10.0, 20.0, 30.0, 40.0]})
    ann = pd.DataFrame({"ann_date": ["2026-01-05", "20260105"],
                        "ts_code": ["600519.SH", "600519.SH"],
                        "title": ["a", "b"]})
    pri = pd.DataFrame({"ts_code": ["600519.SH"],
                        "trade_date": ["20260105"], "close": [100.0]})
    sec = pd.DataFrame({"ts_code": ["600519.SH"],
                        "trade_date": ["20260105"], "close": [999.0]})
    pend = pd.DataFrame({"ts_code": ["600519.SH"],
                         "trade_date": ["20260105"], "close": [1.0]})
    _mk(root, "t_cal", {"part_0000.parquet": cal})
    _mk(root, "t_day", {"600519.SH.parquet": day1, "000001.SZ.parquet": day2,
                        # K100：mtime 最新的空壳（增量批无新数据个体）
                        "000002.SZ.parquet": pd.DataFrame(
                            {"ts_code": [], "trade_date": [], "close": []})})
    # K100：by_code freshness 按 mtime 序取首个非空——固定 mtime 序使
    # 断言确定化：空壳(000002) 最新 > 600519 > 000001
    import time as _t
    _t0 = _t.time()
    for _f, _dt in (("000002.SZ.parquet", 300), ("600519.SH.parquet", 200),
                    ("000001.SZ.parquet", 100)):
        os.utime(os.path.join(root, "data", "tushare", "t_day", _f),
                 (_t0 + _dt, _t0 + _dt))
    _mk(root, "t_min", {"600519_SH_202601.parquet": mins,
                        # K100：文件名月更大的空壳（当月停牌个体）
                        "600519_SH_202602.parquet": mins.iloc[0:0]})
    _mk(root, "t_ann", {"d_20260105.parquet": ann,
                        # K99：0 行空片（pull 侧防重拉设计），freshness 应回退
                        "d_20260106.parquet": pd.DataFrame(
                            {"ann_date": [], "ts_code": [], "title": []})})
    _mk(root, "t_pri", {"part_0000.parquet": pri})
    _mk(root, "t_sec", {"part_0000.parquet": sec})
    _mk(root, "t_pending", {"part_0000.parquet": pend})
    mf2 = {"t_cal": {"done": True, "rows": 6},
           "t_day": {"done": True}, "t_min": {"done": True},
           "t_ann": {"done": True}, "t_pri": {"done": True},
           "t_sec": {"done": True}, "t_pending": {"done": False}}
    os.makedirs(os.path.join(root, "data", "tushare"), exist_ok=True)
    with open(os.path.join(root, "data", "tushare", "_manifest.json"),
              "w", encoding="utf-8") as f:
        json.dump(mf2, f, ensure_ascii=False)


def main():
    tmp = tempfile.mkdtemp(prefix="dm_test_")
    make_fixture(tmp)
    bc.TABLE_DEFS = dict(bc.TABLE_DEFS, t_sec={"priority": 2})
    bc.ALIASES = {"t_alias": {"resolves": ["t_pri", "t_sec"],
                              "note": "测试真源优先级"}}
    cat = bc.build(data_root=tmp, out_path=os.path.join(tmp, "catalog.json"))
    t = cat["tables"]

    # ── build_catalog ──
    check("layout 推断 part/by_code/by_code_month/by_date",
          (t["t_cal"]["layout"] == "part" and t["t_day"]["layout"] == "by_code"
           and t["t_min"]["layout"] == "by_code_month"
           and t["t_ann"]["layout"] == "by_date"))
    check("schema 读自 parquet", t["t_cal"]["schema"][:2] ==
          ["exchange", "cal_date"])
    check("date_col 推断 cal_date/trade_time/ann_date",
          (t["t_cal"]["date_col"] == "cal_date"
           and t["t_min"]["date_col"] == "trade_time"
           and t["t_ann"]["date_col"] == "ann_date"))
    check("done 取 manifest（pending=false）",
          t["t_cal"]["done"] and not t["t_pending"]["done"])
    check("freshness part/by_date/by_code_month",
          (t["t_cal"]["freshness"] == "20260106"
           and t["t_ann"]["freshness"] == "20260105"
           and t["t_min"]["freshness"] == "20260105"))
    check("by_date 空片回退（K99：0 行分片不算新鲜，d_20260106 空片跳过）",
          t["t_ann"]["freshness"] == "20260105")
    check("by_code 空壳跳过（K100：mtime 最新空壳不遮蔽真日期）",
          t["t_day"]["freshness"] == "20260107")
    check("by_code_month 空壳跳过（K100：月更大的空壳不遮蔽真日期）",
          t["t_min"]["freshness"] == "20260105")
    check("priority 特例生效", t["t_sec"]["priority"] == 2
          and t["t_pri"]["priority"] == 1)
    check("aliases 写入", cat["aliases"]["t_alias"]["resolves"] ==
          ["t_pri", "t_sec"])

    # ── datamart ──
    dm.CATALOG = os.path.join(tmp, "catalog.json")
    dm._cat_cache.update(mtime=None, data=None)

    check("ready 别名/物理/未完成", dm.ready("t_alias")
          and dm.ready("t_day") and not dm.ready("t_pending"))
    check("columns 归一", dm.columns("t_day") ==
          ["ts_code", "trade_date", "close"])

    df = dm.read("t_cal")
    check("part 全读 + 排序", len(df) == 6 and
          df["cal_date"].tolist() == sorted(df["cal_date"].tolist()))

    df = dm.read("t_day", codes="600519", fields=["close"])
    check("by_code 6位前缀 + fields", len(df) == 3 and
          list(df.columns) == ["ts_code", "trade_date", "close"])

    df = dm.read("t_day", start="2026-01-05", end="20260106",
                 codes=["600519.SH"])
    check("by_code 日期过滤（横杠日期归一）", len(df) == 2 and
          df["trade_date"].tolist() == ["20260105", "20260106"])

    df = dm.read("t_min", start="20260101", end="20260131",
                 codes="600519.SH")
    check("by_code_month 命中 + trade_time 保留",
          len(df) == 4 and df["trade_time"].iloc[0] == "2026-01-05 09:30:00")

    df = dm.read("t_min", codes="600519", start="20260101",
                 end="20260105")
    check("by_code_month 前缀通配", len(df) == 4)

    df = dm.read("t_ann", codes="600519.SH")
    check("by_date 读取 + 日期归一 + (code,date) 去重",
          len(df) == 1 and df["ann_date"].iloc[0] == "20260105")

    df = dm.read("t_alias")
    check("别名真源优先（pri 而非 sec）", len(df) == 1 and
          df["close"].iloc[0] == 100.0)

    check("未完成表拒绝读", dm.read("t_pending").empty)

    check("latest 取 catalog freshness", dm.latest("t_cal") == "20260106"
          and dm.latest("t_alias") is not None)

    m = dm._months("20251105", None)
    check("_months end缺省→当月跨度", m[0] == "202511" and m[-1] >= "202609")

    print("\n%d PASS / %d FAIL" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
