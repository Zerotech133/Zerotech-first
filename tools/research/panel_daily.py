# -*- coding: utf-8 -*-
"""研报面板日常化运行器：刷新门户静态 JSON + 校验 + 状态台账 + 变更摘要。

定位：把 wave5 的手动 `panel_export.py export` 变成可定时执行的日常任务。
      不修改 panel_export.py / selection.html，全部复用其构建与校验函数（importlib 加载）。

一次 `run` 做四件事：
  ① 读 v3 面板 JSON → 复用 panel_export.build_portal_payload 构建门户 payload，
     复用 panel_export.export_panel 写 portal/static/report_panel.json（原子写）；
  ② 校验（panel_export.validate_portal_payload：必需字段/免责声明/候选长度一致），
     失败 → 不发布坏 JSON（回滚/保留旧文件）并退出非 0；
  ③ 追加 research/reports/report_panel_daily_status.json（runs[] 历史 + latest），
     记录 generated_at / 源文件 mtime / 候选数 / 校验结果 / 耗时 / 变更摘要；
  ④ 与上一次运行对比：候选增减（新增/移除 ID）、页码越界变化、被拦截数变化。

`--check-only` 模式（定时巡检用）：不写门户 JSON，只校验现有 portal JSON 与
源文件新旧（源文件比门户新 → stale 警告），结果同样追加状态台账；校验失败退出非 0。

CLI：
  python tools/research/panel_daily.py run                  # 日常刷新（发布+校验+台账）
  python tools/research/panel_daily.py run --check-only     # 巡检模式（只校验+台账）
  python tools/research/panel_daily.py run --dry-run        # 只构建校验，不写门户文件
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kb_corpus"))
from safe_json import SafeJsonError, backup_then_atomic_write  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPO / "research" / "reports" / "report_panel_v3_20261101.json"
DEFAULT_PORTAL = REPO / "portal" / "static" / "report_panel.json"
DEFAULT_STATUS = REPO / "research" / "reports" / "report_panel_daily_status.json"
MAX_RUNS = 60


class PanelDailyError(RuntimeError):
    """日常任务流程异常（输入缺失/发布失败等）。"""


def _load_panel_export():
    path = Path(__file__).with_name("panel_export.py")
    spec = importlib.util.spec_from_file_location("panel_export_for_daily", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise PanelDailyError(f"无法加载 panel_export.py：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pe = _load_panel_export()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def file_meta(path: Path | str) -> dict:
    path = Path(path)
    if not path.exists():
        return {"path": str(path), "exists": False, "mtime": None, "size": 0}
    st = path.stat()
    return {"path": str(path), "exists": True,
            "mtime": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"),
            "mtime_epoch": round(st.st_mtime, 3), "size": st.st_size}


# ---------------------------------------------------------------------------
# 状态台账
# ---------------------------------------------------------------------------
def load_status(status_path: Path | str = DEFAULT_STATUS) -> dict:
    """读状态台账；缺失/损坏时返回空骨架（容错，不抛异常）。"""
    path = Path(status_path)
    empty = {"schema_version": 1, "updated_at": None, "runs": [], "latest": None}
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    data.setdefault("schema_version", 1)
    data.setdefault("runs", [])
    data.setdefault("latest", data["runs"][-1] if data["runs"] else None)
    return data


def append_status(entry: dict, status_path: Path | str = DEFAULT_STATUS,
                  max_runs: int = MAX_RUNS) -> dict:
    """追加一条运行记录（runs[] 保留最近 max_runs 条，latest=最新），原子写。"""
    path = Path(status_path)
    data = load_status(path)
    runs = [r for r in data.get("runs") or [] if isinstance(r, dict)]
    runs.append(entry)
    runs = runs[-max(1, int(max_runs)):]
    data.update({"schema_version": 1, "updated_at": now_iso(), "runs": runs, "latest": runs[-1]})
    backup_then_atomic_write(path, data)  # 备份+原子写+写后 JSON 校验
    return data


# ---------------------------------------------------------------------------
# 变更摘要
# ---------------------------------------------------------------------------
def change_summary(prev: dict | None, cur: dict) -> dict:
    """与上次运行对比：候选新增/移除、页码越界/被拦截变化。"""
    if not isinstance(prev, dict) or not prev:
        return {"has_prev": False, "note": "首次运行，无历史对比"}
    prev_ids = [str(x) for x in prev.get("candidate_ids") or []]
    cur_ids = [str(x) for x in cur.get("candidate_ids") or []]
    prev_page = int(prev.get("page_invalid_facts") or 0)
    cur_page = int(cur.get("page_invalid_facts") or 0)
    prev_rej = int(prev.get("rejected") or 0)
    cur_rej = int(cur.get("rejected") or 0)
    return {
        "has_prev": True,
        "prev_run_at": prev.get("run_at"),
        "candidates": {"prev": int(prev.get("candidates") or 0), "cur": int(cur.get("candidates") or 0),
                       "delta": int(cur.get("candidates") or 0) - int(prev.get("candidates") or 0),
                       "added": [i for i in cur_ids if i not in prev_ids],
                       "removed": [i for i in prev_ids if i not in cur_ids]},
        "page_invalid_facts": {"prev": prev_page, "cur": cur_page, "delta": cur_page - prev_page},
        "rejected": {"prev": prev_rej, "cur": cur_rej, "delta": cur_rej - prev_rej},
        "source_generated_at_changed": prev.get("source_generated_at") != cur.get("source_generated_at"),
    }


def format_change(change: dict) -> str:
    if not change.get("has_prev"):
        return "首次运行，无历史对比"
    c = change["candidates"]
    p = change["page_invalid_facts"]
    parts = [f"候选 {c['prev']}→{c['cur']}（Δ{c['delta']:+d}"
             + (f"，新增 {', '.join(c['added'])}" if c["added"] else "")
             + (f"，移除 {', '.join(c['removed'])}" if c["removed"] else "") + "）",
             f"页码越界 {p['prev']}→{p['cur']}（Δ{p['delta']:+d}）"]
    if p["delta"] > 0:
        parts.append("⚠ 页码越界增加，需人工复核")
    if c["removed"]:
        parts.append("ℹ 存在被降维研报，注意候选池变化")
    return "；".join(parts)


# ---------------------------------------------------------------------------
# 校验 / 运行
# ---------------------------------------------------------------------------
def _payload_stats(payload: dict) -> dict:
    summary = payload.get("summary") or {}
    review = payload.get("review") or {}
    return {
        "candidates": len(payload.get("candidates") or []),
        "candidate_ids": [str(c.get("report_id")) for c in payload.get("candidates") or []],
        "rejected": len(payload.get("rejected") or []),
        "reports": len(payload.get("reports") or []),
        "page_invalid_facts": summary.get("page_invalid_facts", 0),
        "legacy_union_invalid_facts": summary.get("legacy_union_invalid_facts", 0),
        "review_checkboxes": review.get("open_checkboxes", 0),
        "disclaimer_ok": ("仅供研究" in str(payload.get("disclaimer") or "")
                          and "不构成投资建议" in str(payload.get("disclaimer") or "")),
        "source_generated_at": payload.get("generated_at"),
    }


def _base_entry(mode: str, source: Path | str, portal: Path | str, elapsed: float) -> dict:
    return {"run_at": now_iso(), "mode": mode, "ok": False, "errors": [],
            "source": str(source), "source_meta": file_meta(source),
            "portal": str(portal), "portal_meta": file_meta(portal),
            "elapsed_seconds": round(elapsed, 3)}


def check_only(portal_path: Path | str = DEFAULT_PORTAL,
               source_path: Path | str = DEFAULT_SOURCE,
               status_path: Path | str = DEFAULT_STATUS) -> tuple[int, dict]:
    """巡检模式：只校验现有门户 JSON（不重写），台账追加 mode=check_only。"""
    started = time.monotonic()
    portal_path, source_path = Path(portal_path), Path(source_path)
    entry = _base_entry("check_only", source_path, portal_path, 0.0)
    errors = pe.check_portal_json(portal_path)
    payload = None
    if not errors:
        try:
            payload = pe.load_json(portal_path)
        except pe.PanelExportError as exc:
            errors = [str(exc)]
    if errors:
        entry.update({"ok": False, "errors": errors,
                      "validation": {"ok": False, "errors": errors, "checked_at": now_iso()}})
    else:
        stats = _payload_stats(payload)
        src = file_meta(source_path)
        stale = bool(src.get("exists") and entry["portal_meta"].get("exists")
                     and src["mtime_epoch"] > entry["portal_meta"]["mtime_epoch"])
        entry.update({**stats, "ok": True, "errors": [],
                      "stale": stale,
                      "stale_note": ("源文件比门户 JSON 新，需跑一次 run 刷新" if stale else None),
                      "validation": {"ok": True, "errors": [], "checked_at": now_iso()}})
    entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
    prev = load_status(status_path).get("latest")
    entry["change"] = change_summary(prev, entry)
    append_status(entry, status_path)
    return (0 if entry["ok"] else 1), entry


def run_daily(source_path: Path | str = DEFAULT_SOURCE, portal_path: Path | str = DEFAULT_PORTAL,
              status_path: Path | str = DEFAULT_STATUS, check_only_mode: bool = False,
              dry_run: bool = False) -> tuple[int, dict]:
    """日常运行：check_only → 巡检；否则构建→校验→发布→台账→变更摘要。"""
    if check_only_mode:
        return check_only(portal_path, source_path, status_path)
    started = time.monotonic()
    source_path, portal_path = Path(source_path), Path(portal_path)
    entry = _base_entry("run", source_path, portal_path, 0.0)
    prev = load_status(status_path).get("latest")
    payload = None
    errors: list[str] = []
    try:
        v3 = pe.load_json(source_path)
        payload = pe.build_portal_payload(v3, source_path=source_path)
        errors = pe.validate_portal_payload(payload)
        if errors:
            raise PanelDailyError("构建后校验失败：" + "；".join(errors))
        if not dry_run:
            # 备份+原子发布：校验失败不覆盖旧门户 JSON
            def _portal_validator(parsed):
                errs = pe.validate_portal_payload(parsed)
                if errs:
                    raise ValueError("；".join(errs))
                return None
            try:
                backup_then_atomic_write(portal_path, payload, validator=_portal_validator)
            except SafeJsonError as exc:
                raise PanelDailyError("发布校验失败：" + str(exc)) from exc
    except (pe.PanelExportError, PanelDailyError) as exc:
        errors = errors or [str(exc)]
        entry.update({"ok": False, "errors": errors,
                      "validation": {"ok": False, "errors": errors, "checked_at": now_iso()}})
        entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
        entry["change"] = change_summary(prev, entry)
        append_status(entry, status_path)
        return 1, entry
    stats = _payload_stats(payload)
    portal_errors = [] if dry_run else pe.check_portal_json(portal_path)
    if portal_errors:
        entry.update({**stats, "ok": False, "errors": portal_errors,
                      "validation": {"ok": False, "errors": portal_errors, "checked_at": now_iso()}})
    else:
        entry.update({**stats, "ok": True, "errors": [],
                      "dry_run": bool(dry_run),
                      "portal_meta": file_meta(portal_path),
                      "validation": {"ok": True, "errors": [], "checked_at": now_iso()}})
    entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
    entry["change"] = change_summary(prev, entry)
    append_status(entry, status_path)
    return (0 if entry["ok"] else 1), entry


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="研报面板日常化运行器（刷新门户静态 JSON + 校验 + 状态台账）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="刷新门户静态 JSON 并记录状态/变更摘要")
    run.add_argument("--source", default=str(DEFAULT_SOURCE), help="v3 面板 JSON 路径")
    run.add_argument("--portal", default=str(DEFAULT_PORTAL), help="门户静态 JSON 路径")
    run.add_argument("--status", default=str(DEFAULT_STATUS), help="状态台账路径")
    run.add_argument("--check-only", action="store_true", help="巡检模式：只校验现有门户 JSON，不重写")
    run.add_argument("--dry-run", action="store_true", help="只构建+校验，不写门户文件（仍记台账）")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    code, entry = run_daily(args.source, args.portal, args.status,
                            check_only_mode=args.check_only, dry_run=args.dry_run)
    mode = entry.get("mode")
    status = "OK" if entry.get("ok") else "FAILED"
    print(f"[panel_daily] mode={mode} status={status} elapsed={entry.get('elapsed_seconds')}s")
    if entry.get("ok"):
        print(f"[panel_daily] candidates={entry.get('candidates')} rejected={entry.get('rejected')} "
              f"reports={entry.get('reports')} page_invalid={entry.get('page_invalid_facts')} "
              f"generated_at={entry.get('source_generated_at')}")
        if entry.get("stale"):
            print(f"[panel_daily] ⚠ {entry.get('stale_note')}")
    else:
        print("[panel_daily] errors=" + "；".join(entry.get("errors") or []), file=sys.stderr)
    print(f"[panel_daily] change: {format_change(entry.get('change') or {})}")
    print(f"[panel_daily] status_file={args.status}")
    return code


if __name__ == "__main__":
    sys.exit(main())
