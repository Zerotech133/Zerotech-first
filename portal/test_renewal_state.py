# -*- coding: utf-8 -*-
"""M-A · 已发去重状态 + 时区口径单测（P3.8-lite 2026-10-20，离线）。

纪律：零网络、零 IO 落到临时目录（tmp_path）、不真发、不碰订阅账本、
不启服务。覆盖：首次 load 空态、mark 后 already_sent 为真、不同 stage /
不同 day 不互相压制、prune 清老记录、坏 JSON 不抛且不覆盖、原子写无残留
.tmp、filter_new 正确切分、env 路径覆盖、不完整 item 拒绝、today_str 随
RENEWAL_TZ 切换。
"""
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import renewal_state as st  # noqa: E402

ITEM = {"open_id": "ou_alice001", "plan_id": "basic_monthly",
        "stage": "t3", "days_left": 3, "end": "2026-11-19 10:00:00"}
DAY = "2026-11-16"


def _state_file(tmp_path, name="renewal_state.json"):
    return str(tmp_path / name)


# 1. 首次 load：文件不存在 → 空结构，不抛、不落盘

def test_load_missing_returns_empty_state(tmp_path):
    path = _state_file(tmp_path)
    state = st.load(path)
    assert state["ok"] is True and state["error"] is None
    assert state["corrupt"] is False
    assert state["sent"] == {} and state["problems"] == []
    assert state["path"] == os.path.abspath(path)
    assert state["version"] == st.STATE_VERSION
    assert os.path.exists(path) is False      # load 只读，不创建文件


# 2. mark 后 already_sent 为真，且真落盘（重新 load 仍在）

def test_mark_sent_then_already_sent(tmp_path):
    path = _state_file(tmp_path)
    state = st.load(path)
    assert st.already_sent(state, "ou_alice001", "basic_monthly", "t3", DAY) is False

    out = st.mark_sent(state, ITEM, DAY, path=path)
    assert out["ok"] is True and out["written"] is True and out["error"] is None
    # checklist 34 改造后键为 <md5指纹16>|plan|stage|day（不再含明文）
    _parts = out["key"].split("|")
    assert len(_parts) == 4
    assert _parts[0] == st._oid_fp("ou_alice001")
    assert _parts[1:] == ["basic_monthly", "t3", "2026-11-16"]
    assert "ou_alice001" not in out["key"]
    assert st.already_sent(state, "ou_alice001", "basic_monthly", "t3", DAY) is True

    again = st.load(path)
    assert again["corrupt"] is False
    assert again["sent"][out["key"]]["day"] == DAY
    assert again["sent"][out["key"]]["stage"] == "t3"
    assert st.already_sent(again, "ou_alice001", "basic_monthly", "t3", DAY) is True
    # 换成同人不同 plan 不压制
    assert st.already_sent(again, "ou_alice001", "pro_monthly", "t3", DAY) is False


# 3. 不同 stage 不互相压制（t3 发过，t1 仍要发）

def test_different_stage_not_suppressed(tmp_path):
    path = _state_file(tmp_path)
    state = st.load(path)
    st.mark_sent(state, ITEM, DAY, path=path)
    assert st.already_sent(state, "ou_alice001", "basic_monthly", "t1", DAY) is False
    out = st.mark_sent(state, dict(ITEM, stage="t1"), DAY, path=path)
    assert out["ok"] is True
    assert len(st.load(path)["sent"]) == 2


# 4. 不同 day 不互相压制（昨日发过，今日重跑仍提醒）

def test_different_day_not_suppressed(tmp_path):
    path = _state_file(tmp_path)
    state = st.load(path)
    st.mark_sent(state, ITEM, "2026-11-15", path=path)
    assert st.already_sent(state, "ou_alice001", "basic_monthly", "t3", "2026-11-15") is True
    assert st.already_sent(state, "ou_alice001", "basic_monthly", "t3", "2026-11-16") is False
    assert len(st.load(path)["sent"]) == 1      # 只标记过 11-15 那一条


# 5. prune：清掉早于 (today - keep_days) 的记录，保留近期

def test_prune_drops_old_records(tmp_path):
    path = _state_file(tmp_path)
    state = st.load(path)
    for open_id, day in (("ou_old00001", "2026-10-01"),
                         ("ou_recent02", "2026-11-15"),
                         ("ou_today003", "2026-11-16")):
        st.mark_sent(state, {"open_id": open_id, "plan_id": "p1",
                             "stage": "t3"}, day, path=path)

    # keep_days=30、today=2026-11-16 → 截止 2026-10-17，10-01 被清
    removed = st.prune(state, keep_days=30, today="2026-11-16")
    assert removed == 1
    keys = set(state["sent"])
    assert not any(st._oid_fp("ou_old00001") in k for k in keys)
    assert any(st._oid_fp("ou_recent02") in k for k in keys)
    assert any(st._oid_fp("ou_today003") in k for k in keys)

    # keep_days=0：只留今天
    assert st.prune(state, keep_days=0, today="2026-11-16") == 1
    assert all(st._oid_fp("ou_today003") in k for k in state["sent"])


# 6. 坏 JSON：load 不抛、标 corrupt；mark 拒绝写，原文件一字未改

def test_corrupt_state_reported_and_not_overwritten(tmp_path):
    path = _state_file(tmp_path)
    broken = '{"sent": {oops'
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(broken)

    state = st.load(path)
    assert state["ok"] is False
    assert state["error"] == "state_corrupt"
    assert state["corrupt"] is True
    assert state["sent"] == {}
    assert [p["reason"] for p in state["problems"]] == ["state_corrupt"]

    out = st.mark_sent(state, ITEM, DAY, path=path)
    assert out["ok"] is False and out["error"] == "state_corrupt"
    assert out["written"] is False
    # checklist 34 改造后键为 <md5指纹16>|plan|stage|day（不再含明文）
    _parts = out["key"].split("|")
    assert len(_parts) == 4
    assert _parts[0] == st._oid_fp("ou_alice001")
    assert _parts[1:] == ["basic_monthly", "t3", "2026-11-16"]
    assert "ou_alice001" not in out["key"]
    with open(path, "r", encoding="utf-8") as fh:
        assert fh.read() == broken          # 不覆盖、不丢已发数据


# 7. 原子写：成功后不残留 .tmp，文件可解析且版本正确

def test_atomic_write_leaves_no_tmp(tmp_path):
    path = _state_file(tmp_path)
    state = st.load(path)
    assert st.mark_sent(state, ITEM, DAY, path=path)["ok"] is True
    assert st.mark_sent(state, dict(ITEM, stage="t1"), DAY, path=path)["ok"] is True

    leftovers = [n for n in os.listdir(str(tmp_path)) if n.endswith(".tmp")]
    assert leftovers == []
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["version"] == st.STATE_VERSION
    assert len(data["sent"]) == 2
    assert "updated_at" in data


# 8. filter_new：按已发状态切分，新项 / 已发项各归各位

def test_filter_new_splits(tmp_path):
    path = _state_file(tmp_path)
    state = st.load(path)
    st.mark_sent(state, ITEM, DAY, path=path)

    items = [
        dict(ITEM),                                    # 已发
        dict(ITEM, open_id="ou_bob0002"),              # 新人同 stage
        dict(ITEM, stage="t1"),                        # 同人不同 stage
        {"open_id": "ou_no_stage03", "plan_id": "p1"},  # 无法去重 → 归新项
    ]
    new_items, sent_items = st.filter_new(items, state, DAY)
    assert [it["open_id"] for it in sent_items] == ["ou_alice001"]
    assert len(new_items) == 3
    assert any(it.get("stage") == "t1" for it in new_items)
    assert any(it["open_id"] == "ou_no_stage03" for it in new_items)


# 9. env RENEWAL_STATE_PATH 覆盖默认路径

def test_env_state_path_override(tmp_path, monkeypatch):
    path = _state_file(tmp_path, "env_state.json")
    monkeypatch.setenv("RENEWAL_STATE_PATH", path)
    assert st.state_path() == os.path.abspath(path)

    state = st.load()                       # 不传 path，走 env
    assert state["path"] == os.path.abspath(path)
    st.mark_sent(state, ITEM, DAY)          # 不传 path，同样走 env
    assert os.path.exists(path) is True
    assert st.already_sent(st.load(), "ou_alice001", "basic_monthly", "t3", DAY) is True


def test_default_state_path_under_portal_work(monkeypatch):
    monkeypatch.delenv("RENEWAL_STATE_PATH", raising=False)
    default = st.state_path().replace("\\", "/")
    assert default.endswith("portal/work/renewal_state.json")


# 10. 不完整 item：拒绝标记，不写文件

def test_mark_sent_rejects_incomplete_item(tmp_path):
    path = _state_file(tmp_path)
    state = st.load(path)
    out = st.mark_sent(state, {"open_id": "ou_x"}, DAY, path=path)
    assert out["ok"] is False and out["error"] == "bad_item"
    assert out["written"] is False and out["key"] is None
    assert os.path.exists(path) is False

    bad_day = st.mark_sent(state, ITEM, "not-a-day", path=path)
    assert bad_day["ok"] is False and bad_day["error"] == "bad_day"
    assert os.path.exists(path) is False


# 11. today_str 随 RENEWAL_TZ 切换（与 reminder 的 days_left 同口径）

def test_today_str_follows_renewal_tz(monkeypatch):
    now = datetime(2026, 11, 16, 16, 30, tzinfo=timezone.utc)  # +08 已是次日
    monkeypatch.setenv("RENEWAL_TZ", "Asia/Shanghai")
    assert st.today_str(now) == "2026-11-17"
    monkeypatch.setenv("RENEWAL_TZ", "UTC")
    assert st.today_str(now) == "2026-11-16"


# 12. 空文件：当空态处理但留痕（problems 有 state_empty）

def test_empty_file_treated_as_empty_state(tmp_path):
    path = _state_file(tmp_path)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("  \n")
    state = st.load(path)
    assert state["ok"] is True and state["corrupt"] is False
    assert state["sent"] == {}
    assert [p["reason"] for p in state["problems"]] == ["state_empty"]


# ── 隐私加固（checklist 34）：键与值不落明文 open_id ──────────
def test_oid_never_plaintext_in_key_or_entry(tmp_path):
    """状态文件全文不得出现明文 open_id；键仍须一客一档一阶段一天唯一。"""
    raw_oid = "ou_privacy_check_9f3a"
    p = tmp_path / "state.json"
    _state = st.load(str(p))
    r = st.mark_sent(_state, {"open_id": raw_oid, "plan_id": "basic_monthly",
                          "stage": "d7"}, "2026-10-21", path=str(p))
    assert r["ok"] is True
    body = p.read_text(encoding="utf-8")
    assert raw_oid not in body, "状态文件不得出现明文 open_id"
    assert raw_oid not in (r["key"] or ""), "键不得含明文 open_id"


def test_sent_key_stable_and_unique():
    a = st.sent_key("ou_A", "basic_monthly", "d7", "2026-10-21")
    b = st.sent_key("ou_A", "basic_monthly", "d7", "2026-10-21")
    c = st.sent_key("ou_B", "basic_monthly", "d7", "2026-10-21")
    d = st.sent_key("ou_A", "basic_monthly", "d1", "2026-10-21")
    assert a == b, "同一客同一档同一阶段同一天必须同键（去重有效）"
    assert a != c and a != d, "不同客/不同阶段必须不同键"


def test_already_sent_roundtrip_after_hash(tmp_path):
    _state = st.load(str(tmp_path / "s0.json"))
    item = {"open_id": "ou_RT", "plan_id": "basic_monthly", "stage": "d3"}
    assert st.already_sent(_state, "ou_RT", "basic_monthly", "d3",
                           "2026-10-21") is False
    p = str(tmp_path / "s.json")
    st.mark_sent(_state, item, "2026-10-21", path=p)
    assert st.already_sent(_state, "ou_RT", "basic_monthly", "d3",
                           "2026-10-21") is True


def test_prune_still_works_with_hashed_keys(tmp_path):
    _state = st.load(str(tmp_path / "s0b.json"))
    p = str(tmp_path / "s.json")
    st.mark_sent(_state, {"open_id": "ou_old", "plan_id": "p", "stage": "d7"},
                 "2026-01-01", path=p)
    st.mark_sent(_state, {"open_id": "ou_new", "plan_id": "p", "stage": "d7"},
                 "2026-10-21", path=p)
    removed = st.prune(_state, keep_days=7, today="2026-10-21")
    assert removed == 1
    assert len(_state["sent"]) == 1
