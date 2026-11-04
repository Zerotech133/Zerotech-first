#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JEV-EVALCOV-20261105：回填覆盖率守卫（单一职责，独立于 jev_eval_guard.py）。

背景：回填产出（triples_outcome.jsonl）长期跟不上候选池（triples*.jsonl），
覆盖率低 → 毕业判定 n_eff 上不去，四域全卡在未毕业。原链路只监控"评测有没有跑"，
不监控"样本回填够不够"，属于盲区。

本模块判定三件事：
  1) 回填覆盖率 = outcome 去重 id 数 / (triples + triples_archive) 去重 id 数
     id 字段优先 candidate_id，其次 id；两者都缺失 → 退回行数口径并标注 basis=lines
  2) 覆盖率 < --min-coverage（默认 0.50）→ 告警；
     连续 --streak-days 天（默认 3，按状态文件累计）低于门槛 → 升级「🔴 持续欠回填」
  3) review_data/graduation_status_v2.json 超过 7 天未更新 → WARN（毕业判定数据陈旧）

推送：写 review_data/lark_coverage_msg.txt 后调 backup_alert.py（与 eval_guard 同渠道）。
stdout：仅一行机器可读 JSON（便于计划任务落日志与 grep）；推送诊断走 stderr。
用法：
  python jev_coverage_guard.py                      # 判定 + 推送
  python jev_coverage_guard.py --no-push            # 只判定
  python jev_coverage_guard.py --min-coverage 0.6 --streak-days 5
exit: 0 正常 / 1 告警（覆盖率不足或毕业 JSON 陈旧）/ 2 参数错误
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
REVIEW = HERE / "review_data"
OUTCOME_FILE = "triples_outcome.jsonl"
POOL_FILES = ("triples.jsonl", "triples_archive.jsonl")
GRAD_FILE = "graduation_status_v2.json"
STATE_FILE = "coverage_streak.json"
ID_FIELDS = ("candidate_id", "id")

MIN_COVERAGE = 0.50
STREAK_DAYS = 3
GRAD_MAX_AGE_DAYS = 7

USAGE = (
    "用法: jev_coverage_guard.py [--min-coverage 0.5] [--streak-days 3] "
    "[--data-dir DIR] [--state-file F] [--grad-file F] [--no-push]"
)


# ── 参数 ────────────────────────────────────────────────────
def parse_args(argv: list[str]) -> dict:
    # 兼容 --key=value 形式（PS 调用链两种写法都有人用）
    norm: list[str] = []
    for a in argv:
        if a.startswith("--") and "=" in a:
            k, v = a.split("=", 1)
            norm.extend([k, v])
        else:
            norm.append(a)

    cfg = {
        "min_coverage": MIN_COVERAGE,
        "streak_days": STREAK_DAYS,
        "data_dir": str(REVIEW),
        "state_file": None,
        "grad_file": None,
        "no_push": False,
    }
    i = 0
    while i < len(norm):
        a = norm[i]
        if a == "--no-push":
            cfg["no_push"] = True
        elif a in ("-h", "--help"):
            cfg["help"] = True
        elif a in ("--min-coverage", "--streak-days", "--data-dir",
                   "--state-file", "--grad-file"):
            i += 1
            if i >= len(norm):
                raise ValueError(f"{a} 缺少取值")
            v = norm[i]
            if a == "--min-coverage":
                cfg["min_coverage"] = float(v)
            elif a == "--streak-days":
                cfg["streak_days"] = int(v)
            elif a == "--data-dir":
                cfg["data_dir"] = v
            elif a == "--state-file":
                cfg["state_file"] = v
            else:
                cfg["grad_file"] = v
        else:
            raise ValueError(f"未知参数 {a!r}")
        i += 1
    if cfg.get("help"):
        return cfg
    if not 0.0 < cfg["min_coverage"] <= 1.0:
        raise ValueError(f"--min-coverage 需在 (0,1]，收到 {cfg['min_coverage']}")
    if cfg["streak_days"] < 1:
        raise ValueError(f"--streak-days 需 >=1，收到 {cfg['streak_days']}")
    # 演练用：JEV_GUARD_NO_PUSH=1 抑制推送（计划任务正常跑时不设）
    if os.environ.get("JEV_GUARD_NO_PUSH", "").lower() in ("1", "true", "yes"):
        cfg["no_push"] = True
    return cfg


# ── 覆盖率口径 ──────────────────────────────────────────────
def scan(path: Path) -> dict:
    """读一个 jsonl：返回行数、坏行数、去重 id 集合、命中的 id 字段。"""
    lines = bad = 0
    ids_by_field: dict[str, set[str]] = {f: set() for f in ID_FIELDS}
    if not path.exists():
        return {"exists": False, "lines": 0, "bad": 0, "ids": set(), "field": None}
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            if not raw.strip():
                continue
            lines += 1
            try:
                rec = json.loads(raw)
            except (ValueError, TypeError):
                bad += 1
                continue
            if not isinstance(rec, dict):
                bad += 1
                continue
            for f in ID_FIELDS:
                v = rec.get(f)
                if v not in (None, ""):
                    ids_by_field[f].add(str(v))
    field = next((f for f in ID_FIELDS if ids_by_field[f]), None)
    return {
        "exists": True,
        "lines": lines,
        "bad": bad,
        "ids": ids_by_field[field] if field else set(),
        "field": field,
        "field_hits": {f: len(ids_by_field[f]) for f in ID_FIELDS},
    }


def measure(data_dir: Path) -> dict:
    """按真实字段算覆盖率。任一文件 id 全缺 → basis=lines 退回行数口径。"""
    outcome = scan(data_dir / OUTCOME_FILE)
    pools = {name: scan(data_dir / name) for name in POOL_FILES}
    id_ok = outcome["exists"] and outcome["field"] is not None
    id_ok = id_ok and all(p["field"] is not None for p in pools.values() if p["exists"])

    files = {
        OUTCOME_FILE: {
            "lines": outcome["lines"],
            "bad": outcome["bad"],
            "uniq_ids": len(outcome["ids"]),
            "id_field": outcome["field"],
        }
    }
    for name, p in pools.items():
        files[name] = {
            "lines": p["lines"],
            "bad": p["bad"],
            "uniq_ids": len(p["ids"]),
            "id_field": p["field"],
        }

    if id_ok:
        numer = len(outcome["ids"])
        denom_ids: set[str] = set()
        for p in pools.values():
            denom_ids |= p["ids"]
        denom = len(denom_ids)
        # 落在分母之外的 outcome id 也算进来：它们同样是已回填样本
        extra = outcome["ids"] - denom_ids
        coverage = (numer / denom) if denom else None
        basis = f"id:{outcome['field']}"
    else:
        numer = outcome["lines"]
        denom = sum(p["lines"] for p in pools.values())
        extra = set()
        coverage = (numer / denom) if denom else None
        basis = "lines"

    return {
        "basis": basis,
        "numer": numer,
        "denom": denom,
        "coverage": coverage,
        "outcome_outside_pool": len(extra),
        "files": files,
    }


# ── 连续天数状态 ────────────────────────────────────────────
def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def save_state(path: Path, state: dict) -> None:
    """原子写：tmp + os.replace（本机沙箱 os.unlink 不可用，禁止依赖删除）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(str(tmp), str(path))


def update_streak(state: dict, today: date, below: bool) -> dict:
    """同日幂等；昨天→+1；断档→重置 1；达标→0。"""
    streak = int(state.get("streak") or 0)
    last_raw = state.get("last_date") or ""
    try:
        last = date.fromisoformat(last_raw)
    except ValueError:
        last = None

    if not below:
        return {"last_date": today.isoformat(), "streak": 0,
                "streak_start": None, "last_coverage": state.get("last_coverage")}
    if last == today:
        return dict(state)  # 同日重跑不重复计数
    streak = streak + 1 if last == today - timedelta(days=1) else 1
    start = state.get("streak_start") if streak > 1 else today.isoformat()
    return {
        "last_date": today.isoformat(),
        "streak": streak,
        "streak_start": start or today.isoformat(),
        "last_coverage": state.get("last_coverage"),
    }


# ── 毕业 JSON 新鲜度 ────────────────────────────────────────
def check_grad(path: Path, now: datetime, max_age_days: int = GRAD_MAX_AGE_DAYS) -> dict:
    if not path.exists():
        return {"exists": False, "age_days": None, "stale": True,
                "computed_at": None}
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    age = (now - mtime).days
    computed_at = None
    try:
        computed_at = json.loads(path.read_text(encoding="utf-8")).get("computed_at")
    except (OSError, ValueError):
        pass
    return {
        "exists": True,
        "age_days": age,
        "stale": age > max_age_days,
        "computed_at": computed_at,
        "max_age_days": max_age_days,
    }


# ── 推送 ────────────────────────────────────────────────────
def push(text: str) -> int:
    msg_file = REVIEW / "lark_coverage_msg.txt"
    msg_file.parent.mkdir(exist_ok=True)
    msg_file.write_text(text, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(HERE / "backup_alert.py"), str(msg_file)],
        capture_output=True,
        text=True,
        timeout=90,
    )
    print(f"推送 exit={r.returncode} {r.stdout.strip()[:120]}", file=sys.stderr)
    return r.returncode


# ── 判定 ────────────────────────────────────────────────────
def evaluate(
    data_dir: Path | str | None = None,
    min_coverage: float = MIN_COVERAGE,
    streak_days: int = STREAK_DAYS,
    state_file: Path | str | None = None,
    grad_file: Path | str | None = None,
    now: datetime | None = None,
    write_state: bool = True,
) -> dict:
    """纯判定，返回可 json.dumps 的报告；不推送。"""
    now = now or datetime.now()
    ddir = Path(data_dir) if data_dir else REVIEW
    sfile = Path(state_file) if state_file else ddir / STATE_FILE
    gfile = Path(grad_file) if grad_file else ddir / GRAD_FILE

    m = measure(ddir)
    cov = m["coverage"]
    below = cov is not None and cov < min_coverage

    state = load_state(sfile)
    nxt = update_streak(state, now.date(), below)
    nxt["last_coverage"] = None if cov is None else round(cov, 4)
    if write_state:
        save_state(sfile, nxt)
    streak = int(nxt.get("streak") or 0)
    persistent = below and streak >= streak_days

    grad = check_grad(gfile, now)

    problems: list[str] = []
    if cov is None:
        problems.append(
            f"分母为 0：{ddir} 下 {'+'.join(POOL_FILES)} 无可统计样本"
        )
    elif below:
        problems.append(
            f"回填覆盖率 {cov:.1%} < {min_coverage:.0%}"
            f"（{m['numer']}/{m['denom']}，basis={m['basis']}）"
        )
    if persistent:
        problems.append(
            f"连续 {streak} 天低于门槛（{nxt.get('streak_start')} 起）"
            "→ 回填任务疑似停摆，查 review_data/jev_backfill_task.log"
        )
    if grad["stale"]:
        if grad["exists"]:
            problems.append(
                f"毕业状态 {GRAD_MAX_AGE_DAYS} 天未更新（文件年龄 {grad['age_days']} 天，"
                f"computed_at={grad['computed_at']}）→ 重跑 jev_graduation_status.py"
            )
        else:
            problems.append(f"毕业状态文件缺失：{gfile}")

    if persistent:
        level, code = "CRITICAL", 2
        title = f"🔴 [Jev覆盖率] 持续欠回填（{streak} 天）"
    elif problems:
        level, code = "WARN", 1
        title = "⚠ [Jev覆盖率] 回填覆盖率告警"
    else:
        level, code = "OK", 0
        title = "[Jev覆盖率] 正常"

    cov_txt = "n/a" if cov is None else f"{cov:.1%}"
    msg_lines = [
        title,
        f"回填覆盖率 {cov_txt}（{m['numer']}/{m['denom']}，basis={m['basis']}，"
        f"门槛 {min_coverage:.0%}，连续 {streak}/{streak_days} 天）",
        f"毕业状态: "
        + (
            f"年龄 {grad['age_days']} 天（阈值 {GRAD_MAX_AGE_DAYS} 天）"
            if grad["exists"]
            else "缺失"
        ),
        *[f"  - {p}" for p in problems],
    ]

    report = {
        "ts": now.isoformat(timespec="seconds"),
        "level": level,
        "code": code,
        "coverage": None if cov is None else round(cov, 4),
        "numer": m["numer"],
        "denom": m["denom"],
        "basis": m["basis"],
        "min_coverage": min_coverage,
        "below": below,
        "streak_days": streak,
        "streak_threshold": streak_days,
        "streak_start": nxt.get("streak_start"),
        "persistent": persistent,
        "grad_age_days": grad["age_days"],
        "grad_stale": grad["stale"],
        "grad_computed_at": grad["computed_at"],
        "outcome_outside_pool": m["outcome_outside_pool"],
        "files": m["files"],
        "problems": problems,
    }
    return {"report": report, "msg": "\n".join(msg_lines)}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        cfg = parse_args(argv)
    except ValueError as e:
        print(f"{e}\n{USAGE}", file=sys.stderr)
        return 2
    if cfg.get("help"):
        print(USAGE)
        return 0

    out = evaluate(
        data_dir=cfg["data_dir"],
        min_coverage=cfg["min_coverage"],
        streak_days=cfg["streak_days"],
        state_file=cfg["state_file"],
        grad_file=cfg["grad_file"],
    )
    print(json.dumps(out["report"], ensure_ascii=False, separators=(",", ":")))
    if not cfg["no_push"]:
        push(out["msg"])
    else:
        print(out["msg"], file=sys.stderr)
    return out["report"]["code"]


if __name__ == "__main__":
    sys.exit(main())
