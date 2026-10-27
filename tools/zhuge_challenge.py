# -*- coding: utf-8 -*-
"""zhuge_challenge.py — 诸葛亮质询通道离线 MVP（WO-LEGION-ZHUGE-CHALLENGE-20261027 / C1）。

一句话：把「田丰计划快照 → 诸葛亮反向质询 → 质询卡 → 田丰响应/整改 → 闭环」
这一段，先做成**离线、可跑、可测**的内存/文件级状态机；不接 Kanban、不连网、不排程、
不写沙盘仓与执行仓，零执行权靠「只有质询卡账本一条写通道」保证。

设计出处
--------
- `docs/legion/2026-10-18_田丰与诸葛亮双profile设计稿.md` §4 零执行权隔离三件套、§5 闭环流程
- `docs/legion/2026-10-18_诸葛亮沙盘仓创建回执.md` §1-§6（沙盘仓/白名单/质询卡模板）
- `C:\\项目\\digital-legion-sandbox\\openclaw_readonly_whitelist.yaml`（三件套②，只读加载）
- `C:\\项目\\digital-legion-sandbox\\zhuge_query_card_template.md`（三件套③卡字段占位口径）
- `docs/legion/2026-10-27_诸葛质询通道离线MVP.md`（本 MVP 设计/契约/边界/差异）

MVP 边界（写死）
----------------
1. **离线**：不 import 任何网络/进程库（无 subprocess/socket/urllib/requests/http）；
   快照只来自本地文件（`--snapshot <path>` 或 `.worktrees/tasks.json` 过滤 doing）。
2. **不写沙盘仓、不写执行仓**：唯一写通道 = `work/zhuge_challenge/cards.jsonl`
   （质询卡账本，追加式，作为 Kanban board `zhugeliang-review` 的离线替身）。
3. **不接 Kanban、不排程、不接真模型**：定时化属 L2；真模型质询生成属 LLM 接线，
   本 MVP 的 ref_questions 由确定性规则模板生成（离线可复现）。
4. **白名单 fail-closed**：emit/respond/close/snapshot/list/show 均先加载三件套②白名单，
   校验 allow/deny 与唯一写点约束；不通过即拒绝，不产生任何写。
5. **账本追加式**：事件只追加不改写；状态由事件折叠（fold）得出，非法迁移拒绝并留因
   （追加 `rejected` 事件），历史可回溯。

状态机
------
    (emit) → open ──respond──→ answered ──close(accepted|rejected)──→ closed
      │                            │
      └──── 非法迁移（如重复 close / 对 closed 再 respond）→ 拒绝 + rejected 事件留因
    幂等：同一快照同日重复 emit → 返回同一 card_id，不重复追加；
          respond/close 同 payload 重复提交 → 幂等返回，不重复追加。

数据契约（JSONL 每行一个事件）
------------------------------
    created   {"event":"created","card_id":..., "at":..., "card":{card_schema...}}
    answered  {"event":"answered","card_id":..., "at":..., "by":..., "note":...,
               "verdict":"respond|fix", "response_key":...}
    closed    {"event":"closed","card_id":..., "at":..., "by":..., "note":...,
               "outcome":"accepted|rejected", "close_key":...}
    rejected  {"event":"rejected","card_id":..., "at":..., "op":"respond|close|emit",
               "reason":..., "attempted":{...}}

退出码
------
    0 = 成功；1 = IO/未预期错误；2 = 校验拒绝（白名单/状态机/空快照/账本损坏）；
    3 = 输入错误（坏 JSON/参数非法/卡不存在）。

用法（离线）
------------
    python tools\\zhuge_challenge.py check --json
    python tools\\zhuge_challenge.py snapshot --json
    python tools\\zhuge_challenge.py snapshot --snapshot C:\\path\\plan.json --filter-status doing --json
    python tools\\zhuge_challenge.py emit --snapshot C:\\path\\plan.json --json
    python tools\\zhuge_challenge.py emit --json                 # 默认读 .worktrees/tasks.json 的 doing
    python tools\\zhuge_challenge.py list --status open --json
    python tools\\zhuge_challenge.py show --card-id ZQ-20261027-01 --json
    python tools\\zhuge_challenge.py respond --card-id ZQ-20261027-01 --by 田丰 --note "已答辩" --json
    python tools\\zhuge_challenge.py close --card-id ZQ-20261027-01 --outcome accepted --by 老田 --note "验收" --json
    python tools\\zhuge_challenge.py emit --dry-run --json      # 只算不写
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量与契约
# ---------------------------------------------------------------------------
SCHEMA_TOOL = "zhuge_challenge/v1"
SCHEMA_CARD = "zhuge_challenge_card/v1"
SCHEMA_SNAPSHOT = "zhuge_challenge_snapshot/v1"

BOARD = "zhugeliang-review"          # 三件套③唯一回写看板
CARD_TYPE = "challenge"              # 唯一卡片类型
VERDICT_REQUEST = "respond_or_fix"   # 每卡必须响应或整改，不得静默
DEFAULT_SEVERITY = "medium"
SEVERITIES = ("info", "low", "medium", "high", "critical")
STATUSES = ("open", "answered", "closed")
DECISIONS = ("accepted", "rejected")
VERDICTS = ("respond", "fix")

# 三件套②白名单默认路径（只读；可用 --whitelist 覆盖）
DEFAULT_WHITELIST = r"C:\项目\digital-legion-sandbox\openclaw_readonly_whitelist.yaml"
SANDBOX_PREFIX_FALLBACK = "C:/项目/digital-legion-sandbox"

# W5 沙盘快照消费（2026-10-27 P1）：默认扫描 <sandbox>/plans/ 下最新可用快照；
# 无可用快照时回落 tasks 账本（显式 warning，不静默）。文件由田丰侧按契约推送。
SANDBOX_PLANS_DIRNAME = "plans"
SANDBOX_SNAPSHOT_GLOBS = ("*_plan.json", "plan_*.json", "latest.json")
AUTO_SNAPSHOT_MAX_CANDIDATES = 20

# 本工具所需的工具面（与白名单 allow 名对齐）
REQUIRED_ALLOW = ("file_read", "kanban_read", "kanban_card_write")
REQUIRED_DENY = ("shell_exec", "file_write", "paperclip_api_write", "hermes_control", "network_egress")
OP_TOOL = {
    "snapshot_read": "file_read",
    "ledger_read": "kanban_read",
    "ledger_append": "kanban_card_write",
}

# 账本默认落点：checkout 根（linked worktree 内则落 worktree 的 work/，不污染主仓）
DEFAULT_LEDGER_REL = Path("work") / "zhuge_challenge" / "cards.jsonl"
DEFAULT_MAX_QUESTIONS = 12
MAX_QUESTIONS_HARD_CAP = 60

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CARD_ID_RE = re.compile(r"^ZQ-\d{8}-\d{2}$")
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


# ---------------------------------------------------------------------------
# 错误类型（带退出码，main 统一兜底）
# ---------------------------------------------------------------------------
class ZhugeError(Exception):
    exit_code = 1
    code = "error"

    def __init__(self, detail: str, code: str | None = None, exit_code: int | None = None):
        super().__init__(detail)
        self.detail = str(detail)
        if code:
            self.code = code
        if exit_code is not None:
            self.exit_code = exit_code


class WhitelistError(ZhugeError):
    code = "whitelist_reject"
    exit_code = 2


class SnapshotError(ZhugeError):
    code = "bad_snapshot"
    exit_code = 3


class EmptySnapshotError(SnapshotError):
    code = "empty_snapshot"
    exit_code = 2


class TransitionError(ZhugeError):
    code = "illegal_transition"
    exit_code = 2


class LedgerError(ZhugeError):
    code = "ledger_corrupt"
    exit_code = 2


class InputError(ZhugeError):
    code = "bad_input"
    exit_code = 3


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today_str() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(obj) -> str:
    if not isinstance(obj, (str, bytes)):
        obj = _canonical(obj)
    if isinstance(obj, str):
        obj = obj.encode("utf-8")
    return hashlib.sha256(obj).hexdigest()


def _short_sha(hexdigest: str) -> str:
    return hexdigest[:16]


def _strip_quotes(text: str) -> str:
    text = (text or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _strip_inline_comment(text: str) -> str:
    """去掉 YAML 行尾 `  # 注释`（保留值内部的 # 场景：仅空格后的 # 视为注释）。"""
    text = (text or "").rstrip()
    pos = text.find(" #")
    if pos >= 0:
        return text[:pos].rstrip()
    if text.startswith("#"):
        return ""
    return text


def _normcase(p) -> str:
    try:
        return os.path.normcase(str(Path(p).resolve()))
    except OSError:
        return os.path.normcase(str(p))


def path_inside(path, prefix) -> bool:
    """path 是否位于 prefix 目录内（Windows 大小写不敏感；不存在也按字面判断）。"""
    if not prefix:
        return False
    p = _normcase(path)
    base = _normcase(prefix)
    if p == base:
        return True
    if not base.endswith(os.sep):
        base = base + os.sep
    return p.startswith(base)


def checkout_root(start=None) -> Path:
    """本 checkout 根（linked worktree 内 = worktree 根，不回落主仓）。

    与 `tools/_repo_paths.repo_root()`（回落主仓）刻意不同：质询账本是**工单内运行时产物**，
    必须留在本 checkout 的 work/ 下，避免写进主仓工作树（AGENTS §3 一单一事）。
    """
    p = Path(start or __file__).resolve()
    base = p if p.is_dir() else p.parent
    if base.name == "tools":
        return base.parent
    return base.parent if (base.parent / "tools").is_dir() else base


def resolve_tasks_ledger(root: Path) -> Path | None:
    """解析 `.worktrees/tasks.json`：先本 checkout，再其父级（worktree 情形），最后主仓。"""
    candidates = [
        root / ".worktrees" / "tasks.json",
        root.parent / ".worktrees" / "tasks.json",   # <.worktrees>/<TID> worktree 情形
    ]
    try:
        sys.path.insert(0, str(root / "tools"))
        from _repo_paths import repo_root as _main_root  # noqa: E402

        candidates.append(Path(_main_root(root)) / ".worktrees" / "tasks.json")
    except Exception:  # noqa: BLE001 —— 解析失败不阻塞，候选列表继续
        pass
    for c in candidates:
        if c.is_file():
            return c
    return None


# ---------------------------------------------------------------------------
# 白名单加载/校验（三件套②）
# ---------------------------------------------------------------------------
def _minimal_parse_whitelist(text: str) -> dict:
    """PyYAML 缺失时的极简兜底解析：只支持 allow/deny/sandbox_repo_prefix 结构。

    显式 fallback 语义：解析结果由调用方标注 `_parser="fallback"` 并写入卡片元数据，
    不假装等价于完整 YAML 解析器。
    """
    data = {"sandbox_repo_prefix": "", "allow": [], "deny": []}
    section = None
    cur = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        if indent == 0:
            cur = None
            key, sep, val = body.partition(":")
            if not sep:
                section = None
                continue
            key, val = key.strip(), _strip_quotes(_strip_inline_comment(val))
            if key in ("allow", "deny"):
                section = key
                data[key] = []
            elif key == "sandbox_repo_prefix":
                data[key] = val
                section = None
            else:
                section = None
            continue
        if section is None:
            continue
        if body.startswith("- "):
            payload = body[2:].strip()
            if payload.startswith("name:"):
                cur = {"name": _strip_quotes(_strip_inline_comment(payload.split(":", 1)[1]))}
                data[section].append(cur)
            elif cur is not None and section == "deny":
                item = _strip_quotes(_strip_inline_comment(payload))
                if item:
                    cur.setdefault("exceptions", []).append(item)
            continue
        if ":" in body and cur is not None:
            key, _, val = body.partition(":")
            key, val = key.strip(), _strip_quotes(_strip_inline_comment(val))
            if key == "exceptions":
                cur.setdefault("exceptions", [])
            elif key:
                cur[key] = val
    return data


def load_whitelist(path, parser: str = "auto") -> dict:
    """加载白名单（只读）。parser=auto|pyyaml|fallback；fallback 时显式标注来源。"""
    p = Path(path)
    if not p.is_file():
        raise WhitelistError("白名单文件不存在（fail-closed）：%s" % p, code="whitelist_missing")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise WhitelistError("白名单读取失败：%s" % exc, code="whitelist_unreadable") from exc
    if parser not in ("auto", "pyyaml", "fallback"):
        raise InputError("--whitelist-parser 仅支持 auto/pyyaml/fallback")
    data = None
    used = "pyyaml"
    if parser in ("auto", "pyyaml"):
        try:
            import yaml  # noqa: PLC0415 —— 延迟导入，缺库走 fallback

            data = yaml.safe_load(text)
        except ImportError:
            if parser == "pyyaml":
                raise WhitelistError("PyYAML 不可用且指定了 --whitelist-parser pyyaml")
            data = None
        except Exception:  # noqa: BLE001 —— YAML 语法错误：auto 走 fallback 并显式标注
            data = None
    if data is None:
        data = _minimal_parse_whitelist(text)
        used = "fallback"
    if not isinstance(data, dict):
        raise WhitelistError("白名单顶层不是映射（YAML 结构异常）", code="whitelist_invalid")
    data.setdefault("sandbox_repo_prefix", "")
    data.setdefault("allow", [])
    data.setdefault("deny", [])
    if not isinstance(data["allow"], list) or not isinstance(data["deny"], list):
        raise WhitelistError("白名单 allow/deny 不是列表", code="whitelist_invalid")
    data["_parser"] = used
    data["_path"] = str(p)
    return data


def validate_whitelist(data: dict) -> dict:
    """校验四类约束：必需 allow / 必需 deny / allow∩deny 空 / 唯一写点范围。"""
    problems: list[str] = []
    allow: dict[str, dict] = {}
    for ent in data.get("allow") or []:
        if isinstance(ent, dict) and ent.get("name"):
            allow[str(ent["name"])] = ent
        else:
            problems.append("allow 条目缺少 name：%r" % (ent,))
    deny: dict[str, dict] = {}
    for ent in data.get("deny") or []:
        if isinstance(ent, dict) and ent.get("name"):
            deny[str(ent["name"])] = ent
        else:
            problems.append("deny 条目缺少 name：%r" % (ent,))

    for name in REQUIRED_ALLOW:
        if name not in allow:
            problems.append("allow 缺少必需工具：%s" % name)
    for name in REQUIRED_DENY:
        if name not in deny:
            problems.append("deny 缺少必需禁挂项：%s" % name)
    inter = sorted(set(allow) & set(deny))
    if inter:
        problems.append("allow/deny 存在交集（白名单自相矛盾）：%s" % ",".join(inter))

    card_write = allow.get("kanban_card_write") or {}
    if card_write:
        if str(card_write.get("card_type") or "") != CARD_TYPE:
            problems.append("kanban_card_write.card_type 必须为 %s，实际 %r"
                            % (CARD_TYPE, card_write.get("card_type")))
        if str(card_write.get("board") or "") != BOARD:
            problems.append("kanban_card_write.board 必须为 %s，实际 %r"
                            % (BOARD, card_write.get("board")))
    if not str(data.get("sandbox_repo_prefix") or "").strip():
        problems.append("sandbox_repo_prefix 缺失（只读工具无路径边界）")

    ops_allowed = {}
    for op, tool in OP_TOOL.items():
        ops_allowed[op] = (tool in allow) and (tool not in deny)
    if not ops_allowed.get("ledger_append"):
        problems.append("写入通道不可用：kanban_card_write 未放行或被 deny")

    return {
        "ok": not problems,
        "problems": problems,
        "parser": data.get("_parser", "unknown"),
        "whitelist_path": data.get("_path"),
        "sandbox_repo_prefix": str(data.get("sandbox_repo_prefix") or ""),
        "allow": sorted(allow),
        "deny": sorted(deny),
        "ops_allowed": ops_allowed,
    }


def guard_whitelist(args) -> tuple[dict, dict]:
    """加载 + 校验白名单；不通过即抛 WhitelistError（写入前 fail-closed）。"""
    data = load_whitelist(args.whitelist, parser=args.whitelist_parser)
    report = validate_whitelist(data)
    if not report["ok"]:
        raise WhitelistError("白名单校验不通过：" + "；".join(report["problems"]),
                             code="whitelist_reject")
    return data, report


# ---------------------------------------------------------------------------
# 快照适配（本地文件 / .worktrees/tasks.json 过滤 doing）
# ---------------------------------------------------------------------------
def _item_from_row(row: dict, fallback_id: str = "") -> dict | None:
    task_id = str(row.get("task_id") or row.get("id") or row.get("tid") or fallback_id or "").strip()
    if not task_id:
        return None
    return {
        "task_id": task_id[:64],
        "title": str(row.get("title") or row.get("desc") or row.get("subject")
                     or row.get("name") or "").strip()[:300],
        "status": str(row.get("status") or "").strip().lower(),
        "owner": str(row.get("owner") or row.get("agent") or "").strip()[:64],
        "project": str(row.get("project") or "").strip()[:64],
        "branch": str(row.get("branch") or "").strip()[:200],
        "created": str(row.get("created") or row.get("updated") or "").strip()[:40],
    }


def normalize_items(raw) -> list[dict]:
    """把多种快照形态归一为 [{task_id,title,status,owner,project,branch,created}]。"""
    rows: list = []
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        found = False
        for key in ("tasks", "items", "plan", "doing"):
            if isinstance(raw.get(key), list):
                rows = raw[key]
                found = True
                break
        if not found:
            for key, val in raw.items():
                if isinstance(val, dict):
                    row = dict(val)
                    row.setdefault("task_id", key)
                    rows.append(row)
    else:
        raise SnapshotError("快照顶层必须是列表或映射，实际 %s" % type(raw).__name__)
    items = []
    for row in rows:
        if not isinstance(row, dict):
            raise SnapshotError("快照条目必须是映射，遇到 %s" % type(row).__name__)
        item = _item_from_row(row)
        if item is not None:
            items.append(item)
    return items


def _read_json_file(path: Path, what: str):
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SnapshotError("%s 不存在：%s" % (what, path), code="snapshot_missing") from exc
    except OSError as exc:
        raise SnapshotError("%s 读取失败：%s" % (what, exc), code="snapshot_unreadable") from exc
    try:
        return json.loads(text)
    except UnicodeDecodeError as exc:
        raise SnapshotError("%s 不是 UTF-8 文本：%s" % (what, exc), code="bad_snapshot_json") from exc
    except json.JSONDecodeError as exc:
        raise SnapshotError("%s JSON 解析失败（第 %d 行）：%s" % (what, exc.lineno, exc.msg),
                            code="bad_snapshot_json") from exc


def _split_statuses(text: str | None) -> tuple[str, ...]:
    if text is None:
        return ("doing",)
    parts = tuple(p.strip().lower() for p in str(text).split(",") if p.strip())
    return parts


def snapshot_from_file(path, filter_status: str | None = None) -> dict:
    data = _read_json_file(Path(path), "快照文件")
    items = normalize_items(data)
    statuses = _split_statuses(filter_status) if filter_status else ()
    if statuses:
        items = [it for it in items if it["status"] in statuses]
    sid = _sha256(items)
    return {
        "schema": SCHEMA_SNAPSHOT,
        "source_kind": "snapshot_file",
        "path": str(Path(path)),
        "filter_status": list(statuses),
        "total": len(normalize_items(data)) if not statuses else None,
        "selected": len(items),
        "snapshot_id": sid,
        "items": items,
    }


def snapshot_from_tasks_ledger(path, filter_status: str | None = "doing") -> dict:
    """从 devtask 账本（{tid: {desc,status,...}}）过滤 doing 生成快照（不联网）。"""
    data = _read_json_file(Path(path), "tasks.json")
    if not isinstance(data, dict):
        raise SnapshotError("tasks.json 顶层不是映射（预期 {tid: task}）", code="bad_tasks_ledger")
    statuses = _split_statuses(filter_status)
    rows = []
    for tid, val in data.items():
        if not isinstance(val, dict):
            continue
        status = str(val.get("status") or "").strip().lower()
        if statuses and status not in statuses:
            continue
        row = dict(val)
        row.setdefault("task_id", tid)
        rows.append(row)
    items = normalize_items(rows)
    return {
        "schema": SCHEMA_SNAPSHOT,
        "source_kind": "tasks_ledger",
        "path": str(Path(path)),
        "filter_status": list(statuses),
        "total": len(data),
        "selected": len(items),
        "snapshot_id": _sha256(items),
        "items": items,
    }


def auto_snapshot_dir(args) -> Path:
    """auto-snapshot 扫描目录：env > --snapshot-dir > <sandbox_prefix>/plans。"""
    env_dir = os.environ.get("ZHUGE_SANDBOX_PLANS_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    given = getattr(args, "snapshot_dir", None)
    if given:
        return Path(given)
    prefix = getattr(args, "_sandbox_prefix", None) or SANDBOX_PREFIX_FALLBACK
    return Path(prefix) / SANDBOX_PLANS_DIRNAME


def scan_auto_snapshots(args):
    """扫描沙盘 plans/，按 mtime 新→旧取最新可用快照。

    → (snapshot|None, info)。info 记录 dir/candidates/chosen/fallback/warnings；
    单个坏文件/空快照跳过并留因；无可用快照返回 None（由调用方回落 tasks）。
    """
    d = auto_snapshot_dir(args)
    info = {"dir": str(d), "candidates": [], "chosen": None,
            "fallback": False, "warnings": []}
    if not d.is_dir():
        info["warnings"].append("auto-snapshot: 目录不存在 %s → 回落 tasks" % d)
        info["fallback"] = True
        return None, info
    files = []
    for pat in SANDBOX_SNAPSHOT_GLOBS:
        files.extend(d.glob(pat))
    uniq = sorted({p.resolve() for p in files if p.is_file()},
                  key=lambda p: p.stat().st_mtime, reverse=True)
    uniq = uniq[:AUTO_SNAPSHOT_MAX_CANDIDATES]
    info["candidates"] = [str(p) for p in uniq]
    for p in uniq:
        try:
            snap = snapshot_from_file(p, filter_status=args.filter_status or None)
        except (SnapshotError, WhitelistError) as exc:
            info["warnings"].append("auto-snapshot: 跳过 %s（%s）"
                                    % (p.name, getattr(exc, "code", "bad_snapshot")))
            continue
        if not snap.get("items"):
            info["warnings"].append("auto-snapshot: 跳过空快照 %s" % p.name)
            continue
        info["chosen"] = str(p)
        return snap, info
    info["warnings"].append("auto-snapshot: %s 无可用快照 → 回落 tasks" % d)
    info["fallback"] = True
    return None, info


def _load_tasks_snapshot(args, root: Path, warnings: list, strict: bool) -> dict:
    tasks_path = Path(args.tasks) if args.tasks else resolve_tasks_ledger(root)
    if tasks_path is None or not Path(tasks_path).is_file():
        raise SnapshotError(
            "未找到 .worktrees/tasks.json（可用 --snapshot 或 --tasks 显式指定）",
            code="tasks_ledger_missing")
    snap = snapshot_from_tasks_ledger(tasks_path, filter_status=args.filter_status or "doing")
    snap["inside_sandbox_prefix"] = path_inside(tasks_path, args._sandbox_prefix)
    if not snap["inside_sandbox_prefix"]:
        warnings.append(
            "快照来自 devtask 账本而非沙盘仓（MVP dev 适配）：生产接线后应改用 "
            "digital-legion-sandbox/plans/ 快照")
        if strict:
            raise WhitelistError(
                "strict-sandbox：默认 tasks 账本快照不在沙盘仓前缀内，拒绝；"
                "请用 --snapshot 指向沙盘仓快照", code="snapshot_outside_sandbox")
    return snap


def load_snapshot(args, root: Path) -> tuple[dict, dict]:
    """按 --snapshot / --auto-snapshot / --tasks / 默认 .worktrees/tasks.json 装载快照。

    → (snapshot, meta)；meta 含 source_kind / inside_sandbox_prefix / warnings；
    自动模式额外含 auto_snapshot{dir,candidates,chosen,fallback,warnings}。
    """
    warnings: list[str] = []
    strict = bool(getattr(args, "strict_sandbox", False))
    auto_info = None
    if args.snapshot:
        snap = snapshot_from_file(args.snapshot, filter_status=args.filter_status or None)
        src = Path(args.snapshot)
        inside = path_inside(src, args._sandbox_prefix)
        snap["inside_sandbox_prefix"] = inside
        if not inside:
            warnings.append(
                "快照源不在沙盘仓前缀内（%s）：MVP 离线便利路径，生产接线后应改为沙盘仓 plans/ 快照"
                % (args._sandbox_prefix or "未配置"))
            if strict:
                raise WhitelistError(
                    "strict-sandbox：快照源 %s 不在沙盘仓前缀 %s 内，拒绝读取"
                    % (src, args._sandbox_prefix), code="snapshot_outside_sandbox")
    elif bool(getattr(args, "auto_snapshot", False)):
        snap, auto_info = scan_auto_snapshots(args)
        warnings.extend(auto_info["warnings"])
        if snap is None:
            if strict:
                raise WhitelistError(
                    "strict-sandbox：无可用沙盘快照，拒绝回落 tasks 账本",
                    code="snapshot_outside_sandbox")
            snap = _load_tasks_snapshot(args, root, warnings, strict)
        else:
            inside = path_inside(Path(snap["path"]), args._sandbox_prefix)
            snap["inside_sandbox_prefix"] = inside
            if not inside:
                warnings.append(
                    "auto-snapshot 命中 %s 但不在沙盘仓前缀 %s 内（检查 plans 落点）"
                    % (snap["path"], args._sandbox_prefix or "未配置"))
                if strict:
                    raise WhitelistError(
                        "strict-sandbox：自动快照 %s 不在沙盘仓前缀 %s 内，拒绝"
                        % (snap["path"], args._sandbox_prefix),
                        code="snapshot_outside_sandbox")
    else:
        snap = _load_tasks_snapshot(args, root, warnings, strict)
    meta = {
        "source_kind": snap["source_kind"],
        "path": snap["path"],
        "filter_status": snap["filter_status"],
        "total": snap["total"],
        "selected": snap["selected"],
        "snapshot_id": snap["snapshot_id"],
        "inside_sandbox_prefix": snap["inside_sandbox_prefix"],
        "warnings": warnings,
    }
    if auto_info is not None:
        meta["auto_snapshot"] = auto_info
    return snap, meta


# ---------------------------------------------------------------------------
# 质询问题生成（规则版、离线、可复现；不调用模型）
# ---------------------------------------------------------------------------
_QUESTION_TEMPLATES = (
    ("goal_deviation", "任务《{title}》的完成判据是什么？与当前目标/里程碑如何对应？"),
    ("risk", "若《{title}》失败，最早可观测信号是什么？是否有阻断/回滚预案？"),
    ("budget", "《{title}》占用的时间与资源预算来源、上限与超支处置是什么？"),
)


def build_questions(items: list[dict], max_questions: int = DEFAULT_MAX_QUESTIONS) -> list[dict]:
    """按 doing 任务 × 三维度（目标偏离/风险/预算）生成确定性质询问题。"""
    cap = max(1, min(int(max_questions or DEFAULT_MAX_QUESTIONS), MAX_QUESTIONS_HARD_CAP))
    questions: list[dict] = []
    qn = 0
    for item in items:
        title = item.get("title") or item["task_id"]
        for dim, tpl in _QUESTION_TEMPLATES:
            if len(questions) >= cap:
                return questions
            qn += 1
            questions.append({
                "q_id": "Q%d" % qn,
                "question": tpl.format(title=title),
                "dimension": dim,
                "target_task": item["task_id"],
            })
    return questions


# ---------------------------------------------------------------------------
# 账本（追加式 JSONL，fold 出状态）
# ---------------------------------------------------------------------------
def _apply_event(cards: dict, events: list, ev: dict) -> None:
    events.append(ev)
    cid = ev.get("card_id")
    kind = ev.get("event")
    if not cid:
        return
    if kind == "created" and isinstance(ev.get("card"), dict):
        card = dict(ev["card"])
        card["status"] = "open"
        cards[cid] = card
    elif kind == "answered":
        card = cards.get(cid)
        if card is not None:
            card.update({
                "status": "answered",
                "responded_by": ev.get("by"),
                "response_note": ev.get("note"),
                "verdict": ev.get("verdict"),
                "answered_at": ev.get("at"),
                "response_key": ev.get("response_key"),
            })
    elif kind == "closed":
        card = cards.get(cid)
        if card is not None:
            card.update({
                "status": "closed",
                "outcome": ev.get("outcome"),
                "closed_by": ev.get("by"),
                "close_note": ev.get("note"),
                "closed_at": ev.get("at"),
                "close_key": ev.get("close_key"),
            })
    # rejected：只留痕，不改状态


def load_ledger(path) -> tuple[dict, list]:
    """读取并折叠账本 → (cards, events)。空/不存在=空账本；坏行报行号（fail-closed）。"""
    p = Path(path)
    cards: dict = {}
    events: list = []
    if not p.exists():
        return cards, events
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise LedgerError("账本读取失败：%s" % exc) from exc
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError("账本第 %d 行 JSON 解析失败：%s" % (lineno, exc.msg)) from exc
        if not isinstance(ev, dict) or not ev.get("event") or not ev.get("card_id"):
            raise LedgerError("账本第 %d 行缺少 event/card_id 字段" % lineno)
        _apply_event(cards, events, ev)
    return cards, events


def append_events(path, events: list[dict]) -> None:
    """追加式写入（不改写历史），flush+fsync 保证掉电可读。"""
    if not events:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(ev, ensure_ascii=False) for ev in events]
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            fh.write(line + "\n")
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:  # 某些文件系统不支持 fsync；不掩盖，仅不阻塞
            pass


def _find_event(events: list, card_id: str, kind: str, key_field: str, key: str) -> dict | None:
    for ev in events:
        if ev.get("event") == kind and ev.get("card_id") == card_id and ev.get(key_field) == key:
            return ev
    return None


def next_card_id(cards: dict, date_str: str) -> str:
    prefix = "ZQ-%s-" % date_str.replace("-", "")
    nums = []
    for cid in cards:
        if cid.startswith(prefix):
            tail = cid[len(prefix):]
            if tail.isdigit():
                nums.append(int(tail))
    return "%s%02d" % (prefix, max(nums) + 1 if nums else 1)


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------
def _out(args, payload: dict, human: str = "") -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False))
    elif human:
        print(human)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def _resolve_ledger(args) -> Path:
    if args.ledger:
        return Path(args.ledger)
    root = checkout_root(args.repo_root)
    return root / DEFAULT_LEDGER_REL


def _guard_writes(args, wl: dict, *paths) -> None:
    """禁止把账本/产物写进沙盘仓（只读纪律，白名单 file_write 亦为 deny）。"""
    prefix = str(wl.get("sandbox_repo_prefix") or "")
    for p in paths:
        if prefix and path_inside(p, prefix):
            raise WhitelistError(
                "拒绝写沙盘仓（三件套①只读）：%s" % p, code="sandbox_write_denied")


def cmd_check(args) -> int:
    wl, report = guard_whitelist(args)
    payload = {"ok": True, "command": "check", **report}
    _out(args, payload, human="白名单校验通过（parser=%s，allow=%d/deny=%d）"
         % (report["parser"], len(report["allow"]), len(report["deny"])))
    return 0


def cmd_snapshot(args) -> int:
    wl, _report = guard_whitelist(args)
    args._sandbox_prefix = str(wl.get("sandbox_repo_prefix") or SANDBOX_PREFIX_FALLBACK)
    snap, meta = load_snapshot(args, checkout_root(args.repo_root))
    written = False
    out_path = ""
    if args.out:
        p = Path(args.out)
        _guard_writes(args, wl, p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"snapshot": snap, "meta": meta}, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
        written, out_path = True, str(p)
    payload = {"ok": True, "command": "snapshot", "written": written, "out": out_path,
               "meta": meta, "snapshot": snap}
    _out(args, payload, human="快照 %s：selected=%d / total=%s / snapshot_id=%s"
         % (snap["source_kind"], snap["selected"], snap["total"], _short_sha(snap["snapshot_id"])))
    return 0


def _load_or_emit_snapshot(args, wl: dict):
    args._sandbox_prefix = str(wl.get("sandbox_repo_prefix") or SANDBOX_PREFIX_FALLBACK)
    return load_snapshot(args, checkout_root(args.repo_root))


def cmd_emit(args) -> int:
    wl, report = guard_whitelist(args)
    snap, meta = _load_or_emit_snapshot(args, wl)
    if not snap["items"]:
        raise EmptySnapshotError(
            "快照为空（filter_status=%s）：无待质询任务，拒绝空质询卡"
            % (",".join(snap["filter_status"]) or "全部"))
    if args.severity not in SEVERITIES:
        raise InputError("--severity 非法：%r（可选 %s）" % (args.severity, ",".join(SEVERITIES)))
    max_q = args.max_questions
    questions = build_questions(snap["items"], max_questions=max_q)
    # P2 试点（默认 off）：real3 = 前 3 问用 OpenClaw 载体生成；失败降级规则模板并 warning。
    model_info = {"mode": "off"}
    model_warnings: list[str] = []
    if getattr(args, "model_mode", "off") == "real3":
        try:
            from zhuge_challenge_model import generate_questions as _gen_model
            model_qs, info = _gen_model(
                snap["items"], max_questions=min(3, len(snap["items"])))
        except Exception as exc:  # noqa: BLE001 —— 模型失败不反噬主链
            model_qs, info = None, {"mode": "real3",
                                    "error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
        if model_qs:
            top_ids = [it.get("task_id") for it in snap["items"][:len(model_qs)]]
            mq = [{"q_id": "Q%d" % (i + 1), "question": q,
                   "dimension": "model",
                   "target_task": top_ids[i] if i < len(top_ids) else ""}
                  for i, q in enumerate(model_qs)]
            rest = [q for q in questions if q.get("target_task") not in top_ids]
            questions = (mq + rest)[:max_q]
            for i, q in enumerate(questions):
                q["q_id"] = "Q%d" % (i + 1)
            info["used"] = True
            info["replaced_tasks"] = top_ids
        else:
            info["used"] = False
            model_warnings.append(
                "model real3 失败（%s）→ 已降级规则模板" % info.get("error"))
        model_info = info
    if not questions:
        raise EmptySnapshotError("未能生成任何质询问题（快照条目缺标题/ID？）")
    date_str = args.date or today_str()
    if not DATE_RE.match(date_str):
        raise InputError("--date 必须为 YYYY-MM-DD：%r" % date_str)
    ledger = _resolve_ledger(args)
    _guard_writes(args, wl, ledger)
    cards, events = load_ledger(ledger)

    idem_key = _sha256({"date": date_str, "snapshot_id": snap["snapshot_id"],
                        "questions": questions, "severity": args.severity,
                        "verdict_request": VERDICT_REQUEST})
    for card in cards.values():
        if card.get("idem_key") == idem_key:
            payload = {"ok": True, "command": "emit", "duplicate": True, "card": card,
                       "ledger": str(ledger), "written": False}
            _out(args, payload, human="幂等命中：已存在同一快照的质询卡 %s" % card["card_id"])
            return 0

    card_id = args.card_id or next_card_id(cards, date_str)
    if not CARD_ID_RE.match(card_id):
        raise InputError("--card-id 必须形如 ZQ-YYYYMMDD-NN：%r" % card_id)
    if card_id in cards:
        raise InputError("card_id 已存在（不可覆写追加式账本）：%s" % card_id)

    card = {
        "schema": SCHEMA_CARD,
        "card_id": card_id,
        "date": date_str,
        "board": BOARD,
        "card_type": CARD_TYPE,
        "severity": args.severity,
        "verdict_request": VERDICT_REQUEST,
        "status": "open",
        "ref_questions": questions,
        "target_plan_snapshot": {
            "source_kind": meta["source_kind"],
            "path": meta["path"],
            "filter_status": meta["filter_status"],
            "total": meta["total"],
            "selected": meta["selected"],
            "snapshot_id": meta["snapshot_id"],
            "inside_sandbox_prefix": meta["inside_sandbox_prefix"],
        },
        "idem_key": idem_key,
        "model": model_info,
        "created_at": now_iso(),
        "created_by": "zhuge-offline-mvp",
        "whitelist": {
            "path": report["whitelist_path"],
            "parser": report["parser"],
            "sandbox_repo_prefix": report["sandbox_repo_prefix"],
            "ops_allowed": report["ops_allowed"],
        },
        "warnings": list(meta["warnings"]) + model_warnings,
    }
    if args.dry_run:
        payload = {"ok": True, "command": "emit", "dry_run": True, "written": False,
                   "card": card, "ledger": str(ledger)}
        _out(args, payload, human="[dry-run] 将写入质询卡 %s（%d 问）→ %s"
             % (card_id, len(questions), ledger))
        return 0

    append_events(ledger, [{"event": "created", "card_id": card_id, "at": now_iso(), "card": card}])
    payload = {"ok": True, "command": "emit", "dry_run": False, "written": True,
               "duplicate": False, "card": card, "ledger": str(ledger)}
    _out(args, payload, human="已生成质询卡 %s（%d 问）→ %s"
         % (card_id, len(questions), ledger))
    return 0


def _reject_transition(args, ledger: Path, card_id: str, op: str, reason: str,
                       attempted: dict) -> None:
    """非法迁移：追加 rejected 事件留因（账本追加式），随后抛错。"""
    append_events(ledger, [{
        "event": "rejected", "card_id": card_id, "at": now_iso(),
        "op": op, "reason": reason, "attempted": attempted,
    }])
    raise TransitionError("%s 被拒绝：%s（已留因 rejected 事件）" % (op, reason))


def _require_card(cards: dict, card_id: str):
    card = cards.get(card_id)
    if card is None:
        raise InputError("卡不存在：%s" % card_id, code="card_not_found")
    return card


def cmd_respond(args) -> int:
    wl, _report = guard_whitelist(args)
    ledger = _resolve_ledger(args)
    _guard_writes(args, wl, ledger)
    cards, events = load_ledger(ledger)
    card_id = args.card_id
    card = _require_card(cards, card_id)
    if args.verdict not in VERDICTS:
        raise InputError("--verdict 仅支持 respond/fix：%r" % args.verdict)
    if not str(args.by or "").strip():
        raise InputError("--by 必填（响应人，不得静默）")
    if not str(args.note or "").strip():
        raise InputError("--note 必填（响应/整改留痕，不得静默）")
    response_key = _sha256({"card_id": card_id, "by": args.by, "note": args.note,
                            "verdict": args.verdict})
    if _find_event(events, card_id, "answered", "response_key", response_key):
        payload = {"ok": True, "command": "respond", "duplicate": True, "card": card,
                   "ledger": str(ledger), "written": False}
        _out(args, payload, human="幂等命中：相同响应已记录（%s）" % card_id)
        return 0
    if card["status"] != "open":
        _reject_transition(args, ledger, card_id, "respond",
                           "状态 %s 不可 respond（仅 open 可 responded）" % card["status"],
                           {"by": args.by, "note": args.note, "verdict": args.verdict})
    event = {"event": "answered", "card_id": card_id, "at": now_iso(),
             "by": args.by, "note": args.note, "verdict": args.verdict,
             "response_key": response_key}
    if args.dry_run:
        payload = {"ok": True, "command": "respond", "dry_run": True, "written": False,
                   "planned_event": event, "ledger": str(ledger)}
        _out(args, payload, human="[dry-run] 将记录 answered 事件（%s）" % card_id)
        return 0
    append_events(ledger, [event])
    cards, _ = load_ledger(ledger)
    payload = {"ok": True, "command": "respond", "duplicate": False, "written": True,
               "card": cards[card_id], "ledger": str(ledger)}
    _out(args, payload, human="已记录响应：%s → answered" % card_id)
    return 0


def cmd_close(args) -> int:
    wl, _report = guard_whitelist(args)
    ledger = _resolve_ledger(args)
    _guard_writes(args, wl, ledger)
    cards, events = load_ledger(ledger)
    card_id = args.card_id
    card = _require_card(cards, card_id)
    if args.outcome not in DECISIONS:
        raise InputError("--outcome 仅支持 accepted/rejected：%r" % args.outcome)
    if not str(args.by or "").strip():
        raise InputError("--by 必填（结案人；本 MVP 不代签，由人填入）")
    if not str(args.note or "").strip():
        raise InputError("--note 必填（结案留痕）")
    close_key = _sha256({"card_id": card_id, "outcome": args.outcome,
                         "by": args.by, "note": args.note})
    if _find_event(events, card_id, "closed", "close_key", close_key):
        payload = {"ok": True, "command": "close", "duplicate": True, "card": card,
                   "ledger": str(ledger), "written": False}
        _out(args, payload, human="幂等命中：相同结案已记录（%s）" % card_id)
        return 0
    if card["status"] != "answered":
        _reject_transition(args, ledger, card_id, "close",
                           "状态 %s 不可 close（仅 answered 可 closed；未响应不得静默结案）"
                           % card["status"],
                           {"outcome": args.outcome, "by": args.by, "note": args.note})
    event = {"event": "closed", "card_id": card_id, "at": now_iso(),
             "outcome": args.outcome, "by": args.by, "note": args.note,
             "close_key": close_key}
    if args.dry_run:
        payload = {"ok": True, "command": "close", "dry_run": True, "written": False,
                   "planned_event": event, "ledger": str(ledger)}
        _out(args, payload, human="[dry-run] 将记录 closed 事件（%s）" % card_id)
        return 0
    append_events(ledger, [event])
    cards, _ = load_ledger(ledger)
    payload = {"ok": True, "command": "close", "duplicate": False, "written": True,
               "card": cards[card_id], "ledger": str(ledger)}
    _out(args, payload, human="已结案：%s → closed（%s）" % (card_id, args.outcome))
    return 0


def _card_summary(card: dict) -> dict:
    return {
        "card_id": card.get("card_id"),
        "date": card.get("date"),
        "status": card.get("status"),
        "severity": card.get("severity"),
        "outcome": card.get("outcome"),
        "questions": len(card.get("ref_questions") or []),
        "snapshot_source": (card.get("target_plan_snapshot") or {}).get("source_kind"),
        "created_at": card.get("created_at"),
        "responded_by": card.get("responded_by"),
    }


def cmd_list(args) -> int:
    guard_whitelist(args)
    ledger = _resolve_ledger(args)
    status = (args.status or "").strip().lower()
    if status and status not in STATUSES:
        raise InputError("--status 仅支持 %s：%r" % ("/".join(STATUSES), status))
    cards, events = load_ledger(ledger)
    rows = [c for c in cards.values() if not status or c.get("status") == status]
    rows.sort(key=lambda c: str(c.get("card_id")))
    payload = {"ok": True, "command": "list", "ledger": str(ledger),
               "events": len(events), "status_filter": status or None,
               "count": len(rows), "cards": [_card_summary(c) for c in rows]}
    human = "\n".join("%-18s %-9s %-8s %d问 %s" % (
        r["card_id"], r["status"], r["severity"], r["questions"], r["snapshot_source"])
        for r in payload["cards"]) or "（无卡片）"
    _out(args, payload, human=human)
    return 0


def cmd_show(args) -> int:
    guard_whitelist(args)
    ledger = _resolve_ledger(args)
    cards, events = load_ledger(ledger)
    card = _require_card(cards, args.card_id)
    history = [ev for ev in events if ev.get("card_id") == args.card_id]
    payload = {"ok": True, "command": "show", "ledger": str(ledger),
               "card": card, "history": history, "history_count": len(history)}
    _out(args, payload, human=json.dumps(card, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _add_json_flag(sub_parser) -> None:
    """让 `--json` 既可放全局位（子命令之前）也可放子命令之后（等价）。"""
    sub_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                            help="JSON 输出（等价于全局 --json）")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zhuge_challenge.py",
        description="诸葛亮质询通道离线 MVP（快照→白名单校验→质询卡 JSONL→状态机；不联网/不接 Kanban/不排程）",
    )
    p.add_argument("--ledger", default=None,
                   help="质询卡账本路径（默认 <checkout>/work/zhuge_challenge/cards.jsonl）")
    p.add_argument("--whitelist", default=DEFAULT_WHITELIST,
                   help="三件套②白名单 YAML（只读；默认 %s）" % DEFAULT_WHITELIST)
    p.add_argument("--whitelist-parser", choices=("auto", "pyyaml", "fallback"), default="auto",
                   help="白名单解析器；auto=有 PyYAML 用 PyYAML，否则极简 fallback（显式标注）")
    p.add_argument("--repo-root", default=None, help="checkout 根（默认按脚本位置推断）")
    p.add_argument("--json", action="store_true", help="JSON 输出（机器可读）")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="校验三件套②白名单（只读）"); _add_json_flag(c)
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("snapshot", help="生成/查看计划快照（不联网）"); _add_json_flag(s)
    s.add_argument("--snapshot", default=None, help="快照 JSON 文件路径")
    s.add_argument("--auto-snapshot", action="store_true",
                   help="自动扫描沙盘仓 plans/ 最新可用快照（W5；无可用回落 tasks）")
    s.add_argument("--snapshot-dir", default=None,
                   help="auto-snapshot 扫描目录（默认 <sandbox>/plans；env ZHUGE_SANDBOX_PLANS_DIR 优先）")
    s.add_argument("--tasks", default=None, help="devtask 账本路径（覆盖 .worktrees/tasks.json）")
    s.add_argument("--filter-status", default=None,
                   help="状态过滤：snapshot 源默认不过滤；tasks 源默认 doing")
    s.add_argument("--out", default=None, help="把归一快照写入该 JSON 文件（不得写沙盘仓）")
    s.set_defaults(func=cmd_snapshot)

    e = sub.add_parser("emit", help="生成质询卡（open）"); _add_json_flag(e)
    e.add_argument("--snapshot", default=None, help="快照 JSON 文件路径")
    e.add_argument("--auto-snapshot", action="store_true",
                   help="自动扫描沙盘仓 plans/ 最新可用快照（W5；无可用回落 tasks）")
    e.add_argument("--snapshot-dir", default=None,
                   help="auto-snapshot 扫描目录（默认 <sandbox>/plans；env ZHUGE_SANDBOX_PLANS_DIR 优先）")
    e.add_argument("--tasks", default=None, help="devtask 账本路径（覆盖 .worktrees/tasks.json）")
    e.add_argument("--filter-status", default=None,
                   help="状态过滤：snapshot 源默认全部；tasks 源默认 doing")
    e.add_argument("--severity", default=DEFAULT_SEVERITY, choices=list(SEVERITIES))
    e.add_argument("--card-id", default=None, help="自定义卡号（ZQ-YYYYMMDD-NN）；默认顺延")
    e.add_argument("--date", default=None, help="卡片日期 YYYY-MM-DD（默认北京今日）")
    e.add_argument("--max-questions", type=int, default=DEFAULT_MAX_QUESTIONS)
    e.add_argument("--strict-sandbox", action="store_true",
                   help="快照源必须在沙盘仓前缀内（生产硬化；MVP 离线默认允许 dev 源）")
    e.add_argument("--model-mode", choices=("off", "real3"), default="off",
                   help="质询生成模式：off=规则模板（默认，离线）；real3=前 3 问用 OpenClaw 载体生成（P2 试点，失败降级规则）")
    e.add_argument("--dry-run", action="store_true", help="只算不写")
    e.set_defaults(func=cmd_emit)

    r = sub.add_parser("respond", help="田丰响应/整改（open→answered）"); _add_json_flag(r)
    r.add_argument("--card-id", required=True)
    r.add_argument("--by", required=True, help="响应人")
    r.add_argument("--note", required=True, help="响应/整改留痕")
    r.add_argument("--verdict", default="respond", choices=list(VERDICTS))
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=cmd_respond)

    cl = sub.add_parser("close", help="结案（answered→closed；须响应后才可结案）"); _add_json_flag(cl)
    cl.add_argument("--card-id", required=True)
    cl.add_argument("--outcome", required=True, choices=list(DECISIONS))
    cl.add_argument("--by", required=True, help="结案人（本 MVP 不代签）")
    cl.add_argument("--note", required=True, help="结案留痕")
    cl.add_argument("--dry-run", action="store_true")
    cl.set_defaults(func=cmd_close)

    ls = sub.add_parser("list", help="列出卡片（可按状态过滤）"); _add_json_flag(ls)
    ls.add_argument("--status", default=None, help="open/answered/closed")
    ls.set_defaults(func=cmd_list)

    sh = sub.add_parser("show", help="查看单卡与历史事件"); _add_json_flag(sh)
    sh.add_argument("--card-id", required=True)
    sh.set_defaults(func=cmd_show)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        rc = int(args.func(args))
        # C1 ↔ P1-4（可选，默认关）：命令成功后把质询卡同步到结构化决策账本
        if rc == 0 and os.environ.get("ZHUGE_C1_DECISION_SYNC", "").strip().lower() \
                in ("1", "true", "yes", "on"):
            try:
                tools_dir = str(Path(__file__).resolve().parent)
                if tools_dir not in sys.path:
                    sys.path.insert(0, tools_dir)
                from zhuge_c1_decisions import sync_all
                sync_all()
            except Exception:  # noqa: BLE001 —— 同步失败不反噬 C1 主链
                pass
        return rc
    except ZhugeError as exc:
        payload = {"ok": False, "command": getattr(args, "command", None),
                   "error": {"code": exc.code, "detail": exc.detail}}
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print("错误[%s]：%s" % (exc.code, exc.detail), file=sys.stderr)
        return int(exc.exit_code)
    except OSError as exc:
        payload = {"ok": False, "command": getattr(args, "command", None),
                   "error": {"code": "io_error", "detail": str(exc)}}
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print("IO 错误：%s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
