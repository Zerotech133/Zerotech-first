# -*- coding: utf-8 -*-
"""task-62：数据仓 freshness gate 接入缓存构建的集成测试。

约束：默认 off 完全 no-op；warn 只记录 quality（asof/degraded/stale_sources）不阻断；
不执行真实缓存构建（不读 7.6GB parquet、不写 C:\\Temp 缓存、不触碰 DB）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from zhice_os_v2.infrastructure.data_quality import (
    EXPECTED_STALE_CONSUMED,
    StaleDataError,
    age_gate,
    attach_quality,
    gate_mode_from_env,
    preflight_cache_build,
)

CODE = Path(__file__).resolve().parents[1]
SCRIPTS = CODE / "scripts"

_STALE = {
    "anns_d": ("2026-10-23", "ann_date"),
    "cyq_perf": ("2026-10-21", "trade_date"),
    "index_weight": ("2026-09-30", "trade_date"),
    "limit_list_d": ("2026-10-14", "trade_date"),
    "margin_detail": ("2026-10-22", "trade_date"),
    "sw_l1_member": ("2026-08-27", "in_date"),
    "top_list": ("2026-10-23", "trade_date"),
}


@pytest.fixture()
def fake_inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """hermetic inventory：7 个过期消费源 + 其余 11 个新鲜/期末/静态。"""
    datasets = []
    for ds in age_gate.CONSUMED_DATASETS:
        if ds in _STALE:
            last, primary = _STALE[ds]
            status: dict = {"financial_period": False, "stale": True}
        elif ds in ("hk_fina_indicator", "us_fina_indicator"):
            last, primary = "2026-06-30", "end_date"
            status = {"financial_period": True, "stale": False}
        elif ds in ("stock_basic", "hk_basic", "us_basic"):
            last, primary = None, "list_date"
            status = {"financial_period": False, "no_date": True}
        else:
            last, primary = "2026-10-31", "trade_date"
            status = {"financial_period": False, "stale": False}
        datasets.append({
            "dataset": ds, "primary_date_col": primary, "last_date": last,
            "status": status, "consumers": [{"script": f"scripts/consumer_{ds}.py"}],
        })
    inv = {
        "schema": "zhice.dw_full_inventory/v1",
        "generated_at": "2026-11-02T03:00:00+08:00",
        "reference_date": "2026-11-01",
        "read_only": True,
        "curated": {"datasets": datasets},
    }
    p = tmp_path / "inventory.json"
    p.write_text(json.dumps(inv, ensure_ascii=False), encoding="utf-8")
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(age_gate, "DEFAULT_INVENTORY", p)
    monkeypatch.setattr(age_gate, "RAW_ROOT", raw)
    return p


@pytest.fixture()
def off_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(age_gate.ENV_MODE, raising=False)


def test_default_off_is_complete_noop(off_mode: None) -> None:
    report = preflight_cache_build(producer="dw_multidim_cache")
    assert report.enabled is False
    assert report.mode == "off"
    assert report.verdicts == []
    quality = report.quality_block()
    assert quality["mode"] == "off"
    assert quality["enabled"] is False
    assert quality["checked"] == 0
    # off 模式不得给缓存 payload 增加任何字段（完全 no-op）
    payload = {"schema": "zhice.dw_multidim_cache/v2", "stocks": {}}
    assert attach_quality(payload, report) == {"schema": "zhice.dw_multidim_cache/v2", "stocks": {}}
    assert "quality" not in payload


def test_warn_mode_records_quality_without_blocking(
    fake_inventory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(age_gate.ENV_MODE, "warn")
    report = preflight_cache_build(producer="dw_multidim_cache")
    assert report.enabled is True
    assert report.mode == "warn"
    assert len(report.verdicts) == len(age_gate.CONSUMED_DATASETS)
    assert set(report.stale_datasets) == set(EXPECTED_STALE_CONSUMED)
    # warn 绝不阻断：不抛异常，且全部 action 为 allow
    assert all(v.action == "allow" for v in report.verdicts)

    quality = report.quality_block()
    for key in ("asof", "degraded", "stale_sources"):
        assert key in quality
    assert quality["degraded"] is True
    assert quality["asof"] == "2026-11-01"
    assert len(quality["stale_sources"]) == 7
    item = quality["stale_sources"][0]
    for key in ("dataset", "state", "last_date"):
        assert key in item


def test_attach_quality_writes_only_when_enabled(
    fake_inventory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(age_gate.ENV_MODE, "warn")
    report = preflight_cache_build(producer="dw_extend_cache")
    payload = attach_quality({"schema": "zhice.dw_multidim_cache/v3"}, report)
    assert payload["schema"] == "zhice.dw_multidim_cache/v3"
    assert payload["quality"]["degraded"] is True
    assert payload["quality"]["mode"] == "warn"
    # quality 必须可 JSON 序列化（缓存写入前置条件）
    json.dumps(payload, ensure_ascii=False)


def test_invalid_env_falls_back_to_off(off_mode: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(age_gate.ENV_MODE, "bogus")
    assert gate_mode_from_env() == "off"
    assert preflight_cache_build().enabled is False


def test_deny_mode_contract_is_opt_in(off_mode: None, fake_inventory: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """deny 仅显式设置时生效（本单生产未启用）；当前 fixture 下 STALE 源触发阻断。"""
    monkeypatch.setenv(age_gate.ENV_MODE, "deny")
    with pytest.raises(StaleDataError):
        preflight_cache_build(producer="dw_multidim_cache")


def test_cache_builders_are_wired_to_gate() -> None:
    """集成契约：两个缓存构建脚本必须在 main 里调用 preflight_cache_build + attach_quality。"""
    for name in ("dw_multidim_cache.py", "dw_extend_cache.py"):
        source = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "preflight_cache_build(" in source, f"{name} 未调用 preflight_cache_build"
        assert "attach_quality(" in source, f"{name} 未调用 attach_quality"
        compile(source, name, "exec")  # 语法自检，避免只加文本不合法
