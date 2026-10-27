"""调出首日的明细资格回归：保留原缺键，不推定未来或屏蔽实际记录。"""

import hashlib
import json

import pytest
import data_integrity as module


def evidence(root):
    folder = root / "integrity"
    folder.mkdir()
    announcement = "2026年9月30日，联创光电（证券代码：600363）被实施其他风险警示。本所于2026年9月30日起将以上证券调出融资融券标的证券名单。"
    policy = "明细信息中仅包含当前融资融券标的证券的相关数据，汇总信息中包含被调出标的证券范围的证券的余额余量相关数据。"
    sources = []
    for label, text, url in [
        (
            "removal",
            announcement,
            "https://www.sse.com.cn/disclosure/magin/announcement/ssereport/c/c_20260929_10833673.shtml",
        ),
        (
            "detail_scope",
            policy,
            "https://www.sse.com.cn/market/othersdata/margin/detail/",
        ),
    ]:
        raw = ("<html><p>" + text + "</p></html>").encode("utf-8")
        (folder / (label + ".html")).write_bytes(raw)
        sources.append(
            {
                "kind": label,
                "url": url,
                "file": label + ".html",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "excerpt": text,
            }
        )
    record = {
        "ts_code": "600363.SH",
        "effective_date": "20260930",
        "valid_dates": ["20260930"],
        "available_at": "2026-10-08T06:04:50+00:00",
        "sources": sources,
    }
    path = folder / "margin_detail_eligibility.json"
    path.write_text(
        json.dumps({"schema_version": 1, "records": [record]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path, record


def scan(root, days=None):
    keys = days or {"20260929": {"600363.SH", "600519.SH"}, "20260930": {"600519.SH"}}
    return module._margin_coverage(
        root, keys, {d: len(c) for d, c in keys.items()}, sorted(keys), max(keys)
    )


def test_verified_removal_only_excludes_missing_detail_on_effective_day(tmp_path):
    evidence(tmp_path)
    details, value, failed = scan(tmp_path)
    assert value == 0 and not failed
    assert details["20260930"]["raw_missing"] == ["600363.SH"]
    assert details["20260930"]["delisted_excluded"] == ["600363.SH"]
    assert details["_summary"]["raw_set"] == 1
    assert details["_summary"]["delisted_excluded_count"] == 1


def test_unsupported_future_and_other_missing_code_still_fail(tmp_path):
    evidence(tmp_path)
    details, value, failed = scan(
        tmp_path,
        {
            "20260929": {"600363.SH", "600519.SH"},
            "20260930": set(),
            "20261008": {"600519.SH"},
        },
    )
    assert value == 2 and failed
    assert details["20260930"]["missing"] == ["600519.SH"]
    assert details["20261008"]["missing"] == ["600363.SH"]


def test_present_delisted_row_is_preserved(tmp_path):
    evidence(tmp_path)
    details, value, failed = scan(
        tmp_path, {"20260929": {"600363.SH"}, "20260930": {"600363.SH"}}
    )
    assert value == 0 and not failed
    assert details["20260930"]["unique_codes"] == 1
    assert details["20260930"]["delisted_excluded"] == []


@pytest.mark.parametrize(
    "change", ["hash", "host", "scope", "date", "future", "code", "timezone", "path"]
)
def test_unverified_evidence_fails_closed(tmp_path, change):
    path, record = evidence(tmp_path)
    if change == "hash":
        record["sources"][0]["sha256"] = "0" * 64
    elif change == "host":
        record["sources"][0]["url"] = "https://example.test/notice"
    elif change == "scope":
        record["sources"].pop()
    elif change == "date":
        record["effective_date"] = "20260230"
    elif change == "future":
        record["valid_dates"].append("20261008")
    elif change == "code":
        record["ts_code"] = "600519.SH"
    elif change == "timezone":
        record["available_at"] = "2026-10-08T06:04:50"
    elif change == "path":
        record["sources"][0]["file"] = "../removal.html"
    path.write_text(
        json.dumps({"schema_version": 1, "records": [record]}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        scan(tmp_path)
