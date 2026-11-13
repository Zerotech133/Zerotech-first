# -*- coding: utf-8 -*-
"""探针 2：用**与生产一致的长提示**找能关掉推理的开关（2026-11-13）。

探针 1 用的是短提示，各案都过——但生产提示长 3 倍，reasoning 直接把 2500
预算烧完、正文为空。本探针在真实 prompt 上做 2×2：

  开关三态（无 / reasoning_effort=minimal / thinking=disabled）
  × response_format（有 json_object / 无）

判定只认一条：**正文非空且能解析成 JSON**。reasoning_tokens 只作归因参考。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for _p in (ROOT / "tools" / "preopen", ROOT / "agent_platform"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
sys.stdout.reconfigure(encoding="utf-8")

import daily_check_rescue as R  # noqa: E402
from engine.deepseek import DeepSeekClient, DeepSeekError  # noqa: E402

# 用真实的派单正文构造（与 rescue_one 里一致）
REC = {
    "target": "20261112", "exec_day": "20261113", "verdict_cn": "有条件通过",
    "checked_at": "2026-11-13T05:30:12+08:00",
    "letters": [
        {"dept": "data", "body": "【每日数据检查 · 05:30】数据日 20261112 / 判定 有条件通过\n"
         "1) [告警] 龙虎榜：最新可用 20261111，距目标日 1 个交易日；不参与选票，"
         "相关报告数字按未接上处理\n   证据：{\"id\": \"ts_top_inst\", \"name\": \"龙虎榜\", "
         "\"role\": \"soft\", \"state\": \"warn\", \"max_date\": \"20261111\", "
         "\"lag_trading_days\": 1, \"note\": \"缺目标日，能力降级（该源不参与选票）\", "
         "\"empty_days\": [], \"absent_days\": []}"},
    ],
}
ITEM = {"dept": "data", "verdict": "SILENT", "reason": "信在箱里但无人答复"}


def build_user_msg() -> str:
    payload = {
        "数据日": REC["target"], "执行日": REC["exec_day"],
        "整体判定": REC["verdict_cn"],
        "责任部门": {"键": "data", "名称": "数据智能部", "席码": "DATA1",
                     "职责": "行情主源与采集链路、源端接口、分片落盘"},
        "未回执情形": ITEM["verdict"], "说明": ITEM["reason"],
        "派单时间": REC["checked_at"], "派单正文": [REC["letters"][0]["body"]],
        "可用诊断入口": R.ENTRYPOINTS,
        "已登记的采集侧计划任务": ["TushareSync", "TushareTailSync", "TdxTailSync"],
    }
    return ("以下是 05:30 检查派给该部门、到 07:00 仍无回执的工单，请给出抢险方案：\n"
            + json.dumps(payload, ensure_ascii=False, indent=1))


CASES = [
    ("无开关 + json_object", {}, {"type": "json_object"}),
    ("无开关 + 无 rf", {}, None),
    ("effort=minimal + json_object", {"reasoning_effort": "minimal"}, {"type": "json_object"}),
    ("effort=minimal + 无 rf", {"reasoning_effort": "minimal"}, None),
    ("thinking=disabled + json_object", {"thinking": {"type": "disabled"}}, {"type": "json_object"}),
    ("thinking=disabled + 无 rf", {"thinking": {"type": "disabled"}}, None),
]

msgs = [{"role": "system", "content": R.SYSTEM_PROMPT},
        {"role": "user", "content": build_user_msg()}]
print("提示长度：system=%d user=%d 字" % (len(R.SYSTEM_PROMPT), len(msgs[1]["content"])))

rows = []
for tag, extra, rf in CASES:
    try:
        c = DeepSeekClient(model="deepseek-flash", timeout=200.0)
        res = c.chat_full(msgs, max_tokens=R.MAX_TOKENS, temperature=0.2,
                          response_format=rf, extra=extra or None)
        txt = (res.get("content") or "").strip()
        u = res.get("usage") or {}
        rt = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
        parsed = False
        if txt:
            try:
                json.loads(R._strip_fence(txt))
                parsed = True
            except ValueError:
                parsed = False
        rows.append({"case": tag, "len": len(txt), "json": parsed,
                     "reason_tok": rt, "comp": u.get("completion_tokens"),
                     "finish": res.get("finish_reason"), "head": txt[:80]})
        print("%-34s len=%-4d json=%-5s reason=%-5s comp=%-5s finish=%s"
              % (tag, len(txt), parsed, rt, u.get("completion_tokens"), res.get("finish_reason")))
    except DeepSeekError as e:
        rows.append({"case": tag, "err": str(e)[:160]})
        print("%-34s ERR %s" % (tag, str(e)[:120]))

good = [r["case"] for r in rows if r.get("json")]
print("\n能拿到可用 JSON 的路径: %s" % (good or "无"))
Path(ROOT / "var" / "preopen" / "daily_check" / "_probe2.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
sys.exit(0 if good else 1)
