"""Run full-market 20-day CAR IC / long-short evaluation for B1 or B2 factors."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
import factor_lib as f  # noqa: E402

BATCHES = {
    "b1": ("Tushare-动量", "Tushare-流动性", "Tushare-风险", "Tushare-反转", "Tushare-规模"),
    "b2": ("Tushare-Alpha101",),
}
OUTS = {
    "b1": ROOT / "research" / "reports" / "factor_b1_full_eval_20261105.json",
    "b2": ROOT / "research" / "reports" / "factor_b2_full_eval_20261105.json",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("batch", choices=sorted(BATCHES))
    ap.add_argument("--start", default="20230101")
    ap.add_argument("--end", default="20261104")
    ap.add_argument("--n-boot", type=int, default=50)
    args = ap.parse_args()
    fams = BATCHES[args.batch]
    names = [n for n, s in f.FACTORS.items() if s.family in fams]
    print(f"[{args.batch}] factors={len(names)} families={fams}", flush=True)
    print(f"[{args.batch}] names={','.join(names)}", flush=True)
    t0 = time.time()
    report = f.run_eval(
        start=args.start,
        end=args.end,
        factor_names=names,
        horizon=20,
        cost=0.003,
        universe="tradable",
        n_boot=args.n_boot,
        data_root=None,
    )
    elapsed = time.time() - t0
    out = OUTS[args.batch]
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    meta = report["meta"]
    print(f"[{args.batch}] effective={meta.get('effective')}", flush=True)
    print(
        f"[{args.batch}] runtime_sec={elapsed:.1f} factors={meta.get('factors_evaluated')} out={out}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
