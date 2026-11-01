# -*- coding: utf-8 -*-
"""acad1_pool_adapter：ACAD1 池只读适配器离线单测（临时 parquet 夹具，不依赖生产数据仓）。

覆盖：代码归一（CN sh→.SH、HK 补零）、CN/HK/US 三市场命中、HK 旧分区向前扫描、
缺失（daily/basic）、CRYPTO unsupported、ST 判定、data_lag_days、覆盖率、CLI 退出码、
--out 落盘、只读（不写数据仓）、输入错误。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

TOOL = Path(__file__).resolve().parents[1] / "acad1_pool_adapter.py"


def _load():
    name = "acad1_pool_adapter_under_test"
    spec = importlib.util.spec_from_file_location(name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def _cn_fixture(root: Path):
    _write(root / "tdx" / "ts_daily" / "part_0000.parquet", [
        {"ts_code": "688004.SH", "trade_date": "20261029", "close": 20.0,
         "pre_close": 19.5, "pct_chg": 2.56},
        {"ts_code": "688004.SH", "trade_date": "20261030", "close": 20.5,
         "pre_close": 20.0, "pct_chg": 2.5},
        {"ts_code": "000001.SZ", "trade_date": "20261030", "close": 12.0,
         "pre_close": 11.8, "pct_chg": 1.69},
    ])
    _write(root / "tdx" / "ts_stock_basic" / "part_0000.parquet", [
        {"ts_code": "688004.SH", "name": "*ST测试", "industry": "软件服务",
         "list_date": "20200101"},
        {"ts_code": "000001.SZ", "name": "平安银行", "industry": "银行",
         "list_date": "19910403"},
    ])
    _write(root / "tdx" / "ts_namechange" / "part_0000.parquet", [
        {"ts_code": "688004.SH", "name": "*ST测试", "start_date": "20260901",
         "end_date": "", "ann_date": "", "change_reason": "ST"},
    ])


def _hk_fixture(root: Path):
    _write(root / "tushare" / "hk_daily" / "d_20261029.parquet", [
        {"ts_code": "00700.HK", "trade_date": "20261029", "close": 383.6,
         "pre_close": 380.0, "pct_chg": 0.95},
    ])
    _write(root / "tushare" / "hk_daily" / "d_20261030.parquet", [
        {"ts_code": "00268.HK", "trade_date": "20261030", "close": 8.51,
         "pre_close": 8.4, "pct_chg": 1.31},
    ])
    _write(root / "tushare" / "hk_basic" / "part_0000.parquet", [
        {"ts_code": "00268.HK", "name": "比亚迪电子", "list_date": "20071201"},
        {"ts_code": "00700.HK", "name": "腾讯控股", "list_date": "20040616"},
        {"ts_code": "07552.HK", "name": "FI二南方恒指", "list_date": "20220101"},
    ])


def _us_fixture(root: Path):
    _write(root / "tushare" / "us_daily" / "CART.parquet", [
        {"ts_code": "CART", "trade_date": "20261029", "close": 57.11,
         "pre_close": 58.42, "pct_change": -2.24},
        {"ts_code": "CART", "trade_date": "20261030", "close": 56.45,
         "pre_close": 57.11, "pct_change": -1.16},
    ])
    # US basic 多轮追加存在重复行（取首行去重）
    _write(root / "tushare" / "us_basic" / "part_0000.parquet", [
        {"ts_code": "CART", "name": "Maplebear Inc.", "list_date": "20230919"},
        {"ts_code": "CART", "name": "Maplebear Inc.", "list_date": "20230919"},
    ])


def _all_fixtures(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    _cn_fixture(root)
    _hk_fixture(root)
    _us_fixture(root)


def _cards(tmp_path, cards=None, pool_date="2026-10-31"):
    data = {"schema": "acad1_review_cards/v1", "pool_date": pool_date,
            "cards": cards if cards is not None else [
                {"candidate_id": "sh688004", "name": "sh688004", "market": "CN"},
                {"candidate_id": "000001.SZ", "name": "000001.SZ", "market": "CN"},
                {"candidate_id": "00268", "name": "00268", "market": "HK"},
                {"candidate_id": "CART", "name": "CART", "market": "US"},
            ]}
    path = tmp_path / "cards.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _run(mod, tmp_path, cards_path=None, **kwargs):
    root = tmp_path / "data"
    if not (root / "tdx").exists():
        _all_fixtures(root)
    cards_path = cards_path or _cards(tmp_path)
    cards, pool_date, error = mod._load_cards(cards_path)
    assert error == ""
    return mod.build_report(cards_path, root, cards, pool_date, **kwargs)


def _tree_digest(root: Path) -> str:
    items = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            items.append("%s:%s" % (
                path.relative_to(root).as_posix(),
                hashlib.sha256(path.read_bytes()).hexdigest()[:16]))
    return "|".join(items)


# ---------------------------------------------------------------------------
# 代码归一
# ---------------------------------------------------------------------------
def test_normalize_code_cn():
    mod = _load()
    assert mod.normalize_code("sh688004", "CN") == "688004.SH"
    assert mod.normalize_code("SH688004", "CN") == "688004.SH"
    assert mod.normalize_code("688004.SH", "CN") == "688004.SH"
    assert mod.normalize_code("688004", "CN") == "688004.SH"
    assert mod.normalize_code("000301.SZ", "CN") == "000301.SZ"
    assert mod.normalize_code("sz000301", "CN") == "000301.SZ"
    assert mod.normalize_code("920680.BJ", "CN") == "920680.BJ"


def test_normalize_code_hk_us():
    mod = _load()
    assert mod.normalize_code("00268", "HK") == "00268.HK"
    assert mod.normalize_code("00268.HK", "HK") == "00268.HK"
    assert mod.normalize_code("700", "HK") == "00700.HK"
    assert mod.normalize_code("cart", "US") == "CART"
    assert mod.normalize_code("ETH/USDT", "CRYPTO") == "ETH/USDT"


# ---------------------------------------------------------------------------
# 命中 / 字段
# ---------------------------------------------------------------------------
def test_full_coverage_pass(tmp_path):
    mod = _load()
    report = _run(mod, tmp_path)
    assert report["verdict"] == "pass"
    assert report["summary"]["cards_total"] == 4
    assert report["summary"]["daily_hit"] == 4
    assert report["summary"]["basic_hit"] == 4
    assert report["summary"]["missing"] == 0
    assert report["missing"] == []
    by_market = report["summary"]["by_market"]
    assert by_market["CN"]["daily_coverage_pct"] == 100.0
    assert by_market["US"]["both_hit"] == 1


def test_card_fields_mapped(tmp_path):
    mod = _load()
    report = _run(mod, tmp_path)
    cards = {(c["market"], c["candidate_id"]): c for c in report["cards"]}
    cn = cards[("CN", "sh688004")]
    assert cn["code"] == "688004.SH"
    assert cn["name"] == "*ST测试"
    assert cn["data_date"] == "2026-10-30"
    assert cn["pre_close"] == 20.0
    assert cn["close"] == 20.5
    assert cn["pct_chg"] == 2.5
    assert cn["list_date"] == "2020-01-01"
    assert cn["industry"] == "软件服务"
    assert cn["is_st"] is True
    assert cn["daily_hit"] is True and cn["basic_hit"] is True
    us = cards[("US", "CART")]
    assert us["pct_chg"] == -1.16 and us["data_date"] == "2026-10-30"
    assert us["is_st"] is None


def test_st_false_for_normal_name(tmp_path):
    mod = _load()
    report = _run(mod, tmp_path)
    cn2 = [c for c in report["cards"] if c["code"] == "000001.SZ"][0]
    assert cn2["is_st"] is False


def test_hk_scans_older_partition(tmp_path):
    """新分区无该票（如停牌/缺失）时，向前扫旧分区取最新行。"""
    mod = _load()
    root = tmp_path / "data"
    _all_fixtures(root)
    cards = _cards(tmp_path, cards=[{"candidate_id": "00700", "market": "HK"}])
    report = _run(mod, tmp_path, cards_path=cards)
    card = report["cards"][0]
    assert card["daily_hit"] is True
    assert card["data_date"] == "2026-10-29"


# ---------------------------------------------------------------------------
# 缺失 / unsupported
# ---------------------------------------------------------------------------
def test_missing_daily_hit(tmp_path):
    mod = _load()
    cards = _cards(tmp_path, cards=[{"candidate_id": "07552", "market": "HK"}])
    report = _run(mod, tmp_path, cards_path=cards)
    card = report["cards"][0]
    assert card["daily_hit"] is False
    assert card["missing_reason"] == "daily_missing"
    assert report["verdict"] == "fail"
    assert report["missing"][0]["code"] == "07552.HK"


def test_missing_basic_hit(tmp_path):
    mod = _load()
    root = tmp_path / "data"
    _all_fixtures(root)
    _write(root / "tdx" / "ts_daily" / "part_0001.parquet", [
        {"ts_code": "002999.SZ", "trade_date": "20261030", "close": 5.0,
         "pre_close": 4.9, "pct_chg": 2.04},
    ])
    cards = _cards(tmp_path, cards=[{"candidate_id": "002999.SZ", "market": "CN"}])
    report = _run(mod, tmp_path, cards_path=cards)
    card = report["cards"][0]
    assert card["daily_hit"] is True
    assert card["basic_hit"] is False
    assert card["missing_reason"] == "basic_missing"


def test_crypto_unsupported(tmp_path):
    mod = _load()
    cards = _cards(tmp_path, cards=[
        {"candidate_id": "ETH/USDT", "market": "CRYPTO"},
        {"candidate_id": "CART", "market": "US"},
    ])
    report = _run(mod, tmp_path, cards_path=cards)
    crypto = [c for c in report["cards"] if c["market"] == "CRYPTO"][0]
    assert crypto["daily_hit"] is False and crypto["basic_hit"] is False
    assert crypto["missing_reason"] == "crypto_unsupported"
    assert crypto["data_date"] is None
    assert "CRYPTO" in report["summary"]["unsupported_markets"]
    assert report["summary"]["by_market"]["CRYPTO"]["daily_coverage_pct"] == 0.0
    assert report["verdict"] == "fail"


def test_data_lag_days(tmp_path):
    mod = _load()
    cards = _cards(tmp_path, pool_date="2026-11-02")
    report = _run(mod, tmp_path, cards_path=cards)
    cn = [c for c in report["cards"] if c["market"] == "CN"][0]
    assert cn["data_date"] == "2026-10-30"
    assert cn["data_lag_days"] == 3


# ---------------------------------------------------------------------------
# CLI / 落盘 / 只读
# ---------------------------------------------------------------------------
def _cli_env(tmp_path):
    root = tmp_path / "data"
    _all_fixtures(root)
    cards = _cards(tmp_path)
    return root, cards


def test_cli_json_pass(tmp_path, capsys):
    mod = _load()
    root, cards = _cli_env(tmp_path)
    rc = mod.main(["--json", "--cards", str(cards), "--data-root", str(root)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == mod.SCHEMA
    assert payload["verdict"] == "pass"


def test_cli_exit_codes(tmp_path, capsys):
    mod = _load()
    root, cards = _cli_env(tmp_path)
    missing = tmp_path / "missing.json"
    assert mod.main(["--json", "--cards", str(missing), "--data-root", str(root)]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert mod.main(["--json", "--cards", str(bad), "--data-root", str(root)]) == 2
    assert mod.main(["--json", "--cards", str(cards),
                     "--data-root", str(tmp_path / "nodata")]) == 2
    crypto_cards = _cards(tmp_path, cards=[{"candidate_id": "ETH/USDT", "market": "CRYPTO"}])
    assert mod.main(["--json", "--cards", str(crypto_cards),
                     "--data-root", str(root)]) == 1


def test_cli_out_json_and_readonly(tmp_path, capsys):
    mod = _load()
    root, cards = _cli_env(tmp_path)
    before = _tree_digest(root)
    out = tmp_path / "map.json"
    rc = mod.main(["--json", "--cards", str(cards), "--data-root", str(root),
                   "--out", str(out)])
    capsys.readouterr()
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == mod.SCHEMA
    assert payload["readonly"] is True
    # 只读：数据仓未被写入/改动
    assert _tree_digest(root) == before
    assert not list(root.rglob("*.tmp*"))


def test_cli_out_markdown(tmp_path, capsys):
    mod = _load()
    root, cards = _cli_env(tmp_path)
    out = tmp_path / "map.md"
    assert mod.main(["--json", "--cards", str(cards), "--data-root", str(root),
                     "--out", str(out)]) == 0
    capsys.readouterr()
    text = out.read_text(encoding="utf-8")
    assert "# ACAD1 池只读适配" in text
    assert "| 市场 | 卡数 |" in text


def test_markdown_render(tmp_path):
    mod = _load()
    report = _run(mod, tmp_path)
    text = mod.render_markdown(report)
    assert "# ACAD1 池只读适配" in text
    assert "## 缺失 / unsupported" in text
    assert "## 逐票明细" in text
    assert "688004.SH" in text


def test_pool_date_override(tmp_path):
    mod = _load()
    root = tmp_path / "data"
    _all_fixtures(root)
    cards = _cards(tmp_path, pool_date="2026-10-31")
    result = mod.main(["--json", "--cards", str(cards), "--data-root", str(root),
                       "--pool-date", "2026-11-02"])
    assert result == 0


def test_cards_list_form_without_pool_date(tmp_path):
    mod = _load()
    root = tmp_path / "data"
    _all_fixtures(root)
    cards = tmp_path / "cards_list.json"
    cards.write_text(json.dumps([
        {"candidate_id": "000001.SZ", "market": "CN"},
    ], ensure_ascii=False), encoding="utf-8")
    loaded, pool_date, error = mod._load_cards(cards)
    assert error == "" and len(loaded) == 1 and pool_date is None
    report = mod.build_report(cards, root, loaded, pool_date)
    assert report["cards"][0]["data_lag_days"] is None


def test_empty_cards_input_error(tmp_path, capsys):
    mod = _load()
    root = tmp_path / "data"
    _all_fixtures(root)
    cards = tmp_path / "empty.json"
    cards.write_text(json.dumps({"cards": []}), encoding="utf-8")
    assert mod.main(["--json", "--cards", str(cards), "--data-root", str(root)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_report_json_serializable(tmp_path):
    mod = _load()
    report = _run(mod, tmp_path)
    json.dumps(report, ensure_ascii=False)


def test_resolve_data_root_env_override(tmp_path, monkeypatch):
    mod = _load()
    root = tmp_path / "data"
    _all_fixtures(root)
    monkeypatch.setenv("ACAD1_DATA_ROOT", str(root))
    assert mod.resolve_data_root(None) == root
    explicit = tmp_path / "explicit-root"
    assert mod.resolve_data_root(str(explicit)) == explicit
