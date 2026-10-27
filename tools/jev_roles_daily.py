# -*- coding: utf-8 -*-
"""jev_roles_daily.py — JEV 三席「日产出物」生成器（K-ORG-20261026-JEVROLE）。

为什么要有它
------------
JEV 板块（驿传署/运行体部）有 3 个编制席位，但此前**只有编制没有工作流**：
  · JEV_GATE  廖化 —— 告警紧急度/方向判读（KPI：告警降噪率）
  · JEV_REVIEW 蒋干 —— 信号复核官（KPI：抽检一致率）
  · JEV_QD    伊籍 —— 桥接工程师（KPI：推送成功率）
三席在 llm_tool_audit 里零事件，板块实际由 cry cron + 常驻服务撑着。
本脚本把三席的「当日工作」变成**可判定的产物**，供 tools/agent_daily.py 点名。

数据源（全部只读、可缺省、单点失败降级不抛）
  · jev-eval/logs/gate.log         告警门轮询/推送日志（JSONL）
  · jev-eval/logs/review.log       复核服务轮询日志（JSONL）
  · jev-eval/review_data/qd_pushed.json / qd_failed.json   桥接推送结果
  · news_collector/news.db         当日已判新闻（抽样 20 条供人工/agent 复核）

输出
  · agent_platform/data/jev_roles/<yyyy-MM-dd>.json   机器读（agent_daily 判据）
  · outputs/jev_daily_<yyyy-MM-dd>.md                 人读（当日三席日报）

用法
  python -B tools/jev_roles_daily.py                # 生成今日
  python -B tools/jev_roles_daily.py --date 2026-10-25
  python -B tools/jev_roles_daily.py --dry-run      # 只打印，不写文件
  python -B tools/jev_roles_daily.py --json         # 只输出 JSON

红线：本脚本只读；不写 news.db、不调 LLM、不动服务。夜间 22:00-07:00 运行也是安全的
（只读日志尾部 + news.db 取 20 行，不触发全表扫描）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
import logging

HERE = Path(__file__).resolve()
# 数据根默认=脚本所在仓库；工作树里 jev-eval/logs 与 news.db 被 gitignore，
# 验证时可用 JEV_ROLES_ROOT 指向 master（生产运行不需要设置）。
ROOT = Path(os.environ.get("JEV_ROLES_ROOT") or HERE.parent.parent)
JEV_EVAL = ROOT / "jev-eval"
NEWS_DB = ROOT / "news_collector" / "news.db"
DATA_DIR = ROOT / "agent_platform" / "data" / "jev_roles"
OUTPUTS_DIR = ROOT / "outputs"
GATE_LOG = JEV_EVAL / "logs" / "gate.log"
REVIEW_LOG = JEV_EVAL / "logs" / "review.log"
QD_PUSHED = JEV_EVAL / "review_data" / "qd_pushed.json"
QD_FAILED = JEV_EVAL / "review_data" / "qd_failed.json"

TAIL_BYTES = 2 * 1024 * 1024      # 每个日志只读尾部 2MB（门/复核日志增长快）
SAMPLE_N = 20                      # 复核抽样条数

def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _day_bounds(day: str) -> tuple[datetime, datetime]:
    """'2026-10-26' → (当日 00:00, 次日 00:00)。"""
    d = datetime.strptime(day, "%Y-%m-%d")
    return d, d + timedelta(days=1)


def _tail_jsonl(path: Path, max_bytes: int = TAIL_BYTES) -> list[dict]:
    """读 JSONL 尾部 max_bytes，返回可解析的记录列表。坏行跳过（fail-open）。"""
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()          # 丢弃可能被截断的首行
            for raw in fh:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:      # noqa: BLE001 坏行跳过
                    continue
                if isinstance(d, dict):
                    out.append(d)
    except OSError:
        return []
    return out


def _in_day(ts_text: str, lo: datetime, hi: datetime) -> bool:
    """ISO 时间戳字符串是否落在 [lo, hi)。解析失败按 False。"""
    if not ts_text:
        return False
    try:
        ts = datetime.fromisoformat(str(ts_text).replace("Z", "+00:00"))
        if ts.tzinfo is not None:
            ts = ts.astimezone().replace(tzinfo=None)
        return lo <= ts < hi
    except Exception:                 # noqa: BLE001
        return False


def classify_gate_msg(msg: str) -> str:
    """gate.log 的 msg → 语义桶（纯函数，便于单测）。"""
    m = msg or ""
    if "无新告警" in m:
        return "no_alert"
    # 顺序敏感：「推送失败」必须归 fail 而不是 push
    if "失败" in m or "FAIL" in m.upper():
        return "fail"
    if "重试" in m:
        return "retry"
    if "推送" in m or "PUSH" in m.upper() or "推送到" in m:
        return "push"
    if "降噪" in m or "降级" in m:
        return "suppress"
    return "other"


def collect_gate(day: str) -> dict:
    """JEV_GATE：告警门当日轮询/推送/失败统计 + 降噪率。"""
    lo, hi = _day_bounds(day)
    rec = {"role": "JEV_GATE", "ok": True, "no_alert": 0, "push": 0,
           "suppress": 0, "fail": 0, "retry": 0, "alerts_in": None,
           "suppress_rate": None, "err": None}
    try:
        rows = [r for r in _tail_jsonl(GATE_LOG) if _in_day(r.get("ts", ""), lo, hi)]
        for r in rows:
            bucket = classify_gate_msg(str(r.get("msg") or ""))
            if bucket in rec:
                rec[bucket] += 1
        rec["polls"] = len(rows)
        # 降噪率 = 被拦下 / (拦下 + 推送)；分母为 0 时 None（今日无告警，属正常）
        denom = rec["suppress"] + rec["push"]
        rec["suppress_rate"] = round(rec["suppress"] / denom, 4) if denom else None
    except Exception as exc:          # noqa: BLE001  fail-open
        rec.update(ok=False, err="%s: %s" % (type(exc).__name__, exc))
    return rec


def collect_review(day: str) -> dict:
    """JEV_REVIEW：复核服务当日轮询轮数与输出条数。"""
    lo, hi = _day_bounds(day)
    rec = {"role": "JEV_REVIEW", "ok": True, "rounds": 0, "items": 0,
           "regime": None, "source": None, "err": None}
    try:
        rows = [r for r in _tail_jsonl(REVIEW_LOG) if _in_day(r.get("ts", ""), lo, hi)]
        rec["rounds"] = len(rows)
        for r in rows:
            msg = str(r.get("msg") or "")
            # "本轮 0 条（已落盘） regime=1.00 公告源=tushare"
            if "本轮" in msg:
                head = msg.split("（")[0].replace("本轮", "").replace("条", "").strip()
                try:
                    rec["items"] += int(head)
                except ValueError as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "tools/jev_roles_daily.py", exc)
            if "regime=" in msg:
                rec["regime"] = msg.split("regime=")[1].split()[0]
            if "公告源=" in msg:
                rec["source"] = msg.split("公告源=")[1].split()[0]
    except Exception as exc:          # noqa: BLE001
        rec.update(ok=False, err="%s: %s" % (type(exc).__name__, exc))
    return rec


def _load_json_list(path: Path) -> list:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:                 # noqa: BLE001
        return []


def collect_qd() -> dict:
    """JEV_QD：桥接推送成功率与失败清单。"""
    rec = {"role": "JEV_QD", "ok": True, "pushed": 0, "failed": 0,
           "success_rate": None, "failed_keys": [], "err": None}
    try:
        pushed = _load_json_list(QD_PUSHED)
        failed = _load_json_list(QD_FAILED)
        rec["pushed"] = len(pushed)
        rec["failed"] = len(failed)
        rec["failed_keys"] = [str(x) for x in failed[:20]]
        total = rec["pushed"] + rec["failed"]
        rec["success_rate"] = round(rec["pushed"] / total, 4) if total else None
    except Exception as exc:          # noqa: BLE001
        rec.update(ok=False, err="%s: %s" % (type(exc).__name__, exc))
    return rec


def _parse_jev(raw) -> dict:
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:                 # noqa: BLE001
        return {}


def sample_review_queue(day: str, n: int = SAMPLE_N) -> list[dict]:
    """JEV_REVIEW 复核队列：当日（按 ts）已判新闻抽 n 条。

    说明：news 表无「判读时间」列（审计缺口 ④），只能用新闻 ts 近似「当日判读对象」。
    只取最近 n 行（rowid 倒序），不扫全表。
    """
    if not NEWS_DB.is_file():
        return []
    lo, hi = _day_bounds(day)
    out: list[dict] = []
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % NEWS_DB.as_posix(), uri=True)
        try:
            rows = conn.execute(
                "SELECT id, ts, source, title, jev FROM news "
                "WHERE jev IS NOT NULL AND jev != '' ORDER BY rowid DESC LIMIT ?",
                (n * 4,)).fetchall()
        finally:
            conn.close()
        for nid, ts, source, title, raw in rows:
            if len(out) >= n:
                break
            if not _in_day(ts or "", lo, hi):
                continue
            j = _parse_jev(raw)
            out.append({
                "id": nid, "ts": ts, "source": source,
                "title": str(title or "")[:80],
                "bias": j.get("bias"), "conf": j.get("conf"),
                "event_type": j.get("event_type"), "src": j.get("src"),
                "reason": str(j.get("reason") or "")[:80],
            })
    except Exception:                 # noqa: BLE001  复核队列缺失不拖垮日报
        return out
    return out


def build(day: str) -> dict:
    return {
        "date": day,
        "generated_at": _now_iso(),
        "source": "tools/jev_roles_daily.py",
        "gate": collect_gate(day),
        "review": collect_review(day),
        "qd": collect_qd(),
        "review_queue": sample_review_queue(day),
    }


def _fmt(v, suffix="") -> str:
    return "—" if v is None else "%s%s" % (v, suffix)


def render_md(data: dict) -> str:
    """人读日报（纯函数，便于单测）。"""
    g, r, q = data["gate"], data["review"], data["qd"]
    day = data["date"]
    lines = [
        "# JEV 三席日报 · %s" % day,
        "",
        "生成于 %s ｜ 数据源：gate.log / review.log / qd_*.json / news.db（只读）"
        % data.get("generated_at", ""),
        "",
        "## JEV_GATE 廖化 · 告警门",
        "| 指标 | 值 |",
        "|---|---|",
        "| 当日轮询 | %s |" % _fmt(g.get("polls")),
        "| 推送 | %s |" % _fmt(g.get("push")),
        "| 拦截(降噪) | %s |" % _fmt(g.get("suppress")),
        "| 失败 | %s |" % _fmt(g.get("fail")),
        "| 重试 | %s |" % _fmt(g.get("retry")),
        "| 降噪率 | %s |" % _fmt(g.get("suppress_rate")),
        "",
        "## JEV_REVIEW 蒋干 · 复核",
        "| 指标 | 值 |",
        "|---|---|",
        "| 当日轮询轮数 | %s |" % _fmt(r.get("rounds")),
        "| 输出信号条数 | %s |" % _fmt(r.get("items")),
        "| regime | %s |" % _fmt(r.get("regime")),
        "| 公告源 | %s |" % _fmt(r.get("source")),
        "",
        "## JEV_QD 伊籍 · 桥接",
        "| 指标 | 值 |",
        "|---|---|",
        "| 推送成功 | %s |" % _fmt(q.get("pushed")),
        "| 失败 | %s |" % _fmt(q.get("failed")),
        "| 成功率 | %s |" % _fmt(q.get("success_rate")),
        "",
        "失败清单：%s" % (", ".join(q.get("failed_keys") or []) or "无"),
        "",
        "## 复核抽样（%d 条，按新闻 ts 近似当日）" % len(data.get("review_queue") or []),
    ]
    queue = data.get("review_queue") or []
    if not queue:
        lines.append("_今日无可抽样条目_")
    else:
        lines.append("| id | 来源 | bias | conf | 事件类型 | 标题 |")
        lines.append("|---|---|---|---|---|---|")
        for it in queue:
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                it.get("id"), it.get("source"), it.get("bias"),
                it.get("conf"), it.get("event_type"), it.get("title")))
    lines.append("")
    lines.append("> 三席 KPI 由 tools/agent_daily.py 判定：产物缺失/过期即「未交付」。")
    return "\n".join(lines) + "\n"


def write_outputs(data: dict, out_dir: Path | None = None) -> tuple[Path, Path]:
    day = data["date"]
    jdir = out_dir or DATA_DIR
    jdir.mkdir(parents=True, exist_ok=True)
    jpath = jdir / ("%s.json" % day)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    jpath.write_text(payload, encoding="utf-8")
    # 稳定路径：agent_daily 的 file 证据只看「最新一次生成」，不关心日期命名
    (jdir / "latest.json").write_text(payload, encoding="utf-8")
    # 三席各自一份（agent_daily 逐席判据，避免三席共用一个文件导致「一荣俱荣」）
    for key in ("gate", "review", "qd"):
        (jdir / ("%s.json" % key)).write_text(
            json.dumps(data.get(key) or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    mpath = OUTPUTS_DIR / ("jev_daily_%s.md" % day)
    mpath.write_text(render_md(data), encoding="utf-8")
    return jpath, mpath


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="JEV 三席日产出物生成器（只读）")
    ap.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD，默认今天")
    ap.add_argument("--out-dir", help="JSON 输出目录（默认 agent_platform/data/jev_roles）")
    ap.add_argument("--json", action="store_true", help="只打印 JSON")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    data = build(args.date)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    if args.dry_run:
        print(render_md(data))
        return 0
    jpath, mpath = write_outputs(data, Path(args.out_dir) if args.out_dir else None)
    print("已生成：%s" % jpath)
    print("人读日报：%s" % mpath)
    return 0


if __name__ == "__main__":
    sys.exit(main())
