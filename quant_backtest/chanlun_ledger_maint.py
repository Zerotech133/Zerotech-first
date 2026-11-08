# -*- coding: utf-8 -*-
"""账本维护：字段修复 · 全量重建 · 月度归档（P1/P2 工程口径）。

三个独立动作，默认全做（`--only` 可单选），全部**不改任何盈亏数值**：

1. `repair`  就地修 `chanlun_paper.json`（写前自动备份到
   `data/chanlun_paper.json.bak-<ts>`）：
     · exit_reason 为 None 的记录 → 按 days_held 推断（==HOLD → hold20，
       <HOLD → reverse，>HOLD → 标注 unknown_legacy 不猜）；
     · 补 `ret_na` 标记（旧版把不可算写成 0.0，这些记录已无法区分，保留
       原值并在 `ret_na_legacy` 里注明「0 值存疑」，不静默改写收益）；
     · 补 `alpha/beta/idx_ret`（等权全A基准），已存在则跳过。

2. `rebuild` 从 `chanlun_pool.jsonl` + parquet 重建**全量**结算集
   → `data/chanlun_paper_full.json`。
   动机：`CLOSED_CAP=5000` 把更早的结算记录截掉了，导致所有胜率/均值永远
   只反映最近一段（审计时池内 11,441 行、账本只剩 5,000 笔）。重建不动
   产线账本，仅提供全样本统计。

3. `archive` 把全量结算集按月落 `data/chanlun_paper_backfill/`（append-only
   去重），供以后复盘，避免 paper.json 无限膨胀。

4. `split` 一次性清理：把历史上误写进 `data/chanlun_paper_archive/` 的回填行
   搬到 `data/chanlun_paper_backfill/`（见下）。

**两个归档目录的归属（2026-11-08 拆分，勿再混用）**

| 目录 | 唯一写入者 | 内容 | 判据 |
|---|---|---|---|
| `data/chanlun_paper_archive/` | `chanlun_scan._archive_closed()` | 台账 `CLOSED_CAP=5000` 溢出，**产线实际入场** | 有 `sig_dt`/`entry_px` |
| `data/chanlun_paper_backfill/` | 本文件的 `archive()` | 池+parquet 重算的**回填结算集** | 有 `dt`/`entry_i`/`source=backfill` |

拆之前两者共用 `chanlun_paper_archive/closed_CN_<YYYYMM>.jsonl` 同一个文件名，
于是混成 9,938 行（9,745 回填 + 193 台账）。任何整读该目录的代码都会把"如果
池里每条信号都入场 20 日"的模拟当成产线实际入场，台账被虚增 1,678→2,560。
文件名相同、去重键还不同（`(code,type,sig_dt)` vs `(code,type,dt)`），无解，
只能拆目录。

用法:
    python chanlun_ledger_maint.py                 # 四步全做
    python chanlun_ledger_maint.py --only rebuild  # 只重建
    python chanlun_ledger_maint.py --only repair --dry-run
    python chanlun_ledger_maint.py --only split --dry-run   # 看要搬多少行
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DATA = os.path.join(HERE, "data")
PAPER = os.path.join(DATA, "chanlun_paper.json")
POOL = os.path.join(DATA, "chanlun_pool.jsonl")
FULL = os.path.join(DATA, "chanlun_paper_full.json")
# 台账溢出的归档目录，写入者是 chanlun_scan._archive_closed，本文件**只读不写**。
LEDGER_ARCHIVE = os.path.join(DATA, "chanlun_paper_archive")
# 回填结算集的归档目录，写入者只有本文件的 archive()。
BACKFILL_DIR = os.path.join(DATA, "chanlun_paper_backfill")
HOLD = 20
TYPES = ("1买", "2买", "3买", "1卖", "2卖", "3卖")


def _load(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _blk(vals):
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if not n:
        return None
    mu = sum(vals) / n
    var = sum((v - mu) ** 2 for v in vals) / (n - 1) if n > 1 else 0.0
    t = round(mu / (var ** 0.5 / (n ** 0.5)), 2) if var > 0 else None
    s = sorted(vals)
    return {
        "n": n,
        "win": round(sum(1 for v in vals if v > 0) * 100.0 / n, 1),
        "avg": round(mu * 100, 2),
        "med": round((s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2) * 100, 2),
        "t": t,
    }


# ── 1. 就地修复 ────────────────────────────────────────────
def repair(dry_run=False):
    import chanlun_alpha as ca

    led = _load(PAPER, {"open": [], "closed": []})
    n_reason = n_alpha = n_na = n_fail = 0
    for pos in led.get("closed", []):
        if not pos.get("exit_reason"):
            dh = pos.get("days_held")
            if dh == HOLD:
                pos["exit_reason"] = "hold20"
                n_reason += 1
            elif isinstance(dh, int) and 0 < dh < HOLD:
                pos["exit_reason"] = "reverse"
                n_reason += 1
            elif isinstance(dh, int) and dh > HOLD:
                pos["exit_reason"] = "unknown_legacy"
                n_reason += 1
        if pos.get("ret") is None:
            if not pos.get("ret_na"):
                pos["ret_na"] = True
                n_na += 1
        elif pos.get("alpha") is None and pos.get("exit_date") and pos.get("entry_date"):
            try:
                pos.update(
                    ca.annotate(pos["code"], pos["ret"], pos["entry_date"],
                                pos["exit_date"], pos["entry_date"])
                )
                n_alpha += 1
            except Exception:
                n_fail += 1
    led["repaired_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    led["repair_note"] = "exit_reason/ret_na/alpha 补齐；盈亏数值未改动"
    print("repair: exit_reason +%d, alpha +%d, ret_na +%d, alpha_fail %d (closed=%d)"
          % (n_reason, n_alpha, n_na, n_fail, len(led.get("closed", []))))
    if dry_run:
        print("  --dry-run: 未写盘")
        return led
    bak = PAPER + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(PAPER, bak)
    print("  backup -> %s" % bak)
    _dump(PAPER, led)
    return led


# ── 2. 全量重建 ────────────────────────────────────────────
def rebuild():
    """池内**所有**行按产线规则重放结算，绕过 CLOSED_CAP 截留。"""
    import chanlun_ext_backfill as eb
    import chanlun_scan as cs

    pool = [json.loads(li) for li in open(POOL, encoding="utf-8") if li.strip()]
    pool = [r for r in pool if r.get("market", "CN") == "CN"]
    bars_cache, rows, miss = {}, [], 0
    for r in pool:
        code = r["code"]
        if code not in bars_cache:
            bars_cache[code] = cs._bars_from_parquet(
                os.path.join(cs.ASHARE, code.replace(".", "_") + ".parquet")
            ) or []
        bars = bars_cache[code]
        idx = {b["dt"]: i for i, b in enumerate(bars)}
        ei = idx.get(r["detect_date"])
        if ei is None:
            miss += 1
            continue
        rows.append({
            "code": code, "name": r.get("name", ""), "type": r["type"], "dt": r["dt"],
            "lag": r.get("lag"), "detect_date": r["detect_date"],
            "entry_i": ei, "entry_px": r.get("entry_px") or bars[ei]["c"],
            "_bars_len": len(bars),
            "source": r.get("source"),
            **{k: r.get(k) for k in ("mbei", "bei", "bei2", "vv", "tbe", "reg",
                                     "conf", "div_area", "div_dif", "npv")},
        })
    print("rebuild: pool=%d mapped=%d unmapped=%d" % (len(pool), len(rows), miss))
    rows = eb.settle(rows, HOLD)
    rows = eb.finalize(rows, HOLD)
    closed = [r for r in rows if r.get("status") == "closed"]
    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "hold_days": HOLD,
        "n_pool": len(pool),
        "n_rows": len(rows),
        "n_closed": len(closed),
        "n_unmapped": miss,
        "note": "从池全量重建，绕过 CLOSED_CAP 截留；入场=检出日收盘，"
                "出场=反向信号确认日收盘或满20日收盘",
        "rows": rows,
    }
    _dump(FULL, out)
    print("  -> %s (closed=%d)" % (FULL, len(closed)))
    return out


def compare(full):
    """截留账本 vs 全量重建：量化 CLOSED_CAP 造成的统计偏差。"""
    led = _load(PAPER, {"closed": []})
    old = [p for p in led.get("closed", []) if p.get("ret") is not None]
    new = [r for r in full["rows"] if r.get("status") == "closed" and r.get("ret") is not None]
    print("\n截留账本 n=%d (%s~%s)  ←→  全量重建 n=%d (%s~%s)"
          % (len(old),
             min((p.get("entry_date") or "") for p in old) if old else "-",
             max((p.get("entry_date") or "") for p in old) if old else "-",
             len(new),
             min(r["entry_date"] for r in new) if new else "-",
             max(r["entry_date"] for r in new) if new else "-"))
    print("%-4s %-22s %-22s" % ("type", "截留账本 raw avg/n", "全量重建 raw avg/n"))
    for t in TYPES:
        a = _blk([p["ret"] for p in old if p.get("type") == t])
        b = _blk([r["ret"] for r in new if r["type"] == t])
        print("%-4s %-22s %-22s"
              % (t, "%+.2f%% / %d" % (a["avg"], a["n"]) if a else "-",
                 "%+.2f%% / %d" % (b["avg"], b["n"]) if b else "-"))
    a, b = _blk([p["ret"] for p in old]), _blk([r["ret"] for r in new])
    print("%-4s %-22s %-22s" % ("ALL",
                                "%+.2f%% / %d" % (a["avg"], a["n"]) if a else "-",
                                "%+.2f%% / %d" % (b["avg"], b["n"]) if b else "-"))


# ── 3. 按月归档（回填结算集，独占 BACKFILL_DIR）───────────────
def _month_key(r, fields=("exit_date", "entry_date")):
    """→ 'YYYYMM'。坑：exit_date 是 "2026-10-27" 带横线，直接 [:6] 得 "2026-1"，
    会把 10/11/12 月并成一个「月」。必须先剥掉非数字再取 6 位。"""
    v = ""
    for k in fields:
        v = r.get(k)
        if v:
            break
    return "".join(ch for ch in str(v or "") if ch.isdigit())[:6] or "unknown"


def is_backfill_row(x):
    """回填重算集判据：带池字段 `dt`/`entry_i`，或显式 `source=backfill`，
    或压根没有台账才有的 `sig_dt`。三个条件任一命中即算回填。"""
    return ("dt" in x) or (x.get("source") == "backfill") or ("sig_dt" not in x)


def _append_dedup(path, rows, keyf):
    """append-only 去重写入，返回本次新增条数。"""
    have = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    x = json.loads(line)
                except Exception:
                    continue
                have.add(keyf(x))
    add = [r for r in rows if keyf(r) not in have]
    if not add:
        return 0
    with open(path, "a", encoding="utf-8") as f:
        for r in add:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            have.add(keyf(r))
    return len(add)


def archive(full):
    """把全量重建的回填结算集按月落 `data/chanlun_paper_backfill/`。

    **不要写回 `data/chanlun_paper_archive/`**：那是台账溢出的目录，两个语义
    混在一个文件里会让所有读方把模拟当真实入场（2026-11-08 已踩过）。
    """
    os.makedirs(BACKFILL_DIR, exist_ok=True)
    by = {}
    for r in full["rows"]:
        if r.get("status") != "closed":
            continue
        by.setdefault(_month_key(r), []).append(r)
    n = 0
    for mo, lst in sorted(by.items()):
        path = os.path.join(BACKFILL_DIR, "backfill_CN_%s.jsonl" % mo)
        n += _append_dedup(path, lst, lambda x: (x.get("code"), x.get("type"), x.get("dt")))
    print("archive: %d 条 -> %s (月份 %d 个)" % (n, BACKFILL_DIR, len(by)))


# ── 4. 拆目录（一次性清理，幂等）───────────────────────────
def split(dry_run=False):
    """把误写进 `chanlun_paper_archive/` 的回填行搬到 `chanlun_paper_backfill/`。

    历史成因：`chanlun_scan._archive_closed()`（台账溢出）与本文件 `archive()`
    （回填结算集）往同一目录、同一文件名 `closed_CN_<YYYYMM>.jsonl` 里 append。
    2026-11-08 实测该目录 9,938 行中 9,745 行是回填、仅 193 行是台账。

    做法：逐文件分类 → 原文件仅保留台账行（原子重写）→ 回填行按 exit 月追加到
    新目录（按 `(code,type,dt)` 去重）。原始文件先备份到
    `data/_bak_archive_split_<ts>/`，不删任何字节。幂等：再跑一次搬 0 行。
    """
    if not os.path.isdir(LEDGER_ARCHIVE):
        print("split: 无 %s，跳过" % LEDGER_ARCHIVE)
        return
    files = sorted(glob.glob(os.path.join(LEDGER_ARCHIVE, "closed_*.jsonl")))
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = os.path.join(DATA, "_bak_archive_split_%s" % ts)
    # 坑（2026-11-08 修）：不要在这里 makedirs。幂等重跑时一行都不搬，
    # 却已经把备份目录建出来了，留下 0 字节的空目录（实测留过一个
    # `_bak_archive_split_20261108-091946`）。改到"确有回填行要搬"时再建。

    tot_led = tot_bf = tot_bad = tot_moved = tot_dup = 0
    for p in files:
        keep, move, bad = [], [], []
        with open(p, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    x = json.loads(s)
                except ValueError:
                    bad.append(s)
                    continue
                (move if is_backfill_row(x) else keep).append((s, x))
        tot_led += len(keep)
        tot_bf += len(move)
        tot_bad += len(bad)
        if not move:
            continue
        # 回填行按月落新目录
        by = {}
        for s, x in move:
            by.setdefault(_month_key(x), []).append(s)
        if dry_run:
            for mo, lst in sorted(by.items()):
                print("  [dry] %-22s -> backfill_CN_%s.jsonl  %d 行"
                      % (os.path.basename(p), mo, len(lst)))
            continue
        # 到这里才确定要搬东西，此时才建目录（见上方"不要 makedirs"的说明）
        os.makedirs(bak, exist_ok=True)
        os.makedirs(BACKFILL_DIR, exist_ok=True)
        shutil.copy2(p, os.path.join(bak, os.path.basename(p)))
        for mo, lst in sorted(by.items()):
            tgt = os.path.join(BACKFILL_DIR, "backfill_CN_%s.jsonl" % mo)
            have = set()
            if os.path.exists(tgt):
                with open(tgt, encoding="utf-8") as f:
                    for line in f:
                        try:
                            y = json.loads(line)
                        except Exception:
                            continue
                        have.add((y.get("code"), y.get("type"), y.get("dt")))
            with open(tgt, "a", encoding="utf-8") as f:
                for s in lst:
                    y = json.loads(s)
                    k = (y.get("code"), y.get("type"), y.get("dt"))
                    if k in have:
                        tot_dup += 1
                        continue
                    have.add(k)
                    f.write(s + "\n")
                    tot_moved += 1
        # 原子重写：原文件只剩台账行（空文件也保留，后续 append 才有去重基线）
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for s, _ in keep:
                f.write(s + "\n")
            for s in bad:
                f.write(s + "\n")
        os.replace(tmp, p)

    print("split%s: 台账行 %d 保留 / 回填行 %d 搬走（新增 %d，重复跳过 %d）/ 坏行 %d"
          % ("[dry]" if dry_run else "", tot_led, tot_bf, tot_moved, tot_dup, tot_bad))
    if not dry_run and tot_bf:
        print("  备份 -> %s" % bak)


def main():
    ap = argparse.ArgumentParser(description="chanlun 账本维护")
    ap.add_argument("--only", choices=("repair", "rebuild", "archive", "split"), default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.only == "split":
        split(dry_run=a.dry_run)
        return
    full = None
    if a.only in (None, "rebuild", "archive"):
        full = rebuild()
        if not a.dry_run:
            compare(full)
    if a.only in (None, "repair"):
        repair(dry_run=a.dry_run)
    if a.only in (None, "archive") and full:
        if a.dry_run:
            print("  --dry-run: 跳过归档")
        else:
            archive(full)


if __name__ == "__main__":
    main()
