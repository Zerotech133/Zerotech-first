# -*- coding: utf-8 -*-
"""catalog → warehouse_registry 自动对齐（P0-1，2026-11-03 task-81）。

背景：KgDailySync 每日 06:20 先 build_catalog（catalog 升版）再跑各步，但 registry 的
`catalog_sha256` 只在 registry_rebuild 执行时才更新 → 06:25 后常出现「catalog 已升版、
registry 旧」的人工提醒。本工具把「检测 + 重建 + 元数据校验」收成一条命令，供
KgDailySync 收尾自动调用（接入方案见回执，L2 待批，本单不改计划任务）。

用法：
  python tools/datawarehouse/registry_align.py                 # --check（默认只读）
  python tools/datawarehouse/registry_align.py --check --json
  python tools/datawarehouse/registry_align.py --apply --json   # 备份+重建+校验+kg 日志
  可选：--lock PATH（默认 C:\\Temp\\registry_align.lock）
        --kg-log PATH（默认 C:\\Temp\\kg_sync_task.log）
        --catalog PATH / --registry PATH（模拟/测试用；默认主仓数据根）

判定「已对齐」（aligned）：
  1. registry.catalog_sha256 == catalog.json 实算 sha256；
  2. registry.physical_count == len(physical_tables)，且 display_count == len(display_assets)；
  3. catalog 全部表都被 registry physical_tables 覆盖（缺表即需重建）；
  4. lifecycle=planned/deprecated/archived 的表在 registry 的 qualification_status 与之一致；
  5. （信息项，不阻断）registry 中 catalog 已不存在的 orphan 条目 → 重建会保守保留。

退出码：--check：0=已对齐 / 1=需对齐 / 2=前置错误（文件缺失/坏 JSON）。
        --apply：0=已对齐或对齐成功 / 1=写后校验失败已回滚 / 2=前置错误（锁占用/kg 前置/catalog 不可读）。
并发：--apply 持锁 C:\\Temp\\registry_align.lock（O_EXCL + 陈旧锁回收），退出即释放；
      写前读 kg_sync_task.log 尾部（复用 registry_rebuild.find_kg_log_state 口径）防数据根冲突；
      写后追加 registry_align 审计行到 kg 日志（fsync）。
边界：只写 registry + 备份 + kg 日志 + 锁；不碰 catalog/数据/store；--check 零写入。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import shutil
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import common  # noqa: E402
import metadata_consistency as mc  # noqa: E402
import registry_rebuild as rr  # noqa: E402

DEFAULT_LOCK = Path(r"C:\Temp\registry_align.lock")
DEFAULT_KG_LOG = Path(r"C:\Temp\kg_sync_task.log")
LOCK_STALE_SECONDS = 2 * 3600  # 2h 未释放视为陈旧锁（可回收）
TOOL = "registry_align"


# ---------------- 基础 IO ----------------
def _now():
    return datetime.datetime.now().astimezone()


def _iso():
    return _now().isoformat(timespec="seconds")


def _read_json(path: Path, what: str) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeError("%s 不存在：%s" % (what, path))
    except (OSError, ValueError) as exc:
        raise RuntimeError("%s 不可读或非合法 JSON：%s（%s）" % (what, path, str(exc)[:120]))
    if not isinstance(doc, dict):
        raise RuntimeError("%s 结构非法（非对象）：%s" % (what, path))
    return doc


@contextlib.contextmanager
def use_paths(catalog_path=None, registry_path=None):
    """临时替换 common.CATALOG / common.REGISTRY（模拟历史场景 / 单测注入）。"""
    old_cat, old_reg = common.CATALOG, common.REGISTRY
    try:
        if catalog_path:
            common.CATALOG = Path(catalog_path)
        if registry_path:
            common.REGISTRY = Path(registry_path)
        yield
    finally:
        common.CATALOG, common.REGISTRY = old_cat, old_reg


# ---------------- 对齐判定 ----------------
def _covered_tables(registry: dict) -> set:
    covered = set()
    for e in registry.get("physical_tables") or []:
        if not isinstance(e, dict):
            continue
        for t in e.get("physical_tables") or []:
            if t:
                covered.add(str(t))
    return covered


def _lifecycle_status(table: str, lc) -> str:
    if lc is None:
        try:
            import lifecycle as lc  # noqa: WPS433
        except Exception:  # noqa: BLE001
            return "active"
    try:
        return lc.status_for(table)
    except Exception:  # noqa: BLE001
        return "active"


def alignment(registry: dict, catalog: dict, real_sha: str, lifecycle_mod=None) -> dict:
    """纯计算：返回 {aligned, diffs[], info[]}（不写盘、不打印）。"""
    tables = catalog.get("tables") or {}
    phys = [e for e in (registry.get("physical_tables") or []) if isinstance(e, dict)]
    covered = _covered_tables(registry)
    missing = sorted(set(tables) - covered)
    catalog_names = set(tables)
    orphans = sorted({str(e.get("asset_id")) for e in phys
                      if e.get("asset_id") and str(e.get("asset_id")) not in catalog_names})
    by_asset = {str(e.get("asset_id")): e for e in phys if e.get("asset_id")}
    diffs = []

    if registry.get("catalog_sha256") != real_sha:
        diffs.append({"kind": "catalog_sha256", "registry": registry.get("catalog_sha256"),
                      "catalog": real_sha})
    if registry.get("physical_count") != len(phys):
        diffs.append({"kind": "physical_count_self_inconsistent",
                      "declared": registry.get("physical_count"), "actual": len(phys)})
    if registry.get("display_count") != len(registry.get("display_assets") or []):
        diffs.append({"kind": "display_count_self_inconsistent",
                      "declared": registry.get("display_count"),
                      "actual": len(registry.get("display_assets") or [])})
    if missing:
        diffs.append({"kind": "catalog_tables_missing_from_registry",
                      "count": len(missing), "tables": missing[:20]})
    lc = lifecycle_mod
    lifecycle_mismatch = []
    for name in sorted(tables):
        status = _lifecycle_status(name, lc)
        if status in ("planned", "deprecated", "archived"):
            ent = by_asset.get(name)
            if ent is not None and ent.get("qualification_status") != status:
                lifecycle_mismatch.append({"table": name,
                                           "registry": ent.get("qualification_status"),
                                           "lifecycle": status})
    if lifecycle_mismatch:
        diffs.append({"kind": "lifecycle_status_mismatch",
                      "count": len(lifecycle_mismatch),
                      "tables": lifecycle_mismatch[:20]})
    info = []
    if orphans:
        info.append({"kind": "orphan_registry_entries", "count": len(orphans),
                     "tables": orphans[:20]})
    return {"aligned": not diffs, "diffs": diffs, "info": info,
            "missing_tables": missing, "orphans": orphans, "covered": len(covered),
            "catalog_tables": len(tables), "physical_tables": len(phys)}


# ---------------- 锁 ----------------
def acquire_lock(path: Path, stale_seconds: float = LOCK_STALE_SECONDS):
    """尝试取锁；成功返回 info dict，失败返回 None（锁占用）。陈旧锁自动回收。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        age = None
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
            ts = float(meta.get("ts") or 0)
            age = time.time() - ts if ts else None
        except (OSError, ValueError, TypeError):
            try:
                age = time.time() - p.stat().st_mtime
            except OSError:
                age = None
        if age is None or age < stale_seconds:
            return None  # 新鲜锁 → 占用
        try:
            p.unlink()  # 陈旧锁回收（>stale_seconds）
        except OSError:
            return None
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    try:
        os.write(fd, json.dumps({"tool": TOOL, "pid": os.getpid(),
                                 "ts": time.time(), "started": _iso()}).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return {"path": str(p), "pid": os.getpid(), "started": _iso()}


def release_lock(lock_info) -> None:
    if not lock_info:
        return
    try:
        Path(lock_info["path"]).unlink()
    except OSError:
        pass


# ---------------- kg 日志 ----------------
def kg_log_state(kg_log: Path) -> dict:
    """复用 registry_rebuild.find_kg_log_state 的判定口径（临时指定路径）。"""
    p = Path(kg_log)
    old = rr.KG_LOG
    try:
        rr.KG_LOG = p
        return rr.find_kg_log_state()
    finally:
        rr.KG_LOG = old


def append_kg_log(kg_log: Path, line: str) -> dict:
    """同 registry_rebuild 的 fsync 追加；行前保证换行。"""
    p = Path(kg_log)
    try:
        with p.open("a", encoding="utf-8", newline="\n") as fh:
            if fh.tell() > 0:
                with p.open("rb") as rh:
                    rh.seek(-1, os.SEEK_END)
                    if rh.read(1) != b"\n":
                        fh.write("\n")
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return {"appended": line, "path": str(p)}
    except OSError as exc:
        return {"appended": None, "path": str(p), "error": str(exc)[:160]}


# ---------------- 回滚 ----------------
def restore_backup(backup: Path, registry_path: Path) -> dict:
    """原子恢复备份（tmp + os.replace）；返回 {restored, bytes}。"""
    target = Path(registry_path)
    tmp = target.with_name(target.name + ".restore-%d" % os.getpid())
    try:
        shutil.copy2(backup, tmp)
        os.replace(tmp, target)
        doc = json.loads(target.read_text(encoding="utf-8"))
        return {"restored": True, "bytes": target.stat().st_size,
                "catalog_sha256": doc.get("catalog_sha256")}
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


# ---------------- 主流程 ----------------
def _base_result(mode: str) -> dict:
    return {"tool": TOOL, "mode": mode, "generated_at": _iso(),
            "catalog": {}, "registry": {}, "match_before": None,
            "diffs_before": [], "rebuild": {"performed": False},
            "match_after": None, "diffs_after": [], "metadata": None,
            "kg_log": None, "lock": None, "rolled_back": False,
            "exit_code": 0, "error": None, "elapsed_s": 0.0}


def run(mode: str = "check", lock_path=DEFAULT_LOCK, kg_log=DEFAULT_KG_LOG,
        catalog_path=None, registry_path=None, lifecycle_mod=None) -> dict:
    """执行 check / apply。返回完整结果 dict（含 exit_code）。"""
    t0 = time.time()
    res = _base_result(mode)
    with use_paths(catalog_path, registry_path):
        try:
            catalog = _read_json(common.CATALOG, "catalog.json")
            if not isinstance(catalog.get("tables"), dict):
                raise RuntimeError("catalog.json 结构非法（tables 缺失）：%s" % common.CATALOG)
            real_sha = common.sha256_file(common.CATALOG)
            registry = _read_json(common.REGISTRY, "warehouse_registry.json")
        except RuntimeError as exc:
            res["exit_code"] = 2
            res["error"] = str(exc)
            res["metadata"] = mc.check()
            res["elapsed_s"] = round(time.time() - t0, 2)
            return res

        res["catalog"] = {"path": str(common.CATALOG), "version": catalog.get("version"),
                          "tables": len(catalog["tables"]), "sha256": real_sha}
        res["registry"] = {"path": str(common.REGISTRY),
                           "catalog_sha256": registry.get("catalog_sha256"),
                           "physical_count": registry.get("physical_count"),
                           "display_count": registry.get("display_count"),
                           "generated_at": registry.get("generated_at")}
        before = alignment(registry, catalog, real_sha, lifecycle_mod)
        res["match_before"] = before["aligned"]
        res["diffs_before"] = before["diffs"]
        res["info_before"] = before["info"]

        if mode == "check":
            res["metadata"] = mc.check()  # 只读补充信息（不改 registry）
            res["exit_code"] = 0 if before["aligned"] else 1
            res["elapsed_s"] = round(time.time() - t0, 2)
            return res

        # ---- --apply ----
        lock = acquire_lock(Path(lock_path))
        if lock is None:
            res["exit_code"] = 2
            res["error"] = "锁占用：%s（若确认无并发可删除陈旧锁）" % lock_path
            res["lock"] = {"acquired": False, "path": str(lock_path)}
            res["metadata"] = mc.check()
            res["elapsed_s"] = round(time.time() - t0, 2)
            return res
        res["lock"] = {"acquired": True, **lock}
        try:
            kg = kg_log_state(kg_log)
            res["kg_log"] = kg
            if not kg.get("ok"):
                res["exit_code"] = 2
                res["error"] = "拒跑：kg_sync_task.log 前置检查未通过（%s）" % kg.get("reason")
                res["metadata"] = mc.check()
                return res

            if before["aligned"]:
                # 幂等：已对齐 → 不写盘
                res["rebuild"] = {"performed": False, "reason": "already_aligned"}
                res["match_after"] = True
                res["metadata"] = mc.check()
                ok = res["metadata"].get("code") == 0
                res["exit_code"] = 0 if ok else 1
                if not ok:
                    res["error"] = "registry 已对齐但 metadata_consistency 未 PASS（需人工处理）"
                return res

            # 备份 + 重建（复用 registry_rebuild 的 build/backup/atomic/verify）
            built = rr.build_payload(catalog, registry)
            ts = _now().strftime("%Y%m%d-%H%M%S")
            backup = rr.write_backup(ts)
            rr.atomic_write(built["registry"])
            verify = rr.verify_written(built["stats"])
            res["rebuild"] = {"performed": True, "backup": str(backup),
                              "backup_rel": common.rel(backup),
                              "stats": {k: built["stats"][k] for k in (
                                  "catalog_version", "catalog_tables", "old_physical_count",
                                  "added", "physical_count", "display_count")},
                              "verify": verify}

            catalog2 = _read_json(common.CATALOG, "catalog.json")
            real_sha2 = common.sha256_file(common.CATALOG)
            registry2 = _read_json(common.REGISTRY, "warehouse_registry.json")
            after = alignment(registry2, catalog2, real_sha2, lifecycle_mod)
            res["match_after"] = after["aligned"]
            res["diffs_after"] = after["diffs"]
            meta = mc.check()
            res["metadata"] = meta
            # 2026-11-05 修复：registry_rebuild 按设计「保守保留 catalog 已不存在的孤儿条目」，
            # physical_count 允许 > catalog 表数（metadata_consistency 仅记 warning）。
            # 原判定用 catalog 表数当期望值，导致存在孤儿条目时 apply 永远回滚
            # （kg 日志 06:42 registry_align exit=1 即此因）。期望值改为 registry 自身条数，
            # 自洽性由 verify_written(physical_count_match) 保证。
            expected_physical = len(registry2.get("physical_tables") or [])
            ok = (after["aligned"] and verify.get("ok") and meta.get("code") == 0
                  and meta.get("physical_count") == expected_physical)
            if ok:
                line = ("%s date=%s action=apply catalog=v%s sha=%s physical=%s display=%s "
                        "rebuild=ok exit=0" % (
                            TOOL, _iso(), catalog2.get("version"), (real_sha2 or "")[:16],
                            meta.get("physical_count"), registry2.get("display_count")))
                kg_append = append_kg_log(kg_log, line)
                res["kg_log"] = {**kg, **kg_append}
                res["exit_code"] = 0 if not kg_append.get("error") else 1
                if kg_append.get("error"):
                    res["error"] = "registry 已对齐，但 kg 日志追加失败（需人工补记）：%s" % kg_append["error"]
            else:
                # 写后校验失败 → 自动回滚到备份，避免留下坏 registry
                restored = restore_backup(backup, common.REGISTRY)
                res["rolled_back"] = True
                res["restore"] = restored
                res["metadata_after_rollback"] = mc.check()
                res["exit_code"] = 1
                res["error"] = ("对齐后校验失败，已回滚到备份 %s：aligned=%s verify=%s "
                                "metadata_code=%s physical=%s" % (
                                    backup, after["aligned"], verify.get("ok"),
                                    meta.get("code"), meta.get("physical_count")))
            return res
        finally:
            release_lock(lock)


# ---------------- 输出 ----------------
def render_human(res: dict) -> str:
    lines = ["== %s [%s] ==" % (TOOL, res["mode"]),
             "catalog: %s v%s / %s 表 / sha=%s" % (
                 res["catalog"].get("path"), res["catalog"].get("version"),
                 res["catalog"].get("tables"), (res["catalog"].get("sha256") or "")[:16]),
             "registry: %s / sha=%s / physical=%s / display=%s" % (
                 res["registry"].get("path"),
                 (res["registry"].get("catalog_sha256") or "")[:16],
                 res["registry"].get("physical_count"),
                 res["registry"].get("display_count")),
             "match_before: %s" % res["match_before"]]
    for d in res.get("diffs_before") or []:
        lines.append("  DIFF %s %s" % (d.get("kind"), json.dumps(
            {k: v for k, v in d.items() if k != "kind"}, ensure_ascii=False)[:180]))
    if res["mode"] == "apply":
        rb = res.get("rebuild") or {}
        lines.append("rebuild: performed=%s backup=%s" % (
            rb.get("performed"), rb.get("backup") or "-"))
        for key in ("match_after", "rolled_back"):
            if res.get(key) is not None:
                lines.append("%s: %s" % (key, res[key]))
        meta = res.get("metadata") or {}
        lines.append("metadata: code=%s issues=%s warnings=%s physical=%s" % (
            meta.get("code"), len(meta.get("issues") or []),
            len(meta.get("warnings") or []), meta.get("physical_count")))
    if res.get("error"):
        lines.append("ERROR: %s" % res["error"])
    lines.append("exit_code=%s elapsed=%ss" % (res.get("exit_code"), res.get("elapsed_s")))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="catalog→warehouse_registry 自动对齐（默认 --check 只读）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="只读检测（默认）")
    g.add_argument("--apply", action="store_true", help="备份+重建+metadata 校验+kg 日志")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--lock", default=str(DEFAULT_LOCK), help="锁文件路径")
    ap.add_argument("--kg-log", default=str(DEFAULT_KG_LOG), help="kg 互斥日志路径")
    ap.add_argument("--catalog", default=None, help="catalog 路径（模拟/测试用）")
    ap.add_argument("--registry", default=None, help="registry 路径（模拟/测试用）")
    a = ap.parse_args(argv)
    mode = "apply" if a.apply else "check"
    res = run(mode, lock_path=Path(a.lock), kg_log=Path(a.kg_log),
              catalog_path=a.catalog, registry_path=a.registry)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    else:
        print(render_human(res))
    return int(res.get("exit_code", 2))


if __name__ == "__main__":
    sys.exit(main())
