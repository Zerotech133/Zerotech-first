# -*- coding: utf-8 -*-
"""post_sync_qualify：首夜同步后「资质复判自动化」工具（2026-11-02 task-73）。

用途（每次同步后一条命令）：
  1. 读 registry 中 `qualification_status=unknown` 的表（或 --tables 指定）；
  2. 分批（≤20/批）调用 `qualify_probe.py --dry-run --json` 复判，汇总 verdict/reasons；
  3. 仅当 `--apply` 时，对 verdict=evidence_ok 的表分批调用 `qualify_probe.py --apply --json`
     写回——**完全复用** qualify_probe 的备份/双阶段 validation/kg 日志校验，本工具不直接写 registry；
  4. 输出 JSON 摘要（before/after 分布、升 ok 清单、partial/bad 归因）+ 人类摘要；
     写回失败/复判失败 → exit 2（fail-visible）；无 unknown → exit 0。

用法：
  python tools/datawarehouse/post_sync_qualify.py --dry-run --json \
      --out C:\\Temp\\post_sync_qualify_<ts>.json
  python tools/datawarehouse/post_sync_qualify.py --apply --json \
      --out C:\\Temp\\post_sync_qualify_<ts>_apply.json
  python tools/datawarehouse/post_sync_qualify.py --tables a,b --batch-size 10 --dry-run

退出码：0 = 复判完成/无 unknown/写回成功；2 = 复判或写回失败（含 validation 非全 true）；
  1 = 参数错误（argparse 默认 2，此处保留 0/2 语义）。

边界：本工具只写 C:\\Temp 日志/--out 摘要；registry 写回只经 qualify_probe --apply。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOLS = Path(__file__).resolve().parent
QUALIFY_PROBE = TOOLS / "qualify_probe.py"
REGISTRY = REPO / "quant_backtest" / "data" / "warehouse_registry.json"
DEFAULT_LOG = Path(r"C:\Temp\post_sync_qualify.log")
BATCH_LIMIT = 20
DEFAULT_TIMEOUT = 600
EXIT_OK, EXIT_FAIL = 0, 2


def _now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def append_log(path: Path | None, line: str) -> None:
    """单行 JSON 日志追加（best-effort；日志失败不影响主流程）。"""
    if path is None:
        return
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": _now_iso(), "event": line}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_registry(path: Path) -> dict:
    """只读 registry；失败抛 RuntimeError（fail-visible）。"""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeError("registry 不存在：%s" % path)
    except (OSError, ValueError) as exc:
        raise RuntimeError("registry 不可读：%s: %s" % (path, str(exc)[:120]))
    if not isinstance(doc, dict) or not isinstance(doc.get("physical_tables"), list):
        raise RuntimeError("registry 结构非法（physical_tables 缺失）：%s" % path)
    return doc


def distribution(reg: dict) -> dict:
    return dict(Counter(e.get("qualification_status")
                        for e in reg.get("physical_tables") or []))


def unknown_tables(reg: dict) -> list:
    return [e.get("asset_id") for e in reg.get("physical_tables") or []
            if e.get("qualification_status") == "unknown" and e.get("asset_id")]


def chunked(seq, size: int) -> list:
    size = max(1, int(size))
    return [list(seq[i:i + size]) for i in range(0, len(seq), size)]


def _run_qualify(tables: list, apply: bool, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """调用 qualify_probe CLI（--json）→ {rc, payload, stdout_tail, error}。"""
    cmd = [sys.executable, "-X", "utf8", str(QUALIFY_PROBE),
           "--tables", ",".join(tables), "--json"]
    cmd.append("--apply" if apply else "--dry-run")
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, cwd=str(REPO), env=env)
    except subprocess.TimeoutExpired:
        return {"rc": -1, "payload": None, "stdout_tail": "",
                "error": "qualify_probe 超时（%.0fs）" % timeout}
    except OSError as exc:
        return {"rc": -1, "payload": None, "stdout_tail": "",
                "error": "qualify_probe 启动失败：%s" % str(exc)[:160]}
    payload = None
    err = None
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        err = "qualify_probe 输出非 JSON（rc=%s）：%s" % (
            proc.returncode, (proc.stdout or proc.stderr or "")[-300:].replace("\n", " "))
    return {"rc": proc.returncode, "payload": payload,
            "stdout_tail": (proc.stdout or "")[-2000:], "error": err,
            "stderr_tail": (proc.stderr or "")[-800:]}


def _cards(payload: dict) -> list:
    cards = payload.get("tables") or []
    return [c for c in cards if isinstance(c, dict) and c.get("table")]


def _parse_probe_result(res: dict) -> dict:
    """校验 probe 返回并提取 cards；失败返回 error 描述（None=成功）。"""
    if res.get("error"):
        return {"error": res["error"]}
    payload = res.get("payload")
    if not isinstance(payload, dict):
        return {"error": "qualify_probe JSON 结构非法"}
    if res.get("rc") not in (0, 1):
        return {"error": "qualify_probe rc=%s（code=%s, error=%s）"
                         % (res.get("rc"), payload.get("code"), payload.get("error"))}
    cards = _cards(payload)
    if not cards:
        return {"error": "qualify_probe 返回 0 张表（code=%s）" % payload.get("code")}
    return {"cards": cards, "payload": payload}


def run(registry_path: Path | None = None, *, apply: bool = False,
        tables: list | None = None, batch_size: int = BATCH_LIMIT,
        timeout: float = DEFAULT_TIMEOUT, probe=None,
        read_registry_fn=None, log_path=DEFAULT_LOG,
        registry_label: str | None = None) -> dict:
    """执行复判（+可选写回）→ JSON 摘要 dict。probe/read_registry_fn 可注入（测试）。"""
    registry_path = Path(registry_path or REGISTRY)
    probe = probe or _run_qualify
    read_reg = read_registry_fn or read_registry
    t0 = time.time()
    summary = {
        "tool": "post_sync_qualify",
        "generated_at": _now_iso(),
        "mode": "apply" if apply else "dry_run",
        "registry": {"path": registry_label or str(registry_path)},
        "qualify_probe": {"path": str(QUALIFY_PROBE)},
        "batches": [],
        "dry_run_summary": {"ok": [], "partial": [], "bad": [],
                            "verdicts": {}, "by_reason": {}},
        "apply": {"requested": bool(apply), "batches": [], "ok_tables": [],
                  "applied_tables": [], "failed": False, "error": None},
        "after": None,
        "exit_code": EXIT_OK,
        "elapsed_s": 0.0,
    }
    try:
        before_reg = read_reg(registry_path)
    except RuntimeError as exc:
        summary["error"] = str(exc)
        summary["exit_code"] = EXIT_FAIL
        summary["elapsed_s"] = round(time.time() - t0, 1)
        append_log(log_path, "error registry=%s error=%s rc=%d"
                   % (summary["registry"]["path"], str(exc)[:120], EXIT_FAIL))
        return summary
    before_dist = distribution(before_reg)
    unknown = tables if tables is not None else unknown_tables(before_reg)
    unknown = [t for t in dict.fromkeys(unknown or []) if t]  # 去重保序
    batches = chunked(unknown, batch_size) if unknown else []
    summary["registry"].update({
        "physical_count": before_reg.get("physical_count"),
        "catalog_sha256": before_reg.get("catalog_sha256"),
        "before": {"distribution": before_dist, "unknown": unknown},
        "unknown_count": len(unknown),
        "batch_size": batch_size,
    })
    append_log(log_path, "start mode=%s registry=%s unknown=%d batches=%d"
               % (summary["mode"], summary["registry"]["path"], len(unknown), len(batches)))

    ok, partial, bad, errors = [], [], [], []
    reason_counter = Counter()
    if not unknown:
        summary["dry_run_summary"]["verdicts"] = {"ok": 0, "partial": 0, "bad": 0,
                                                  "unknown": 0}
        summary["exit_code"] = EXIT_OK
        summary["elapsed_s"] = round(time.time() - t0, 1)
        append_log(log_path, "nothing_to_do unknown=0 rc=0")
        if apply:
            summary["after"] = {"distribution": before_dist,
                                "unknown_count": 0, "upgraded_count": 0}
        return summary

    # ① 分批 dry-run（只读）
    for i, batch in enumerate(batches, 1):
        res = probe(batch, False, timeout)
        parsed = _parse_probe_result(res)
        entry = {"batch": i, "tables": batch, "rc": res.get("rc"),
                 "ok": [], "partial": [], "bad": [], "error": None}
        if parsed.get("error"):
            entry["error"] = parsed["error"]
            errors.append({"stage": "dry_run", "batch": i, "error": parsed["error"]})
        else:
            for card in parsed["cards"]:
                verdict = card.get("verdict")
                reason = "/".join(card.get("reasons") or [])
                entry.setdefault("reasons", {})[card["table"]] = reason
                if verdict == "evidence_ok":
                    entry["ok"].append(card["table"])
                    ok.append(card["table"])
                elif verdict == "evidence_bad":
                    entry["bad"].append(card["table"])
                    bad.append(card["table"])
                else:
                    entry["partial"].append(card["table"])
                    partial.append(card["table"])
                for r in card.get("reasons") or []:
                    reason_counter[r] += 1
        summary["batches"].append(entry)
        append_log(log_path, "dry_run batch=%d/%d tables=%d rc=%s ok=%d partial=%d bad=%d%s"
                   % (i, len(batches), len(batch), res.get("rc"),
                      len(entry["ok"]), len(entry["partial"]), len(entry["bad"]),
                      (" error=" + entry["error"][:120]) if entry["error"] else ""))
        if entry["error"] and res.get("rc") not in (0, 1):
            break  # 环境级错误（catalog/registry 失配等）不再继续打批

    summary["dry_run_summary"] = {
        "ok": ok, "partial": partial, "bad": bad,
        "verdicts": {"ok": len(ok), "partial": len(partial), "bad": len(bad),
                     "unknown": len(unknown)},
        "by_reason": dict(reason_counter.most_common()),
    }
    if errors:
        summary["exit_code"] = EXIT_FAIL
        summary["apply"]["error"] = "dry-run 存在失败批次"

    # ② 仅 ok 且 --apply → 分批写回（复用 qualify_probe apply 的备份/validation/kg 日志）
    if apply and ok and not errors:
        ok_batches = chunked(ok, batch_size)
        applied = []
        for i, batch in enumerate(ok_batches, 1):
            res = probe(batch, True, timeout)
            parsed = _parse_probe_result(res)
            rw = (parsed.get("payload") or {}).get("registry_write") or {}
            validation_ok = bool((rw.get("validation") or {}).get("ok"))
            success = (not parsed.get("error")) and rw.get("applied") is True \
                and validation_ok and res.get("rc") == 0
            entry = {"batch": i, "tables": batch, "rc": res.get("rc"),
                     "applied": bool(rw.get("applied")), "validation_ok": validation_ok,
                     "backup": rw.get("backup"), "success": success,
                     "error": parsed.get("error")}
            summary["apply"]["batches"].append(entry)
            if success:
                applied.extend(batch)
            else:
                summary["apply"]["failed"] = True
                summary["apply"]["error"] = (parsed.get("error")
                                             or "写回失败：applied=%s validation_ok=%s rc=%s"
                                             % (rw.get("applied"), validation_ok, res.get("rc")))
                summary["exit_code"] = EXIT_FAIL
                append_log(log_path, "apply FAILED batch=%d/%d tables=%d rc=%s error=%s"
                           % (i, len(ok_batches), len(batch), res.get("rc"),
                              str(summary["apply"]["error"])[:160]))
                break
            append_log(log_path, "apply ok batch=%d/%d tables=%d backup=%s validation=True"
                       % (i, len(ok_batches), len(batch), rw.get("backup")))
        summary["apply"]["ok_tables"] = ok
        summary["apply"]["applied_tables"] = applied
        summary["apply"]["skipped_tables"] = [t for t in ok if t not in applied]
        if errors:
            summary["apply"]["error"] = "dry-run 存在失败批次，跳过写回"
    elif apply and not ok:
        summary["apply"]["error"] = "无 verdict=ok 的表，跳过写回"
        summary["apply"]["skipped_tables"] = []
        append_log(log_path, "apply skipped: no ok tables")

    # ③ after 分布（apply 成功/无写回都重读；dry-run 用 before）
    if apply and summary["apply"]["applied_tables"]:
        try:
            after_reg = read_reg(registry_path)
            after_dist = distribution(after_reg)
            unknown_after = len(unknown_tables(after_reg))
            upgraded = [t for t in summary["apply"]["applied_tables"]
                        if next((e.get("qualification_status")
                                 for e in after_reg.get("physical_tables") or []
                                 if e.get("asset_id") == t), None) == "evidence_ok"]
            summary["after"] = {"distribution": after_dist,
                                "unknown_count": unknown_after,
                                "upgraded_count": len(upgraded),
                                "upgraded_tables": upgraded,
                                "reread_from": str(registry_path)}
        except RuntimeError as exc:
            summary["after"] = {"error": str(exc)}
            summary["exit_code"] = EXIT_FAIL
    else:
        summary["after"] = {"distribution": before_dist,
                            "unknown_count": len(unknown),
                            "upgraded_count": 0, "written": False}

    summary["elapsed_s"] = round(time.time() - t0, 1)
    append_log(log_path, "done mode=%s before_unknown=%d ok=%d partial=%d bad=%d "
               "applied=%d after_unknown=%s rc=%d"
               % (summary["mode"], len(unknown), len(ok), len(partial), len(bad),
                  len(summary["apply"]["applied_tables"]),
                  (summary["after"] or {}).get("unknown_count"),
                  summary["exit_code"]))
    return summary


def render_human(summary: dict) -> str:
    lines = [
        "== post_sync_qualify（%s）==" % summary.get("mode"),
        "registry: %s" % summary["registry"].get("path"),
        "before: %s；unknown=%s" % (
            json.dumps(summary["registry"]["before"]["distribution"], ensure_ascii=False),
            summary["registry"].get("unknown_count")),
        "batch: size=%s 批数=%s" % (
            summary["registry"].get("batch_size"), len(summary.get("batches") or [])),
        "verdicts: %s" % json.dumps(
            summary["dry_run_summary"].get("verdicts"), ensure_ascii=False),
    ]
    if summary["dry_run_summary"].get("ok"):
        lines.append("升 ok 候选（%d）：%s" % (
            len(summary["dry_run_summary"]["ok"]),
            ",".join(summary["dry_run_summary"]["ok"])))
    if summary["dry_run_summary"].get("by_reason"):
        lines.append("仍不 ok 归因：%s" % json.dumps(
            summary["dry_run_summary"]["by_reason"], ensure_ascii=False))
    if summary["apply"].get("requested"):
        a = summary["apply"]
        lines.append("apply: applied=%d/%d failed=%s%s" % (
            len(a.get("applied_tables") or []), len(a.get("ok_tables") or []),
            a.get("failed"), (" error=" + str(a.get("error"))[:160]) if a.get("error") else ""))
        for b in a.get("batches") or []:
            lines.append("  - batch%d applied=%s validation_ok=%s backup=%s" % (
                b.get("batch"), b.get("applied"), b.get("validation_ok"), b.get("backup")))
    after = summary.get("after") or {}
    if after.get("distribution"):
        lines.append("after: %s；unknown=%s；upgraded=%s" % (
            json.dumps(after["distribution"], ensure_ascii=False),
            after.get("unknown_count"), after.get("upgraded_count")))
    lines.append("exit_code=%s elapsed=%ss" % (summary.get("exit_code"),
                                              summary.get("elapsed_s")))
    if summary.get("error"):
        lines.append("ERROR: %s" % summary["error"])
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="首夜同步后资质复判自动化（读 unknown → 分批 dry-run → 仅 ok 可写回）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="只读复判（默认）")
    g.add_argument("--apply", action="store_true", help="对 verdict=ok 的表写回 registry")
    ap.add_argument("--tables", default="", help="逗号分隔表名（默认 registry 全部 unknown）")
    ap.add_argument("--batch-size", type=int, default=BATCH_LIMIT,
                    help="每批表数（上限 %d）" % BATCH_LIMIT)
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="单批超时秒")
    ap.add_argument("--json", action="store_true", help="stdout 输出 JSON 摘要")
    ap.add_argument("--out", default="", help="可选：JSON 摘要写入路径")
    ap.add_argument("--log", default=str(DEFAULT_LOG), help="日志路径（默认 C:\\Temp\\post_sync_qualify.log）")
    ap.add_argument("--registry", default=str(REGISTRY), help="registry 路径（默认主仓数据根）")
    a = ap.parse_args(argv)
    if a.batch_size < 1 or a.batch_size > BATCH_LIMIT:
        print("--batch-size 需在 1~%d（≤%d/批）" % (BATCH_LIMIT, BATCH_LIMIT), file=sys.stderr)
        return EXIT_FAIL
    tables = [t.strip() for t in a.tables.split(",") if t.strip()] or None
    summary = run(Path(a.registry), apply=a.apply, tables=tables,
                  batch_size=a.batch_size, timeout=a.timeout,
                  log_path=Path(a.log) if a.log else None,
                  registry_label=a.registry)
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
    if a.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(render_human(summary))
    return int(summary.get("exit_code", EXIT_FAIL))


if __name__ == "__main__":
    sys.exit(main())
