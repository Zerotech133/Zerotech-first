#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 catalog.json 生成「物理表层」登记（product_registry_catalog.yaml）。

为什么不直接写进 product_registry.yaml（2026-11-12 决定）：
  手写那 6 条是**契约**——谁写、什么节奏、成功怎么算，是人确认过的。
  259 张表里绝大多数没人认领节奏，只能从 catalog 的 freq 推。
  两类混在一张表里，读的人分不清「这是承诺」还是「这是推断」。
  所以分两个文件：product_registry.yaml（契约）/ product_registry_catalog.yaml（推断，带 derived 标记）。

字段来源与**不确定性**（必须写在生成物里，不能让人以为都是实测）：
  compose.path      layout 决定（by_date→d_*.parquet，by_code→*.parquet，part→part_*.parquet …）
  date_from         by_date 用文件名；其余用 catalog 的 date_col 列
  promise.cadence   catalog.freq → daily/weekly/monthly/quarterly/yearly
  promise.lag_ok    取 sources.yaml 的 freshness_sla_days（D:3 W:10 M:35 Q:150），
                    单位是**自然日**，所以这类产物的 lag 也按自然日算（basis: calendar）
  readers           全仓文本扫描命中的文件（粗筛，只说明「有人提到」，不等于「真读」）
  orphan            一个 reader 都没有 → 可能是死表，值得人看

不生成 / 标记 unsupported 的：
  sqlite / pg  需要 SQL 查询才能取日期，本脚本不做，标 unsupported 而不是假装通过
  jsonl        没有列语义，标 unsupported
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent                      # 仓根
CATALOG = ROOT / "quant_backtest" / "data" / "catalog.json"
SOURCES = ROOT / "tools" / "data_hub" / "sources.yaml"
OUT = HERE / "product_registry_catalog.yaml"

# 扫描口径：只扫代码，不扫数据与第三方。命中判定用「表名作为独立词」，
# 避免 daily 命中 daily_basic 这种子串误报。
SCAN_EXT = (".py", ".ps1", ".psm1")
SKIP_DIR = {".git", ".worktrees", "__pycache__", "node_modules", "data", "outbox",
            ".venv", "venv", "site-packages", ".mypy_cache", ".pytest_cache"}

# 主仓是公开的，被 .gitignore 排除的子树（私有仓、本机数据）的**仓名与目录结构**
# 不能出现在这里。但也不能悄悄丢掉——丢掉的引用是真实影响面。
# 判据不用「记住哪个目录是私有」，而是**问 git 自己**：不在 ls-files 里的就不是本仓内容。
# 这样既不会漏掉将来新增的私有子树，也不用把任何目录名写进公开仓。

FREQ2CADE = {"D": "daily", "W": "weekly", "M": "monthly", "Q": "quarterly",
             "Y": "yearly", "A": "yearly", "1min": "daily"}

# date_col 有 28 种，但它们不是同一种东西。第一版全当成「观测日」，
# 结果 16 条判成「取不到日期」、5 条判成「超前」——全是分类错，不是数据错。
#   observation  观测日：一条数据对应「哪一天发生的」，可以算 lag
#   period       期次：月度/季度/年度，要按**期数**算 lag，按交易日算必错
#   schedule     计划端点：list_date / end_date 之类本身就可能在未来，不判新鲜度
#   horizon      覆盖未来的日历：交易日历必然含未来日期，判新鲜度等于判它错
PERIOD_COLS = ("month", "MONTH", "quarter", "year", "年", "年份")
SCHED_COLS = ("list_date", "in_date", "ipo_date", "surv_date")


# 承诺容差必须跟着「修正后的节奏」走。表名说月频、catalog 说日频时，
# 若容差仍取日频的 3 天，就会出现「每月 承诺落后 ≤3 个交易日」这种自相矛盾的行。
CAD2FREQ = {"daily": "D", "weekly": "W", "monthly": "M", "quarterly": "Q",
            "semiannual": "Q", "yearly": "A"}


def cadence_of(table_id: str, freq: str) -> tuple[str, str]:
    """返回 (cadence, 说明)。两条覆盖规则，都是「catalog 的 freq 与事实不符」：

    ① 港股财报只有半年报/年报（联交所不要求季报）。catalog 一律记 Q，
       于是 202606 的港股中报在 11 月被判「落后 2 季」——每天误报。
    ② 表名自带频率的（ts_index_monthly / ts_index_weekly / *_yearly），
       表名是证据，catalog 的 D 是抓取频率不是发布频率。
       两处冲突时按表名，并留下说明，不静默改。
    """
    cad = FREQ2CADE.get(str(freq).upper(), "daily")
    note = ""
    if table_id.startswith("hk_") and cad == "quarterly":
        return "semiannual", "港股仅半年报/年报，按半年频判定（catalog freq=Q 不适用）"
    for suf, c2 in (("_monthly", "monthly"), ("_weekly", "weekly"),
                    ("_yearly", "yearly"), ("_annual", "yearly")):
        if table_id.endswith(suf) and cad != c2:
            note = "表名示 %s，catalog freq=%s 与之不符，按表名判定" % (c2, freq)
            return c2, note
    return cad, note


def role_of(date_col: str, freq: str) -> str:
    """日期语义不能只看列名，还要看节奏。

    end_date 是歧义词：18 张财报里它是**报告期末**（季度期次，20260930 就是三季报），
    而 stk_pledge / ts_cb_share 里它是**质押/转股截止日**（本身在未来）。
    只看列名会把 18 张财报判成「不判定」，等于放过「三季报还没入库」这种真问题。
    """
    if date_col == "cal_date":
        return "horizon"
    if date_col in PERIOD_COLS:
        return "period"
    if date_col == "end_date":
        return "period" if str(freq).upper() in ("Q", "M", "Y", "A") else "schedule"
    if date_col in SCHED_COLS:
        return "schedule"
    return "observation"

# 不是表：隔离区目录里放的是「被挡下来的写入」，本身没有日期语义
SKIP_TABLE = {"quarantine_prod_overwrite": "隔离区目录（存放被挡下的写入），不是数据表"}

# layout → (glob 模板, date_from 形式)
LAYOUT = {
    "by_date":        ("{store}/d_*.parquet", "filename"),
    "by_code":        ("{store}/*.parquet", "column"),
    "by_code_month":  ("{store}/*.parquet", "column"),
    "by_period":      ("{store}/p_*.parquet", "column"),
    "part":           ("{store}/part_*.parquet", "column"),
    "single":         ("{store}/part_*.parquet", "column"),
    "parquet_snapshot": ("{store}/latest.parquet", "column"),
    "parquet":        ("{store}/*.parquet", "column"),
}
UNSUPPORTED = {"sqlite": "需 SQL 查询取日期，本生成器不猜",
               "pg": "远端库，需连接才能取日期",
               "jsonl": "无列语义，日期在 JSON 字段里"}


def rel(p: str) -> str:
    """把绝对路径转成相对仓根的形式，入库不外泄本机目录。"""
    try:
        r = os.path.relpath(p, str(ROOT))
    except ValueError:
        return p
    return r.replace("\\", "/") if not r.startswith("..") else ""


def load_sla() -> dict:
    try:
        d = yaml.safe_load(io.open(SOURCES, encoding="utf-8")) or {}
        return d.get("freshness_sla_days") or {}
    except Exception:
        return {}


def build_index() -> dict[str, set[str]]:
    """一次扫描建「标识符 → 文件集合」倒排表。

    为什么不按表名逐个正则搜：实测单次 find_readers 约 1.0s（10225 个文件、
    1.08 亿字符），259 张表就是 4 分半，直接把 nightly 任务拖死。
    改成一次分词建倒排，总耗时掉到十几秒，结果等价。
    """
    tok = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
    inv: dict[str, set[str]] = {}
    n = 0
    for base, dirs, files in os.walk(str(ROOT)):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR]
        for f in files:
            if not f.endswith(SCAN_EXT):
                continue
            p = os.path.join(base, f)
            try:
                if os.path.getsize(p) > 2_000_000:
                    continue
                txt = io.open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            relp = os.path.relpath(p, str(ROOT)).replace("\\", "/")
            n += 1
            for w in set(tok.findall(txt)):
                inv.setdefault(w, set()).add(relp)
    print("倒排表：%d 个文件、%d 个标识符" % (n, len(inv)))
    return inv


def tracked_files() -> set[str] | None:
    """本仓内容的文件集合：已入库 + 未入库但未被忽略。

    只用 --cached 会让「刚写完还没提交的文件」不算读者，同一份代码提交前后结论不同；
    加上 --others --exclude-standard 后，判据变成「是否属于本仓工作区」，
    与提交状态无关，而被 .gitignore 排除的子树仍然自动挡在外面。
    """
    try:
        import subprocess
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=str(ROOT), capture_output=True, timeout=60)
        if out.returncode != 0:
            return None
        return {x.replace("\\", "/") for x in out.stdout.decode("utf-8", "replace").split("\0") if x}
    except Exception:
        return None


def split_readers(name: str, inv: dict[str, set[str]],
                  tracked: set[str] | None) -> tuple[list[str], int]:
    """返回 (可公开的读者路径, 仓外引用数)。

    仓外的路径一律不落盘，只留计数 —— 见 PRIVATE_PREFIX 处的注释。
    """
    hits = sorted(inv.get(name) or ())
    if tracked is None:
        return hits, 0
    pub = [h for h in hits if h in tracked]
    return pub, len(hits) - len(pub)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--limit", type=int, default=0, help="只生成前 N 条，调试用")
    a = ap.parse_args()

    cat = json.load(io.open(CATALOG, encoding="utf-8"))
    tables = cat["tables"]
    sla = load_sla()
    idx = build_index()
    tracked = tracked_files()
    if tracked is None:
        print("警告：git ls-files 取不到，读者列将不过滤（可能带入仓外路径，勿入库）")
    print("catalog %d 张表" % len(tables))

    prods, skipped = [], []
    for name in sorted(tables):
        v = tables[name]
        if a.limit and len(prods) >= a.limit:
            break
        layout = str(v.get("layout") or "")
        store = str(v.get("store") or "")
        date_col = str(v.get("date_col") or "")
        freq = str(v.get("freq") or "D")
        desc = str(v.get("desc") or "")

        if layout in UNSUPPORTED:
            skipped.append({"id": name, "layout": layout, "why": UNSUPPORTED[layout]})
            continue
        if name in SKIP_TABLE:
            skipped.append({"id": name, "layout": layout, "why": SKIP_TABLE[name]})
            continue
        if layout not in LAYOUT:
            skipped.append({"id": name, "layout": layout, "why": "未知 layout"})
            continue
        if v.get("static_no_date"):
            skipped.append({"id": name, "layout": layout, "why": "静态维度表，无日期列"})
            continue
        if not date_col:
            skipped.append({"id": name, "layout": layout, "why": "catalog 无 date_col"})
            continue

        tmpl, mode = LAYOUT[layout]
        # store 是绝对路径时转相对；pg:// 之类已在上面被 UNSUPPORTED 拦掉
        sp = rel(store)
        if not sp:
            skipped.append({"id": name, "layout": layout, "why": "store 不在仓内，路径无法相对化"})
            continue
        if not os.path.isdir(store):
            skipped.append({"id": name, "layout": layout, "why": "store 目录不存在"})
            continue

        path = tmpl.format(store=sp)
        date_from = "filename" if mode == "filename" else "column:" + date_col
        readers, n_priv = split_readers(name, idx, tracked)
        cad0, cad_note0 = cadence_of(name, freq)
        lag_ok = int(sla.get(CAD2FREQ.get(cad0, "D")) or 3)
        role = role_of(date_col, freq)

        # 成功口径按日期语义分写。四类混用一句话，读的人会拿日频标准去卡年度数据。
        if role == "period":
            success = ("最新%s不早于当前%s往前 %d 个自然日（口径来自 sources.yaml freshness_sla_days）"
                       % (date_col, {"month": "月", "MONTH": "月", "quarter": "季",
                                     "year": "年", "年": "年", "年份": "年"}.get(date_col, "期"),
                          lag_ok))
        elif role in ("schedule", "horizon"):
            success = ("%s 是计划/覆盖端点，本身允许落在未来，不作新鲜度判定；"
                       "只断言「有数据」，落后与否需按业务口径另判" % date_col)
        else:
            success = ("max(%s) 不早于今天-%d 个自然日（口径来自 sources.yaml freshness_sla_days）"
                       % (date_col, lag_ok))

        cad, cad_note = cad0, cad_note0
        prods.append({
            "id": name,
            "name": (desc or name)[:40],
            "derived": True,
            "purpose": desc or "（catalog 无描述）",
            "writer": "未认领（节奏由 freq=%s 推断，未经写方确认）%s"
                      % (freq, ("；" + cad_note) if cad_note else ""),
            "readers": readers[:12],
            "reader_count": len(readers) + n_priv,
            "readers_private": n_priv,
            "orphan": (len(readers) + n_priv) == 0,
            "compose": [{"path": path, "date_from": date_from,
                         "date_role": role,
                         "role": "catalog layout=%s，schema=%s"
                                 % (layout, ",".join(map(str, (v.get("schema") or [])[:5])))}],
            "merge": "max",
            "promise": {"cadence": cad, "lag_ok": lag_ok, "basis": "calendar"},
            "success": success,
            "catalog_freshness": str(v.get("freshness") or ""),
            "rows": v.get("rows"),
            "deprecated": bool(v.get("deprecated")),
        })

    orphans = [p["id"] for p in prods if p["orphan"]]
    doc = {
        "version": 1,
        "updated": None,
        "generated_by": "portal/daily_report/gen_registry_catalog.py",
        "source": "quant_backtest/data/catalog.json",
        "note": ("本文件由脚本生成，不要手改。里面的 writer / promise 是从 catalog.freq "
                 "推断的，未经写方确认，标记 derived: true；契约层见 product_registry.yaml。"),
        "clock": "quote_daily",
        "products": prods,
        "skipped": skipped,
    }
    # updated 用运行时日期，写在文件里便于判断新鲜度
    import datetime
    doc["updated"] = datetime.date.today().strftime("%Y-%m-%d")

    io.open(a.out, "w", encoding="utf-8").write(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=1000))

    print("生成 %d 条 → %s" % (len(prods), os.path.basename(a.out)))
    print("跳过 %d 条（%s）" % (len(skipped), "、".join(sorted({s["why"][:12] for s in skipped}))))
    print("无人引用（orphan）%d 条：%s%s"
          % (len(orphans), ", ".join(orphans[:12]), " …" if len(orphans) > 12 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
