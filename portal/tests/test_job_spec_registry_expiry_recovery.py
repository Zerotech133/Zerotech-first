# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import job_spec_registry as registry_module
from job_spec_registry import JobSpecDescriptor, JobSpecRegistry


class _Clock(datetime):
    current = datetime(2030, 1, 1, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz or timezone.utc)


@pytest.fixture
def two_source_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(registry_module, "datetime", _Clock)
    _Clock.current = datetime(2030, 1, 1, tzinfo=timezone.utc)
    path = tmp_path / "catalog.json"
    registry = JobSpecRegistry(str(path))
    common = dict(
        source_version="v1",
        source_kind="bar_store",
        market="CN",
        timezone="Asia/Shanghai",
        timeframe="1m",
        store_config_ref="runtime:store",
        intent_batch_ref="runtime:intents",
        provider_policy_ref="runtime:policy",
        registered_at=_Clock.current.isoformat(),
    )
    registry.register(
        JobSpecDescriptor(
            source_ref="expired",
            expires_at=(_Clock.current + timedelta(days=1)).isoformat(),
            **common,
        )
    )
    registry.register(
        JobSpecDescriptor(
            source_ref="active",
            expires_at=(_Clock.current + timedelta(days=30)).isoformat(),
            **common,
        )
    )
    _Clock.current += timedelta(days=2)
    return path


def test_natural_expiry_does_not_invalidate_other_source(two_source_catalog):
    path = two_source_catalog
    saved = path.read_bytes()
    registry = JobSpecRegistry(str(path))

    assert registry.catalog_status == "ok"
    assert registry.catalog_error is None
    assert set(registry.descriptors()) == {"expired", "active"}
    assert registry.is_expired("expired")
    assert not registry.is_expired("active")
    assert registry.get("active").source_ref == "active"
    assert path.read_bytes() == saved

    with pytest.raises(ValueError, match="expires_at is in the past"):
        registry.register(JobSpecDescriptor(**registry.get("expired").as_dict()))


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda raw: raw.update(store_config_ref="file:///etc/passwd"),
        lambda raw: raw.update(expires_at="2030-01-02T00:00:00"),
        lambda raw: raw.update(source_ref="wrong-key"),
    ],
)
def test_malformed_expired_descriptor_still_invalidates_catalog(
    two_source_catalog, corrupt
):
    path = two_source_catalog
    raw = json.loads(path.read_text(encoding="utf-8"))
    corrupt(raw["expired"])
    path.write_text(json.dumps(raw), encoding="utf-8")
    registry = JobSpecRegistry(str(path))

    assert registry.catalog_status == "invalid"
    assert registry.catalog_error
    assert registry.descriptors() == {}
    assert registry.get("active") is None
