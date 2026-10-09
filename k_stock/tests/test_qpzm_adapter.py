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
    p.write_text(json.dumps({"res": rows, "ts": "2026-10-09 16:00"}, ensure_ascii=False),
                 encoding="utf-8")
    return p


def test_cn_buy_in_us_buy_skip_gate_blocked_out():
    rows = [
        {"code": "600593.SH", "name": "大连圣亚", "market": "CN", "action": "买入",
         "score": 78, "px": 12.3, "dims": "tech+rps", "rps": 90.1},
        {"code": "gb_pltr", "name": "Palantir", "market": "US", "action": "买入", "score": 86},
        {"code": "btc_usd", "name": "BTC", "market": "CRYPTO", "action": "买入", "score": 70},
        {"code": "000001.SZ", "name": "平安银行", "market": "CN", "action": "观察"},
        {"code": "600887.SH", "name": "伊利股份", "market": "CN", "action": "回避（环境闸门）"},
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
        assert it["_meta"]["score"] == 78          # 原值进 _meta 供审计
        assert out.name.startswith("poc_qpzm_")    # 命名与双刀 poc_qd_ 同族
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
        print("PASS test_real_latest_fail_loud_or_convert (fail-loud:", str(e)[:50], ")")
        return
    items = json.loads(out.read_text(encoding="utf-8"))
    for it in items:
        code = it["stock"]
        assert code[-3:] in (".SH", ".SZ"), f"非 A 股格式混入: {code}"
    print(f"PASS test_real_latest_fail_loud_or_convert ({len(items)} CN buys)")


def test_split_us_routes_us_buys():
    """K-STOCK-044 M2：split_us=True 时 US 买入行产独立 poc_qpzmus_ 文件；CRYPTO 仍 skip。"""
    rows = [
        {"code": "600593.SH", "name": "大连圣亚", "market": "CN", "action": "买入", "score": 78},
        {"code": "gb_PLTR", "name": "Palantir", "market": "US", "action": "买入", "score": 86},
        {"code": "btc_usd", "name": "BTC", "market": "CRYPTO", "action": "买入", "score": 70},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        cn_path, us_path = cache_to_run(_write_res(rows, tmp), out_dir=Path(tmp),
                                        split_us=True)
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
    rows = [{"code": "gb_aapl", "name": "Apple", "market": "US", "action": "买入", "score": 80}]
    with tempfile.TemporaryDirectory() as tmp:
        cn_path, us_path = cache_to_run(_write_res(rows, tmp), out_dir=Path(tmp),
                                        split_us=True)
        assert cn_path is None and us_path is not None
        try:
            cache_to_run(_write_res(rows, tmp), out_dir=Path(tmp))
            raise AssertionError("split_us=False 且无 CN 买入应 raise")
        except ValueError as e:
            assert "无 CN 买入行" in str(e)
    print("PASS test_split_us_all_us_no_cn")


if __name__ == "__main__":
    test_cn_buy_in_us_buy_skip_gate_blocked_out()
    test_no_buy_rows_raises()
    test_split_us_routes_us_buys()
    test_split_us_false_keeps_legacy()
    test_split_us_all_us_no_cn()
    test_real_latest_fail_loud_or_convert()
    print("5/5 PASS (+1 真实产物用例)")
