# -*- coding: utf-8 -*-
"""P2：视图 registry 化配置 + 环境覆盖 + 结构化失败日志。"""
import json
import sys
from pathlib import Path


_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from legion_command import zhaoyun_view as zv  # noqa: E402


def test_registry_display_name():
    assert zv._registry_display_name() == "赵云·治理"


def test_env_overrides(tmp_path, monkeypatch):
    prof = tmp_path / "prof"
    (prof / "skills").mkdir(parents=True)
    (prof / "skills" / "CAPABILITIES.json").write_text(
        json.dumps({"capabilities": [{"name": "x", "status": "ready"}]}, ensure_ascii=False),
        encoding="utf-8")
    fail_log = tmp_path / "fails.jsonl"
    keep = tmp_path / "keep.log"
    keep.write_text("line1\nline2\n", encoding="utf-8")
    monkeypatch.setenv("ZHAOYUN_PROFILE_DIR", str(prof))
    monkeypatch.setenv("ZHAOYUN_SIGN_REQUESTS_ROOT", str(tmp_path / "sign"))
    monkeypatch.setenv("ZHAOYUN_VIEW_FAIL_LOG", str(fail_log))
    monkeypatch.setenv("ZHAOYUN_KEEPALIVE_LOG", str(keep))

    assert zv._profile_dir() == prof
    assert zv._sign_root() == tmp_path / "sign"
    assert zv._keepalive_path() == keep
    assert zv._keepalive()["last"] == "line2"

    view = zv.build_view(platform=tmp_path / "platform", now=1761800000.0)
    assert view["identity"]["display_name"] == "赵云·治理"
    assert view["capabilities"]["total"] >= 1
    assert view["failure_log"] == str(fail_log)


def test_failure_log_jsonl(tmp_path, monkeypatch):
    log = tmp_path / "f.jsonl"
    monkeypatch.setenv("ZHAOYUN_VIEW_FAIL_LOG", str(log))
    zv._log_fail("source_x", ValueError("boom"))
    rec = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert rec["source"] == "source_x" and "ValueError" in rec["error"]
