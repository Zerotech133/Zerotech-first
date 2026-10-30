# -*- coding: utf-8 -*-
"""N-B · 订阅日跑编排入口单测（飞书中台 P3.9-lite · WO-FS-122，离线）。

纪律：零网络、零服务、零真发、零碰生产。全部用例把 env ``SUBSCRIPTION_DB``
与 ``RENEWAL_STATE_PATH`` 锚到 ``tmp_path``，库与状态文件都在临时目录里造；
时间用 ``--now`` 钉死（2026-11-16 09:00:00），判定不随宿主机时钟漂移。

覆盖：库缺失不建库 / dry-run 不写状态文件 / dry-run 不改账本 / 3 天档命中 /
已发去重 / commit-state 幂等 + 真清扫 / 状态文件 corrupt 直接停且不覆盖 /
勾稽不通过必须非 0 / JSON 结构齐备且 open_id 全掩码 / 源码无发送调用与建库
调用 / --help 明示不发送 / 参数错误退出 2。
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parent
TOOLS = PORTAL.parent / "tools"
for _dir in (str(PORTAL), str(TOOLS)):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

import daily_subscription_job as job  # noqa: E402
import renewal_state as st  # noqa: E402
import subscription_store as ss  # noqa: E402

NOW = "2026-11-16 09:00:00"
TODAY = "2026-11-16"
ALICE = "ou_alice001"
PLAN = "basic_monthly"
# 4 天后到期 → 落在 t3 档（日历日差 = 3）
END_T3 = "2026-11-19 10:00:00"
START = "2026-10-20 00:00:00"

REQUIRED_KEYS = {
    "ok",
    "mode",
    "tz",
    "ledger",
    "state",
    "collected",
    "expired_swept",
    "reminders",
    "items",
    "reconcile",
    "side_effects",
    "problems",
    "warnings",
}


# ---------------------------------------------------------------------------
# 夹具：全部锚到临时目录
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    """DB 与状态文件都指向 tmp；时区钉死 +08:00，避免 zoneinfo 缺失影响口径。"""
    db = tmp_path / "work" / "sub.sqlite3"
    state = tmp_path / "work" / "renewal_state.json"
    monkeypatch.setenv("SUBSCRIPTION_DB", str(db))
    monkeypatch.setenv("RENEWAL_STATE_PATH", str(state))
    monkeypatch.setenv("RENEWAL_TZ", "+08:00")
    monkeypatch.delenv("SUBSCRIPTION_OWN_DOMAIN", raising=False)
    return db, state


def _schema(db):
    """建 5 表 + 种子档位（测试侧造夹具；被测工具自身不得建库）。"""
    ss._connect(create=True).close()


def _subscribe(db, open_id, plan_id, start, end, status="active"):
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO subscriber(open_id, display_name, created_at)"
            " VALUES(?,?,?)",
            (open_id, "测试订阅者", start),
        )
        conn.execute(
            "INSERT INTO subscription(subscriber_open_id, plan_id, start, end,"
            " status) VALUES(?,?,?,?,?)",
            (open_id, plan_id, start, end, status),
        )
        conn.commit()
    finally:
        conn.close()


def _status_of(db, open_id):
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT status FROM subscription WHERE subscriber_open_id=?", (open_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _row_counts(db):
    conn = sqlite3.connect(str(db))
    try:
        return tuple(
            conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
            for t in (
                "subscriber",
                "plan",
                "subscription",
                "entitlement_log",
                "payment_order",
            )
        )
    finally:
        conn.close()


def _run(capsys, *argv):
    code = job.main(list(argv))
    return code, capsys.readouterr().out


def _run_json(capsys, *argv):
    code, out = _run(capsys, *argv)
    return code, json.loads(out)


# ---------------------------------------------------------------------------
# 1. 库不存在：报 ledger_missing、退出 1、不建库
# ---------------------------------------------------------------------------


def test_01_ledger_missing_reports_and_creates_nothing(env, capsys):
    db, state = env
    code, data = _run_json(capsys, "--json", "--now", NOW)

    assert code == job.EXIT_ISSUES == 1
    assert data["ok"] is False
    assert data["mode"] == "dry-run"
    assert [p["reason"] for p in data["problems"]] == ["ledger_missing"]
    assert data["ledger"]["exists"] is False
    assert data["reconcile"]["skipped"] == "ledger_missing"
    assert data["expired_swept"] is None
    assert data["side_effects"] == []
    # 关键：没有顺手建库、也没有落状态文件
    assert db.exists() is False
    assert state.exists() is False


# ---------------------------------------------------------------------------
# 2. dry-run 不写状态文件
# ---------------------------------------------------------------------------


def test_02_dry_run_writes_no_state_file(env, capsys):
    db, state = env
    _schema(db)
    _subscribe(db, ALICE, PLAN, START, END_T3)

    code, data = _run_json(capsys, "--dry-run", "--json", "--now", NOW)

    assert code == 0
    assert data["reminders"]["to_send"] == 1
    assert data["state"]["corrupt"] is False
    assert data["side_effects"] == []
    assert state.exists() is False


# ---------------------------------------------------------------------------
# 3. dry-run 不改账本（mtime 与行数都不变）
# ---------------------------------------------------------------------------


def test_03_dry_run_leaves_ledger_untouched(env, capsys):
    db, state = env
    _schema(db)
    _subscribe(db, ALICE, PLAN, START, END_T3)
    _subscribe(db, "ou_bob0002", PLAN, START, "2026-11-18 10:00:00")

    before_mtime = db.stat().st_mtime_ns
    before_rows = _row_counts(db)

    _run_json(capsys, "--dry-run", "--json", "--now", NOW)
    _run_json(capsys, "--dry-run", "--json", "--now", NOW)

    assert db.stat().st_mtime_ns == before_mtime
    assert _row_counts(db) == before_rows


# ---------------------------------------------------------------------------
# 4. 3 天后到期 → to_send=1
# ---------------------------------------------------------------------------


def test_04_three_days_left_is_due(env, capsys):
    db, state = env
    _schema(db)
    _subscribe(db, ALICE, PLAN, START, END_T3)

    code, data = _run_json(capsys, "--dry-run", "--json", "--now", NOW)

    assert code == 0
    assert data["reminders"]["total"] == 1
    assert data["reminders"]["to_send"] == 1
    assert data["reminders"]["already_sent"] == 0
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["stage"] == "t3"
    assert item["days_left"] == 3
    assert item["plan_id"] == PLAN
    assert item["open_id_masked"] == "ou_***001"


# ---------------------------------------------------------------------------
# 5. 已 mark_sent 过的项 → already_sent=1、to_send=0
# ---------------------------------------------------------------------------


def test_05_already_sent_is_not_queued_again(env, capsys):
    db, state = env
    _schema(db)
    _subscribe(db, ALICE, PLAN, START, END_T3)

    saved = st.load(str(state))
    out = st.mark_sent(
        saved,
        {"open_id": ALICE, "plan_id": PLAN, "stage": "t3"},
        TODAY,
        path=str(state),
    )
    assert out["ok"] is True and out["written"] is True

    code, data = _run_json(capsys, "--dry-run", "--json", "--now", NOW)

    assert code == 0
    assert data["reminders"]["total"] == 1
    assert data["reminders"]["already_sent"] == 1
    assert data["reminders"]["to_send"] == 0
    assert data["items"] == []


# ---------------------------------------------------------------------------
# 6. commit-state 写状态文件，第二次运行 to_send 归 0（幂等）
# ---------------------------------------------------------------------------


def test_06_commit_state_is_idempotent(env, capsys):
    """commit-state + --mark-sent 才落标记；落了之后重跑幂等。

    收编修订（P3.9-lite）：原用例断言 commit-state 自动落标记，与工位自陈
    风险「标记为已发但实际没发、提醒被永久吞掉」冲突。现改为必须显式
    --mark-sent，本用例覆盖「显式开启后的幂等」。
    """
    db, state = env
    _schema(db)
    _subscribe(db, ALICE, PLAN, START, END_T3)

    code, first = _run_json(
        capsys, "--commit-state", "--mark-sent", "--json", "--now", NOW
    )

    assert code == 0
    assert first["mode"] == "commit-state"
    assert first["expired_swept"]["ok"] is True
    assert first["reminders"]["to_send"] == 1
    assert any(s.startswith("已写账本") for s in first["side_effects"])
    assert any(s.startswith("已写状态文件") for s in first["side_effects"])
    # 标记了但没投递，必须留下可见警告，不得只在 side_effects 里含糊
    assert any(w.startswith("marked_without_delivery") for w in first["warnings"])
    assert state.exists() is True
    on_disk = json.loads(state.read_text(encoding="utf-8"))
    assert len(on_disk["sent"]) == 1

    code2, second = _run_json(
        capsys, "--commit-state", "--mark-sent", "--json", "--now", NOW
    )

    assert code2 == 0
    assert second["reminders"]["to_send"] == 0
    assert second["reminders"]["already_sent"] == 1
    assert second["items"] == []
    assert len(json.loads(state.read_text(encoding="utf-8"))["sent"]) == 1


def test_06b_commit_state_without_mark_sent_never_marks(env, capsys):
    """commit-state 不带 --mark-sent 时：写账本清扫，但绝不落已发标记。

    背景：发送侧尚未接入，此时落标记等于把提醒永久吞掉（同日去重键已写，
    次日 stage 变更前不会重发）。此例钉住「不为未发生的投递打标记」——
    清账本可以，标记不行，且必须给出可见的跳过提示与警告。
    """
    db, state = env
    _schema(db)
    _subscribe(db, ALICE, PLAN, START, END_T3)

    code, rep = _run_json(capsys, "--commit-state", "--json", "--now", NOW)

    assert code == 0
    assert any(s.startswith("已写账本") for s in rep["side_effects"])
    assert not any(s.startswith("已写状态文件") for s in rep["side_effects"])
    assert any(s.startswith("未写状态文件") for s in rep["side_effects"])
    assert "mark_sent_skipped_no_sender" in rep["warnings"]
    assert rep["reminders"]["to_send"] == 1, "只算不标记，待发清单必须仍在"
    assert state.exists() is False, "状态文件不得被创建"

    # 再跑一次仍算得到同一批：证明没被标记吞掉
    code2, rep2 = _run_json(capsys, "--commit-state", "--json", "--now", NOW)
    assert code2 == 0
    assert rep2["reminders"]["to_send"] == 1
    assert rep2["reminders"]["already_sent"] == 0
    assert state.exists() is False


# ---------------------------------------------------------------------------
# 7. commit-state 真调 expire_sweep（过期未清扫行 → expired）
# ---------------------------------------------------------------------------


def test_07_commit_state_runs_expire_sweep(env, capsys):
    db, state = env
    _schema(db)
    _subscribe(
        db,
        "ou_stale001",
        PLAN,
        "2026-10-01 00:00:00",
        "2026-11-01 00:00:00",
        status="active",
    )

    # dry-run 只看不扫：账本仍是 active，勾稽报 stale_active → 退出 1
    code, dry = _run_json(capsys, "--dry-run", "--json", "--now", NOW)
    assert code == 1
    assert dry["expired_swept"] is None
    assert dry["reconcile"]["ok"] is False
    assert _status_of(db, "ou_stale001") == "active"

    code, data = _run_json(capsys, "--commit-state", "--json", "--now", NOW)

    assert data["expired_swept"] == {"ok": True, "swept": 1}
    assert _status_of(db, "ou_stale001") == "expired"
    assert code == 0


# ---------------------------------------------------------------------------
# 8. 状态文件 corrupt：退出 1、不继续、不覆盖
# ---------------------------------------------------------------------------


def test_08_corrupt_state_stops_without_overwrite(env, capsys):
    db, state = env
    _schema(db)
    _subscribe(db, ALICE, PLAN, START, END_T3)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_bytes(b'{"sent": {oops')

    before = state.read_bytes()
    code, data = _run_json(capsys, "--commit-state", "--json", "--now", NOW)

    assert code == 1
    assert data["state"]["corrupt"] is True
    assert any(p["reason"] == "state_corrupt" for p in data["problems"])
    # 没有继续算、没有写账本、也没有 mark
    assert data["collected"]["count"] == 0
    assert data["items"] == []
    assert data["expired_swept"] is None
    assert data["side_effects"] == []
    assert state.read_bytes() == before


# ---------------------------------------------------------------------------
# 9. 勾稽发现问题（paid 无订阅）→ 退出 1 且 reconcile.ok=false
# ---------------------------------------------------------------------------


def test_09_reconcile_issue_makes_exit_nonzero(env, capsys):
    db, state = env
    _schema(db)
    _subscribe(db, ALICE, PLAN, START, END_T3)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO payment_order(order_id, open_id, amount, channel,"
            " status, created_at) VALUES(?,?,?,?,?,?)",
            ("PO-0001", "ou_paidonly01", "", "", "paid", "2026-11-10 12:00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    code, data = _run_json(capsys, "--dry-run", "--json", "--now", NOW)

    assert code == 1
    assert data["ok"] is False
    assert data["reconcile"]["ok"] is False
    assert data["reconcile"]["issues_count"] >= 1
    assert any(p["reason"] == "reconcile_not_ok" for p in data["problems"])
    # 提醒侧照常算出来了，但整体不得被当成成功
    assert data["reminders"]["to_send"] == 1


# ---------------------------------------------------------------------------
# 10. --json 结构齐备、open_id 掩码、全文不含明文 open_id
# ---------------------------------------------------------------------------


def test_10_json_shape_and_no_plain_open_id(env, capsys):
    db, state = env
    _schema(db)
    _subscribe(db, ALICE, PLAN, START, END_T3)

    code, out = _run(capsys, "--dry-run", "--json", "--now", NOW)
    data = json.loads(out)

    assert code == 0
    assert REQUIRED_KEYS <= set(data)
    # P3.10-lite 契约演进：collected 追加分页元信息三键（pages/truncated/complete）
    assert set(data["collected"]) == {
        "count",
        "warnings",
        "problems",
        "pages",
        "truncated",
        "complete",
    }
    assert set(data["reminders"]) == {"total", "to_send", "already_sent", "skipped"}
    assert set(data["reconcile"]) == {"ok", "checked", "issues_count"}
    assert data["items"] and set(data["items"][0]) == {
        "open_id_masked",
        "plan_id",
        "days_left",
        "stage",
    }
    assert data["items"][0]["open_id_masked"] == "ou_***001"
    # 明文 open_id 不得出现在输出任何位置（日志 / 汇报会原样贴这段）
    assert ALICE not in out
    assert "alice" not in out


# ---------------------------------------------------------------------------
# 11. 源码静态断言：无发送调用、无建库调用
# ---------------------------------------------------------------------------


def test_11_source_has_no_send_and_no_schema_calls():
    src = (TOOLS / "daily_subscription_job.py").read_text(encoding="utf-8")
    for token in (
        "send_msg",
        "send_card",
        "feishu_client",
        "requests.post",
        "ensure_schema",
    ):
        assert token not in src, "源码不得出现 %s" % token
    # 确实用了该用的两个写/读入口，说明不是空壳
    assert "expire_sweep" in src
    assert "_state_mod.mark_sent" in src
    assert "reconcile(" in src


# ---------------------------------------------------------------------------
# 12. --help 明示不发送；13. 参数错误退出 2
# ---------------------------------------------------------------------------


def test_12_help_states_no_message_is_sent(capsys):
    with pytest.raises(SystemExit) as excinfo:
        job.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "本工具不发送任何消息" in out
    assert "--commit-state" in out and "--dry-run" in out


def test_13_bad_now_is_argument_error(env, capsys):
    with pytest.raises(SystemExit) as excinfo:
        job.main(["--now", "not-a-timestamp"])
    assert excinfo.value.code == job.EXIT_FATAL == 2


# ---------------------------------------------------------------------------
# P3.10-lite O-B 追加：分页元信息与截断联动
# ---------------------------------------------------------------------------


def test_ob_6_json_collected_has_paging_keys(env, capsys):
    """--json 的 collected 必须带 pages/truncated/complete 三键（只追加口径）。"""
    db, state = env
    _schema(db)
    _subscribe(db, ALICE, PLAN, START, END_T3)

    code, out = _run(capsys, "--dry-run", "--json", "--now", NOW)
    data = json.loads(out)

    assert code == 0
    c = data["collected"]
    assert isinstance(c["pages"], int) and c["pages"] >= 0
    assert c["truncated"] is False and c["complete"] is True


def test_ob_7_truncated_forces_exit_1(env, capsys, monkeypatch):
    """截断必须影响退出码：truncated=True → problems 注入 + 退出 1。"""
    db, state = env
    _schema(db)
    _subscribe(db, ALICE, PLAN, START, END_T3)

    import daily_subscription_job as job

    real = job._reminder.collect_subscribers

    def fake_collect(**kw):
        got = dict(real(**kw))
        got["pages"] = 50
        got["truncated"] = True
        got["complete"] = False
        return got

    monkeypatch.setattr(job._reminder, "collect_subscribers", fake_collect)
    code, out = _run(capsys, "--dry-run", "--json", "--now", NOW)
    data = json.loads(out)

    assert code == 1
    assert data["collected"]["truncated"] is True
    assert any(p["reason"] == "subscribers_truncated" for p in data["problems"])
    assert data["ok"] is False
