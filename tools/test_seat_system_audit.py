# -*- coding: utf-8 -*-
"""test_seat_system_audit.py — 席位完整性审计工具测试。

全部用 tmp_path 造假 home（monkeypatch 掉三席位 HOMES），绝不读写真实 profile；
真实 profile 仅允许只读（本测试连读都不依赖）。覆盖：完整席位 / 缺文件 / 坏 JSON /
缺 SKILL 卡 / 计数不一致 / 设计内 warning / CLI 退出码与 JSON 形状。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seat_system_audit as ssa

SEATS = ("tianfeng", "zhaoyun", "zhuge")
CHECK_IDS = ["C%d" % idx for idx in range(1, 10)]
DEFAULT_CAPS = {"alpha": "ready", "beta": "ready", "gamma": "pending_wiring"}

SOUL_OK = (
    "# SOUL — 测试席位\n\n## 身份\n\n"
    "测试席位，仅用于审计工具的离线假 home 验证；不指向任何真实 profile，也不具备任何真实权限。\n"
    "本段用于把 SOUL.md 撑到 200 字符以上，覆盖「身份」节检查的正例：文件存在、长度足够、标题含身份。\n"
    "所有文件都在 tmp_path 下生成，测试结束后随 pytest 临时目录一并清理，不触碰生产目录。\n"
    "若这条正例失效，说明 C1 的判据过严或文本长度统计口径有变，应先查审计工具再查数据。\n"
    "本文件不是任何席位的真实 SOUL，只作为测试夹具（fixture）中的静态样本使用。\n"
)
WORKING_OK = (
    "# 工作记忆索引 — 测试席位\n\n## 常读真源\n"
    "- 工单账本：.worktrees/tasks.json\n- 健康卡：python tools/gen_health_card.py\n"
)


# ---------------------------------------------------------------- 假 home 工厂

@pytest.fixture
def homes(tmp_path, monkeypatch):
    """把三席位 home 全部指到 tmp：任何检查都不会落回真实 profile。"""
    roots = {}
    for seat in SEATS:
        home = tmp_path / seat
        home.mkdir()
        monkeypatch.setitem(ssa.HOMES, seat, str(home))
        roots[seat] = home
    return roots


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_json(path, payload):
    return _write(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _skill_card(name):
    return "---\nname: %s\ndescription: 测试技能卡 %s\n---\n\n# %s\n\n仅测试用。\n" % (
        name, name, name)


def _counts(statuses):
    counts = {key: 0 for key in ssa.CAP_STATUSES}
    for status in statuses.values():
        counts[status] += 1
    return counts


def _caps_payload(seat, statuses):
    caps = [{"name": name, "what": "测试能力", "how": "python tools/test_%s.py" % name,
             "boundary": "只读", "status": status} for name, status in statuses.items()]
    counts = _counts(statuses)
    return {
        "schema": ssa.CAP_SCHEMA,
        "generated_at": "2026-10-26T00:00:00+08:00",
        "seat": seat,
        "total": len(caps),
        "ready": counts["ready"],
        "pending_key": counts["pending_key"],
        "pending_wiring": counts["pending_wiring"],
        "capabilities": caps,
        "note": "测试用假清单",
    }


def _seat_card(seat, statuses):
    counts = _counts(statuses)
    return {
        "schema": ssa.CARD_SCHEMA,
        "generated_at": "2026-10-26T00:00:00+08:00",
        "seat": seat,
        "cn_name": "测试",
        "capabilities_summary": {
            "total": len(statuses),
            "ready": counts["ready"],
            "pending_key": counts["pending_key"],
            "pending_wiring": counts["pending_wiring"],
            "ready_names": [name for name, status in statuses.items() if status == "ready"],
            "pending_names": [name for name, status in statuses.items() if status != "ready"],
        },
        "layers": {
            "L1_work": {"what": "会话级运行时"},
            "L2_seat": {"what": "席位层", "duty": "职责：仅做只读审计测试样例。"},
            "L3_org": {"what": "组织层", "refs": [
                {"name": "组织编制真源", "path": "agent_platform/registry/org.json"},
            ]},
        },
    }


def make_complete(homes, seat, statuses=None):
    """造「完整席位」：按各席位设计该有的都有；zhuge 按设计无经验域/工作区/定时任务。"""
    statuses = dict(statuses or DEFAULT_CAPS)
    home = homes[seat]
    _write(home / "SOUL.md", SOUL_OK)
    _write_json(home / "SEAT_CARD.json", _seat_card(seat, statuses))
    _write_json(home / "skills" / "CAPABILITIES.json", _caps_payload(seat, statuses))
    for name in statuses:
        _write(home / "skills" / name / "SKILL.md", _skill_card(name))
    _write(home / "memories" / "WORKING_MEMORY.md", WORKING_OK)
    if seat == "zhaoyun":
        for domain in ssa.EXPERIENCE_DOMAINS:
            _write(home / "experience" / domain / "EXPERIENCE.md",
                   "# 经验 %s\n\n测试条目。\n" % domain)
    elif seat == "tianfeng":
        # 田丰的可读链：赵云写、田丰读
        _write(homes["zhaoyun"] / "experience" / "supervision" / "EXPERIENCE.md",
               "# 经验 supervision\n\n赵云写，田丰读（测试桩）。\n")
    if seat != "zhuge":
        _write_json(home / "webui_state" / "workspaces.json", [
            {"path": str(home), "name": "seat-home"},
            {"path": str(home.parent), "name": "tmp-root"},
        ])
        _write_json(home / "cron" / "jobs.json",
                    {"jobs": [], "updated_at": "2026-10-26T00:00:00+08:00"})
    return home


def _status(report, cid):
    for check in report["checks"]:
        if check["id"] == cid:
            return check["status"]
    raise AssertionError("未找到检查项：%s" % cid)


def _detail(report, cid):
    for check in report["checks"]:
        if check["id"] == cid:
            return check["detail"]
    raise AssertionError("未找到检查项：%s" % cid)


# ---------------------------------------------------------------- 正例：完整席位

def test_tianfeng_complete_all_pass(homes):
    make_complete(homes, "tianfeng")
    report = ssa.audit_seat("tianfeng")
    assert set(report) == {"seat", "home", "ok", "checks", "summary"}
    assert report["seat"] == "tianfeng"
    assert report["home"] == os.path.normpath(str(homes["tianfeng"]))
    assert [check["id"] for check in report["checks"]] == CHECK_IDS
    assert report["summary"] == {"pass": 9, "fail": 0, "warn": 0}
    assert report["ok"] is True
    for check in report["checks"]:
        assert set(check) == {"id", "status", "detail"}
        assert check["status"] in {"pass", "fail", "warn"}
        assert check["status"] == "pass", check
        assert check["detail"]


def test_zhaoyun_complete_all_pass(homes):
    make_complete(homes, "zhaoyun")
    report = ssa.audit_seat("zhaoyun")
    assert report["summary"] == {"pass": 9, "fail": 0, "warn": 0}
    assert report["ok"] is True
    assert "五域" in _detail(report, "C6")


def test_zhuge_by_design_warn_only_and_strict(homes):
    make_complete(homes, "zhuge")
    report = ssa.audit_seat("zhuge")
    statuses = {check["id"]: check["status"] for check in report["checks"]}
    assert statuses == {
        "C1": "pass", "C2": "pass", "C3": "pass", "C4": "pass", "C5": "pass",
        "C6": "warn", "C7": "warn", "C8": "warn", "C9": "pass",
    }
    assert report["summary"] == {"pass": 6, "fail": 0, "warn": 3}
    assert report["ok"] is True
    strict = ssa.audit_seat("zhuge", strict=True)
    assert strict["ok"] is False
    # strict 只改判定口径，不改逐项状态与计数
    assert strict["summary"] == report["summary"]
    assert "按设计" in _detail(report, "C6")


def test_audit_seat_rejects_unknown_seat(homes):
    with pytest.raises(ValueError):
        ssa.audit_seat("default")


# ---------------------------------------------------------------- C1 / C2

def test_c1_soul_failures(homes):
    home = make_complete(homes, "tianfeng")
    (home / "SOUL.md").unlink()
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C1") == "fail"
    assert "SOUL.md" in _detail(report, "C1")

    _write(home / "SOUL.md", "## 身份\n太短")
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C1") == "fail"
    assert "200" in _detail(report, "C1")

    _write(home / "SOUL.md", "# SOUL\n\n" + "没有身份标题但长度足够。" * 30)
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C1") == "fail"
    assert "身份" in _detail(report, "C1")

    _write(home / "SOUL.md", SOUL_OK)
    assert _status(ssa.audit_seat("tianfeng"), "C1") == "pass"


def test_c2_seat_card_failures(homes):
    home = make_complete(homes, "tianfeng")
    card_path = home / "SEAT_CARD.json"

    _write(card_path, "{不是 JSON")
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C2") == "fail"
    assert "JSON" in _detail(report, "C2")
    # 联动：卡读不到时 C9 记 warning
    assert _status(report, "C9") == "warn"

    card = _seat_card("tianfeng", DEFAULT_CAPS)
    card["schema"] = "seat_memory_card/v0"
    _write_json(card_path, card)
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C2") == "fail"
    assert "schema" in _detail(report, "C2")

    card = _seat_card("tianfeng", DEFAULT_CAPS)
    card["seat"] = "zhaoyun"
    _write_json(card_path, card)
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C2") == "fail"
    assert "seat" in _detail(report, "C2")

    card = _seat_card("tianfeng", DEFAULT_CAPS)
    card["layers"]["L2_seat"]["duty"] = "   "
    _write_json(card_path, card)
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C2") == "fail"
    assert "duty" in _detail(report, "C2")

    card = _seat_card("tianfeng", DEFAULT_CAPS)
    card["layers"]["L3_org"]["refs"] = []
    _write_json(card_path, card)
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C2") == "fail"
    assert "refs" in _detail(report, "C2")

    card_path.unlink()
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C2") == "fail"
    assert "不存在" in _detail(report, "C2")


# ---------------------------------------------------------------- C3 / C4

def test_c3_capabilities_failures(homes):
    home = make_complete(homes, "tianfeng")
    caps_path = home / "skills" / "CAPABILITIES.json"

    _write(caps_path, "not json")
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C3") == "fail"
    assert "JSON" in _detail(report, "C3")
    # 清单读不到：C4/C9 只能记 warning（信息不足，不误判为 fail）
    assert _status(report, "C4") == "warn"
    assert _status(report, "C9") == "warn"

    payload = _caps_payload("tianfeng", DEFAULT_CAPS)
    payload["total"] = 4
    _write_json(caps_path, payload)
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C3") == "fail"
    assert "total" in _detail(report, "C3")

    payload = _caps_payload("tianfeng", DEFAULT_CAPS)
    payload["ready"] = 3
    _write_json(caps_path, payload)
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C3") == "fail"
    assert "ready" in _detail(report, "C3")

    payload = _caps_payload("tianfeng", DEFAULT_CAPS)
    payload["schema"] = "seat_capabilities/v0"
    _write_json(caps_path, payload)
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C3") == "fail"
    assert "schema" in _detail(report, "C3")

    payload = _caps_payload("tianfeng", DEFAULT_CAPS)
    del payload["capabilities"][0]["boundary"]
    _write_json(caps_path, payload)
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C3") == "fail"
    assert "boundary" in _detail(report, "C3")

    payload = _caps_payload("tianfeng", DEFAULT_CAPS)
    payload["capabilities"][0]["status"] = "done"
    _write_json(caps_path, payload)
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C3") == "fail"
    assert "status" in _detail(report, "C3")

    payload = _caps_payload("tianfeng", DEFAULT_CAPS)
    payload["capabilities"] = {}
    _write_json(caps_path, payload)
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C3") == "fail"
    assert "capabilities" in _detail(report, "C3")


def test_c4_skill_card_missing_and_malformed(homes):
    home = make_complete(homes, "tianfeng")
    card_path = home / "skills" / "beta" / "SKILL.md"

    card_path.unlink()
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C4") == "fail"
    assert "beta" in _detail(report, "C4")
    assert "缺 1 张" in _detail(report, "C4")

    _write(card_path, "# 没有 front matter 的卡\n")
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C4") == "fail"
    assert "front matter" in _detail(report, "C4")
    assert "beta" in _detail(report, "C4")

    _write(card_path, "---\nname: beta\n---\n\n# beta\n")
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C4") == "fail"
    assert "front matter" in _detail(report, "C4")

    _write(card_path, "---\nname: beta\ndescription:   \n---\n\n# beta\n")
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C4") == "fail"

    _write(card_path, _skill_card("beta"))
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C4") == "pass"
    assert "3/3" in _detail(report, "C4")


# ---------------------------------------------------------------- C5 / C6

def test_c5_working_memory_failures(homes):
    home = make_complete(homes, "tianfeng")
    memory = home / "memories" / "WORKING_MEMORY.md"

    memory.unlink()
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C5") == "fail"
    assert "WORKING_MEMORY.md" in _detail(report, "C5")

    _write(memory, "# 短记忆\n")
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C5") == "fail"
    assert "50" in _detail(report, "C5")

    _write(memory, WORKING_OK)
    assert _status(ssa.audit_seat("tianfeng"), "C5") == "pass"


def test_c6_experience_zhaoyun_five_domains(homes):
    home = make_complete(homes, "zhaoyun")
    missing = home / "experience" / "tooling" / "EXPERIENCE.md"

    missing.unlink()
    report = ssa.audit_seat("zhaoyun")
    assert _status(report, "C6") == "fail"
    assert "tooling" in _detail(report, "C6")

    _write(missing, "# 经验 tooling\n")
    report = ssa.audit_seat("zhaoyun")
    assert _status(report, "C6") == "pass"


def test_c6_experience_tianfeng_readable_chain(homes):
    make_complete(homes, "tianfeng")
    chain = homes["zhaoyun"] / "experience" / "supervision" / "EXPERIENCE.md"

    chain.unlink()
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C6") == "fail"
    assert "可读链" in _detail(report, "C6")

    _write(chain, "# 经验 supervision\n")
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C6") == "pass"


# ---------------------------------------------------------------- C7 / C8 / C9

def test_c7_workspace_variants(homes):
    home = make_complete(homes, "tianfeng")
    ws = home / "webui_state" / "workspaces.json"

    ws.unlink()
    assert _status(ssa.audit_seat("tianfeng"), "C7") == "warn"

    _write(ws, "not json")
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C7") == "fail"
    assert "JSON" in _detail(report, "C7")

    _write_json(ws, {"path": str(home), "name": "x"})
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C7") == "fail"
    assert "list" in _detail(report, "C7")

    _write_json(ws, [{"path": str(home / "missing-dir"), "name": "bad"}])
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C7") == "fail"
    assert "不存在" in _detail(report, "C7")

    _write_json(ws, [{"path": str(home)}])
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C7") == "fail"
    assert "name" in _detail(report, "C7")

    _write_json(ws, [{"path": str(home), "name": "ok"}])
    assert _status(ssa.audit_seat("tianfeng"), "C7") == "pass"


def test_c8_cron_variants(homes):
    home = make_complete(homes, "tianfeng")
    jobs = home / "cron" / "jobs.json"

    jobs.unlink()
    assert _status(ssa.audit_seat("tianfeng"), "C8") == "warn"

    _write(jobs, "{坏 JSON")
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C8") == "fail"
    assert "JSON" in _detail(report, "C8")

    _write_json(jobs, {"jobs": {}})
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C8") == "fail"
    assert "jobs" in _detail(report, "C8")

    _write_json(jobs, {"other": []})
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C8") == "fail"
    assert "jobs" in _detail(report, "C8")

    _write_json(jobs, {"jobs": [{"id": 1}]})
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C8") == "pass"
    assert "1 条" in _detail(report, "C8")


def test_c9_total_mismatch_and_stale_counts(homes):
    home = make_complete(homes, "tianfeng")
    card_path = home / "SEAT_CARD.json"

    card = _seat_card("tianfeng", DEFAULT_CAPS)
    card["capabilities_summary"]["total"] = 5
    _write_json(card_path, card)
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C9") == "fail"
    assert "≠" in _detail(report, "C9")

    # total 一致但 ready 陈旧：按口径记 pass，仅在 detail 附注（不计失败）
    card = _seat_card("tianfeng", DEFAULT_CAPS)
    card["capabilities_summary"]["ready"] = 99
    _write_json(card_path, card)
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C9") == "pass"
    assert "附注" in _detail(report, "C9")

    card_path.unlink()
    report = ssa.audit_seat("tianfeng")
    assert _status(report, "C9") == "warn"


# ---------------------------------------------------------------- HOMES 口径

def test_homes_match_seat_memory_sync():
    """tianfeng/zhaoyun 必须与 tools/seat_memory_sync.py 的 SEATS[*]["home"] 同口径（防漂移）。"""
    import seat_memory_sync as sms

    for seat in ("tianfeng", "zhaoyun"):
        assert os.path.normpath(ssa.HOMES[seat]) == os.path.normpath(sms.SEATS[seat]["home"])
    assert set(ssa.HOMES) == set(SEATS)
    assert os.path.normpath(ssa.HOMES["zhuge"]) == os.path.normpath(
        os.path.join(ssa.REPO, "legion_agents", "zhuge", "workspace"))


def test_repo_root_plain_checkout(tmp_path, monkeypatch):
    """普通 checkout（.git 是目录）：根 = HERE/..，与 seat_memory_sync 口径一致。"""
    plain = tmp_path / "plain"
    (plain / ".git").mkdir(parents=True)
    (plain / "tools").mkdir()
    monkeypatch.setattr(ssa, "HERE", str(plain / "tools"))
    assert ssa._repo_root() == os.path.normpath(str(plain))


def test_repo_root_linked_worktree_returns_main_repo(tmp_path, monkeypatch):
    """linked worktree（.git 是文本文件）：根回到主工作树，保证 zhuge 真实席位可被审计。"""
    main = tmp_path / "main"
    gitdir = main / ".git" / "worktrees" / "K-TEST"
    gitdir.mkdir(parents=True)
    worktree = tmp_path / "wt"
    (worktree / "tools").mkdir(parents=True)
    (worktree / ".git").write_text(
        "gitdir: %s\n" % gitdir.as_posix(), encoding="utf-8")
    monkeypatch.setattr(ssa, "HERE", str(worktree / "tools"))
    assert ssa._repo_root() == os.path.normpath(str(main))


# ---------------------------------------------------------------- CLI

def test_cli_json_single_and_all(homes, capsys):
    for seat in SEATS:
        make_complete(homes, seat)

    rc = ssa.main(["--seat", "tianfeng", "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc == 0
    assert isinstance(payload, dict)
    assert set(payload) == {"seat", "home", "ok", "checks", "summary"}
    assert payload["seat"] == "tianfeng"
    assert payload["ok"] is True
    assert os.path.normpath(payload["home"]) == os.path.normpath(str(homes["tianfeng"]))
    assert len(payload["checks"]) == 9
    assert payload["summary"] == {"pass": 9, "fail": 0, "warn": 0}

    for argv in (["--seat", "all", "--json"], ["--json"]):
        rc = ssa.main(argv)
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert rc == 0
        assert isinstance(payload, list)
        assert [item["seat"] for item in payload] == list(SEATS)


def test_cli_exit_codes(homes, capsys):
    for seat in SEATS:
        make_complete(homes, seat)

    # 全过（zhuge 有设计内 warning，非 strict 仍 0）
    assert ssa.main(["--seat", "all"]) == 0
    capsys.readouterr()

    # 单席位 fail → 1；all 中有 fail → 1
    (homes["tianfeng"] / "SOUL.md").unlink()
    assert ssa.main(["--seat", "tianfeng"]) == 1
    assert "FAIL" in capsys.readouterr().out
    assert ssa.main(["--seat", "all"]) == 1
    capsys.readouterr()

    # 只有 warning：非 strict 0，strict 1
    assert ssa.main(["--seat", "zhuge"]) == 0
    capsys.readouterr()
    assert ssa.main(["--seat", "zhuge", "--strict"]) == 1
    out = capsys.readouterr().out
    assert "--strict" in out

    # 用法错误 → 2（argparse）
    with pytest.raises(SystemExit) as exc:
        ssa.main(["--seat", "bogus"])
    assert exc.value.code == 2


def test_cli_strict_json_ok_flag(homes, capsys):
    make_complete(homes, "zhuge")

    assert ssa.main(["--seat", "zhuge", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["summary"]["warn"] == 3

    assert ssa.main(["--seat", "zhuge", "--json", "--strict"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["summary"]["warn"] == 3


def test_cli_human_summary_line_count(homes, capsys):
    for seat in SEATS:
        make_complete(homes, seat)

    # 单席位全过：3-5 行
    assert ssa.main(["--seat", "tianfeng"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert 3 <= len(lines) <= 5, lines
    assert "tianfeng" in lines[0]
    assert "PASS" in lines[1]

    # all：3 席位各 1 行 + 汇总，正好 5 行
    assert ssa.main([]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert 3 <= len(lines) <= 5, lines
    text = "\n".join(lines)
    for seat in SEATS:
        assert "[%s]" % seat in text
    assert "汇总" in lines[-1]

    # 多失败席位：失败明细折叠，仍 3-5 行
    (homes["tianfeng"] / "SOUL.md").unlink()
    (homes["tianfeng"] / "memories" / "WORKING_MEMORY.md").unlink()
    (homes["tianfeng"] / "SEAT_CARD.json").unlink()
    assert ssa.main(["--seat", "tianfeng"]) == 1
    lines = capsys.readouterr().out.strip().splitlines()
    assert 3 <= len(lines) <= 5, lines
    assert "FAIL" in "\n".join(lines)
