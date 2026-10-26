# -*- coding: utf-8 -*-
"""osleg_http_probe 纯函数测试（不启端口、不联网、不动真实 store）。

对应工单：K-QUANT-20261026-OSLEG-8795PREP。
探针的 --run 模式属批准窗口动作，单测只覆盖纯函数与 fail-closed 前置检查。
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import osleg_http_probe as P  # noqa: E402


def test_endpoint_translation_covers_all_channel_stages():
    """通道②五阶段与现役 gateway 端点的对照表必须齐全且不重复。"""
    rows = P.ENDPOINT_TRANSLATION
    assert len(rows) == 5
    w4_paths = [row[1] for row in rows]
    assert len(set(w4_paths)) == len(w4_paths)
    for stage in ("submit", "consume", "answer", "deliver", "receipt"):
        assert any(stage in path for path in w4_paths), stage
    # 现役 gateway 只实现 submit/receipt，其余阶段必须显式标注未实现（不得假装接通）
    implemented = [row for row in rows if not str(row[3]).startswith("/(未实现")]
    assert {row[1] for row in implemented} == {"/osleg/v1/submit", "/osleg/v1/receipt"}


def test_sha_listing_empty_and_change_detection(tmp_path):
    before = P._sha_listing(tmp_path)
    assert before["count"] == 0 and before["bytes"] == 0 and before["digest"]
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    after = P._sha_listing(tmp_path)
    assert after["count"] == 1 and after["bytes"] == 2
    assert before["digest"] != after["digest"]


def test_real_store_snapshot_shape():
    snap = P.real_store_snapshot()
    assert set(snap) >= {"exists", "count", "bytes", "digest"}
    assert isinstance(snap["count"], int) and snap["count"] >= 0


def test_port_in_use_detects_listener():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.listen(1)
    try:
        assert P.port_in_use("127.0.0.1", port) is True
    finally:
        sock.close()


def test_dry_run_does_not_bind_8795():
    out = P.dry_run()
    assert out["mode"] == "dry-run"
    assert isinstance(out["checks"], list) and out["checks"]
    names = {c["name"] for c in out["checks"]}
    assert {"endpoint_translation_nonempty", "platform_dir_exists",
            "work_dir_writable", "port_8795_free"} <= names
    # dry-run 只做自检，不监听端口
    assert P.port_in_use("127.0.0.1", 8795) is False


def test_run_rejects_occupied_8795(monkeypatch):
    """8795 被占用时 --run 必须 fail-closed 拒绝启动（不抢占端口）。"""
    monkeypatch.setattr(P, "port_in_use", lambda host, port, timeout=0.5: True)
    try:
        P.run_probe()
    except SystemExit as exc:
        assert "8795" in str(exc) or "占用" in str(exc)
    else:  # pragma: no cover - 明确失败而不是静默通过
        raise AssertionError("端口被占用时 run_probe 必须拒绝")
