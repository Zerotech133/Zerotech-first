# -*- coding: utf-8 -*-
"""lifecycle 四态（archived）单元测试（2026-10-30 P72）。

覆盖：
- archived 是有效状态，且与 planned/deprecated 一样跳过资格/新鲜度；
- 未知状态归一 active（fail-open）；
- reason_for 返回登记原因。
"""
import sys
from pathlib import Path

DW = Path(__file__).resolve().parent.parent
if str(DW) not in sys.path:
    sys.path.insert(0, str(DW))

import lifecycle  # noqa: E402

YAML = """version: 1
updated: 2026-10-30
tables:
  archived_one:
    lifecycle: archived
    reason: 90 天到期转归档（迁移演练）
  deprecated_one:
    lifecycle: deprecated
    reason: 已废弃
  weird_one:
    lifecycle: bogus
    reason: 非法值应归一 active
"""


def _with_yaml(tmp_path, monkeypatch):
    p = tmp_path / "data_lifecycle.yaml"
    p.write_text(YAML, encoding="utf-8")
    monkeypatch.setattr(lifecycle, "OVERRIDES", Path(p))
    lifecycle._load.cache_clear()
    return p


def test_archived_is_valid_and_skipped(tmp_path, monkeypatch):
    _with_yaml(tmp_path, monkeypatch)
    assert lifecycle.status_for("archived_one") == "archived"
    assert lifecycle.status_for("deprecated_one") == "deprecated"
    assert lifecycle.skipped("archived_one") is True
    assert lifecycle.skipped("deprecated_one") is True
    assert lifecycle.skipped("active_unknown") is False
    assert lifecycle.reason_for("archived_one").startswith("90 天")


def test_unknown_status_normalized_to_active(tmp_path, monkeypatch):
    _with_yaml(tmp_path, monkeypatch)
    assert lifecycle.status_for("weird_one") == "active"
    assert lifecycle.skipped("weird_one") is False


def test_all_overrides_roundtrip(tmp_path, monkeypatch):
    _with_yaml(tmp_path, monkeypatch)
    data = lifecycle.all_overrides()
    assert set(data) == {"archived_one", "deprecated_one", "weird_one"}
