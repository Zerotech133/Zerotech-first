# -*- coding: utf-8 -*-
"""zhuge_challenge 单测（WO-LEGION-ZHUGE-CHALLENGE-20261027 / C1）。

覆盖：白名单放行/拒绝（缺写点、allow∩deny）、PyYAML→fallback 显式降级、
快照 doing 过滤、坏 JSON、空快照拒绝、状态机合法/非法迁移、幂等、干跑不写、
账本损坏行号、沙盘仓写拒绝、strict-sandbox、无网络/无 shell 静态断言。

全部用例 tmp_path 隔离：不碰主仓 work/、不碰 digital-legion-sandbox、不联网。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import zhuge_challenge as zc  # noqa: E402

DEFAULT_SANDBOX = "C:/tmp/zhuge-challenge-fixture-sandbox"
DEFAULT_ALLOW = ["file_read", "list_dir", "git_log_read", "kanban_read", "kanban_card_write"]
DEFAULT_DENY = ["shell_exec", "file_write", "paperclip_api_write", "hermes_control", "network_egress"]


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------
def whitelist_text(allow=None, deny=None, card_type="challenge", board="zhugeliang-review",
                   prefix=DEFAULT_SANDBOX) -> str:
    allow = list(DEFAULT_ALLOW if allow is None else allow)
    deny = list(DEFAULT_DENY if deny is None else deny)
    lines = ['sandbox_repo_prefix: "%s"' % prefix, "", "allow:"]
    for name in allow:
        lines.append("  - name: %s" % name)
        if name == "kanban_card_write":
            lines.append("    card_type: %s" % card_type)
            lines.append("    board: %s" % board)
    lines.append("")
    lines.append("deny:")
    for name in deny:
        lines.append("  - name: %s" % name)
    return "\n".join(lines) + "\n"


SNAPSHOT_ROWS = [
    {"task_id": "T-1", "title": "晨报定时发送器", "status": "doing", "owner": "A0"},
    {"task_id": "T-2", "title": "采纳率接线", "status": "doing", "owner": "A0"},
    {"task_id": "T-3", "title": "已完成项", "status": "done", "owner": "A0"},
]


def write_json(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture()
def env(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "repo"
    root.mkdir()
    ledger = root / "work" / "zhuge_challenge" / "cards.jsonl"
    wl = tmp_path / "whitelist.yaml"
    wl.write_text(whitelist_text(), encoding="utf-8")
    snap = write_json(tmp_path / "snapshot.json", SNAPSHOT_ROWS)
    empty = write_json(tmp_path / "empty.json", [])
    bad = tmp_path / "bad.json"
    bad.write_text("{not-json", encoding="utf-8")
    tasks = write_json(tmp_path / "tasks.json", {
        "T-A": {"desc": "做 A", "status": "doing", "owner": "A0", "project": "p1"},
        "T-B": {"desc": "做 B", "status": "done"},
        "T-C": {"desc": "评审 C", "status": "review"},
    })
    return SimpleNamespace(root=root, ledger=ledger, whitelist=wl, snapshot=snap,
                           empty=empty, bad=bad, tasks=tasks, tmp=tmp_path)


def base_args(env, *extra) -> list:
    return ["--json", "--ledger", str(env.ledger), "--whitelist", str(env.whitelist),
            "--repo-root", str(env.root), *extra]


def run(capsys, argv: list) -> tuple[int, dict]:
    rc = zc.main(argv)
    out = capsys.readouterr().out.strip()
    data = json.loads(out) if out else {}
    return rc, data


def do_emit(capsys, env, extra=()):
    argv = base_args(env, "emit", "--snapshot", str(env.snapshot),
                     "--date", "2026-10-27", *extra)
    return run(capsys, argv)


def ledger_lines(env) -> list:
    if not env.ledger.exists():
        return []
    return [ln for ln in env.ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# 1. 白名单（三件套②）校验
# ---------------------------------------------------------------------------
def test_check_whitelist_ok(capsys, env):
    rc, data = run(capsys, base_args(env, "check"))
    assert rc == 0
    assert data["ok"] is True
    assert data["parser"] in ("pyyaml", "fallback")
    assert data["ops_allowed"]["ledger_append"] is True
    assert data["sandbox_repo_prefix"] == DEFAULT_SANDBOX
    assert "kanban_card_write" in data["allow"]


def test_check_whitelist_reject_missing_card_write(capsys, env):
    env.whitelist.write_text(
        whitelist_text(allow=["file_read", "list_dir", "git_log_read", "kanban_read"]),
        encoding="utf-8")
    rc, data = run(capsys, base_args(env, "check"))
    assert rc == 2
    assert data["error"]["code"] == "whitelist_reject"
    assert "kanban_card_write" in data["error"]["detail"]


def test_check_whitelist_reject_intersection(capsys, env):
    env.whitelist.write_text(
        whitelist_text(deny=DEFAULT_DENY + ["kanban_card_write"]), encoding="utf-8")
    rc, data = run(capsys, base_args(env, "check"))
    assert rc == 2
    assert "交集" in data["error"]["detail"]


def test_whitelist_wrong_board_rejected(capsys, env):
    env.whitelist.write_text(whitelist_text(board="some-other-board"), encoding="utf-8")
    rc, data = run(capsys, base_args(env, "check"))
    assert rc == 2
    assert "zhugeliang-review" in data["error"]["detail"]


def test_whitelist_fallback_parser_explicit(capsys, env):
    loaded = zc.load_whitelist(env.whitelist, parser="fallback")
    assert loaded["_parser"] == "fallback"
    report = zc.validate_whitelist(loaded)
    assert report["ok"] is True
    assert report["parser"] == "fallback"
    # CLI 层同样可显式 fallback（全局参数须在子命令之前）
    argv = ["--json", "--ledger", str(env.ledger), "--whitelist", str(env.whitelist),
            "--repo-root", str(env.root), "--whitelist-parser", "fallback", "check"]
    rc, data = run(capsys, argv)
    assert rc == 0
    assert data["parser"] == "fallback"


def test_emit_refused_when_whitelist_missing(capsys, env):
    env.whitelist.unlink()
    rc, data = do_emit(capsys, env)
    assert rc == 2
    assert data["error"]["code"] == "whitelist_missing"
    assert not env.ledger.exists()  # fail-closed：拒绝时不产生任何写


# ---------------------------------------------------------------------------
# 2. 快照（本地文件 / tasks.json 过滤 doing）
# ---------------------------------------------------------------------------
def test_snapshot_filters_doing_from_tasks_ledger(capsys, env):
    argv = base_args(env, "snapshot", "--tasks", str(env.tasks))
    rc, data = run(capsys, argv)
    assert rc == 0
    assert data["meta"]["source_kind"] == "tasks_ledger"
    assert data["meta"]["selected"] == 1
    assert data["meta"]["total"] == 3
    assert [it["task_id"] for it in data["snapshot"]["items"]] == ["T-A"]
    assert data["snapshot"]["filter_status"] == ["doing"]


def test_snapshot_file_without_filter(capsys, env):
    rc, data = run(capsys, base_args(env, "snapshot", "--snapshot", str(env.snapshot)))
    assert rc == 0
    assert data["meta"]["selected"] == 3
    assert data["snapshot"]["inside_sandbox_prefix"] is False  # tmp 快照不在沙盘前缀
    assert data["meta"]["warnings"]  # MVP dev 适配提示必须显式记录


def test_emit_bad_json_snapshot(capsys, env):
    rc, data = run(capsys, base_args(env, "emit", "--snapshot", str(env.bad)))
    assert rc == 3
    assert data["error"]["code"] == "bad_snapshot_json"
    assert not env.ledger.exists()


def test_emit_empty_snapshot_refused(capsys, env):
    rc, data = run(capsys, base_args(env, "emit", "--snapshot", str(env.empty)))
    assert rc == 2
    assert data["error"]["code"] == "empty_snapshot"
    assert not env.ledger.exists()


def test_emit_strict_sandbox_refuses_external(capsys, env):
    argv = base_args(env, "emit", "--snapshot", str(env.snapshot), "--strict-sandbox")
    rc, data = run(capsys, argv)
    assert rc == 2
    assert data["error"]["code"] == "snapshot_outside_sandbox"


# ---------------------------------------------------------------------------
# 3. emit / 幂等 / 干跑 / 追加式
# ---------------------------------------------------------------------------
def test_emit_creates_open_card_append_only(capsys, env):
    rc, data = do_emit(capsys, env)
    assert rc == 0
    assert data["ok"] is True and data["written"] is True and data["duplicate"] is False
    card = data["card"]
    assert re.match(r"^ZQ-\d{8}-01$", card["card_id"])
    assert card["status"] == "open"
    assert card["board"] == "zhugeliang-review"
    assert card["card_type"] == "challenge"
    assert card["verdict_request"] == "respond_or_fix"
    assert card["severity"] == "medium"
    assert len(card["ref_questions"]) == 9  # 3 doing 任务 × 3 维度
    assert card["target_plan_snapshot"]["source_kind"] == "snapshot_file"
    assert card["whitelist"]["ops_allowed"]["ledger_append"] is True
    lines = ledger_lines(env)
    assert len(lines) == 1
    ev = json.loads(lines[0])
    assert ev["event"] == "created" and ev["card_id"] == card["card_id"]


def test_emit_idempotent_same_snapshot(capsys, env):
    rc1, data1 = do_emit(capsys, env)
    assert rc1 == 0 and data1["written"] is True
    rc2, data2 = do_emit(capsys, env)
    assert rc2 == 0
    assert data2["duplicate"] is True and data2["written"] is False
    assert data2["card"]["card_id"] == data1["card"]["card_id"]
    assert len(ledger_lines(env)) == 1  # 幂等不重复追加


def test_emit_dry_run_no_write(capsys, env):
    rc, data = do_emit(capsys, env, extra=("--dry-run",))
    assert rc == 0
    assert data["dry_run"] is True and data["written"] is False
    assert data["card"]["status"] == "open"
    assert not env.ledger.exists()


def test_emit_max_questions_capped(capsys, env):
    rc, data = do_emit(capsys, env, extra=("--max-questions", "4"))
    assert rc == 0
    assert len(data["card"]["ref_questions"]) == 4


def test_emit_refuses_ledger_in_sandbox(capsys, env, tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    env.whitelist.write_text(whitelist_text(prefix=str(sandbox)), encoding="utf-8")
    env.ledger = sandbox / "cards.jsonl"
    rc, data = do_emit(capsys, env)
    assert rc == 2
    assert data["error"]["code"] == "sandbox_write_denied"
    assert not env.ledger.exists()


# ---------------------------------------------------------------------------
# 4. 状态机 open→answered→closed / 非法迁移留因
# ---------------------------------------------------------------------------
def test_respond_and_close_happy_path(capsys, env):
    _, emit_data = do_emit(capsys, env)
    cid = emit_data["card"]["card_id"]
    rc, data = run(capsys, base_args(env, "respond", "--card-id", cid,
                                     "--by", "A0田丰", "--note", "已答辩：补了回滚预案"))
    assert rc == 0 and data["card"]["status"] == "answered"
    assert data["card"]["responded_by"] == "A0田丰"
    rc, data = run(capsys, base_args(env, "close", "--card-id", cid, "--outcome", "accepted",
                                     "--by", "laotian", "--note", "验收通过"))
    assert rc == 0
    assert data["card"]["status"] == "closed"
    assert data["card"]["outcome"] == "accepted"
    rc, show = run(capsys, base_args(env, "show", "--card-id", cid))
    assert rc == 0 and show["history_count"] == 3
    assert [e["event"] for e in show["history"]] == ["created", "answered", "closed"]


def test_illegal_transition_close_before_answered_records_reason(capsys, env):
    _, emit_data = do_emit(capsys, env)
    cid = emit_data["card"]["card_id"]
    rc, data = run(capsys, base_args(env, "close", "--card-id", cid, "--outcome", "accepted",
                                     "--by", "laotian", "--note", "直接结案"))
    assert rc == 2
    assert data["error"]["code"] == "illegal_transition"
    lines = ledger_lines(env)
    assert len(lines) == 2  # created + rejected（留因）
    rejected = json.loads(lines[1])
    assert rejected["event"] == "rejected"
    assert rejected["op"] == "close"
    assert "answered" in rejected["reason"]
    rc, listed = run(capsys, base_args(env, "list", "--status", "open"))
    assert rc == 0 and listed["count"] == 1  # 状态未被非法迁移改变


def test_illegal_transition_respond_after_closed(capsys, env):
    _, emit_data = do_emit(capsys, env)
    cid = emit_data["card"]["card_id"]
    run(capsys, base_args(env, "respond", "--card-id", cid, "--by", "A0", "--note", "答辩"))
    run(capsys, base_args(env, "close", "--card-id", cid, "--outcome", "accepted",
                          "--by", "laotian", "--note", "验收"))
    rc, data = run(capsys, base_args(env, "respond", "--card-id", cid,
                                     "--by", "A0", "--note", "再答辩一次"))
    assert rc == 2
    assert data["error"]["code"] == "illegal_transition"
    assert json.loads(ledger_lines(env)[-1])["event"] == "rejected"


def test_respond_idempotent_same_payload(capsys, env):
    _, emit_data = do_emit(capsys, env)
    cid = emit_data["card"]["card_id"]
    args = base_args(env, "respond", "--card-id", cid, "--by", "A0", "--note", "同一份答辩")
    rc1, data1 = run(capsys, args)
    assert rc1 == 0 and data1["written"] is True
    rc2, data2 = run(capsys, args)
    assert rc2 == 0
    assert data2["duplicate"] is True and data2["written"] is False
    assert len(ledger_lines(env)) == 2  # created + answered，未重复追加


def test_respond_requires_by_and_note(capsys, env):
    _, emit_data = do_emit(capsys, env)
    cid = emit_data["card"]["card_id"]
    rc, data = run(capsys, base_args(env, "respond", "--card-id", cid, "--by", " ", "--note", "x"))
    assert rc == 3
    assert data["error"]["code"] == "bad_input"


def test_show_unknown_card(capsys, env):
    rc, data = run(capsys, base_args(env, "show", "--card-id", "ZQ-20261027-99"))
    assert rc == 3
    assert data["error"]["code"] == "card_not_found"


# ---------------------------------------------------------------------------
# 5. list / 干跑 / 账本损坏 / 静态纪律
# ---------------------------------------------------------------------------
def test_list_status_filter(capsys, env):
    _, emit1 = do_emit(capsys, env)
    rc, data = run(capsys, base_args(env, "list"))
    assert rc == 0 and data["count"] == 1 and data["status_filter"] is None
    cid = emit1["card"]["card_id"]
    run(capsys, base_args(env, "respond", "--card-id", cid, "--by", "A0", "--note", "答辩"))
    rc, opened = run(capsys, base_args(env, "list", "--status", "open"))
    assert rc == 0 and opened["count"] == 0
    rc, answered = run(capsys, base_args(env, "list", "--status", "answered"))
    assert rc == 0 and answered["count"] == 1
    assert answered["cards"][0]["card_id"] == cid


def test_mutation_dry_run_does_not_write(capsys, env):
    _, emit_data = do_emit(capsys, env)
    cid = emit_data["card"]["card_id"]
    before = env.ledger.read_text(encoding="utf-8")
    rc, data = run(capsys, base_args(env, "respond", "--card-id", cid, "--by", "A0",
                                     "--note", "dry", "--dry-run"))
    assert rc == 0 and data["dry_run"] is True and data["written"] is False
    assert env.ledger.read_text(encoding="utf-8") == before
    # 先真实响应，再干跑结案（非法迁移应被状态机拒绝，属另一用例）
    rc, _ = run(capsys, base_args(env, "respond", "--card-id", cid, "--by", "A0", "--note", "答辩"))
    assert rc == 0
    before = env.ledger.read_text(encoding="utf-8")
    rc, data = run(capsys, base_args(env, "close", "--card-id", cid, "--outcome", "accepted",
                                     "--by", "t", "--note", "dry", "--dry-run"))
    assert rc == 0 and data["dry_run"] is True
    assert env.ledger.read_text(encoding="utf-8") == before


def test_corrupt_ledger_reports_line_number(capsys, env):
    do_emit(capsys, env)
    with open(env.ledger, "a", encoding="utf-8") as fh:
        fh.write("this-is-not-json\n")
    rc, data = run(capsys, base_args(env, "list"))
    assert rc == 2
    assert data["error"]["code"] == "ledger_corrupt"
    assert "第 2 行" in data["error"]["detail"]


def test_no_network_and_no_shell_imports():
    src = (TOOLS_DIR / "zhuge_challenge.py").read_text(encoding="utf-8")
    forbidden = [
        r"^\s*import\s+subprocess\b", r"^\s*from\s+subprocess\b",
        r"^\s*import\s+socket\b", r"^\s*from\s+socket\b",
        r"^\s*import\s+urllib\b", r"^\s*from\s+urllib\b",
        r"^\s*import\s+requests\b", r"^\s*import\s+http\.client\b",
        r"os\.system\s*\(", r"os\.popen\s*\(",
    ]
    for pat in forbidden:
        assert not re.search(pat, src, re.M), "离线纪律违反：命中 %s" % pat


def test_real_sandbox_whitelist_if_present():
    real = Path(zc.DEFAULT_WHITELIST)
    if not real.is_file():
        pytest.skip("本机无 digital-legion-sandbox 白名单")
    loaded = zc.load_whitelist(real)
    report = zc.validate_whitelist(loaded)
    assert report["ok"] is True, report["problems"]
    assert report["parser"] in ("pyyaml", "fallback")
    assert report["ops_allowed"]["ledger_append"] is True


def test_offline_default_ledger_path_is_checkout_local():
    # linked worktree 内默认账本落本 checkout 的 work/，不回落主仓（一单一事）
    root = zc.checkout_root(TOOLS_DIR / "zhuge_challenge.py")
    assert (root / "tools" / "zhuge_challenge.py").is_file()
    assert root == TOOLS_DIR.parent


# ---------------------------------------------------------------------------
# 8. W5 沙盘快照消费（--auto-snapshot，2026-10-27 P1）
# ---------------------------------------------------------------------------
def auto_args(tmp_path, snapshot_dir, strict=False):
    return SimpleNamespace(
        snapshot=None, tasks=None, filter_status=None, auto_snapshot=True,
        snapshot_dir=str(snapshot_dir), strict_sandbox=strict,
        _sandbox_prefix=str(tmp_path))


def make_repo_with_tasks(root: Path, rows=None):
    d = root / ".worktrees"
    d.mkdir(parents=True, exist_ok=True)
    write_json(d / "tasks.json", rows or {
        "T-A": {"desc": "做 A", "status": "doing"},
        "T-B": {"desc": "做 B", "status": "done"},
    })
    return root


def test_auto_snapshot_picks_newest_valid(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    old = write_json(plans / "old_plan.json", SNAPSHOT_ROWS)
    bad = plans / "new_plan.json"
    bad.write_text("{bad-json", encoding="utf-8")
    latest = write_json(plans / "latest.json", [
        {"task_id": "T-Z", "title": "最新快照任务", "status": "doing"}])
    import os
    os.utime(old, (100, 100))
    os.utime(bad, (400, 400))  # 坏文件最新 → 先被扫描并跳过，再命中 latest 有效
    os.utime(latest, (300, 300))
    root = make_repo_with_tasks(tmp_path / "repo")
    snap, meta = zc.load_snapshot(auto_args(tmp_path, plans), root)
    assert snap["source_kind"] == "snapshot_file"
    assert snap["path"].endswith("latest.json")
    assert [it["task_id"] for it in snap["items"]] == ["T-Z"]
    assert meta["auto_snapshot"]["chosen"].endswith("latest.json")
    assert any("跳过" in w for w in meta["auto_snapshot"]["warnings"])


def test_auto_snapshot_missing_dir_falls_back_tasks(tmp_path):
    root = make_repo_with_tasks(tmp_path / "repo")
    snap, meta = zc.load_snapshot(
        auto_args(tmp_path, tmp_path / "no-such-plans"), root)
    assert snap["source_kind"] == "tasks_ledger"
    assert meta["auto_snapshot"]["fallback"] is True
    assert any("目录不存在" in w or "回落 tasks" in w
               for w in meta["auto_snapshot"]["warnings"])


def test_auto_snapshot_empty_skipped_then_falls_back(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    write_json(plans / "empty_plan.json", [])
    root = make_repo_with_tasks(tmp_path / "repo")
    snap, meta = zc.load_snapshot(auto_args(tmp_path, plans), root)
    assert snap["source_kind"] == "tasks_ledger"
    assert meta["auto_snapshot"]["fallback"] is True


def test_auto_snapshot_strict_without_snapshot_rejects(tmp_path):
    root = make_repo_with_tasks(tmp_path / "repo")
    with pytest.raises(zc.WhitelistError):
        zc.load_snapshot(auto_args(tmp_path, tmp_path / "plans", strict=True), root)


def test_auto_snapshot_inside_prefix_strict_ok(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    write_json(plans / "2026-10-27_plan.json", SNAPSHOT_ROWS)
    root = make_repo_with_tasks(tmp_path / "repo")
    snap, meta = zc.load_snapshot(auto_args(tmp_path, plans, strict=True), root)
    assert snap["source_kind"] == "snapshot_file"
    assert snap["inside_sandbox_prefix"] is True


def test_auto_snapshot_cli_smoke(capsys, env):
    plans = env.tmp / "plans"
    plans.mkdir()
    write_json(plans / "2026-10-27_plan.json", SNAPSHOT_ROWS)
    rc, data = run(capsys, base_args(
        env, "snapshot", "--auto-snapshot", "--snapshot-dir", str(plans)))
    assert rc == 0
    assert data["ok"] is True
    assert data["meta"]["auto_snapshot"]["chosen"].endswith("2026-10-27_plan.json")
    assert data["snapshot"]["source_kind"] == "snapshot_file"
