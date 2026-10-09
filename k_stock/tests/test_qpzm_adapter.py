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


if __name__ == "__main__":
    test_cn_buy_in_us_buy_skip_gate_blocked_out()
    test_no_buy_rows_raises()
    test_real_latest_fail_loud_or_convert()
    print("3/3 PASS")
