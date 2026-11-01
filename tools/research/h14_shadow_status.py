# -*- coding: utf-8 -*-
"""H14 影子盘状态 → 门户静态 JSON（零重启）。

数据：work/research/h14_shadow/shadow_*.json（月度持仓）+ work/data_sync/h14_shadow.log 最新行 +
     research/reports/H14_shadow_*.json（回测对比，若有）
输出：portal/static/h14_shadow.json
用法：python tools/research/h14_shadow_status.py
"""
from __future__ import annotations

import glob
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
SHADOW = REPO / "work" / "research" / "h14_shadow"
LOG = REPO / "work" / "data_sync" / "h14_shadow.log"
OUT = REPO / "portal" / "static" / "h14_shadow.json"


def main() -> int:
    files = sorted(SHADOW.glob("shadow_*.json"))
    if not files:
        print("no shadow files", flush=True)
        return 1
    cur = json.loads(files[-1].read_text(encoding="utf-8"))
    holdings = cur.get("holdings") or []
    trap = [h for h in holdings if h.get("trap")]
    # 最新日志行（含回测对比）
    cmp = {}
    if LOG.exists():
        lines = [ln for ln in LOG.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            try:
                last = json.loads(lines[-1])
                cmp = last.get("cmp") or {}
            except Exception:  # noqa: BLE001
                pass
    # 历史影子期列表
    periods = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            periods.append({"file": f.name, "period": f.stem.replace("shadow_", ""),
                            "signal_date": d.get("signal_date"), "n": d.get("n_holdings"),
                            "trap_w": round(float(d.get("trap_weight_constrained") or 0) * 100, 2)})
        except Exception:  # noqa: BLE001
            pass
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "period": files[-1].stem.replace("shadow_", ""),
        "scheme": cur.get("scheme"),
        "signal_date": cur.get("signal_date"),
        "n_holdings": cur.get("n_holdings"),
        "trap_cap_pct": round(float(cur.get("trap_cap") or 0) * 100, 1),
        "trap_weight_unconstrained_pct": round(float(cur.get("trap_weight_unconstrained") or 0) * 100, 2),
        "trap_weight_constrained_pct": round(float(cur.get("trap_weight_constrained") or 0) * 100, 2),
        "trap_count": len(trap),
        "top_holdings": sorted(holdings, key=lambda h: -(h.get("w_constrained") or 0))[:20],
        "cmp": cmp,
        "periods": periods,
        "health": {"trap_within_cap": float(cur.get("trap_weight_constrained") or 0) <= float(cur.get("trap_cap") or 0.05) + 1e-9,
                   "n_reasonable": 100 <= int(cur.get("n_holdings") or 0) <= 2000},
        "next_check": "2026-12-01 08:40（第二期验证）",
        "note": "H14 影子盘研究跟踪；不构成投资建议",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("period", "signal_date", "n_holdings",
                                              "trap_weight_unconstrained_pct", "trap_weight_constrained_pct",
                                              "trap_count", "health")}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
