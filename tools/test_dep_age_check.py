# -*- coding: utf-8 -*-
"""O8 P3b 依赖年龄检查单测（不联网）。"""
import importlib.util
import pathlib
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("dep_age_check", HERE / "dep_age_check.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def test_parse_pip_freeze():
    text = "# comment\naccelerate==1.13.0\nfoo==2.0 ; python_version>='3.8'\nbar==1.0\nfoo==2.0\n"
    assert m.parse_pip_freeze(text) == [("accelerate", "1.13.0"), ("bar", "1.0"), ("foo", "2.0")]


def test_parse_uv_lock():
    text = """
[[package]]
name = "demo"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "selfproj"
version = "0.1.0"
source = { virtual = "." }
"""
    assert m.parse_uv_lock_text(text) == [("demo", "1.0.0")]


def test_check_fresh_old_unknown_error():
    times = {
        ("freshpkg", "1.0"): datetime(2026, 10, 27, tzinfo=timezone.utc),
        ("oldpkg", "1.0"): datetime(2026, 1, 1, tzinfo=timezone.utc),
        ("unknownpkg", "1.0"): None,
    }

    def fake(name, version, timeout=10.0):
        if name == "errorpkg":
            raise RuntimeError("boom")
        return times[(name, version)]

    res = m.check([("freshpkg", "1.0"), ("oldpkg", "1.0"),
                   ("unknownpkg", "1.0"), ("errorpkg", "1.0")],
                  fetcher=fake, now=NOW, min_age_days=7)
    assert res["fresh_count"] == 1
    assert res["fresh"][0]["name"] == "freshpkg"
    assert res["error_count"] == 2
    assert res["ok"] is False
    assert res["warn_only"] is True


def test_main_warn_only_and_strict(tmp_path, monkeypatch):
    lock = tmp_path / "x.lock"
    lock.write_text("freshpkg==1.0\noldpkg==1.0\n", encoding="utf-8")

    def fake(name, version, timeout=10.0):
        if name == "freshpkg":
            return datetime(2026, 10, 27, tzinfo=timezone.utc)
        return datetime(2026, 1, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(m, "fetch_pypi_release_time", fake)
    assert m.main(["--path", str(lock), "--json"]) == 0
    out = m.main(["--path", str(lock), "--strict"])
    assert out == 1


def test_main_missing_files(tmp_path):
    assert m.main(["--path", str(tmp_path / "none.lock")]) == 2
