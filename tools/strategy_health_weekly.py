# -*- coding: utf-8 -*-
r"""Phase 6-B：Strategy Health 周报生成 CLI（只读 PG → 原子写 latest.json）。

把「读多日后验行 → 生成周报 JSON → 落盘」收敛为可重复、可测试的单一入口，
供人工按需运行（**不新增计划任务**）。产物默认写
``automation_runs/strategy_health/latest.json``（gitignore 产物目录），
与 dashboard_api 的 ``GET /strategy_health.json`` 读取路径对齐。

只读红线
--------
- rows 默认从 PG ``strategy.signals_combined`` **只读**拉取
  （``strategy_layer.combined_verdicts.load_rows_pg``，SELECT only，limit 可配），
  也可 ``--rows-file`` 离线注入；本 CLI 不写 PG、不改 registry/权重/交易链；
- 生成逻辑复用 ``strategy_layer.strategy_health_report.build_weekly_health_report``；
- 落盘仅限 ``--out`` 显式路径（默认 automation_runs/strategy_health/latest.json），
  原子写（tmp + os.replace，失败清理 tmp 不留半文件）；``--dry-run`` 不落盘；
- **不造数**：PG 不可用 → 显式错误退出码；无 rows → 显式 ``insufficient_data``
  报告（不填充假行、不覆盖既有 latest.json）；变量/窗口缺失一律显式 null。

退出码
------
  0 = 报告生成成功（dry-run 时为「成功但未落盘」）
  1 = PG 不可用 / 只读拉取失败（显式报错，不落盘）
  2 = 用法/输入错误（非法参数、rows-file 缺失或坏 JSON）
  3 = 无 rows（显式 insufficient_data；不造数、不落盘）

用法::

    python -X utf8 tools/strategy_health_weekly.py
    python -X utf8 tools/strategy_health_weekly.py --days 14 --limit 5000
    python -X utf8 tools/strategy_health_weekly.py --rows-file rows.json \
        --out C:\Temp\weekly.json
    python -X utf8 tools/strategy_health_weekly.py --dry-run       # 只打印不落盘
"""

from __future__ import annotations

import json
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUANT_BT = os.path.join(REPO_ROOT, "quant_backtest")
for _path in (REPO_ROOT, QUANT_BT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

try:  # 只读复用周报生成器与 PG 行加载器（脚本直跑路径兜底）
    from strategy_layer import combined_verdicts as _combined_verdicts
    from strategy_layer import strategy_health_report as _health_report
except ImportError:  # pragma: no cover - 正常路径已加 sys.path
    sys.path.insert(0, QUANT_BT)
    from strategy_layer import combined_verdicts as _combined_verdicts
    from strategy_layer import strategy_health_report as _health_report

TID = "WO-HEALTH-WEEKLY-GEN-20261031"
GENERATOR = "tools/strategy_health_weekly.py"
DEFAULT_DAYS = 7
# 行数上限默认值：约 150 行/交易日（signals_combined 融合行），3000 行可覆盖
# 默认 7 天窗口；--days 放大时需同步调大 --limit（窗口外的行会被报告显式计入
# rows_out_of_window，不会静默丢数）。
DEFAULT_LIMIT = 3000
DEFAULT_OUT = os.path.join(
    REPO_ROOT, "automation_runs", "strategy_health", "latest.json"
)

EXIT_OK = 0
EXIT_PG_UNAVAILABLE = 1
EXIT_USAGE = 2
EXIT_NO_ROWS = 3

USAGE = """用法:
  python -X utf8 tools/strategy_health_weekly.py [--days N] [--limit N]
      [--out PATH] [--rows-file PATH] [--dry-run]

只读：默认从 PG signals_combined 拉 rows（SELECT only）→ 生成周报 JSON →
原子写 automation_runs/strategy_health/latest.json；不写 PG、不改 registry/
权重、不下单、不新增计划任务。--dry-run 只打印 JSON 不落盘。
退出码：0=成功；1=PG 不可用；2=用法/输入错误；3=无 rows（insufficient，不造数）。
"""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ── 只读 rows 加载（PG / rows-file） ─────────────────────────────────
def _load_rows_pg(limit: int) -> list:
    """PG 只读拉取（薄包装，便于测试 mock；失败由 run() 显式转错误码）。"""
    return _combined_verdicts.load_rows_pg(limit=limit)


def load_rows(limit: int = DEFAULT_LIMIT, rows_file=None) -> tuple:
    """加载 rows → ``(rows, source_dict)``。

    - ``rows_file`` 给出时离线读取（不连 PG）；坏文件抛 ``ValueError``；
    - 否则从 PG 只读拉取；拉取失败统一抛 ``RuntimeError``（显式，不兜底造数）。
    """
    if rows_file:
        try:
            rows = _combined_verdicts.load_rows_file(rows_file)
        except (OSError, ValueError) as exc:  # 缺失/坏 JSON 统一显式输入错误
            raise ValueError("rows-file 读取失败 %s: %s" % (rows_file, exc)) from exc
        return rows, {
            "kind": "rows_file",
            "path": os.path.abspath(rows_file),
        }
    try:
        rows = _load_rows_pg(limit)
    except Exception as exc:  # noqa: BLE001 - PG 不可用必须显式上抛
        raise RuntimeError(
            "PG 只读拉取失败（%s）: %s" % (type(exc).__name__, str(exc)[:300])
        ) from exc
    return rows, {"kind": "pg", "limit": int(limit), "query": "SELECT only"}


def _rows_source_label(source: dict) -> str:
    if source.get("kind") == "rows_file":
        return "rows_file:%s" % source.get("path")
    return "pg:signals_combined:limit=%d" % int(source.get("limit") or 0)


# ── 生成 + 原子写 ────────────────────────────────────────────────────
def build_report(rows, source: dict, days: int = DEFAULT_DAYS) -> dict:
    """调用周报生成器 → 报告 dict（附生成器来源元数据；不落盘）。"""
    doc = _health_report.build_weekly_health_report(
        rows=rows,
        rows_source=_rows_source_label(source),
        days=days,
    )
    doc["generator"] = GENERATOR
    doc["generator_wo"] = TID
    doc["source"] = dict(source)
    doc["generated_at"] = doc.get("generated_at") or _now_iso()
    return doc


def write_report_atomic(doc: dict, out_path: str) -> str:
    """JSON 原子写（tmp + os.replace；失败清理 tmp，不留半文件；自动建父目录）。"""
    path = os.path.abspath(out_path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    text = json.dumps(doc, ensure_ascii=False, indent=2)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def _summary_line(doc: dict, out_path=None, dry_run: bool = False) -> str:
    period = doc.get("period") or {}
    summary = doc.get("summary") or {}
    levels = doc.get("health_levels") or {}
    target = (
        "dry-run（未落盘）"
        if dry_run
        else "→ %s" % (out_path or DEFAULT_OUT)
    )
    return (
        "[strategy_health_weekly] status=%s period=%s~%s days=%d rows=%d "
        "strategies=%d green=%d yellow=%d red=%d insufficient=%d tickets=%d "
        "asof=%s %s"
        % (
            doc.get("status") or "-",
            period.get("start") or "-",
            period.get("end") or "-",
            period.get("trading_days") or 0,
            period.get("rows") or 0,
            summary.get("strategies") or 0,
            levels.get("green") or 0,
            levels.get("yellow") or 0,
            levels.get("red") or 0,
            levels.get("insufficient") or 0,
            summary.get("bias_tickets") or 0,
            doc.get("asof") or "-",
            target,
        )
    )


# ── 主流程 ───────────────────────────────────────────────────────────
def run(
    days: int = DEFAULT_DAYS,
    limit: int = DEFAULT_LIMIT,
    out=None,
    rows_file=None,
    dry_run: bool = False,
) -> int:
    """生成一次周报；返回退出码（见模块 docstring）。"""
    if not isinstance(days, int) or isinstance(days, bool) or days <= 0:
        print("[strategy_health_weekly] --days 必须为正整数: %r" % (days,), file=sys.stderr)
        return EXIT_USAGE
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        print("[strategy_health_weekly] --limit 必须为正整数: %r" % (limit,), file=sys.stderr)
        return EXIT_USAGE

    try:
        rows, source = load_rows(limit=limit, rows_file=rows_file)
    except ValueError as exc:  # rows-file 缺失/坏 JSON
        print("[strategy_health_weekly] 输入非法: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    except RuntimeError as exc:  # PG 不可用（显式错误，不造数、不落盘）
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": "pg_unavailable",
                    "error": str(exc),
                    "source": "pg",
                    "readonly": True,
                },
                ensure_ascii=False,
            )
        )
        print("[strategy_health_weekly] %s" % exc, file=sys.stderr)
        return EXIT_PG_UNAVAILABLE

    if not rows:
        # 无 rows：显式 insufficient_data 报告，不填充假行、不覆盖既有 latest.json
        doc = build_report([], source, days=days)
        print(json.dumps(doc, ensure_ascii=False))
        print(
            "[strategy_health_weekly] 无 rows（status=%s）：不造数、不落盘"
            % (doc.get("status") or "-"),
            file=sys.stderr,
        )
        return EXIT_NO_ROWS

    doc = build_report(rows, source, days=days)
    if dry_run:
        print(json.dumps(doc, ensure_ascii=False))
        print(_summary_line(doc, dry_run=True), file=sys.stderr)
        return EXIT_OK
    out_path = write_report_atomic(doc, out or DEFAULT_OUT)
    print(_summary_line(doc, out_path=out_path))
    return EXIT_OK


# ── CLI（手工解析，风格同 strategy_health_report） ───────────────────
_VALUE_FLAGS = ("--days", "--limit", "--out", "--rows-file")


def main(argv=None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    days = DEFAULT_DAYS
    limit = DEFAULT_LIMIT
    out = None
    rows_file = None
    dry_run = False
    index = 0
    while index < len(argv):
        arg = str(argv[index])
        if arg in ("-h", "--help"):
            print(USAGE)
            return 0
        if arg == "--dry-run":
            dry_run = True
            index += 1
            continue
        if arg not in _VALUE_FLAGS:
            print("[strategy_health_weekly] 未知参数: %s" % arg, file=sys.stderr)
            print(USAGE, file=sys.stderr)
            return EXIT_USAGE
        if index + 1 >= len(argv):
            print("[strategy_health_weekly] 参数 %s 缺值" % arg, file=sys.stderr)
            return EXIT_USAGE
        value = str(argv[index + 1])
        index += 2
        if arg == "--days":
            try:
                days = int(value)
            except ValueError:
                print(
                    "[strategy_health_weekly] --days 必须为整数: %r" % value,
                    file=sys.stderr,
                )
                return EXIT_USAGE
        elif arg == "--limit":
            try:
                limit = int(value)
            except ValueError:
                print(
                    "[strategy_health_weekly] --limit 必须为整数: %r" % value,
                    file=sys.stderr,
                )
                return EXIT_USAGE
        elif arg == "--out":
            out = value
        elif arg == "--rows-file":
            rows_file = value
    return run(
        days=days,
        limit=limit,
        out=out,
        rows_file=rows_file,
        dry_run=dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
