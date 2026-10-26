# -*- coding: utf-8 -*-
"""K-QUANT-20261026-OSLEG-P2B：真实 store P2b 闸测试。

- 默认硬闸仍拒绝真实 bridge_inbox；
- P2b 放行必须 allow_real_store + only_msg ⊆ 批准自测票；
- only_msg 过滤只消费指定单，其他 eligible 原样保留；重复运行 0 新消费。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from gateway import osleg_consumer as C  # noqa: E402

APPROVED = "lg-060c67fbf89a"


def _ticket(msg_id, source="synthetic", kind="question", state="inbox"):
    return {
        "id": msg_id,
        "idempotency_key": "key-" + msg_id,
        "kind": kind,
        "source": source,
        "payload": {"q": "自测", "report_id": "R-1", "trace_id": "T-1"},
        "submitted_at": "2026-10-26T22:00:00+0800",
        "state": state,
    }


def _write_inbox(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / "inbox").mkdir(exist_ok=True)
    for rec in (_ticket(APPROVED, source="e2e"),
                _ticket("lg-other-synthetic"),
                _ticket("lg-5917d4c4357b", source="synthetic")):
        (root / "inbox" / (rec["id"] + ".json")).write_text(
            json.dumps(rec, ensure_ascii=False), encoding="utf-8")


def test_default_gate_still_refuses_real_store():
    with pytest.raises(C.ConsumerRefused):
        C._assert_isolated(C.REAL_STORE)
    with pytest.raises(C.ConsumerRefused):
        C._assert_isolated(C.REAL_STORE, allow_real_store=True)  # 无 only_msg
    with pytest.raises(C.ConsumerRefused):
        C._assert_isolated(C.REAL_STORE, allow_real_store=True,
                           only_msg_ids=("lg-other-synthetic",))
    C._assert_isolated(C.REAL_STORE, allow_real_store=True,
                       only_msg_ids=(APPROVED,))  # 批准单放行


def test_run_once_only_msg_filters_and_idempotent(tmp_path):
    _write_inbox(tmp_path)
    res = C.run_once(tmp_path, only_msg_ids=(APPROVED,))
    assert res["ok"] is True
    assert [x["msg_id"] for x in res["consumed"]] == [APPROVED]
    assert not res["failed"]
    # 其它 eligible 未被消费
    assert (tmp_path / "inbox" / "lg-other-synthetic.json").exists()
    assert (tmp_path / "inbox" / "lg-5917d4c4357b.json").exists()
    # 重复运行：0 新消费；台账记 done（票据已消费移动，不再进 eligible）
    res2 = C.run_once(tmp_path, only_msg_ids=(APPROVED,))
    assert res2["ok"] is True and res2["consumed"] == []
    ledger = json.loads((tmp_path / "osleg_consumer" / "ledger.json")
                        .read_text(encoding="utf-8"))
    assert ledger["entries"][APPROVED]["status"] == "done"


def test_run_once_rejects_unapproved_only_msg(tmp_path):
    _write_inbox(tmp_path)
    with pytest.raises(C.ConsumerRefused):
        C.run_once(tmp_path, only_msg_ids=("lg-other-synthetic",))

