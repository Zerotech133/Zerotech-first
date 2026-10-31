# -*- coding: utf-8 -*-
"""量学战法规格库（research/strategy_specs/*.yaml）加载 / 校验 CLI。

规格库把黑马王子两本量学书（《伏击涨停》《涨停密码》）中的 24 个命名战法
整理成机器可读 YAML，供策略库、因子库、Agent 读取与引用。

用法（在仓库根目录或任意目录均可，路径基于本文件定位仓库根）::

    python tools/research/strategy_specs.py validate [--json] [--spec-dir DIR]
    python tools/research/strategy_specs.py list --status untested|tested|all [--json]
    python tools/research/strategy_specs.py get --id LX02_GOLDEN_PILLAR [--json]

退出码：0=成功；1=校验失败；2=参数/未知 id 错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover - PyYAML 在 py312 已安装
    raise SystemExit("缺少 PyYAML：请先安装 pyyaml（C:\\项目\\py312 环境已内置）") from exc

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC_DIR = REPO_ROOT / "research" / "strategy_specs"

EXPECTED_COUNT = 24
VALID_TYPES = ("形态", "事件", "过滤器", "概念")
VALID_STATUSES = ("tested_rejected", "tested_backtested", "untested", "concept")
TESTED_STATUSES = ("tested_rejected", "tested_backtested")
VALID_COMPUTABILITY = ("A", "B", "C")
VALID_STATUS_FILTERS = ("untested", "tested", "all")

# 任务 schema 的必填字段（page_start/page_end 或 evidence_page 至少一组，单独校验）
REQUIRED_FIELDS = (
    "id",
    "name",
    "source",
    "chapter",
    "type",
    "computability",
    "params",
    "entry_rule",
    "exit_rule",
    "test_status",
    "tested_ref",
    "h_ids",
    "notes",
    "updated_at",
    "evidence_quote",
)
REQUIRED_NONEMPTY_STR = (
    "id",
    "name",
    "source",
    "chapter",
    "entry_rule",
    "exit_rule",
    "notes",
    "updated_at",
    "evidence_quote",
)
PAGE_FIELDS = ("page_start", "page_end", "evidence_page")

ID_RE = re.compile(r"^LX\d{2}_[A-Z0-9_]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FILE_RE = re.compile(r"^(\d{3})_[^/\\]+\.yaml$")


def spec_dir_or_default(spec_dir: str | Path | None = None) -> Path:
    if spec_dir is None:
        return DEFAULT_SPEC_DIR
    return Path(spec_dir)


def iter_spec_files(spec_dir: str | Path | None = None) -> list[Path]:
    """按文件名排序返回全部 YAML 规格文件。"""
    d = spec_dir_or_default(spec_dir)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.yaml") if p.is_file())


def load_specs(spec_dir: str | Path | None = None) -> list[dict]:
    """加载全部规格 YAML；文件格式错误时抛 ValueError（含文件名）。"""
    specs: list[dict] = []
    for path in iter_spec_files(spec_dir):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"{path.name}: YAML 解析失败: {exc}") from exc
        if not isinstance(doc, dict):
            raise ValueError(f"{path.name}: 顶层必须是 mapping（dict），实际 {type(doc).__name__}")
        specs.append(doc)
    return specs


def specs_by_id(specs: list[dict]) -> dict[str, dict]:
    return {str(s.get("id")): s for s in specs}


def filter_specs(specs: list[dict], status: str = "all") -> list[dict]:
    """status: untested / tested（tested_rejected+tested_backtested）/ all。"""
    if status not in VALID_STATUS_FILTERS:
        raise ValueError(f"未知 status: {status!r}（可选 {VALID_STATUS_FILTERS}）")
    if status == "all":
        return list(specs)
    if status == "tested":
        return [s for s in specs if s.get("test_status") in TESTED_STATUSES]
    return [s for s in specs if s.get("test_status") == "untested"]


def get_spec(specs: list[dict], spec_id: str) -> dict:
    for spec in specs:
        if spec.get("id") == spec_id:
            return spec
    raise KeyError(spec_id)


def _status_counts(specs: list[dict]) -> dict[str, int]:
    counts = {status: 0 for status in VALID_STATUSES}
    for spec in specs:
        status = spec.get("test_status")
        if isinstance(status, str):
            counts[status] = counts.get(status, 0) + 1
    return counts


def validate_specs(spec_dir: str | Path | None = None) -> dict:
    """校验规格库，返回 {ok, count, errors, warnings, status_counts, spec_dir}。"""
    d = spec_dir_or_default(spec_dir)
    errors: list[str] = []
    warnings: list[str] = []
    specs: list[dict] = []

    if not d.is_dir():
        return {
            "ok": False,
            "count": 0,
            "errors": [f"规格目录不存在: {d}"],
            "warnings": [],
            "status_counts": {},
            "spec_dir": str(d),
            "files": 0,
        }

    files = iter_spec_files(d)
    if not files:
        errors.append(f"规格目录下没有 YAML 文件: {d}")
    if len(files) != EXPECTED_COUNT:
        errors.append(f"规格文件数应为 {EXPECTED_COUNT} 个，实际 {len(files)} 个")

    seen_ids: dict[str, str] = {}
    seen_prefixes: dict[int, str] = {}

    for path in files:
        m = FILE_RE.match(path.name)
        if not m:
            errors.append(f"{path.name}: 文件名应形如 '001_战法名.yaml'（三位序号前缀）")
        else:
            prefix = int(m.group(1))
            if prefix in seen_prefixes:
                errors.append(f"{path.name}: 文件名序号 {prefix:03d} 与 {seen_prefixes[prefix]} 重复")
            seen_prefixes[prefix] = path.name

        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            errors.append(f"{path.name}: YAML 解析失败: {exc}")
            continue
        if not isinstance(doc, dict):
            errors.append(f"{path.name}: 顶层必须是 mapping（dict），实际 {type(doc).__name__}")
            continue
        specs.append(doc)

        def err(msg: str) -> None:
            errors.append(f"{path.name}: {msg}")

        # ---- 必填字段 ----
        for field in REQUIRED_FIELDS:
            if field not in doc:
                err(f"缺少必填字段 '{field}'")
        for field in REQUIRED_NONEMPTY_STR:
            value = doc.get(field)
            if not isinstance(value, str) or not value.strip():
                err(f"字段 '{field}' 必须是非空字符串")
        if not any(field in doc for field in PAGE_FIELDS):
            err("缺少页码字段（page_start/page_end 或 evidence_page 至少一组）")

        # ---- id / 序号一致性 ----
        spec_id = doc.get("id")
        if isinstance(spec_id, str) and spec_id:
            if not ID_RE.match(spec_id):
                err(f"id '{spec_id}' 不符合 'LXnn_UPPER_SNAKE' 规范")
            if spec_id in seen_ids:
                err(f"id '{spec_id}' 与 {seen_ids[spec_id]} 重复")
            else:
                seen_ids[spec_id] = path.name
            num = spec_id[2:4]
            if m and num.isdigit() and int(num) != int(m.group(1)):
                err(f"id 序号 {num} 与文件名序号 {m.group(1)} 不一致")

        # ---- 枚举字段 ----
        if doc.get("type") not in VALID_TYPES:
            err(f"type 必须是 {VALID_TYPES} 之一，实际 {doc.get('type')!r}")
        if doc.get("test_status") not in VALID_STATUSES:
            err(f"test_status 必须是 {VALID_STATUSES} 之一，实际 {doc.get('test_status')!r}")
        if doc.get("computability") not in VALID_COMPUTABILITY:
            err(f"computability 必须是 {VALID_COMPUTABILITY} 之一，实际 {doc.get('computability')!r}")

        # ---- params ----
        if not isinstance(doc.get("params"), dict):
            err("params 必须是 dict（可调参数表）")

        # ---- 页码 ----
        for field in PAGE_FIELDS:
            if field in doc:
                value = doc[field]
                if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                    err(f"'{field}' 必须是正整数页码，实际 {value!r}")
        start, end = doc.get("page_start"), doc.get("page_end")
        if isinstance(start, int) and isinstance(end, int) and start > end:
            err(f"page_start({start}) 不能大于 page_end({end})")

        # ---- 状态与证据引用 ----
        status = doc.get("test_status")
        tested_ref = doc.get("tested_ref")
        h_ids = doc.get("h_ids")
        if not isinstance(h_ids, list) or not all(isinstance(x, str) and x for x in h_ids):
            err("h_ids 必须是字符串列表（无已测假设时用 []）")
            h_ids = []
        if status in TESTED_STATUSES:
            if not isinstance(tested_ref, str) or not tested_ref.strip():
                err(f"test_status={status} 时 tested_ref 必须非空（指向研究报告）")
            else:
                ref_path = REPO_ROOT / tested_ref
                if not ref_path.is_file():
                    err(f"tested_ref 指向的报告不存在: {tested_ref}")
            if not h_ids:
                err(f"test_status={status} 时 h_ids 不能为空（至少登记一个 H 编号）")
        elif status in ("untested", "concept"):
            if tested_ref is not None:
                err(f"test_status={status} 时 tested_ref 应为 null（尚未测试）")
            if h_ids:
                err(f"test_status={status} 时 h_ids 应为 []")

        # ---- 日期 ----
        updated_at = doc.get("updated_at")
        if isinstance(updated_at, str) and updated_at and not DATE_RE.match(updated_at):
            err(f"updated_at 应为 YYYY-MM-DD，实际 {updated_at!r}")

    if len(specs) != len(files) and files:
        errors.append(f"可解析规格数({len(specs)})与文件数({len(files)})不一致")

    counts = _status_counts(specs)
    untested_n = counts.get("untested", 0)
    if specs and untested_n == 0:
        warnings.append("没有 untested 规格（若确为全部已测请更新本警告文案）")

    return {
        "ok": not errors,
        "count": len(specs),
        "files": len(files),
        "errors": errors,
        "warnings": warnings,
        "status_counts": counts,
        "spec_dir": str(d),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - 非 TextIOWrapper 时跳过
            pass


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _cmd_validate(args: argparse.Namespace) -> int:
    report = validate_specs(args.spec_dir)
    if args.json:
        _print_json(report)
    elif report["ok"]:
        print(f"校验通过：{report['count']}/{EXPECTED_COUNT} 个规格（目录 {report['spec_dir']}）")
        counts = report["status_counts"]
        print("状态统计：" + ", ".join(f"{k}={v}" for k, v in counts.items()))
        for warning in report["warnings"]:
            print(f"警告：{warning}", file=sys.stderr)
    else:
        print(f"校验失败：{len(report['errors'])} 个错误（已解析 {report['count']}/{EXPECTED_COUNT} 个规格）",
              file=sys.stderr)
        for item in report["errors"]:
            print(f"  - {item}", file=sys.stderr)
        return 1
    return 0 if report["ok"] else 1


def _row(spec: dict) -> str:
    return " | ".join(
        [
            str(spec.get("id", "")),
            str(spec.get("name", "")),
            str(spec.get("type", "")),
            str(spec.get("test_status", "")),
            str(spec.get("computability", "")),
            f"{spec.get('source', '')} {spec.get('chapter', '')}",
        ]
    )


def _cmd_list(args: argparse.Namespace) -> int:
    try:
        specs = load_specs(args.spec_dir)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    try:
        selected = filter_specs(specs, args.status)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    if args.json:
        payload = [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "type": s.get("type"),
                "test_status": s.get("test_status"),
                "computability": s.get("computability"),
                "source": s.get("source"),
                "chapter": s.get("chapter"),
            }
            for s in selected
        ]
        _print_json({"status": args.status, "count": len(payload), "items": payload})
        return 0

    print("ID | 名称 | 类型 | 状态 | 可计算化 | 来源 章节")
    for spec in selected:
        print(_row(spec))
    print(f"共 {len(selected)} 项（status={args.status}）")
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    try:
        specs = load_specs(args.spec_dir)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    try:
        spec = get_spec(specs, args.id)
    except KeyError:
        print(f"错误：未找到 id={args.id}（可用 list 子命令查看全部 id）", file=sys.stderr)
        return 2

    if args.json:
        _print_json(spec)
    else:
        print(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=1000, default_flow_style=False).rstrip())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="strategy_specs",
        description="量学战法规格库（24 个 YAML）加载 / 校验 / 查询工具",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--spec-dir", default=None, help="规格目录（默认 research/strategy_specs）")
        p.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")

    p_validate = sub.add_parser("validate", help="校验 24 个规格的必填字段/类型/id 唯一/文件齐全")
    add_common(p_validate)
    p_validate.set_defaults(func=_cmd_validate)

    p_list = sub.add_parser("list", help="列出规格（默认 all）")
    add_common(p_list)
    p_list.add_argument("--status", choices=VALID_STATUS_FILTERS, default="all",
                        help="untested=未测, tested=已测（含 rejected/backtested）, all=全部")
    p_list.set_defaults(func=_cmd_list)

    p_get = sub.add_parser("get", help="按 id 输出单个规格")
    add_common(p_get)
    p_get.add_argument("--id", required=True, help="规格 id，如 LX02_GOLDEN_PILLAR")
    p_get.set_defaults(func=_cmd_get)
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
