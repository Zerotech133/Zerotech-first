# -*- coding: utf-8 -*-
"""07:00 回执核验 + 云端抢险 的真实链路验收（2026-11-13）。

验收原则：
  · 投递与告警全部拦截（monkeypatch），避免验收动作真的发信到部门；
  · 云端模型**真调**——要验的正是「抢险方案到底有没有用」，
    这条用假数据验不出来；
  · 断言不只看「没报错」，要看结构字段与红线自检是否生效。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for _p in (ROOT / "tools" / "preopen", ROOT / "tools", ROOT / "agent_platform"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

sys.stdout.reconfigure(encoding="utf-8")

import daily_check_rescue as R  # noqa: E402
from gateway import inbox_gateway, internal_notify  # noqa: E402

OK, BAD = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def chk(tag: str, cond: bool, extra: str = "") -> bool:
    results.append((OK if cond else BAD, tag, extra))
    print("%-4s %s %s" % (OK if cond else BAD, tag, ("| " + extra) if extra else ""))
    return bool(cond)


# ---------------------------------------------------------------- 拦截投递
SENT: list[dict] = []
RED: list[dict] = []


def fake_emit(target, title, body="", **kw):
    SENT.append({"target": target, "title": title, "body": body,
                 "dedupe_key": kw.get("dedupe_key"), "created_at": kw.get("created_at")})
    return {"ok": True, "data": {"id": "fake-%d" % len(SENT), "replayed": False}}


internal_notify.emit = fake_emit


def fake_receipt(msg_id):
    # 模拟：部门一直没动（信在箱里、无答复戳记）
    return {"ok": True, "data": {"id": msg_id, "state": "inbox", "record": {},
                                 "answered": False, "delivered": False}}


inbox_gateway.receipt = fake_receipt


class _FakeHub:
    @staticmethod
    def notify_red(tool, reason, key="", dedupe_h=4.0):
        RED.append({"tool": tool, "reason": reason, "key": key})
        return True


sys.modules["notify_hub"] = _FakeHub

# ---------------------------------------------------------------- 构造台账
REAL_LEDGER = R.LEDGER_DIR
TMP_LEDGER = ROOT / "var" / "preopen" / "daily_check" / "_verify_tmp"
R.LEDGER_DIR = TMP_LEDGER
TMP_LEDGER.mkdir(parents=True, exist_ok=True)

TARGET = "20261112"
rec_fake = {
    "schema": "preopen/daily_check/v1", "exec_day": "20261113",
    "checked_at": "2026-11-13T05:30:12+08:00", "target": TARGET,
    "verdict": "warn", "verdict_cn": "有条件通过", "state": "CHECKED",
    "dry": False,
    "letters": [
        {"dept": "data", "target": "DATA1", "priority": "warning", "n_items": 1,
         "title": "[05:30 每日检查] →DATA1 数据智能部：1 项待处理（告警级）",
         "bridge_msg_id": "msg-data-001", "action": "SENT",
         "body": "【每日数据检查 · 05:30】数据日 20261112 / 判定 有条件通过\n"
                 "1) [告警] 龙虎榜：最新可用 20261111，距目标日 1 个交易日；"
                 "不参与选票，相关报告数字按未接上处理\n"
                 "   证据：{\"id\": \"ts_top_inst\", \"state\": \"warn\", "
                 "\"max_date\": \"20261111\", \"lag_trading_days\": 1}"},
        {"dept": "invest", "target": "D1", "priority": "warning", "n_items": 1,
         "title": "[05:30 每日检查] →D1 量化交易部：1 项待处理（告警级）",
         "bridge_msg_id": "msg-inv-002", "action": "SENT",
         "body": "【每日数据检查 · 05:30】数据日 20261112\n"
                 "1) [告警] 共识池最新包 asof=20261110，距目标日已隔 2 个交易日"},
    ],
}
(TMP_LEDGER / ("check_%s.json" % TARGET)).write_text(
    json.dumps({"schema": "preopen/daily_check/v1", "runs": [rec_fake],
                "target": TARGET, "n_runs": 1}, ensure_ascii=False, indent=1),
    encoding="utf-8")

# ---------------------------------------------------------------- ① 纯函数
print("\n== ① 回执三态 ==")
items = R.check_receipts(rec_fake)
chk("①-1 两封都判 SILENT（信在箱且无答复）",
    [i["verdict"] for i in items] == ["SILENT", "SILENT"], str([i["verdict"] for i in items]))
chk("①-2 未把「无 msg_id」记成已回执",
    R.check_receipts({"letters": [{"dept": "data", "action": "DRY_RUN"}]})[0]["verdict"] == "NOT_SENT")


def _ok_cnt():
    return {"ok": True, "data": {"state": "processed", "answered": True,
                                 "record": {"answered_at": "x", "answered_by": "DATA1",
                                            "answer_ref": "p"}}}


def _lost_cnt(mid):
    return {"ok": True, "data": {"state": "not_found", "record": {}, "answered": False}}


_saved = inbox_gateway.receipt
inbox_gateway.receipt = lambda m: _ok_cnt()
chk("①-3 有 answered 判 responded",
    R.check_receipts(rec_fake)[0]["verdict"] == "RESPONDED")
inbox_gateway.receipt = _lost_cnt
chk("①-4 查不到信封判 LOST（不是 SILENT）",
    R.check_receipts(rec_fake)[0]["verdict"] == "LOST")
inbox_gateway.receipt = _saved

print("\n== ② 红线自检 ==")
fake_actions = {"actions": [
    {"step": 1, "cmd": "python tools/preopen/audit_preopen.py --date 20261112 --json"},
    {"step": 2, "cmd": "python C:/nope/ghost_script.py --fix"},
    {"step": 3, "cmd": "Remove-Item C:/data/x.parquet -Force"},
    {"step": 4, "cmd": "python tools/preopen/detect_drift.py --date 20261112"},
]}
a = R.audit_actions(fake_actions)
chk("②-1 每条动作都标 requires_human",
    all(x["requires_human"] for x in a["actions"]))
chk("②-2 真实入口路径判存在",
    a["actions"][0]["path_exists"].get(
        "tools/preopen/audit_preopen.py") is True,
    str(a["actions"][0]["path_exists"]))
chk("②-3 虚构路径被判不存在并进 flags",
    any("不存在" in f and "ghost" in f for f in a["flags"]), str(a["flags"][:1]))
chk("②-4 高危命令（Remove-Item）被 flags 抓到",
    any("高危" in f for f in a["flags"]), str([f for f in a["flags"] if "高危" in f][:1]))
chk("②-5 跨脚本错用参数（--date 给 detect_drift）被逐条抓到",
    a["actions"][3]["unknown_flags"] == ["--date"],
    "detect_drift 动作的未登记参数=%s" % a["actions"][3]["unknown_flags"])
chk("②-6 合法参数不误报", a["actions"][0]["unknown_flags"] == [],
    str(a["actions"][0]["unknown_flags"]))
chk("②-7 未登记脚本（ghost）单独标注",
    any("调用了未登记脚本" in f and "ghost" in f for f in a["flags"]),
    str([f for f in a["flags"] if "未登记脚本" in f][:1]))

# ---------------------------------------------------------------- ③ 真调模型
print("\n== ③ 真调云端（deepseek-flash）==")
import argparse  # noqa: E402

args = argparse.Namespace(date=TARGET, dry=False, no_llm=False, no_feishu=False,
                          no_ledger=True, force=False, model="deepseek-flash",
                          timeout=150.0)
out = R.run(args)
chk("③-1 状态为已抢险", out.get("state") in ("RESCUED", "RESCUE_PARTIAL"), str(out.get("state")))
chk("③-2 两封派单都判无反应", out["summary"]["n_silent"] == 2, str(out["summary"]))
chk("③-3 两封加急信都已（模拟）投出", len(SENT) == 2, "sent=%d" % len(SENT))
chk("③-4 起了红告警（有部门无回执）", len(RED) >= 1, str(RED[:1]))

bad = [i["dept"] for i in out["items"] if not (i.get("rescue") or {}).get("ok")]
chk("③-5 云端抢险全部成功", not bad, "失败部门=%s" % bad)
for i in out["items"]:
    rs = i.get("rescue") or {}
    p = rs.get("parsed") or {}
    print("   [%s] cause=%s conf=%s actions=%d flags=%d"
          % (i["dept"], p.get("likely_cause"), p.get("confidence"),
             len(rs.get("actions") or []), len(rs.get("flags") or [])))
    chk("③-6-%s 有 diagnosis" % i["dept"], bool(p.get("diagnosis")))
    chk("③-7-%s 产出可执行动作" % i["dept"], len(rs.get("actions") or []) >= 1)
    chk("③-8-%s 红线字段齐全（needs_human/unknowns/confidence）" % i["dept"],
        isinstance(p.get("needs_human"), list) and isinstance(p.get("unknowns"), list)
        and p.get("confidence") in ("high", "medium", "low"))

body = SENT[0]["body"] if SENT else ""
chk("③-9 信里写明「系统未代为执行任何命令」", "未代为执行" in body)
chk("③-10 信里带回执方式", "inbox_gateway.consume" in body)
chk("③-11 幂等键按「数据日+部门」固定", SENT and SENT[0]["dedupe_key"].startswith(
    "preopen-rescue-20261112-"), SENT[0]["dedupe_key"] if SENT else "")

# ---------------------------------------------------------------- ④ 幂等
print("\n== ④ 重跑不重复烧 token ==")
n_before = len(SENT)
out2 = R.run(argparse.Namespace(date=TARGET, dry=False, no_llm=False, no_feishu=True,
                                no_ledger=True, force=False, model="deepseek-flash",
                                timeout=150.0))
reused = [bool((i.get("rescue") or {}).get("reused")) for i in out2["items"]]
chk("④-1 无 rescue 台账时不复用（首次必算）", all(r is False for r in reused), str(reused))
R.write_ledger(out)
out3 = R.run(argparse.Namespace(date=TARGET, dry=False, no_llm=False, no_feishu=True,
                                no_ledger=True, force=False, model="deepseek-flash",
                                timeout=150.0))
reused3 = [bool((i.get("rescue") or {}).get("reused")) for i in out3["items"]]
chk("④-2 已有台账则复用、不重复调模型", all(reused3), str(reused3))

# ---------------------------------------------------------------- 收尾
R.LEDGER_DIR = REAL_LEDGER
print("\n=== 汇总 %d/%d PASS ===" % (
    sum(1 for s, _, _ in results if s == OK), len(results)))
fails = [(t, e) for s, t, e in results if s == BAD]
for t, e in fails:
    print("FAIL %s %s" % (t, e))
print("RULE 验收不删临时台账（留在 %s，人工确认后可清）" % TMP_LEDGER.relative_to(ROOT))
sys.exit(1 if fails else 0)
