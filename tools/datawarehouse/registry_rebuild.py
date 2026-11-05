# -*- coding: utf-8 -*-
"""warehouse_registry v2 重建：按 catalog v12 全量对齐物理表资格台账。

背景（2026-10-27 DATA1 首轮数据仓治理）：
- catalog.json = v12 / 245 表（updated 2026-10-27T06:23:31），是**物理表现况真源**；
- warehouse_registry.json 停留在 2026-10-09（83 physical / 92 display，catalog_sha256 不匹配）；
- 本工具把 registry 重建为「catalog 全量 245 表」的资格台账：
  * 旧 83 条 physical 条目**逐字段原样保留**（历史证据不许机械覆盖）；
  * catalog 新增的 162 表自动补登（qualification_status=unknown，资格待取证）；
  * display_assets 原样保留（不增不删；2026-11-02 起有补登记批次，条数以文件内 display_count 为准）。

用法：
    python tools/datawarehouse/registry_rebuild.py --dry-run          # 默认，只看前后差异
    python tools/datawarehouse/registry_rebuild.py --dry-run --json
    python tools/datawarehouse/registry_rebuild.py --apply           # 执行（写主仓数据根）

--apply 纪律（AGENTS.md §3 数据根互斥）：
    1. 先读 C:\\Temp\\kg_sync_task.log 尾部，有异常/进行中操作即拒跑（exit 2）；
    2. 备份旧文件为同目录 warehouse_registry.json.bak-<YYYYmmdd-HHMMSS>；
    3. 原子写（tmp + os.replace）；
    4. 重读校验：catalog_sha256 一致 + JSON 合法 + 计数符合预期；
    5. 向 kg_sync_task.log 追加 registry_rebuild 流水行。

退出码：0=ok / 1=错误 / 2=前置条件未满足（kg 日志异常/进行中、catalog 不可读）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import logging

# ---- 常量：v2 自动补登口径 -------------------------------------------------
TZ_CN = timezone(timedelta(hours=8))
KG_LOG = Path(r"C:\Temp\kg_sync_task.log")
NEW_UNSUPPORTED_REASON = "来源资格待取证：registry v2 自动补登（2026-10-27）"
NEW_EVIDENCE_BASIS = "catalog v12 自动补登（2026-10-27），非采集/覆盖核验回执"
PENDING_FIELDS = ["market", "event_date_kind", "policy_evidence",
                  "calendar", "supported_scope", "verification_rule"]
# catalog.schema 中属于内部记账/来源溯源列，不是业务列
PROVENANCE_COLUMNS = {
    "_fetched_at", "actual_received_at", "source_fetched_at", "source_provider",
    "source_endpoint", "source_category", "source_market", "source_request_count",
    "source_start", "available_at", "source_body_sha256", "source_qualification",
}
LIMITATIONS_V2 = [
    "v2（2026-10-27）由 catalog v12 自动重建：physical_tables 覆盖 catalog 全部表，"
    "其中新增表为自动补登、qualification_status=unknown（资格仍待逐项取证）",
    "display_assets 沿用 2026-10-09 历史证据 + 2026-11-02 起补登记批次"
    "（条数以本文件 display_count 为准；新增为机械映射、待新看板逐项复核），不称当前实时资产清单",
]


def now_cn() -> datetime:
    return datetime.now(TZ_CN)


def load_catalog() -> dict:
    """读 catalog.json，校验 v12/245 表结构。"""
    cat = common.load_json(common.CATALOG)
    if not isinstance(cat, dict) or not isinstance(cat.get("tables"), dict):
        raise ValueError("catalog.json 不可读或结构异常")
    return cat


def load_registry() -> dict:
    """读现有 registry；缺失/损坏时返回空壳（重建仍可进行，但会少 backup）。"""
    reg = common.load_json(common.REGISTRY)
    return reg if isinstance(reg, dict) else {}


def derive_business_columns(entry: dict) -> list | None:
    """从 catalog.schema 推业务列：排除主键/日期/代码列与内部溯源列。"""
    schema = entry.get("schema")
    if not isinstance(schema, list) or not schema:
        return None
    drop = set(entry.get("primary_key") or [])
    for k in ("date_col", "code_col"):
        if entry.get(k):
            drop.add(entry[k])
    cols = [c for c in schema
            if c not in drop and c not in PROVENANCE_COLUMNS and not str(c).startswith("_")]
    return cols or None


# ---- kg 日志行判定（v2）----------------------------------------------------
# 带结论的行有两种形态：显式退出码（exit=N / rc=N）、独立成败标记（OK/FAIL/WARN）。
_RE_KG_EXIT = re.compile(r"\bexit=(\d+)\b")
_RE_KG_RC = re.compile(r"\brc=(\d+)\b")
_RE_KG_WORD = re.compile(r"(?<![A-Za-z])(OK|FAIL|WARN)(?![A-Za-z])")
# 无退出码但带异常痕迹的行同样算失败（旧口径的 bad_marks，不能因为改版就丢掉）
_RE_KG_ERROR = re.compile(r"(\berror=|ERROR\b|Traceback)")
# 辅助作业：会写自己的结论行，但不碰数据根（备份演练恢复的是快照副本）。
# 它们的失败**不阻塞** registry 重建，但要作为告警带出去，不许静默吞掉。
AUX_JOB_PATTERN = re.compile(r"^(b4_[a-z_]+|restore_drill)\b")


def _kg_verdict(line: str):
    """判定单行是否带操作结论。

    返回 True（成功）/ False（失败）/ None（注释行，无结论）。
    """
    code = None
    hit = _RE_KG_EXIT.search(line) or _RE_KG_RC.search(line)
    if hit:
        code = int(hit.group(1))
    word_hit = _RE_KG_WORD.search(line)
    word = word_hit.group(1) if word_hit else None
    if word in ("FAIL", "WARN"):
        return False
    if code is not None:
        return code == 0
    if word == "OK":
        return True
    if _RE_KG_ERROR.search(line):
        return False
    return None


def _kg_job(line: str) -> str:
    """取结论行的作业名（首 token），用于区分数据根作业与辅助作业。"""
    return line.split(None, 1)[0] if line.strip() else ""


def find_kg_log_state() -> dict:
    """读 kg_sync_task.log 尾部，判断是否有异常/进行中操作。

    返回 {"ok": bool, "last": str, "verdict": str, "skipped": int, "aux": list, "reason": str}。
    ``last`` 仍是物理最后一行（审计留痕用），``verdict`` 是实际据以判定的那行。

    判定规则 v2（2026-11-05 修订）：尾部 20 行里**从后往前找第一条带结论的行**
    （显式 ``exit=N`` / ``rc=N``，或独立的 OK/FAIL/WARN 标记），只看它成败；
    不带任何结论的行（备份演练 ``b4_restore_drill``、人工 ``manual-precheck``
    备注、``fx_fill ... dry=False`` 这类信息行）一律视为注释行跳过并计数。

    为什么改：旧规则要求"最后一行必须带 OK/exit=0"。日志里只要落一条非数据操作
    行（演练、手工备注），前置检查就永久判失败，直到下一次 kg_sync 成功；2026-11-05
    10:06 的 ``b4_restore_drill`` 把 09:51 那次 ``registry_align ... exit=0`` 盖住，
    表现为"数据明明对齐却拒跑"，只能靠人工往日志里补一行带 OK 的备注来解锁。

    仍然保守的部分：尾部 3 行出现进行中标记一律拒跑；整段存量里找不到结论行也拒跑。
    辅助作业（``b4_*`` 备份演练一类）的结论行不参与判定，但其失败会放进 ``aux``
    字段带出去——跳过不等于掩盖。
    """
    if not KG_LOG.is_file():
        return {"ok": False, "last": "", "verdict": "", "skipped": 0,
                "reason": "日志不存在：%s" % KG_LOG}
    try:
        lines = [ln.strip() for ln in KG_LOG.read_text(encoding="utf-8", errors="replace").splitlines()]
    except OSError as exc:
        return {"ok": False, "last": "", "verdict": "", "skipped": 0,
                "reason": "日志不可读：%s" % exc}
    tail = [ln for ln in lines if ln][-20:]
    if not tail:
        return {"ok": False, "last": "", "verdict": "", "skipped": 0, "reason": "日志为空"}
    last = tail[-1]
    busy_marks = ("in_progress", "进行中", "running", "started", "START", "开始")
    if any(m in last for m in busy_marks) or any(m in ln for ln in tail[-3:] for m in busy_marks):
        return {"ok": False, "last": last, "verdict": "", "skipped": 0,
                "reason": "日志尾部疑似有其他会话操作进行中"}

    # 先在整个窗口里收集辅助作业的失败——不能因为判定提前返回就漏报。
    # 反面教材：2026-11-05 10:27 那次 apply 在 b4_restore_drill(rc=1) 之后追加了一条
    # exit=0，倒序扫到它即返回，演练的 P1 就被"顺便"掩盖了。
    aux = [ln[:600] for ln in tail
           if AUX_JOB_PATTERN.match(_kg_job(ln)) and _kg_verdict(ln) is False]

    skipped = 0
    for line in reversed(tail):
        verdict = _kg_verdict(line)
        if verdict is None:
            skipped += 1
            continue
        if AUX_JOB_PATTERN.match(_kg_job(line)):
            # 辅助作业（备份演练等）：成败不参与数据根互斥判定，失败已在 aux 里
            skipped += 1
            continue
        if verdict:
            return {"ok": True, "last": last, "verdict": line, "skipped": skipped,
                    "aux": aux, "reason": ""}
        return {"ok": False, "last": last, "verdict": line, "skipped": skipped,
                "aux": aux, "reason": "最近一条数据操作未成功：%s" % line[:120]}
    return {"ok": False, "last": last, "verdict": "", "skipped": skipped, "aux": aux,
            "reason": "尾部 %d 行内没有可判定的数据操作结果" % len(tail)}


def make_new_entry(table: str, entry: dict) -> dict:
    """为 catalog 新表生成 registry 条目（字段与旧 registry 同构）。"""
    pk = entry.get("primary_key")
    return {
        "asset_id": table,
        "physical_tables": [table],
        "aliases": [],
        "provider": entry.get("source") if entry.get("source") == "tushare" else None,
        "channel": entry.get("source"),
        "market": None,
        "frequency": entry.get("freq"),
        "primary_key": pk if isinstance(pk, list) and pk else None,
        "business_columns": derive_business_columns(entry),
        "event_date_kind": None,
        "date_column": entry.get("date_col"),
        "policy_evidence": None,
        "session_cutoff": None,
        "available_after": None,
        "calendar": None,
        "supported_scope": None,
        "verification_rule": None,
        "qualification_status": "unknown",
        "unsupported_reason": NEW_UNSUPPORTED_REASON,
        "evidence_basis": NEW_EVIDENCE_BASIS,
        "pending_fields": list(PENDING_FIELDS),
    }


def build_payload(catalog: dict, old: dict) -> dict:
    """构造新 registry 内容 + 差异统计（纯计算，不写盘）。"""
    tables = catalog["tables"]
    old_physical = [e for e in (old.get("physical_tables") or []) if isinstance(e, dict)]
    old_display = [e for e in (old.get("display_assets") or []) if isinstance(e, dict)]

    covered: set[str] = set()
    physical: list[dict] = []
    retained = 0
    orphans: list[str] = []
    for e in old_physical:
        names = [str(t) for t in (e.get("physical_tables") or []) if t]
        if names and all(n in tables for n in names):
            # 旧条目已有内容优先：逐字段原样保留（含历史证据文本）
            physical.append(json.loads(json.dumps(e, ensure_ascii=False)))
            covered.update(names)
            retained += 1
        else:
            # catalog 中已不存在的旧表：保守保留原条目并记入报告，绝不静默丢弃
            physical.append(json.loads(json.dumps(e, ensure_ascii=False)))
            orphans.append(e.get("asset_id") or ",".join(names) or "?")

    added: list[str] = []
    for table in sorted(set(tables) - covered):
        physical.append(make_new_entry(table, tables[table]))
        added.append(table)

    # 生命周期覆盖：planned/deprecated/archived 不计入 unknown 资格欠账
    # 2026-11-02 task-77 修复：先解析全部覆盖再统一落状态（两阶段），lifecycle 异常不再
    # 静默（记入 stats.lifecycle_warning），避免"部分条目已改、计数却清零"的隐藏中间态。
    life_counts: dict[str, int] = {}
    life_assignments: dict[int, tuple] = {}
    life_warning = ""
    try:
        import lifecycle  # noqa: WPS433
        for idx, e in enumerate(physical):
            names = [str(t) for t in (e.get("physical_tables") or []) if t]
            for table in names:
                status = lifecycle.status_for(table)
                if status in ("planned", "deprecated", "archived"):
                    reason = lifecycle.reason_for(table) or ("资产状态：%s" % status)
                    life_assignments[idx] = (status, reason, table)
                    break
    except Exception as exc:  # noqa: BLE001 — 记录而非静默；不阻断重建
        life_assignments = {}
        life_warning = "lifecycle 覆盖解析失败（本次不套用生命周期状态，需人工复核）：%s: %s" % (
            type(exc).__name__, str(exc)[:160])
    for idx, (status, reason, table) in life_assignments.items():
        e = physical[idx]
        e["qualification_status"] = status
        e["unsupported_reason"] = reason
        e["evidence_basis"] = "data_lifecycle.yaml（2026-10-28）"
        e["pending_fields"] = []
        life_counts[status] = life_counts.get(status, 0) + 1

    limitations = list(old.get("limitations") or [])
    for line in LIMITATIONS_V2:
        if line not in limitations:
            limitations.append(line)

    cat_sha = common.sha256_file(common.CATALOG)
    gen_at = now_cn().isoformat()
    payload = {
        "generated_at": gen_at,
        "catalog_sha256": cat_sha,
        "catalog_updated": catalog.get("updated"),
        "display_evidence_sha256": old.get("display_evidence_sha256"),
        "physical_count": len(physical),
        "display_count": len(old_display),
        "physical_tables": physical,
        "display_assets": json.loads(json.dumps(old_display, ensure_ascii=False)),
        "limitations": limitations,
    }
    stats = {
        "catalog_version": catalog.get("version"),
        "catalog_tables": len(tables),
        "old_physical_count": len(old_physical),
        "old_display_count": len(old_display),
        "retained": retained,
        "added": len(added),
        "added_tables": added,
        "orphan_entries": orphans,
        "physical_count": len(physical),
        "display_count": len(old_display),
        "old_catalog_sha256": old.get("catalog_sha256"),
        "old_generated_at": old.get("generated_at"),
        "new_catalog_sha256": cat_sha,
        "new_generated_at": gen_at,
        "sha_match_before": bool(old.get("catalog_sha256") and old.get("catalog_sha256") == cat_sha),
        "lifecycle_counts": life_counts,
        "lifecycle_warning": life_warning,
    }
    return {"registry": payload, "stats": stats}


def write_backup(ts: str) -> Path:
    """备份旧 registry（同目录），返回备份路径；旧文件不存在则抛错。"""
    src = common.REGISTRY
    if not src.is_file():
        raise FileNotFoundError("旧 registry 不存在，无法备份：%s" % src)
    dst = src.with_name(src.name + ".bak-" + ts)
    if dst.exists():
        dst = src.with_name(src.name + ".bak-" + ts + "-%d" % os.getpid())
    shutil.copy2(src, dst)
    return dst


def atomic_write(payload: dict) -> None:
    """原子写：同目录 tmp + os.replace。"""
    target = common.REGISTRY
    tmp = target.with_name(target.name + ".tmp-%d" % os.getpid())
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "tools/datawarehouse/registry_rebuild.py", exc)


def verify_written(expect: dict) -> dict:
    """重读写盘结果做独立校验。"""
    cur = common.load_json(common.REGISTRY)
    cur_sha = common.sha256_file(common.CATALOG)
    checks = {
        "json_valid": isinstance(cur, dict),
        "catalog_sha256_match": bool(isinstance(cur, dict) and cur.get("catalog_sha256") == cur_sha),
        "physical_count_match": bool(isinstance(cur, dict)
                                     and cur.get("physical_count") == len(cur.get("physical_tables") or [])
                                     and cur.get("physical_count") == expect["physical_count"]),
        "display_count_match": bool(isinstance(cur, dict)
                                    and cur.get("display_count") == len(cur.get("display_assets") or [])
                                    and cur.get("display_count") == expect["display_count"]),
    }
    return {"ok": all(checks.values()), "checks": checks}


def append_kg_log(stats: dict) -> str:
    """向共享日志追加一行 registry_rebuild 流水（AGENTS.md §3 要求补记）。"""
    line = "registry_rebuild date=%s tables=%d sha=%s exit=0\n" % (
        now_cn().isoformat(timespec="seconds"), stats["physical_count"],
        (stats["new_catalog_sha256"] or "")[:16])
    with KG_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line)
    return line.strip()


def _summary(stats: dict, error: str = "") -> dict:
    out = {
        "tool": "registry_rebuild",
        "mode": "dry-run",
        "code": common.EXIT_OK,
        "catalog": {"version": stats["catalog_version"], "tables": stats["catalog_tables"]},
        "physical": {"before": stats["old_physical_count"], "retained": stats["retained"],
                     "added": stats["added"], "after": stats["physical_count"]},
        "display": {"before": stats["old_display_count"], "after": stats["display_count"]},
        "catalog_sha256": {"before": stats["old_catalog_sha256"],
                           "after": stats["new_catalog_sha256"],
                           "match_before": stats["sha_match_before"]},
        "generated_at": {"before": stats["old_generated_at"], "after": stats["new_generated_at"]},
        "added_sample": stats["added_tables"][:10],
        "orphan_entries": stats["orphan_entries"],
        "registry_path": common.rel(common.REGISTRY),
        "catalog_path": common.rel(common.CATALOG),
        "kg_log_last_before": "",
        "kg_log_aux_warnings": [],
    }
    if stats.get("lifecycle_warning"):
        out["warning"] = stats["lifecycle_warning"]
    if error:
        out["error"] = error
    return out


def run(apply_mode: bool, as_json: bool) -> int:
    try:
        catalog = load_catalog()
        old = load_registry()
        built = build_payload(catalog, old)
    except (ValueError, OSError) as exc:
        _emit({"tool": "registry_rebuild", "code": common.EXIT_NOT_READY, "error": str(exc)}, as_json)
        return common.EXIT_NOT_READY

    stats = built["stats"]
    payload = _summary(stats)
    payload["mode"] = "apply" if apply_mode else "dry-run"
    payload["code"] = common.EXIT_OK
    if not apply_mode:
        _emit(payload, as_json)
        return common.EXIT_OK

    # ---- apply：数据根互斥纪律 ----
    # mode 值 100 只是内部占位，下面统一改回真实退出码
    log_state = find_kg_log_state()
    payload["kg_log_last_before"] = log_state["last"]
    payload["kg_log_aux_warnings"] = log_state.get("aux") or []
    if not log_state["ok"]:
        payload["code"] = common.EXIT_NOT_READY
        payload["error"] = "拒跑：kg_sync_task.log 前置检查未通过（%s）" % log_state["reason"]
        _emit(payload, as_json)
        return common.EXIT_NOT_READY

    ts = now_cn().strftime("%Y%m%d-%H%M%S")
    written = False
    try:
        backup = write_backup(ts)
        payload["backup_path"] = common.rel(backup)
        atomic_write(built["registry"])
        written = True
        payload["written"] = True
        check = verify_written(stats)
        payload["verify"] = check
        if not check["ok"]:
            payload["code"] = common.EXIT_WARN
            payload["error"] = "写盘后校验失败（registry 可能已被写），保留备份供回滚：%s" % payload["backup_path"]
            _emit(payload, as_json)
            return common.EXIT_WARN
        # 2026-11-02 task-77：写盘成功后的 kg 日志补记失败单独区分（registry 已是新内容，
        # 不再笼统报"执行失败"；标注 written=true + 需人工补记，fail-visible）。
        try:
            payload["kg_log_appended"] = append_kg_log(stats)
        except OSError as exc:
            payload["code"] = common.EXIT_WARN
            payload["written"] = True
            payload["error"] = ("registry 已写盘并通过校验，但 kg_sync_task.log 补记失败"
                                "（需人工补记一行 registry_rebuild exit=0）：%s" % str(exc)[:160])
            _emit(payload, as_json)
            return common.EXIT_WARN
    except (OSError, ValueError) as exc:
        payload["code"] = common.EXIT_WARN
        payload["written"] = written
        payload["error"] = ("执行失败（written=%s）：%s" % (written, exc))
        _emit(payload, as_json)
        return common.EXIT_WARN

    _emit(payload, as_json)
    return common.EXIT_OK


def _emit(payload: dict, as_json: bool) -> None:
    """输出；--json 时只输出 JSON，便于机器消费。"""
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print("== registry_rebuild [%s] ==" % payload.get("mode", "dry-run"))
    print("catalog: v%s / %s 表" % (payload["catalog"]["version"], payload["catalog"]["tables"]))
    print("physical: 前 %s → 保留 %s + 新增 %s → 后 %s" % (
        payload["physical"]["before"], payload["physical"]["retained"],
        payload["physical"]["added"], payload["physical"]["after"]))
    print("display: %s → %s（原样保留）" % (payload["display"]["before"], payload["display"]["after"]))
    print("catalog_sha256 前: %s" % (payload["catalog_sha256"]["before"] or "(无)"))
    print("catalog_sha256 后: %s（%s）" % (
        payload["catalog_sha256"]["after"],
        "与旧一致" if payload["catalog_sha256"]["match_before"] else "与旧不一致=登记陈旧"))
    if payload.get("added_sample"):
        print("新增样例: %s" % ", ".join(payload["added_sample"]))
    if payload.get("orphan_entries"):
        print("catalog 中已不存在的旧条目（保守保留）: %s" % ", ".join(payload["orphan_entries"]))
    for key in ("backup_path", "error", "kg_log_appended"):
        if payload.get(key):
            print("%s: %s" % (key, payload[key]))
    if payload.get("verify"):
        print("写盘校验: %s" % json.dumps(payload["verify"]["checks"], ensure_ascii=False))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="warehouse_registry v2 重建（按 catalog v12 全量对齐）")
    ap.add_argument("--dry-run", action="store_true", help="只打印前后差异（默认）")
    ap.add_argument("--apply", action="store_true", help="执行重建（备份+原子写+校验+补记日志）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    a = ap.parse_args(argv)
    if a.dry_run and a.apply:
        print("参数冲突：--dry-run 与 --apply 只能二选一", file=sys.stderr)
        return common.EXIT_WARN
    return run(apply_mode=bool(a.apply), as_json=bool(a.json))


if __name__ == "__main__":
    raise SystemExit(main())
