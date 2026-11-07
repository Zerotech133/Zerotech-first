# -*- coding: utf-8 -*-
"""6 表增量接管通道编排器（K-STOCK-211 增量线）。

用户裁决 2026-10-17：纯脚本全自动（每日 20:30 计划任务）+ 发布后自动提交
manifest（4dc69546 预批准模式通道化）。

链路（三层，发布器协议零改动——增量只是新消费者）：
  1. capture：subprocess 调 tail_fill_capture.py（缺口算法天然增量：只拉
     > 生产 max d_*.parquet 的开市日；脏行过滤/万行翻页/幂等 skip 内建）
  2. 质量门禁：新 receipt 校验 row_count>=表阈值 且 duplicate_keys==0；
     不合格自动移 held_tail_fill/<table>/ 留证（margin_detail T+1 源侧
     滞后自愈：次日缺口重算自动重试，4dc69546 判例常态化）
  3. 发布：subprocess 调 staging_materialize_full_history.py
     --pattern "tail_fill/*"（幂等 skip + sha/行数双回读 + 发布锁互斥人工单；
     增量只填空，不带 --allow-overwrite——覆盖类修复单永远走人工）
  4. manifest：仅发布 stats.mismatch==0 且 missing==0 时更新（否则 DEGRADED
     留人工）；rows 累加新合格单元、file 指最新分片、note 追加通道记录（滚动
     保留最近 3 条）
  5. 账本留痕：manifest 写回后**只记实际写入量**（path/tables/units/rows_added），
     不做 git 提交——`data/` 被 .gitignore 整体忽略，manifest 非版本化账本
     （2026-11-07 K-ORG-20261107-S3 裁决；原「git add + 内联身份提交」从未
     生效且恒输出假 no_diff，详见第 5 步注释）
  6. 报告：work/increment_guard_YYYYMMDDTHHMMSS.json（status: OK/NO_GAP/
     HELD_ONLY/DEGRADED/FAIL，供例行检查与告警）

安全边界：无新开市日→NO_GAP 直落报告；capture 非零退出→已合格单元照发
（缺口明日自愈）+ 报告记 rc；发布锁由发布器内建持有-释放。
"""

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
import logging


def _configure_stdio() -> None:
    """2026-10-26 EXEC-R38：计划任务在 GBK 控制台下 print 捕获输出（含 U+FFFD）
    会 UnicodeEncodeError 崩溃——TushareIncrementGuard 自 10-17 起每日 rc=1，
    ts_daily/adj_factor/daily_basic/margin_detail/index_daily/moneyflow 六表
    自 10-16 起停止更新。统一 stdout/stderr 为 UTF-8 + errors=replace。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/increment_guard.py", exc)


def _safe_print(text: str, limit: int, file=None) -> None:
    chunk = (text or "")[-limit:]
    if not chunk:
        return
    try:
        print(chunk, end="", file=file)
    except UnicodeEncodeError:
        print(chunk.encode("ascii", "replace").decode("ascii"), end="", file=file)


_configure_stdio()

QB = Path(__file__).resolve().parent
STAGING = QB / "data" / "tushare_staging"
TAIL = STAGING / "tail_fill"
HELD = STAGING / "held_tail_fill"
PROD = QB / "data" / "tushare"
WORK = QB.parent / "work"

TABLES = [
    "ts_daily",
    "ts_adj_factor",
    "ts_daily_basic",
    "ts_margin_detail",
    "ts_index_daily",
    "ts_moneyflow",
]
# 行数下限：近期完整日分位取松下限（margin_detail ~4455 完整 / 2001 一所）
MIN_ROWS = {
    "ts_daily": 5000,
    "ts_adj_factor": 5000,
    "ts_daily_basic": 4400,
    "ts_margin_detail": 4000,
    "ts_index_daily": 9000,
    "ts_moneyflow": 4500,
}
CHANNEL_TAG = "K-STOCK-211 增量通道"
# 注：原 GIT_ID = ["-c","user.name=laotian","-c","user.email=laotian@local"]
# 已随「manifest 不入库」裁决（2026-11-07 K-ORG-20261107-S3）停用并删除，
# 便于检索时一眼看出「本脚本不再调 git」。


def run(cmd, **kw):
    return subprocess.run(
        cmd,
        cwd=str(QB),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kw,
    )


def receipts(tbl_dir: Path) -> set:
    return (
        {p.name for p in tbl_dir.glob("*.receipt.json")} if tbl_dir.is_dir() else set()
    )


def table_of(receipt_name: str) -> str:
    return receipt_name.rsplit("_", 1)[0]


def prod_max_date(dir_name: str) -> str:
    fs = (
        sorted((PROD / dir_name).glob("d_*.parquet"))
        if (PROD / dir_name).is_dir()
        else []
    )
    return fs[-1].stem[2:] if fs else "-"


def _record_manifest(report, mp, per_tbl):
    """第 5 步：账本留痕（K-ORG-20261107-S3 裁决 2026-11-07，manifest 不入库）。

    原设计（2026-10-17「通道化自动提交」）为 git add + 内联身份提交，但两条
    硬事实使该步从未生效、且恒产出假信号：
      a) 路径错：run() 的 cwd 是 quant_backtest/，"_manifest.json" 解析到
         quant_backtest/_manifest.json（不存在）→ git add rc=128；
      b) 即便路径改对，data/ 被 .gitignore:27 整体忽略、该文件从未被跟踪，
         git add rc=1、`git status --short -- data/tushare/_manifest.json`
         恒空 → 恒走「manifest 无变更」分支。
    判例：2026-11-07 13:09:11 那次真补 5573 行（DATAGAP 补跑）的报告仍写
    git_commit="no_diff"——数据写了却对外声称无变更，假信号实证。

    故改为只记「实际写了什么」，不再声称提交；git_commit 字段保留（下游解析
    不破），取值显式标注跳过原因，禁止再回落到无法证伪的措辞。
    """
    report["manifest"] = {
        "path": str(mp),
        "tables": sorted(per_tbl),
        "units": sum(len(a["dates"]) for a in per_tbl.values()),
        "rows_added": sum(a["rows"] for a in per_tbl.values()),
    }
    report["git_commit"] = "skipped:untracked_by_design"
    print(
        "[git] 跳过提交（manifest 位于 .gitignore 的 data/ 下，非版本化账本）；"
        "本次写入 +%d 单元 +%d 行 → %s"
        % (
            report["manifest"]["units"],
            report["manifest"]["rows_added"],
            mp.name,
        )
    )
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tables", default=",".join(TABLES))
    ap.add_argument(
        "--dryrun",
        action="store_true",
        help="只预览缺口/阈值/生产 max，不 capture 不发布",
    )
    args = ap.parse_args()
    tables = [t for t in args.tables.split(",") if t in TABLES]
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    report = {
        "tool": "increment_guard",
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "tables": tables,
        "dryrun": args.dryrun,
    }

    # -- dryrun：缺口预览（读生产 max，不调 API） --
    if args.dryrun:
        preview = {}
        for t in tables:
            preview[t] = {"prod_max": prod_max_date(t[3:]), "min_rows": MIN_ROWS[t]}
        report["preview"] = preview
        print(f"[dryrun] {json.dumps(preview, ensure_ascii=False)}")
        print("[dryrun] 计划任务真跑链路：capture→门禁→发布→manifest→git")
        (WORK / f"increment_guard_{stamp}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return 0

    # 1. capture（前后 receipt 集合 diff 得新单元）
    before = {t: receipts(TAIL / t) for t in tables}
    cap = run(
        [sys.executable, "-u", "tail_fill_capture.py", "--tables", ",".join(tables)]
    )
    rc_cap = cap.returncode
    report["capture_rc"] = rc_cap
    _safe_print(cap.stdout, 2000)
    if cap.stderr:
        _safe_print(cap.stderr, 1000, file=sys.stderr)

    # 2. 质量门禁：新 receipt 行数阈值 + duplicate_keys
    passed, held = [], []
    for t in tables:
        for name in sorted(receipts(TAIL / t) - before[t]):
            rp = TAIL / t / name
            e = json.loads(rp.read_text(encoding="utf-8"))
            rows, dup = e.get("row_count"), int(e.get("duplicate_keys") or 0)
            if rows is None or rows < MIN_ROWS[t] or dup:
                held.append(
                    {
                        "table": t,
                        "receipt": name,
                        "row_count": rows,
                        "duplicate_keys": dup,
                        "min_rows": MIN_ROWS[t],
                    }
                )
                HELD.joinpath(t).mkdir(parents=True, exist_ok=True)
                pq = rp.with_name(rp.name.replace(".receipt.json", ".parquet"))
                for f in (rp, pq):
                    if f.is_file():
                        f.replace(HELD / t / f.name)
            else:
                passed.append(
                    {
                        "table": t,
                        "date": (e.get("request_params") or {}).get("trade_date"),
                        "row_count": rows,
                    }
                )
    report["units_passed"], report["units_held"] = passed, held
    if held:
        print(
            f"[held] {len(held)} 单元移 held_tail_fill（源侧滞后/行数不足）："
            + "; ".join(f"{h['table']}/{h['receipt']}({h['row_count']})" for h in held)
        )

    if not passed:
        report["status"] = "NO_GAP" if not held else "HELD_ONLY"
        print(f"[done] status={report['status']}（无新合格单元，不发布）")
        _write(report, stamp)
        return 0

    # 3. 发布（增量只填空，绝不带 --allow-overwrite）
    mat_report = WORK / f"increment_guard_materialize_{stamp}.json"
    mat = run(
        [
            sys.executable,
            "-u",
            "staging_materialize_full_history.py",
            "--pattern",
            "tail_fill/*",
            "--report",
            str(mat_report),
        ]
    )
    report["materialize_rc"] = mat.returncode
    print(mat.stdout[-1500:] if mat.stdout else "", end="")
    try:
        mr = json.loads(mat_report.read_text(encoding="utf-8"))
        stats = mr.get("stats", {})
        report["materialize_stats"] = stats
    except Exception as exc:
        stats, report["materialize_error"] = {}, str(exc)

    if (
        stats.get("mismatch")
        or stats.get("missing")
        or stats.get("conflict_preserved")
        or mat.returncode != 0
    ):
        report["status"] = "DEGRADED"
        print(
            "[degraded] 发布异常（mismatch/missing/conflict），"
            "manifest/git 不动，留人工处置"
        )
        _write(report, stamp)
        return 3

    # 4. manifest：新合格单元按表聚合（ok 单元即全部 passed——增量只填空）
    mp = PROD / "_manifest.json"
    man = json.loads(mp.read_text(encoding="utf-8"))
    per_tbl = {}
    for u in passed:
        a = per_tbl.setdefault(u["table"], {"rows": 0, "dates": []})
        a["rows"] += u["row_count"]
        a["dates"].append(u["date"])
    for t, a in per_tbl.items():
        key = t[3:]
        ent = man.setdefault(key, {"done": True})
        ent["done"] = True
        ent["rows"] = int(ent.get("rows") or 0) + a["rows"]
        ent["file"] = f"d_{max(a['dates'])}.parquet"
        notes = [n for n in (ent.get("note") or "").split("；") if CHANNEL_TAG not in n]
        notes.append(
            f"{CHANNEL_TAG} {dt.date.today().isoformat()} "
            f"+{len(a['dates'])} 单元 +{a['rows']} 行"
        )
        ent["note"] = "；".join(notes[-12:])  # 滚动保留（含固定历史段）
        print(
            f"[manifest] {key}: +{len(a['dates'])} 单元 +{a['rows']} 行 "
            f"→ file={ent['file']} rows={ent['rows']}"
        )
    mp.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")

    # 5. 账本留痕（K-ORG-20261107-S3 裁决 2026-11-07：manifest 不入库）
    _record_manifest(report, mp, per_tbl)

    report["status"] = "OK"
    print(f"[done] status=OK passed={len(passed)} held={len(held)}")
    _write(report, stamp)
    return 0


def _write(report, stamp):
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / f"increment_guard_{stamp}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[report] work/increment_guard_{stamp}.json")


if __name__ == "__main__":
    sys.exit(main())
