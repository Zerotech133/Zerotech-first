"""申万行业历史成分拉取工具测试（FakeSession，不联网）。"""

from __future__ import annotations

import pandas as pd
import pytest

from tools.data_sources.tushare_sw_member import (
    TusharePermissionError,
    fetch_level,
    fetch_one,
    load_classify_codes,
    load_token,
    normalize_member_rows,
)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payloads: dict):
        self.payloads = payloads
        self.calls: list[dict] = []

    def post(self, url, json=None, timeout=None):
        self.calls.append(json or {})
        code = (json or {}).get("params", {}).get("index_code")
        return _Resp(self.payloads.get(code, {"code": 0, "data": {"fields": [], "items": []}}))


def _payload(rows):
    fields = ["index_code", "con_code", "in_date", "out_date", "is_new"]
    return {"code": 0, "data": {"fields": fields, "items": rows}}


def _classify(tmp_path, level="L2"):
    p = tmp_path / "classify"
    p.mkdir()
    pd.DataFrame([
        {"index_code": "801011.SI", "industry_name": "林业", "level": "L2"},
        {"index_code": "801012.SI", "industry_name": "农产品加工", "level": "L2"},
        {"index_code": "801010.SI", "industry_name": "农林牧渔", "level": "L1"},
    ]).to_parquet(p / "part_0000.parquet", index=False)
    return p


def test_normalize_member_rows_dedup_and_sort():
    payload = _payload([
        ["801011.SI", "000001.SZ", "20200101", None, "Y"],
        ["801011.SI", "000001.SZ", "20200101", None, "Y"],
        ["801011.SI", "000002.SZ", "20190101", "20200101", "N"],
    ])
    df = normalize_member_rows(payload, "801011.SI")
    assert len(df) == 2
    assert list(df["con_code"]) == ["000001.SZ", "000002.SZ"]


def test_load_classify_codes_filters_level(tmp_path):
    d = _classify(tmp_path)
    assert load_classify_codes(d, "L2") == ["801011.SI", "801012.SI"]
    assert load_classify_codes(d, "L1") == ["801010.SI"]


def test_fetch_one_permission_error():
    session = FakeSession({"801011.SI": {"code": 40203, "msg": "无权限"}})
    with pytest.raises(TusharePermissionError):
        fetch_one("801011.SI", "token", session=session)


def test_fetch_level_resume_and_empty(tmp_path):
    classify = _classify(tmp_path)
    out = tmp_path / "members"
    session = FakeSession({
        "801011.SI": _payload([["801011.SI", "000001.SZ", "20200101", None, "Y"]]),
        "801012.SI": _payload([]),
    })
    s1 = fetch_level("L2", classify_dir=classify, out_dir=out, token="t",
                     session=session, sleep=0)
    assert s1["index_count"] == 2
    assert s1["fetched"] == 1
    assert len(s1["empty"]) == 1
    assert s1["rows"] == 1
    assert (out / "801011.SI.parquet").is_file()

    s2 = fetch_level("L2", classify_dir=classify, out_dir=out, token="t",
                     session=session, sleep=0)
    assert s2["skipped"] == 1
    assert s2["fetched"] == 0


def test_fetch_level_strict_empty(tmp_path):
    classify = _classify(tmp_path)
    session = FakeSession({"801011.SI": _payload([]), "801012.SI": _payload([])})
    s = fetch_level("L2", classify_dir=classify, out_dir=tmp_path / "m", token="t",
                    session=session, sleep=0, allow_empty=False)
    assert len(s["failed"]) == 2
    assert s["empty"] == []


def test_load_token_env_precedence(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "aaa")
    monkeypatch.setenv("XT_DATA_KEY", "bbb")
    assert load_token() == "aaa"
    monkeypatch.delenv("TUSHARE_TOKEN")
    assert load_token() == "bbb"
