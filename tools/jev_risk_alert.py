# -*- coding: utf-8 -*-
"""JEV 风险告警桥（2026-10-30 JEVALERT，只读 news.db + 本地去重状态）。

目标：把 JEV 高影响利空事件从"可查"变"会喊"。
  - 扫描近 N 小时已判新闻中 bias=bear、impact=high、conf>=阈值 的事件；
  - 排除低信号事件（latest.json 的 low_signal_event_types）与去重状态里已报过的 id；
  - 默认 dry-run 打印；`--emit-outbox` 追加到 risk_alerts.jsonl（供 Hermes/API 消费）；
    `--emit-flag` 额外写 jev-eval/alerts/JEV_RISK_<ts>.flag（现有 alert_forward.ps1 转发飞书）。
  - 自动调度/推送上线需老田批准（本工具不注册任务、不主动外发）。

用法：
    python -B tools/jev_risk_alert.py --hours 2
    python -B tools/jev_risk_alert.py --hours 2 --emit-outbox --emit-flag
    python -B tools/jev_risk_alert.py --hours 24 --threshold-conf 0.9 --json out.json
退出码：0=成功（无新告警也 0）；1=读取/参数错误。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

NEWS_DB = Path(r"C:\aios\news_collector\news.db")
AUDIT_DIR = Path(r"C:\aios\agent_platform\data\jev_audit")
LATEST = AUDIT_DIR / "latest.json"
OUTBOX = AUDIT_DIR / "risk_alerts.jsonl"
STATE = AUDIT_DIR / "risk_alert_state.json"
ALERTS_DIR = Path(r"C:\aios\jev-eval\alerts")
IMPACT_W = {"high": 3, "medium": 2, "low": 1}
SEVERITY = {3: "high", 2: "medium", 1: "low"}


def load_low_signal() -> set[str]:
    try:
        data = json.loads(LATEST.read_text(encoding="utf-8"))
        return set((data.get("assessment") or {}).get("low_signal_event_types") or [])
    except (OSError, ValueError):
        return set()


def load_state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("seen"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"seen": []}


def save_state(state: dict) -> None:
    state["seen"] = list(dict.fromkeys(state.get("seen") or []))[-5000:]
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def scan(hours: int = 2, min_impact: str = "high", min_conf: float = 0.8,
         limit: int = 300) -> list[dict]:
    """从 news.db 读近 hours 小时已判新闻 → 过滤成告警候选（未去重）。"""
    low_signal = load_low_signal()
    conn = sqlite3.connect("file:%s?mode=ro" % NEWS_DB.as_posix(), uri=True, timeout=20)
    try:
        rows = conn.execute(
            "SELECT id, ts, source, title, jev FROM news"
            " WHERE jev IS NOT NULL AND jev<>''"
            " AND created_at >= datetime('now', ?) ORDER BY rowid DESC LIMIT ?",
            ("-%d hours" % int(hours), int(limit))).fetchall()
    finally:
        conn.close()
    out = []
    min_w = IMPACT_W.get(min_impact, 3)
    for nid, ts, source, title, raw in rows:
        try:
            j = json.loads(raw or "{}")
        except ValueError:
            continue
        if (j.get("bias") or "") != "bear":
            continue
        impact = str(j.get("impact") or "low").lower()
        if IMPACT_W.get(impact, 1) < min_w:
            continue
        conf = j.get("conf")
        if not isinstance(conf, (int, float)) or float(conf) < min_conf:
            continue
        ev = str(j.get("event_type") or "other")
        if ev in low_signal:
            continue
        out.append({
            "news_id": nid, "ts": ts, "source": source,
            "title": str(title or "")[:200],
            "bias": "bear", "conf": round(float(conf), 2),
            "impact": impact, "severity": SEVERITY[IMPACT_W[impact]],
            "event_type": ev, "horizon": j.get("horizon") or "unknown",
            "codes": j.get("companies") or [],
            "reason": str(j.get("reason") or "")[:120],
        })
    return out


def select_new(candidates: list[dict], state: dict) -> list[dict]:
    """去重：过滤 state.seen 中已有的 news_id（纯函数）。"""
    seen = set(state.get("seen") or [])
    return [c for c in candidates if c["news_id"] not in seen]


def flag_text(alerts: list[dict]) -> str:
    lines = ["JEV 风险告警（%d 条）" % len(alerts)]
    for a in alerts:
        bits = [str(a.get("ts") or "")[5:16], "利空·%s" % a.get("severity"),
                "conf=%s" % a.get("conf"), a.get("title") or ""]
        if a.get("codes"):
            bits.append("关联:" + ",".join(a["codes"][:4]))
        lines.append("- " + " ".join(str(b) for b in bits))
    lines.append("（JEV 判定，低信号事件已排除；详情用 jev_query 查证）")
    return "\n".join(lines)


def emit_outbox(alerts: list[dict], path: Path = OUTBOX) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for a in alerts:
            fh.write(json.dumps(a, ensure_ascii=False) + "\n")
    return len(alerts)


def emit_flag(alerts: list[dict], alerts_dir: Path = ALERTS_DIR) -> Path:
    alerts_dir.mkdir(parents=True, exist_ok=True)
    p = alerts_dir / ("JEV_RISK_%s.flag" % time.strftime("%Y%m%d_%H%M%S"))
    p.write_text(flag_text(alerts), encoding="utf-8")
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=2)
    ap.add_argument("--min-impact", default="high", choices=("high", "medium"))
    ap.add_argument("--threshold-conf", type=float, default=0.8)
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--emit-outbox", action="store_true")
    ap.add_argument("--emit-flag", action="store_true")
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)
    try:
        candidates = scan(a.hours, a.min_impact, a.threshold_conf, a.limit)
    except (OSError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:200]}, ensure_ascii=False))
        return 1
    state = load_state()
    new = select_new(candidates, state)
    result = {"ok": True, "hours": a.hours, "candidates": len(candidates),
              "new": len(new), "alerts": new,
              "emitted": {"outbox": 0, "flag": ""}}
    if new and a.emit_outbox:
        result["emitted"]["outbox"] = emit_outbox(new)
    if new and a.emit_flag:
        result["emitted"]["flag"] = str(emit_flag(new))
    if new:
        state.setdefault("seen", []).extend(x["news_id"] for x in new)
        save_state(state)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if a.json:
        Path(a.json).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
