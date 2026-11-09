# -*- coding: utf-8 -*-
"""因子计算 worker（独立子进程；只读 parquet，不联网、不写库）。

由 portal/api_factor.py 的 POST /api/factor/compute 调用：
  stdin  : 一个 JSON object（见共享契约）
  stdout : 一个 JSON object（rows 为 [date, code, factor, value] 数组）
  stderr : 诊断/异常回溯，由父进程截断后记录，不直接回给客户端

隔离原因：pandas CPU 计算会阻塞 8091 主进程（标准库 HTTP Handler），
且 B3 财报因子首次加载需要读大量小 parquet；子进程可超时被杀、不污染
门户进程内存。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_RESEARCH = _REPO / "tools" / "research"
if str(_RESEARCH) not in sys.path:
    sys.path.insert(0, str(_RESEARCH))

import factor_lib  # noqa: E402  （路径注入后导入）


def _load_panel(start: str, end: str, universe: str, names: list[str]):
    need_roe = False
    for name in names:
        spec = factor_lib.FACTORS[name]
        if spec.family == "Tushare-财务" or "roe" in spec.requires:
            need_roe = True
            break
    # horizon 只影响尾部多载行数；factor value 不需要 forward return。
    # 40 日 warmup 是 factor_lib 的默认口径，短窗口因子在早期为 NaN 属正常。
    return factor_lib.load_panel(
        start, end, horizon=5, universe=universe, load_roe=need_roe)


def _eligible_codes(panel, universe: str) -> set[str]:
    if universe == "all":
        return set(map(str, panel.codes))
    mask = panel.eligible_mask()
    # 只要在任一交易日可交易，就允许返回该股票的值；调用方按
    # date+code 自行决定当天是否使用（避免把停牌前的值也完全隐藏）。
    cols = mask.columns[mask.any(axis=0)]
    return set(map(str, cols))


def main() -> int:
    t0 = time.time()
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        req = json.loads(raw)
        if not isinstance(req, dict):
            raise ValueError("payload 必须是 JSON object")
        names = [str(x) for x in req.get("factors", [])]
        if not names:
            raise ValueError("factors 不能为空")
        unknown = [n for n in names if n not in factor_lib.FACTORS]
        if unknown:
            raise ValueError("未知因子: " + ",".join(unknown[:10]))
        start = str(req["start"])
        end = str(req["end"])
        universe = str(req.get("universe", "tradable"))
        if universe not in ("tradable", "all"):
            raise ValueError("universe 只能是 tradable/all")
        codes_req = [str(x) for x in req.get("codes", [])]
        limit = int(req.get("limit", 50000))
        if limit < 1:
            raise ValueError("limit 必须 > 0")

        panel = _load_panel(start, end, universe, names)
        allowed = _eligible_codes(panel, universe)
        if codes_req:
            allowed &= set(codes_req)
        code_pos = {str(c): i for i, c in enumerate(panel.codes)}
        keep_positions = [code_pos[c] for c in sorted(allowed) if c in code_pos]
        # 只输出请求信号区间；warmup/尾部不返回。
        date_positions = [i for i, d in enumerate(map(str, panel.dates))
                          if start <= d <= end]

        rows: list[list] = []
        truncated = False
        for name in names:
            spec = factor_lib.FACTORS[name]
            frame = spec.compute(panel)
            frame = frame.reindex(index=panel.dates, columns=panel.codes)
            values = frame.to_numpy(dtype=float, copy=False)
            for di in date_positions:
                d = str(panel.dates[di])
                for ci in keep_positions:
                    v = values[di, ci]
                    if not np.isfinite(v):
                        continue
                    if len(rows) >= limit:
                        truncated = True
                        break
                    rows.append([d, str(panel.codes[ci]), name,
                                 round(float(v), 8)])
                if truncated:
                    break
            if truncated:
                break

        out = {
            "ok": True,
            "factor_lib_version": getattr(factor_lib, "LIB_VERSION", ""),
            "start": start,
            "end": end,
            "universe": universe,
            "factors": names,
            "rows": rows,
            "count": len(rows),
            "truncated": truncated,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }
        sys.stdout.write(json.dumps(out, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.stderr.write("\nFACTOR_WORKER_ERROR: %s: %s\n" % (
            type(exc).__name__, exc))
        sys.stdout.write(json.dumps({
            "ok": False,
            "error": "%s: %s" % (type(exc).__name__, exc),
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
