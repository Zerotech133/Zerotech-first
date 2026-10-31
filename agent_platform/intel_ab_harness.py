# -*- coding: utf-8 -*-
"""情报 A/B 隔离运行器（WO-INTEL-ABHARNESS-20261031 / task-20）。

用途：为 `INTEL_TO_N1`（news_event 文本）与 `INTEL_TO_D1`（ledger 情报证据）做
**隔离、可审计、不篡改生产**的 A/B：
  1) 输入 codes + 两臂目录 + flags → 生成 off/on 两条 `agent_platform/run_shadow.py`
     命令（默认 `--dry-run` **只打印不执行**；`--run` 才真正执行）；
  2) 两臂各自 `--rehearsal --root <隔离根>` 运行，ledger 落
     `<root>/shadow/shadow_ledger.jsonl`（隔离守卫：拒绝生产 data 根 / 生产 ledger 路径）；
  3) 读取两臂 ledger，按 (date, symbol) 对齐，对比：
     `intel_evidence.source`、recommendation/confidence/parse_ok 差异、fallback 率；
  4) 诚实性：当前 `jev_review_queue` 0 resolved 时明确报告「B 臂应全 fallback」，
     `improvement_claims` 恒为空，不伪造改进。

本工具自身**离线**：只读写本地文件/只读 sqlite（news.db `mode=ro`），不发起网络请求；
`--run` 启动的是隔离数据根的 run_shadow（真实链路含行情/模型，需另行授权），本单不跑。

CLI 示例：
    python -m agent_platform.intel_ab_harness --codes 600519,000858 \
        --off-root C:\\Temp\\intel_ab_off --on-root C:\\Temp\\intel_ab_on
    python -m agent_platform.intel_ab_harness ... --run           # 显式执行两臂
    python -m agent_platform.intel_ab_harness ... --compare --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUN_SHADOW = REPO / "agent_platform" / "run_shadow.py"
PRODUCTION_DATA_ROOTS = (REPO / "agent_platform" / "data", REPO / "data")
PRODUCTION_LEDGER = REPO / "agent_platform" / "data" / "shadow" / "shadow_ledger.jsonl"
DEFAULT_OFF_FLAGS = {"INTEL_TO_N1": "0", "INTEL_TO_D1": "0"}
DEFAULT_ON_FLAGS = {"INTEL_TO_N1": "1", "INTEL_TO_D1": "1"}
BASE_ENV = {"SHADOW_REHEARSAL": "1", "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1"}
CODE_RE = re.compile(r"^(?:sh|sz|bj)?(\d{6})$", re.IGNORECASE)
FLAG_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,80}$")
SCHEMA = "intel_ab_report/v1"


# ---------------------------------------------------------------- 参数解析
def parse_codes(raw) -> list:
    """'600519,sz000858;601318' → ['600519','000858','601318']；非法 fail-visible。"""
    out = []
    for token in re.split(r"[,;\s]+", str(raw or "").strip()):
        token = token.strip()
        if not token:
            continue
        m = CODE_RE.match(token)
        if not m:
            raise ValueError("非法股票代码：%r（需 6 位，可带 sh/sz/bj 前缀）" % token)
        code = m.group(1)
        if code not in out:
            out.append(code)
    if not out:
        raise ValueError("codes 为空：至少给一个 6 位代码")
    return out


def parse_flags(raw, base=None) -> dict:
    """'INTEL_TO_N1=1,INTEL_TO_D1=0' → dict；键名/换行校验，非法 fail-visible。"""
    flags = dict(base or {})
    for item in str(raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError("flag 需 KEY=VALUE 形式：%r" % item)
        key, value = item.split("=", 1)
        key, value = key.strip(), value.strip()
        if not FLAG_KEY_RE.match(key):
            raise ValueError("非法 flag 名：%r" % key)
        if "\n" in value or "\r" in value:
            raise ValueError("flag 值不得含换行：%r" % key)
        flags[key] = value
    return flags


# ---------------------------------------------------------------- 命令生成
def _arm_ledger(root: Path) -> Path:
    return Path(root) / "shadow" / "shadow_ledger.jsonl"


def _check_root(root, arm: str) -> Path:
    path = Path(root).expanduser()
    if not path.is_absolute():
        raise ValueError("%s root 必须是绝对路径：%r" % (arm, str(root)))
    resolved = path.resolve()
    production = {p.resolve() for p in PRODUCTION_DATA_ROOTS}
    if resolved in production:
        raise ValueError(
            "%s root 指向生产 data 根（%s）——A/B 必须用隔离目录，拒绝执行"
            % (arm, resolved))
    ledger = _arm_ledger(resolved).resolve()
    if ledger == PRODUCTION_LEDGER.resolve():
        raise ValueError("%s 的 ledger 路径等于生产 shadow_ledger，拒绝执行" % arm)
    return resolved


def build_plan(codes, off_root, on_root, *, on_flags=None, off_flags=None,
               intel_db=None, python=None, timeout=3600, extra_args=None) -> dict:
    """生成两臂执行计划（纯计算，不执行任何命令）。"""
    codes = list(codes) if not isinstance(codes, str) else parse_codes(codes)
    if not codes:
        raise ValueError("codes 为空")
    on_flags = dict(DEFAULT_ON_FLAGS if on_flags is None else on_flags)
    off_flags = dict(DEFAULT_OFF_FLAGS if off_flags is None else off_flags)
    off_root = _check_root(off_root, "off")
    on_root = _check_root(on_root, "on")
    if off_root == on_root:
        raise ValueError("off/on root 不能相同：%s" % off_root)
    exe = python or sys.executable
    extra = [str(x) for x in (extra_args or [])]
    common_env = dict(BASE_ENV)
    if intel_db:
        common_env["INTEL_N1_DB"] = str(Path(intel_db).expanduser())
    arms = []
    for arm, root, flags in (("off", off_root, off_flags),
                             ("on", on_root, on_flags)):
        env = dict(common_env)
        env.update({str(k): str(v) for k, v in flags.items()})
        env["SHADOW_DATA_ROOT"] = str(root)
        argv = [str(exe), str(RUN_SHADOW), "--rehearsal", "--root", str(root),
                "--codes", ",".join(codes)] + extra
        arms.append({
            "arm": arm,
            "root": str(root),
            "ledger": str(_arm_ledger(root)),
            "flags": {str(k): str(v) for k, v in flags.items()},
            "env": env,
            "argv": argv,
            "cwd": str(REPO),
            "display": _display_command(env, argv),
        })
    return {"ok": True, "schema": "intel_ab_plan/v1", "codes": codes,
            "off_root": str(off_root), "on_root": str(on_root),
            "arms": arms,
            "notes": ["默认 --dry-run 只打印；--run 才执行（隔离 rehearsal 根）",
                      "生产 data 根/生产 ledger 路径一律拒绝"]}


# `build_commands` 别名（便于调用方按语义取用）
build_commands = build_plan


def _display_command(env: dict, argv: list) -> str:
    keys = ("INTEL_TO_N1", "INTEL_TO_D1", "INTEL_N1_DB", "SHADOW_DATA_ROOT")
    prefix = " ".join("%s=%s" % (k, env[k]) for k in keys if k in env)
    def _q(a: str) -> str:
        return '"%s"' % a if any(ch in a for ch in (" ", "\\", ",", ";")) else a
    quoted = " ".join(_q(a) for a in argv)
    return prefix + " " + quoted


# ---------------------------------------------------------------- 执行
def run_arm(arm: dict, *, timeout: float = 3600, runner=None) -> dict:
    """执行单臂（子进程 run_shadow），stdout/stderr 落 <root>/ab_run_<arm>.log。"""
    root = Path(arm["root"])
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / ("ab_run_%s.log" % arm["arm"])
    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in (arm.get("env") or {}).items()})
    started = time.time()
    runner = runner or subprocess.run
    try:
        cp = runner(arm["argv"], cwd=arm.get("cwd") or str(REPO), env=env,
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = ((exc.stdout or "") + (exc.stderr or "")) if isinstance(
            exc.stdout, str) else ""
        log_path.write_text(str(out)[-200_000:], encoding="utf-8")
        return {"arm": arm["arm"], "ok": False, "error": "timeout",
                "returncode": None, "log": str(log_path),
                "elapsed_s": round(time.time() - started, 1)}
    except OSError as exc:
        return {"arm": arm["arm"], "ok": False, "error": "exec_failed: %s"
                % str(exc)[:160], "returncode": None, "log": "",
                "elapsed_s": round(time.time() - started, 1)}
    out = str(cp.stdout or "") + str(cp.stderr or "")
    log_path.write_text(out[-200_000:], encoding="utf-8")
    return {"arm": arm["arm"], "ok": cp.returncode == 0,
            "returncode": cp.returncode, "log": str(log_path),
            "elapsed_s": round(time.time() - started, 1)}


# ---------------------------------------------------------------- 读 ledger
def read_ledger(path) -> tuple:
    """读 jsonl ledger → (rows, errors)。坏行/坏文件 fail-visible，不抛。"""
    path = Path(path)
    rows, errors = [], []
    if not path.is_file():
        errors.append({"error": "ledger_missing", "path": str(path)})
        return rows, errors
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        errors.append({"error": "ledger_read_failed", "path": str(path),
                       "detail": str(exc)[:160]})
        return rows, errors
    for no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append({"error": "bad_json", "line": no,
                           "detail": str(exc)[:120]})
            continue
        if not isinstance(row, dict):
            errors.append({"error": "bad_row_type", "line": no})
            continue
        if not row.get("date") or not row.get("symbol"):
            errors.append({"error": "missing_date_or_symbol", "line": no})
            continue
        rows.append(row)
    return rows, errors


# ---------------------------------------------------------------- 对比
def _key(row: dict) -> tuple:
    return (str(row.get("date") or ""), str(row.get("symbol") or ""))


def _num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _arm_stats(rows) -> dict:
    done = [r for r in rows if str(r.get("status") or "") == "done"]
    fallback_known = [r for r in done if r.get("fallback") is not None]
    fallback_n = sum(1 for r in fallback_known if r.get("fallback") is True)
    evidence_rows = [r for r in done if isinstance(r.get("intel_evidence"), dict)]
    sources: dict = {}
    for r in evidence_rows:
        src = str((r.get("intel_evidence") or {}).get("source") or "unknown")
        sources[src] = sources.get(src, 0) + 1
    reviewed_n = sources.get("reviewed_jev", 0)
    price_n = sources.get("price_proxy", 0)
    evidence_fallback_rate = (round(price_n / len(evidence_rows), 4)
                              if evidence_rows else None)
    return {
        "rows": len(rows),
        "done": len(done),
        "failed": sum(1 for r in rows if str(r.get("status") or "") == "failed"),
        "fallback_known": len(fallback_known),
        "fallback_n": fallback_n,
        "fallback_rate": (round(fallback_n / len(fallback_known), 4)
                          if fallback_known else None),
        "degraded": sum(1 for r in rows if r.get("degraded") is True),
        "evidence_present": bool(evidence_rows),
        "evidence_rows": len(evidence_rows),
        "evidence_sources": sources,
        "reviewed_jev_rows": reviewed_n,
        "intel_fallback_rate": evidence_fallback_rate,
    }


def compare_ledgers(off_path, on_path, *, sample_limit: int = 20) -> dict:
    """按 (date, symbol) 对齐两臂 ledger 并计算差异（纯离线）。"""
    off_rows, off_errors = read_ledger(off_path)
    on_rows, on_errors = read_ledger(on_path)
    errors = ([dict(e, arm="off") for e in off_errors]
              + [dict(e, arm="on") for e in on_errors])
    off_map = {}
    for row in off_rows:
        off_map.setdefault(_key(row), row)     # 同键重复：首行为准（重复可见于 stats）
    on_map = {}
    for row in on_rows:
        on_map.setdefault(_key(row), row)
    aligned = sorted(set(off_map) & set(on_map))
    off_only = [k for k in off_map if k not in on_map]
    on_only = [k for k in on_map if k not in off_map]
    rec_changed, conf_changed, parse_changed = [], [], []
    status_changed = []
    for key in aligned:
        a, b = off_map[key], on_map[key]
        if str(a.get("recommendation")) != str(b.get("recommendation")):
            rec_changed.append({"date": key[0], "symbol": key[1],
                                "off": a.get("recommendation"),
                                "on": b.get("recommendation")})
        ca, cb = _num(a.get("confidence")), _num(b.get("confidence"))
        if (ca is None) != (cb is None) or (
                ca is not None and cb is not None and abs(ca - cb) > 1e-9):
            conf_changed.append({"date": key[0], "symbol": key[1],
                                 "off": a.get("confidence"),
                                 "on": b.get("confidence"),
                                 "delta": (None if ca is None or cb is None
                                           else round(cb - ca, 6))})
        if bool(a.get("parse_ok")) != bool(b.get("parse_ok")):
            parse_changed.append({"date": key[0], "symbol": key[1],
                                  "off": bool(a.get("parse_ok")),
                                  "on": bool(b.get("parse_ok"))})
        if str(a.get("status") or "") != str(b.get("status") or ""):
            status_changed.append({"date": key[0], "symbol": key[1],
                                   "off": a.get("status"), "on": b.get("status")})
    evidence_pairs = []
    for key in aligned:
        a = off_map[key].get("intel_evidence")
        b = on_map[key].get("intel_evidence")
        evidence_pairs.append({
            "date": key[0], "symbol": key[1],
            "off_source": (a or {}).get("source") if isinstance(a, dict) else None,
            "on_source": (b or {}).get("source") if isinstance(b, dict) else None,
        })
    return {
        "ok": not errors,
        "errors": errors,
        "arms": {"off": {"ledger": str(off_path), **_arm_stats(off_rows)},
                 "on": {"ledger": str(on_path), **_arm_stats(on_rows)}},
        "aligned": {"n": len(aligned),
                    "off_only": [{"date": k[0], "symbol": k[1]} for k in off_only[:sample_limit]],
                    "off_only_n": len(off_only),
                    "on_only": [{"date": k[0], "symbol": k[1]} for k in on_only[:sample_limit]],
                    "on_only_n": len(on_only)},
        "differences": {
            "recommendation_changed_n": len(rec_changed),
            "confidence_changed_n": len(conf_changed),
            "parse_ok_changed_n": len(parse_changed),
            "status_changed_n": len(status_changed),
            "recommendation_changed": rec_changed[:sample_limit],
            "confidence_changed": conf_changed[:sample_limit],
            "parse_ok_changed": parse_changed[:sample_limit],
            "status_changed": status_changed[:sample_limit],
        },
        "evidence": {
            "off_sources": _arm_stats(off_rows)["evidence_sources"],
            "on_sources": _arm_stats(on_rows)["evidence_sources"],
            "pairs": evidence_pairs[:sample_limit],
            "pairs_n": len(evidence_pairs),
        },
    }


# ---------------------------------------------------------------- reviewed 覆盖探测（只读）
def probe_reviewed(codes, db_path=None) -> dict:
    """只读探测 news.db：resolved 总数 + 各 code reviewed 证据（不联网）。"""
    try:
        from agent_platform import intel_context as IC
    except ImportError:  # 脚本上下文（agent_platform 在 sys.path）
        import intel_context as IC  # type: ignore
    path = Path(db_path) if db_path else IC.default_db_path()
    out = {"db": str(path), "exists": Path(path).is_file(),
           "resolved_total": None, "codes": {}, "errors": []}
    if not out["exists"]:
        out["errors"].append({"error": "news_db_missing", "db": str(path)})
        return out
    try:
        con = sqlite3.connect("file:%s?mode=ro" % str(path).replace("\\", "/"),
                              uri=True, timeout=5)
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM jev_review_queue WHERE status='resolved'"
            ).fetchone()
            out["resolved_total"] = int(row[0]) if row else 0
        finally:
            con.close()
    except sqlite3.Error as exc:
        out["errors"].append({"error": "news_db_error", "detail": str(exc)[:160]})
    for code in codes:
        try:
            res = IC.reviewed_news_event(code, db_path=path)
            out["codes"][code] = {
                "source": res.get("source"),
                "events": len(res.get("event_ids") or []),
                "reason": res.get("reason") or "",
            }
        except Exception as exc:  # noqa: BLE001 —— 探测失败 fail-visible 不阻断
            out["codes"][code] = {"source": "error",
                                  "events": 0,
                                  "error": str(exc)[:120]}
            out["errors"].append({"error": "probe_failed", "code": code,
                                  "detail": str(exc)[:120]})
    return out


# ---------------------------------------------------------------- 诚实性
def assemble_honesty(compare: dict, probe: dict) -> dict:
    """明确「B 臂是否应全 fallback」；improvement_claims 恒为空（不伪造改进）。"""
    on_stats = (compare.get("arms") or {}).get("on") or {}
    reviewed_n = int(on_stats.get("reviewed_jev_rows") or 0)
    resolved = probe.get("resolved_total") if probe else None
    notes, b_all_fallback = [], None
    if not on_stats.get("evidence_present"):
        notes.append("B 臂 ledger 无 intel_evidence（INTEL_TO_D1 未启用或本批未落），"
                     "无法比较情报证据来源。")
    elif reviewed_n == 0:
        b_all_fallback = True
        if resolved == 0:
            notes.append("当前 news.db reviewed JEV = 0 条（jev_review_queue 0 resolved），"
                         "B 臂 intel_evidence 预期全为 price_proxy/fallback；"
                         "本次 A/B 不构成改进证据。")
        elif resolved is None:
            notes.append("未取得 resolved 计数（DB 不可用），B 臂本批全 fallback；"
                         "不得声称改进。")
        else:
            notes.append("B 臂本批 0 条 reviewed_jev（库内 resolved 总数 %d），"
                         "intel_evidence 全为 price_proxy；不得声称改进。" % resolved)
    else:
        b_all_fallback = False
        notes.append("B 臂含 %d 条 reviewed_jev 证据行，可继续按指标人工评审；"
                     "运行器不做质量结论/不自动宣称改进。" % reviewed_n)
    if probe.get("errors"):
        notes.append("reviewed 探测有 %d 项错误（fail-visible）：%s"
                     % (len(probe["errors"]),
                        "; ".join(str(e.get("error")) for e in probe["errors"][:3])))
    return {"b_arm_all_fallback": b_all_fallback,
            "reviewed_jev_rows": reviewed_n,
            "resolved_total": resolved,
            "notes": notes,
            "improvement_claims": []}


# ---------------------------------------------------------------- 报告
def build_report(plan: dict, compare: dict, probe: dict) -> dict:
    return {
        "schema": SCHEMA,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "codes": plan.get("codes") or [],
        "roots": {"off": plan.get("off_root"), "on": plan.get("on_root")},
        "flags": {a["arm"]: a["flags"] for a in plan.get("arms") or []},
        "ledgers": {a["arm"]: a["ledger"] for a in plan.get("arms") or []},
        "compare": compare,
        "reviewed_probe": probe,
        "honesty": assemble_honesty(compare, probe),
        "errors": compare.get("errors") or [],
    }


# ---------------------------------------------------------------- CLI
def _print_plan(plan: dict) -> None:
    print("[dry-run] 只打印命令，不执行；隔离 rehearsal 根，不写生产 ledger。")
    for arm in plan["arms"]:
        print("")
        print("[%s] root=%s" % (arm["arm"].upper(), arm["root"]))
        print("  ledger: %s" % arm["ledger"])
        print("  flags : %s" % json.dumps(arm["flags"], ensure_ascii=False))
        print("  cmd   : %s" % arm["display"])
    print("")
    print("[dry-run] 用 --run 执行两臂；或 --compare 只读对比已有 ledger。")


def _print_report(report: dict) -> None:
    c = report.get("compare") or {}
    h = report.get("honesty") or {}
    print("[A/B] codes=%s" % ",".join(report.get("codes") or []))
    for arm in ("off", "on"):
        s = (c.get("arms") or {}).get(arm) or {}
        print("[%s] rows=%s done=%s failed=%s fallback_rate=%s "
              "evidence=%s sources=%s"
              % (arm, s.get("rows"), s.get("done"), s.get("failed"),
                 s.get("fallback_rate"), s.get("evidence_present"),
                 json.dumps(s.get("evidence_sources") or {}, ensure_ascii=False)))
    aligned = c.get("aligned") or {}
    diffs = c.get("differences") or {}
    print("[align] n=%s off_only=%s on_only=%s"
          % (aligned.get("n"), aligned.get("off_only_n"), aligned.get("on_only_n")))
    print("[diff ] recommendation=%s confidence=%s parse_ok=%s status=%s"
          % (diffs.get("recommendation_changed_n"),
             diffs.get("confidence_changed_n"),
             diffs.get("parse_ok_changed_n"),
             diffs.get("status_changed_n")))
    print("[honest] b_arm_all_fallback=%s resolved_total=%s"
          % (h.get("b_arm_all_fallback"), h.get("resolved_total")))
    for note in h.get("notes") or []:
        print("  - %s" % note)
    if c.get("errors"):
        print("[ERROR] ledger/执行错误 %d 项（fail-visible）" % len(c["errors"]))
        for err in c["errors"][:5]:
            print("  - %s" % json.dumps(err, ensure_ascii=False))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="情报 A/B 隔离运行器（INTEL_TO_N1 / INTEL_TO_D1；默认 dry-run）")
    ap.add_argument("--codes", required=True,
                    help="逗号分隔 6 位代码（可带 sh/sz/bj 前缀）")
    ap.add_argument("--off-root", required=True, help="A 臂（flags off）隔离数据根")
    ap.add_argument("--on-root", required=True, help="B 臂（flags on）隔离数据根")
    ap.add_argument("--off-flags", default="", help="A 臂 flag 覆盖，如 INTEL_TO_N1=0")
    ap.add_argument("--on-flags", default="", help="B 臂 flag 覆盖，如 INTEL_TO_N1=1,INTEL_TO_D1=1")
    ap.add_argument("--intel-db", default="", help="只读 news.db 路径（两臂 INTEL_N1_DB）")
    ap.add_argument("--python", default="", help="子进程解释器（默认 sys.executable）")
    ap.add_argument("--timeout", type=float, default=3600, help="单臂执行超时秒")
    ap.add_argument("--run", action="store_true", help="执行两臂（默认只 dry-run）")
    ap.add_argument("--compare", action="store_true", help="只读对比已有 ledger，不执行")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    ap.add_argument("--report-out", default="", help="报告 JSON 落盘路径")
    args = ap.parse_args(argv)
    if args.run and args.compare:
        print("[ERROR] --run 与 --compare 不能同时使用", file=sys.stderr)
        return 2
    try:
        codes = parse_codes(args.codes)
        off_flags = parse_flags(args.off_flags, DEFAULT_OFF_FLAGS)
        on_flags = parse_flags(args.on_flags, DEFAULT_ON_FLAGS)
        plan = build_plan(codes, args.off_root, args.on_root,
                          on_flags=on_flags, off_flags=off_flags,
                          intel_db=args.intel_db or None,
                          python=args.python or None,
                          timeout=args.timeout)
    except ValueError as exc:
        print("[ERROR] %s" % exc, file=sys.stderr)
        return 2

    if not args.run and not args.compare:      # 默认 dry-run：只打印
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=1))
        else:
            _print_plan(plan)
        return 0

    if args.run:
        for arm in plan["arms"]:
            res = run_arm(arm, timeout=args.timeout)
            print("[run] %s rc=%s elapsed=%ss log=%s"
                  % (arm["arm"], res.get("returncode"), res.get("elapsed_s"),
                     res.get("log") or "-"), file=sys.stderr)
            if not res.get("ok"):
                print("[run][FAIL] %s: %s" % (arm["arm"], res.get("error")),
                      file=sys.stderr)

    compare = compare_ledgers(plan["arms"][0]["ledger"],
                              plan["arms"][1]["ledger"])
    probe = probe_reviewed(codes, args.intel_db or None)
    report = build_report(plan, compare, probe)
    if args.report_out:
        out = Path(args.report_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        print("[report] -> %s" % out)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _print_report(report)
    return 0 if report["compare"].get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
