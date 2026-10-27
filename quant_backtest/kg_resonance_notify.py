# -*- coding: utf-8 -*-
"""kg_resonance_notify: 三线共振晨报哨兵（K208·E3，K179/K199 哨兵第三例）。

口径（K206 池项拍板，用户「好的，执行吧」；如需改口径改本文件常量）：
  · 每日 08:05 盘前一次（计划任务 KgResonanceNotify），读图内最近同步数据；
  · 主题 = 引擎已入图主题（K216 起：人形机器人、AI 算力、商业航天——
    商业航天冷启动空态即真实状态，标的挂链随研报抽取生长）；
  · 纯信息层只读摘要：三线共振明细 + 两线候选 + 单线计数，不改任何
    信号/推送触发/拦截口径（K206 影子纪律：查询模块只记不推，推送由
    本哨兵独立承担）；
  · 主题顺序：信息量小的先查、人形机器人最后查——影子 last 文件单主题
    语义，把信息量最大的机器人快照留在 last 供 /kg/shadow E2 看板卡
    直读（K217 起商业航天插入中间位）。

实现：直接 import kg_resonance.query()（返回 dict，免解析 stdout），
KgClient 每主题独立开闭。推送经 news_collector/send_feishu.py
（outbox 临时文件 + 子进程，SEND_OK 口径，与 K179/K199 同通道）。

幂等：状态文件 data/kg_resonance_notify_state.json（当日已处理即退，
SEND_OK 才写状态）。全部主题查询失败 → 推失败告警（失败可见），
exit 3；推送失败 exit 4 下次重试。
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
STATE = os.path.join(HERE, "data", "kg_resonance_notify_state.json")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))

THEMES = ("AI 算力", "商业航天", "人形机器人")  # last 查询留机器人（见 docstring 口径）
DAYS = 30
MAX_FULL = 6  # 三线明细条数上限
MAX_PARTIAL_NAMES = 8  # 两线候选仅列名上限


def _today_dashed():
    return datetime.now().strftime("%Y-%m-%d")


def _write_json(path, obj):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _push(text):
    """outbox 临时文件 → send_feishu.py（SEND_OK 口径）。"""
    os.makedirs(os.path.join(NEWS_DIR, "outbox"), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.join(NEWS_DIR, "outbox"), suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        ok = "SEND_OK" in (r.stdout or "")
        print((r.stdout or r.stderr or "").strip()[:100])
        return ok
    except Exception as e:
        print("resonance notify push error: %s" % str(e)[:80])
        return False


def _theme_block(theme, res, err):
    if err:
        return "【%s】❌ 查询失败：%s" % (theme, str(err)[:60])
    full = res.get("full") or []
    partial = res.get("partial") or []
    lines = [
        "【%s】★三线 %d / ☆两线 %d / 单线 %d 不输出"
        % (theme, len(full), len(partial), int(res.get("n_single") or 0))
    ]
    if not full and not partial:
        lines.append("  （冷启动：链骨架已入图，标的挂链随研报抽取生长）")
    for it in full[:MAX_FULL]:
        seg = ""
        if it.get("segs"):
            seg = " @%s" % (it["segs"][0][0] or "")
        star = "★" * min(5, int(it.get("stars") or 0))
        sig = ""
        if it.get("sigs"):
            tp, d, dd = it["sigs"][0][:3]
            sig = "（%s detect %s）" % (tp, dd)
        lines.append(
            "  · %s %s%s%s%s" % (it.get("name"), it.get("code"), seg, star, sig)
        )
    if len(full) > MAX_FULL:
        lines.append("  · …等 %d 条" % len(full))
    if partial:
        names = []
        for it in partial[:MAX_PARTIAL_NAMES]:
            miss = next(
                (
                    ln
                    for ln in ("结构", "传导", "共识")
                    if ln not in (it.get("lines") or [])
                ),
                "?",
            )
            names.append("%s（缺%s）" % (it.get("name"), miss))
        lines.append("  ☆ 两线候选：%s" % "、".join(names))
    # K239 三期：图谱叙事段（kg_digest 影子产物，verified 才上）
    try:
        dgf = os.path.join(HERE, "data", "kg_digest_last.json")
        with open(dgf, encoding="utf-8") as f:
            dg = json.load(f).get("themes", {}).get(theme, {})
        dgx = (dg.get("digest") or "").strip()
        if dgx and dg.get("verified"):
            lines.append("  📖 " + dgx)
        elif dg.get("note"):
            lines.append("  📖 " + dg["note"])
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/kg_resonance_notify.py", exc)
    return "\n".join(lines)


def main():
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
        if st.get("date") == _today_dashed():
            print("already handled today: %s" % st.get("status"))
            return
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/kg_resonance_notify.py", exc)

    sys.path.insert(0, HERE)
    blocks, errs = [], 0
    for theme in THEMES:
        try:
            import kg_resonance as KR

            res = KR.query(theme, DAYS)
            blocks.append(_theme_block(theme, res, None))
        except Exception as e:
            errs += 1
            blocks.append(_theme_block(theme, None, e))

    if errs >= len(THEMES):
        text = (
            "⚠️ 三线共振晨报 · %s\n全部主题查询失败（图谱服务异常？）"
            "——请查 kg 图服务与本任务日志。" % _today_dashed()
        )
        ok = _push(text)
        if ok:
            _write_json(
                STATE,
                {
                    "date": _today_dashed(),
                    "status": "all_query_failed",
                    "ts": datetime.now().isoformat(timespec="seconds"),
                },
            )
            sys.exit(3)
        sys.exit(4)

    text = (
        "🎯 三线共振晨报 · %s（窗口 %d 自然日，图内最近同步数据）\n%s\n"
        "详情 → /kg/shadow（E2 看板卡直读影子快照）"
        % (_today_dashed(), DAYS, "\n".join(blocks))
    )
    ok = _push(text)
    if ok:
        _write_json(
            STATE,
            {
                "date": _today_dashed(),
                "status": "pushed",
                "ts": datetime.now().isoformat(timespec="seconds"),
                "themes": list(THEMES),
            },
        )
        print("state=pushed")
        sys.exit(0)
    sys.exit(4)


if __name__ == "__main__":
    main()
