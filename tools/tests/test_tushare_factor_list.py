# -*- coding: utf-8 -*-
"""tools/tests/test_tushare_factor_list.py · factor_list 接入器单元测试。

不访问真实网络：用 FakeSession 注入 Tushare JSON 响应。
覆盖：token 解析、成功拉取、限频重试、权限/认证/网络错误、schema 校验、
去重排序、离线 validate、快照/latest/manifest、CLI check/status/show、
CLI fetch --dry-run、无 token 退出码。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "data_sources"))

import tushare_factor_list as m  # noqa: E402


class FakeResp:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if not self.payloads:
            raise AssertionError("FakeSession payloads exhausted")
        item = self.payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_payload(rows, fields=None, code=0, msg=""):
    fields = list(fields or m.FIELDS)
    items = [[row.get(field) for field in fields] for row in rows]
    return {"code": code, "msg": msg, "data": {"fields": fields, "items": items}}


def sample_rows(n=3):
    rows = []
    types = ["Growth", "Alpha101", "Value"]
    for i in range(n):
        ftype = types[i % len(types)]
        rows.append({
            "factor_name": f"factor_{i:03d}",
            "asset_type": "STK",
            "factor_type": ftype,
            "factor_desc": f"desc {i}",
        })
    return rows


def test_load_token_from_env(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("XT_DATA_KEY", raising=False)
    assert m.load_token(env={"TUSHARE_TOKEN": "abc"}) == "abc"
    assert m.load_token(env={"XT_DATA_KEY": "xyz"}) == "xyz"


def test_load_token_from_env_local(tmp_path, monkeypatch):
    env_local = tmp_path / "quant_backtest" / ".env.local"
    env_local.parent.mkdir(parents=True)
    env_local.write_text("XT_DATA_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setattr(m, "ROOT", tmp_path)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("XT_DATA_KEY", raising=False)
    assert m.load_token(env={}) == "from-file"


def test_fetch_success():
    sess = FakeSession([FakeResp(make_payload(sample_rows(2)))])
    data = m.fetch_factor_list("tk", params={"factor_type": "Risk"}, session=sess)
    assert data["code"] == 0
    assert data["fields"] == list(m.FIELDS)
    assert len(data["items"]) == 2
    assert sess.calls[0]["json"]["api_name"] == "factor_list"
    assert sess.calls[0]["json"]["params"] == {"factor_type": "Risk"}
    assert sess.calls[0]["json"]["token"] == "tk"


def test_fetch_permission_error():
    sess = FakeSession([FakeResp(make_payload([], code=40203, msg="没有权限"))])
    with pytest.raises(m.TusharePermissionError) as exc:
        m.fetch_factor_list("tk", session=sess, retries=0)
    assert "因子库" in str(exc.value)


def test_fetch_auth_error():
    sess = FakeSession([FakeResp(make_payload([], code=40101, msg="token不对"))])
    with pytest.raises(m.TushareAuthError):
        m.fetch_factor_list("tk", session=sess, retries=0)


def test_fetch_retries_rate_limit_then_success():
    sess = FakeSession([
        FakeResp(make_payload([], code=40002, msg="请求过于频繁")),
        FakeResp(make_payload(sample_rows(1))),
    ])
    data = m.fetch_factor_list("tk", retries=1, sleep=0, session=sess)
    assert data["code"] == 0
    assert len(sess.calls) == 2


def test_fetch_network_error_after_retries():
    import requests

    sess = FakeSession([requests.RequestException("boom"), requests.RequestException("boom")])
    with pytest.raises(m.TushareNetworkError):
        m.fetch_factor_list("tk", retries=1, sleep=0, session=sess)


def test_normalize_dedupes_and_sorts():
    rows = [
        {"factor_name": "b", "asset_type": "STK", "factor_type": "Risk", "factor_desc": "B"},
        {"factor_name": "a", "asset_type": "STK", "factor_type": "Alpha101", "factor_desc": "A"},
        {"factor_name": "b", "asset_type": "STK", "factor_type": "Risk", "factor_desc": "dup"},
    ]
    normalized, warnings = m.normalize_factor_rows(make_payload(rows))
    assert [row["factor_name"] for row in normalized] == ["a", "b"]
    assert normalized[0]["factor_type"] == "Alpha101"
    assert any("重复" in warning for warning in warnings)


def test_normalize_rejects_missing_required_field():
    payload = {
        "code": 0,
        "data": {"fields": ["factor_name", "asset_type", "factor_type"], "items": [["x", "STK", "Risk"]]},
    }
    with pytest.raises(m.TushareSchemaError) as exc:
        m.normalize_factor_rows(payload)
    assert "factor_desc" in str(exc.value)


def test_validate_rows_structural_errors():
    rows = sample_rows(3)
    result = m.validate_rows(rows, min_rows=3)
    assert result["errors"] == []
    assert result["stats"]["total"] == 3

    rows2 = [dict(rows[0]), dict(rows[0])]
    rows2[1]["factor_name"] = rows2[0]["factor_name"]
    result2 = m.validate_rows(rows2, min_rows=1)
    assert any("重复" in err for err in result2["errors"])

    rows3 = [{"factor_name": "x", "asset_type": "STK", "factor_type": "NewType", "factor_desc": ""}]
    result3 = m.validate_rows(rows3, min_rows=1)
    assert result3["errors"] == []
    assert any("新分类" in w for w in result3["warnings"])
    assert any("factor_desc" in w for w in result3["warnings"])


def test_save_snapshot_latest_manifest_and_parquet(tmp_path):
    rows = sample_rows(5)
    meta = {
        "api": "factor_list",
        "source": m.API,
        "fetched_at": "2026-11-05 07:00:00",
        "token_sha8": "abcd1234",
        "fields": list(m.FIELDS),
    }
    paths = m.save_snapshot(rows, meta, tmp_path)
    assert paths["snapshot_json"].exists()
    assert paths["snapshot_parquet"].exists()
    assert (tmp_path / "latest.json").exists()
    assert (tmp_path / "latest.parquet").exists()
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["total"] == 5
    assert manifest["token_sha8"] == "abcd1234"
    assert "token" not in {key.lower() for key in manifest}
    loaded, loaded_manifest = m.load_latest(tmp_path)
    assert len(loaded) == 5
    assert loaded_manifest["sha256"] == paths["sha256"]


def test_cli_check_status_show(tmp_path):
    rows = sample_rows(5)
    meta = {"api": "factor_list", "source": m.API, "fetched_at": "2026-11-05 07:00:00", "token_sha8": "abcd1234"}
    m.save_snapshot(rows, meta, tmp_path)
    assert m.main(["check", "--out-dir", str(tmp_path), "--min-rows", "3", "--json"]) == 0
    assert m.main(["status", "--out-dir", str(tmp_path), "--json"]) == 0
    assert m.main(["show", "--out-dir", str(tmp_path), "--limit", "2", "--json"]) == 0


def test_cli_check_flags_low_rows(tmp_path):
    rows = sample_rows(3)
    meta = {"fetched_at": "2026-11-05 07:00:00"}
    m.save_snapshot(rows, meta, tmp_path)
    assert m.main(["check", "--out-dir", str(tmp_path), "--min-rows", "200"]) == 7


def test_cli_check_missing_snapshot(tmp_path):
    assert m.main(["check", "--out-dir", str(tmp_path)]) == 7
    assert m.main(["status", "--out-dir", str(tmp_path)]) == 7


def test_cli_fetch_dry_run(tmp_path, monkeypatch, capsys):
    rows = []
    types = list(m.EXPECTED_TYPES)
    for i in range(272):
        rows.append({
            "factor_name": f"f{i:03d}",
            "asset_type": "STK",
            "factor_type": types[i % len(types)],
            "factor_desc": f"desc {i}",
        })
    payload = make_payload(rows)
    monkeypatch.setattr(m, "load_token", lambda: "tk")
    monkeypatch.setattr(m, "fetch_factor_list", lambda *a, **k: {
        "code": 0, "msg": "", "fields": payload["data"]["fields"],
        "items": payload["data"]["items"], "params": {}, "elapsed_seconds": 0.1,
    })
    rc = m.main(["fetch", "--dry-run", "--json", "--out-dir", str(tmp_path)])
    assert rc == 0
    assert not (tmp_path / "latest.json").exists()
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 272
    assert out["token_sha8"] == m._sha8("tk")


def test_cli_fetch_filtered_does_not_overwrite_latest(tmp_path, monkeypatch):
    rows = [{"factor_name": f"risk_{i:02d}", "asset_type": "STK",
             "factor_type": "Risk", "factor_desc": f"risk {i}"} for i in range(25)]
    payload = make_payload(rows)
    monkeypatch.setattr(m, "load_token", lambda: "tk")
    monkeypatch.setattr(m, "fetch_factor_list", lambda *a, **k: {
        "code": 0, "msg": "", "fields": payload["data"]["fields"],
        "items": payload["data"]["items"], "params": {"factor_type": "Risk"},
        "elapsed_seconds": 0.1,
    })
    rc = m.main(["fetch", "--factor-type", "Risk", "--json", "--out-dir", str(tmp_path)])
    assert rc == 0
    assert not (tmp_path / "latest.json").exists()
    filtered = list((tmp_path / "snapshots").glob("factor_list_*_*.json"))
    assert filtered, "筛选查询应写 snapshots"
    assert any("manifest" in path.name for path in filtered)


def test_cli_fetch_no_token(monkeypatch, capsys):
    monkeypatch.setattr(m, "load_token", lambda: "")
    rc = m.main(["fetch", "--dry-run"])
    assert rc == 6
    assert "token" in capsys.readouterr().out.lower()


def test_main_defaults_to_fetch_dry_run(monkeypatch):
    monkeypatch.setattr(m, "load_token", lambda: "")
    assert m.main(["--dry-run"]) == 6
