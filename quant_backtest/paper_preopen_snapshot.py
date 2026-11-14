# -*- coding: utf-8 -*-
"""open 前账户快照（证据留痕，不参与任何判定）。

为什么需要：
  `paper_trade.py:564-566` 的 open 结清后只保留「未成交残留」挂单，
  已成交与"被静默删除"两种情形事后不可区分 —— 判据③ 的卖单完备性
  因此存在天然盲区。唯一救法是在 open 之前留一份不可变快照。

纪律：
  · 只读生产账户，只写 C:\\Temp，绝不改 account.json；
  · **首写锁定**：同日已有快照则拒绝覆盖（重跑会把"open 前"刷成
    "open 后"，等于销毁证据）；需要重拍必须显式 --force；
  · 退出码：0 = 已存在或已写入；1 = 硬错误。调用方一律不得据此
    影响交易 rc（证据采集失败不该阻断交易）。
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys

DEFAULT_PAPER = r"C:\aios\quant_backtest\data\paper"
DEFAULT_OUT = r"C:\Temp"


def sha256_of(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            if limit is not None:
                limit -= len(chunk)
                if limit <= 0:
                    break
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=datetime.date.today().strftime("%Y%m%d"),
                    help="快照归属交易日 YYYYMMDD，默认今天")
    ap.add_argument("--paper-dir", default=DEFAULT_PAPER)
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true",
                    help="已存在时强制重拍（会销毁原证据，默认禁止）")
    args = ap.parse_args(argv)

    day = str(args.day)
    try:
        os.makedirs(args.out_dir, exist_ok=True)
    except OSError as e:
        print("[preopen] ERR cannot create out-dir (%s): %s"
              % (type(e).__name__, args.out_dir))
        return 1
    acc_p = os.path.join(args.paper_dir, "account.json")
    trd_p = os.path.join(args.paper_dir, "trades.jsonl")
    if not os.path.isfile(acc_p):
        print("[preopen] ERR account.json not found: %s" % acc_p)
        return 1

    out_p = os.path.join(args.out_dir, "paper_preopen_" + day + ".json")
    if os.path.isfile(out_p) and not args.force:
        print("[preopen] SKIP already exists (first-write lock, refuse overwrite)")
        print("[preopen]   path=%s" % out_p)
        try:
            old = json.load(open(out_p, encoding="utf-8"))
            print("[preopen]   taken_at=%s sha=%s"
                  % (old.get("taken_at"), str(old.get("account_sha256"))[:16]))
        except Exception as e:                      # noqa: BLE001
            print("[preopen]   unparsable: %s" % type(e).__name__)
        return 0

    acc = json.load(open(acc_p, encoding="utf-8"))
    pb = acc.get("pending_buys") or []
    ps = acc.get("pending_sells") or []
    pos = acc.get("positions") or {}
    meta = acc.get("pending_buys_meta") or {}

    snap = {
        "taken_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "day": day,
        "purpose": "stage_a_criterion3_completeness",
        "account_path": acc_p,
        "account_sha256": sha256_of(acc_p),
        "account_mtime": datetime.datetime.fromtimestamp(
            os.path.getmtime(acc_p)).isoformat(timespec="seconds"),
        "trades_path": trd_p if os.path.isfile(trd_p) else None,
        "trades_sha256": sha256_of(trd_p) if os.path.isfile(trd_p) else None,
        "trades_bytes": os.path.getsize(trd_p) if os.path.isfile(trd_p) else 0,
        "counts": {"positions": len(pos), "pending_buys": len(pb),
                   "pending_sells": len(ps)},
        # account["rules"] 存的是**建账时的参数整块**（version 可能仍是 v1），
        # 与 paper_trade.py 里当前 RULES 版本不是一回事，两个都记，别混称。
        "account_rules_version": (acc.get("rules") or {}).get("version")
                                 if isinstance(acc.get("rules"), dict)
                                 else acc.get("rules"),
        "account_rules_blob": acc.get("rules"),
        "account_rules_markers": {k: acc.get(k) for k in
                                  ("rules_v2_from", "rules_v2_1_from",
                                   "catchup_v2_1", "catchup_v2_3_orders")
                                  if acc.get(k)},
        "cash": acc.get("cash"),
        "pending_sells": ps,
        "pending_sells_codes": [s.get("code") for s in ps],
        "pending_buys_codes": [b.get("code") for b in pb],
        "pending_buys_basis": {b.get("code"): b.get("list_basis_date")
                               for b in pb},
        "positions": {c: {"shares": p.get("shares"),
                          "avg_cost": p.get("avg_cost"),
                          "opened": p.get("opened"),
                          "cost_adj": p.get("cost_adj")}
                      for c, p in pos.items()},
        "pending_buys_meta": {"basis_date": meta.get("basis_date"),
                              "basis_source": meta.get("basis_source"),
                              "generated_at": meta.get("generated_at")},
    }

    tmp = out_p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    os.replace(tmp, out_p)

    # 顺手留一份原样副本：即使 account.json 事后被改，也能字节级比对
    raw_copy = os.path.join(args.out_dir, "paper_preopen_" + day + "_account.json")
    if not os.path.isfile(raw_copy) or args.force:
        shutil.copy2(acc_p, raw_copy)

    print("[preopen] OK %s" % out_p)
    print("[preopen]   taken_at=%s" % snap["taken_at"])
    print("[preopen]   sha256=%s" % snap["account_sha256"][:16])
    print("[preopen]   positions=%d pending_buys=%d pending_sells=%d"
          % (len(pos), len(pb), len(ps)))
    print("[preopen]   sells=%s" % ",".join(snap["pending_sells_codes"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
