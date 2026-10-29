# -*- coding: utf-8 -*-
"""kg 数据/写入 helper（label/card save+diff）（P4 api_kg 拆分第一批：自 api_kg.py 纯搬运，零行为变化）。"""
import json
import os
import sys
import time
import os as _os
from api_common import (
    QBT_DIR,
    pill_nav,
    _BTSEL_TAG,
    _PILL, _PILL_ON,
    CANDLE_CN,
    _LABELS_FP, _CARD_FB_FP)
import logging



def _kg_label_save(rec):
    """v2.5 标注落盘（data/labels/chanlun_labels.jsonl，纯文件不写图）。

    幂等：code+period+last_dt 相同覆盖旧行；返回 diff 摘要供前端即时显示。
    """
    code = str(rec.get("code") or "").strip()
    period = str(rec.get("period") or "").strip()
    last_dt = str(rec.get("last_dt") or "").strip()
    user = rec.get("user") or {}
    algo = rec.get("algo") or {}
    if not (code and period and last_dt):
        return {"error": "code/period/last_dt 必填"}
    if not (isinstance(user.get("bis"), list)
            and isinstance(user.get("pivots"), list)
            and isinstance(user.get("bs"), list)):
        return {"error": "user 结构需含 bis/pivots/bs 数组"}
    os.makedirs(os.path.dirname(_LABELS_FP), exist_ok=True)
    key = (code, period, last_dt)
    rows = []
    try:
        with open(_LABELS_FP, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if (r.get("code"), r.get("period"),
                        r.get("last_dt")) != key:
                    rows.append(r)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_kg.py", exc)
    diff = _kg_label_diff(algo, user)
    rows.append({"code": code, "period": period, "last_dt": last_dt,
                 "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "algo": algo, "user": user, "diff": diff})
    with open(_LABELS_FP, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"ok": True, "n_rows": len(rows), "diff": diff}


def _kg_label_diff(algo, user):
    """v2.5 标注 diff（算法 vs 人工）——系统性偏差定位。

    口径：笔按 e_dt 配对（新增/删除/端点位移%>0.5 计移动），
    买卖点按 dt+type 配对，中枢按 s_dt+e_dt 配对（zg/zd 变化%）。
    """
    a_bis = algo.get("bis") or []
    u_bis = user.get("bis") or []
    am = {str(b.get("e_dt")): b for b in a_bis}
    um = {str(b.get("e_dt")): b for b in u_bis}
    bi_add = sorted(set(um) - set(am))
    bi_del = sorted(set(am) - set(um))
    moved, shifts = 0, []
    for k in set(am) & set(um):
        a, u = am[k], um[k]
        mx = max(
            abs(u.get("s_price", 0) - a.get("s_price", 0))
            / max(abs(a.get("s_price", 1)), 1e-9),
            abs(u.get("e_price", 0) - a.get("e_price", 0))
            / max(abs(a.get("e_price", 1)), 1e-9)) * 100
        if mx > 0.5:
            moved += 1
            shifts.append(round(mx, 2))
    a_bs = {"%s|%s" % (s.get("dt"), s.get("type")) for s in algo.get("bs") or []}
    u_bs = {"%s|%s" % (s.get("dt"), s.get("type")) for s in user.get("bs") or []}
    bs_add = sorted(u_bs - a_bs)
    bs_del = sorted(a_bs - u_bs)
    a_pv = {"%s|%s" % (p.get("s_dt"), p.get("e_dt")): p
            for p in algo.get("pivots") or []}
    u_pv = {"%s|%s" % (p.get("s_dt"), p.get("e_dt")): p
            for p in user.get("pivots") or []}
    pv_shift = []
    for k in set(a_pv) & set(u_pv):
        for f_ in ("zg", "zd"):
            if a_pv[k].get(f_) and u_pv[k].get(f_):
                pv_shift.append(round(
                    abs(u_pv[k][f_] - a_pv[k][f_])
                    / max(abs(a_pv[k][f_]), 1e-9) * 100, 2))
    return {"bi_algo_n": len(a_bis), "bi_user_n": len(u_bis),
            "bi_added": bi_add, "bi_removed": bi_del, "bi_moved": moved,
            "bi_max_shift_pct": max(shifts) if shifts else 0,
            "bs_added": bs_add, "bs_removed": bs_del,
            "pivot_algo_n": len(a_pv), "pivot_user_n": len(u_pv),
            "pivot_max_shift_pct": max(pv_shift) if pv_shift else 0,
            "summary": ("笔 %d→%d（人 +%d/-%d/移 %d）· 中枢 %d→%d · "
                        "买卖点 %d→%d（人 +%d/-%d）· 端点最大位移 %.2f%%"
                        % (len(a_bis), len(u_bis), len(bi_add), len(bi_del),
                           moved, len(a_pv), len(u_pv), len(a_bs), len(u_bs),
                           len(bs_add), len(bs_del),
                           max(shifts) if shifts else 0))}


def _kg_card_fb_save(rec):
    """K29 推演卡三键校对反馈落盘（对/偏/错 → card_feedback.jsonl）。

    幂等：code+period+last_dt 相同覆盖旧行（改判重标）；verdict ∈
    right/partial/wrong；note 可选。纯文件，供推演卡质量迭代统计。
    """
    import time as _t
    code = str(rec.get("code") or "").strip()
    period = str(rec.get("period") or "").strip()
    last_dt = str(rec.get("last_dt") or "").strip()
    verdict = str(rec.get("verdict") or "").strip()
    if not (code and period and last_dt
            and verdict in ("right", "partial", "wrong")):
        return {"error": "code/period/last_dt/verdict(right|partial|wrong) 必填"}
    row = {"code": code, "period": period, "last_dt": last_dt,
           "verdict": verdict, "note": str(rec.get("note") or "")[:200],
           "ts": _t.strftime("%Y-%m-%dT%H:%M:%S")}
    os.makedirs(os.path.dirname(_CARD_FB_FP), exist_ok=True)
    key = (code, period, last_dt)
    rows = []
    try:
        with open(_CARD_FB_FP, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if (r.get("code"), r.get("period"),
                        r.get("last_dt")) != key:
                    rows.append(r)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_kg.py", exc)
    rows.append(row)
    tmp = _CARD_FB_FP + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, _CARD_FB_FP)
    return {"ok": True, "n_rows": len(rows), "saved": row}
