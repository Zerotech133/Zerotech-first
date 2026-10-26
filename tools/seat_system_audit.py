# -*- coding: utf-8 -*-
"""seat_system_audit.py — 席位系统完整性审计（只读，2026-10-26）。

对标赵云系统的完整度，给三席位（tianfeng/zhaoyun/zhuge）做逐项只读体检：
人格 / 席位卡 / 能力清单 / 技能卡 / 工作记忆 / 经验链 / 工作区 / 定时任务 /
卡-清单一致性，产出可机读 JSON 与 3-5 行人读摘要。

席位目录口径（与 tools/seat_memory_sync.py 的 SEATS.home、tools/seat_capability_inject.py
的 HOMES 同口径；由单测交叉断言防漂移）：
  tianfeng / zhaoyun = ~/.hermes/profiles/<seat>
  zhuge              = <repo>/legion_agents/zhuge/workspace
  （<repo> 取主工作树根：legion_agents/ 是主仓运行时目录、不入 git 跟踪，在 worktree 内
   跑审计时自动回到主线仓，否则诸葛亮会被误判成空壳。）

检查项（id）：
  C1 soul        SOUL.md 存在且 >=200 字符、含「身份」节
  C2 seat_card   SEAT_CARD.json 合法 JSON、schema=seat_memory_card/v1.1、seat 一致、
                 L2_seat.duty 非空、L3_org.refs 非空
  C3 capabilities skills/CAPABILITIES.json 合法、schema=seat_capabilities/v1、
                 total==len(capabilities)、ready/pending 计数与逐项 status 一致、
                 每项含 name/what/how/boundary/status 且 status 合法
  C4 skill_cards 每个 capability 在 skills/<name>/SKILL.md 有 YAML front matter（name:/description:）
  C5 working_memory memories/WORKING_MEMORY.md 存在且 >=50 字符
  C6 experience  zhaoyun 要求 experience/ 五域各含 EXPERIENCE.md；tianfeng 要求可读链
                 （zhaoyun 的 experience/supervision/EXPERIENCE.md）存在；zhuge 记 warning（按设计无经验域）
  C7 workspace   webui_state/workspaces.json 若存在须为 path 存在的 list[{path,name}]；不存在记 warning
  C8 cron        cron/jobs.json 若存在须合法 JSON 且含 jobs 列表；不存在记 warning
  C9 seat_card_capabilities_一致  SEAT_CARD.capabilities_summary.total == C3 的 total
                 （读不到任一侧记 warning；total 一致但 ready/pending 字段陈旧时仅在 detail 附注，不计失败）

状态语义：pass 通过 / fail 失败 / warn 警告（设计内缺失或信息不足，不阻断）。
--strict 时 warning 也计入失败（ok=False、退出码 1）。

严格只读：不写任何 profile、不启服务、不联网。

用法：
    python -X utf8 tools/seat_system_audit.py                      # 三席位人读摘要
    python -X utf8 tools/seat_system_audit.py --seat zhaoyun --json
    python -X utf8 tools/seat_system_audit.py --seat all --json --strict
退出码：0=全过（--strict 下 warning 也算失败），1=有 fail，2=用法错误。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _repo_root() -> str:
    """主工作树根（linked worktree 内也指回主线仓库）。

    zhuge 席位目录 legion_agents/ 是主仓运行时目录、未入 git 跟踪，git worktree 不会带出；
    若只用本文件相对根，从 worktree 跑审计会把诸葛亮误判成空壳。故：
      - 普通 checkout（.git 是目录）：根 = HERE/..，与 seat_memory_sync 口径完全一致；
      - linked worktree（.git 是文本文件 "gitdir: <主仓>/.git/worktrees/<名>"）：根 = 主工作树。
    """
    root = os.path.normpath(os.path.join(HERE, ".."))
    git_entry = os.path.join(root, ".git")
    if os.path.isdir(git_entry) or not os.path.isfile(git_entry):
        return root
    try:
        with open(git_entry, "r", encoding="utf-8") as fh:
            first = fh.readline().strip()
    except (OSError, UnicodeError):
        return root
    if not first.startswith("gitdir:"):
        return root
    gitdir = first.split(":", 1)[1].strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(root, gitdir)
    main_root = os.path.normpath(os.path.join(gitdir, "..", "..", ".."))
    return main_root if os.path.isdir(main_root) else root


REPO = _repo_root()

SEAT_ORDER = ("tianfeng", "zhaoyun", "zhuge")
# 与 seat_memory_sync.SEATS[*]["home"] / seat_capability_inject.HOMES 同口径（单测交叉断言）
HOMES = {
    "tianfeng": os.path.normpath(os.path.expanduser("~/.hermes/profiles/tianfeng")),
    "zhaoyun": os.path.normpath(os.path.expanduser("~/.hermes/profiles/zhaoyun")),
    "zhuge": os.path.normpath(os.path.join(REPO, "legion_agents", "zhuge", "workspace")),
}

CARD_SCHEMA = "seat_memory_card/v1.1"
CAP_SCHEMA = "seat_capabilities/v1"
CAP_STATUSES = ("ready", "pending_key", "pending_wiring")
CAP_FIELDS = ("name", "what", "how", "boundary", "status")
EXPERIENCE_DOMAINS = ("execution", "riskguard", "stock_screen", "supervision", "tooling")
TIANFENG_CHAIN_SEAT = "zhaoyun"
TIANFENG_CHAIN_REL = os.path.join("experience", "supervision", "EXPERIENCE.md")


# ---------------------------------------------------------------- 基础设施

def _check(cid: str, status: str, detail: str) -> dict:
    return {"id": cid, "status": status, "detail": detail}


def _dig(obj, *keys):
    """安全取嵌套 dict 值：任何一层不是 dict 都返回 None（不抛异常）。"""
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _read_text(path: str):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read(), None
    except FileNotFoundError:
        return None, "文件不存在"
    except (OSError, UnicodeError) as exc:
        return None, "读取失败（%s）" % exc


def _load_json(path: str):
    text, err = _read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, "JSON 非法（%s）" % exc


def _load_capabilities(home: str):
    return _load_json(os.path.join(home, "skills", "CAPABILITIES.json"))


def _seat_home(seat: str) -> str:
    return os.path.normpath(HOMES[seat])


def _has_heading(text: str, keyword: str) -> bool:
    pattern = r"^#{1,6}\s*[^\n]*%s" % re.escape(keyword)
    return re.search(pattern, text, re.MULTILINE) is not None


def _front_matter(text: str):
    """解析 SKILL.md 开头的 YAML front matter（只认顶层 key: value，够校验 name/description）。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for idx in range(1, len(lines)):
        if lines[idx].strip() != "---":
            continue
        fields = {}
        for line in lines[1:idx]:
            if not line.strip() or line[0].isspace() or ":" not in line:
                continue
            key, _sep, value = line.partition(":")
            fields[key.strip()] = value.strip()
        return fields
    return None


def _cap_counts(caps: list) -> dict:
    counts = {status: 0 for status in CAP_STATUSES}
    for item in caps:
        status = item.get("status") if isinstance(item, dict) else None
        if status in counts:
            counts[status] += 1
    return counts


def _join_limited(items: list, limit: int = 6, sep: str = "；") -> str:
    shown = items[:limit]
    text = sep.join(shown)
    if len(items) > limit:
        text += "%s…等 %d 项" % (sep, len(items))
    return text


# ---------------------------------------------------------------- C1-C9

def check_c1_soul(seat: str, home: str) -> dict:
    path = os.path.join(home, "SOUL.md")
    text, err = _read_text(path)
    if err:
        return _check("C1", "fail", "SOUL.md %s" % err)
    body = text.strip()
    problems = []
    if len(body) < 200:
        problems.append("仅 %d 字符（<200）" % len(body))
    if not _has_heading(body, "身份"):
        problems.append("缺「身份」节")
    if problems:
        return _check("C1", "fail", "SOUL.md " + "；".join(problems))
    return _check("C1", "pass", "SOUL.md %d 字符，含「身份」节" % len(body))


def check_c2_seat_card(seat: str, home: str) -> dict:
    card, err = _load_json(os.path.join(home, "SEAT_CARD.json"))
    if err:
        return _check("C2", "fail", "SEAT_CARD.json %s" % err)
    if not isinstance(card, dict):
        return _check("C2", "fail", "SEAT_CARD.json 顶层不是对象")
    problems = []
    if card.get("schema") != CARD_SCHEMA:
        problems.append("schema=%r（应 %s）" % (card.get("schema"), CARD_SCHEMA))
    if card.get("seat") != seat:
        problems.append("seat=%r（应 %r）" % (card.get("seat"), seat))
    duty = _dig(card, "layers", "L2_seat", "duty")
    if not isinstance(duty, str) or not duty.strip():
        problems.append("L2_seat.duty 为空")
    refs = _dig(card, "layers", "L3_org", "refs")
    if not isinstance(refs, list) or not refs:
        problems.append("L3_org.refs 为空")
    if problems:
        return _check("C2", "fail", "SEAT_CARD.json " + "；".join(problems))
    return _check("C2", "pass",
                  "schema=%s；seat=%s；L2 duty %d 字符；L3 refs %d 条"
                  % (CARD_SCHEMA, seat, len(duty.strip()), len(refs)))


def check_c3_capabilities(seat: str, home: str) -> dict:
    data, err = _load_capabilities(home)
    if err:
        return _check("C3", "fail", "skills/CAPABILITIES.json %s" % err)
    if not isinstance(data, dict):
        return _check("C3", "fail", "skills/CAPABILITIES.json 顶层不是对象")
    problems = []
    if data.get("schema") != CAP_SCHEMA:
        problems.append("schema=%r（应 %s）" % (data.get("schema"), CAP_SCHEMA))
    caps = data.get("capabilities")
    if not isinstance(caps, list):
        problems.append("capabilities 不是 list")
        caps = []
    if data.get("total") != len(caps):
        problems.append("total=%r ≠ len(capabilities)=%d" % (data.get("total"), len(caps)))
    counts = _cap_counts(caps)
    for key in CAP_STATUSES:
        got = data.get(key)
        if not isinstance(got, int):
            problems.append("缺计数字段 %s（逐项统计 %d）" % (key, counts[key]))
        elif got != counts[key]:
            problems.append("%s=%d ≠ 逐项统计 %d" % (key, got, counts[key]))
    for idx, item in enumerate(caps, 1):
        if not isinstance(item, dict):
            problems.append("第 %d 项不是对象" % idx)
            continue
        for field in CAP_FIELDS:
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                problems.append("第 %d 项缺 %s" % (idx, field))
        status = item.get("status")
        if isinstance(status, str) and status.strip() and status not in CAP_STATUSES:
            problems.append("第 %d 项 status=%r 非法" % (idx, status))
    if problems:
        return _check("C3", "fail",
                      "skills/CAPABILITIES.json " + _join_limited(problems))
    return _check("C3", "pass",
                  "schema ok；total=%d；ready=%d/pending_key=%d/pending_wiring=%d 与逐项一致"
                  % (len(caps), counts["ready"], counts["pending_key"], counts["pending_wiring"]))


def check_c4_skill_cards(seat: str, home: str) -> dict:
    data, err = _load_capabilities(home)
    if err:
        return _check("C4", "warn", "能力清单不可读（%s），无法核验技能卡" % err)
    caps = _dig(data, "capabilities")
    if not isinstance(caps, list):
        return _check("C4", "warn", "能力清单无 capabilities 列表，无法核验技能卡")
    names, unresolved = [], 0
    for item in caps:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
        else:
            unresolved += 1
    missing, malformed = [], []
    for name in names:
        text, err = _read_text(os.path.join(home, "skills", name, "SKILL.md"))
        if err:
            missing.append(name)
            continue
        fields = _front_matter(text)
        if fields is None or not fields.get("name") or not fields.get("description"):
            malformed.append(name)
    if missing or malformed or unresolved:
        parts = []
        if missing:
            parts.append("缺 %d 张技能卡：%s" % (len(missing), "、".join(missing)))
        if malformed:
            parts.append("front matter 不合规 %d 张（需 name:/description:）：%s"
                         % (len(malformed), "、".join(malformed)))
        if unresolved:
            parts.append("%d 项能力缺 name，无法定位技能卡" % unresolved)
        return _check("C4", "fail", "；".join(parts))
    if not caps:
        return _check("C4", "pass", "能力清单为空（0 项），无需技能卡")
    return _check("C4", "pass", "%d/%d 项能力均有合规技能卡（skills/<name>/SKILL.md）"
                  % (len(names), len(caps)))


def check_c5_working_memory(seat: str, home: str) -> dict:
    text, err = _read_text(os.path.join(home, "memories", "WORKING_MEMORY.md"))
    if err:
        return _check("C5", "fail", "memories/WORKING_MEMORY.md %s" % err)
    length = len(text.strip())
    if length < 50:
        return _check("C5", "fail", "memories/WORKING_MEMORY.md 仅 %d 字符（<50）" % length)
    return _check("C5", "pass", "memories/WORKING_MEMORY.md %d 字符" % length)


def check_c6_experience(seat: str, home: str) -> dict:
    if seat == "zhuge":
        return _check("C6", "warn", "无经验域（建议面按设计不设 Z4 经验库）")
    if seat == "zhaoyun":
        missing = [domain for domain in EXPERIENCE_DOMAINS
                   if not os.path.isfile(os.path.join(home, "experience", domain, "EXPERIENCE.md"))]
        if missing:
            return _check("C6", "fail",
                          "experience/ 缺 %d 个域的 EXPERIENCE.md：%s"
                          % (len(missing), "、".join(missing)))
        return _check("C6", "pass", "experience/ 五域 EXPERIENCE.md 齐备（%s）"
                      % "/".join(EXPERIENCE_DOMAINS))
    # tianfeng：只读链——赵云写、田丰读
    chain = os.path.normpath(os.path.join(_seat_home(TIANFENG_CHAIN_SEAT), TIANFENG_CHAIN_REL))
    try:
        with open(chain, "rb") as fh:
            fh.read(1)
    except FileNotFoundError:
        return _check("C6", "fail",
                      "可读链断：赵云的 experience/supervision/EXPERIENCE.md 不存在（%s）" % chain)
    except OSError as exc:
        return _check("C6", "fail", "可读链不可读：%s（%s）" % (chain, exc))
    return _check("C6", "pass", "可读链存在：赵云 experience/supervision/EXPERIENCE.md（%d 字节）"
                  % os.path.getsize(chain))


def check_c7_workspace(seat: str, home: str) -> dict:
    path = os.path.join(home, "webui_state", "workspaces.json")
    if not os.path.isfile(path):
        return _check("C7", "warn", "webui_state/workspaces.json 不存在（未登记工作区；非阻塞）")
    data, err = _load_json(path)
    if err:
        return _check("C7", "fail", "webui_state/workspaces.json %s" % err)
    if not isinstance(data, list):
        return _check("C7", "fail", "webui_state/workspaces.json 顶层应为 list，实为 %s"
                      % type(data).__name__)
    problems, checked = [], 0
    for idx, item in enumerate(data, 1):
        if not isinstance(item, dict):
            problems.append("第 %d 项不是对象" % idx)
            continue
        name = item.get("name")
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            problems.append("第 %d 项缺 path" % idx)
            continue
        if not isinstance(name, str) or not name.strip():
            problems.append("第 %d 项缺 name" % idx)
        resolved = os.path.expanduser(raw_path.strip())
        if os.path.exists(resolved):
            checked += 1
        else:
            problems.append("第 %d 项 path 不存在：%s" % (idx, resolved))
    if problems:
        return _check("C7", "fail", "webui_state/workspaces.json " + _join_limited(problems))
    return _check("C7", "pass", "webui_state/workspaces.json %d 节点均合法且 path 存在" % checked)


def check_c8_cron(seat: str, home: str) -> dict:
    path = os.path.join(home, "cron", "jobs.json")
    if not os.path.isfile(path):
        return _check("C8", "warn", "cron/jobs.json 不存在（无定时任务；非阻塞）")
    data, err = _load_json(path)
    if err:
        return _check("C8", "fail", "cron/jobs.json %s" % err)
    if not isinstance(data, dict):
        return _check("C8", "fail", "cron/jobs.json 顶层不是对象")
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        return _check("C8", "fail", "cron/jobs.json 缺 jobs 列表")
    return _check("C8", "pass", "cron/jobs.json 合法，jobs %d 条" % len(jobs))


def check_c9_card_caps_consistency(seat: str, home: str) -> dict:
    card, card_err = _load_json(os.path.join(home, "SEAT_CARD.json"))
    caps, caps_err = _load_capabilities(home)
    if card_err and caps_err:
        return _check("C9", "warn", "SEAT_CARD.json 与 CAPABILITIES.json 均不可用，无法比对")
    if card_err:
        return _check("C9", "warn", "SEAT_CARD.json %s，无法比对" % card_err)
    if caps_err:
        return _check("C9", "warn", "CAPABILITIES.json %s，无法比对" % caps_err)
    card_total = _dig(card, "capabilities_summary", "total")
    cap_total = _dig(caps, "total")
    if not isinstance(card_total, int) or not isinstance(cap_total, int):
        return _check("C9", "warn", "摘要 total 缺失（card=%r / capabilities=%r），无法比对"
                      % (card_total, cap_total))
    if card_total != cap_total:
        return _check("C9", "fail",
                      "SEAT_CARD.capabilities_summary.total=%d ≠ CAPABILITIES.total=%d"
                      % (card_total, cap_total))
    detail = "total 一致：%d" % cap_total
    stale = []
    for key in CAP_STATUSES:
        card_value = _dig(card, "capabilities_summary", key)
        cap_value = _dig(caps, key)
        if isinstance(card_value, int) and isinstance(cap_value, int) and card_value != cap_value:
            stale.append("%s 卡=%d/清单=%d" % (key, card_value, cap_value))
    if stale:
        detail += "；附注（不计失败）：卡内计数陈旧（%s）" % "，".join(stale)
    return _check("C9", "pass", detail)


CHECKS = (
    check_c1_soul,
    check_c2_seat_card,
    check_c3_capabilities,
    check_c4_skill_cards,
    check_c5_working_memory,
    check_c6_experience,
    check_c7_workspace,
    check_c8_cron,
    check_c9_card_caps_consistency,
)


# ---------------------------------------------------------------- 汇总

def summarize(checks: list) -> dict:
    summary = {"pass": 0, "fail": 0, "warn": 0}
    for check in checks:
        summary[check["status"]] += 1
    return summary


def audit_seat(seat: str, home: str | None = None, strict: bool = False) -> dict:
    """审计单个席位，返回 {seat, home, ok, checks, summary}。

    ok = 当前口径下的判定：有 fail 即 False；--strict 时存在 warn 也 False。
    """
    if seat not in HOMES:
        raise ValueError("未知席位：%r（可选 %s）" % (seat, "/".join(SEAT_ORDER)))
    home = os.path.normpath(home) if home else _seat_home(seat)
    checks = [run(seat, home) for run in CHECKS]
    summary = summarize(checks)
    ok = summary["fail"] == 0 and (not strict or summary["warn"] == 0)
    return {"seat": seat, "home": home, "ok": ok, "checks": checks, "summary": summary}


def _short(text, limit: int = 180) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "…"


def format_human(reports: list, strict: bool = False) -> str:
    """3-5 行人读摘要。"""
    if len(reports) == 1:
        report = reports[0]
        summary = report["summary"]
        lines = [
            "席位 %s 审计（home: %s）" % (report["seat"], report["home"]),
            "pass %d / fail %d / warn %d -> %s%s" % (
                summary["pass"], summary["fail"], summary["warn"],
                "PASS" if report["ok"] else "FAIL",
                "（--strict：warning 计入失败）" if strict else ""),
        ]
        issues = [check for check in report["checks"] if check["status"] != "pass"]
        if not issues:
            lines.append("结论：C1-C9 全部通过")
        else:
            for check in issues[:2]:
                label = "失败" if check["status"] == "fail" else "警告"
                lines.append("%s %s：%s" % (label, check["id"], _short(check["detail"])))
            rest = issues[2:]
            if rest:
                lines.append("另有 %d 项未过：%s" % (len(rest), "、".join(c["id"] for c in rest)))
        return "\n".join(lines)

    lines = ["席位完整性审计（只读，%d 席位）%s"
             % (len(reports), "；--strict：warning 计入失败" if strict else "")]
    for report in reports:
        summary = report["summary"]
        marks = []
        fails = [c["id"] for c in report["checks"] if c["status"] == "fail"]
        warns = [c["id"] for c in report["checks"] if c["status"] == "warn"]
        if fails:
            marks.append("fail: " + ",".join(fails))
        if warns:
            marks.append("warn: " + ",".join(warns))
        lines.append("[%s] pass %d / fail %d / warn %d -> %s%s"
                     % (report["seat"], summary["pass"], summary["fail"], summary["warn"],
                        "PASS" if report["ok"] else "FAIL",
                        "（%s）" % "；".join(marks) if marks else ""))
    total_fail = sum(len([c for c in r["checks"] if c["status"] == "fail"]) for r in reports)
    total_warn = sum(len([c for c in r["checks"] if c["status"] == "warn"]) for r in reports)
    passed = sum(1 for report in reports if report["ok"])
    lines.append("汇总：%d/%d 席位全过；失败 %d 项、警告 %d 项 -> 退出码 %d"
                 % (passed, len(reports), total_fail, total_warn,
                    0 if passed == len(reports) else 1))
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seat_system_audit.py",
        description="席位系统完整性审计（只读）：逐项 C1-C9 体检三席位 profile/workspace。",
        epilog="退出码：0=全过（--strict 下 warning 也算失败），1=有 fail，2=用法错误。",
    )
    parser.add_argument("--seat", default="all", choices=list(SEAT_ORDER) + ["all"],
                        help="审计哪个席位（默认 all = tianfeng/zhaoyun/zhuge）")
    parser.add_argument("--json", action="store_true",
                        help="输出 JSON（单席位为对象；all 为对象数组）")
    parser.add_argument("--strict", action="store_true",
                        help="warning 也算失败（ok=False、退出码 1）")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    _configure_stdout()
    seats = list(SEAT_ORDER) if args.seat == "all" else [args.seat]
    reports = [audit_seat(seat, strict=args.strict) for seat in seats]
    if args.json:
        payload = reports[0] if len(reports) == 1 else reports
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_human(reports, strict=args.strict))
    return 0 if all(report["ok"] for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
