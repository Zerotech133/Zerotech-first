# -*- coding: utf-8 -*-
"""v1.1 M0 批前只读预检器（不执行 M0、不写 contract/配置/代码）。

用途：在 ARCH1 v1.1 C 条件批（G0）通过后、正式执行 M0（S0–S6）**之前**，
只读核对当前仓内状态与 M0 执行清单的差距，输出 PASS/PARTIAL/FAIL +
建议先做哪一步（S0–S6）+ 机器可读 JSON + 人类摘要 + 退出码（0/1/2）。

检查项（全部只读）：
  C0 证据基线：`work/audit-20261101/v11_m0/manifest.json` 是否已建（S0/S5 产物）
  C1 冻结契约：`docs/dev/legion_screen_contract.json`——schema/status/version/
     endpoint/readonly + response 7 key 完整性（v1.0 frozen）
  C2 live 可用性：8734 `/api/dev/legion` 只读 GET（或 `--live-snapshot` 快照）；
     不可达/超时 → 降级 PARTIAL（不 500、不重试、不写盘）
  C3 key 漂移：冻结 response（7 key）vs live 顶层（16 key）——冻结 key 必须仍在
     live（v1 不回退）；live 多出的 key 属 v1.1 新增未入契约 → 建议 S2 only-add
  C4 字段白名单：`docs/dev/dev_legion_v11_fields.json`（S1 产物；不存在→PARTIAL）
     校验 allowed/banned/k_anon=5/max_rows=5000/cache_ttl_s<=60/log_no_raw_rows/
     source_label
  C5 adapters 冻结：`portal/api_dev_legion.py` / `dev_legion_quant_real.py` /
     `dev_legion_risk_real.py` 的 sha256 与 M0 基线前缀一致性（只锁不改→S3）
  C6 ADR 编号：`docs/adr/ADR-*.md` 解析——下一可用编号应为 **ADR-015**；
     ADR-009 需追加 v1.1 optional_extensions 节；重号（ADR-009×2）记为治理债
  C7 文档漂移：`docs/dev/dev_legion_v11.md` 是否仍写“待重启”（应更正，S5）

出口：0=PASS（全部通过，可进入 S0）；1=PARTIAL（有需先做/待批项）；
2=FAIL（阻断：契约缺失/坏、编号被占、adapter 缺失、白名单 JSON 坏）。

边界：只读仓内文件 + 可选只读 HTTP GET；**不写任何文件**、不执行 M0、
不改 contract/配置/代码、不重启服务。live 响应含 generated_at，预检不落盘。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPORT_SCHEMA = "v11_m0_preflight/v1"
DEFAULT_LIVE_URL = "http://127.0.0.1:8734/api/dev/legion"
DEFAULT_EXPECTED_ADR = 15  # M0 清单 §三：ADR-001~014 已占 → 下一可用 ADR-015
CONTRACT_SCHEMA = "legion_screen_contract/v1"
CONTRACT_FROZEN_KEYS = ("generated_at", "department", "seats",
                        "work_in_progress", "metrics", "redlines", "sources")
LIVE_REQUIRED_KEYS = CONTRACT_FROZEN_KEYS
FROZEN_CONTRACT_STATUS = ("frozen", "legion_screen_contract/v1", "1.0")
FIELDS_REQUIRED = ("allowed", "banned", "k_anon", "max_rows", "cache_ttl_s",
                   "log_no_raw_rows", "source_label")
FIELDS_EXPECT = {"k_anon": 5, "max_rows": 5000}
FIELDS_BANNED_MUST_CONTAIN = ("symbol", "name", "price", "target_price",
                              "rationale", "trace_id", "owner", "recommendation")
DEFAULT_ADAPTERS = (
    ("portal/api_dev_legion.py", "432890ac"),
    ("portal/dev_legion_quant_real.py", "4dd7a4cd"),
    ("portal/dev_legion_risk_real.py", "d644ab8d"),
)
DEFAULT_ADR_APPEND_MARKERS = ("v1.1", "optional_extensions", "optional extensions")
STATUS_ORDER = {"PASS": 0, "SKIP": 0, "PARTIAL": 1, "FAIL": 2}
STEP_ORDER = ("S0", "S1", "S2", "S3", "S4", "S5", "S6")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path):
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, "JSON 解析失败：%s" % exc


def _check(check_id, name):
    return {"id": check_id, "name": name, "status": "PASS", "findings": [],
            "evidence": {}}


def _finding(check, level, code, detail, steps=()):
    check["findings"].append({"level": level, "code": code, "detail": detail,
                              "suggest_steps": list(steps)})
    if level == "FAIL" or STATUS_ORDER.get(level, 0) > \
            STATUS_ORDER.get(check["status"], 0):
        check["status"] = level


# ---------------------------------------------------------------------------
# C0 证据基线
# ---------------------------------------------------------------------------
def check_baseline(root):
    check = _check("C0", "证据基线（S0/S5 产物）")
    manifest = Path(root) / "work" / "audit-20261101" / "v11_m0" / "manifest.json"
    check["evidence"]["manifest_path"] = str(manifest)
    if manifest.exists():
        check["evidence"]["manifest_exists"] = True
        check["evidence"]["manifest_sha256"] = _sha256(manifest)
    else:
        check["evidence"]["manifest_exists"] = False
        _finding(check, "PARTIAL", "baseline_missing",
                 "M0 证据基线 manifest 未建立（work/audit-20261101/v11_m0/）",
                 ("S0", "S5"))
    return check


# ---------------------------------------------------------------------------
# C1 冻结契约
# ---------------------------------------------------------------------------
def check_contract(path):
    check = _check("C1", "v1.0 冻结契约（7 key）")
    contract, error = _read_json(path)
    check["evidence"]["path"] = str(path)
    check["evidence"]["exists"] = Path(path).exists()
    if error:
        _finding(check, "FAIL", "contract_unreadable",
                 "冻结契约不可读：%s" % error, ("S0",))
        return check
    if not isinstance(contract, dict):
        _finding(check, "FAIL", "contract_invalid", "冻结契约须为 JSON 对象",
                 ("S0",))
        return check
    schema = contract.get("schema")
    status = contract.get("status")
    version = contract.get("version")
    endpoint = contract.get("endpoint")
    readonly = contract.get("readonly")
    check["evidence"].update({"schema": schema, "status": status,
                              "version": version, "endpoint": endpoint,
                              "readonly": readonly})
    if schema != CONTRACT_SCHEMA:
        _finding(check, "FAIL", "schema_mismatch",
                 "schema=%r，期望 %r" % (schema, CONTRACT_SCHEMA), ("S2",))
    if status != "frozen":
        _finding(check, "FAIL", "status_not_frozen",
                 "status=%r，M0 预检要求 frozen（只增不改）" % status, ("S2",))
    if str(version) != "1.0":
        _finding(check, "PARTIAL", "version_drift",
                 "version=%r，基线为 1.0（变更为 only-add 时在 optional_extensions）"
                 % version, ("S2",))
    if endpoint != "/api/dev/legion":
        _finding(check, "FAIL", "endpoint_mismatch",
                 "endpoint=%r，期望 /api/dev/legion" % endpoint, ("S2",))
    if readonly is not True:
        _finding(check, "FAIL", "readonly_not_true",
                 "readonly=%r，只读契约必须为 true" % readonly, ("S2",))
    response = contract.get("response")
    if not isinstance(response, dict):
        _finding(check, "FAIL", "response_missing",
                 "response 段缺失/非对象", ("S2",))
    else:
        contract_keys = sorted(response.keys())
        check["evidence"]["response_keys"] = contract_keys
        check["evidence"]["response_key_count"] = len(contract_keys)
        missing = [key for key in CONTRACT_FROZEN_KEYS
                   if key not in response]
        if missing:
            if len(missing) == len(CONTRACT_FROZEN_KEYS):
                _finding(check, "FAIL", "frozen_keys_missing",
                         "冻结 7 key 全缺：%s" % ",".join(missing), ("S2",))
            else:
                _finding(check, "PARTIAL", "frozen_keys_partial",
                         "冻结 key 缺：%s" % ",".join(missing), ("S2",))
        else:
            check["evidence"]["frozen_keys_ok"] = True
    return check


# ---------------------------------------------------------------------------
# C2 live 可用性 / C3 key 漂移
# ---------------------------------------------------------------------------
def fetch_live(url, timeout=3.0):
    """只读 GET live payload；返回 (payload, error)。任何异常都降级返回。"""
    if not url:
        return None, "no_url"
    try:
        request = urllib.request.Request(str(url), method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = getattr(response, "status", 200)
            if int(status) != 200:
                return None, "http_%s" % status
            payload = json.loads(body)
            if not isinstance(payload, dict):
                return None, "payload_not_object"
            return payload, None
    except urllib.error.HTTPError as exc:
        return None, "http_%s" % exc.code
    except (urllib.error.URLError, OSError) as exc:
        return None, "unreachable(%s)" % (getattr(exc, "reason", exc),)
    except ValueError as exc:
        return None, "bad_json(%s)" % exc


def resolve_live(args):
    """返回 (payload, source, error)。--live-snapshot 优先（离线确定性）。"""
    if args.live_snapshot:
        payload, error = _read_json(args.live_snapshot)
        if error:
            return None, "snapshot", error
        return payload, "snapshot", None
    if args.offline:
        return None, "offline", "offline（未取 live，按降级处理）"
    payload, error = fetch_live(args.live_url, args.timeout)
    return payload, "live", error


def check_live(live_payload, source, error):
    check = _check("C2", "live payload 可用性（8734 只读 GET / 快照）")
    check["evidence"]["source"] = source
    if live_payload is None:
        _finding(check, "PARTIAL", "live_unavailable",
                 "live 不可达/未提供（%s）→ 降级：key 漂移检查跳过，"
                 "批后需补只读复核" % (error or "unknown"), ("S0", "S5"))
        check["evidence"]["degraded"] = True
        return check
    check["evidence"]["degraded"] = False
    check["evidence"]["top_key_count"] = len(live_payload)
    check["evidence"]["top_keys"] = sorted(live_payload.keys())
    check["evidence"]["schema"] = live_payload.get("schema")
    check["evidence"]["readonly"] = live_payload.get("readonly")
    if live_payload.get("schema") != CONTRACT_SCHEMA:
        _finding(check, "FAIL", "live_schema_mismatch",
                 "live schema=%r，v1 客户端会回归" % live_payload.get("schema"),
                 ("S2",))
    if live_payload.get("readonly") is not True:
        _finding(check, "FAIL", "live_readonly_not_true",
                 "live readonly=%r（只读面被打破）" % live_payload.get("readonly"),
                 ("S2",))
    return check


def check_key_drift(contract_check, live_check, live_payload):
    check = _check("C3", "契约 key 漂移（frozen 7 vs live 16）")
    if live_payload is None:
        check["status"] = "SKIP"
        _finding(check, "SKIP", "live_unavailable",
                 "live 不可达，key 漂移无法核对（C2 已降级）", ("S0",))
        return check
    frozen = list((contract_check.get("evidence") or {}).get("response_keys")
                  or [])
    live_keys = sorted(live_payload.keys())
    frozen_set, live_set = set(frozen), set(live_keys)
    extra = sorted(live_set - frozen_set)      # live 有、契约未声明（v1.1 新增）
    missing = sorted(frozen_set - live_set)    # 契约声明、live 缺失（v1 回退）
    check["evidence"].update({
        "frozen_key_count": len(frozen), "live_key_count": len(live_keys),
        "live_extra_keys": extra, "frozen_missing_in_live": missing,
    })
    if not frozen:
        _finding(check, "FAIL", "contract_keys_unavailable",
                 "契约 response key 未取到（C1 未通过），无法计算漂移", ("S0",))
        return check
    if missing:
        _finding(check, "FAIL", "frozen_key_regression",
                 "冻结 key 在 live 缺失（v1 回退）：%s" % ",".join(missing),
                 ("S2", "R4"))
    if extra:
        _finding(check, "PARTIAL", "live_extra_keys_uncontracted",
                 "live 顶层多出 %d 个未入契约 key：%s → M0 以 only-add 追平"
                 % (len(extra), ",".join(extra)), ("S2",))
    if not extra and not missing:
        check["evidence"]["key_drift"] = "none"
    return check


# ---------------------------------------------------------------------------
# C4 字段白名单
# ---------------------------------------------------------------------------
def check_fields(path):
    check = _check("C4", "字段白名单 lock（S1 产物）")
    check["evidence"]["path"] = str(path)
    if not Path(path).exists():
        _finding(check, "PARTIAL", "fields_missing",
                 "字段白名单未建立：%s（S1 待做）" % path, ("S1",))
        return check
    doc, error = _read_json(path)
    if error:
        _finding(check, "FAIL", "fields_invalid_json",
                 "字段白名单 JSON 不可解析：%s" % error, ("S1",))
        return check
    if not isinstance(doc, dict):
        _finding(check, "FAIL", "fields_not_object", "字段白名单须为 JSON 对象",
                 ("S1",))
        return check
    missing = [key for key in FIELDS_REQUIRED if key not in doc]
    if missing:
        _finding(check, "PARTIAL", "fields_required_missing",
                 "白名单缺字段：%s" % ",".join(missing), ("S1",))
    banned = doc.get("banned") or []
    if not isinstance(banned, list):
        _finding(check, "FAIL", "banned_not_list", "banned 须为数组", ("S1",))
        banned = []
    banned_text = " ".join(str(item) for item in banned).lower()
    banned_missing = [name for name in FIELDS_BANNED_MUST_CONTAIN
                      if name not in banned_text]
    if banned_missing:
        _finding(check, "PARTIAL", "banned_incomplete",
                 "禁止集缺：%s（按白名单方案 §一/§二）" % ",".join(banned_missing),
                 ("S1",))
    if doc.get("k_anon") != FIELDS_EXPECT["k_anon"]:
        _finding(check, "PARTIAL", "k_anon_mismatch",
                 "k_anon=%r，期望 %r" % (doc.get("k_anon"),
                                       FIELDS_EXPECT["k_anon"]), ("S1",))
    if doc.get("max_rows") != FIELDS_EXPECT["max_rows"]:
        _finding(check, "PARTIAL", "max_rows_mismatch",
                 "max_rows=%r，期望 %r" % (doc.get("max_rows"),
                                         FIELDS_EXPECT["max_rows"]), ("S1",))
    ttl = doc.get("cache_ttl_s")
    if not isinstance(ttl, (int, float)) or float(ttl) > 60:
        _finding(check, "PARTIAL", "cache_ttl_invalid",
                 "cache_ttl_s=%r，要求 <=60" % ttl, ("S1",))
    if doc.get("log_no_raw_rows") is not True:
        _finding(check, "PARTIAL", "log_no_raw_rows_false",
                 "log_no_raw_rows=%r，要求 true（不落原始行）"
                 % doc.get("log_no_raw_rows"), ("S1",))
    source_label = str(doc.get("source_label") or "").lower()
    if not source_label or not any(tag in source_label
                                   for tag in ("as_of", "source")):
        _finding(check, "PARTIAL", "source_label_invalid",
                 "source_label 需含 as_of/source（现 %r）"
                 % doc.get("source_label"), ("S1",))
    check["evidence"]["keys"] = sorted(doc.keys())
    return check


# ---------------------------------------------------------------------------
# C5 adapters 冻结（只锁不改）
# ---------------------------------------------------------------------------
def parse_adapter_specs(specs, root):
    parsed = []
    for item in specs or []:
        if "=" in item:
            rel, prefix = item.split("=", 1)
        else:
            rel, prefix = item, ""
        path = Path(rel)
        if not path.is_absolute():
            path = Path(root) / rel
        parsed.append((rel, path, prefix.strip().lower()))
    return parsed


def check_adapters(root, specs):
    check = _check("C5", "adapters 文件 hash（只锁不改）")
    rows = []
    for rel, path, expected in specs:
        row = {"path": rel, "exists": path.exists()}
        if not path.exists():
            row["status"] = "missing"
            _finding(check, "FAIL", "adapter_missing",
                     "adapter 文件缺失：%s" % rel, ("S3", "R1"))
        else:
            digest = _sha256(path)
            row.update({"sha256": digest, "size": path.stat().st_size,
                        "expected_prefix": expected,
                        "status": "ok"})
            if expected and not digest.startswith(expected):
                row["status"] = "drift"
                _finding(check, "PARTIAL", "adapter_hash_drift",
                         "%s hash=%s… 与 M0 基线 %s… 不一致（只锁不改前需复核）"
                         % (rel, digest[:12], expected), ("S3",))
        rows.append(row)
    check["evidence"]["adapters"] = rows
    return check


# ---------------------------------------------------------------------------
# C6 ADR 编号 / C7 文档漂移
# ---------------------------------------------------------------------------
def scan_adr(adr_dir):
    """返回 (numbers→files, duplicates)。"""
    numbers, files = {}, []
    for path in sorted(Path(adr_dir).glob("ADR-*.md")):
        name = path.name
        try:
            number = int(name.split("-")[1])
        except (IndexError, ValueError):
            continue
        numbers.setdefault(number, []).append(path)
        files.append(path)
    return numbers, files


def check_adr(adr_dir, expected_number=DEFAULT_EXPECTED_ADR):
    check = _check("C6", "ADR 编号与 ADR-009 追加（G2/S2）")
    numbers, files = scan_adr(adr_dir)
    check["evidence"]["adr_dir"] = str(adr_dir)
    check["evidence"]["file_count"] = len(files)
    if not files:
        _finding(check, "FAIL", "adr_dir_empty",
                 "ADR 目录无 ADR-*.md（%s）" % adr_dir, ("S2",))
        return check
    used = sorted(numbers.keys())
    next_available = (max(used) + 1) if used else 1
    duplicates = {str(num): [p.name for p in paths]
                  for num, paths in numbers.items() if len(paths) > 1}
    check["evidence"].update({"used_numbers": used,
                              "next_available": next_available,
                              "expected": int(expected_number),
                              "duplicates": duplicates})
    if duplicates:
        _finding(check, "PARTIAL", "adr_duplicate_numbers",
                 "ADR 重号（既有治理债，M0 不扩面）：%s" % duplicates, ("S2",))
    if int(expected_number) in numbers:
        _finding(check, "FAIL", "expected_adr_occupied",
                 "期望编号 ADR-%03d 已被占用：%s → 需 ARCH1 重新确认编号"
                 % (int(expected_number),
                    [p.name for p in numbers[int(expected_number)]]),
                 ("S2",))
    elif next_available != int(expected_number):
        _finding(check, "PARTIAL", "expected_adr_mismatch",
                 "下一可用 ADR-%03d ≠ 建议 ADR-%03d → 需 ARCH1 书面确认"
                 % (next_available, int(expected_number)), ("S2",))
    # ADR-009 追加要求（v1.1 optional_extensions 节）
    adr009_files = numbers.get(9, [])
    if not adr009_files:
        _finding(check, "PARTIAL", "adr009_missing",
                 "未找到 ADR-009（应在 docs/adr/ADR-009-*）", ("S2",))
    else:
        appended = []
        for path in adr009_files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            if any(marker.lower() in text.lower()
                   for marker in DEFAULT_ADR_APPEND_MARKERS):
                appended.append(path.name)
        check["evidence"]["adr009_files"] = [p.name for p in adr009_files]
        check["evidence"]["adr009_v11_appended"] = appended
        if not appended:
            _finding(check, "PARTIAL", "adr009_append_missing",
                     "ADR-009 尚未追加 v1.1 optional_extensions 记录（S2）",
                     ("S2",))
    return check


def check_docs(root):
    check = _check("C7", "文档漂移（dev_legion_v11.md）")
    path = Path(root) / "docs" / "dev" / "dev_legion_v11.md"
    check["evidence"]["path"] = str(path)
    if not path.exists():
        _finding(check, "PARTIAL", "v11_doc_missing",
                 "docs/dev/dev_legion_v11.md 不存在（DOC1 需补）", ("S5",))
        return check
    text = path.read_text(encoding="utf-8", errors="replace")
    stale_markers = ("待重启", "重启生效")
    stale = any(marker in text for marker in stale_markers) and \
        "已生效" not in text
    check["evidence"]["has_stale_waitrestart"] = stale
    check["evidence"]["mentions_yi_sheng_xiao"] = "已生效" in text
    if stale:
        _finding(check, "PARTIAL", "v11_doc_waitrestart",
                 "dev_legion_v11.md 仍写“待重启/重启生效”（应为“已生效”+M0 "
                 "冻结说明）", ("S5",))
    return check


# ---------------------------------------------------------------------------
# 汇总 / 建议 S0–S6 / 渲染
# ---------------------------------------------------------------------------
def summarize(checks):
    statuses = [check["status"] for check in checks]
    if "FAIL" in statuses:
        status = "FAIL"
    elif "PARTIAL" in statuses or "SKIP" in statuses:
        status = "PARTIAL"
    else:
        status = "PASS"
    steps = set()
    for check in checks:
        for finding in check["findings"]:
            steps.update(finding.get("suggest_steps") or [])
    suggested = [step for step in STEP_ORDER if step in steps]
    exit_code = {"PASS": 0, "PARTIAL": 1, "FAIL": 2}[status]
    return {"status": status, "exit_code": exit_code,
            "suggested_steps": suggested,
            "next_step": suggested[0] if suggested else "S6",
            "readonly": True}


def build_report(args):
    root = Path(args.root).resolve()
    contract_path = Path(args.contract)
    if not contract_path.is_absolute():
        contract_path = root / contract_path
    fields_path = Path(args.fields)
    if not fields_path.is_absolute():
        fields_path = root / fields_path
    adr_dir = Path(args.adr_dir)
    if not adr_dir.is_absolute():
        adr_dir = root / adr_dir
    specs = parse_adapter_specs(args.adapter or
                                ["%s=%s" % item for item in DEFAULT_ADAPTERS],
                                root)
    contract_check = check_contract(contract_path)
    live_payload, live_source, live_error = resolve_live(args)
    live_check = check_live(live_payload, live_source, live_error)
    checks = [
        check_baseline(root),
        contract_check,
        live_check,
        check_key_drift(contract_check, live_check, live_payload),
        check_fields(fields_path),
        check_adapters(root, specs),
        check_adr(adr_dir, args.expected_adr),
        check_docs(root),
    ]
    summary = summarize(checks)
    return {
        "schema": REPORT_SCHEMA,
        "readonly": True,
        "not_executing_m0": True,
        "root": str(root),
        "inputs": {
            "contract": str(contract_path),
            "fields": str(fields_path),
            "adr_dir": str(adr_dir),
            "live_source": live_source,
            "live_url": (args.live_url if not args.offline
                         and not args.live_snapshot else None),
            "live_snapshot": args.live_snapshot,
            "expected_adr": int(args.expected_adr),
        },
        "summary": summary,
        "status": summary["status"],
        "exit_code": summary["exit_code"],
        "suggested_steps": summary["suggested_steps"],
        "next_step": summary["next_step"],
        "checks": checks,
        "notes": [
            "只读预检：未执行 M0、未写 contract/配置/代码、未重启服务。",
            "G0（ARCH1 C 条件批）未回时只可跑本预检，不得执行 S0–S6。",
            "live 响应含 generated_at，本预检不落盘、不做冻结证据。",
        ],
    }


def render_summary(report):
    lines = ["[v11_m0_preflight] 状态=%s（退出码 %d）· 只读预检，不执行 M0"
             % (report["status"], report["exit_code"])]
    for check in report["checks"]:
        lines.append("  %-3s %-9s %s"
                     % (check["id"], check["status"], check["name"]))
        for finding in check["findings"]:
            lines.append("        - [%s] %s | 建议步=%s"
                         % (finding["level"], finding["detail"],
                            ",".join(finding["suggest_steps"]) or "-"))
    lines.append("  建议先做：%s（next=%s）"
                 % (",".join(report["suggested_steps"]) or "-",
                    report["next_step"]))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="v1.1 M0 批前只读预检（不执行 M0、不写文件）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]),
                    help="仓库根（默认工具所在仓库）")
    ap.add_argument("--contract", default="docs/dev/legion_screen_contract.json")
    ap.add_argument("--fields", default="docs/dev/dev_legion_v11_fields.json")
    ap.add_argument("--adr-dir", default="docs/adr")
    ap.add_argument("--expected-adr", type=int, default=DEFAULT_EXPECTED_ADR)
    ap.add_argument("--adapter", action="append", default=None,
                    help="adapter 路径[=期望 sha256 前缀]，可重复；默认 M0 基线三件")
    ap.add_argument("--live-url", default=DEFAULT_LIVE_URL)
    ap.add_argument("--live-snapshot", default=None,
                    help="离线 payload JSON（优先于 --live-url）")
    ap.add_argument("--offline", action="store_true",
                    help="不取 live（按降级 PARTIAL 处理）")
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = ap.parse_args(argv)
    report = build_report(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(render_summary(report))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
