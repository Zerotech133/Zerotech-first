# -*- coding: utf-8 -*-
"""弱于板块案例跟踪监测（K35-b §4/§6 命令块⑦ · 每日盘后）。

每日流程：
  A. 三线现价 vs 关键位（个股 pivots 显式；板块/大盘支撑=近 60 日 low，
     板块前高=近 120 日 high 自动取）；
  B. 日线趋势三线（20/60 双均线：多=last>ma20>ma60，空=last<ma20<ma60）；
  C. 250 日超额现值（个股-板块，与 /bt-linkage 同源口径）；
  D. §6 复评触发判定：① 超额收敛至 -5pp 以内 ② 池内新信号
     （detect_date > --since）③ 归因反转=人工项不机查 ④ 破左侧关键位
     （px < pivots[0]，§5 情景 B 前置——20261014 补机查，破位此前
     只展示不触发，复评链不会被激活=首案结案卡点）。

板块日线滞后时自动「分钟尾巴刷新 → 30m 聚合补日」。
状态追加 reports/weak_track_<code6>.jsonl（每日一行）。
退出码：0=正常跟踪 / 1=触发复评（接 alert_fail_wrap.py 推送）。

用法:
  python weak_track_check.py 301216 --pivots 16.12,16.80,17.93 --since 2026-10-02
  python weak_track_check.py 600519.SH --pivots 1500,1600 --since 2026-10-02

注意（P1-3）：--pivots / --since 为个案参数（来自 start_weak_track.ps1
显式传参），不再提供默认值——新案例忘传参直接报错退出，防止静默跑错案例。
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import linkage
import weak_track_summary as wts  # noqa: E402  （共用 valid_day 日期校验）

POOL = os.path.join(HERE, "data", "chanlun_pool.jsonl")
OUT_DIR = os.path.join(os.path.dirname(HERE), "reports")


from codes import normalize_code as _norm  # noqa: E402
import logging

TRIG_EX_TH = -5  # §6 触发1 阈值：250 日超额收敛至 -5pp 以内


def stood_on(px, pivot):
    """关键位判定：现价是否站上关键位（>=，边界相等=站上）。

    对应 §4 三线表「已站上/未站上」与「破关键位」口径：px < pivot
    即跌破关键位。
    """
    return px >= pivot


def trig_ex_converge(ex, th=TRIG_EX_TH):
    """§6 复评触发1：250 日超额收敛至 th 以内（>=，边界相等=触发）。

    ex=None（超额不可算）不触发。
    """
    return ex is not None and ex >= th


def trig_pivot_break(px, pivot):
    """§6 触发4：破左侧关键位（px < pivot，边界相等=未破）。

    px 或 pivot 缺失不触发。§5 情景 B 前置条件（破 16.12 类破位）
    补进机查——此前破位只在 §4 打印展示，不进 trig，链路零感知。
    """
    return px is not None and pivot is not None and px < pivot


def _last_px_d(fp):
    """状态流末条合法 px_d（无流/无合法值 → 空串）。"""
    last = ""
    if not os.path.exists(fp):  # 首次跟踪时流还不存在，属正常
        return last
    try:
        with open(fp, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if wts.valid_day(r.get("px_d")):
                    last = str(r["px_d"])[:10]
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "quant_backtest/weak_track_check.py", exc
        )
    return last


def _guard_px_d(px_d, fp):
    """坏日期拒写：px_d 非法时回退状态流末条合法值，绝不把坏串落盘。

    行情缓存 bar 的 dt 可能被写坏（月/日越界、不存在的日期），而下游
    weak_track_close.nd 按 px_d 去重算「跟踪 N 日」——多日塌成同一个
    坏串会低估天数、连带台账时长失真。落盘前设闸：
      * 非法值不写，沿用该流上一合法交易日；取不到则落 "?"；
      * 缺失值（空/"?"）不算坏数据，静默沿用，不刷告警；
      * 合法但小于上一合法日 = 倒退，仍照写但告警（可能是数据源回滚，
        不阻断采集，异常交给 §13 审计的「日期倒退」项跟踪）。

    返回 (落盘用 px_d, 被拦截的原始坏值或 None)；原始值仅用于审计追溯。
    """
    s = str(px_d or "").strip()
    last = _last_px_d(fp)
    if wts.valid_day(s):
        s = s[:10]
        if last and s < last:  # 只告警不阻断：倒退多为缓存回滚
            print("\n[warn] px_d 倒退：%s → %s（行情缓存可能回滚/陈旧）" % (last, s))
        return s, None
    missing = s in ("", "?")
    print(
        "\n%s px_d=%r → %s"
        % (
            "[warn] 行情源日期非法：" if not missing else "[info] px_d 缺失：",
            px_d,
            "沿用上一合法日 %s" % last if last else "无可沿用值，落 ?",
        )
    )
    return (last or "?"), (None if missing else s)


def trig_pool_new_sig(detect_date, since):
    """§6 复评触发2：池内新信号——detect_date 严格大于 since 才算新
    （边界相等=since 当日已有，不算新）。"""
    return str(detect_date or "") > str(since or "")


def _closes(bars):
    return [float(b.get("close", b.get("c"))) for b in bars]


def trend_tag(closes):
    if len(closes) < 60:
        return "样本不足"
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60
    last = closes[-1]
    if last > ma20 > ma60:
        return "多"
    if last < ma20 < ma60:
        return "空"
    return "平（均线缠绕）"


def block_daily_bc(bc, refresh=True):
    """板块日线，本地滞后时刷分钟尾巴并 30m 聚合补日。"""
    if refresh:
        try:
            linkage.refresh_min_tail(quiet=True)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_check.py", exc)
    bb = linkage.block_bars(bc, "1d", 500)
    have = bb[-1]["dt"] if bb else ""
    mm = linkage.block_bars(bc, "30m", 480)
    agg = {}
    for b in mm:
        agg[b["dt"][:10]] = b["close"]
    extra = [{"dt": d, "close": c} for d, c in sorted(agg.items()) if d > have]
    return bb + extra, len(extra)


def main():
    ap = argparse.ArgumentParser(description="弱于板块案例跟踪监测")
    ap.add_argument("code", nargs="?", default="301216")
    # P1-3：个案参数必填（唯一自动化调用方 start_weak_track.ps1 恒显式传参，
    # 已核对计划任务注册链 WeakTrackCheck → start_weak_track.ps1）。
    ap.add_argument(
        "--pivots", required=True, help="个股关键位,逗号分隔（个案参数，必填）"
    )
    ap.add_argument(
        "--since", required=True, help="立案日(池信号扫描起点，个案参数，必填)"
    )
    ap.add_argument("--no-refresh", action="store_true")
    a = ap.parse_args()
    code = _norm(a.code)
    pivots = [float(x) for x in str(a.pivots).split(",") if x.strip()]
    trig = []

    from chanlun_data import load_kline

    blk = linkage.resolve_block(code)
    if not blk:
        print("板块解析失败")
        return 2
    bc, _bname = blk["code"], blk["name"]

    # --- A/B. 三线现价+关键位+趋势 ---
    sk = load_kline(code, "1d", 500)
    sb = sk.get("bars") or []
    ik = load_kline("000001.SH", "1d", 500)
    ib = ik.get("bars") or []
    bd, n_ext = block_daily_bc(bc, refresh=not a.no_refresh)

    px = _closes(sb)[-1] if sb else None
    px_d = sb[-1]["dt"][:10] if sb else "?"
    bpx = _closes(bd)[-1] if bd else None
    bpx_d = bd[-1]["dt"][:10] if bd else "?"
    ipx = _closes(ib)[-1] if ib else None
    print(
        "=== A/B. 三线现价 × 关键位 × 日线趋势（个股 %s / 板块 %s %s / 上证）==="
        % (px_d, bpx_d, bc)
    )
    if px:
        near = min(pivots, key=lambda p: abs(p - px))
        print(
            "  个股 %.2f｜关键位 %s｜最近 %s（%+.2f%%）｜趋势 %s"
            % (
                px,
                "/".join("%.2f" % p for p in pivots),
                near,
                (px / near - 1) * 100,
                trend_tag(_closes(sb)),
            )
        )
    if bpx and len(bd) >= 60:
        lo = min(float(b.get("low", b.get("close", 0)) or b["close"]) for b in bd[-60:])
        hi = max(
            float(b.get("high", b.get("close", 0)) or b["close"]) for b in bd[-120:]
        )
        print(
            "  板块 %.2f｜前低(60日) %.2f（%+.2f%%）｜前高(120日) %.2f｜趋势 %s%s"
            % (
                bpx,
                lo,
                (bpx / lo - 1) * 100,
                hi,
                trend_tag(_closes(bd)),
                "（%d 日由分钟聚合补尾）" % n_ext if n_ext else "",
            )
        )
    if ipx:
        ilo = min(float(b.get("l", 0) or b.get("c")) for b in ib[-60:])
        print(
            "  上证 %.2f｜前低(60日) %.2f（%+.2f%%）｜趋势 %s"
            % (ipx, ilo, (ipx / ilo - 1) * 100, trend_tag(_closes(ib)))
        )

    # 5 分钟检查项（§4 第三行）
    s5 = load_kline(code, "5m", 60)
    b5 = linkage.block_bars(bc, "5m", 60)
    if s5.get("bars") and b5:
        s5c, b5c = _closes(s5["bars"]), _closes(b5)
        ma20 = sum(b5c[-20:]) / 20
        print(
            "  [5m] 个股 %.2f vs 站位 %.2f：%s｜板块 %.2f vs 5m MA20 %.2f：%s"
            % (
                s5c[-1],
                pivots[min(1, len(pivots) - 1)],
                "已站上"
                if stood_on(s5c[-1], pivots[min(1, len(pivots) - 1)])
                else "未站上",
                b5c[-1],
                ma20,
                "已站上" if b5c[-1] >= ma20 else "未站上",
            )
        )

    # --- C. 250 日超额 ---
    r = linkage.series(code, "1d", limit=250, block=bc)
    ex = None
    if r.get("ok") and r.get("block"):
        ex = round(r["stock"][-1] - r["block"][-1], 1)
        print(
            "\n=== C. 250 日超额（个股-板块）：%+.1fpp（个股 %+.1f%% vs 板块"
            " %+.1f%%）===" % (ex, r["stock"][-1], r["block"][-1])
        )

    # --- D. §6 三触发 ---
    print("\n=== D. §6 复评触发判定 ===")
    if trig_ex_converge(ex):
        trig.append("超额收敛至 %+.1fpp（≥-5pp，触发1）" % ex)
    if trig_pivot_break(px, pivots[0] if pivots else None):
        trig.append("破左侧关键位 %.2f（现价 %.2f，情景B前置·触发4）" % (pivots[0], px))
    new_sig = []
    if os.path.exists(POOL):
        with open(POOL, encoding="utf-8") as f:
            for line in f:
                if code.split(".")[0] not in line:
                    continue
                try:
                    q = json.loads(line)
                except Exception:
                    continue
                if str(q.get("code", "")).startswith(
                    code.split(".")[0]
                ) and trig_pool_new_sig(q.get("detect_date", ""), a.since):
                    new_sig.append(
                        "%s %s@%s"
                        % (q.get("detect_date"), q.get("type"), q.get("price"))
                    )
    if new_sig:
        trig.append("池内新信号：%s（触发2）" % "; ".join(new_sig))
    print(
        "  触发1（超额收敛≥-5pp）：%s"
        % (
            "命中 %+.1fpp" % ex
            if trig_ex_converge(ex)
            else ("未命中（%+.1fpp）" % ex if ex is not None else "超额不可算")
        )
    )
    print(
        "  触发2（池内新信号，detect>%s）：%s"
        % (a.since, "；".join(new_sig) if new_sig else "无")
    )
    print("  触发3（归因反转）：人工项，不机查")
    print(
        "  触发4（破左侧关键位 %s）：%s"
        % (
            "%.2f" % pivots[0] if pivots else "—",
            "命中：%.2f < %s" % (px, "%.2f" % pivots[0])
            if trig_pivot_break(px, pivots[0] if pivots else None)
            else ("未命中（现价 %.2f）" % px if px else "现价不可算"),
        )
    )
    # 状态统一构造（推送+落盘共用）；fp 提前确定以便坏日期回退时读历史流
    os.makedirs(OUT_DIR, exist_ok=True)
    fp = os.path.join(OUT_DIR, "weak_track_%s.jsonl" % code.split(".")[0])
    px_d, px_d_bad = _guard_px_d(px_d, fp)
    state = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "code": code,
        "px": px,
        "px_d": px_d,
        "ex250": ex,
        "trend": [
            trend_tag(_closes(sb)),
            trend_tag(_closes(bd)),
            trend_tag(_closes(ib)),
        ],
        "trig": trig,
    }
    if px_d_bad:  # 被拦截的原始坏值留痕，供 §13 审计追溯
        state["px_d_bad"] = px_d_bad
    if trig:
        print("  >>> 触发复评！按 §6 重跑模板（执行 SOP 见 §8）。")
        # 推送主通路：飞书卡片版（默认；环境变量 WEAK_TRACK_TEXT=1 回退文本）
        try:
            from weak_track_card import alert_card, send_card

            if os.environ.get("WEAK_TRACK_TEXT"):
                from weak_track_alert import build_alert, send_alert

                send_alert(build_alert(code, state))
            else:
                send_card(alert_card(state))
            print("  §8 推送已发送（重发：weak_track_alert.py %s --send）" % code)
        except Exception as e:
            print(
                "  §8 推送发送失败（可用 weak_track_alert.py --send 补发）：%s"
                % str(e)[:80]
            )
    else:
        print("  >>> 无触发，继续影子跟踪。")

    # 状态落盘（fp 已在构造 state 前确定）
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps(state, ensure_ascii=False) + "\n")
    print("\n状态已追加 %s" % fp)
    return 1 if trig else 0


if __name__ == "__main__":
    raise SystemExit(main())
