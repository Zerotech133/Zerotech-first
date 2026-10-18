# -*- coding: utf-8 -*-
"""S4 影子期每日采集编排（classic 四策略一次跑齐）。

数据流：交易日解析（T+1 口径）→ 逐策略构造 ClassicStrategyAdapter →
shadow_collect.collect_once（事件帧口径内建：只记 buy/sell，hold 计
skipped_non_event）→ 汇总 recorded/replayed/skipped_non_event →
compute_shadow_stats 落 stats JSON（只统计不判 gate 升降级，gate 人工
评审纪律，见 shadow_report_template.md）。

影子池口径：BATCH1_DOSIER.md（S1 档案）未定影子池；S4 离线彩排
（s4_rehearsal_classic_20261018）采用单标的 600519.SH，本脚本沿用同款。
影子池待扩容拍板：拍板后改 SHADOW_POOL 常量即可，采集幂等键
strategy_id|symbol|trade_date 含 symbol，扩容不冲历史档。

交易日历：不硬编码日期。判例 bar_store/calendar_provider——日历必须是
显式快照、不推断节假日；本脚本以仓库只读日线（data_loader.load_bars）
已落库 bar 的显式日期快照作为交易日历：取 end=参考日-1 的最近一根 bar
日期为「上一交易日」（T+1 口径：盘前采 T-1 日信号）。参考日=本地今天
（盘前跑），周末/节假日自然回退到最近交易日，重复跑靠幂等收口。

长跑输出约定（调度判进度）：
- stdout/stderr 全量重定向进日志文件（注册命令见 schedule_plan.md，
  形如 `>> <log> 2>&1`，看日志字节增长判活）；
- 进度判读以产物文件为准：daily_shadow_summary_<date>.json（每轮覆盖
  写，含各策略 recorded/replayed/skipped_non_event 与退出码）与
  <strategy>_shadow.jsonl 字节增长。

退出码（fail-visible 纪律，任何非 0 都不允许静默重试掩盖）：
    0  正常（允许单策略当日无事件，见 stderr WARN）
    2  参数非法
    3  数据源不可用（池标的日线为空/查询失败）
    4  影子数据过期（最近 bar 距参考日超过 STALE_DAYS，疑似采集断流）
    6  采集零产出（全策略 recorded+replayed=0 或全策略 score 抛错——
       数据断供/adapter 失效兜底信号，不静默）
    7  编排异常（collect/stats/写盘抛错）

纪律：零 LLM、零网络（数据走本地只读接口：data_loader / datamart
前向收益只读）；影子档生产写点仅 quant_backtest/data/shadow_onboarding/
（shadow_collect.SHADOW_DIR，本工位名下唯一写点）；干跑用 --out-dir
重定向，不落业务目录。

用法：
    # 每日盘前（调度）
    python strategy_layer/onboarding/daily_shadow.py >> <log> 2>&1

    # 手动补采指定日
    python strategy_layer/onboarding/daily_shadow.py --date 20261016

    # 干跑（产物落 automation_runs，不碰业务影子档）
    python strategy_layer/onboarding/daily_shadow.py \
        --out-dir C:/aios/automation_runs/<dir>
"""
import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence

_HERE = os.path.dirname(os.path.abspath(__file__))
_QB_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _QB_ROOT not in sys.path:
    sys.path.insert(0, _QB_ROOT)

from strategy_layer.onboarding import shadow_collect  # noqa: E402
from strategy_layer.onboarding.classic_adapter import (  # noqa: E402
    ClassicStrategyAdapter,
)

# ---------------------------------------------------------------------- #
# 常量（影子池待扩容拍板，见模块 docstring）
# ---------------------------------------------------------------------- #
STRATEGIES = ("ma_cross", "trend_follow", "rsi_reversal", "boll_breakout")
MARKET = "CN"
SHADOW_POOL: List[str] = ["600519.SH"]
STALE_DAYS = 15          # 最近 bar 距参考日的最大容忍自然日（覆盖春节长假）
EXIT_OK, EXIT_ARGS, EXIT_NO_DATA, EXIT_STALE, EXIT_ZERO, EXIT_ERROR = \
    0, 2, 3, 4, 6, 7


class DailyShadowError(Exception):
    """带退出码语义的编排错误（code: no_data / stale）。"""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = detail


def warn(msg: str) -> None:
    """stderr WARN（fail-visible：调度日志可见，不影响本轮其余策略）。"""
    print("[daily_shadow][WARN] %s" % msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------- #
# 交易日解析（T+1 口径；bar 日期快照当交易日历，不硬编码）
# ---------------------------------------------------------------------- #
def _default_day_loader(market: str, symbols: Sequence[str],
                        start: Optional[str], end: Optional[str]) -> Dict[str, Any]:
    """只读日线加载（与 classic_adapter._default_loader 同源判例）。"""
    from data_loader import load_bars
    return load_bars(market, list(symbols), start, end)


def resolve_prev_trading_day(
    today: Optional[date] = None,
    loader: Optional[Callable[[str, Sequence[str], Optional[str], Optional[str]],
                              Dict[str, Any]]] = None,
    pool: Optional[Sequence[str]] = None,
) -> str:
    """返回上一交易日 YYYYMMDD（盘前 T+1 口径的采集日）。

    取池标的 end=参考日-1 的日线日期并集最大值；周末/节假日自然回退。
    数据不可用 → DailyShadowError("no_data")；最近 bar 距参考日超过
    STALE_DAYS → DailyShadowError("stale")（疑似采集断流，不静默）。
    """
    loader = loader or _default_day_loader
    pool = list(pool or SHADOW_POOL)
    ref = today or date.today()
    end_dash = (ref - timedelta(days=1)).isoformat()
    try:
        frames = loader(MARKET, pool, None, end_dash) or {}
    except Exception as exc:
        raise DailyShadowError("no_data", "日线只读查询失败: %s" % exc) from exc
    dates = set()
    for df in frames.values():
        if df is None or len(df) == 0:
            continue
        for dt in df.index:
            s = str(dt).replace("-", "")[:8]
            if s.isdigit() and len(s) == 8:
                dates.add(s)
    if not dates:
        raise DailyShadowError(
            "no_data", "池标的 %s 在 %s 前无任何已落库日线" % (pool, end_dash))
    latest = max(dates)
    age = (ref - timedelta(days=1)) - date(int(latest[:4]), int(latest[4:6]),
                                           int(latest[6:]))
    if age.days > STALE_DAYS:
        raise DailyShadowError(
            "stale", "最近 bar %s 距参考日-1 已 %d 自然日（>%d），疑似数据断流"
            % (latest, age.days, STALE_DAYS))
    return latest


# ---------------------------------------------------------------------- #
# 前向收益（bar 收盘只读注入；判例 = S4 彩排 make_fwd / council_shadow._fwd）
# ---------------------------------------------------------------------- #
def build_bar_fwd(
    loader: Optional[Callable[[str, Sequence[str], Optional[str], Optional[str]],
                              Dict[str, Any]]] = None,
    pool: Optional[Sequence[str]] = None,
    today: Optional[date] = None,
) -> Optional[Callable[[str, str], Optional[Dict[int, float]]]]:
    """用池标的日线收盘构造 fwd_fn(symbol, trade_date) -> {5:%, 20:%}。

    口径同 shadow_collect._default_fwd（信号日收盘 → 第 N 交易日收盘，%），
    数据源走 data_loader 只读（与 adapter 同源）。datamart_read 缺省通道
    不可用时此为统计前向收益的兜底判例（S4 彩排同款）。数据不可用返回
    None（compute_shadow_stats 回落缺省通道，r5/r20 置 None 不致命）。
    """
    loader = loader or _default_day_loader
    pool = list(pool or SHADOW_POOL)
    ref = today or date.today()
    try:
        frames = loader(MARKET, pool, None, ref.isoformat()) or {}
    except Exception:
        return None
    table: Dict[str, Any] = {}
    for sym, df in frames.items():
        if df is None or len(df) == 0:
            continue
        dates = [str(d).replace("-", "")[:8] for d in df.index]
        closes = [float(c) for c in df["close"].tolist()]
        ix = {d: i for i, d in enumerate(dates)}
        table[sym] = (ix, closes)

    def fwd(symbol: str, trade_date: str) -> Optional[Dict[int, float]]:
        entry = table.get(symbol)
        if not entry:
            return None
        i = entry[0].get(str(trade_date).replace("-", "")[:8])
        if i is None:
            return None
        closes = entry[1]
        return {n: round((closes[i + n] / closes[i] - 1) * 100, 2)
                for n in shadow_collect.NS if i + n < len(closes)} or None
    return fwd if table else None


# ---------------------------------------------------------------------- #
# 编排主流程（adapter 注入点供测试；生产走 build_adapters）
# ---------------------------------------------------------------------- #
def build_adapters(pool: Optional[Sequence[str]] = None) -> List[Any]:
    """逐策略构造 ClassicStrategyAdapter（参数回落 PARAM_DEFAULTS）。"""
    pool = list(pool or SHADOW_POOL)
    return [ClassicStrategyAdapter(name, pool, market=MARKET)
            for name in STRATEGIES]


def run_once(trade_date: str,
             adapters: Sequence[Any],
             out_dir: Optional[str] = None,
             fwd_fn: Optional[Callable[[str, str],
                                       Optional[Dict[int, float]]]] = None,
             ) -> Dict[str, Any]:
    """单轮采集 + 统计，返回 summary dict（含 exit_code 判定所需的全部字段）。

    out_dir None → 生产写点（shadow_collect.shadow_path）；给定 → 干跑
    重定向（<out_dir>/<strategy>_shadow.jsonl），不落业务目录。
    fwd_fn 注入供测试零依赖；None → build_bar_fwd（bar 收盘只读兜底，
    S4 彩排判例），仍不可用再回落 compute_shadow_stats 缺省 datamart
    只读通道（失败不致命，r5/r20 置 None）。

    退出码规则（见模块 docstring）：
    - 单策略 recorded+replayed=0：stderr WARN，不致命（单标的单日
      无仓位变化事件属正常影子态）；
    - 全策略零产出或全策略 score 抛错：exit 6（数据断供/adapter 失效
      兜底，不静默）。
    """
    td8 = str(trade_date).replace("-", "")
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    per: Dict[str, Any] = {}
    zero_strategies: List[str] = []
    for ad in adapters:
        sid = ad.strategy_id
        out_path = (os.path.join(out_dir, "%s_shadow.jsonl" % sid)
                    if out_dir else None)
        entry: Dict[str, Any] = {"path": out_path or
                                 shadow_collect.shadow_path(sid)}
        # score 预检：抛错显式捕获（adapter 约定不 raise 时此为双保险；
        # ClassicStrategyAdapter 的 as-of 缓存使 collect_once 内二次调用免费）
        try:
            ad.score(td8)
        except Exception as exc:
            entry["error"] = str(exc)
            warn("%s adapter.score 抛错，本轮该策略不采集: %s" % (sid, exc))
            per[sid] = entry
            continue
        res = shadow_collect.collect_once(ad, td8, out_path=out_path)
        entry.update({k: res[k] for k in
                      ("recorded", "replayed", "skipped_non_event")})
        if entry["recorded"] + entry["replayed"] == 0:
            zero_strategies.append(sid)
            warn("%s 本轮零产出（recorded=0 replayed=0；单标的单日无事件"
                 "属正常，全策略零产出则以退出码 %d 上报）"
                 % (sid, EXIT_ZERO))
        per[sid] = entry

    all_zero = all(per[s].get("error")
                   or (per[s].get("recorded", 0) + per[s].get("replayed", 0)) == 0
                   for s in (a.strategy_id for a in adapters))
    exit_code = EXIT_ZERO if all_zero else EXIT_OK

    # 统计：只统计不判 gate 升降级（人工评审纪律）；samples 不落日档
    if fwd_fn is None:
        fwd_fn = build_bar_fwd()  # bar 收盘只读兜底（彩排判例）；仍不可用则回落缺省
    stats: Dict[str, Any] = {}
    for ad in adapters:
        sid = ad.strategy_id
        if per[sid].get("error"):
            continue
        rows = shadow_collect.load_rows(per[sid]["path"])
        st = shadow_collect.compute_shadow_stats(rows, fwd_fn=fwd_fn)
        st.pop("samples", None)  # 日档瘦身，全量样本仍在影子档 JSONL
        stats[sid] = st
        per[sid]["gate"] = st["gate"]["status"]

    summary = {"trade_date": td8, "pool": list(SHADOW_POOL),
               "market": MARKET, "strategies": per, "stats": stats,
               "exit_code": exit_code, "zero_strategies": zero_strategies,
               "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    base = out_dir or shadow_collect.SHADOW_DIR
    sum_path = os.path.join(base, "daily_shadow_summary_%s.json" % td8)
    tmp = sum_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    os.replace(tmp, sum_path)  # 覆盖写原子化，调度判进度看本文件
    summary["summary_path"] = sum_path
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="S4 影子期每日采集编排（classic 四策略）")
    ap.add_argument("--date", default=None,
                    help="采集交易日 YYYYMMDD（缺省=上一交易日，T+1 口径）")
    ap.add_argument("--out-dir", default=None,
                    help="影子档/统计重定向目录（干跑用；缺省=业务写点）")
    a = ap.parse_args(argv)

    try:
        if a.date:
            d = a.date.replace("-", "")
            if len(d) != 8 or not d.isdigit():
                print("[daily_shadow] --date 必须为 YYYYMMDD: %r" % a.date,
                      file=sys.stderr)
                return EXIT_ARGS
            td8 = d
        else:
            td8 = resolve_prev_trading_day()
    except DailyShadowError as exc:
        warn("交易日解析失败: %s" % exc)
        return EXIT_NO_DATA if exc.code == "no_data" else EXIT_STALE

    try:
        adapters = build_adapters()
        summary = run_once(td8, adapters, out_dir=a.out_dir)
    except Exception as exc:
        warn("编排异常: %r" % exc)
        return EXIT_ERROR

    for sid, e in summary["strategies"].items():
        if e.get("error"):
            continue
        print("[daily_shadow] %s %s recorded=%d replayed=%d "
              "skipped_non_event=%d -> %s"
              % (sid, td8, e["recorded"], e["replayed"],
                 e["skipped_non_event"], e["path"]))
    print("[daily_shadow] 完成 trade_date=%s exit=%d summary=%s"
          % (td8, summary["exit_code"], summary["summary_path"]))
    return summary["exit_code"]


if __name__ == "__main__":
    # 自写日志（调度判活看本文件字节增长；仅脚本直跑生效，测试 main() 不受影响）。
    # --out-dir 干跑时日志跟随重定向目录，不落业务写点。
    _argv = sys.argv[1:]
    _out_dir = (_argv[_argv.index("--out-dir") + 1]
                if "--out-dir" in _argv
                and _argv.index("--out-dir") + 1 < len(_argv) else None)
    _LOG = os.path.join(
        _out_dir or shadow_collect.SHADOW_DIR, "daily_shadow.log")

    class _Tee:
        def __init__(self, *streams):
            self._streams = streams

        def write(self, s):
            for st in self._streams:
                st.write(s)
                st.flush()
            return len(s)

        def flush(self):
            for st in self._streams:
                st.flush()

    os.makedirs(os.path.dirname(_LOG), exist_ok=True)
    _log_f = open(_LOG, "a", encoding="utf-8")
    _log_f.write("---- %s ----\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    sys.stdout = _Tee(sys.stdout, _log_f)
    sys.stderr = _Tee(sys.stderr, _log_f)
    sys.exit(main())
