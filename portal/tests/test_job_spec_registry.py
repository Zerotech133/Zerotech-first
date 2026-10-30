# -*- coding: utf-8 -*-
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from job_spec_registry import JobSpecDescriptor, JobSpecRegistry


def _descriptor():
    return JobSpecDescriptor(
        source_ref="fixture",
        source_version="v1",
        source_kind="bar_store",
        market="CN",
        timezone="Asia/Shanghai",
        timeframe="1m",
        store_config_ref="runtime:fixture",
        intent_batch_ref="runtime:fixture-intents",
        provider_policy_ref="runtime:fixture-providers",
        registered_at=datetime.now(timezone.utc).isoformat(),
    )


def test_descriptor_persists_without_runtime_objects(tmp_path):
    registry = JobSpecRegistry(str(tmp_path / "runtime" / "catalog.json"))
    registry.register(_descriptor())
    loaded = JobSpecRegistry(str(tmp_path / "runtime" / "catalog.json"))
    descriptor = loaded.get("fixture")
    assert descriptor is not None
    raw = json.loads(
        (tmp_path / "runtime" / "catalog.json").read_text(encoding="utf-8")
    )
    assert "callable" not in json.dumps(raw).lower()
    assert "order_intents" not in json.dumps(raw).lower()
    assert "status_provider" not in json.dumps(raw).lower()


def test_rehydrate_failure_marks_source_unavailable(tmp_path):
    registry = JobSpecRegistry(str(tmp_path / "catalog.json"))
    registry.register(_descriptor())
    registry.mark_unavailable("fixture", "runtime factory unavailable")
    assert registry.get("fixture").availability == "source_unavailable"
    assert registry.get("fixture").error == "SOURCE_UNAVAILABLE"


def test_corrupt_catalog_is_invalid_not_empty(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text("{not-json", encoding="utf-8")
    registry = JobSpecRegistry(str(path))
    assert registry.catalog_status == "invalid"
    assert registry.catalog_error
    assert registry.descriptors() == {}


def test_expired_descriptor_is_rejected(tmp_path):
    registry = JobSpecRegistry(str(tmp_path / "catalog.json"))
    descriptor = JobSpecDescriptor(
        **{**_descriptor().as_dict(), "expires_at": "2020-01-01T00:00:00+00:00"}
    )
    try:
        registry.register(descriptor)
    except ValueError as exc:
        assert "expires_at" in str(exc)
    else:
        raise AssertionError("expired descriptor must be rejected")
    assert not (tmp_path / "catalog.json").exists()


@pytest.mark.parametrize(
    "value",
    [
        "/tmp/private",  # nosec B108 - 测试夹具/docker参数/字符串常量（已核实）
        "C:\\\\secret\\data",
        "file:///etc/passwd",
        "postgresql://user:pass@host/db",
        "callable:factory",
    ],
)
def test_non_opaque_refs_are_rejected(tmp_path, value):
    registry = JobSpecRegistry(str(tmp_path / "catalog.json"))
    descriptor = JobSpecDescriptor(
        **{**_descriptor().as_dict(), "store_config_ref": value}
    )
    with pytest.raises(ValueError, match="REF_NOT_OPAQUE"):
        registry.register(descriptor)
    assert not (tmp_path / "catalog.json").exists()


def test_calendar_version_must_be_sha256(tmp_path):
    """registry 与 CalendarIdentity 契约统一：version 只接受 64 位小写 SHA-256。"""
    registry = JobSpecRegistry(str(tmp_path / "catalog.json"))
    for bad in ("v1", "V1", "1.0.0", "a" * 63, "a" * 65, "A" * 64):
        descriptor = JobSpecDescriptor(
            **{
                **_descriptor().as_dict(),
                "calendar_ref": "registry:calendar-cn-sse",
                "calendar_version": bad,
            }
        )
        with pytest.raises(ValueError, match="calendar_version"):
            registry.register(descriptor)
    good = JobSpecDescriptor(
        **{
            **_descriptor().as_dict(),
            "calendar_ref": "registry:calendar-cn-sse",
            "calendar_version": "a" * 64,
        }
    )
    registry.register(good)
    assert registry.get("fixture").calendar_version == "a" * 64
