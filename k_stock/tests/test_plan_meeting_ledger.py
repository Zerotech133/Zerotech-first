# -*- coding: utf-8 -*-
"""D-4+E-4 计划与会议版本账测试（WO-QT-20261020-006，tmp 隔离）。

覆盖工单三部分验收：
  ① 预案版本链：同票两版本（保守/激进画像）生效时点不同，按「信号时刻」
     反查当时生效版本（as_of_plan）；
  ② 会议账：会前分歧两条立场入库、会后修订指向新 plan_version、追溯完整；
  ③ 禁回写（核心验收）：capture/assert 两层识别「用会后版本冒充信号时刻
     版本」并拒+留痕；会议账生效时点必须晚于议题引用时刻；
  ④ append-only：UPDATE/DELETE 由触发器在数据库层拒绝，更正只许追加；
  ⑤ 加法演进零变化：capture 不给 plan_version → 快照无该键（既有路径
     回归，schema 维持 v3）。
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))
sys.path.insert(0, str(HERE / "tests"))

import decision_snapshot                                    # noqa: E402
import plan_meeting_ledger as pml                           # noqa: E402
from plan_meeting_ledger import PlanBackdateError, PlanLedgerError   # noqa: E402
from decision_snapshot import PlanStampError                          # noqa: E402


COMBO = "combo_jijin"
STOCK = "000002.SZ"

# 信号时刻：2026-10-09 09:35 触发；保守版本 10-08 已生效，激进版本 10-10
# （会后修订产出）才生效——用它验证禁回写。
T_SIGNAL = "2026-10-09T09:35:00"
T_BEFORE = "2026-10-08T09:00:00"
T_AFTER = "2026-10-10T14:00:00"
T_FUTURE = "2099-01-01T00:00:00"   # 晚于任何测试运行时刻（capture 时点锚=快照写入时刻）


@pytest.fixture()
def con(tmp_path):
    c = sqlite3.connect(tmp_path / "ledger.db")
    c.row_factory = sqlite3.Row
    pml.ensure_table(c)
    yield c
    c.close()


@pytest.fixture()
def blog(tmp_path, monkeypatch):
    """禁回写留痕文件注入（tmp 隔离，不动仓内 data/）。"""
    p = tmp_path / "backdate.jsonl"
    monkeypatch.setattr(pml, "DEFAULT_BACKDATE_LOG", p)
    return p


def _plan_version(con, pv="pv-001", *, eff=T_BEFORE, risk="conservative",
                  prev=None, plan=None):
    return pml.add_plan_version(
        con, combo_id=COMBO, stock=STOCK, plan_version=pv,
        plan=plan or {"buy_point": "回踩 100.0", "position_pct": 5},
        risk_profile=risk, effective_at=eff, source="role_invest_a",
        prev_version=prev)


def _capture(con, tmp_path, **over):
    """直调 decision_snapshot.capture（引擎透传未接线，capture 即契约入口）。"""
    run = tmp_path / "poc_pv.json"
    run.write_text("[]", encoding="utf-8")
    kw = dict(run_path=run, run_name="poc_pv.json", out={"orders": [{"order_id": "o1"}]},
              account_id=COMBO, strategy="qingping")
    kw.update(over)
    return decision_snapshot.capture(con, **kw)


def _traces(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


# ---------- ① 预案版本链：按信号时刻反查当时生效版本 ----------

def test_plan_chain_two_profiles_asof_lookup(con):
    """同票两版本（保守/激进画像）生效时点不同，按信号时刻反查各得其所。"""
    _plan_version(con, "pv-cons-v1", eff="2026-10-08T09:00:00", risk="conservative")
    _plan_version(con, "pv-aggr-v1", eff="2026-10-08T12:00:00", risk="aggressive")
    # 信号时刻 10-09 09:35：两画像各有其当时生效版本
    got = pml.as_of_plan(con, COMBO, STOCK, T_SIGNAL, risk_profile="conservative")
    assert got["plan_version"] == "pv-cons-v1"
    assert got["plan"]["position_pct"] == 5
    got2 = pml.as_of_plan(con, COMBO, STOCK, T_SIGNAL, risk_profile="aggressive")
    assert got2["plan_version"] == "pv-aggr-v1"
    # 不带画像过滤：取生效最晚的版本
    assert pml.as_of_plan(con, COMBO, STOCK, T_SIGNAL)["plan_version"] == "pv-aggr-v1"


def test_asof_before_all_versions_returns_none(con):
    _plan_version(con, eff=T_SIGNAL)
    assert pml.as_of_plan(con, COMBO, STOCK, T_BEFORE) is None


def test_asof_picks_latest_effective(con):
    """同画像多版本：信号时刻反查取生效最晚（最新已知）版本。"""
    _plan_version(con, "pv-v1", eff="2026-10-08T09:00:00", prev=None)
    _plan_version(con, "pv-v2", eff="2026-10-09T08:00:00", prev="pv-v1")
    assert pml.as_of_plan(con, COMBO, STOCK, T_SIGNAL)["plan_version"] == "pv-v2"
    # 信号在 v2 生效前 → 反查到 v1
    assert pml.as_of_plan(con, COMBO, STOCK, "2026-10-08T15:00:00")["plan_version"] == "pv-v1"


# ---------- 版本链完整性 / append-only 唯一约束 ----------

def test_duplicate_plan_version_rejected(con):
    _plan_version(con, "pv-v1")
    with pytest.raises(PlanLedgerError, match="append-only"):
        _plan_version(con, "pv-v1", eff=T_AFTER)


def test_prev_version_chain_integrity(con):
    _plan_version(con, "pv-v1")
    with pytest.raises(PlanLedgerError, match="prev_not_found"):
        _plan_version(con, "pv-v3", prev="pv-v2")       # 前序不存在→拒
    _plan_version(con, "pv-v2", prev="pv-v1")           # 前序在账→OK
    rows = con.execute("SELECT plan_version, prev_version FROM plan_versions "
                       "ORDER BY id").fetchall()
    assert [(r["plan_version"], r["prev_version"]) for r in rows] == \
        [("pv-v1", None), ("pv-v2", "pv-v1")]


# ---------- ② 会议账：分歧入库 + 修订指向 + 追溯完整 ----------

def test_meeting_dissent_and_revision_traceable(con):
    """会前分歧两条立场入库；会后修订指向新 plan_version；全链可追溯。"""
    _plan_version(con, "pv-v1", eff=T_BEFORE)                       # 会前已生效版本
    _plan_version(con, "pv-v2", eff=T_AFTER, prev="pv-v1")          # 会议修订产出
    r = pml.add_meeting_record(
        con, meeting_id="mtg-20261009-001", topic_type="signal",
        topic_ref="sig-20261009-0001", topic_time=T_SIGNAL,
        dissent=[
            {"role": "role_invest_a", "stance": "维持 pv-v1 保守仓位",
             "reason": "回踩未确认，仓位方案 5% 已够"},
            {"role": "role_watch_b", "stance": "修订为 pv-v2 激进方案",
             "reason": "量能突破证据充分，盯盘判断可加仓"},
        ],
        conclusion="revise", revised_plan_version="pv-v2",
        effective_at="2026-10-10T10:00:00",
        attendees=["role_invest_a", "role_watch_b", "role_trader_c"])
    assert r["conclusion"] == "revise"
    # 追溯完整：会议 → 修订指向版本 → 版本链（prev 指针）→ 生效时点
    m = pml.meeting_by_id(con, "mtg-20261009-001")
    assert [d["role"] for d in m["dissent"]] == ["role_invest_a", "role_watch_b"]
    assert m["revised_plan_version"] == "pv-v2"
    pv2 = con.execute("SELECT * FROM plan_versions WHERE plan_version='pv-v2'").fetchone()
    assert pv2["prev_version"] == "pv-v1" and pv2["effective_at"] == T_AFTER
    assert pml.as_of_plan(con, COMBO, STOCK, T_AFTER)["plan_version"] == "pv-v2"


def test_meeting_effective_must_be_after_topic_time(con, blog):
    """禁回写：会议生效时点未晚于议题引用时刻 → 拒+留痕。"""
    _plan_version(con, "pv-v1", eff=T_BEFORE)
    with pytest.raises(PlanBackdateError, match="禁回写"):
        pml.add_meeting_record(
            con, meeting_id="mtg-bad", topic_type="signal", topic_ref="sig-x",
            topic_time=T_SIGNAL, conclusion="maintain",
            dissent=[{"role": "r", "stance": "s", "reason": "why"}],
            effective_at=T_SIGNAL,                    # =topic_time：未晚于→拒
            attendees=["role_a"], log_path=blog)
    assert not con.execute("SELECT 1 FROM meeting_records WHERE meeting_id='mtg-bad'").fetchone()
    ts = _traces(blog)
    assert len(ts) == 1 and ts[0]["stage"] == "meeting" and ts[0]["meeting_id"] == "mtg-bad"


def test_meeting_dissent_and_conclusion_validated(con):
    """分歧记录必填（定纲：只存共识=丢掉反例）、结论/议题类型白名单。"""
    with pytest.raises(PlanLedgerError, match="dissent_missing"):
        pml.add_meeting_record(con, meeting_id="m1", topic_type="signal",
                               topic_ref="s", topic_time=T_SIGNAL, dissent=[],
                               conclusion="maintain", effective_at=T_AFTER,
                               attendees=["a"])
    with pytest.raises(PlanLedgerError, match="conclusion_invalid"):
        pml.add_meeting_record(con, meeting_id="m2", topic_type="signal",
                               topic_ref="s", topic_time=T_SIGNAL,
                               dissent=[{"role": "r", "stance": "s", "reason": "w"}],
                               conclusion="approve", effective_at=T_AFTER,
                               attendees=["a"])
    with pytest.raises(PlanLedgerError, match="topic_type_invalid"):
        pml.add_meeting_record(con, meeting_id="m3", topic_type="all",
                               topic_ref="s", topic_time=T_SIGNAL,
                               dissent=[{"role": "r", "stance": "s", "reason": "w"}],
                               conclusion="maintain", effective_at=T_AFTER,
                               attendees=["a"])


def test_meeting_revise_requires_existing_and_not_early_plan(con):
    """修订指向版本：必须在账、生效时点不得早于会议生效（防回写口子）。"""
    _plan_version(con, "pv-v1", eff=T_BEFORE)
    _plan_version(con, "pv-early", eff=T_SIGNAL)        # 早于会议生效时点
    with pytest.raises(PlanLedgerError, match="plan_not_found"):
        pml.add_meeting_record(con, meeting_id="m1", topic_type="signal",
                               topic_ref="s", topic_time=T_SIGNAL,
                               dissent=[{"role": "r", "stance": "s", "reason": "w"}],
                               conclusion="revise", revised_plan_version="pv-nope",
                               effective_at=T_AFTER, attendees=["a"])
    with pytest.raises(PlanLedgerError, match="revise_plan_early"):
        pml.add_meeting_record(con, meeting_id="m2", topic_type="signal",
                               topic_ref="s", topic_time=T_SIGNAL,
                               dissent=[{"role": "r", "stance": "s", "reason": "w"}],
                               conclusion="revise", revised_plan_version="pv-early",
                               effective_at=T_AFTER, attendees=["a"])


def test_meeting_correction_is_append_only(con):
    """更正语义：原记录不可改，更正=追加新记录指向被更正者。"""
    kw = dict(topic_type="signal", topic_ref="s", topic_time=T_SIGNAL,
              dissent=[{"role": "r", "stance": "s", "reason": "w"}],
              conclusion="maintain", effective_at=T_AFTER, attendees=["a"])
    pml.add_meeting_record(con, meeting_id="m1", **kw)
    with pytest.raises(PlanLedgerError, match="duplicate_meeting_id"):
        pml.add_meeting_record(con, meeting_id="m1", **kw)
    pml.add_meeting_record(con, meeting_id="m1-fix", corrects_meeting_id="m1", **kw)
    assert pml.meeting_by_id(con, "m1-fix")["corrects_meeting_id"] == "m1"


# ---------- ③ 禁回写：快照关联 plan_version 的时点强制 ----------

def test_capture_backdated_plan_rejected_with_trace(con, tmp_path, blog):
    """试图让快照关联「快照时刻尚未生效」的版本 → 写入时拒+留痕（closed on failure）。

    capture 的时点锚=快照写入时刻（capture 不知信号时刻，生产路径中二者
    同一刻）：pv-v2 生效时点晚于快照时刻（会后才生效），此刻关联即回写。"""
    _plan_version(con, "pv-v1", eff=T_BEFORE)
    _plan_version(con, "pv-v2", eff=T_FUTURE, prev="pv-v1")   # 会后才生效
    stamp = {"combo_id": COMBO, "stock": STOCK, "plan_version": "pv-v2",
             "effective_at": T_FUTURE, "risk_profile": "aggressive"}
    with pytest.raises(PlanBackdateError, match="禁回写"):
        _capture(con, tmp_path, plan_version=stamp)
    # 拒绝后零写入：快照未落账
    assert con.execute("SELECT COUNT(*) FROM decision_snapshots").fetchone()[0] == 0
    ts = _traces(blog)
    assert len(ts) == 1
    assert ts[0]["stage"] == "capture" and ts[0]["plan_version"] == "pv-v2"
    assert ts[0]["at_time"] >= T_SIGNAL[:11]                # 快照时刻（今天）晚于信号日
    assert ts[0]["effective_at"] == T_FUTURE                # 生效时点晚于快照时刻→拒


def test_capture_effective_plan_passes(con, tmp_path, blog):
    """合法关联（信号时刻已生效版本）→ 通过，印记落 context_json["plan_version"]。"""
    _plan_version(con, "pv-v1", eff=T_BEFORE)
    stamp = {"combo_id": COMBO, "stock": STOCK, "plan_version": "pv-v1",
             "effective_at": T_BEFORE, "risk_profile": "conservative"}
    snap = _capture(con, tmp_path, plan_version=stamp)
    assert snap["schema_version"] == decision_snapshot.SNAPSHOT_SCHEMA_VERSION == 3
    row = decision_snapshot.by_ref(con, "poc_pv.json")
    assert row["context_json"]["plan_version"]["plan_version"] == "pv-v1"
    # 审计断言 API 对合法快照通过
    assert pml.assert_snapshot_plan(con, row, log_path=blog)["plan_version"] == "pv-v1"
    assert not blog.exists()


def test_assert_snapshot_plan_detects_backdated_snapshot(con, blog):
    """审计断言 API：识别存量/外部写入的回写快照（引用晚于快照时刻的版本）。"""
    _plan_version(con, "pv-v1", eff=T_BEFORE)
    _plan_version(con, "pv-v2", eff=T_AFTER, prev="pv-v1")
    # 模拟一条绕过 capture 校验的快照（存量/外部写入）：ts=信号时刻，
    # 却引用了 10-10 才生效的 pv-v2
    ctx = json.dumps({"plan_version": {"combo_id": COMBO, "stock": STOCK,
                                       "plan_version": "pv-v2", "effective_at": T_AFTER}})
    decision_snapshot.ensure_table(con)                     # 裸 INSERT 前先建快照表
    con.execute(
        "INSERT INTO decision_snapshots(snapshot_id,ts,schema_version,combo_id,"
        "account_id,strategy,run_ref,run_content,orders_json,skipped_json,"
        "versions_json,context_json) "
        "VALUES('s1',?,3,?,?,?,?,?,?,?,NULL,?)",
        (T_SIGNAL, COMBO, COMBO, "qingping", "legacy.json", "[]", "[]", "[]", ctx))
    snap = decision_snapshot.by_ref(con, "legacy.json")
    with pytest.raises(PlanBackdateError, match="禁回写"):
        pml.assert_snapshot_plan(con, snap, log_path=blog)
    ts = _traces(blog)
    assert ts[0]["stage"] == "assert_snapshot" and ts[0]["plan_version"] == "pv-v2"


def test_assert_snapshot_without_plan_stamp_returns_none(con, tmp_path, blog):
    _plan_version(con, "pv-v1")
    snap = _capture(con, tmp_path)                          # 不带 plan_version
    row = decision_snapshot.by_ref(con, "poc_pv.json")
    assert pml.assert_snapshot_plan(con, row, log_path=blog) is None
    assert snap is not None


def test_capture_plan_not_in_ledger_rejected(con, tmp_path, blog):
    """版本不在账即拒（closed on failure：快照只能引用已入库版本）。"""
    stamp = {"combo_id": COMBO, "stock": STOCK, "plan_version": "pv-ghost",
             "effective_at": T_BEFORE}
    with pytest.raises(PlanLedgerError, match="plan_not_found"):
        _capture(con, tmp_path, plan_version=stamp)


def test_plan_stamp_structural_validation(con, tmp_path):
    """印记结构校验：非 dict / 缺必填键 → PlanStampError（不入账不留快照）。"""
    with pytest.raises(PlanStampError, match="须为 dict"):
        _capture(con, tmp_path, plan_version="pv-v1")
    with pytest.raises(PlanStampError, match="缺必填键"):
        _capture(con, tmp_path, plan_version={"combo_id": COMBO, "stock": STOCK})


# ---------- ⑤ 加法演进零变化 ----------

def test_capture_without_plan_stamp_zero_change(con, tmp_path):
    """不给 plan_version → 快照无该键（combo/pool/signal 既有印记语义不变）。"""
    snap = _capture(con, tmp_path)
    assert snap["schema_version"] == 3
    row = decision_snapshot.by_ref(con, "poc_pv.json")
    assert "plan_version" not in row["context_json"]


# ---------- ④ append-only：触发器层拒绝改写 ----------

def test_append_only_triggers_block_update_and_delete(con):
    _plan_version(con, "pv-v1")
    pml.add_meeting_record(con, meeting_id="m1", topic_type="signal",
                           topic_ref="s", topic_time=T_SIGNAL,
                           dissent=[{"role": "r", "stance": "s", "reason": "w"}],
                           conclusion="maintain", effective_at=T_AFTER,
                           attendees=["a"])
    for table in ("plan_versions", "meeting_records"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute(f"UPDATE {table} SET effective_at='2000-01-01T00:00:00'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute(f"DELETE FROM {table}")


def test_ensure_table_idempotent(con):
    pml.ensure_table(con)
    pml.ensure_table(con)
    assert con.execute("SELECT COUNT(*) FROM plan_versions").fetchone()[0] == 0
    _plan_version(con)
    pml.ensure_table(con)                                   # 重复建表不损数据
    assert con.execute("SELECT COUNT(*) FROM plan_versions").fetchone()[0] == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
