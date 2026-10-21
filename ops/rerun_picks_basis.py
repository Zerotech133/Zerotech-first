# -*- coding: utf-8 -*-
"""按指定基准日重跑 todays_picks 聚合（回填/复现专用，不动生产代码）。

场景：行情湖最新交易日已推进到目标基准日之后（如湖=20261016，需复现
2026-10-14 档扫描）。直跑 build_todays_picks.py 会以湖最新日为基准、
轮动对比对象随之漂移（vs 20261014 而非 vs 20261013），且 trade_basis
文案变成 10-16——不符合复现口径。本包装 monkeypatch tdx_read.latest_date
把基准日钉死为入参日，其余全部走原脚本逻辑：
  - 过期闸门：源基准新于钉死日（lag<0）放行，旧于钉死日按原阈值拦截；
  - 分层/T1 闸/CROWD/影子标注：原样；
  - 池子轮动：_latest_history 排除钉死日归档，对比其余归档中 mtime 最新者；
  - 行情回填：read_day(ts_daily, 钉死日) 取该日收盘价/涨跌幅；
  - 归档：首写锁定不变（todays_<钉死日>.json 已存在则保留首次快照，
    仅刷新 portal/data/todays_picks.json 活文件）。

用法: python ops/rerun_picks_basis.py YYYYMMDD
退出码口径同 build_todays_picks: 0=正常; 3=跑完但 T1 停挂; 2=参数错。
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def main():
    basis = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not (len(basis) == 8 and basis.isdigit()):
        print("usage: python ops/rerun_picks_basis.py YYYYMMDD")
        return 2
    sys.path.insert(0, os.path.join(ROOT, "quant_backtest"))
    import tdx_read  # noqa: E402
    tdx_read.latest_date = lambda table="ts_daily", _b=basis: _b
    sys.path.insert(0, os.path.join(ROOT, "portal"))
    import build_todays_picks  # noqa: E402
    print("[rerun] 基准日钉死为 %s（行情湖实际最新日以 latest_date 原值为准，"
          "此处仅复现口径）" % basis)
    ok = build_todays_picks.main()
    print("[rerun] basis=%s veto_ok=%s" % (basis, ok))
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
