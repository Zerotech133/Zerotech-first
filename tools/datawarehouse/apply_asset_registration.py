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
import glob
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

# 批次2（task-169）：12 项人工注册的 catalog 粒度决策（「目录/容器资产」登记，不逐文件登记）。
# 每项含：layout / 文件级 schema / date_col / code_col / primary_key / freq / static_no_date /
#        decision（决策理由，写入报告）。store/source 沿用草案；evidence 由 _dir_evidence 现场采样。
MANUAL_GRANULARITY: dict[str, dict] = {
    "research_shadow": {
        "layout": "shadow_ledger_dir",
        "schema": ["subdir", "date", "ret", "net", "turnover", "n_positions"],
        "date_col": "date", "code_col": None, "primary_key": ["subdir", "date"],
        "freq": "D", "static_no_date": False, "freshness": "2026-11-02",
        "decision": "影子盘台账目录（baseline/s08/s10/s11/selection 5 个子资产，各含 ledger.jsonl + shadow_report.json）；"
                    "以目录资产登记，schema 取 ledger.jsonl 文件级字段，主键=子目录+date，D 频"},
    "yt_raw_zip": {
        "layout": "zip_batch_manifest",
        "schema": ["package_id", "scope", "created_at", "counts", "members", "packaging_integrity", "sha256"],
        "date_col": "created_at", "code_col": None, "primary_key": ["package_id"],
        "freq": "batch", "static_no_date": False, "freshness": None,
        "decision": "分包归档：780 个 .zip + 同名 .manifest.json + .sha256（另含 补充更正_v2 子目录）；"
                    "catalog 粒度=包级资产（不逐文件登记），主键 package_id，date_col=manifest.created_at"},
    "yt_normalized": {
        "layout": "corpus_parquet_jsonl",
        "schema": ["video_id", "comment_id", "parent", "text", "author_hash", "like_count",
                   "published_at", "collected_at", "snapshot_id"],
        "date_col": "published_at", "code_col": "video_id", "primary_key": ["video_id", "comment_id"],
        "freq": "event", "static_no_date": False, "freshness": None,
        "decision": "规范化语料目录（videos/comments/channel_map 的 parquet+jsonl）；目录资产 schema 以 comments 主表字段为准，"
                    "主键=video_id+comment_id，事件时间 published_at"},
    "yt_cleaned_comments": {
        "layout": "corpus_clean_dir",
        "schema": ["video_id", "comment_id", "parent", "text", "author_hash", "like_count", "published_at",
                   "collected_at", "snapshot_id", "pii_masked", "text_sha1"],
        "date_col": "published_at", "code_col": "video_id", "primary_key": ["video_id", "comment_id"],
        "freq": "event", "static_no_date": False, "freshness": None,
        "decision": "清洗+PII 脱敏评论语料（comments_clean + laomu_notes + 两期视频号学习子集）；"
                    "schema 以 comments_clean parquet 为准（含 pii_masked/text_sha1）"},
    "yt_knowledge_units": {
        "layout": "corpus_jsonl",
        "schema": ["unit_id", "project_id", "claim", "evidence", "status", "created_at"],
        "date_col": "created_at", "code_col": "project_id", "primary_key": ["unit_id"],
        "freq": "event", "static_no_date": False, "freshness": None,
        "decision": "知识单元 draft/effective 共 7 个 jsonl；文件级 schema，主键 unit_id，按 project_id+created_at 组织"},
    "yt_faq": {
        "layout": "faq_assets_mixed",
        "schema": ["cluster_id", "size", "like_sum", "sample_examples", "n_videos", "ok",
                   "is_technical", "faq_en", "faq_zh", "topic"],
        "date_col": None, "code_col": None, "primary_key": ["cluster_id"],
        "freq": "event", "static_no_date": True, "freshness": None,
        "decision": "FAQ 挖掘产物（json/jsonl/py/log/npy 混合，19 文件）；目录资产无统一日期列 → static_no_date=true，"
                    "schema 取 faq_cluster_labels.jsonl，主键 cluster_id，按构建批次(event)更新"},
    "yt_index": {
        "layout": "index_shards",
        "schema": ["doc_id", "video_id", "channel_id", "kind", "title", "upload_date", "text"],
        "date_col": "upload_date", "code_col": "video_id", "primary_key": ["doc_id"],
        "freq": "event", "static_no_date": False, "freshness": None,
        "decision": "62,401 docs bge-m3 索引（lite 索引 + full/shards）；目录资产 schema 取 shard jsonl 字段，主键 doc_id，"
                    "按索引构建批次更新"},
    "yt_learning_cards": {
        "layout": "markdown_daily",
        "schema": ["file_path", "trade_date", "title", "body_markdown"],
        "date_col": "trade_date", "code_col": None, "primary_key": ["file_path"],
        "freq": "D", "static_no_date": False, "freshness": None,
        "decision": "学习卡 Markdown 文件（cards_YYYYMMDD.md，按日）；目录资产以 file_path 为主键，trade_date 由文件名解析，D 频"},
    "yt_briefs": {
        "layout": "markdown_daily",
        "schema": ["file_path", "trade_date", "title", "body_markdown"],
        "date_col": "trade_date", "code_col": None, "primary_key": ["file_path"],
        "freq": "D", "static_no_date": False, "freshness": None,
        "decision": "简报 Markdown 文件（briefing_YYYYMMDD.md，按日）；以 file_path 为主键，trade_date 由文件名解析，D 频"},
    "yt_review": {
        "layout": "review_bundle",
        "schema": ["file_path", "generated_at", "seed", "total_pool", "items"],
        "date_col": "generated_at", "code_col": None, "primary_key": ["file_path"],
        "freq": "event", "static_no_date": False, "freshness": None,
        "decision": "复核报告成对文件（review_*.json + .md）；以 file_path 为主键，事件时间 generated_at，按复核批次更新"},
    "yt_reports": {
        "layout": "report_bundle",
        "schema": ["file_path", "generated_at", "root", "totals", "packages"],
        "date_col": "generated_at", "code_col": None, "primary_key": ["file_path"],
        "freq": "event", "static_no_date": False, "freshness": None,
        "decision": "报告集（22 json + 11 md，含 corpus_manifest.json）；以 file_path 为主键，generated_at 为事件时间"},
    "yt_learning.db": {
        "layout": "sqlite_db_container",
        "schema": ["dim_channel", "dim_topic", "dim_video", "bridge_video_topic", "fact_comment",
                   "fact_knowledge_unit", "fact_transcript", "fact_faq_semantic", "bridge_faq_comment",
                   "meta_faq_build", "fact_transcript_seg", "meta_etl", "fact_transcript_signal"],
        "date_col": None, "code_col": None, "primary_key": ["table_name"],
        "freq": "mixed", "static_no_date": True, "freshness": None,
        "decision": "星型模型库级容器资产（yt_learning.db 共 15 表；其中 13 张星型表已在批次1 单独注册，本项只登记库容器，"
                    "schema=已注册表名清单，避免与表级资产重复计覆盖）"},
}


def _dir_evidence(store: str | None) -> dict:
    """目录型/容器型资产的现场采样证据（只读）：顶层条目、递归文件数、扩展名分布、最新 mtime。"""
    from collections import Counter
    p = Path(str(store).replace("/", "\\")) if store else None
    ev = {"store_exists": bool(p and p.exists())}
    if not ev["store_exists"]:
        return ev
    try:
        if p.is_file():
            ev.update({"kind": "file", "bytes": p.stat().st_size,
                       "latest_mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")})
            return ev
        tops = sorted(glob.glob(str(p / "*")))
        files, ext, n_rec = [], Counter(), 0
        for root, ds, fs in os.walk(p):
            ds[:] = [x for x in ds if x not in (".git", "__pycache__")]
            for f in fs:
                n_rec += 1
                ext[os.path.splitext(f)[1].lower()] += 1
                if os.path.dirname(os.path.join(root, f)) == str(p):
                    files.append(f)
            if n_rec > 200000:
                break
        latest = 0.0
        for root, ds, fs in os.walk(p):
            ds[:] = [x for x in ds if x not in (".git", "__pycache__")]
            for f in fs:
                try:
                    latest = max(latest, os.path.getmtime(os.path.join(root, f)))
                except OSError:
                    pass
        ev.update({"kind": "dir", "top_entries": len(tops), "top_files": len(files),
                   "top_dirs": len([x for x in tops if os.path.isdir(x)]), "recursive_files": n_rec,
                   "ext_dist": dict(ext.most_common(8)),
                   "sample_files": sorted(files)[:5],
                   "latest_mtime": datetime.fromtimestamp(latest).strftime("%Y-%m-%d") if latest else None})
        # 若能找到 parquet/jsonl，采样文件级 schema
        for pat, kind in (("**/*.parquet", "parquet"), ("**/*.jsonl", "jsonl")):
            cand = sorted(glob.glob(str(p / "**" / pat.split("/")[-1]), recursive=True))[:1]
            if not cand:
                continue
            try:
                if kind == "parquet":
                    import pyarrow.parquet as _pq
                    names = _pq.read_schema(cand[0]).names
                else:
                    first = open(cand[0], encoding="utf-8", errors="ignore").readline()
                    names = list(json.loads(first).keys())
                ev["schema_sample"] = {"file": os.path.relpath(cand[0], p), "kind": kind, "columns": names[:20]}
                break
            except Exception:  # noqa: BLE001
                continue
        return ev
    except OSError as exc:  # noqa: BLE001
        ev["error"] = str(exc)[:200]
        return ev


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
    """把 task-162 草案项（或批次2 人工粒度决策）转换为 catalog v37 风格条目。"""
    mg = MANUAL_GRANULARITY.get(str(d.get("id")))
    if mg:
        d = {**(d or {}), **mg}
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
        "readiness_basis": "manual_granularity" if mg else "sampling",
        "verification_status": "metadata_manual_decision" if mg else "metadata_sampled",
        "manifest_status": "not_applicable",
        "deprecated": False,
        "desc": desc,
    }


def build_diff(catalog: dict, drafts: list[dict], expected: int | None = None) -> dict:
    expected = EXPECT_ADD if expected is None else int(expected)
    tables = catalog.get("tables") or {}
    conflicts = [d["id"] for d in drafts if d["id"] in tables]
    missing_store = [d["id"] for d in drafts
                     if not d.get("store") or not Path(str(d["store"]).replace("/", "\\")).exists()]
    ids = [d["id"] for d in drafts]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    new_entries = {d["id"]: build_catalog_entry(d) for d in drafts}
    empty_schema = [tid for tid, e in new_entries.items() if not (e.get("schema") or [])]
    granularity = []
    for d in drafts:
        mg = MANUAL_GRANULARITY.get(str(d.get("id")))
        if not mg:
            continue
        granularity.append({
            "id": d["id"], "label": d.get("label"), "store": d.get("store"),
            "decision": mg.get("decision"), "layout": mg.get("layout"),
            "schema": mg.get("schema"), "date_col": mg.get("date_col"),
            "code_col": mg.get("code_col"), "primary_key": mg.get("primary_key"),
            "freq": mg.get("freq"), "static_no_date": mg.get("static_no_date"),
            "evidence": _dir_evidence(d.get("store")),
        })
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
            and len(drafts) == expected,
            "expected": expected, "ids": ids, "new_entries": new_entries,
            "granularity_decisions": granularity}


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
        report_prefix: Path | None = None, backup_dir: Path | None = None,
        ids: list[str] | None = None, batch: str = "auto",
        expected: int | None = None, task_id: str = "task-165 W7 数据仓注册执行器") -> dict:
    t0 = time.time()
    draft_path = Path(draft_path) if draft_path else DEFAULT_DRAFT
    report_prefix = Path(report_prefix) if report_prefix else DEFAULT_PREFIX
    backup_dir = Path(backup_dir) if backup_dir else BACKUP_DIR
    res = {"tool": TOOL, "mode": mode, "generated_at": _now(), "task": task_id,
           "catalog_path": str(CATALOG), "registry_path": str(REGISTRY),
           "draft_path": str(draft_path), "pass": False, "error": None}

    draft = _read_json(draft_path)
    all_drafts = draft.get("register_drafts") or []
    manual_ids = [d["id"] for d in all_drafts if not d.get("auto_generated")]
    auto_ids = [d["id"] for d in all_drafts if d.get("auto_generated")]
    if ids:
        want = [str(x).strip() for x in ids if str(x).strip()]
        known = {d["id"] for d in all_drafts}
        unknown_ids = [x for x in want if x not in known]
        if unknown_ids:
            res["error"] = f"草案中不存在这些 id: {unknown_ids}"
            return res
        selected = [d for d in all_drafts if d["id"] in set(want)]
    elif batch == "manual":
        selected = [d for d in all_drafts if d["id"] in set(manual_ids)]
    elif batch == "auto":
        selected = [d for d in all_drafts if d["id"] in set(auto_ids)]
    else:
        selected = list(all_drafts)
    expect = expected if expected is not None else (
        EXPECT_MANUAL if batch == "manual" else EXPECT_ADD if batch == "auto" else len(all_drafts))
    res["register_total"] = len(all_drafts)
    res["batch"] = batch
    res["selected_count"] = len(selected)
    res["selected_ids"] = [d["id"] for d in selected]
    res["not_selected"] = [{"id": d.get("id"), "label": d.get("label"),
                            "auto_generated": bool(d.get("auto_generated"))}
                           for d in all_drafts if d["id"] not in set(res["selected_ids"])]
    res["auto_selected"] = len([d for d in selected if d.get("auto_generated")])
    res["excluded_manual"] = res["not_selected"]
    if len(selected) != expect:
        res["error"] = f"选中项数量 {len(selected)} != 预期 {expect}（batch={batch}）"
        return res

    catalog = _read_json(CATALOG)
    registry = _read_json(REGISTRY)
    res["before"] = {"catalog_version": catalog.get("version"),
                     "catalog_tables": len(catalog.get("tables") or {}),
                     "catalog_sha256": _sha(CATALOG),
                     "registry_sha256": _sha(REGISTRY),
                     "registry_physical": registry.get("physical_count"),
                     "registry_display": registry.get("display_count")}
    diff = build_diff(catalog, selected, expected=expect)
    res["dry_run"] = {k: diff[k] for k in ("add_count", "expected", "conflicts", "missing_store",
                                           "empty_schema", "duplicate_ids", "warnings",
                                           "clean", "ids")}
    res["granularity_decisions"] = diff.get("granularity_decisions") or []
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
        res["risks"] = ["写后复检失败已自动回滚，catalog/registry 恢复注册前状态；请检查 metadata/registry_align 输出"]
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
    res["risks"] = _risks_for(batch)
    res["elapsed_s"] = round(time.time() - t0, 2)
    _write_report(res, report_prefix)
    return res


# ============================================================ 报告
def _risks_for(batch: str) -> list[str]:
    """批次风险与后续动作（写入报告；不改变执行语义）。"""
    common_risks = [
        "corpus_layer 以「目录/容器资产」粒度登记（不逐文件），文件级 schema 为采样值；逐文件清单/校验和需后续 manifest 化",
        "目录型资产无统一日期列 → 按 task-169 决策标 static_no_date=true / freq=event（静态或批次口径），新鲜度检查对这些资产豁免",
        "catalog/registry 属 .gitignore 数据文件，本任务不 commit；生产写入以 metadata_consistency+registry_align 复检为准",
    ]
    if batch == "manual":
        return common_risks + [
            "yt_learning.db 为库级容器资产，与批次1 已注册的 13 张星型表存在「容器+表」双登记：覆盖统计需按表级去重",
            "yt_raw_zip 以包级登记（780 包），不含包内逐文件校验；包完整性由 .sha256 文件保障，建议后续补逐文件 manifest",
            "markdown/py/log/npy 等非表格资产（learning_cards/briefs/faq 等）无行级 schema，下游取数需按文件路径+解析器约定",
        ]
    return common_risks


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
        f"# 数据仓注册执行器运行报告（{res.get('task')}）", "",
        f"- 生成：{res.get('generated_at')}；模式 **{res.get('mode')}**；任务 {res.get('task')}；"
        f"结论 **{'PASS' if res.get('pass') else 'FAIL'}**",
        f"- 输入草案：{res.get('draft_path')}（本批 batch={res.get('batch')}，选中 "
        f"{res.get('selected_count')}/{res.get('register_total')} 项，未选中 {len(res.get('not_selected') or [])} 项不写）",
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
    decisions = res.get("granularity_decisions") or []
    if decisions:
        L += ["", "## 四、人工粒度决策（batch2 · 目录/容器资产登记）", "",
              "| id | layout | schema(文件级/表级) | date_col | primary_key | freq | static_no_date | 证据 |",
              "|---|---|---|---|---|---|---|---|"]
        for g in decisions:
            ev = g.get("evidence") or {}
            sch = ",".join((g.get("schema") or [])[:4]) + ("…" if len(g.get("schema") or []) > 4 else "")
            L.append(f"| {g.get('id')} | {g.get('layout')} | {sch} | {g.get('date_col') or '—'} | "
                     f"{g.get('primary_key')} | {g.get('freq')} | {g.get('static_no_date')} | "
                     f"store={ev.get('store_exists')}, files={ev.get('recursive_files')}, "
                     f"latest={ev.get('latest_mtime')} |")
        L += ["", "决策理由："]
        L += [f"- **{g.get('id')}**：{g.get('decision')}" for g in decisions]
    L += ["", "## 五、未选中项（本批不写）", "",
          "| id | label | 是否自动项 |", "|---|---|---|"]
    for m in res.get("not_selected") or []:
        L.append(f"| {m.get('id')} | {m.get('label')} | {m.get('auto_generated')} |")
    L += ["", "## 六、风险与后续", ""]
    risks = res.get("risks") or []
    L += [f"- {r}" for r in risks] if risks else ["- 无"]
    L += ["", "## 七、边界", "",
          "- 只写 catalog/registry 元数据；不改数据文件（store 只读）；不 commit。",
          "- registry 由官方 registry_align --apply 重建（自带备份/校验/kg 日志），执行器负责 catalog 写入与总回滚。",
          "- 未选中项按批次边界不写；如有人工项剩余，需人工补全元数据后另单注册。", ""]
    md.write_text("\n".join(L), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="apply_asset_registration.py",
                                 description="W7 数据仓注册执行器（默认 dry-run；--apply 才写）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="只读 diff（默认）")
    g.add_argument("--apply", action="store_true", help="写 catalog + registry_align --apply 重建 registry")
    ap.add_argument("--draft", default=str(DEFAULT_DRAFT))
    ap.add_argument("--ids", default="", help="逗号分隔的指定 id 列表（优先级高于 --batch）")
    ap.add_argument("--batch", choices=["auto", "manual", "all"], default="auto",
                    help="auto=批次1 的 36 自动项（默认，向后兼容）；manual=批次2 的 12 人工项；all=48 全量")
    ap.add_argument("--expect", type=int, default=None, help="期望新增条数（默认按批次：auto=36/manual=12）")
    ap.add_argument("--report-prefix", default=str(DEFAULT_PREFIX))
    ap.add_argument("--backup-dir", default=str(BACKUP_DIR))
    ap.add_argument("--task", default="task-165 W7 数据仓注册执行器", help="报告标注的任务号/名称")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    mode = "apply" if args.apply else "dry-run"
    ids = [x for x in str(args.ids).split(",") if x.strip()] if args.ids else None
    res = run(mode=mode, draft_path=Path(args.draft), report_prefix=Path(args.report_prefix),
              backup_dir=Path(args.backup_dir), ids=ids, batch=args.batch,
              expected=args.expect, task_id=args.task)
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
