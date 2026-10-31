# -*- coding: utf-8 -*-
"""情报来源登记表只读查询 + 已有落盘样本采样（Phase 2，WO-INTEL-SOURCES-20261031）。

拍板 5（2026-10-31）：OSINT/股吧 Phase 2 只做「来源登记 + 只读采样」，
股吧优先、视频后置。本工具是该阶段的唯一命令行入口::

    python tools/intel_sources.py --list
    python tools/intel_sources.py --list --json
    python tools/intel_sources.py --sample guba --limit 5
    python tools/intel_sources.py --sample guba --limit 2 --registry <path>   # 测试注入

纪律（与登记表 policy 同口径）
------------------------------
- 登记≠接入：--list 只读登记表，不探测端口、不联网、不触发任何采集；
- 采样≠发布：--sample 只读 sample_path 下**已有**落盘数据并打印样本，
  不写文件/不写库/不新增常驻服务/不回写登记表/不对外分发；
- fail-visible：登记表缺失/坏 JSON/字段缺失、来源未知、来源未开采样、
  样本路径不存在或没有可用记录 → 显式非零退出 + stderr 原因，绝不静默返回空样本。

退出码：0 成功；2 登记表/参数错误；3 无可用样本（缺文件/空数据）；4 该来源不允许采样。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = REPO / "agent_platform" / "data" / "intel" / "sources.json"

SCHEMA_LIST = "intel_sources_list/v1"
SCHEMA_SAMPLE = "intel_sample/v1"

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_NO_SAMPLE = 3
EXIT_NOT_SAMPLING = 4

DEFAULT_LIMIT = 5
MAX_LIMIT = 100

MODES = ("sampling", "registered")
TRUST_LEVELS = ("high", "medium", "low")
REQUIRED_SOURCE_FIELDS = (
    "source_id", "name", "kind", "mode", "trust", "authorization",
    "sample_path", "last_sample", "owner",
)
REQUIRED_POLICY_KEYS = ("registration_is_not_ingestion", "sampling_is_not_publication")


class IntelSourcesError(Exception):
    """登记表/参数层错误（退出码 2）。"""

    exit_code = EXIT_BAD_INPUT


class NotSamplingError(IntelSourcesError):
    """该来源在 Phase 2 不允许采样（未启用/仅登记，退出码 4）。"""

    exit_code = EXIT_NOT_SAMPLING


class NoSampleError(IntelSourcesError):
    """样本路径缺失或没有可用落盘记录（fail-visible，退出码 3）。"""

    exit_code = EXIT_NO_SAMPLE


# ── 登记表读取与校验（只读）──────────────────────────────────
def _read_json(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as ex:
        raise IntelSourcesError("登记表不可读：%s（%s）" % (path, ex)) from ex
    try:
        return json.loads(text)
    except ValueError as ex:
        raise IntelSourcesError("登记表不是合法 JSON：%s（%s）" % (path, ex)) from ex


def validate_registry(data, path=None) -> dict:
    """校验登记表契约；返回原 dict。任何缺口显式抛错，不静默补默认值。"""
    where = str(path) if path else "<registry>"
    if not isinstance(data, dict):
        raise IntelSourcesError("登记表根对象必须是 dict：%s" % where)
    if not isinstance(data.get("schema_version"), str) or not data["schema_version"]:
        raise IntelSourcesError("登记表缺 schema_version：%s" % where)
    policy = data.get("policy")
    if not isinstance(policy, dict):
        raise IntelSourcesError("登记表缺 policy 对象（须写明登记≠接入/采样≠发布）：%s" % where)
    for key in REQUIRED_POLICY_KEYS:
        if not isinstance(policy.get(key), str) or not policy[key].strip():
            raise IntelSourcesError("登记表 policy 缺口径：%s（%s）" % (key, where))
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise IntelSourcesError("登记表 sources 必须是非空数组：%s" % where)
    seen = set()
    for idx, src in enumerate(sources):
        if not isinstance(src, dict):
            raise IntelSourcesError("sources[%d] 不是对象：%s" % (idx, where))
        missing = [k for k in REQUIRED_SOURCE_FIELDS if k not in src]
        if missing:
            raise IntelSourcesError("sources[%d] 缺字段 %s：%s"
                                    % (idx, ",".join(missing), where))
        sid = src.get("source_id")
        if not isinstance(sid, str) or not sid.strip():
            raise IntelSourcesError("sources[%d].source_id 非法：%s" % (idx, where))
        if sid in seen:
            raise IntelSourcesError("source_id 重复：%s（%s）" % (sid, where))
        seen.add(sid)
        if src.get("mode") not in MODES:
            raise IntelSourcesError("来源 %s mode 非法（可选 %s）：%s"
                                    % (sid, "/".join(MODES), where))
        if "enabled" in src and not isinstance(src["enabled"], bool):
            raise IntelSourcesError("来源 %s enabled 必须是 bool：%s" % (sid, where))
        for field in ("name", "kind", "authorization", "sample_path", "owner"):
            if not isinstance(src.get(field), str) or not src[field].strip():
                raise IntelSourcesError("来源 %s 字段 %s 必须是非空字符串：%s"
                                        % (sid, field, where))
        if not (src.get("last_sample") is None or isinstance(src.get("last_sample"), str)):
            raise IntelSourcesError("来源 %s last_sample 必须是 null 或 ISO 字符串：%s"
                                    % (sid, where))
    return data


def load_registry(path=None):
    """→ (registry dict, 实际路径)。只读，不创建/不回写任何文件。"""
    target = Path(path) if path else DEFAULT_REGISTRY
    return validate_registry(_read_json(target), target), target


def source_index(registry_filled: dict) -> dict:
    return {src["source_id"]: src for src in registry_filled["sources"]}


def get_source(registry_filled: dict, source_id: str) -> dict:
    hit = source_index(registry_filled).get(source_id)
    if hit is None:
        known = ", ".join(sorted(source_index(registry_filled)))
        raise IntelSourcesError("未知来源 %r（已登记：%s）" % (source_id, known))
    return hit


# ── 样本落点状态（只读 stat，不遍历内容）────────────────────
def path_stats(path_value) -> dict:
    """→ {exists, type, files_total, files_nonempty, newest_nonempty, error}。"""
    out = {"exists": False, "type": "missing", "files_total": 0,
           "files_nonempty": 0, "newest_nonempty": None, "error": None}
    try:
        path = Path(path_value)
        if not path.exists():
            return out
        out["exists"] = True
        if path.is_file():
            out["type"] = "file"
            out["files_total"] = 1
            if path.stat().st_size > 0:
                out["files_nonempty"] = 1
                out["newest_nonempty"] = _fmt_mtime(path.stat().st_mtime)
            return out
        out["type"] = "dir"
        newest = None
        for item in path.rglob("*"):
            if not item.is_file():
                continue
            out["files_total"] += 1
            try:
                size = item.stat().st_size
                mtime = item.stat().st_mtime
            except OSError:
                continue
            if size > 0:
                out["files_nonempty"] += 1
                if newest is None or mtime > newest:
                    newest = mtime
        if newest is not None:
            out["newest_nonempty"] = _fmt_mtime(newest)
        return out
    except OSError as ex:
        out["error"] = "%s: %s" % (type(ex).__name__, ex)
        return out


def _fmt_mtime(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


# ── --list：只读展示 ────────────────────────────────────────
def list_sources(registry_filled: dict, registry_path) -> dict:
    sources = []
    for src in registry_filled["sources"]:
        row = dict(src)
        row["sample_path_status"] = path_stats(src["sample_path"])
        sources.append(row)
    return {
        "schema": SCHEMA_LIST,
        "ok": True,
        "readonly": True,
        "network": False,
        "registry": str(registry_path),
        "phase": registry_filled.get("phase"),
        "policy": registry_filled.get("policy") or {},
        "source_count": len(sources),
        "sources": sources,
    }


def format_list_text(payload: dict) -> str:
    lines = [
        "情报来源登记（%s）" % (payload.get("phase") or "Phase 2"),
        "登记表：%s" % payload["registry"],
        "口径：登记≠接入、采样≠发布；本工具只读，不联网、不写库、不新增服务。",
        "",
    ]
    for src in payload["sources"]:
        st = src.get("sample_path_status") or {}
        if not st.get("exists"):
            sample_state = "MISSING（fail-visible）"
        elif st.get("type") == "dir":
            sample_state = "dir · 非空文件 %d/%d · 最新 %s" % (
                st.get("files_nonempty", 0), st.get("files_total", 0),
                st.get("newest_nonempty") or "-")
        else:
            sample_state = "file · 最新 %s" % (st.get("newest_nonempty") or "-")
        lines.append("- %s ｜%s ｜kind=%s ｜mode=%s ｜enabled=%s ｜full_ingest=%s ｜trust=%s"
                     % (src["source_id"], src["name"], src["kind"], src["mode"],
                        src.get("enabled"), src.get("full_ingest", "-"), src["trust"]))
        lines.append("    sample_path: %s" % src["sample_path"])
        lines.append("    样本状态: %s" % sample_state)
        lines.append("    last_sample: %s ｜ owner: %s"
                     % (src.get("last_sample"), src["owner"]))
    return "\n".join(lines)


# ── --sample：只读采样已有落盘数据 ──────────────────────────
def _limit_arg(value: str) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("limit 必须是 1..%d 的整数" % MAX_LIMIT)
    if n < 1 or n > MAX_LIMIT:
        raise argparse.ArgumentTypeError("limit 越界：%d（允许 1..%d）" % (n, MAX_LIMIT))
    return n


def _sample_files(path: Path, sample_format: str) -> list:
    if path.is_file():
        return [path]
    suffix = {"jsonl": ".jsonl", "json": ".json"}.get(sample_format)
    if suffix is None:
        raise IntelSourcesError("目录采样仅支持 jsonl/json 格式（当前 %r）：%s"
                                % (sample_format, path))
    files = [p for p in path.rglob("*") if p.is_file() and p.suffix.lower() == suffix]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def _read_raw_records(path: Path, sample_format: str):
    """读单文件 → [(定位串, 记录), ...]；坏行/空行计数由调用方处理。"""
    rows = []
    invalid = 0
    if sample_format == "jsonl":
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    rows.append((lineno, json.loads(line)))
                except ValueError:
                    invalid += 1
        return rows, invalid
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return [], 1
    if isinstance(data, list):
        rows = [(idx, item) for idx, item in enumerate(data)]
    else:
        rows = [(0, data)]
    return rows, invalid


def sample_source(registry_filled: dict, source_id: str, limit: int = DEFAULT_LIMIT) -> dict:
    """只读采样：只返回 sample_path 下已有落盘记录，最多 limit 条。"""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > MAX_LIMIT:
        raise IntelSourcesError("limit 越界：%r（允许 1..%d）" % (limit, MAX_LIMIT))
    src = get_source(registry_filled, source_id)
    if src["mode"] != "sampling":
        raise NotSamplingError(
            "来源 %s 为 %s（仅登记，不采样）；--sample 只对 mode=sampling 生效"
            % (source_id, src["mode"]))
    if src.get("enabled") is not True:
        raise NotSamplingError(
            "来源 %s 未启用采样（enabled=%s；full_ingest=%s）。"
            "Phase 2 仅登记，不采样、不接入。"
            % (source_id, src.get("enabled"), src.get("full_ingest", "-")))
    path = Path(src["sample_path"])
    if not path.exists():
        raise NoSampleError(
            "样本路径不存在：%s（source=%s）。fail-visible：不联网补采、不造样本。"
            % (path, source_id))
    sample_format = str(src.get("sample_format") or
                        ("jsonl" if path.suffix.lower() == ".jsonl" else "json"))
    try:
        files = _sample_files(path, sample_format)
    except OSError as ex:
        raise NoSampleError("样本路径不可读：%s（%s）" % (path, ex)) from ex

    records = []
    files_scanned = 0
    files_skipped_empty = 0
    lines_skipped_invalid = 0
    for file_path in files:
        if len(records) >= limit:
            break
        try:
            if file_path.stat().st_size == 0:
                files_skipped_empty += 1
                continue
            rows, invalid = _read_raw_records(file_path, sample_format)
        except OSError:
            continue
        files_scanned += 1
        lines_skipped_invalid += invalid
        try:
            label = file_path.name if path.is_file() else str(file_path.relative_to(path))
        except ValueError:
            label = str(file_path)
        for line_no, record in rows:
            if len(records) >= limit:
                break
            records.append({"file": label, "line": line_no, "record": record})

    if not records:
        raise NoSampleError(
            "样本路径存在但无可用落盘记录：%s（扫描文件 %d、跳过空文件 %d、坏行 %d）。"
            "fail-visible：不联网补采、不造样本。"
            % (path, files_scanned, files_skipped_empty, lines_skipped_invalid))

    return {
        "schema": SCHEMA_SAMPLE,
        "ok": True,
        "readonly": True,
        "network": False,
        "source_id": source_id,
        "name": src["name"],
        "mode": src["mode"],
        "sample_path": str(path),
        "sample_format": sample_format,
        "sampled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "limit": limit,
        "returned": len(records),
        "files_scanned": files_scanned,
        "files_skipped_empty": files_skipped_empty,
        "lines_skipped_invalid": lines_skipped_invalid,
        "records": records,
        "note": "只读采样：不联网、不写文件/库、不新增服务、不回写登记表；采样≠发布。",
    }


def format_sample_text(payload: dict) -> str:
    lines = [
        "采样 %s（%s）→ 返回 %d/%d 条（只读；采样≠发布）"
        % (payload["source_id"], payload["name"], payload["returned"], payload["limit"]),
        "样本路径：%s（格式 %s）" % (payload["sample_path"], payload["sample_format"]),
        "扫描文件 %d、跳过空文件 %d、跳过坏行 %d、采样时间 %s"
        % (payload["files_scanned"], payload["files_skipped_empty"],
           payload["lines_skipped_invalid"], payload["sampled_at"]),
    ]
    for idx, item in enumerate(payload["records"], start=1):
        lines.append("--- record %d [%s:%s] ---"
                     % (idx, item.get("file"), item.get("line")))
        lines.append(json.dumps(item.get("record"), ensure_ascii=False))
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="情报来源登记只读查询/只读采样（Phase 2：登记≠接入、采样≠发布）")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true",
                        help="只读展示来源登记表（含样本落点是否存在，不联网）")
    action.add_argument("--sample", metavar="SOURCE_ID",
                        help="只读采样该来源 sample_path 下已有落盘数据")
    parser.add_argument("--limit", type=_limit_arg, default=DEFAULT_LIMIT,
                        help="采样条数上限（1..%d，默认 %d）" % (MAX_LIMIT, DEFAULT_LIMIT))
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--registry", default=None,
                        help="登记表路径（默认 agent_platform/data/intel/sources.json，"
                             "测试注入用；仍只读）")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        registry, registry_path = load_registry(args.registry)
        if args.list:
            payload = list_sources(registry, registry_path)
            print(json.dumps(payload, ensure_ascii=False, indent=1)
                  if args.json else format_list_text(payload))
            return EXIT_OK
        payload = sample_source(registry, args.sample, args.limit)
        print(json.dumps(payload, ensure_ascii=False, indent=1)
              if args.json else format_sample_text(payload))
        return EXIT_OK
    except IntelSourcesError as ex:
        print("FAIL-VISIBLE: %s" % ex, file=sys.stderr)
        return ex.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
