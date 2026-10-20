# -*- coding: utf-8 -*-
"""O-C · G3 演练脚本单测（P3.10-lite · WO-FS-133，2026-10-20）。

覆盖 13 例：隔离拒绝三连（缺 env 退出 2 且不创建真实库文件 / 只缺
RENEWAL_STATE_PATH / 显式指向真实默认路径或 portal/work 内路径）、
全流程演练 pass 退出 0、九步覆盖无 fail、撤销步替身语义（不谎称已实现）、
--json 结构键齐备、is_entitled 与 summary 一致、T-3 提醒命中与正文合规、
清扫后勾稽自洽、重复跑两次结果一致、源码静态防线（无发送调用、
无 ensure_schema 之外的建库路径、缺省拒绝运行）。

纪律：零网络、不真发、不写真实库；所有演练落在 pytest tmp_path 沙盒，
subprocess 运行前先剥离宿主 env 里的 SUBSCRIPTION_DB / RENEWAL_STATE_PATH /
SUBSCRIPTION_OWN_DOMAIN / RENEWAL_TZ，避免外部环境污染判定。
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))          # portal/
ROOT = os.path.dirname(HERE)                               # 仓库根
TOOLS = os.path.join(ROOT, "tools")
for _p in (HERE, TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

SMOKE_SCRIPT = os.path.join(TOOLS, "subscription_smoke.py")

# 禁用调用字面量按分片拼装：本文件只做「断言演练脚本里不存在」，自身
# 不应命中收编对演练脚本的 grep 校验口径
_BAD_TOKENS = ("send" + "_msg", "send" + "_card", "feishu" + "_client",
               "requests" + ".post", "web" + "hook")
_NETWORK_IMPORTS = ("import requests", "import urllib", "http.client",
                    "import socket")
_MONEY_TOKENS = ("元", "¥", "￥", "价格", "金额", "收费", "费用")

# 剥离宿主环境，保证隔离判定只看本测试显式给的沙盒路径
_STRIP_ENV = ("SUBSCRIPTION_DB", "RENEWAL_STATE_PATH",
              "SUBSCRIPTION_OWN_DOMAIN", "RENEWAL_TZ")


def _run_smoke(args=None, env_over=None):
    env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
    if env_over:
        env.update(env_over)
    return subprocess.run([sys.executable, SMOKE_SCRIPT] + (args or []),
                          capture_output=True, text=True, env=env,
                          cwd=ROOT, timeout=180)


# ---------------------------------------------------------------------------
# 沙盒助手
# ---------------------------------------------------------------------------

def _make_sandbox(base: Path):
    d = base / "g3sandbox"
    d.mkdir(parents=True, exist_ok=True)
    return d / "smoke.sqlite3", d / "renewal_state.json"


def _env_for(db, state):
    return {"SUBSCRIPTION_DB": str(db), "RENEWAL_STATE_PATH": str(state)}


def _run_json(base: Path, extra_args=None):
    db, state = _make_sandbox(base)
    proc = _run_smoke(["--json"] + (extra_args or []), _env_for(db, state))
    payload = json.loads(proc.stdout)
    return proc, payload, db, state


def _real_db_path():
    """真实默认库路径（与 subscription_store._db_path 的缺省口径一致）。"""
    return Path(HERE) / "work" / "subscription.sqlite3"


def _real_state_path():
    return Path(HERE) / "work" / "renewal_state.json"


# ---------------------------------------------------------------------------
# 1. 缺省无 env：退出 2 且绝不创建真实库/状态文件
# ---------------------------------------------------------------------------

def test_missing_env_refuses_exit2_and_never_touches_real_paths():
    real_db, real_state = _real_db_path(), _real_state_path()
    db_before, state_before = real_db.exists(), real_state.exists()
    proc = _run_smoke()  # 不传任何 env / 参数
    assert proc.returncode == 2
    assert "隔离" in (proc.stderr + proc.stdout)
    if not db_before:
        assert not real_db.exists(), "拒绝运行时创建了真实库文件"
    if not state_before:
        assert not real_state.exists(), "拒绝运行时创建了真实状态文件"


# ---------------------------------------------------------------------------
# 2. 只缺 RENEWAL_STATE_PATH：同样拒绝
# ---------------------------------------------------------------------------

def test_missing_state_env_refuses_exit2(tmp_path):
    db, _ = _make_sandbox(tmp_path)
    env = {"SUBSCRIPTION_DB": str(db)}  # 只给库不给状态文件
    proc = _run_smoke(["--db", str(db)], env)
    assert proc.returncode == 2


# ---------------------------------------------------------------------------
# 3. 显式指向真实默认路径 / portal/work 内路径：拒绝且不落盘
# ---------------------------------------------------------------------------

def test_explicit_real_default_db_path_rejected(tmp_path):
    _, state = _make_sandbox(tmp_path)
    real_db = _real_db_path()
    proc = _run_smoke(["--db", str(real_db), "--state", str(state)])
    assert proc.returncode == 2


def test_path_inside_real_work_dir_rejected(tmp_path):
    _, state = _make_sandbox(tmp_path)
    probe = _real_db_path().parent / "probe_must_not_exist.sqlite3"
    proc = _run_smoke(["--db", str(probe), "--state", str(state)])
    assert proc.returncode == 2
    assert not probe.exists()


# ---------------------------------------------------------------------------
# 4. 全流程演练 pass，退出 0，默认跑完即删沙盒
# ---------------------------------------------------------------------------

def test_full_run_exit_zero_and_cleans_up(tmp_path):
    proc, payload, db, state = _run_json(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert payload["ok"] is True
    assert not db.exists(), "默认应跑完即删临时库"
    assert not state.exists(), "默认应跑完即删状态文件"


# ---------------------------------------------------------------------------
# 5. steps 覆盖九步且无 fail
# ---------------------------------------------------------------------------

def test_steps_cover_nine_no_fail(tmp_path):
    import subscription_smoke
    _, payload, _, _ = _run_json(tmp_path)
    names = [s["name"] for s in payload["steps"]]
    assert len(names) == 9
    assert set(names) == set(subscription_smoke.STEP_NAMES)
    assert all(s["status"] == "pass" for s in payload["steps"])


# ---------------------------------------------------------------------------
# 6. 撤销步标注替身/SKIP 语义，不谎称已实现
# ---------------------------------------------------------------------------

def test_revoke_step_marked_surrogate(tmp_path):
    _, payload, _, _ = _run_json(tmp_path)
    step = next(s for s in payload["steps"] if s["name"] == "revoke_surrogate")
    assert step["status"] == "pass"
    assert step.get("surrogate") is True
    assert "替身" in step["detail"]
    assert "尚未上线" in step["detail"]
    assert payload["summary"]["surrogate"] == ["revoke_surrogate"]


# ---------------------------------------------------------------------------
# 7. --json 结构键齐备
# ---------------------------------------------------------------------------

def test_json_structure_keys_complete(tmp_path):
    _, payload, _, _ = _run_json(tmp_path)
    assert set(payload) >= {"ok", "steps", "summary"}
    for s in payload["steps"]:
        assert set(s) >= {"name", "status", "detail", "elapsed_ms"}
        assert isinstance(s["elapsed_ms"], int) and s["elapsed_ms"] >= 0
        assert s["status"] in ("pass", "fail", "skip")
    summary = payload["summary"]
    assert set(summary) >= {"total", "pass", "fail", "skip", "surrogate",
                            "db", "state", "keep", "cleanup_errors"}
    assert summary["total"] == len(payload["steps"])
    assert summary["fail"] == 0 and summary["skip"] == 0


# ---------------------------------------------------------------------------
# 8. 开通后 is_entitled 判定与 summary 一致
# ---------------------------------------------------------------------------

def test_entitlement_consistent_with_summary(tmp_path, monkeypatch):
    db, state = _make_sandbox(tmp_path)
    monkeypatch.setenv("SUBSCRIPTION_DB", str(db))
    monkeypatch.setenv("RENEWAL_STATE_PATH", str(state))
    import subscription_store as store
    r = store.ensure_schema()
    assert r["ok"] is True
    store.upsert_subscriber("ou_case8_0001", "用例八")
    store.grant_subscription("ou_case8_0001", "basic_monthly", 30)
    ok, why = store.is_entitled("ou_case8_0001", "news.brief")
    assert ok is True and why == "subscription_active"
    s = store.get_subscriber_summary("ou_case8_0001")
    assert "error" not in s
    assert s["reason"] == "subscription_active"
    assert s["subscriptions"][0]["status"] == "active"
    ok2, why2 = store.is_entitled("ou_case8_none", "news.brief")
    assert ok2 is False and why2 == "no_subscription"


# ---------------------------------------------------------------------------
# 9. T-3 提醒命中且正文合规（无金额、无明文 open_id）
# ---------------------------------------------------------------------------

def test_t3_reminder_hit_and_text_compliance(tmp_path, monkeypatch):
    db, state = _make_sandbox(tmp_path)
    monkeypatch.setenv("SUBSCRIPTION_DB", str(db))
    monkeypatch.setenv("RENEWAL_STATE_PATH", str(state))
    import renewal_reminder as rr
    import subscription_store as store
    store.ensure_schema()
    store.upsert_subscriber("ou_case9_0001", "用例九")
    g = store.grant_subscription("ou_case9_0001", "basic_monthly", 3)
    fetched = rr.collect_subscribers(store=store)
    assert fetched["ok"] is True and fetched["complete"] is True
    report = rr.plan_reminders(fetched, now=g["start"])
    hits = [i for i in report["items"] if i["open_id"] == "ou_case9_0001"]
    assert len(hits) == 1
    assert hits[0]["days_left"] == 3 and hits[0]["stage"] == "t3"
    text = rr.build_reminder_text(hits[0])
    assert "ou_case9_0001" not in text
    assert rr.mask_open_id("ou_case9_0001") in text
    for token in _MONEY_TOKENS:
        assert token not in text


# ---------------------------------------------------------------------------
# 10. 清扫后勾稽仍 ok
# ---------------------------------------------------------------------------

def test_sweep_then_reconcile_ok(tmp_path, monkeypatch):
    db, state = _make_sandbox(tmp_path)
    monkeypatch.setenv("SUBSCRIPTION_DB", str(db))
    monkeypatch.setenv("RENEWAL_STATE_PATH", str(state))
    import reconcile_subscription as rec
    import subscription_store as store
    assert store.ensure_schema()["ok"] is True
    store.upsert_subscriber("ou_case10_0001", "用例十")
    # 直插一条历史已过期行（end 固定在过去，任何时候跑都成立）
    uri = Path(db).resolve().as_uri() + "?mode=rw"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        conn.execute(
            "INSERT INTO subscription(subscriber_open_id, plan_id, start,"
            " end, status) VALUES (?,?,?,?,?)",
            ("ou_case10_0001", "basic_monthly",
             "2026-09-01 00:00:00", "2026-10-01 00:00:00", "active"))
        conn.commit()
    finally:
        conn.close()  # closed on failure：连接必须关
    assert store.expire_sweep() >= 1
    result = rec.reconcile(db_path=str(db))
    assert result["ok"] is True
    assert result["issues"] == []


# ---------------------------------------------------------------------------
# 11. 可重复跑：两次全新沙盒，结果一致
# ---------------------------------------------------------------------------

def test_rerun_fresh_sandbox_same_result(tmp_path):
    sigs = []
    for i in range(2):
        proc, payload, _, _ = _run_json(tmp_path / ("round%d" % i))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert payload["ok"] is True
        sigs.append([(s["name"], s["status"]) for s in payload["steps"]])
    assert sigs[0] == sigs[1]


# ---------------------------------------------------------------------------
# 12. 源码静态防线：无发送调用、无 ensure_schema 之外的建库路径
# ---------------------------------------------------------------------------

def test_source_static_guards():
    src = Path(SMOKE_SCRIPT).read_text(encoding="utf-8")
    # 12a. 发送/网络禁令
    for tok in _BAD_TOKENS + _NETWORK_IMPORTS:
        assert tok not in src, "演练脚本出现禁用字面量: %s" % tok
    # 12b. 建库路径唯一：只有 ensure_schema，无自建 DDL、无裸连接
    assert "ensure_schema" in src
    assert "CREATE TABLE" not in src
    assert "executescript" not in src
    connects = re.findall(r"sqlite3\.connect\([^)]*\)", src)
    assert len(connects) == 2, "直连只允许 _open_rw/_open_ro 两个助手"
    for c in connects:
        assert "uri=True" in c, "裸 sqlite3.connect 会建文件: %s" % c
    # mode 编码在 URI 构造里：恰好一处 ?mode=rw（演练直连）与一处 ?mode=ro（只读）
    assert src.count('"?mode=rw"') == 1 and src.count('"?mode=ro"') == 1
    # 12c. 隔离门槛与真实路径防线在源码中真实存在
    assert "SUBSCRIPTION_DB" in src and "RENEWAL_STATE_PATH" in src
    assert "IsolationError" in src
    assert "subscription.sqlite3" in src and "renewal_state.json" in src
    assert "portal" in src and "work" in src
