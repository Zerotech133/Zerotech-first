# -*- coding: utf-8 -*-
"""gold_signal_shadow: 沪金 3买/3卖 影子标注（K227，structure-signal-shadow-eval
流程第二阶段）。

每日 18:30 任务 GoldShadow → 全史重跑缠论（与 K226 影子统计同引擎同口径）
→ 检出 dt >= JOURNAL_START 的新增 b3/s3（键 (t,dt) 幂等）→ 落 jsonl 日志
（记录 detect_date 与笔终点日 dt——实测确认滞后 = 两者之差）→ 面板 json
原子再生（L=1 可成交口径 d5/10/20 跟踪：entry=信号日后第 1 根开盘价）。

影子纪律：只记不推，不改任何信号/推送/入池口径；影子期面板对照达标才谈
接入（structure-signal-shadow-eval 第三阶段）。历史信号（dt < JOURNAL_START）
不回填——影子期从部署日起算，天然 out-of-sample。

状态机：pending_entry（进场 bar 未到）→ tracking（entry 已知，d20 未满）
→ done（d20 已知）。重复运行幂等（无新信号即只刷面板）。
退出码：0 正常；1 异常（包装脚本 alert_fail 告警）。
"""

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
        i0 = idx.get(j["dt"])
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
