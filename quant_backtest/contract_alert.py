# -*- coding: utf-8 -*-
"""契约体检分级告警（05:30 晨检派单，2026-11-13 老田拍板）。

需求原文：「每日检查要放到凌晨五点半，出现问题给对应的部门发信要求
处理，街道信息部门应该立即处理」。落地口径（老田确认）：
  · 派单目标：数据问题 → 量化群（例行）；紧急 → 飞书加急发个人 + 量化群；
  · 紧急定义（分级）：P0 表（日线/复权/交易日历等下游强依赖）出现
    dead/absent/contract_bug/shrink 红项 = 立即；其余红项 = 例行汇总。
  · 「立即」的物理上限：飞书无强制电话能力——加急 = urgent 消息
    （飞书 buzzing 强提醒）+ 个人 + 群双通道。若需电话级，后续接
    值班电话机器人（当前无该通道，如实声明不虚标）。

用法（被 run_contract_health.ps1 或手工调用）：
  python contract_alert.py reports/contract_health.json [--dry]
输出：飞书消息（例行→量化群；紧急→量化群+个人加急）；rc=0 正常 /
1=发送失败（不反噬体检自身）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "news_collector"))

ALERT_STATUSES = ("dead", "absent", "contract_bug", "shrink", "reader_gap")
URGENT_STATUSES = ("dead", "absent", "contract_bug", "shrink")


def classify(doc):
    """体检 JSON → (例行红项, 紧急红项)。紧急 = P0 表 × 红状态。"""
    routine, urgent = [], []
    for r in doc.get("tables") or []:
        if r.get("status") not in ALERT_STATUSES:
            continue
        line = "%-22s %-6s asof=%-9s lag=%-4s 归因=%s" % (
            r.get("table"), r.get("status"), r.get("asof"), r.get("lag"),
            r.get("cause") or "-")
        if r.get("tier") == "P0" and r.get("status") in URGENT_STATUSES:
            urgent.append(line)
        else:
            routine.append(line)
    return routine, urgent


def _send(text, receive_id=None, receive_id_type="open_id", urgent=False):
    """发文本。content 必须是 JSON 字符串（send_text 的口径），传 dict
    会被 lark-cli 兜底分支当 PathLike 用而炸——首跑判例。
    urgent：走 feishu_client.send_msg(critical=True)——K-PUSH-003 语义：
    预警类豁免 8097 闸门的静默时段（夜间/配额），风险提示不可吞。
    05:30 晨检天然落在静默时段，不加 critical 个人加急会被闸门吞掉。"""
    import json as _json
    import feishu_client as FC
    content = _json.dumps({"text": text}, ensure_ascii=False)
    if receive_id is None:
        receive_id, receive_id_type = "", "open_id"
    ok, why = FC.send_msg(content, "text", receive_id or None,
                          receive_id_type, kind="contract_health",
                          critical=urgent)
    return ok


def alert(doc_path, dry=False):
    doc = json.load(open(doc_path, encoding="utf-8"))
    routine, urgent = classify(doc)
    counts = doc.get("counts") or {}
    head = "晨检 %s 基准%s：ok=%s dead=%s absent=%s shrink=%s" % (
        doc.get("run_at", ""), doc.get("base_asof", ""),
        counts.get("ok"), counts.get("dead"), counts.get("absent"),
        counts.get("shrink"))

    import push_targets
    qun = push_targets.target_id("量化群")
    person = push_targets.target_id("个人")

    msgs = []
    if urgent:
        body = head + "\n\n【紧急·P0红项 %d 项，需立即处理】\n%s" % (
            len(urgent), "\n".join(urgent[:10]))
        msgs.append(("量化群(紧急)", qun, "chat_id", body, True))
        msgs.append(("个人(紧急)", person, "open_id", body, True))
    if routine or not urgent:
        body2 = head + ("\n\n例行红项 %d 项：\n%s" % (len(routine),
                                               "\n".join(routine[:12]))
                        if routine else "\n无红项，数据面正常。")
        if not urgent:
            msgs.append(("量化群(例行)", qun, "chat_id", body2, False))
        else:
            # 紧急时例行项并入同一条，避免刷屏
            msgs[0] = (msgs[0][0], msgs[0][1], msgs[0][2],
                       msgs[0][3] + "\n\n其余红项 %d 项：\n%s"
                       % (len(routine), "\n".join(routine[:8])), True)

    if dry:
        for name, _rid, _t, body, u in msgs:
            print("[dry -> %s%s]\n%s\n" % (name, " 加急" if u else "",
                                           body[:400]))
        return 0
    ok = True
    for name, rid, rtype, body, u in msgs:
        try:
            _send(body, rid, rtype, urgent=u)
            print("[alert] 已发 %s%s" % (name, "（加急）" if u else ""))
        except Exception as e:  # noqa: BLE001 —— 告警失败不反噬
            ok = False
            print("[alert] 发送失败 %s: %s" % (name, str(e)[:80]),
                  file=sys.stderr)
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="契约体检分级告警")
    ap.add_argument("doc", help="contract_health.json 路径")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    return alert(a.doc, a.dry)


if __name__ == "__main__":
    sys.exit(main())
