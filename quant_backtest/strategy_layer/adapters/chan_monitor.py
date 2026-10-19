# -*- coding: utf-8 -*-
"""缠论信号包装策略适配器（strategy_id = "chan_monitor"）。

上游产出：``chanlun_signal.py``（三类买卖点规则引擎）的信号由
``chanlun_scan.py`` 扫描后落入信号池 JSONL（本适配器读其产出文件，
不直接 import 引擎，保持包装层独立可测）：

- ``data/chanlun_pool.jsonl``（A 股）、``chanlun_pool_us.jsonl``（美股）、
  ``chanlun_pool_hk.jsonl``（港股，存在才读）
- 每行字段：market, code, name, period, type(1买/2买/3买/1卖/2卖/3卖),
  dt(YYYY-MM-DD), price, detect_date, lag, entry_px,
  mbei/bei/bei2/vv(影子标注), reg, tbe, trend ...

转换规则（score 按缠论买卖点类型分位映射）：
- 分位基表：1买=95 / 2买=85 / 3买=75 / 3卖=25 / 2卖=15 / 1卖=5；
- 影子共振加成：mbei / bei / bei2 / vv 每个为 True 加 2 分，封顶 100；
- action 映射：买点 → buy，卖点 → sell（缠论信号本身就是择时结论，
  不产生 hold/watch）；
- confidence：基数 0.6，mbei/bei/bei2/vv 每个共振 +0.1，封顶 0.9；
- 毒层降档（Δ4，2026-10-19 确认门① A 方案拍板）：买点中
  3买∧bei=False 或 2买∧trend=range 判毒层（口径同 picks 链
  ``_chanlun_toxic``），score 封顶 ``TOXIC_SCORE_CAP``、action 不变
  （buy 仍 buy——池内低分沉底，语义同 picks 链「T1 共振不计票、
  票仍走 T2 单策略通道」），rationale 追加毒层标记供后验分层；
  ``TOXIC_DOWNGRADE=False`` 一键回退。卖点不判毒；
- rationale：``缠论{type}·{trend或reg}：{备注}（信号日 {dt}，价 {price}）``；
  未知 type 跳过（防御）。

选日规则（D3 并轨口径，2026-10-19 确认门①拍板）：分市场各取
detect_date <= trade_date 中最大 detect_date 当日全部信号（即「最新
扫描批次」，与 build_todays_picks.load_chanlun 一致）。原 dt 口径
只取最近一份已完结信号，缠论买卖点稀疏下 A 股实际停在旧批次
（2026-10-08，信号量仅 picks 链 3%）。保留 QA-20261017 的分市场
分组修正（美股 detect_date 较新时不遮蔽 A 股）。

降级语义：池文件缺失 / 全部行损坏 / 无匹配信号 → 打印日志返回 []，
绝不 raise 到调用方。
"""
import glob
import json
import os

from typing import Any, Dict, List, Optional

from ..signal import Signal

# 项目数据目录
_QB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(_QB_ROOT, "data")
DEFAULT_POOL_GLOBS = [
    os.path.join(DATA_DIR, "chanlun_pool.jsonl"),
    os.path.join(DATA_DIR, "chanlun_pool_us.jsonl"),
    os.path.join(DATA_DIR, "chanlun_pool_hk.jsonl"),
]

# 买卖点类型 → 分位基表（score 基准）
_TYPE_BASE_SCORE: Dict[str, float] = {
    "1买": 95.0, "2买": 85.0, "3买": 75.0,
    "3卖": 25.0, "2卖": 15.0, "1卖": 5.0,
}

# 买卖点类型 → action
_TYPE_ACTION: Dict[str, str] = {
    "1买": "buy", "2买": "buy", "3买": "buy",
    "1卖": "sell", "2卖": "sell", "3卖": "sell",
}

# 参与共振加成的影子标注字段
_RESONANCE_FIELDS = ("mbei", "bei", "bei2", "vv")

# 毒层降档（Δ4，确认门① A 方案）：毒层买点 score 封顶值。
# 低于正常买点分位基表下限（3买=75），保证毒层票在融合池内低分沉底
# 但不移出（action 仍 buy，语义同 picks 链 T2 单策略通道）。
TOXIC_SCORE_CAP = 60.0
# 毒层降档总开关（False = 一键回退到不过滤行为，对齐 picks 链
# TOXIC_EXCLUDE 模式）。
TOXIC_DOWNGRADE = True


class ChanMonitorAdapter:
    """缠论信号池 → 标准信号。

    Args:
        pool_globs: 信号池 JSONL 路径（或 glob）列表；默认三条市场池。
            测试直接传临时文件路径即可 mock 产出内容。
    """

    strategy_id = "chan_monitor"

    def __init__(self, pool_globs: Optional[List[str]] = None) -> None:
        self._pool_globs = list(pool_globs) if pool_globs else list(DEFAULT_POOL_GLOBS)

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def score(self, trade_date: str) -> List[Signal]:
        """给定交易日（YYYYMMDD），返回最近一份已完结缠论信号。"""
        try:
            rows = self._load_rows()
            if not rows:
                print(f"[chan_monitor] 降级：信号池无可用记录"
                      f"({self._pool_globs})")
                return []
            picked = self._pick_rows(rows, trade_date)
            if not picked:
                print(f"[chan_monitor] 降级：{trade_date} 之前无信号")
                return []
            out: List[Signal] = []
            for row in picked:
                sig = self._convert(row, trade_date)
                if sig is not None:
                    out.append(sig)
            out.sort(key=lambda s: -s.score)
            return out
        except Exception as exc:
            print(f"[chan_monitor] 降级：打分失败，返回空信号列表: {exc}")
            return []

    # ------------------------------------------------------------------ #
    # 读取信号池
    # ------------------------------------------------------------------ #
    def _pool_files(self) -> List[str]:
        """展开 glob 并去重排序；结果可能为空（降级由调用方处理）。"""
        files: List[str] = []
        for g in self._pool_globs:
            files.extend(glob.glob(g))
        return sorted(set(files))

    def _load_rows(self) -> List[Dict[str, Any]]:
        """读全部信号池 JSONL 行；坏行跳过，全部失败返回 []。"""
        rows: List[Dict[str, Any]] = []
        files = self._pool_files()
        if not files:
            return []
        ok_files = 0
        for path in files:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    got = 0
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue  # 单行损坏不致命
                        if isinstance(obj, dict):
                            rows.append(obj)
                            got += 1
                if got:
                    ok_files += 1
            except OSError as exc:
                print(f"[chan_monitor] 跳过不可读信号池 {path}: {exc}")
        if not ok_files:
            print(f"[chan_monitor] 降级：全部信号池读取失败: {files}")
        return rows

    # ------------------------------------------------------------------ #
    # 选日与转换
    # ------------------------------------------------------------------ #
    @staticmethod
    def _pick_rows(rows: List[Dict[str, Any]],
                   trade_date: str) -> List[Dict[str, Any]]:
        """分市场各取 detect_date <= trade_date 中最大 detect_date 的全部信号行。

        D3 并轨口径（2026-10-19 确认门①拍板，替代原 dt 口径）：选日
        基准由 dt（信号日）改 detect_date（扫描批次日），与
        build_todays_picks.load_chanlun 的「最新扫描批次」对齐——dt 口径
        只取最近一份已完结信号，买卖点稀疏下会停在旧批次（A 股实证仅
        picks 链 3% 量）。保留 QA-20261017 的分市场分组修正：
        - 市场 = code 后缀（.SZ/.SH/.BJ 归 A 股组 CN；其余各归各组）；
        - 每组独立取组内最大 dt ≤ trade_date 的全部行，结果合并；
        - 某组无 dt ≤ trade_date 的行则该组返回空（不影响其他组）；
        - 行缺 detect_date / 非 YYYY-MM-DD 格式 / 缺 code 的一律忽略。
        """
        def det_key(row: Dict[str, Any]) -> str:
            d = row.get("detect_date")
            if not isinstance(d, str) or len(d) != 10 or d[4] != "-":
                return ""
            return d.replace("-", "")

        def market_of(row: Dict[str, Any]) -> str:
            code = row.get("code")
            if not isinstance(code, str) or "." not in code:
                return "NA"
            suffix = code.rsplit(".", 1)[-1].upper()
            return "CN" if suffix in ("SZ", "SH", "BJ") else suffix

        cutoff = trade_date
        best_by_mkt: Dict[str, str] = {}
        for r in rows:
            k = det_key(r)
            if not k or k > cutoff:
                continue
            mkt = market_of(r)
            if k > best_by_mkt.get(mkt, ""):
                best_by_mkt[mkt] = k
        if not best_by_mkt:
            return []
        out: List[Dict[str, Any]] = []
        for mkt, best in best_by_mkt.items():
            out.extend(r for r in rows
                       if det_key(r) == best and market_of(r) == mkt)
        return out

    @staticmethod
    def _toxic_reason(sig_type: str, row: Dict[str, Any]) -> str:
        """毒层买点判定（口径同 picks 链 _chanlun_toxic）；非毒层返回 ""。

        毒层 = 3买∧bei=False（盘整背驰明确不成立）或 2买∧trend=range。
        bei=None（不可判）/ trend 缺失不判毒（保守方向，与 picks 链
        P4 fail-closed 哲学一致）。卖点不判毒。
        """
        if sig_type == "3买" and row.get("bei") is False:
            return "3买∧bei=False"
        if sig_type == "2买" and row.get("trend") == "range":
            return "2买∧trend=range"
        return ""

    def _convert(self, row: Dict[str, Any],
                 trade_date: str) -> Optional[Signal]:
        """单行信号池记录 → Signal；未知 type / 缺关键字段返回 None。"""
        sig_type = row.get("type")
        if sig_type not in _TYPE_BASE_SCORE:
            return None
        code = row.get("code")
        if not isinstance(code, str) or not code.strip():
            return None

        base = _TYPE_BASE_SCORE[sig_type]
        bonus = sum(2 for f in _RESONANCE_FIELDS if row.get(f) is True)
        score = min(base + bonus, 100.0)

        conf = 0.6 + sum(0.1 for f in _RESONANCE_FIELDS if row.get(f) is True)
        conf = min(conf, 0.9)

        tone = row.get("trend") or row.get("reg") or ""
        tone_part = f"·{tone}" if tone else ""
        rationale = (f"缠论{sig_type}{tone_part}：信号日 {row.get('dt')}，"
                     f"价 {row.get('price')}")

        # 毒层降档（Δ4，确认门① A 方案）：score 封顶、action 不变
        # （buy 仍 buy，池内低分沉底，语义同 picks 链「T1 共振不计票、
        # 票仍走 T2」）；rationale 追加原因标记供后验分层。
        toxic_reason = self._toxic_reason(sig_type, row)
        if toxic_reason and TOXIC_DOWNGRADE:
            score = min(score, TOXIC_SCORE_CAP)
            rationale += f"（毒层降档：{toxic_reason}）"

        return Signal(
            symbol=code.strip(),
            action=_TYPE_ACTION[sig_type],
            score=round(score, 2),
            confidence=round(conf, 2),
            rationale=rationale,
            strategy=self.strategy_id,
            trade_date=trade_date,
            dt=row.get("dt") if isinstance(row.get("dt"), str) else None,
        )


# ---------------------------------------------------------------------- #
# 真实冒烟（只读本地信号池文件；手动执行，不进 pytest）
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    import datetime as _dt

    today = _dt.date.today().strftime("%Y%m%d")
    adapter = ChanMonitorAdapter()
    sigs = adapter.score(today)
    print(f"chan_monitor smoke: {len(sigs)} signals @ {today}")
    for s in sigs[:5]:
        print(f"  {s.symbol} {s.action} score={s.score} "
              f"conf={s.confidence} | {s.rationale}")
