#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JEV 公告源守卫：把「静默降级」变成「可见告警」。

背景（2026-11-05）：
  signal_review_gate.py 的公告源探测失败时只打一行 info：
    {"level":"info","msg":"公告源探测失败（降级 none 模式）: anns_d 失败 code=40203: ..."}
  之后每轮照常输出「本轮 0 条 ... 公告源=none」，外人看来门是健康的，实际公告
  维度长期空转。2026-10-30 至今就是这种状态（先是 40101 令牌失效，后是 40203 无
  接口权限），只有翻日志才发现。

判定（读 review 日志尾部即可，不连库、不调外部 API）：
  · 最近一轮 mode=tushare          → OK
  · 最近一轮 mode=none + 40101/40102 → CRITICAL（令牌失效，换令牌能解）
  · 最近一轮 mode=none + 40203/40204 → WARN（有令牌无接口权限，只能提积分/换源）
  · 最近一轮 mode=none 无错误码      → WARN（TUSHARE_TOKEN 没注入）
  · 日志里根本没有「公告源=」记录    → WARN（review 门没跑 / 日志轮转后为空）
  · 最近一轮时间超 --max-age-hours   → CRITICAL（review 门停摆，先查 9102）

推送：写 review_data/lark_source_msg.txt 后调 backup_alert.py（与其它守卫同渠道）。
去重：同一 cause 在 --remind-days 天内只推一次（避免每天重复喊同一句），
      状态存 review_data/source_guard_state.json；状态变化立即推。

stdout：一行机器可读 JSON；人读说明走 stderr。
exit: 0 正常 / 1 告警 / 2 严重 / 3 参数错误
用法：
  python jev_source_guard.py                 # 判定 + 按需推送
  python jev_source_guard.py --no-push       # 只判定
  python jev_source_guard.py --log logs/review.log --max-age-hours 6
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
REVIEW = HERE / "review_data"
LOGS = HERE / "logs"
STATE_FILE = "source_guard_state.json"
TAIL_BYTES = 2_000_000
MAX_AGE_HOURS = 6.0
REMIND_DAYS = 3

MODE_RE = re.compile(r"公告源=([A-Za-z_]+)")
FAIL_RE = re.compile(r"公告源探测失败.*?code=(\d+)[:：]?\s*(.*)$")

CAUSE_INFO = {
    "40101": ("TOKEN_INVALID", "令牌无效（token 不对/被吊销）",
              "换令牌：tools/set_env_secret.py --key TUSHARE_TOKEN --from-dotenv <有效源> --probe"),
    "40102": ("TOKEN_INVALID", "令牌过期",
              "换令牌：tools/set_env_secret.py --key TUSHARE_TOKEN --stdin --probe"),
    "40203": ("NO_PERMISSION", "有令牌但无 anns_d 接口权限（积分不足）",
              "提积分开 anns_d，或改换公告源（巨潮/交易所/东财）"),
    "40204": ("NO_PERMISSION", "积分不足或权限到期",
              "充值/续期，或改换公告源"),
}

USAGE = ("用法: jev_source_guard.py [--log F] [--data-dir DIR] [--state-file F] "
         "[--max-age-hours 6] [--remind-days 3] [--no-push]")


# ── 参数 ────────────────────────────────────────────────────
def parse_args(argv: list[str]) -> dict:
    norm: list[str] = []
    for a in argv:
        if a.startswith("--") and "=" in a:
            k, v = a.split("=", 1)
            norm.extend([k, v])
        else:
            norm.append(a)

    cfg = {
        "log": str(LOGS / "review.log"),
        "data_dir": str(REVIEW),
        "state_file": None,
        "max_age_hours": MAX_AGE_HOURS,
        "remind_days": REMIND_DAYS,
        "no_push": False,
    }
    i = 0
    while i < len(norm):
        a = norm[i]
        if a == "--no-push":
            cfg["no_push"] = True
        elif a in ("-h", "--help"):
            cfg["help"] = True
        elif a in ("--log", "--data-dir", "--state-file", "--max-age-hours", "--remind-days"):
            i += 1
            if i >= len(norm):
                raise ValueError(f"{a} 缺少取值")
            v = norm[i]
            if a == "--log":
                cfg["log"] = v
            elif a == "--data-dir":
                cfg["data_dir"] = v
            elif a == "--state-file":
                cfg["state_file"] = v
            elif a == "--max-age-hours":
                cfg["max_age_hours"] = float(v)
            else:
                cfg["remind_days"] = int(v)
        else:
            raise ValueError(f"未知参数 {a!r}")
        i += 1
    if cfg.get("max_age_hours") <= 0:
        raise ValueError("--max-age-hours 需 > 0")
    if cfg["remind_days"] < 0:
        raise ValueError("--remind-days 需 >= 0")
    # 演练用：JEV_GUARD_NO_PUSH=1 抑制推送（计划任务正常跑时不设）
    if os.environ.get("JEV_GUARD_NO_PUSH", "").lower() in ("1", "true", "yes"):
        cfg["no_push"] = True
    return cfg


# ── 日志解析 ────────────────────────────────────────────────
def tail_lines(path: Path, max_bytes: int = TAIL_BYTES) -> list[str]:
    if not path.exists():
        return []
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(-max_bytes, os.SEEK_END)
        data = fh.read()
    text = data.decode("utf-8", errors="ignore")
    lines = text.splitlines()
    # 截断时首行多半是半个 JSON，丢掉
    return lines[1:] if size > max_bytes and lines else lines


def parse_ts(raw: str) -> datetime | None:
    raw = (raw or "").replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw[:26], fmt)
        except ValueError:
            continue
    return None


def scan_log(path: Path) -> dict:
    """返回最近一轮 mode、最近一次失败码、以及尾部连续 none 的轮数。"""
    rounds: list[tuple[datetime | None, str]] = []
    last_fail: dict | None = None

    for raw in tail_lines(path):
        if not raw.strip():
            continue
        ts = None
        msg = raw
        try:
            rec = json.loads(raw)
            if isinstance(rec, dict):
                ts = parse_ts(str(rec.get("ts") or ""))
                msg = str(rec.get("msg") or "")
        except (ValueError, TypeError):
            pass

        m = MODE_RE.search(msg)
        if m:
            rounds.append((ts, m.group(1)))
            continue

        f = FAIL_RE.search(msg)
        if f:
            code = f.group(1)
            last_fail = {"code": code, "text": f.group(2).strip()[:120], "ts": ts}

    streak = 0
    for _ts, mode in reversed(rounds):
        if mode == "tushare":
            break
        streak += 1

    return {
        "rounds": len(rounds),
        "mode": rounds[-1][1] if rounds else None,
        "last_round_ts": rounds[-1][0] if rounds else None,
        "none_streak": streak,
        "last_fail": last_fail,
    }


# ── 状态（推送去重） ────────────────────────────────────────
def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))


def should_push(state: dict, cause: str, now: datetime, remind_days: int) -> bool:
    if remind_days <= 0:
        return True
    if state.get("cause") != cause:
        return True
    try:
        last = datetime.fromisoformat(str(state.get("pushed_at") or ""))
    except ValueError:
        return True
    return now - last >= timedelta(days=remind_days)


# ── 推送 ────────────────────────────────────────────────────
def push(text: str, data_dir: Path) -> int:
    msg_file = data_dir / "lark_source_msg.txt"
    msg_file.parent.mkdir(parents=True, exist_ok=True)
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
    log_path: Path | str | None = None,
    data_dir: Path | str | None = None,
    state_file: Path | str | None = None,
    max_age_hours: float = MAX_AGE_HOURS,
    now: datetime | None = None,
    write_state: bool = True,
) -> dict:
    now = now or datetime.now()
    log = Path(log_path) if log_path else LOGS / "review.log"
    ddir = Path(data_dir) if data_dir else REVIEW
    sfile = Path(state_file) if state_file else ddir / STATE_FILE

    info = scan_log(log)
    cause = None
    cause_txt = ""
    action = ""

    if info["mode"] is None:
        level, code = "WARN", 1
        cause = "NO_SIGNAL"
        cause_txt = "日志尾部无「公告源=」记录（review 门未跑 / 日志被轮转清空）"
        action = "查 9102 是否在跑：Get-NetTCPConnection -LocalPort 9102；再查 logs/review.log 尾部"
    elif info["mode"] == "tushare":
        level, code = "OK", 0
        cause_txt = "公告源在线"
    else:
        fail = info["last_fail"] or {}
        fcode = str(fail.get("code") or "")
        if fcode in CAUSE_INFO:
            kind, cause_txt, action = CAUSE_INFO[fcode]
            cause = f"{kind}:{fcode}"
        elif fcode:
            cause, cause_txt = "OTHER:" + fcode, (fail.get("text") or "未知错误")[:80]
            action = "查 Tushare 文档该错误码，或 tools/tushare_probe.py --require anns_d 定位"
        else:
            cause, cause_txt = "NO_TOKEN", "公告源=none 且无错误码（TUSHARE_TOKEN 未注入）"
            action = "查 jev-gate.env.enc 是否含 TUSHARE_TOKEN；python tools/tushare_probe.py"

        token_dead = fcode in ("40101", "40102")
        level, code = ("CRITICAL", 2) if token_dead else ("WARN", 1)

    # 停摆判定优先于错误码：日志根本不再更新时，错误码是旧痕迹
    age_hours = None
    if info["last_round_ts"]:
        age_hours = round((now - info["last_round_ts"]).total_seconds() / 3600.0, 2)
        if age_hours > max_age_hours:
            level, code = "CRITICAL", 2
            cause = "STALLED"
            cause_txt = f"review 最后一轮在 {age_hours}h 前（阈值 {max_age_hours}h）"
            action = "review 门停摆：Start-ScheduledTask -TaskName JevStack 后看日志"

    state = load_state(sfile)
    if write_state:
        if level == "OK":
            new_state = {"cause": None, "pushed_at": None, "last_ok": now.isoformat(timespec="seconds")}
        else:
            new_state = dict(state)
            new_state["cause"] = cause
            new_state["level"] = level
        save_state(sfile, new_state)

    report = {
        "ts": now.isoformat(timespec="seconds"),
        "level": level,
        "code": code,
        "ann_mode": info["mode"],
        "ann_cause": cause,
        "ann_cause_txt": cause_txt,
        "action": action,
        "rounds_seen": info["rounds"],
        "none_streak": info["none_streak"],
        "last_round_ts": info["last_round_ts"].isoformat(timespec="seconds") if info["last_round_ts"] else None,
        "age_hours": age_hours,
        "last_fail_code": (info["last_fail"] or {}).get("code"),
        "log": str(log),
    }

    icon = {"OK": "🟢", "WARN": "⚠", "CRITICAL": "🔴"}[level]
    msg_lines = [
        f"{icon} [Jev公告源] {level}",
        f"公告源 mode={info['mode']}（尾部连续 none {info['none_streak']} 轮）",
        f"原因: {cause_txt}",
    ]
    if action:
        msg_lines.append(f"处置: {action}")
    if age_hours is not None:
        msg_lines.append(f"最后一轮: {report['last_round_ts']}（{age_hours}h 前）")
    return {"report": report, "msg": "\n".join(msg_lines), "should_push": level != "OK"}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        cfg = parse_args(argv)
    except ValueError as e:
        print(f"{e}\n{USAGE}", file=sys.stderr)
        return 3
    if cfg.get("help"):
        print(USAGE)
        return 0

    ddir = Path(cfg["data_dir"])
    sfile = Path(cfg["state_file"]) if cfg["state_file"] else ddir / STATE_FILE
    out = evaluate(
        log_path=cfg["log"],
        data_dir=ddir,
        state_file=sfile,
        max_age_hours=cfg["max_age_hours"],
        write_state=not cfg["no_push"],
    )
    rep = out["report"]
    print(json.dumps(rep, ensure_ascii=False, separators=(",", ":")))
    print(out["msg"], file=sys.stderr)

    if cfg["no_push"] or not out["should_push"]:
        return rep["code"]

    state = load_state(sfile)
    if not should_push(state, str(rep["ann_cause"]), datetime.now(), cfg["remind_days"]):
        print("推送跳过（同因未到提醒间隔）", file=sys.stderr)
        return rep["code"]

    rc = push(out["msg"], ddir)
    state = load_state(sfile)
    state["pushed_at"] = datetime.now().isoformat(timespec="seconds")
    state["cause"] = rep["ann_cause"]
    save_state(sfile, state)
    if rc != 0:
        print(f"推送失败 rc={rc}（判定结果仍有效）", file=sys.stderr)
    return rep["code"]


if __name__ == "__main__":
    sys.exit(main())
