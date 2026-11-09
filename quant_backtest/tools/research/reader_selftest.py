# -*- coding: utf-8 -*-
"""warehouse_reader 自测（P0 契约验证）。只读，不写数据。"""
import sys, warnings, traceback
sys.path.insert(0, "C:/项目/老田的AI助手/quant_backtest")
from warehouse_reader import (
    WarehouseReader, UnknownAssetError, OutOfBoundsError,
    EmptyResultError, ColumnError, ReplicaWarning,
)

R = WarehouseReader()
PASS, FAIL = [], []

def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"[PASS] {name}")
    except Exception as e:
        FAIL.append(name)
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        traceback.print_exc()

# 1. info 暴露多副本覆盖
def t_info():
    info = R.info("daily")
    dirs = [c["dir"].replace("\\", "/") for c in info["candidates"]]
    assert any("tushare/daily" in d for d in dirs), dirs
    assert any("tushare_v2/daily" in d for d in dirs), dirs
    # 两个非 staging 副本
    nonstg = [c for c in info["candidates"] if not c["is_staging"]]
    assert len(nonstg) >= 2, nonstg
check("info() 暴露 daily 双副本覆盖", t_info)

# 2. authoritative 默认并集 → 最完整覆盖（取 1991 起）
def t_union_coverage():
    info = R.info("daily")
    nonstg = [c for c in info["candidates"] if not c["is_staging"]]
    dmin = min(c["date_min"] for c in nonstg)
    assert str(dmin) <= "19910102", dmin   # tushare/daily 超集
check("authoritative 并集覆盖=1991 起", t_union_coverage)

# 3. 越界报错（us_daily 最早 1973，查 1950 必报错）
def t_out_of_bounds():
    try:
        R.read("us_daily", start="19000101", end="19500101")
        raise AssertionError("应当抛出 OutOfBoundsError")
    except OutOfBoundsError:
        pass
check("越界查询 → OutOfBoundsError", t_out_of_bounds)

# 4. 空结果报错（过滤到不存在的代码）
def t_empty():
    try:
        R.read("daily", where="ts_code='999999.XX'")
        raise AssertionError("应当抛出 EmptyResultError")
    except EmptyResultError:
        pass
check("过滤空结果 → EmptyResultError", t_empty)

# 4b. 允许空时返回 0 行而不报错
def t_allow_empty():
    df = R.read("daily", where="ts_code='999999.XX'", require_nonempty=False)
    assert len(df) == 0
check("require_nonempty=False 返回 0 行", t_allow_empty)

# 5. 列名不存在 → ColumnError
def t_column():
    try:
        R.read("us_daily", columns=["no_such_col"], limit=1)
        raise AssertionError("应当抛出 ColumnError")
    except ColumnError:
        pass
check("列不存在 → ColumnError", t_column)

# 6. 未知资产 → UnknownAssetError
def t_unknown():
    try:
        R.read("this_asset_does_not_exist_xyz")
        raise AssertionError("应当抛出 UnknownAssetError")
    except UnknownAssetError:
        pass
check("未知资产 → UnknownAssetError", t_unknown)

# 7. schema 漂移：us_daily trade_date 部分 VARCHAR，union_by_name 必须可读
def t_drift():
    df = R.read("us_daily", start="20260101", end="20260131", limit=50)
    assert len(df) > 0
    assert "trade_date" in df.columns
check("us_daily schema 漂移可读(union_by_name)", t_drift)

# 8. 多副本可见告警（daily 应 warn）
def t_replica_warn():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        R.read("daily", start="20260101", end="20260131", limit=10)
        assert any(issubclass(x.category, ReplicaWarning) for x in w), \
            [str(x.message)[:60] for x in w]
check("daily 多副本 → ReplicaWarning", t_replica_warn)

# 9. 钉选特定副本（replica='tushare_v2'）
def t_pin_replica():
    df = R.read("daily", replica="tushare_v2",
                start="20240101", end="20240131", limit=10)
    assert len(df) > 0
check("replica 钉选 tushare_v2", t_pin_replica)

# 10. 日期列自动探测（report_rc 注册表为 None，真实 report_date）
def t_date_autodetect():
    info = R.info("report_rc")
    # 至少有一个候选能探测到日期列
    ok = any(c["date_min"] for c in info["candidates"])
    assert ok, info["candidates"]
check("report_rc 日期列自动探测", t_date_autodetect)

# 11. 真实取数样例（不报错，打印规模）
def t_sample():
    df = R.read("us_daily", columns=["ts_code", "trade_date", "close"],
               start="20261101", end="20261104", limit=5)
    assert len(df) > 0
    print("     us_daily 样例:", df.to_dict("records")[:2])
check("真实取数样例", t_sample)

R.close()
print("\n==== 结果 ====")
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
print("全部通过。")
