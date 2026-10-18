# -*- coding: utf-8 -*-
"""R6 产线侧 emit 配方单测（tools/event_emit.py）。

红线：全程 tmp 目录，HTTP 层用 _post 注入替身——不起任何真实服务，
不碰 127.0.0.1 端口。运行：
  python -X utf8 -m pytest tools/tests/test_event_emit.py -q
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
for p in (str(TOOLS), str(TOOLS.parent / "portal")):
    if p not in sys.path:
        sys.path.insert(0, p)

import event_emit  # noqa: E402

TS = "2026-10-18T17:10:03+08:00"
TZ = timezone(timedelta(hours=8))


@pytest.fixture(autouse=True)
def _clean(tmp_path):
    event_emit.reset_throttle()
    yield
    event_emit.reset_throttle()


def _emit(outbox_dir, _post, etype="task_fail", run_key="2026-10-18",
          payload=None, **kw):
    """统一入口：source 固定 agent_shadow_daily，缺省信封齐备。"""
    if payload is None:
        payload = {"task": "AgentShadowDaily", "severity": "P1"}
    kw.setdefault("outbox_dir", outbox_dir)
    kw.setdefault("_post", _post)
    kw.setdefault("ts", TS)
    return event_emit.emit_event("agent_shadow_daily", etype, run_key,
                                 payload, **kw)


def _outbox_lines(outbox_dir):
    return [json.loads(ln)
            for f in sorted(Path(outbox_dir).glob("outbox_*.jsonl"))
            for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _ok_post(url, body, timeout):
    return 200, {"ok": True}


def _fail_post(url, body, timeout):
    raise OSError("connection refused")


# ----------------------------------------------------------------------
class TestEmit:
    def test_success_no_outbox(self, tmp_path):
        """hub 200 → ok 回执，零 outbox 残留。"""
        d = tmp_path / "outbox"
        r = _emit(d, _ok_post)
        assert r["ok"] is True and r["event_id"] == event_emit.compute_event_id(
            "agent_shadow_daily", "task_fail", "2026-10-18", TS)
        assert not d.exists() or not list(d.glob("outbox_*.jsonl"))

    def test_failure_stages_outbox(self, tmp_path):
        """hub 不可达 → 本地暂存（fail-visible 不抛异常），信封完整。"""
        d = tmp_path / "outbox"
        r = _emit(d, _fail_post)
        assert r["ok"] is False and r["staged"] is True
        assert Path(r["staged_file"]).exists()
        recs = _outbox_lines(d)
        assert len(recs) == 1
        rec = recs[0]
        assert rec["schema_ver"] == "r6.v1"
        assert rec["caller"] == "task-AgentShadowDaily"  # caller 缺省注入
        assert rec["ts"] == TS
        # 幂等键同式重算（与服务端口径冻结一致）
        import hashlib
        expect = hashlib.sha256(
            "agent_shadow_daily|task_fail|2026-10-18|2026-10-18"
            .encode("utf-8")).hexdigest()[:24]
        assert rec["event_id"] == expect

    def test_http_500_also_stages(self, tmp_path):
        """hub 明确拒（5xx）→ 同样暂存（不重试风暴）。"""
        def _500(url, body, timeout):
            return 500, {"error": "boom"}
        r = _emit(tmp_path / "outbox", _500)
        assert r["ok"] is False and r["status"] == 500 and r["staged"] is True

    def test_invalid_payload_not_staged(self, tmp_path):
        """payload 缺 task/severity → 客户端拒发且不污染 outbox。"""
        r = event_emit.emit_event("agent_shadow_daily", "task_fail", "k",
                                  {"severity": "P1"},
                                  outbox_dir=tmp_path / "outbox",
                                  _post=_ok_post, ts=TS)
        assert r["ok"] is False and "task" in r["error"]
        r2 = event_emit.emit_event("agent_shadow_daily", "task_fail", "k",
                                   {"task": "T", "severity": "P9"},
                                   outbox_dir=tmp_path / "outbox",
                                   _post=_ok_post, ts=TS)
        assert r2["ok"] is False and "severity" in r2["error"]
        assert not (tmp_path / "outbox").exists()


class TestThrottle:
    def test_60s_window_same_source_type(self, tmp_path):
        """60s 窗口同 source+type 本地节流：第二次不发不暂存。"""
        calls = []
        def _count(url, body, timeout):
            calls.append(body)
            return 200, {"ok": True}
        d = tmp_path / "outbox"
        r1 = _emit(d, _count)
        r2 = _emit(d, _count, run_key="second")
        assert r1["ok"] is True
        assert r2["ok"] is False and r2["throttled"] is True
        assert r2["retry_after"] > 0
        assert len(calls) == 1  # 第二次未出网

    def test_different_type_not_throttled(self, tmp_path):
        calls = []
        def _count(url, body, timeout):
            calls.append(body)
            return 200, {"ok": True}
        d = tmp_path / "outbox"
        assert _emit(d, _count)["ok"] is True
        assert _emit(d, _count, etype="task_run", run_key="r2")["ok"] is True
        assert len(calls) == 2


class TestFlush:
    def test_flush_reposts_and_clears(self, tmp_path):
        """补投：暂存 2 条 → hub 恢复 flush 全部成功 → outbox 清空。"""
        d = tmp_path / "outbox"
        _emit(d, _fail_post)
        event_emit.reset_throttle()
        _emit(d, _fail_post, etype="task_run", run_key="k2")
        assert len(_outbox_lines(d)) == 2
        r = event_emit.flush_outbox(outbox_dir=d, _post=_ok_post)
        assert r["ok"] is True and r["flushed"] == 2 and r["kept"] == 0
        assert _outbox_lines(d) == []

    def test_flush_keeps_failures(self, tmp_path):
        """补投部分失败 → 失败行保留（append-only 可再补投）。"""
        d = tmp_path / "outbox"
        _emit(d, _fail_post)
        event_emit.reset_throttle()
        _emit(d, _fail_post, etype="task_run", run_key="k2")
        seq = [(200, {"ok": True}), (500, {"error": "still down"})]
        def _mixed(url, body, timeout):
            return seq.pop(0)
        r = event_emit.flush_outbox(outbox_dir=d, _post=_mixed)
        assert r["flushed"] == 1 and r["kept"] == 1
        assert len(_outbox_lines(d)) == 1


class TestFileCap:
    def test_500_cap_rotates(self, tmp_path):
        """单文件满 500 条 → 轮转新文件（§7 风险 5 上限裁剪）。"""
        d = tmp_path / "outbox"
        d.mkdir()
        today = datetime.now(TZ).strftime("%Y%m%d")
        cur = d / ("outbox_%s.jsonl" % today)
        filler = json.dumps({"filler": True})
        cur.write_text("\n".join([filler] * 500) + "\n", encoding="utf-8")
        r = _emit(d, _fail_post)
        assert r["ok"] is False and r["staged"] is True
        assert Path(r["staged_file"]) != cur  # 轮转到新文件
        recs = [json.loads(ln) for ln in
                Path(r["staged_file"]).read_text(encoding="utf-8").splitlines()
                if ln.strip()]
        assert len(recs) == 1 and recs[0]["event_id"]
        assert len(cur.read_text(encoding="utf-8").splitlines()) == 500  # 原文件不动


# ----------------------------------------------------------------------
def test_pure_stdlib_and_never_raises():
    """红线自证：纯 stdlib（无三方 import）+ 底线不抛异常。"""
    src = Path(event_emit.__file__).read_text(encoding="utf-8")
    for bad in ("import requests", "import httpx", "import portal_api"):
        assert bad not in src
    # 极端输入：永不向产线主流程抛异常
    r = event_emit.emit_event(None, None, None, None,
                              outbox_dir=None, _post=None)
    assert r["ok"] is False and "error" in r
