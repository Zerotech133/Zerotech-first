# -*- coding: utf-8 -*-
"""青萍之末 adapter 单测（B1/K-STOCK-041）：合成产物测转换过滤，真实产物测 fail-loud。"""

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".." / "paper"))

from qpzm_adapter import cache_to_run, QINGPING_CONF  # noqa: E402


def _write_res(rows, tmp):
    p = Path(tmp) / "rps_latest.json"
    p.write_text(
        json.dumps({"res": rows, "ts": "2026-10-09 16:00"}, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


def test_cn_buy_in_us_buy_skip_gate_blocked_out():
    rows = [
        {
            "code": "600593.SH",
            "name": "大连圣亚",
            "market": "CN",
            "action": "买入",
            "score": 78,
            "px": 12.3,
            "dims": "tech+rps",
            "rps": 90.1,
        },
        {
            "code": "gb_pltr",
            "name": "Palantir",
            "market": "US",
            "action": "买入",
            "score": 86,
        },
        {
            "code": "btc_usd",
            "name": "BTC",
            "market": "CRYPTO",
            "action": "买入",
            "score": 70,
        },
        {"code": "000001.SZ", "name": "平安银行", "market": "CN", "action": "观察"},
        {
            "code": "600887.SH",
            "name": "伊利股份",
            "market": "CN",
            "action": "回避（环境闸门）",
        },
    ]
    with tempfile.TemporaryDirectory() as tmp:
        out = cache_to_run(_write_res(rows, tmp), out_dir=Path(tmp))
        items = json.loads(out.read_text(encoding="utf-8"))
        assert len(items) == 1, f"应只收 CN 买入 1 条，得 {len(items)}"
        it = items[0]
        assert it["stock"] == "600593.SH"
        assert it["outputs"]["D1"]["action"] == "BUY"
        assert it["outputs"]["D1"]["confidence"] == QINGPING_CONF == 0.60
        assert it["_meta"]["source"] == "qingping"
        assert it["_meta"]["score"] == 78  # 原值进 _meta 供审计
        assert out.name.startswith("poc_qpzm_")  # 命名与双刀 poc_qd_ 同族
    print("PASS test_cn_buy_in_us_buy_skip_gate_blocked_out")


def test_no_buy_rows_raises():
    rows = [{"code": "600593.SH", "market": "CN", "action": "回避"}]
    with tempfile.TemporaryDirectory() as tmp:
        try:
            cache_to_run(_write_res(rows, tmp), out_dir=Path(tmp))
            raise AssertionError("无买入行应 raise（fail-loud）")
        except ValueError as e:
            assert "无买入行" in str(e)
    print("PASS test_no_buy_rows_raises")


def test_real_latest_fail_loud_or_convert():
    """真实 rps_latest.json：无 CN 买入时 fail-loud（当前 CN 全被环境闸门挡，预期路径）；
    有 CN 买入时转换成功且 stock 全部 A 股格式。"""
    try:
        out = cache_to_run()
    except (ValueError, FileNotFoundError) as e:
        print(
            "PASS test_real_latest_fail_loud_or_convert (fail-loud:", str(e)[:50], ")"
        )
        return
    items = json.loads(out.read_text(encoding="utf-8"))
    for it in items:
        code = it["stock"]
        assert code[-3:] in (".SH", ".SZ"), f"非 A 股格式混入: {code}"
    print(f"PASS test_real_latest_fail_loud_or_convert ({len(items)} CN buys)")


def test_split_us_routes_us_buys():
    """K-STOCK-044 M2：split_us=True 时 US 买入行产独立 poc_qpzmus_ 文件；CRYPTO 仍 skip。"""
    rows = [
        {
            "code": "600593.SH",
            "name": "大连圣亚",
            "market": "CN",
            "action": "买入",
            "score": 78,
        },
        {
            "code": "gb_PLTR",
            "name": "Palantir",
            "market": "US",
            "action": "买入",
            "score": 86,
        },
        {
            "code": "btc_usd",
            "name": "BTC",
            "market": "CRYPTO",
            "action": "买入",
            "score": 70,
        },
    ]
    with tempfile.TemporaryDirectory() as tmp:
        cn_path, us_path = cache_to_run(
            _write_res(rows, tmp), out_dir=Path(tmp), split_us=True
        )
        assert cn_path and us_path
        assert cn_path.name.startswith("poc_qpzm_")
        assert us_path.name.startswith("poc_qpzmus_")
        us_items = json.loads(us_path.read_text(encoding="utf-8"))
        assert len(us_items) == 1 and us_items[0]["stock"] == "gb_PLTR"
        assert us_items[0]["outputs"]["D1"]["confidence"] == QINGPING_CONF
    print("PASS test_split_us_routes_us_buys")


def test_split_us_false_keeps_legacy():
    """默认口径（split_us=False）：US 行回退 skipped、只返回 CN 单值路径（历史行为零变化）。"""
    rows = [
        {"code": "600593.SH", "market": "CN", "action": "买入", "score": 78},
        {"code": "gb_pltr", "market": "US", "action": "买入", "score": 86},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        out = cache_to_run(_write_res(rows, tmp), out_dir=Path(tmp))
        items = json.loads(out.read_text(encoding="utf-8"))
        assert len(items) == 1 and items[0]["stock"] == "600593.SH"
        assert Path(out).exists() and not list(Path(tmp).glob("poc_qpzmus_*"))
    print("PASS test_split_us_false_keeps_legacy")


def test_split_us_all_us_no_cn():
    """全 US 买入：split_us=True 时 cn_path=None、us_path 有效；split_us=False fail-loud。"""
    rows = [
        {
            "code": "gb_aapl",
            "name": "Apple",
            "market": "US",
            "action": "买入",
            "score": 80,
        }
    ]
    with tempfile.TemporaryDirectory() as tmp:
        cn_path, us_path = cache_to_run(
            _write_res(rows, tmp), out_dir=Path(tmp), split_us=True
        )
        assert cn_path is None and us_path is not None
        try:
            cache_to_run(_write_res(rows, tmp), out_dir=Path(tmp))
            raise AssertionError("split_us=False 且无 CN 买入应 raise")
        except ValueError as e:
            assert "无 CN 买入行" in str(e)
    print("PASS test_split_us_all_us_no_cn")


def test_split_hk_routes_hk_buys_with_code_convert():
    """H-D2：split_hk=True 时 HK 买入行产独立 poc_qpzmhk_ 文件；watchlist 形态
    rt_hk00700 适配器内转引擎规范码 00700.HK；CRYPTO 仍 skip；三元组返回。"""
    rows = [
        {
            "code": "600593.SH",
            "name": "大连圣亚",
            "market": "CN",
            "action": "买入",
            "score": 78,
        },
        {
            "code": "rt_hk00700",
            "name": "腾讯控股",
            "market": "HK",
            "action": "买入",
            "score": 88,
        },
        {
            "code": "rt_hk9988",
            "name": "阿里巴巴-W",
            "market": "HK",
            "action": "买入",
            "score": 85,
        },
        {
            "code": "btc_usd",
            "name": "BTC",
            "market": "CRYPTO",
            "action": "买入",
            "score": 70,
        },
    ]
    with tempfile.TemporaryDirectory() as tmp:
        cn_path, us_path, hk_path = cache_to_run(
            _write_res(rows, tmp), out_dir=Path(tmp), split_us=True, split_hk=True
        )
        assert cn_path and us_path is None and hk_path
        assert hk_path.name.startswith("poc_qpzmhk_")
        hk_items = json.loads(hk_path.read_text(encoding="utf-8"))
        assert len(hk_items) == 2
        assert hk_items[0]["stock"] == "00700.HK"  # rt_hk00700 → 5 位规范码
        assert hk_items[1]["stock"] == "09988.HK"  # 老式 4 位 zfill(5)
        assert hk_items[0]["outputs"]["D1"]["confidence"] == QINGPING_CONF
        assert hk_items[0]["_meta"]["name"] == "腾讯控股"
    print("PASS test_split_hk_routes_hk_buys_with_code_convert")


def test_split_hk_false_keeps_legacy():
    """默认口径（split_hk=False）：HK 行回退 skipped、返回形态与历史完全一致
    （split_us=False 单值 / split_us=True 二元组），不产 poc_qpzmhk_ 文件。"""
    rows = [
        {"code": "600593.SH", "market": "CN", "action": "买入", "score": 78},
        {"code": "rt_hk00700", "market": "HK", "action": "买入", "score": 88},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        out = cache_to_run(_write_res(rows, tmp), out_dir=Path(tmp))
        assert Path(out).exists() and not list(Path(tmp).glob("poc_qpzmhk_*"))
        cn_path, us_path = cache_to_run(
            _write_res(rows, tmp), out_dir=Path(tmp), split_us=True
        )
        assert cn_path and us_path is None
        assert not list(Path(tmp).glob("poc_qpzmhk_*"))
    print("PASS test_split_hk_false_keeps_legacy")


def test_split_hk_all_hk_no_cn():
    """全 HK 买入：split_hk=True 时 cn/us 均 None、hk 有效；split_hk=False 且
    无 CN 买入仍 fail-loud（历史口径不变）。"""
    rows = [
        {
            "code": "rt_hk00700",
            "name": "腾讯控股",
            "market": "HK",
            "action": "买入",
            "score": 88,
        }
    ]
    with tempfile.TemporaryDirectory() as tmp:
        cn_path, us_path, hk_path = cache_to_run(
            _write_res(rows, tmp), out_dir=Path(tmp), split_us=True, split_hk=True
        )
        assert cn_path is None and us_path is None and hk_path is not None
        try:
            cache_to_run(_write_res(rows, tmp), out_dir=Path(tmp))
            raise AssertionError("split_hk=False 且无 CN 买入应 raise")
        except ValueError as e:
            assert "无 CN 买入行" in str(e)
    print("PASS test_split_hk_all_hk_no_cn")


def test_hk_engine_code_variants():
    """_hk_engine_code：rt_hk 前缀剥离 + 老票 1-4 位 zfill(5)，全部归一 .HK。"""
    from qpzm_adapter import _hk_engine_code

    assert _hk_engine_code("rt_hk00700") == "00700.HK"
    assert _hk_engine_code("rt_hk9988") == "09988.HK"
    assert _hk_engine_code("rt_hk1") == "00001.HK"  # 长和（老票 1 位）
    assert _hk_engine_code("00700") == "00700.HK"  # 已剥前缀形态兜底
    print("PASS test_hk_engine_code_variants")


def test_run_hk_only_no_cn_fabrication():
    """--hk-only 裁决钉：CN 买入存在也不写 poc_qpzm_ 文件、不报错（防夹带）；
    HK 买入正常路由（hk exec 由灰态用例覆盖，此处只钉产物面）。"""
    from qpzm_adapter import cache_to_run

    rows = [
        {"code": "600593.SH", "market": "CN", "action": "买入", "score": 78},
        {"code": "rt_hk00700", "market": "HK", "action": "买入", "score": 88},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        cn_path, us_path, hk_path = cache_to_run(
            _write_res(rows, tmp), out_dir=Path(tmp), split_hk=True, write_cn=False
        )
        assert cn_path is None and us_path is None and hk_path is not None
        assert not list(Path(tmp).glob("poc_qpzm_*"))
        assert list(Path(tmp).glob("poc_qpzmhk_*"))
    print("PASS test_run_hk_only_no_cn_fabrication")


def test_run_hk_only_gray_no_buys():
    """--hk-only 灰态：全回避产物 → run_hk_only 返回 note 正常态不 raise
    （4e 安静日不作失败；force_trading 绕守卫，无 HK 买入不触引擎/账本）。"""
    import qpzm_adapter

    rows = [
        {"code": "600593.SH", "market": "CN", "action": "回避（环境闸门）"},
        {"code": "rt_hk00700", "market": "HK", "action": "观察"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        cache = _write_res(rows, tmp)
        res = qpzm_adapter.run_hk_only(
            dry_run=True, cache_path=Path(cache), force_trading=True
        )
        assert res["hk"] is None and res["orders"] == []
        assert "灰态正常" in res.get("note", "")
    print("PASS test_run_hk_only_gray_no_buys")


def test_run_hk_gray_state_skip():
    """_run_hk 灰态：无 hk_% 账本（hk_pass→None）→ ALL skipped 不炸、ok 语义。
    force_trading=True 绕交易日守卫（守卫语义由 trade_guard 既有测试覆盖）。"""
    import qpzm_adapter

    run_path = Path(tempfile.mkdtemp()) / "poc_qpzmhk_20261020_070505.json"
    run_path.write_text(
        json.dumps([{"stock": "00700.HK"}], ensure_ascii=False), encoding="utf-8"
    )
    orig_hk_pass = None
    sys.path.insert(0, str(HERE / ".." / "paper"))
    import hk_market

    orig_hk_pass = hk_market.hk_pass
    try:
        hk_market.hk_pass = lambda do_refresh=True: None
        res = qpzm_adapter._run_hk(run_path, dry_run=True, force_trading=True)
        assert res["skipped"] and res["skipped"][0]["stock"] == "ALL"
        assert res["orders"] == []
    finally:
        hk_market.hk_pass = orig_hk_pass
    print("PASS test_run_hk_gray_state_skip")


def test_run_cn_us_gray_no_buys():
    """--cnus 灰态：全回避产物 → note 正常态不 raise（4d 安静日不作失败，
    同 4d 验收单「无买入」口径；force_trading 绕守卫，无买入不触引擎）。"""
    import qpzm_adapter

    rows = [
        {"code": "600593.SH", "market": "CN", "action": "回避（环境闸门）"},
        {"code": "gb_pltr", "market": "US", "action": "观察"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        cache = _write_res(rows, tmp)
        res = qpzm_adapter.run_cn_us(
            dry_run=True, cache_path=Path(cache), force_trading=True
        )
        assert res["us"] is None and res["orders"] == []
        assert "灰态正常" in res.get("note", "")
    print("PASS test_run_cn_us_gray_no_buys")


def test_run_cn_us_hk_rows_sidelined():
    """--cnus 分工钉：HK 买入行不产 poc_qpzmhk 产物、结果无 hk 段（HK 落单
    归属 4e --hk-only，4d 不得夹带）；CN 路正常 exec（引擎 stub 隔离真账本）。"""
    import market
    import engine
    import qpzm_adapter

    rows = [
        {"code": "600593.SH", "market": "CN", "action": "买入", "score": 78},
        {"code": "rt_hk00700", "market": "HK", "action": "买入", "score": 88},
    ]
    calls = {}

    class _FakeEng:
        def __init__(self, *a, **k):
            pass

        def exec_run(self, path, **k):
            calls["cn"] = Path(path).name
            return {"run": Path(path).name, "orders": [], "skipped": []}

    orig = (
        market.ParquetMarket,
        engine.PaperEngine,
        qpzm_adapter._run_us,
        qpzm_adapter.DATA_DIR,
    )
    try:
        market.ParquetMarket = lambda: object()
        engine.PaperEngine = _FakeEng
        qpzm_adapter._run_us = lambda *a, **k: {"account_id": "stub"}
        with tempfile.TemporaryDirectory() as tmp:
            qpzm_adapter.DATA_DIR = Path(tmp)
            cache = _write_res(rows, tmp)
            res = qpzm_adapter.run_cn_us(
                dry_run=True, cache_path=Path(cache), force_trading=True
            )
            assert calls.get("cn", "").startswith("poc_qpzm_")  # CN 路正常
            assert not list(Path(tmp).glob("poc_qpzmhk_*"))  # HK 不产文件
            assert "hk" not in res  # HK 段零触发
    finally:
        (
            market.ParquetMarket,
            engine.PaperEngine,
            qpzm_adapter._run_us,
            qpzm_adapter.DATA_DIR,
        ) = orig
    print("PASS test_run_cn_us_hk_rows_sidelined")


if __name__ == "__main__":
    test_cn_buy_in_us_buy_skip_gate_blocked_out()
    test_no_buy_rows_raises()
    test_split_us_routes_us_buys()
    test_split_us_false_keeps_legacy()
    test_split_us_all_us_no_cn()
    test_split_hk_routes_hk_buys_with_code_convert()
    test_split_hk_false_keeps_legacy()
    test_split_hk_all_hk_no_cn()
    test_hk_engine_code_variants()
    test_run_hk_only_no_cn_fabrication()
    test_run_hk_only_gray_no_buys()
    test_run_hk_gray_state_skip()
    test_run_cn_us_gray_no_buys()
    test_run_cn_us_hk_rows_sidelined()
    test_real_latest_fail_loud_or_convert()
    print("14/14 PASS (+1 真实产物用例)")
