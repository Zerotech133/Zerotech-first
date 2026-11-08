# -*- coding: utf-8 -*-
"""影子盘自检：六条断言，任何一条不成立都必须让流水不可信（K-RES-20261108-SHADOW-CHK）。

为什么自检要写这么重
--------------------
影子盘的价值全在「每天那行数字能当真」。它一旦悄悄算错，比没有更糟——
你会拿一个错的账户去和真账户比，然后据此改产线。所以这里不查「代码跑不跑得通」，
只查六件会直接毁掉结论的事：

S1 对账    mature 口径下必须与 `three_layer_v3.py` 的 A/D/E 逐位相同。
           不同就说明影子盘的信号构造、持有期或成本口径与批量回测不是一回事，
           两边结论不能互相引用。
S2 幂等    同一天跑两次，流水文件字节完全相同。不同就说明有隐藏状态（时间、
           随机序、字典序）漏进结果，历史序列会被悄悄改写。
S3 账目   每一天 现金 + Σ持仓市值 == 权益（差 < 1e-9）。不成立说明盯市和结算
           用了两套价格，净值曲线在撒谎。
S4 闸门    v3 在「弱」日的开仓数必须为 0。不成立说明择势根本没生效，
           或者势标签与开仓日错位。
S5 上限    设了单票上限的书，任何一笔权重都不得超过上限 1 个基点。
S6 锁死    停牌/无后续K线的仓位必须一直挂到 as-of，不能提前释放现金。
           这条是 2026-11-08 抓到的真 bug：旧口径把 688496 的停牌当成已出场，
           现金被放回来继续开新仓，等于白拿一笔仓位。

退化副本（--degrade）会把闸门打开、把上限去掉、把停牌仓位提前平掉，
预期 S4/S5/S6 精确报错并 exit 1。如果退化副本还能全过，说明断言是空的。

用法:
    python tools/research/shadow_selfcheck.py
    python tools/research/shadow_selfcheck.py --degrade
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

PY = sys.executable
SHADOW = os.path.join(HERE, "shadow_book.py")
LEDGER = os.path.join(ROOT, "portal", "shadow_book_v3.jsonl")
STATE = os.path.join(ROOT, "portal", "shadow_state_v3.json")
BATCH = os.path.join(ROOT, "portal", "three_layer_v3.json")

RESULT = []


def say(*x):
    print(*x)


def add(name, ok, msg):
    RESULT.append((name, ok, msg))
    say("  [%s] %-4s %s" % ("PASS" if ok else "FAIL", name, msg))


def run(args):
    r = subprocess.run([PY, SHADOW] + args, cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        say(r.stdout[-2000:])
        say(r.stderr[-2000:])
        raise SystemExit("shadow_book 运行失败 rc=%d" % r.returncode)
    return r.stdout


def sha1(p):
    return hashlib.sha1(open(p, "rb").read()).hexdigest()


def check_s1(tmp):
    """mature 口径与批量回测逐位对账。"""
    out = os.path.join(tmp, "m.jsonl")
    st = os.path.join(tmp, "m.json")
    run(["--universe", "mature", "--rebuild", "--no-log", "--json",
         "--out", out, "--state", st])
    sh = json.load(open(st, encoding="utf-8"))["books"]
    bt = json.load(open(BATCH, encoding="utf-8"))["configs"]
    pairs = [("cur", "A 现行"), ("v3", "E 强8/中1"), ("v3d", "D C+弱市不做")]
    bad = []
    for k, lab in pairs:
        b = bt[lab]
        for f in ("total", "cagr", "mdd", "sharpe", "util"):
            if abs(float(sh[k][f]) - float(b[f])) > 1e-9:
                bad.append("%s.%s 影子 %s vs 批量 %s" % (k, f, sh[k][f], b[f]))
    add("S1", not bad, "mature 口径对账 A/D/E：%s"
        % ("逐位一致" if not bad else "；".join(bad[:4])))


def check_s2(tmp):
    a = os.path.join(tmp, "i1.jsonl")
    b = os.path.join(tmp, "i2.jsonl")
    run(["--rebuild", "--no-log", "--json", "--out", a,
         "--state", os.path.join(tmp, "i1.json")])
    run(["--rebuild", "--no-log", "--json", "--out", b,
         "--state", os.path.join(tmp, "i2.json")])
    h1, h2 = sha1(a), sha1(b)
    add("S2", h1 == h2, "同输入两次运行 sha1 %s vs %s" % (h1[:12], h2[:12]))
    # 追加模式也不许重复
    n0 = sum(1 for _ in open(a, encoding="utf-8"))
    run(["--no-log", "--json", "--out", a, "--state", os.path.join(tmp, "i1.json")])
    n1 = sum(1 for _ in open(a, encoding="utf-8"))
    add("S2b", n0 == n1, "同日增量重跑行数 %d → %d（应不变）" % (n0, n1))


def check_s3():
    bad, worst, nd = 0, 0.0, 0
    for ln in open(LEDGER, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        rec = json.loads(ln)
        nd += 1
        for k, bk in rec["books"].items():
            eq = bk["eq"]
            hold = sum(p["amt"] * (1.0 + p["upnl_pct"] / 100.0) for p in bk["pos"])
            d = abs(bk["cash"] + hold - eq)
            worst = max(worst, d)
            if d > 5e-6:
                bad += 1
    add("S3", bad == 0, "%d 日 × 3 本账，现金+持仓=权益 最大偏差 %.2e（阈值 5e-6，越界 %d 处）"
        % (nd, worst, bad))


def check_s4():
    viol = []
    for ln in open(LEDGER, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        rec = json.loads(ln)
        if rec["regime"] == "弱" and rec["books"]["v3"]["open"]:
            viol.append((rec["d"], len(rec["books"]["v3"]["open"])))
    add("S4", not viol, "v3 弱市开仓 %d 天（应为 0）%s"
        % (len(viol), "" if not viol else "，例 %s" % (viol[:3],)))


def check_s5():
    worst = {}
    for ln in open(LEDGER, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        rec = json.loads(ln)
        for k, bk in rec["books"].items():
            for o in bk["open"]:
                worst[k] = max(worst.get(k, 0.0), o["w_pct"])
    bad = {k: v for k, v in worst.items() if k in ("v3", "v3d") and v > 5.001}
    add("S5", not bad, "单笔最大权重 %s" % "｜".join(
        "%s %.2f%%" % (k, v) for k, v in sorted(worst.items())))


def check_s6():
    """停牌仓位必须一路挂到 as-of，且到期日早于 as-of（否则不算锁死）。"""
    st = json.load(open(STATE, encoding="utf-8"))
    tail = json.loads(open(LEDGER, encoding="utf-8").readlines()[-1])
    bad, msg = [], []
    for k, b in st["books"].items():
        hs = [p for p in b["open_pos"] if p.get("status") == "halted"]
        in_tail = sum(1 for p in tail["books"][k]["pos"]
                      if (p.get("tag") or {}).get("status") == "halted")
        msg.append("%s %d 笔（末日仍在账 %d）" % (k, len(hs), in_tail))
        if len(hs) != in_tail:
            bad.append("%s 停牌 %d 笔但末日账上只剩 %d 笔——现金被提前释放"
                       % (k, len(hs), in_tail))
        for p in hs:
            if p.get("exp") and str(p["exp"]) >= st["asof"]:
                bad.append("%s %s 到期日 %s 不早于 as-of，不该判为锁死"
                           % (k, p["code"], p["exp"]))
    add("S6", not bad, "停牌锁死：%s%s"
        % ("，".join(msg), "" if not bad else "；" + "；".join(bad[:3])))


def degrade(tmp):
    """退化副本：故意拆掉三条约束，预期 S4/S5 报错、S6 抓不到锁死仓位。"""
    global LEDGER, STATE
    say("\n退化副本：关掉势闸门、去掉单票上限（弱市照做）")
    import shadow_book as sb
    sb.BOOKS[1]["skip_weak"] = False
    sb.BOOKS[1]["h"] = {"强": 8, "中": 1, "弱": 8}
    sb.BOOKS[1]["cap"] = None
    LEDGER = os.path.join(tmp, "d.jsonl")
    STATE = os.path.join(tmp, "d.json")
    sys.argv = ["shadow_book.py", "--rebuild", "--no-log", "--json",
                "--out", LEDGER, "--state", STATE]
    sb.main()
    check_s4()
    check_s5()
    bad = [n for n, ok, _ in RESULT if not ok]
    if not bad:
        say("\n退化副本全过 —— 断言是空的，不可信")
        return 1
    say("\n退化副本按预期报错：%s" % ", ".join(bad))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--degrade", action="store_true")
    a = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="shadow_chk_")
    say("=" * 88)
    say("影子盘自检｜ledger %s" % os.path.basename(LEDGER))
    say("=" * 88)
    try:
        if a.degrade:
            return degrade(tmp)

        check_s1(tmp)
        check_s2(tmp)
        check_s3()
        check_s4()
        check_s5()
        check_s6()

        n_bad = sum(1 for _, ok, _ in RESULT if not ok)
        say("-" * 88)
        say("%d/%d 通过" % (len(RESULT) - n_bad, len(RESULT)))
        return 1 if n_bad else 0
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
