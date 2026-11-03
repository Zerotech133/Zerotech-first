# -*- coding: utf-8 -*-
"""tools/datawarehouse/apply_asset_registration.py · W7 数据仓注册执行器（task-165）。

把 task-162 元数据草案中「36 个可自动生成」的注册项安全写入 catalog.json（并触发
官方 registry_align --apply 重建 warehouse_registry.json），带完整预检 / 备份 / 原子写 /
写后复检 / 失败自动回滚。**12 个人工项不写**。

安全边界
--------
  · 默认 dry-run：只读+输出 diff，不写任何文件；`--apply` 才写；
  · 前置：kg_sync_task.log 尾部必须是成功收尾（复用 registry_rebuild.find_kg_log_state 口径）；
  · 写前：catalog.json / warehouse_registry.json 备份到 research/reports/backups/（带 sha256）；
  · 原子写：同目录 tmp + os.replace；
  · registry 写入交给官方 `tools/datawarehouse/registry_align.py --apply`（自带备份/重建/kg 日志）；
  · 写后复检：`metadata_consistency.py --json` + `registry_align.py --check --json` 双 PASS 才算成功；
  · 任一失败：从备份原子回滚 catalog+registry，并复检恢复态，报告 rolled_back=True；
  · 不 commit、不改数据文件（parquet/store 只读）。

用法
----
    python -X utf8 tools/datawarehouse/apply_asset_registration.py --dry-run --json
    python -X utf8 tools/datawarehouse/apply_asset_registration.py --apply --json
    python -X utf8 tools/datawarehouse/apply_asset_registration.py --apply --report-prefix research/reports/asset_registration_apply_20261104
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
REPO = TOOLS_DIR.parents[1]
DATA = REPO / "quant_backtest" / "data"
CATALOG = DATA / "catalog.json"
REGISTRY = DATA / "warehouse_registry.json"
OUT = REPO / "research" / "reports"
BACKUP_DIR = OUT / "backups"
DEFAULT_DRAFT = OUT / "data_asset_metadata_draft_20261104.json"
DEFAULT_PREFIX = OUT / "asset_registration_apply_20261104"
KG_LOG = Path(r"C:\Temp\kg_sync_task.log")
PY = sys.executable or r"C:\项目\py312\python.exe"
TOOL = "asset_registration"
EXPECT_ADD = 36
EXPECT_MANUAL = 12

sys.path.insert(0, str(TOOLS_DIR))
import registry_rebuild as rr  # noqa: E402


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, payload: dict) -> None:
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp-%d" % os.getpid())
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _run(cmd: list[str], timeout: int = 240) -> dict:
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        out = (proc.stdout or "").strip()
        parsed = None
        if out.startswith("{"):
            try:
                parsed = json.loads(out)
            except ValueError:
                parsed = None
        return {"cmd": " ".join(cmd), "rc": int(proc.returncode), "json": parsed,
                "stdout_tail": out[-2000:], "stderr_tail": (proc.stderr or "").strip()[-1000:],
                "elapsed_s": round(time.time() - t0, 2)}
    except subprocess.TimeoutExpired:
        return {"cmd": " ".join(cmd), "rc": -9, "json": None, "stdout_tail": "",
                "stderr_tail": "timeout", "elapsed_s": round(time.time() - t0, 2)}


# ============================================================ 草案 → catalog 条目
def build_catalog_entry(d: dict) -> dict:
    """把 task-162 草案项转换为 catalog v35 风格条目。"""
    schema = []
    for item in d.get("schema") or []:
        if isinstance(item, dict):
            schema.append(str(item.get("name")))
        else:
            schema.append(str(item))
    layout = d.get("layout") or ("sqlite" if str(d.get("store", "")).endswith(".db") else "dir")
    if layout in ("sqlite_table", "sqlite"):
        layout = "sqlite"
    is_sqlite = layout == "sqlite"
    pk = d.get("primary_key") or []
    if is_sqlite and pk == ["rowid"]:
        pk = []      # catalog 约定：无业务主键记 []
    desc = str(d.get("desc") or "")
    desc = (desc[:220] + "；W4 批准注册（task-162 元数据草案/采样证据，task-165 执行）")
    return {
        "source": d.get("source"),
        "store": d.get("store"),
        "layout": layout,
        "schema": schema,
        "schema_scope": "sqlite_pragma" if is_sqlite else "sampled",
        "schema_files_inspected": 1 if is_sqlite else None,
        "schema_errors": [],
        "date_col": d.get("date_col"),
        "code_col": d.get("code_col"),
        "date_status": "registered" if d.get("date_col") else "not_applicable",
        "static_no_date": bool(d.get("static_no_date", False)),
        "future_ok": False,
        "primary_key": pk,
        "freq": d.get("freq"),
        "priority": 2,
        "manifest": None,
        "freshness": d.get("freshness"),
        "rows": d.get("rows"),
        "files": int(d.get("files") or 0),
        "done": False,
        "readiness_basis": "sampling",
        "verification_status": "metadata_sampled",
        "manifest_status": "not_applicable",
        "deprecated": False,
        "desc": desc,
    }


def build_diff(catalog: dict, drafts: list[dict]) -> dict:
    tables = catalog.get("tables") or {}
    conflicts = [d["id"] for d in drafts if d["id"] in tables]
    missing_store = [d["id"] for d in drafts
                     if not d.get("store") or not Path(str(d["store"]).replace("/", "\\")).exists()]
    empty_schema = [d["id"] for d in drafts if not (d.get("schema") or [])]
    ids = [d["id"] for d in drafts]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    new_entries = {d["id"]: build_catalog_entry(d) for d in drafts}
    import re as _re
    warnings = []
    for d in drafts:
        dc = str(d.get("date_col") or "")
        if dc and not d.get("static_no_date") and \
                not _re.search(r"date|time|day|dt$|_ts$|^ts$|at$|since", dc, _re.I):
            warnings.append("%s: date_col=%r 非日期型列名，建议人工复核" % (d["id"], dc))
    return {"add_count": len(drafts), "conflicts": conflicts, "missing_store": missing_store,
            "empty_schema": empty_schema, "duplicate_ids": dup, "warnings": warnings,
            "clean": not (conflicts or missing_store or empty_schema or dup)
            and len(drafts) == EXPECT_ADD,
            "ids": ids, "new_entries": new_entries}


def apply_catalog(catalog: dict, new_entries: dict) -> dict:
    new_cat = copy.deepcopy(catalog)
    tables = new_cat.setdefault("tables", {})
    for tid in sorted(new_entries):
        tables[tid] = new_entries[tid]
    old_version = catalog.get("version")
    new_cat["version"] = (int(old_version) + 1) if isinstance(old_version, int) else 36
    new_cat["updated"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return new_cat


# ============================================================ 复检 / 回滚
def verify_state() -> dict:
    mc = _run([PY, "-X", "utf8", str(TOOLS_DIR / "metadata_consistency.py"), "--json"])
    ra = _run([PY, "-X", "utf8", str(TOOLS_DIR / "registry_align.py"), "--check", "--json"])
    mc_json = mc.get("json") or {}
    ra_json = ra.get("json") or {}
    ok = (mc["rc"] == 0 and int(mc_json.get("code", 2)) == 0
          and ra["rc"] == 0 and ra_json.get("match_before") is True)
    return {"ok": bool(ok), "metadata_consistency": mc, "registry_align_check": ra}


def rollback(backups: dict) -> dict:
    """从备份原子恢复 catalog+registry，并复检恢复态。"""
    out = {}
    for key, target in (("catalog", CATALOG), ("registry", REGISTRY)):
        src = Path(backups[key])
        tmp = target.with_name(target.name + ".rollback-%d" % os.getpid())
        shutil.copy2(src, tmp)
        os.replace(tmp, target)
        out[key] = {"restored": True, "from": str(src), "sha256": _sha(target)}
    out["state"] = verify_state()
    return out


def _append_kg(line: str) -> dict:
    try:
        KG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with KG_LOG.open("a", encoding="utf-8", newline="\n") as fh:
            if fh.tell() > 0:
                with KG_LOG.open("rb") as rh:
                    rh.seek(-1, os.SEEK_END)
                    if rh.read(1) != b"\n":
                        fh.write("\n")
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return {"appended": line, "path": str(KG_LOG)}
    except OSError as exc:  # noqa: BLE001
        return {"appended": None, "path": str(KG_LOG), "error": str(exc)[:160]}


# ============================================================ 主流程
def run(mode: str = "dry-run", draft_path: Path | None = None,
        report_prefix: Path | None = None, backup_dir: Path | None = None) -> dict:
    t0 = time.time()
    draft_path = Path(draft_path) if draft_path else DEFAULT_DRAFT
    report_prefix = Path(report_prefix) if report_prefix else DEFAULT_PREFIX
    backup_dir = Path(backup_dir) if backup_dir else BACKUP_DIR
    res = {"tool": TOOL, "mode": mode, "generated_at": _now(), "task": "task-165",
           "catalog_path": str(CATALOG), "registry_path": str(REGISTRY),
           "draft_path": str(draft_path), "pass": False, "error": None}

    draft = _read_json(draft_path)
    all_drafts = draft.get("register_drafts") or []
    auto = [d for d in all_drafts if d.get("auto_generated")]
    manual = [{"id": d.get("id"), "label": d.get("label"), "reason": "; ".join(d.get("needs_manual") or [])}
              for d in all_drafts if not d.get("auto_generated")]
    res["register_total"] = len(all_drafts)
    res["auto_selected"] = len(auto)
    res["excluded_manual"] = manual
    if len(auto) != EXPECT_ADD:
        res["error"] = f"自动项数量 {len(auto)} != 预期 {EXPECT_ADD}"
        return res

    catalog = _read_json(CATALOG)
    registry = _read_json(REGISTRY)
    res["before"] = {"catalog_version": catalog.get("version"),
                     "catalog_tables": len(catalog.get("tables") or {}),
                     "catalog_sha256": _sha(CATALOG),
                     "registry_sha256": _sha(REGISTRY),
                     "registry_physical": registry.get("physical_count"),
                     "registry_display": registry.get("display_count")}
    diff = build_diff(catalog, auto)
    res["dry_run"] = {k: diff[k] for k in ("add_count", "conflicts", "missing_store",
                                           "empty_schema", "duplicate_ids", "warnings",
                                           "clean", "ids")}
    if not diff["clean"]:
        res["error"] = "dry-run diff 不干净（冲突/缺 store/空 schema/重复）"
        return res
    if mode == "dry-run":
        res["pass"] = True
        res["elapsed_s"] = round(time.time() - t0, 2)
        return res

    # ---- 前置：kg 日志 + 备份
    kg_state = rr.find_kg_log_state()
    res["kg_precheck"] = kg_state
    if not kg_state.get("ok"):
        res["error"] = "前置拒跑：%s" % kg_state.get("reason")
        return res
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backups = {"catalog": backup_dir / f"catalog_before_task165_{ts}.json",
               "registry": backup_dir / f"warehouse_registry_before_task165_{ts}.json"}
    shutil.copy2(CATALOG, backups["catalog"])
    shutil.copy2(REGISTRY, backups["registry"])
    res["backups"] = {k: {"path": str(v), "sha256": _sha(v), "bytes": v.stat().st_size}
                      for k, v in backups.items()}
    res["rollback_command"] = (
        f"Copy-Item '{backups['catalog']}' '{CATALOG}' -Force; "
        f"Copy-Item '{backups['registry']}' '{REGISTRY}' -Force")

    # ---- 写 catalog（原子）→ registry_align --apply 重建 registry（官方工具，自带备份/kg 日志）
    new_cat = apply_catalog(catalog, diff["new_entries"])
    _atomic_write_json(CATALOG, new_cat)
    res["after_catalog_write"] = {"catalog_version": new_cat.get("version"),
                                  "catalog_tables": len(new_cat.get("tables") or {}),
                                  "catalog_sha256": _sha(CATALOG)}
    apply_res = _run([PY, "-X", "utf8", str(TOOLS_DIR / "registry_align.py"), "--apply", "--json"])
    res["registry_align_apply"] = apply_res
    ok = apply_res["rc"] == 0 and bool((apply_res.get("json") or {}).get("match_after"))
    checks = verify_state() if ok else None
    if checks is not None:
        res["verify"] = checks
        ok = checks["ok"]
    if not ok:
        res["rolled_back"] = True
        res["rollback"] = rollback(backups)
        res["error"] = "写后复检失败，已自动回滚（详见 registry_align_apply/verify）"
        res["elapsed_s"] = round(time.time() - t0, 2)
        _write_report(res, report_prefix)
        return res

    after = _read_json(CATALOG)
    new_registry = _read_json(REGISTRY)
    res["after"] = {"catalog_version": after.get("version"),
                    "catalog_tables": len(after.get("tables") or {}),
                    "catalog_sha256": _sha(CATALOG),
                    "registry_sha256": _sha(REGISTRY),
                    "registry_physical": new_registry.get("physical_count"),
                    "registry_display": new_registry.get("display_count"),
                    "added": sorted(diff["ids"])}
    res["pass"] = True
    line = (f"{TOOL} date={_now()} action=apply catalog=v{after.get('version')} "
            f"sha={res['after']['catalog_sha256'][:16]} added={len(diff['ids'])} "
            f"tables={res['after']['catalog_tables']} physical={new_registry.get('physical_count')} "
            f"display={new_registry.get('display_count')} PASS exit=0")
    res["kg_append"] = _append_kg(line)
    res["elapsed_s"] = round(time.time() - t0, 2)
    _write_report(res, report_prefix)
    return res


# ============================================================ 报告
def _write_report(res: dict, prefix: Path) -> None:
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    js = prefix.with_suffix(".json")
    md = prefix.with_suffix(".md")
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    before = res.get("before") or {}
    after = res.get("after") or {}
    dry = res.get("dry_run") or {}
    checks = res.get("verify") or {}
    mc = (checks.get("metadata_consistency") or {})
    ra = (checks.get("registry_align_check") or {})
    L = [
        "# 数据仓注册执行器运行报告（task-165 · W7）", "",
        f"- 生成：{res.get('generated_at')}；模式 **{res.get('mode')}**；任务 task-165；"
        f"结论 **{'PASS' if res.get('pass') else 'FAIL'}**",
        f"- 输入草案：{res.get('draft_path')}（自动项 {res.get('auto_selected')}/"
        f"{res.get('register_total')}，人工项 {len(res.get('excluded_manual') or [])} 不写）",
        f"- catalog：v{before.get('catalog_version')}（{before.get('catalog_tables')} 表）"
        f" → v{after.get('catalog_version')}（{after.get('catalog_tables')} 表）；"
        f"新增 **{len(after.get('added') or dry.get('ids') or [])}** 表",
        f"- registry：physical {before.get('registry_physical')} → {after.get('registry_physical')}；"
        f"display {before.get('registry_display')} → {after.get('registry_display')}",
        f"- 备份：catalog `{(res.get('backups') or {}).get('catalog', {}).get('path')}`；"
        f"registry `{(res.get('backups') or {}).get('registry', {}).get('path')}`",
        f"- 回滚命令：`{res.get('rollback_command')}`", "",
        "## 一、dry-run diff", "",
        f"- 新增 {dry.get('add_count')} 项；id 冲突 {dry.get('conflicts')}；"
        f"store 缺失 {dry.get('missing_store')}；空 schema {dry.get('empty_schema')}；"
        f"重复 id {dry.get('duplicate_ids')}；diff_clean={dry.get('clean')}",
        "", "新增 id 清单：", "",
    ]
    ids = after.get("added") or dry.get("ids") or []
    L += [f"- {i}" for i in ids]
    L += ["", "## 二、写后复检证据", "",
          f"- metadata_consistency：rc={mc.get('rc')}，code={(mc.get('json') or {}).get('code')}，"
          f"tables={(mc.get('json') or {}).get('tables')}，"
          f"physical={(mc.get('json') or {}).get('physical_count')}，"
          f"issues={(mc.get('json') or {}).get('issues')}",
          f"- registry_align --check：rc={ra.get('rc')}，"
          f"match_before={(ra.get('json') or {}).get('match_before')}，"
          f"match_after={(ra.get('json') or {}).get('match_after')}",
          f"- registry_align --apply：rc={(res.get('registry_align_apply') or {}).get('rc')}，"
          f"rebuild={(res.get('registry_align_apply') or {}).get('json', {}).get('rebuild')}",
          f"- kg_sync_task.log 追加：{(res.get('kg_append') or {}).get('appended')}"]
    if res.get("rolled_back"):
        L += ["", "## 三、失败与回滚", "", f"- 错误：{res.get('error')}",
              f"- 回滚后复检：{(res.get('rollback') or {}).get('state')}"]
    L += ["", "## 四、人工项（不写清单）", "",
          "| id | label | 需人工字段 |", "|---|---|---|"]
    for m in res.get("excluded_manual") or []:
        L.append(f"| {m.get('id')} | {m.get('label')} | {m.get('reason')} |")
    L += ["", "## 五、边界", "",
          "- 只写 catalog/registry 元数据；不改数据文件（store 只读）；不 commit。",
          "- registry 由官方 registry_align --apply 重建（自带备份/校验/kg 日志），执行器负责 catalog 写入与总回滚。",
          "- 12 个人工项按批准范围不写；待人工补全元数据后另单注册。", ""]
    md.write_text("\n".join(L), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="apply_asset_registration.py",
                                 description="W7 数据仓注册执行器（默认 dry-run；--apply 才写）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="只读 diff（默认）")
    g.add_argument("--apply", action="store_true", help="写 catalog + registry_align --apply 重建 registry")
    ap.add_argument("--draft", default=str(DEFAULT_DRAFT))
    ap.add_argument("--report-prefix", default=str(DEFAULT_PREFIX))
    ap.add_argument("--backup-dir", default=str(BACKUP_DIR))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    mode = "apply" if args.apply else "dry-run"
    res = run(mode=mode, draft_path=Path(args.draft), report_prefix=Path(args.report_prefix),
              backup_dir=Path(args.backup_dir))
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(f"# {TOOL} [{mode}] pass={res.get('pass')} error={res.get('error')}")
        print(f"# dry_run={res.get('dry_run')}")
        if res.get("after"):
            print(f"# after catalog=v{res['after']['catalog_version']} tables={res['after']['catalog_tables']} "
                  f"physical={res['after']['registry_physical']} display={res['after']['registry_display']}")
    return 0 if res.get("pass") else 1


if __name__ == "__main__":
    sys.exit(main())
