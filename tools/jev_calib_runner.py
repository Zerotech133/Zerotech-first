# -*- coding: utf-8 -*-
"""JEV 后验校准 runner（WO-JEV-OPS-20261103，只读 dec_audit，只写 outputs/）。

背景（2026-11-03 实测）：`agent_platform/data/jev_audit/jev_audit.db::dec_audit`
115,220 行，其中 `hit` 为 NULL 90,609 行（78.6%）——后验未回填；`jev_calib.py`
只有代码、无任务无产物。本 runner 把「hit 覆盖率 / 方向命中率 / 分段样本」算清楚，
产出可复核的 JSON + Markdown，**不写 DB、不执行任何 L2 操作**。

口径：
  - `hit`：1=方向命中，0=未命中，NULL=未回填后验（`ret/bench/excess` 同时为 NULL）；
  - hit 覆盖率 = hit∈{0,1} 行数 / 总行数；
  - 方向命中率 = hit=1 / hit∈{0,1}（hit 可用样本）；neutral 的带宽命中同源口径；
  - 分段：model / horizon / source 各给 n、hit 可用、覆盖率、方向命中率；
  - 坏行（fail-visible）：hit 非 0/1/NULL 的值、hit 与 ret 不一致的行，逐条样例落报告。

用法：
  python -X utf8 -B tools/jev_calib_runner.py            # 写 outputs/jev_calib_<YYYYMMDD>.json/.md
  python -X utf8 -B tools/jev_calib_runner.py --json     # 同时打印 JSON
退出码：0=成功；1=产物写失败；2=前置错误（库/表/列缺失、参数非法）；
        4=存在坏行且指定 --fail-on-bad。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path(
    os.environ.get("JEV_AUDIT_DB")
    or (REPO / "agent_platform" / "data" / "jev_audit" / "jev_audit.db"))
DEFAULT_OUT_DIR = Path(os.environ.get("JEV_CALIB_OUT_DIR") or (REPO / "outputs"))
REQUIRED_COLUMNS = ("news_id", "hit", "model", "horizon", "source")
OPTIONAL_COLUMNS = ("seq", "ret", "posterior_at", "ts", "trade_day", "bias",
                    "conf", "event_type", "prompt_version")
SEGMENTS = ("model", "horizon", "source")
UNKNOWN = "(unknown)"


class PreconditionError(RuntimeError):
    """前置条件不满足（缺库/缺表/缺列/参数非法）——fail-visible，rc=2。"""


# --------------------------------------------------------------------------- #
# 只读连接 / schema
# --------------------------------------------------------------------------- #
def ro_connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        "file:%s?mode=ro" % Path(db_path).as_posix(), uri=True, timeout=30)
    conn.execute("PRAGMA query_only=1")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(r[1]) for r in conn.execute('PRAGMA table_info("%s")' % table)]


def require_schema(conn: sqlite3.Connection, db_path: Path) -> list[str]:
    """校验 dec_audit；缺表/缺列 fail-visible（rc=2）。返回可用列。"""
    if not table_exists(conn, "dec_audit"):
        raise PreconditionError(
            "缺表 dec_audit：%s 不是 jev_audit.db 或 schema 未初始化（表=%s）"
            % (db_path, [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]))
    cols = table_columns(conn, "dec_audit")
    missing = [c for c in REQUIRED_COLUMNS if c not in cols]
    if missing:
        raise PreconditionError(
            "dec_audit 缺列 %s（实际列=%s）" % (missing, cols))
    return cols


# --------------------------------------------------------------------------- #
# 指标
# --------------------------------------------------------------------------- #
def classify_hit(value) -> str:
    """返回 hit / miss / null / invalid / hit_coerced / miss_coerced。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "invalid"
    if isinstance(value, (int, float)):
        if value == 1:
            return "hit"
        if value == 0:
            return "miss"
        return "invalid"
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return "null"
        try:
            num = float(text)
        except ValueError:
            return "invalid"
        if num == 1:
            return "hit_coerced"
        if num == 0:
            return "miss_coerced"
        return "invalid"
    return "invalid"


def _norm_seg(value) -> str:
    if value is None:
        return UNKNOWN
    text = str(value).strip()
    return text if text else UNKNOWN


def _pct(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _seg_row(name: str) -> dict:
    return {"name": name, "n": 0, "labeled": 0, "hit1": 0, "miss": 0,
            "null": 0, "invalid": 0, "coerced": 0}


def _finish_seg(seg: dict) -> dict:
    seg["coverage"] = _pct(seg["labeled"], seg["n"])
    seg["hit_rate"] = _pct(seg["hit1"], seg["labeled"])
    seg["null_rate"] = _pct(seg["null"], seg["n"])
    return seg


def analyze(conn: sqlite3.Connection, cols: list[str]) -> dict:
    """只读统计；返回完整报告 dict（不含文件路径）。"""
    has_ret = "ret" in cols
    has_posterior = "posterior_at" in cols
    select_parts = ["news_id", "hit", "model", "horizon", "source"]
    if "seq" in cols:
        select_parts.append("seq")
    if has_ret:
        select_parts.append("ret")
    if has_posterior:
        select_parts.append("posterior_at")
    if "ts" in cols:
        select_parts.append("ts")
    if "trade_day" in cols:
        select_parts.append("trade_day")
    sql = "SELECT %s FROM dec_audit" % ", ".join(select_parts)

    total = 0
    n_hit1 = n_miss = n_null = n_invalid = n_coerced = 0
    n_posterior = 0
    bad_invalid: list[dict] = []
    bad_ret: list[dict] = []
    segments = {name: {} for name in SEGMENTS}
    ts_min = ts_max = ""
    trade_min = trade_max = ""
    idx = {name: select_parts.index(name) for name in select_parts}
    for row in conn.execute(sql):
        total += 1
        news_id = row[idx["news_id"]]
        hit = row[idx["hit"]]
        state = classify_hit(hit)
        if state == "hit":
            n_hit1 += 1
        elif state == "miss":
            n_miss += 1
        elif state == "null":
            n_null += 1
        elif state == "hit_coerced":
            n_hit1 += 1
            n_coerced += 1
        elif state == "miss_coerced":
            n_miss += 1
            n_coerced += 1
        else:
            n_invalid += 1
            if len(bad_invalid) < 20:
                bad_invalid.append({"news_id": str(news_id), "hit": repr(hit),
                                    "seq": row[idx["seq"]] if "seq" in idx else None})
        if has_ret:
            ret = row[idx["ret"]]
            labeled = state in ("hit", "miss", "hit_coerced", "miss_coerced")
            if labeled and ret is None:
                if len(bad_ret) < 20:
                    bad_ret.append({"news_id": str(news_id),
                                    "hit": repr(hit), "ret": repr(ret),
                                    "kind": "hit_labeled_ret_null"})
            elif state in ("null", "invalid") and ret is not None:
                if len(bad_ret) < 20:
                    bad_ret.append({"news_id": str(news_id),
                                    "hit": repr(hit), "ret": repr(ret),
                                    "kind": "hit_missing_ret_present"})
        if has_posterior and row[idx["posterior_at"]] not in (None, ""):
            n_posterior += 1
        if "ts" in idx:
            ts = str(row[idx["ts"]] or "")
            if ts:
                ts_min = ts if not ts_min or ts < ts_min else ts_min
                ts_max = ts if ts > ts_max else ts_max
        if "trade_day" in idx:
            day = str(row[idx["trade_day"]] or "")
            if day:
                trade_min = day if not trade_min or day < trade_min else trade_min
                trade_max = day if day > trade_max else trade_max
        for name in SEGMENTS:
            key = _norm_seg(row[idx[name]])
            seg = segments[name].setdefault(key, _seg_row(key))
            seg["n"] += 1
            if state in ("hit", "hit_coerced"):
                seg["hit1"] += 1
                seg["labeled"] += 1
            elif state in ("miss", "miss_coerced"):
                seg["miss"] += 1
                seg["labeled"] += 1
            elif state == "null":
                seg["null"] += 1
            else:
                seg["invalid"] += 1
            if state in ("hit_coerced", "miss_coerced"):
                seg["coerced"] += 1

    labeled = n_hit1 + n_miss
    overall = {
        "total": total,
        "hit_available": labeled,
        "hit_1": n_hit1,
        "hit_0": n_miss,
        "hit_missing": n_null,
        "hit_invalid": n_invalid,
        "hit_coerced": n_coerced,
        "hit_coverage": _pct(labeled, total),
        "direction_hit_rate": _pct(n_hit1, labeled),
        "hit_missing_rate": _pct(n_null, total),
        "posterior_filled": n_posterior if has_posterior else None,
    }
    seg_out = {}
    for name in SEGMENTS:
        rows = [_finish_seg(seg) for seg in segments[name].values()]
        rows.sort(key=lambda r: (-r["n"], r["name"]))
        seg_out[name] = rows
    bad_rows_invalid = n_invalid
    bad_rows_ret = len(bad_ret)
    return {
        "tool": "jev_calib_runner",
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scope": {
            "table": "dec_audit",
            "hit_semantics": "1=方向命中 / 0=未命中 / NULL=未回填后验",
            "direction_hit_rate": "hit=1 / hit∈{0,1}（hit 可用样本）",
        },
        "ranges": {
            "ts_min": ts_min, "ts_max": ts_max,
            "trade_day_min": trade_min, "trade_day_max": trade_max,
        },
        "overall": overall,
        "segments": seg_out,
        "bad_rows": {
            "n": bad_rows_invalid + bad_rows_ret,
            "invalid_hit": bad_rows_invalid,
            "hit_ret_inconsistent": bad_rows_ret,
            "samples_invalid_hit": bad_invalid,
            "samples_hit_ret": bad_ret,
        },
    }


# --------------------------------------------------------------------------- #
# 产物
# --------------------------------------------------------------------------- #
def _fmt_pct(rate) -> str:
    return "-" if rate is None else "%.2f%%" % (rate * 100.0)


def _fmt_num(n) -> str:
    return "{:,}".format(int(n))


def render_md(rep: dict, db_path: Path, out_json: Path, out_md: Path) -> str:
    o = rep["overall"]
    r = rep["ranges"]
    lines = [
        "# JEV 后验校准报告（%s）" % rep["generated_at"],
        "",
        "- DB（只读）：`%s`" % db_path,
        "- 表：`dec_audit`；hit 语义：%s" % rep["scope"]["hit_semantics"],
        "- 产物：`%s` / `%s`" % (out_json.name, out_md.name),
        "",
        "## 一、总览",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        "| 总行数 | %s |" % _fmt_num(o["total"]),
        "| hit 可用（0/1） | %s |" % _fmt_num(o["hit_available"]),
        "| 其中 hit=1 / hit=0 | %s / %s |" % (_fmt_num(o["hit_1"]),
                                                _fmt_num(o["hit_0"])),
        "| hit 为空（未回填后验） | %s（占比 %s） |" % (
            _fmt_num(o["hit_missing"]), _fmt_pct(o["hit_missing_rate"])),
        "| hit 非法坏行 | %s |" % _fmt_num(o["hit_invalid"]),
        "| **hit 覆盖率** | **%s** |" % _fmt_pct(o["hit_coverage"]),
        "| **方向命中率（hit 可用样本）** | **%s** |" % _fmt_pct(
            o["direction_hit_rate"]),
        "| posterior_at 已回填 | %s |" % (
            "-" if o["posterior_filled"] is None
            else _fmt_num(o["posterior_filled"])),
        "| ts 范围 | %s ~ %s |" % (r["ts_min"] or "-", r["ts_max"] or "-"),
        "| trade_day 范围 | %s ~ %s |" % (r["trade_day_min"] or "-",
                                           r["trade_day_max"] or "-"),
        "",
    ]
    titles = {"model": "按 model 分段", "horizon": "按 horizon 分段",
              "source": "按 source 分段"}
    lines += ["## 二、分段指标", ""]
    for name in SEGMENTS:
        lines += ["### %s" % titles[name], "",
                  "| %s | n | hit 可用 | hit=1 | hit 为空 | 覆盖率 | 方向命中率 |"
                  % name,
                  "|---|---|---|---|---|---|---|"]
        for seg in rep["segments"][name]:
            lines.append("| `%s` | %s | %s | %s | %s | %s | %s |" % (
                str(seg["name"]).replace("|", "\\|"), _fmt_num(seg["n"]),
                _fmt_num(seg["labeled"]), _fmt_num(seg["hit1"]),
                _fmt_num(seg["null"]), _fmt_pct(seg["coverage"]),
                _fmt_pct(seg["hit_rate"])))
        lines.append("")
    bad = rep["bad_rows"]
    lines += ["## 三、坏行（fail-visible）", "",
              "- hit 非法：%s 行；hit 与 ret 不一致：%s 行；合计 %s 行。"
              % (_fmt_num(bad["invalid_hit"]),
                 _fmt_num(bad["hit_ret_inconsistent"]), _fmt_num(bad["n"])), ""]
    if bad["samples_invalid_hit"]:
        lines += ["hit 非法样例（前 20）：", ""]
        for item in bad["samples_invalid_hit"]:
            lines.append("- news_id=`%s` hit=%s seq=%s"
                         % (item["news_id"], item["hit"], item.get("seq")))
        lines.append("")
    if bad["samples_hit_ret"]:
        lines += ["hit/ret 不一致样例（前 20）：", ""]
        for item in bad["samples_hit_ret"]:
            lines.append("- news_id=`%s` hit=%s ret=%s kind=%s"
                         % (item["news_id"], item["hit"], item["ret"],
                            item["kind"]))
        lines.append("")
    lines += [
        "## 四、口径与边界",
        "",
        "1. 本 runner **只读** `dec_audit`，不写 DB、不改判定、不重启任何服务；",
        "2. `hit` 为空 = `ret/bench/excess` 也为空的后验未回填样本（T+1 行情链未覆盖），",
        "   不是「未命中」；方向命中率分母只取 hit 可用样本；",
        "3. 分段结论受样本量/分布影响（如 `horizon=unknown` 占多数），仅作校准输入，",
        "   不自动改变生产阈值/权重；",
        "4. 定时化（每日计划任务）属 L2，待老田批准：",
        "   `docs/ops/2026-11-03_JEV复核与校准L2申请单_v1.md`。",
        "",
    ]
    return "\n".join(lines)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _parse_date_arg(value: str) -> str:
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        raise PreconditionError("--date 必须是 YYYYMMDD（8 位数字），实际=%r" % value)
    try:
        time.strptime(text, "%Y%m%d")
    except ValueError as exc:
        raise PreconditionError("--date 非法：%r（%s）" % (value, exc))
    return text


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 非 TTY / 旧解释器忽略
        pass
    ap = argparse.ArgumentParser(
        description="JEV 后验校准报告（只读 dec_audit，只写 outputs/）")
    ap.add_argument("--db", default=str(DEFAULT_DB),
                    help="jev_audit.db 路径（默认 %s）" % DEFAULT_DB)
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR),
                    help="产物目录（默认 %s）" % DEFAULT_OUT_DIR)
    ap.add_argument("--date", default=time.strftime("%Y%m%d"),
                    help="产物日期戳 YYYYMMDD（默认今天）")
    ap.add_argument("--json", action="store_true", help="额外打印 JSON 报告")
    ap.add_argument("--fail-on-bad", action="store_true",
                    help="存在坏行时 rc=4")
    a = ap.parse_args(argv)

    try:
        date_str = _parse_date_arg(a.date)
    except PreconditionError as exc:
        print("[FAIL] %s" % exc, file=sys.stderr)
        return 2
    db_path = Path(a.db)
    if not db_path.is_file():
        print("[FAIL] DB 不存在：%s" % db_path, file=sys.stderr)
        return 2
    try:
        conn = ro_connect(db_path)
        try:
            cols = require_schema(conn, db_path)
            rep = analyze(conn, cols)
        finally:
            conn.close()
    except PreconditionError as exc:
        print("[FAIL] %s" % exc, file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print("[FAIL] 只读统计 SQLite 错误：%s" % exc, file=sys.stderr)
        return 2

    out_dir = Path(a.out_dir)
    out_json = out_dir / ("jev_calib_%s.json" % date_str)
    out_md = out_dir / ("jev_calib_%s.md" % date_str)
    rep["outputs"] = {"json": str(out_json), "md": str(out_md)}
    rep["db"] = str(db_path)
    try:
        _atomic_write(out_json, json.dumps(rep, ensure_ascii=False, indent=2) + "\n")
        _atomic_write(out_md, render_md(rep, db_path, out_json, out_md))
    except OSError as exc:
        print("[FAIL] 产物写入失败：%s" % exc, file=sys.stderr)
        return 1

    o = rep["overall"]
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print("JEV 后验校准报告（只读 dec_audit）")
        print("DB: %s" % db_path)
        print("总行数 %s ｜ hit 可用 %s ｜ hit=1 %s / hit=0 %s ｜ hit 为空 %s（%s）"
              % (_fmt_num(o["total"]), _fmt_num(o["hit_available"]),
                 _fmt_num(o["hit_1"]), _fmt_num(o["hit_0"]),
                 _fmt_num(o["hit_missing"]), _fmt_pct(o["hit_missing_rate"])))
        print("hit 覆盖率 %s ｜ 方向命中率（hit 可用样本）%s ｜ 坏行 %s"
              % (_fmt_pct(o["hit_coverage"]),
                 _fmt_pct(o["direction_hit_rate"]),
                 _fmt_num(rep["bad_rows"]["n"])))
        for name in SEGMENTS:
            top = rep["segments"][name][:5]
            print("%s 分段 Top%d: %s" % (
                name, len(top),
                "；".join("%s n=%s 命中率=%s" % (
                    seg["name"], _fmt_num(seg["n"]), _fmt_pct(seg["hit_rate"]))
                    for seg in top)))
        print("产物: %s" % out_json)
        print("      %s" % out_md)
    if a.fail_on_bad and rep["bad_rows"]["n"] > 0:
        print("[FAIL] 存在 %d 行坏行，--fail-on-bad 置 rc=4"
              % rep["bad_rows"]["n"], file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
