# -*- coding: utf-8 -*-
"""K56-B4 数据穿透 table_preview 单元测试（零网络，临时目录）。

覆盖：白名单拦截 / by_code 快路径 / by_date 窗口 / part 无日期列 /
n 边界（1..20）/ 日期倒序 / None·NaN 文本化 / float 精度 / 空表兜底 /
_prev_cache 生效与 TTL 过期 / 宽表 12 列截断 / 长 str 48 截断 /
by_date 文件名过期 limit 兜底 / by_code 25 文件 mtime 截断 /
门户包装异常兜底 / _TP_MKT 徽章与 15 表同步。
跑法：python -X utf8 test_table_preview.py（exit code = FAIL 数）。
"""

import json
import os
import sys
import tempfile
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_catalog as bc  # noqa: E402
import datamart as dm  # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    print(("PASS  " if cond else "FAIL  ") + name)
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


def make_fixture(root):
    """四张穿透测试表（by_code / by_date / part / 空表）+ manifest。"""
    base = os.path.join(root, "data", "tushare")

    def mk(tbl, files):
        d = os.path.join(base, tbl)
        os.makedirs(d, exist_ok=True)
        for name, df in files.items():
            df.to_parquet(os.path.join(d, name), index=False)

    # by_code：两票文件；600519 三天（最新 20260107）+ None vol 行；
    # 000001 两天（更旧、日期独立——避免并列排序不定）。
    # 倒序后 rows[2] 恒为 600519@20260105：close=100.123456、vol=None
    mk(
        "tp_day",
        {
            "600519.SH.parquet": pd.DataFrame(
                {
                    "ts_code": ["600519.SH"] * 3,
                    "trade_date": ["20260105", "20260106", "20260107"],
                    "close": [100.123456, 101.0, 102.5],
                    "vol": [None, 2000.0, 3000.0],
                }
            ),
            "000001.SZ.parquet": pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"] * 2,
                    "trade_date": ["20260103", "20260104"],
                    "close": [10.0, 10.1],
                    "vol": [100.0, 200.0],
                }
            ),
        },
    )
    # by_date：3 行——前两行混格式同键（归一后去重 keep=first 留 88.0），
    # 第三行异键带 NaN close（倒序居首，验 NaN 文本化）
    mk(
        "tp_ann",
        {
            "d_20260105.parquet": pd.DataFrame(
                {
                    "ts_code": ["600519.SH", "600519.SH", "000001.SZ"],
                    "ann_date": ["2026-01-05", "20260105", "2026-01-07"],
                    "close": [88.0, 88.5, float("nan")],
                }
            )
        },
    )
    # part：date_col=cal_date 不在候选列 → 不排序不应炸
    mk(
        "tp_cal",
        {
            "part_0000.parquet": pd.DataFrame(
                {
                    "exchange": ["SSE"] * 3,
                    "cal_date": ["20260105", "20260106", "20260107"],
                    "is_open": [1, 1, 0],
                }
            )
        },
    )
    # 空表：目录存在无文件 → note 兜底
    os.makedirs(os.path.join(base, "tp_empty"), exist_ok=True)
    # by_code 宽表：15 列（cols 应截 12）+ v3 长 str（应截 48 字符）
    mk(
        "tp_wide",
        {
            "600000.SH.parquet": pd.DataFrame(
                {
                    "ts_code": ["600000.SH"],
                    "trade_date": ["20260107"],
                    **{
                        "v%d" % i: (["x" * 60] if i == 3 else [float(i)])
                        for i in range(1, 14)
                    },
                }
            )
        },
    )
    # by_date 文件名过期：d_20250601 早于 latest-12 天窗口（列内
    # ann_date=20260105）→ 窗口读空 → limit=300 兜底仍出样本
    mk(
        "tp_stale",
        {
            "d_20250601.parquet": pd.DataFrame(
                {"ts_code": ["600000.SH"], "ann_date": ["20260105"], "close": [1.0]}
            )
        },
    )
    # by_code 30 文件：日期随 i 递增、mtime 人为递减（i 大→mtime 旧）
    # → scandir 取 mtime 最新 25 = i0..i24（日期 20260101..20260125）
    # → 样本 max 恒 20260125（若 25 截断失效，max 会是 20260130）
    codes = ["A%04d.SZ" % i for i in range(30)]
    mk(
        "tp_many",
        {
            "%s.parquet" % c: pd.DataFrame(
                {"ts_code": [c], "trade_date": [str(20260101 + i)], "close": [1.0]}
            )
            for i, c in enumerate(codes)
        },
    )
    _t0 = time.time() - 3600
    for i, c in enumerate(codes):
        p = os.path.join(base, "tp_many", "%s.parquet" % c)
        os.utime(p, (_t0 + 29 - i, _t0 + 29 - i))  # i 越大 mtime 越旧

    mf = {
        t: {"done": True}
        for t in (
            "tp_day",
            "tp_ann",
            "tp_cal",
            "tp_empty",
            "tp_wide",
            "tp_stale",
            "tp_many",
        )
    }
    with open(os.path.join(base, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(mf, f, ensure_ascii=False)


def main():
    tmp = tempfile.mkdtemp(prefix="tp_test_")
    make_fixture(tmp)
    bc.build(data_root=tmp, out_path=os.path.join(tmp, "catalog.json"))
    dm.CATALOG = os.path.join(tmp, "catalog.json")
    dm._cat_cache.update(mtime=None, data=None)

    # 白名单随 FRESHNESS_SPEC 现读——追加测试表后生效
    orig_spec = list(dm.FRESHNESS_SPEC)
    dm.FRESHNESS_SPEC = orig_spec + [
        ("tp_day", "测试", 0, ""),
        ("tp_ann", "测试", 0, ""),
        ("tp_cal", "测试", 0, ""),
        ("tp_empty", "测试", 0, ""),
        ("tp_wide", "测试", 0, ""),
        ("tp_stale", "测试", 0, ""),
        ("tp_many", "测试", 0, ""),
    ]

    # ── 白名单 ──
    r = dm.table_preview("no_such_table")
    check("白名单外拒绝", "error" in r and "whitelist" in r["error"])
    r = dm.table_preview("daily")  # 真表名但 fixture 无数据
    check(
        "白名单内真表名可调（本仓空→不炸）",
        "error" not in r and isinstance(r.get("rows"), list),
    )

    # ── by_code 快路径 ──
    r = dm.table_preview("tp_day", n=8)
    check("by_code 样本非空", len(r["rows"]) == 5)
    check(
        "by_code 列名归一（ts_code/trade_date）",
        "ts_code" in r["cols"] and "trade_date" in r["cols"],
    )
    check(
        "by_code 日期倒序（最新 20260107 首、最旧 20260103 尾）",
        r["rows"][0]["trade_date"] == "20260107"
        and r["rows"][-1]["trade_date"] == "20260103",
    )
    check("date_col 识别 trade_date", r.get("date_col") == "trade_date")
    check("None 文本化为 —", r["rows"][2].get("vol") == "—")
    check("float 精度 %.4g（100.123456→100.1）", r["rows"][2].get("close") == "100.1")

    # ── n 边界 ──
    check("n=0 下限 1 行", len(dm.table_preview("tp_day", n=0)["rows"]) == 1)
    check("n=99 上限 20 行", len(dm.table_preview("tp_day", n=99)["rows"]) == 5)

    # ── by_date 窗口 ──
    r = dm.table_preview("tp_ann")
    check("by_date 混格式日期归一去重（3 行→2 行）", len(r["rows"]) == 2)
    check("by_date NaN 文本化为 —", r["rows"][0].get("close") == "—")
    check("by_date date_col=ann_date", r.get("date_col") == "ann_date")

    # ── part 无候选日期列 ──
    r = dm.table_preview("tp_cal")
    check(
        "part 无日期列不炸（date_col=None）",
        r.get("date_col") is None and len(r["rows"]) == 3,
    )

    # ── 空表兜底 ──
    r = dm.table_preview("tp_empty")
    check("空表 note 兜底", r["rows"] == [] and "note" in r)

    # ── 补充：边界与兜底 ──
    check("n=-5 下限仍 1 行", len(dm.table_preview("tp_day", n=-5)["rows"]) == 1)
    r = dm.table_preview("tp_wide")
    check(
        "宽表 cols 截 12 列",
        len(r["cols"]) == 12
        and r["cols"][0] == "ts_code"
        and r["cols"][1] == "trade_date",
    )
    check("长 str 截 48 字符", r["rows"][0].get("v3") == "x" * 48)
    r = dm.table_preview("tp_stale")
    check(
        "by_date 文件名过期走 limit 兜底",
        len(r["rows"]) == 1 and r["rows"][0]["ts_code"] == "600000.SH",
    )
    r = dm.table_preview("tp_many", n=20)
    check(
        "by_code 25 文件 mtime 截断（max 恒 20260125）",
        len(r["rows"]) == 20 and max(x["trade_date"] for x in r["rows"]) == "20260125",
    )

    # ── _prev_cache ──
    dm._prev_cache.clear()
    r1 = dm.table_preview("tp_day", n=3)
    check("首穿后写缓存", ("by_code", "tp_day") in dm._prev_cache)
    r2 = dm.table_preview("tp_day", n=3)
    check("缓存命中结果一致", r1 == r2)
    _stale = time.time() - 700
    dm._prev_cache[("by_code", "tp_day")] = (_stale, pd.DataFrame({"junk": [1]}))
    r3 = dm.table_preview("tp_day", n=3)
    check(
        "TTL 过期重读（弃陈旧缓存）",
        r3 == r1 and dm._prev_cache[("by_code", "tp_day")][0] > _stale,
    )
    dm._prev_cache.clear()

    # ── 门户包装层（import 失败降级跳过，保持零门户依赖）──
    try:
        pdir = os.path.join(os.path.dirname(HERE), "portal")
        sys.path.insert(0, pdir)
        import portal_api as pa

        ok_wrap = True
    except Exception as ex:
        print("SKIP  门户包装层（import 失败：%s）" % str(ex)[:60])
        ok_wrap = False
    if ok_wrap:
        r = pa.bridge_table_preview("tp_day", n=2)
        check("门户包装透传（rows 一致）", "error" not in r and len(r["rows"]) == 2)
        _orig = dm.table_preview
        dm.table_preview = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            r = pa.bridge_table_preview("tp_day")
            check("门户包装异常兜底 error", "error" in r and "boom" in r["error"])
        finally:
            dm.table_preview = _orig
        # 渲染静态构件：徽章/展开行/JS（防回归金断言）
        spec_tables = {t for t, _, _, _ in dm.FRESHNESS_SPEC if not t.startswith("tp_")}
        check("_TP_MKT 市场徽章与白名单 15 表同步", set(pa._TP_MKT) == spec_tables)
        check(
            "_TP_MKT 徽章语义抽查（A股/美股/加密）",
            pa._TP_MKT.get("daily") == "A股"
            and pa._TP_MKT.get("us_daily") == "美股"
            and pa._TP_MKT.get("crypto_daily") == "加密",
        )
        row_html = pa._tp_row("daily")
        check(
            "_tp_row 展开容器 id 成对",
            "id='tpr-daily'" in row_html
            and "id='tpc-daily'" in row_html
            and "colspan='5'" in row_html,
        )
        check(
            "_TP_JS 含 toggle 与穿透 API",
            "function tp_toggle" in pa._TP_JS
            and "/api/bridge/table_preview" in pa._TP_JS,
        )

    dm.FRESHNESS_SPEC = orig_spec
    print("\n%d pass, %d fail" % (PASS, FAIL))
    return FAIL


if __name__ == "__main__":
    sys.exit(main())
