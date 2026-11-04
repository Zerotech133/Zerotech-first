# -*- coding: utf-8 -*-
"""W26 · 百炼数据集 schema 校验门禁（task-186）：上传前离线校验 SFT / DPO JSONL。

背景
----
百炼（Model Studio）数据集导入对 schema 很挑：SFT 只接受 `messages`（可带 `meta`？）等顶层结构，
**多出的顶层字段（如 `category`）会导致导入失败**（v4 raw 踩坑：顶层 `category`；v4d 为清洗后应通过）。
本脚本作为上传前门禁，杜绝再次踩坑。

规则（本脚本实现）
------------------
通用：
  1. 文件 UTF-8 编码且**无 BOM**；
  2. 每行（忽略空行）必须是 JSON object；
  3. 单行字符数 ≤ `--max-line`（默认 4000）；
  4. 输出条数/行长度统计，退出码 0（全部通过）/ 1（存在错误）。
SFT（--type sft）：
  - 顶层 keys 仅允许 `messages`、`meta`（额外字段如 `category` 报错）；
  - `messages` 必须为 list：每条仅 `role`/`content`；role ∈ {system,user,assistant}；
  - system 只允许一条且在首位；其后 user/assistant 交替、以 assistant 结尾；
  - user/assistant 的 content 非空；`meta` 若存在必须是 object。
DPO（--type dpo）：
  - 顶层 keys 仅允许 `messages`、`chosen`、`rejected`（`prompt` 等额外字段报错）；
  - `messages` 同 SFT 交替规则，且必须以 **user** 结尾（prompt 对话）；
  - `chosen` / `rejected` 必须是 object 且 `role=assistant`、content 非空。

用法
----
    # 门禁：v4d（清洗后）应通过
    python tools/finetune/validate_bailian_schema.py --path research/finetune/sft/quant_kb_sft_v4d_train.jsonl --type sft
    # raw v4 应报错（顶层 category）
    python tools/finetune/validate_bailian_schema.py --path research/finetune/sft/quant_kb_sft_v4_train.jsonl --type sft
    # DPO 校验 + JSON 输出（CI/报告）
    python tools/finetune/validate_bailian_schema.py --path <dpo.jsonl> --type dpo --json
    # 自定义行长度上限
    python tools/finetune/validate_bailian_schema.py --path <jsonl> --type sft --max-line 8000

退出码：0=通过；1=存在 schema/格式错误（可接 CI/上传前脚本）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

VER = "1.0.0"
ALLOWED_TOP_KEYS = {"sft": {"messages", "meta"}, "dpo": {"messages", "chosen", "rejected"}}
MESSAGE_KEYS = {"role", "content"}
ROLES = {"system", "user", "assistant"}
DEFAULT_MAX_LINE = 4000
MAX_REPORTED_ERRORS = 200


def validate_messages(messages, *, kind: str) -> list[dict]:
    """校验 messages：角色集合、system 位置、user/assistant 交替、结尾角色、content 非空。"""
    errs: list[dict] = []

    def add(code: str, message: str, index: int | None = None):
        e = {"code": code, "message": message}
        if index is not None:
            e["message_index"] = index
        errs.append(e)

    if not isinstance(messages, list):
        add("messages_not_list", "messages 必须是 list")
        return errs
    if len(messages) < 2:
        add("messages_too_short", f"messages 至少 2 条（实际 {len(messages)}）")
        return errs
    roles: list[str] = []
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            add("message_not_object", "message 必须是 object", i)
            continue
        extra = set(m) - MESSAGE_KEYS
        if extra:
            add("message_extra_keys", f"message 含多余字段 {sorted(extra)}", i)
        role = m.get("role")
        if role not in ROLES:
            add("message_role", f"非法 role={role!r}", i)
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            add("message_content", "content 必须为非空字符串", i)
        roles.append(str(role))
    if any(r not in ROLES for r in roles):
        return errs  # 角色非法时不再做交替判断
    # system：最多一条且在首位
    if "system" in roles:
        if roles.count("system") > 1 or roles[0] != "system":
            add("system_position", "system 只允许一条且在首位")
    body = roles[1:] if roles and roles[0] == "system" else roles
    if not body:
        add("messages_too_short", "除 system 外无对话消息")
        return errs
    expected = "user"
    for idx, r in enumerate(body):
        if r != expected:
            add("message_alternation", f"第 {idx + 1} 条对话应为 {expected}，实际 {r}")
            break
        expected = "assistant" if expected == "user" else "user"
    if kind == "sft" and body[-1] != "assistant":
        add("missing_assistant", "SFT messages 必须以 assistant 结尾")
    if kind == "dpo" and body[-1] != "user":
        add("dpo_prompt_tail", "DPO messages 必须以 user 结尾（chosen/rejected 为回答）")
    return errs


def validate_response(obj, *, name: str) -> list[dict]:
    """DPO chosen/rejected：必须是 role=assistant 且 content 非空的 object。"""
    errs: list[dict] = []
    if not isinstance(obj, dict):
        return [{"code": f"{name}_not_object", "message": f"{name} 必须是 object"}]
    if obj.get("role") != "assistant":
        errs.append({"code": f"{name}_role", "message": f"{name}.role 必须为 assistant（实际 {obj.get('role')!r}）"})
    content = obj.get("content")
    if not isinstance(content, str) or not content.strip():
        errs.append({"code": f"{name}_content", "message": f"{name}.content 必须为非空字符串"})
    extra = set(obj) - MESSAGE_KEYS
    if extra:
        errs.append({"code": f"{name}_extra_keys", "message": f"{name} 含多余字段 {sorted(extra)}"})
    return errs


def validate_record(obj, *, kind: str) -> list[dict]:
    """校验单条 JSONL object（顶层 keys + messages/meta/chosen/rejected）。"""
    if not isinstance(obj, dict):
        return [{"code": "not_object", "message": "顶层必须是 JSON object"}]
    errs: list[dict] = []
    allowed = ALLOWED_TOP_KEYS[kind]
    extra = sorted(set(obj) - allowed)
    if extra:
        errs.append({"code": "extra_top_level_keys",
                     "message": f"顶层出现百炼不接受的字段：{extra}（{kind} 仅允许 {sorted(allowed)}）"})
    missing = sorted(allowed - set(obj) - ({"meta"} if kind == "sft" else set()))
    if missing:
        errs.append({"code": "missing_top_level_keys", "message": f"缺少必需字段：{missing}"})
    if "messages" in obj:
        for e in validate_messages(obj["messages"], kind=kind):
            errs.append(e)
    if kind == "sft" and "meta" in obj and not isinstance(obj["meta"], dict):
        errs.append({"code": "meta_not_object", "message": "meta 若存在必须是 object"})
    if kind == "dpo":
        for name in ("chosen", "rejected"):
            if name in obj:
                errs += validate_response(obj[name], name=name)
    return errs


def validate_file(path: str | Path, *, kind: str = "sft", max_line: int = DEFAULT_MAX_LINE) -> dict:
    """校验一个 JSONL 文件，返回统计与错误报告（纯离线，不写文件）。"""
    p = Path(path)
    result = {"path": str(p), "type": kind, "version": VER, "max_line": int(max_line),
              "exists": p.is_file(), "bytes": 0, "lines_total": 0, "lines_nonempty": 0,
              "blank_lines": 0, "valid_lines": 0, "invalid_lines": 0,
              "max_line_chars": 0, "avg_line_chars": 0.0, "role_counts": {},
              "top_key_counts": {}, "errors": [], "error_count": 0, "ok": False}
    if not p.is_file():
        result["errors"].append({"line": 0, "code": "missing_file", "message": f"文件不存在：{p}"})
        result["error_count"] = 1
        return result
    raw = p.read_bytes()
    result["bytes"] = len(raw)
    if raw.startswith(b"\xef\xbb\xbf"):
        result["errors"].append({"line": 0, "code": "bom", "message": "文件含 UTF-8 BOM（百炼导入不接受）"})
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        result["errors"].append({"line": 0, "code": "utf8_decode",
                                 "message": f"非 UTF-8 编码：{exc}"})
        result["error_count"] = len(result["errors"])
        return result
    if raw.startswith(b"\xef\xbb\xbf"):
        text = text.lstrip("\ufeff")
    lines = text.splitlines()
    result["lines_total"] = len(lines)
    lengths: list[int] = []
    role_counts: dict[str, int] = {}
    top_counts: dict[str, int] = {}
    for no, line in enumerate(lines, 1):
        if not line.strip():
            result["blank_lines"] += 1
            continue
        result["lines_nonempty"] += 1
        lengths.append(len(line))
        if len(line) > int(max_line):
            result["errors"].append({"line": no, "code": "line_too_long",
                                     "message": f"行字符数 {len(line)} > {max_line}"})
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            result["errors"].append({"line": no, "code": "invalid_json",
                                     "message": f"JSON 解析失败：{exc.msg}（col {exc.colno}）"})
            result["invalid_lines"] += 1
            continue
        if isinstance(obj, dict):
            for k in obj:
                top_counts[str(k)] = top_counts.get(str(k), 0) + 1
            msgs = obj.get("messages")
            if isinstance(msgs, list):
                for m in msgs:
                    if isinstance(m, dict):
                        role_counts[str(m.get("role"))] = role_counts.get(str(m.get("role")), 0) + 1
            for name in ("chosen", "rejected"):
                if isinstance(obj.get(name), dict):
                    r = str(obj[name].get("role"))
                    role_counts[f"{name}:{r}"] = role_counts.get(f"{name}:{r}", 0) + 1
        errs = validate_record(obj, kind=kind)
        if errs:
            result["invalid_lines"] += 1
            for e in errs:
                if len(result["errors"]) < MAX_REPORTED_ERRORS:
                    result["errors"].append({"line": no, **e})
        else:
            result["valid_lines"] += 1
    result["max_line_chars"] = max(lengths) if lengths else 0
    result["avg_line_chars"] = round(sum(lengths) / len(lengths), 1) if lengths else 0.0
    result["role_counts"] = role_counts
    result["top_key_counts"] = top_counts
    result["error_count"] = len(result["errors"])
    result["ok"] = result["error_count"] == 0
    return result


def summarize(result: dict) -> str:
    lines = [f"[bailian_schema] {result['path']} (type={result['type']})",
             f"  bytes={result['bytes']} lines={result['lines_total']} "
             f"(非空 {result['lines_nonempty']} / 空行 {result['blank_lines']}) "
             f"valid={result['valid_lines']} invalid={result['invalid_lines']}",
             f"  line_chars: max={result['max_line_chars']} avg={result['avg_line_chars']} "
             f"max_line_limit={result['max_line']}"]
    if result["error_count"]:
        codes: dict[str, int] = {}
        for e in result["errors"]:
            codes[e["code"]] = codes.get(e["code"], 0) + 1
        lines.append(f"  ERROR count≥{result['error_count']} → by code: {dict(sorted(codes.items()))}")
        for e in result["errors"][:5]:
            lines.append(f"    - line {e.get('line')}: [{e['code']}] {e['message']}")
        lines.append("  RESULT: FAIL（门禁拒绝：该文件不可直接上传百炼）")
    else:
        lines.append("  RESULT: PASS（schema 通过，可上传百炼）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass
    ap = argparse.ArgumentParser(description="百炼数据集 schema 校验门禁（SFT/DPO JSONL）")
    ap.add_argument("--path", required=True, help="待校验 JSONL 路径")
    ap.add_argument("--type", required=True, choices=["sft", "dpo"], help="数据集类型")
    ap.add_argument("--max-line", type=int, default=DEFAULT_MAX_LINE, help=f"单行字符上限（默认 {DEFAULT_MAX_LINE}）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON 报告")
    args = ap.parse_args(argv)
    result = validate_file(args.path, kind=args.type, max_line=args.max_line)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(summarize(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
