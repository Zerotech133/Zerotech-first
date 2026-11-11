# -*- coding: utf-8 -*-
# =============================================================================
# 【已停用 2026-11-05】计划任务 GoldShadow 已 Enabled=False（State=Disabled）。
# 停用原因：验收标准数学不可达，非故障。
#   - 沪金 b3 频率 1.76 个/年（18.8 年 4,572 根日线全历史仅 33 个）
#   - 检测 d5 超额 +0.90% 需 155 样本（α=0.05, power=80%）= 88 年
#   - 部署 2026-10-05 至 2026-11-04，每日 18:30 均 exit=0，累计 journal=0
# 恢复条件：只有换到信号频率 ≥ 数十个/年的标的才值得重启影子期；沪金不满足。
# 依据：deliverables/2026-11-05_沪金b3信号_实时可见性_v7_边际证据判定.md
#
# 【已修 2026-11-07 · P2-a】面板结算前视已改正：
#   旧实现 i0 = idx.get(j["dt"])（结构端点日）→ entry = opens[i0+1]，等于提前
#   1~3 日知道端点（detect_date 与 dt 之差，中位 1 日、最大 3 日）。
#   实测影响（v7 逐日截断重放，n=33）：事后 d20 +2.64% → 实时 +1.85%，
#   前视虚增 0.80pp；K226 报的 d5 胜率 97.0% 在实时口径降至 75.8%。
#   现在：settle_anchor() 按 detect_date 定位，anchor = 第一个 >= detect_date 的
#   交易日，entry = opens[anchor+1]；detect_date 非交易日（周末/停牌）时取之前
#   最后一根 bar 作锚，入场仍是其后第一根。
#   旧 journal 行若缺 detect_date，退回 dt 结算并在面板标 settle_basis=legacy_dt，
#   审计时一眼可辨，不再用 raise 一刀切（原防线会崩掉整日任务）。
#   注意：改动后旧面板数字不可与新面板混读，重启影子期须重新起算。
# =============================================================================
"""gold_signal_shadow: 沪金 3买/3卖 影子标注（K227，structure-signal-shadow-eval
流程第二阶段）。

每日 18:30 任务 GoldShadow → 全史重跑缠论（与 K226 影子统计同引擎同口径）
→ 检出 dt >= JOURNAL_START 的新增 b3/s3（键 (t,dt) 幂等）→ 落 jsonl 日志
（记录 detect_date 与笔终点日 dt——实测确认滞后 = 两者之差）→ 面板 json
原子再生（L=1 可成交口径 d5/10/20 跟踪：entry=detect_date 后第 1 根开盘价，
即「看见信号的第一个交易时点」，不用结构端点日 dt）。

影子纪律：只记不推，不改任何信号/推送/入池口径；影子期面板对照达标才谈
接入（structure-signal-shadow-eval 第三阶段）。历史信号（dt < JOURNAL_START）
不回填——影子期从部署日起算，天然 out-of-sample。

状态机：pending_entry（进场 bar 未到）→ tracking（entry 已知，d20 未满）
→ done（d20 已知）。重复运行幂等（无新信号即只刷面板）。
退出码：0 正常；1 异常（包装脚本 alert_fail 告警）。
"""

import bisect
import json
import os
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gold_chanlun_shadow import build_continuous, SYM  # noqa: E402
from chanlun_bi import analyze  # noqa: E402

JOURNAL = os.path.join(HERE, "data", "gold_signal_journal.jsonl")
PANEL = os.path.join(HERE, "data", "gold_signal_panel.json")
JOURNAL_START = "2026-10-05"  # 部署日：dt 早于此的历史信号不回填（K226 已统计）
HOLDS = (5, 10, 20)
WATCH = ("b3", "s3")


def _atomic_write(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        fp.write(text)
    os.replace(tmp, path)


def settle_anchor(dts, j):
    """结算定位（P2-a）：返回 (anchor_i, basis, lag_bars)。

    实时口径：入场只能发生在"第一次看见该信号"之后。
      · 有 detect_date：anchor = 第一个 >= detect_date 的交易日；
        若 detect_date 本身不是交易日（周末/停牌），anchor 取它之前最后一根，
        入场仍是 anchor+1（即 detect_date 之后的第一个交易日）。
      · 无 detect_date（旧 journal 行）：退回 dt，basis=legacy_dt 供审计。
    lag_bars = detect_date 相对 dt 滞后了几根 bar（无数据时为 None）。
    """
    dd = j.get("detect_date")
    dt_i = dts.index(j["dt"]) if j.get("dt") in dts else None
    if not dd:
        return dt_i, "legacy_dt", None
    k = bisect.bisect_left(dts, dd)
    anchor = k if (k < len(dts) and dts[k] == dd) else k - 1
    if anchor < 0:
        return None, "detect_date", None
    lag = (k - dt_i) if (dt_i is not None and k < len(dts)) else None
    return anchor, "detect_date", lag


def _load_journal():
    out = []
    if not os.path.exists(JOURNAL):
        return out
    with open(JOURNAL, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def _append_journal(entries):
    with open(JOURNAL, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    df = build_continuous(SYM, 20)
    if len(df) < 60:
        print("bars insufficient")
        return 1
    dts = [
        "%s-%s-%s" % (str(v)[:4], str(v)[4:6], str(v)[6:8]) for v in df["trade_date"]
    ]
    bars = [
        {"dt": dts[i], "h": float(r["high"]), "l": float(r["low"])}
        for i, (_, r) in enumerate(df.iterrows())
    ]
    closes = [float(r["close"]) for _, r in df.iterrows()]
    opens = [float(r["open"]) for _, r in df.iterrows()]
    st = analyze(bars, closes)
    idx = {b["dt"]: i for i, b in enumerate(bars)}

    journal = _load_journal()
    known = {(j["type"], j["dt"]) for j in journal}

    new = []
    for s in st["signals"]:
        if s["t"] not in WATCH or s["d"] < JOURNAL_START:
            continue
        if (s["t"], s["d"]) in known:
            continue
        new.append(
            {
                "type": s["t"],
                "dt": s["d"],
                "price": s.get("p"),
                "detect_date": today,
                "first_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    if new:
        _append_journal(new)
        journal = _load_journal()

    # 面板：全量 journal × 最新行情 → 可成交口径跟踪
    entries = []
    for j in journal:
        e = dict(j)
        # P2-a（2026-11-07）：按 detect_date 定位入场，不再用结构端点日 dt。
        # 见文件头「已修」段；旧行缺 detect_date 时退回 dt 并标 legacy_dt。
        i0, _basis, _lag = settle_anchor(dts, j)
        e["settle_basis"] = _basis
        e["confirm_lag_bars"] = _lag
        e["status"] = "pending_entry"
        if i0 is not None and i0 + 1 < len(opens) and opens[i0 + 1]:
            e["entry_date"] = dts[i0 + 1]
            e["entry_px"] = opens[i0 + 1]
            entry = opens[i0 + 1]
            sign = 1.0 if j["type"] == "b3" else -1.0
            e["status"] = "tracking"
            for h in HOLDS:
                j2 = i0 + 1 + h
                if j2 < len(closes) and closes[j2]:
                    e["d%d" % h] = round(sign * (closes[j2] / entry - 1.0) * 100, 2)
            if all(("d%d" % h) in e for h in HOLDS):
                e["status"] = "done"
        entries.append(e)
    done = [e for e in entries if e["status"] == "done"]
    d20 = [e["d20"] for e in done]
    summary = {
        "n_journal": len(entries),
        "n_done": len(done),
        "n_tracking": sum(1 for e in entries if e["status"] == "tracking"),
        "n_pending": sum(1 for e in entries if e["status"] == "pending_entry"),
        "d20_win": round(sum(1 for x in d20 if x > 0) / len(d20) * 100, 1)
        if d20
        else None,
        "d20_avg": round(sum(d20) / len(d20), 2) if d20 else None,
    }
    panel = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": SYM,
        "journal_start": JOURNAL_START,
        "settle_rule": "entry = detect_date 后第一根开盘（P2-a 实时口径）；"
                       "legacy_dt 行为旧口径结果，不可与新行混算",
        "summary": summary,
        "entries": entries,
    }
    os.makedirs(os.path.dirname(PANEL), exist_ok=True)
    _atomic_write(PANEL, json.dumps(panel, ensure_ascii=False, indent=1))
    print(
        "gold shadow: new=%d journal=%d done=%d tracking=%d pending=%d"
        % (
            len(new),
            len(entries),
            summary["n_done"],
            summary["n_tracking"],
            summary["n_pending"],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
