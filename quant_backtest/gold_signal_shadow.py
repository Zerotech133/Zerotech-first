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
# 【已知缺陷·未修·重启前必须修】面板结算存在前视：
#   第 107 行 i0 = idx.get(j["dt"]) 用「结构端点日」定位，第 110/112 行按
#   i0+1 开盘入场，忽略了「确认滞后」（detect_date 与 dt 之差，中位 1 日、
#   最大 3 日）。模块自己的 docstring 已记录该滞后存在，但结算未使用它。
#   实测影响（v7 逐日截断重放，n=33）：事后 d20 +2.64% → 实时 +1.85%，
#   前视虚增 0.80pp；K226 报的 d5 胜率 97.0% 在实时口径降至 75.8%。
#   正确做法：i0 = idx.get(j["detect_date"])，entry = opens[i0+1]。
#   当前未改的理由：任务已停用、样本为 0，改动无法被任何数据验证；
#   留待重启影子期时连同标的更换一并修改。
#   已加防线（不改口径）：结算前若 dt != detect_date 直接 raise，任务重启后
#   第一笔样本即崩，避免带着前视继续产出「漂亮」面板。journal 为空，当前不触发。
# =============================================================================
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
        # TODO(前视): 下方用 j["dt"]（结构端点日）定位后按 i0+1 入场，忽略确认滞后，
        # 属前视口径（实测虚增 d20 0.80pp）。重启影子期前应改为 j["detect_date"]。
        # 详见文件头「已知缺陷·未修」段与 v7 报告。
        # 前视守卫（2026-11-05 加）：任务已停用、journal 为空，此守卫当前不触发。
        # 一旦重启影子期且有样本进来，dt != detect_date 会立即报错，而不是继续
        # 产出一份偏乐观的面板。宁可崩，不可静默虚增。
        _dd = j.get("detect_date")
        if _dd and _dd != j["dt"]:
            raise RuntimeError(
                "gold_signal_shadow 结算前视拦截：dt=%s detect_date=%s（滞后 %s 日）。"
                "请先把结算改为按 j['detect_date'] 定位入场（见文件头「已知缺陷·未修」）。"
                % (j["dt"], _dd, j.get("lag", "?"))
            )
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
