"""GOVINTEG（P1 消费接线 + P3 输入契约）测试。

覆盖：
- experience_store.public_summary 只读统计/最近条目/风控域只读标记；
- gov_ai.assert_payload：三类动作的合法输入通过、空/结构不符拒绝；
- gov_ai.run 在空输入时**不调用模型**（client 零调用）且抛 GovAiError；
- serve._evolution 的经验库只读字段（直接构造 index 文件后调静态方法）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
for _p in (str(_PLATFORM), str(_PLATFORM / "tools"), str(_PLATFORM / "legion_command")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gov_ai as G  # noqa: E402
from engine import experience_store as S  # noqa: E402


# ---------------------------------------------------------------- P1：经验库只读


def _write_index(root: Path, rows: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "_index.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_public_summary_counts_and_recent(tmp_path):
    root = tmp_path / "lib"
    _write_index(
        root,
        [
            {
                "entry_id": "EXP-SUPERVISION-001",
                "domain": "supervision",
                "cue": "老条目",
                "confidence": 0.9,
                "signed_by": "老田",
                "written_at": "2026-10-26T12:00:00+0800",
                "verified": True,
            },
            {
                "entry_id": "EXP-SUPERVISION-002",
                "domain": "supervision",
                "cue": "新条目",
                "confidence": 0.8,
                "signed_by": "老田",
                "written_at": "2026-10-26T13:00:00+0800",
                "verified": True,
            },
            {
                "entry_id": "EXP-RISKGUARD-001",
                "domain": "riskguard",
                "cue": "风控只读",
                "confidence": 0.7,
                "signed_by": "老田",
                "written_at": "2026-10-25T10:00:00+0800",
                "verified": False,
            },
        ],
    )
    s = S.public_summary(root=root)
    assert s["entries_total"] == 3 and s["verified_total"] == 2
    assert s["domains"]["supervision"] == {
        "entries": 2,
        "verified": 2,
        "readonly": False,
    }
    assert s["domains"]["riskguard"]["readonly"] is True
    assert s["latest_signed_at"] == "2026-10-26T13:00:00+0800"
    assert s["recent"][0]["entry_id"] == "EXP-SUPERVISION-002"
    # 不暴露 lesson 正文
    assert "lesson" not in s["recent"][0]


def test_public_summary_empty(tmp_path):
    s = S.public_summary(root=tmp_path / "none")
    assert s == {
        "entries_total": 0,
        "verified_total": 0,
        "domains": s["domains"],
        "recent": [],
        "latest_signed_at": None,
    }


# ---------------------------------------------------------------- P3：输入契约


def _anomaly_payload(**over):
    p = {
        "as_of": "2026-10-26T09:56:00+08:00",
        "counts": {"total_scanned": 46},
        "known_tasks": ["AlertWatch"],
        "window": {"window_hours": 24.0},
        "failures": [],
    }
    p.update(over)
    return p


def _health_payload(**over):
    p = {
        "items": [{"id": "h%d" % i, "state": "green"} for i in range(31)],
        "known_items": ["h%d" % i for i in range(31)],
        "counts": {"green": 31},
    }
    p.update(over)
    return p


def test_assert_payload_accepts_valid():
    G.assert_payload("anomaly_digest", _anomaly_payload())
    G.assert_payload("priority_advice", _health_payload())
    G.assert_payload(
        "case_draft", {"timeline": [{"id": "E1"}], "known_bundle_ids": ["E1"]}
    )


@pytest.mark.parametrize(
    "action,payload",
    [
        ("anomaly_digest", _anomaly_payload(counts={"total_scanned": 0})),
        ("anomaly_digest", _anomaly_payload(known_tasks=[])),
        ("anomaly_digest", _anomaly_payload(window={})),
        ("priority_advice", _health_payload(items=[{"id": "h1"}], known_items=["h1"])),
        ("priority_advice", _health_payload(known_items=[])),
        ("case_draft", {"timeline": [], "known_bundle_ids": []}),
        ("case_draft", {"timeline": [{"id": "E1"}], "known_bundle_ids": []}),
    ],
)
def test_assert_payload_rejects_bad(action, payload):
    with pytest.raises(G.GovAiError):
        G.assert_payload(action, payload)


def test_run_empty_ledger_never_calls_model(tmp_path, monkeypatch):
    monkeypatch.setattr(
        G,
        "build_ledger_input",
        lambda hours=24.0: {
            "counts": {"total_scanned": 0},
            "known_tasks": [],
            "window": {},
        },
    )
    client = type(
        "C",
        (),
        {
            "model": "fake",
            "calls": 0,
            "chat_full": lambda self, *a, **k: self.calls.__add__(1),
        },
    )()
    with pytest.raises(G.GovAiError):
        G.run("anomaly_digest", client=client, output_root=tmp_path, if_changed=False)
    assert client.calls == 0
    assert not (tmp_path / "advice").exists()


# ---------------------------------------------------------------- P1：8796 进化视图


def test_evolution_includes_experience_recent(tmp_path, monkeypatch):
    """serve._evolution 的经验库只读字段（直接读临时 index，不启服务）。"""
    import serve  # 延迟导入：模块级 import 不启服务

    lib = tmp_path / "lib"
    _write_index(
        lib,
        [
            {
                "entry_id": "EXP-SUPERVISION-009",
                "domain": "supervision",
                "cue": "c9",
                "confidence": 0.55,
                "signed_by": "老田",
                "written_at": "2026-10-26T12:46:53+0800",
                "verified": True,
            },
        ],
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    # Path.home() -> tmp_path，需要真实目录结构 ~/.hermes/profiles/zhaoyun/experience
    exp = tmp_path / ".hermes" / "profiles" / "zhaoyun" / "experience"
    exp.mkdir(parents=True, exist_ok=True)
    (exp / "_index.jsonl").write_text(
        (lib / "_index.jsonl").read_text(encoding="utf-8"), encoding="utf-8"
    )
    out = serve.Handler._evolution()
    assert out["experience_verified_total"] == 1
    assert out["experience_recent"][0]["entry_id"] == "EXP-SUPERVISION-009"
    assert out["experience_domains"]["supervision"]["readonly"] is False
    assert out["experience_domains"]["riskguard"]["readonly"] is True
