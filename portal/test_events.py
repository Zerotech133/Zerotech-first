# -*- coding: utf-8 -*-
"""R6 事件总线 api_events 单测（仿 test_api_brief.py 判例）。

红线：全程 tmp 目录注入（manifest_path/events_dir），不读真实
portal/data/events/，不起服务不碰端口，纯函数级调用（Handler 薄壳不测网络）。
运行：python -X utf8 -m pytest portal/test_events.py -q
"""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import api_events  # noqa: E402

TS = "2026-10-18T17:10:03+08:00"


def _manifest(tmp_path, sources=None):
    """tmp manifest（缺省单 source：agent_shadow_daily 四事件，§3.2 示例口径）。"""
    if sources is None:
        sources = [
            {
                "source": "agent_shadow_daily",
                "match_task": "AgentShadowDaily",
                "event_types": [
                    "task_run",
                    "task_fail",
                    "task_recovered",
                    "brief_ready",
                ],
                "enabled": True,
            }
        ]
    p = tmp_path / "event_manifest.json"
    p.write_text(
        json.dumps(
            {"version": 1, "updated": "2026-10-18", "sources": sources},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return p


def _body(**kw):
    """信封（§2.1 示例同构，event_id 客户端同式重算）。"""
    b = {
        "schema_ver": "r6.v1",
        "type": "task_fail",
        "source": "agent_shadow_daily",
        "ts": TS,
        "caller": "task-agent_shadow_daily",
        "payload": {
            "task": "AgentShadowDaily",
            "run_key": "2026-10-18",
            "severity": "P1",
            "summary": "影子链任务失败",
        },
    }
    b.update(kw)
    b.setdefault(
        "event_id",
        api_events.compute_event_id(
            b["source"], b["type"], b["payload"].get("run_key", ""), b["ts"]
        ),
    )
    return b


@pytest.fixture(autouse=True)
def _clean_state():
    api_events.reset_state()
    yield
    api_events.reset_state()


def _ingest_all(tmp_path, bodies, **kw):
    return [
        api_events.ingest_event(
            b, manifest_path=_manifest(tmp_path, **kw), events_dir=tmp_path / "events"
        )
        for b in bodies
    ]


def _lines(d, name):
    p = d / name
    if not p.exists():
        return []
    return [
        json.loads(ln)
        for ln in p.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


# ----------------------------------------------------------------------
# 双白名单（§2.3：403 + caller 归因落账 + 报因含合法集摘要）
# ----------------------------------------------------------------------
class TestWhitelist:
    def test_unknown_source_403(self, tmp_path):
        code, r = api_events.ingest_event(
            _body(source="rogue_line", event_id=None),
            manifest_path=_manifest(tmp_path),
            events_dir=tmp_path / "events",
        )
        assert code == 403 and r["ok"] is False
        assert r["caller"] == "task-agent_shadow_daily"
        assert "agent_shadow_daily" in r["allowed_sources"]  # 合法集摘要
        # caller 归因落账（rejected.jsonl）
        rej = _lines(tmp_path / "events", "rejected.jsonl")
        assert len(rej) == 1 and rej[0]["caller"] == "task-agent_shadow_daily"
        assert "未注册" in rej[0]["reason"]

    def test_cross_type_403(self, tmp_path):
        """source 合法但 type 越权（不在该 source 的 event_types）→ 403。"""
        code, r = api_events.ingest_event(
            _body(type="alert_close", event_id=None),
            manifest_path=_manifest(tmp_path),
            events_dir=tmp_path / "events",
        )
        assert code == 403 and r["source"] == "agent_shadow_daily"
        assert set(r["allowed_types"]) == {
            "task_run",
            "task_fail",
            "task_recovered",
            "brief_ready",
        }
        assert _lines(tmp_path / "events", "rejected.jsonl")

    def test_disabled_source_403(self, tmp_path):
        code, r = api_events.ingest_event(
            _body(event_id=None),
            manifest_path=_manifest(
                tmp_path,
                sources=[
                    {
                        "source": "agent_shadow_daily",
                        "event_types": ["task_fail"],
                        "enabled": False,
                    }
                ],
            ),
            events_dir=tmp_path / "events",
        )
        assert code == 403


# ----------------------------------------------------------------------
# 信封校验（§2.1，缺字段/非法值 → 400）
# ----------------------------------------------------------------------
class TestEnvelope:
    def test_non_dict_body_400(self, tmp_path):
        code, r = api_events.ingest_event(
            ["nope"], manifest_path=_manifest(tmp_path), events_dir=tmp_path / "events"
        )
        assert code == 400

    def test_missing_ts_400(self, tmp_path):
        b = _body(event_id=None)
        b.pop("ts")
        code, r = api_events.ingest_event(
            b, manifest_path=_manifest(tmp_path), events_dir=tmp_path / "events"
        )
        assert code == 400 and any("ts" in e for e in r["details"])

    def test_bad_ts_offset_400(self, tmp_path):
        """ts 非 +08:00 时区 → 拒（§2.1 硬约束）。"""
        code, _ = api_events.ingest_event(
            _body(ts="2026-10-18T09:10:03+00:00", event_id=None),
            manifest_path=_manifest(tmp_path),
            events_dir=tmp_path / "events",
        )
        assert code == 400

    def test_bad_severity_400(self, tmp_path):
        b = _body(event_id=None)
        b["payload"]["severity"] = "P3"
        code, r = api_events.ingest_event(
            b, manifest_path=_manifest(tmp_path), events_dir=tmp_path / "events"
        )
        assert code == 400 and any("severity" in e for e in r["details"])

    def test_missing_payload_task_400(self, tmp_path):
        b = _body(event_id=None)
        b["payload"].pop("task")
        code, _ = api_events.ingest_event(
            b, manifest_path=_manifest(tmp_path), events_dir=tmp_path / "events"
        )
        assert code == 400

    def test_bad_schema_ver_400(self, tmp_path):
        code, _ = api_events.ingest_event(
            _body(schema_ver="v9", event_id=None),
            manifest_path=_manifest(tmp_path),
            events_dir=tmp_path / "events",
        )
        assert code == 400

    def test_bad_caller_400(self, tmp_path):
        """caller 不属 task-*/hermes-*/ops-* → 拒（K-ORG-109 归因口径）。"""
        code, _ = api_events.ingest_event(
            _body(caller="someone", event_id=None),
            manifest_path=_manifest(tmp_path),
            events_dir=tmp_path / "events",
        )
        assert code == 400


# ----------------------------------------------------------------------
# 幂等键 + 24h 去重（§7 风险 2 最后防线）
# ----------------------------------------------------------------------
class TestIdempotency:
    def test_ingest_ok_appends(self, tmp_path):
        code, r = api_events.ingest_event(
            _body(), manifest_path=_manifest(tmp_path), events_dir=tmp_path / "events"
        )
        assert code == 200 and r["ok"] is True and r["dedup"] is False
        ledger = _lines(tmp_path / "events", "events_2026-10.jsonl")
        assert len(ledger) == 1
        rec = ledger[0]
        assert set(rec) == {
            "event_id",
            "type",
            "source",
            "ts",
            "caller",
            "payload",
            "schema_ver",
        }
        assert rec["event_id"] == api_events.compute_event_id(
            "agent_shadow_daily", "task_fail", "2026-10-18", TS
        )

    def test_duplicate_200_dedup_no_append(self, tmp_path):
        for _ in range(2):
            code, r = api_events.ingest_event(
                _body(),
                manifest_path=_manifest(tmp_path),
                events_dir=tmp_path / "events",
            )
        assert code == 200 and r["dedup"] is True
        assert len(_lines(tmp_path / "events", "events_2026-10.jsonl")) == 1

    def test_event_id_mismatch_422(self, tmp_path):
        code, r = api_events.ingest_event(
            _body(event_id="f" * 24),
            manifest_path=_manifest(tmp_path),
            events_dir=tmp_path / "events",
        )
        assert code == 422 and r["expected_event_id"] == api_events.compute_event_id(
            "agent_shadow_daily", "task_fail", "2026-10-18", TS
        )
        assert _lines(tmp_path / "events", "rejected.jsonl")

    def test_new_run_key_is_new_event(self, tmp_path):
        """重跑产生新 run_key → 合法新事件（§7 风险 2 语义）。"""
        _ingest_all(
            tmp_path,
            [
                _body(),
                _body(
                    payload={
                        "task": "AgentShadowDaily",
                        "run_key": "2026-10-18-r2",
                        "severity": "P1",
                    }
                ),
            ],
        )
        assert len(_lines(tmp_path / "events", "events_2026-10.jsonl")) == 2

    def test_outbox_replay_dedup_after_restart(self, tmp_path):
        """outbox 兼容路径：hub 重启后补投同事件 → 落盘索引去重，不双记。"""
        d = tmp_path / "events"
        api_events.ingest_event(
            _body(), manifest_path=_manifest(tmp_path), events_dir=d
        )
        api_events.reset_state(d)  # 模拟 hub 进程重启（内存态清零）
        code, r = api_events.ingest_event(
            _body(), manifest_path=_manifest(tmp_path), events_dir=d
        )
        assert code == 200 and r["dedup"] is True
        assert len(_lines(d, "events_2026-10.jsonl")) == 1


# ----------------------------------------------------------------------
# 风暴折叠（§7 风险 1：同 source+type 每分钟 N=30）
# ----------------------------------------------------------------------
class TestFold:
    def _storm(self, tmp_path, n=33, sev="P1"):
        bodies = []
        for i in range(n):
            b = _body(
                event_id=None,
                payload={
                    "task": "AgentShadowDaily",
                    "run_key": "storm-%02d" % i,
                    "severity": sev,
                },
            )
            bodies.append(b)
        return _ingest_all(tmp_path, bodies)

    def test_fold_triggered_at_31st(self, tmp_path):
        rs = self._storm(tmp_path, 33)
        assert all(code == 200 for code, _ in rs[:30])
        assert rs[29][1]["folded"] is False  # 第 30 条仍正常
        assert rs[30][1]["folded"] is True  # 第 31 条起折叠
        ledger = _lines(tmp_path / "events", "events_2026-10.jsonl")
        # 30 条普通 + 1 条 summary（不丢量只丢条）
        assert len(ledger) == 31
        summaries = [r for r in ledger if r["payload"].get("folded")]
        assert len(summaries) == 1
        assert summaries[0]["payload"]["count"] == 3  # 33-30 条被折叠计数
        assert summaries[0]["caller"] == "hermes-eventbus"

    def test_fold_severity_escalates(self, tmp_path):
        """折叠窗口内混入 P0 → summary severity 取最严重（分发不降级）。"""
        bodies = [
            _body(
                event_id=None,
                payload={
                    "task": "AgentShadowDaily",
                    "run_key": "s-%02d" % i,
                    "severity": "P1",
                },
            )
            for i in range(30)
        ]
        bodies.append(
            _body(
                event_id=None,
                payload={
                    "task": "AgentShadowDaily",
                    "run_key": "s-30",
                    "severity": "P0",
                },
            )
        )
        _ingest_all(tmp_path, bodies)
        ledger = _lines(tmp_path / "events", "events_2026-10.jsonl")
        summary = [r for r in ledger if r["payload"].get("folded")][0]
        assert summary["payload"]["severity"] == "P0"

    def test_folded_original_dedup_on_retry(self, tmp_path):
        """被折叠的原事件（storm-30）重试 → 200 dedup=true（幂等键仍生效）。"""
        self._storm(tmp_path, 31)
        b = _body(
            event_id=None,
            payload={
                "task": "AgentShadowDaily",
                "run_key": "storm-30",
                "severity": "P1",
            },
        )
        code, r = api_events.ingest_event(
            b, manifest_path=_manifest(tmp_path), events_dir=tmp_path / "events"
        )
        assert code == 200 and r["dedup"] is True


# ----------------------------------------------------------------------
# query（只读投影）
# ----------------------------------------------------------------------
class TestQuery:
    def test_query_filters(self, tmp_path):
        _ingest_all(
            tmp_path,
            [
                _body(
                    event_id=None,
                    type="task_fail",
                    ts="2026-10-18T17:10:03+08:00",
                    payload={
                        "task": "AgentShadowDaily",
                        "run_key": "a",
                        "severity": "P1",
                    },
                ),
                _body(
                    event_id=None,
                    type="task_run",
                    ts="2026-10-18T18:10:03+08:00",
                    payload={
                        "task": "AgentShadowDaily",
                        "run_key": "b",
                        "severity": "P2",
                    },
                ),
                _body(
                    event_id=None,
                    type="task_run",
                    ts="2026-10-17T18:10:03+08:00",
                    payload={
                        "task": "AgentShadowDaily",
                        "run_key": "c",
                        "severity": "P2",
                    },
                ),
            ],
        )
        d = tmp_path / "events"
        r = api_events.query_events(events_dir=d)
        assert r["ok"] is True and r["count"] == 3
        # 同月文件行倒序（append 顺序 a,b,c → 读序 c,b,a）
        assert [i["payload"]["run_key"] for i in r["items"]] == ["c", "b", "a"]
        r2 = api_events.query_events(event_type="task_run", events_dir=d)
        assert r2["count"] == 2
        r3 = api_events.query_events(since="2026-10-18T00:00:00+08:00", events_dir=d)
        assert {i["payload"]["run_key"] for i in r3["items"]} == {"a", "b"}
        r4 = api_events.query_events(
            since="2026-10-18T00:00:00+08:00",
            until="2026-10-18T17:10:03+08:00",
            events_dir=d,
        )
        assert r4["count"] == 1 and r4["items"][0]["payload"]["run_key"] == "a"

    def test_query_empty_dir_fail_visible(self, tmp_path):
        r = api_events.query_events(events_dir=tmp_path / "nope")
        assert r["ok"] is True and r["count"] == 0


# ----------------------------------------------------------------------
# manifest 故障 fail-visible
# ----------------------------------------------------------------------
class TestManifestFail:
    def test_missing_manifest_500(self, tmp_path):
        code, r = api_events.ingest_event(
            _body(), manifest_path=tmp_path / "no.json", events_dir=tmp_path / "events"
        )
        assert code == 500 and r["ok"] is False


# ----------------------------------------------------------------------
# 纯函数红线：模块面无调度/无端口
# ----------------------------------------------------------------------
def test_no_schedule_no_port():
    """红线自证：api_events 不得出现 schtasks/端口监听/子进程（只落账不派单）。"""
    src = Path(api_events.__file__).read_text(encoding="utf-8")
    assert "schtasks" not in src
    assert "subprocess" not in src
    assert "ThreadingHTTPServer" not in src
