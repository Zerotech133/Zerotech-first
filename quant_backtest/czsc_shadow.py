# -*- coding: utf-8 -*-
"""CZSC 影子旁路管道（K-ORG-202，承 Phase 1 G1 特征侧放行）。

双模式设计（隔离核心）：
- 默认模式（编排器）：由任意主环境 python 运行，**本文件顶层不 import czsc**。
  职责：读生产 ledger.jsonl 最近 found_day 影子名单 → mtime 守卫 → 以独立 venv
  python 子进程调起本文件的 --worker 模式 → 校验产物摘要。
- --worker 模式：由 quant_backtest/.venv_czsc/Scripts/python.exe 运行（czsc==1.0.2
  仅存于该 venv），对名单内票计算当日截面 6 特征 + 4 原生信号触发标记，append-only
  写入 data/chanlun_learn/czsc_ledger.jsonl（(code, dt) 键幂等跳过）。

影子铁律：czsc_ledger.jsonl 仅作观察旁证（周滚动 RankIC 读数进周记旁证段），
永不进 verdict / L3 判读 / L4 出口，不产生交易指令；生产三账本（ledger/verify/
attribution）只读。
"""

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(BASE, ".venv_czsc", "Scripts", "python.exe")
LEDGER_PATH = os.path.join(BASE, "data", "chanlun_learn", "ledger.jsonl")
CZSC_LEDGER_PATH = os.path.join(BASE, "data", "chanlun_learn", "czsc_ledger.jsonl")
ASHARE_DIR = os.path.join(BASE, "data", "ashare")

SIGS = [
    ("日线_D1_三买辅助V230228_三买_任意_任意_0", "三买"),
    ("日线_D1_表里关系V230101_向上_任意_任意_0", "向上"),
    ("日线_D1_涨跌停V230331_涨停_任意_任意_0", "涨停"),
    ("日线_D1B_BUY1V221126_一买_任意_任意_0", "!其他"),
]
SCHEMA_V = 2  # v2: 增加 found_day 元数据


# ───────────────────────── 编排器（主环境） ─────────────────────────


def extract_codes(ledger_path):
    """最近一个 found_day 的去重票集。返回 (found_day, codes)。"""
    rows = [json.loads(l) for l in open(ledger_path, encoding="utf-8") if l.strip()]
    days = [r.get("found_day") for r in rows if r.get("found_day")]
    latest = max(days) if days else None
    codes = []
    for r in rows:
        c = r.get("code")
        if latest is None or r.get("found_day") != latest or not c:
            continue
        code_n = c.replace(".", "_")  # 去重基于替换后形式，避免 . 与 _ 碰撞漏判
        if code_n not in codes:
            codes.append(code_n)
    return latest, codes


def mtime_is_today(path, now=None):
    now = now or datetime.now()
    return datetime.fromtimestamp(os.path.getmtime(path)).date() == now.date()


def orchestrate(
    venv_py=VENV_PY,
    ledger_path=LEDGER_PATH,
    out_path=CZSC_LEDGER_PATH,
    ashare_dir=ASHARE_DIR,
    now=None,
):
    """编排一次影子落账。返回 (status, detail)。status ∈ ran/skip_*。"""
    if not os.path.exists(venv_py):
        return "skip_no_venv", f"venv python 不存在: {venv_py}"
    if not os.path.exists(ledger_path):
        return "skip_no_ledger", f"生产 ledger 不存在: {ledger_path}"
    if not mtime_is_today(ledger_path, now):
        mtime = datetime.fromtimestamp(os.path.getmtime(ledger_path)).isoformat()
        return "skip_stale_ledger", f"ledger mtime 非当日({mtime})，L1 未跑或跨日"
    found_day, codes = extract_codes(ledger_path)
    if not codes:
        return "skip_empty_universe", "最近 found_day 无票"
    codes_file = out_path + ".codes.json"
    json.dump(codes, open(codes_file, "w", encoding="utf-8"))
    cmd = [
        venv_py,
        os.path.abspath(__file__),
        "--worker",
        "--codes",
        codes_file,
        "--out",
        out_path,
        "--ashare-dir",
        ashare_dir,
        "--meta",
        str(found_day),
    ]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=900)
    if proc.returncode != 0:
        return "error_worker", (proc.stderr or proc.stdout)[-300:]
    detail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "{}"
    return "ran", detail


def main_orchestrate():
    status, detail = orchestrate()
    print(json.dumps({"status": status, "detail": detail}, ensure_ascii=False))
    return 0  # 影子链非阻塞：任何 skip/error 都不令调度红


# ───────────────────────── worker（venv 子进程） ─────────────────────────


def _fnum(v):
    try:
        f = float(v)
        return round(f, 6) if math.isfinite(f) else None
    except Exception:
        return None


def worker_row(tk, ashare_dir, cfg):
    p = os.path.join(ashare_dir, tk + ".parquet")
    if not os.path.exists(p):
        return None
    import pandas as pd
    from czsc import CZSC, format_standard_kline, get_zs_seq, generate_czsc_signals

    raw = pd.read_parquet(p).sort_values("trade_date").reset_index(drop=True)
    dates = raw["trade_date"].astype(str).tolist()
    df = pd.DataFrame(
        {
            "symbol": tk,
            "dt": pd.to_datetime(raw["trade_date"].astype(str)),
            "open": raw["open"].astype(float),
            "close": raw["close"].astype(float),
            "high": raw["high"].astype(float),
            "low": raw["low"].astype(float),
            "vol": raw["volume"].astype(float),
            "amount": raw["amount"].astype(float),
        }
    )
    bars = format_standard_kline(df, freq="日线")
    c = CZSC(bars, max_bi_num=500)
    as_of = dates[-1]
    close = float(raw["close"].iloc[-1])
    feats = {
        "power": None,
        "slope": None,
        "rsq": None,
        "angle": None,
        "accel": None,
        "zz_rel": None,
    }
    if c.bi_list:
        b = c.bi_list[-1]
        for k, attr in (
            ("power", "power"),
            ("slope", "slope"),
            ("rsq", "rsq"),
            ("angle", "angle"),
            ("accel", "acceleration"),
        ):
            feats[k] = _fnum(getattr(b, attr, None))
    for zs in reversed(get_zs_seq(c.bi_list)):
        try:
            if zs.sdt.strftime("%Y%m%d") <= as_of:
                zd, gg = float(zs.zd), float(zs.gg)
                if gg > zd:
                    feats["zz_rel"] = round((close - zd) / (gg - zd), 6)
                break
        except Exception:
            continue
    sig = generate_czsc_signals(
        bars, signals_config=cfg, sdt=dates[max(0, len(dates) - 250)], df=True
    )
    sig["dt"] = pd.to_datetime(sig["dt"]).dt.strftime("%Y%m%d")
    last = sig.iloc[-1]
    fired = {}
    for s, fire in SIGS:
        base = "_".join(s.split("_")[:3])
        if base not in sig.columns:
            fired[base] = False
            continue
        h = str(last[base]).split("_")[0]
        fired[base] = (h != "其他") if fire == "!其他" else (h == fire)
    return {
        "code": tk,
        "dt": as_of,
        "close": round(close, 3),
        "feats": feats,
        "fired": fired,
        "bi_n": len(c.bi_list),
        "zs_n": len(get_zs_seq(c.bi_list)),
    }


def worker_main(codes, out_path, ashare_dir, meta):
    from czsc import get_signals_config

    cfg = get_signals_config([s for s, _ in SIGS])
    have = set()
    if os.path.exists(out_path):
        for l in open(out_path, encoding="utf-8"):
            if l.strip():
                r = json.loads(l)
                have.add((r["code"], r["dt"]))
    written = skipped = errored = 0
    with open(out_path, "a", encoding="utf-8") as f:
        for tk in codes:
            try:
                row = worker_row(tk, ashare_dir, cfg)
            except Exception as e:  # 单票异常不中断全批
                if (tk, "ERROR") in have:  # error 行同样幂等：只写一次
                    skipped += 1
                else:
                    have.add((tk, "ERROR"))
                    f.write(
                        json.dumps(
                            {
                                "schema_v": SCHEMA_V,
                                "code": tk,
                                "meta": meta,
                                "error": str(e)[:160],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    errored += 1
                continue
            if row is None:
                continue
            row = {"schema_v": SCHEMA_V, "meta": meta, **row}
            if (row["code"], row["dt"]) in have:
                skipped += 1
                continue
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1
    print(
        json.dumps(
            {
                "written": written,
                "skipped_existing": skipped,
                "errored": errored,
                "meta": meta,
            }
        )
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", action="store_true")
    ap.add_argument("--codes")
    ap.add_argument("--out")
    ap.add_argument("--ashare-dir", default=ASHARE_DIR)
    ap.add_argument("--meta", default="")
    a = ap.parse_args()
    if a.worker:
        codes = json.load(open(a.codes, encoding="utf-8"))
        worker_main(codes, a.out, a.ashare_dir, a.meta)
    else:
        sys.exit(main_orchestrate())
