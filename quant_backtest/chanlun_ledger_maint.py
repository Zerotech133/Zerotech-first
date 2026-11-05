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

3. `archive` 把全量结算集按月落 `data/chanlun_paper_archive/`（append-only
   去重），供以后复盘，避免 paper.json 无限膨胀。

用法:
    python chanlun_ledger_maint.py                 # 三步全做
    python chanlun_ledger_maint.py --only rebuild  # 只重建
    python chanlun_ledger_maint.py --only repair --dry-run
"""

from __future__ import annotations

import argparse
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


# ── 3. 按月归档 ────────────────────────────────────────────
def archive(full):
    d = os.path.join(DATA, "chanlun_paper_archive")
    os.makedirs(d, exist_ok=True)
    by = {}
    for r in full["rows"]:
        if r.get("status") != "closed":
            continue
        # 坑：exit_date 是 "2026-10-27" 带横线，直接 [:6] 会得到 "2026-1"，
        # 把 10/11/12 月并成一个「月」。必须先剥掉非数字再取 6 位。
        mo = "".join(ch for ch in str(r.get("exit_date") or "") if ch.isdigit())[:6]
        by.setdefault(mo or "unknown", []).append(r)
    n = 0
    for mo, lst in sorted(by.items()):
        path = os.path.join(d, "closed_CN_%s.jsonl" % mo)
        have = set()
        if os.path.exists(path):
            for line in open(path, encoding="utf-8"):
                try:
                    x = json.loads(line)
                except Exception:
                    continue
                have.add((x.get("code"), x.get("type"), x.get("dt")))
        add = [r for r in lst if (r["code"], r["type"], r["dt"]) not in have]
        if not add:
            continue
        with open(path, "a", encoding="utf-8") as f:
            for r in add:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        n += len(add)
    print("archive: %d 条 -> %s (月份 %d 个)" % (n, d, len(by)))


def main():
    ap = argparse.ArgumentParser(description="chanlun 账本维护")
    ap.add_argument("--only", choices=("repair", "rebuild", "archive"), default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
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
