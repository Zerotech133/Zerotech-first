# -*- coding: utf-8 -*-
"""完整救援场景触发（ts_daily 落盘后运行）：

模拟 ashare 滞后 2 个交易日（fake max_d=0922），其余全走真实链路：
  真实 trade_cal 缺失计算 → 真实 _tdx_rescue（真实 ts_daily 全市场 map）
  → 真实 parquet bars + _tdx_topup → 与 parquet 自身同日值 parity 终验
  → 非覆盖票（ETF）优雅跳过 → 门闸放行判定输出

零副作用：不写池文件、不跑 scan_all（topup 对新鲜数据为 no-op，接线
逻辑已由单测覆盖；本脚本验证的是救援链路的真实数据正确性）。
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chanlun_scan as cs
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ASHARE = cs.ASHARE
FAKE_MAX_D = "20260922"  # 模拟 ashare 停在 0922（滞后 2 日）
SAMPLES = ["600519.SH", "000001.SZ", "300750.SZ", "601318.SH"]
ETF_PROBE = "510300.SH"  # TDX 不覆盖 → 应优雅跳过


def main():
    # ── 1. 真实 trade_cal：缺失交易日 ──────────────────────────
    import rps_resonance as rps

    rows, fields = rps._ts(
        "trade_cal",
        exchange="SSE",
        start_date=FAKE_MAX_D,
        end_date=datetime.now().strftime("%Y%m%d"),
    )
    i_o, i_c = fields.index("is_open"), fields.index("cal_date")
    missing = sorted(r[i_c] for r in rows if r[i_o] == 1 and r[i_c] > FAKE_MAX_D)
    print("模拟滞后: local=%s → 真实缺失交易日=%s" % (FAKE_MAX_D, missing))
    assert len(missing) >= cs.FRESH_LAG, "缺失不足 FRESH_LAG，场景不成立"

    # ── 2. 真实 _tdx_rescue（真实 ts_daily）─────────────────────
    cs.TDX_RESCUE = cs._tdx_rescue(missing)
    if not cs.TDX_RESCUE:
        print("RESCUE FAILED: TDX 不可补 → 门闸将维持退出（fail-closed）")
        sys.exit(2)
    n_codes = len(next(iter(cs.TDX_RESCUE.values())))
    print(
        "rescue 放行: %d 个缺失日全部可补，单日 map 覆盖 %d 票"
        % (len(cs.TDX_RESCUE), n_codes)
    )

    # ── 3. 模拟滞后 bars + _tdx_topup + parity 终验 ─────────────
    # ashare 实际新鲜（含 0923/0924），截断到 FAKE_MAX_D 模拟滞后，
    # topup 补出的 bar 与 parquet 自身同日真值比对（最强终验）。
    n_ok = n_bad = 0
    for code in SAMPLES:
        f = os.path.join(ASHARE, code.replace(".", "_") + ".parquet")
        if not os.path.exists(f):
            print("  %s: parquet 不存在，跳过" % code)
            continue
        bars = cs._bars_from_parquet(f, limit=cs.LIMIT_BARS)
        stale = [b for b in bars if b["dt"].replace("-", "") <= FAKE_MAX_D]
        base_len = len(stale)
        topped = cs._tdx_topup(stale, code, cs.TDX_RESCUE)
        added = topped[base_len:]
        tail = [(b["dt"], b["c"]) for b in added]
        print("  %s: %d→%d 根，补 %s" % (code, base_len, len(topped), tail or "无"))
        # parity：补出的 bar vs parquet 自身同日 close（真实权威源）
        df = pd.read_parquet(f, columns=["trade_date", "close"])
        ref = {
            str(d)[:10].replace("-", ""): float(c)
            for d, c in zip(df["trade_date"], df["close"])
            if pd.notna(c)
        }
        for b in added:
            d8 = b["dt"].replace("-", "")
            if d8 in ref and abs(b["c"] - ref[d8]) < 0.01:
                n_ok += 1
            else:
                n_bad += 1
                print("    MISMATCH %s topup=%s parquet=%s" % (d8, b["c"], ref.get(d8)))
    # ── 4. ETF 探针：TDX 不覆盖票应优雅跳过 ────────────────────
    f = os.path.join(ASHARE, ETF_PROBE.replace(".", "_") + ".parquet")
    if os.path.exists(f):
        bars = cs._bars_from_parquet(f, limit=cs.LIMIT_BARS)
        topped = cs._tdx_topup(bars, ETF_PROBE, cs.TDX_RESCUE)
        print(
            "ETF 探针 %s: %d→%d 根（预期不变=优雅跳过）"
            % (ETF_PROBE, len(bars), len(topped))
        )
    else:
        print("ETF 探针: parquet 不存在，跳过")

    print("\n=== 完整救援场景结果 ===")
    print("parity(补 bar vs parquet 同日 close): %d OK / %d MISMATCH" % (n_ok, n_bad))
    print(
        "门闸判定: rescue map 覆盖全部缺失日 → scan 将放行"
        if cs.TDX_RESCUE
        else "门闸判定: 维持退出"
    )
    if n_bad == 0 and cs.TDX_RESCUE:
        print("FULL RESCUE SMOKE PASSED")


if __name__ == "__main__":
    main()
