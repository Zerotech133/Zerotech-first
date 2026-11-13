# -*- coding: utf-8 -*-
"""05:30 派单后的「回执核验 + 云端抢险」。

## 为什么需要这一层

05:30 `daily_check_dispatch.py` 把问题按责任部门发进 bridge_inbox，要求
「立即处理、08:10 前回执」。但**入箱不等于已处理**——如果部门根本没动，
08:10 出名单时用的还是坏数据，而链路上没有任何一处会说「没人管」。

本模块补的正是这一段：07:00 核验回执，**没人接的自动升级**。

## 判定（三态，不许合并）

- `RESPONDED`：`inbox_gateway.receipt(msg_id).answered == True`，有
  `answered_by` + `answer_ref` 双向戳记。**只认这个**——`state=processed`
  只说明被领走，不代表处理完（`_has_answer` 才算）。
- `SILENT`：`state=inbox` 且未答复。部门没动。
- `LOST`：`state=not_found`。信根本没落库 = 投递链自己坏了，
  这比部门不理更严重，派给运行体部而不是原部门。

## 云端抢险做什么、不做什么（红线，别改）

**做**：读问题证据 + 本机既有诊断入口，让云端模型给出
「最可能成因 / 可执行的诊断命令 / 建议修复动作 / 是否需要人介入」，
落盘成 `rescue_<target>.json`，并回一封「加急+抢险建议」信。

**不做**：不执行模型给的任何命令，不自动改数据、不改配置、不改计划任务。
模型输出里每条动作都标 `requires_human: true`；代码里对动作提到的
路径做存在性校验（`path_exists`），虚构路径会被标出来——模型幻觉
不能直接变成人的操作依据。

理由：抢险场景下「自动执行」的失败模式是**不可逆的**（改坏分片、删错行、
重跑到一半），而「给出可核验的方案 + 人来点最后一下」在全网 2h40m 的
窗口里完全来得及。要做自动执行，须另行裁定白名单与回滚点。

## 幂等

按 `<target>:<msg_id>` 记账。已成功抢险过的不重复调模型（省 token、
也避免同一问题给出互相矛盾的两套方案），除非 `--force`。

## 退出码

`0` = 跑完了（无论有没有人没反应）；`3` = 本模块自己没跑完
（云端不可用、投递失败、台账读写失败）。与 05:30 同口径：
LastTaskResult 问的是「跑完了吗」，不是「结论好不好」。

用法：
  python tools/preopen/daily_check_rescue.py --dry        # 只核验回执，不调模型不发信
  python tools/preopen/daily_check_rescue.py              # 实跑（07:00 任务调用）
  python tools/preopen/daily_check_rescue.py --selftest   # 只测云端通道
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "tools" / "preopen", ROOT / "tools", ROOT / "agent_platform"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from gateway import inbox_gateway, internal_notify  # noqa: E402

LEDGER_DIR = ROOT / "var" / "preopen" / "daily_check"
RESPONDED, SILENT, LOST, NOT_SENT = "RESPONDED", "SILENT", "LOST", "NOT_SENT"

# 推理模型：reasoning 与正文共用预算。抢险方案是结构化长输出，
# 2500 配 reasoning_effort=minimal 实测稳定（思考 ≈86 token，正文 ≈600 字）。
MAX_TOKENS = 2500

# 单封工单正文喂给模型的上限。现在 dispatch 已落全文（实测 567~840 字），
# 正常不会触顶；此上限只为防某个部门问题条目极多时把 prompt 撑爆。
MAX_EVIDENCE_CHARS = 6000

# 允许模型引用的诊断命令：**模板**，{target} 会被替换成真实数据日。
# 为什么不只给脚本路径：2026-11-13 首轮实测，只给路径时模型把
#   detect_drift 写成 --date（真参数是 --day）、event_log 写成 --date
#   （真参数是 --show）、dispatch 写成 --dry-run（真参数是 --dry）。
# 命令因参数错而报错，会被下一步误读成「链路有问题」——比不给方案更糟。
CMD_TEMPLATES = [
    "python C:/项目/老田的AI助手/tools/preopen/audit_preopen.py --date {target} --json",
    "python C:/项目/老田的AI助手/tools/preopen/audit_preopen.py --date {target}",
    "python C:/项目/老田的AI助手/tools/preopen/daily_check_dispatch.py --date {target} --dry --no-aux",
    "python C:/项目/老田的AI助手/tools/preopen/detect_drift.py --day {target} --json",
    "python C:/项目/老田的AI助手/tools/preopen/event_log.py --show {target}",
    "python C:/项目/老田的AI助手/portal/daily_report/data_health.py --target {target} --quiet",
]

# 参数白名单**按脚本登记**，不能只按参数名全局登记：
# 2026-11-13 验收抓到模型把 `--date` 传给 detect_drift（真参数是 `--day`），
# 全局白名单里两者都有，于是放过——「参数存在」不等于「这个脚本有这个参数」。
SCRIPT_FLAGS = {
    "audit_preopen.py": {"--date", "--json"},
    "daily_check_dispatch.py": {"--date", "--routes", "--dry", "--no-feishu",
                                "--no-aux", "--no-ledger"},
    "detect_drift.py": {"--day", "--json"},
    "event_log.py": {"--from-run", "--run-json", "--note", "--show", "--amend", "--list"},
    "data_health.py": {"--target", "--data-dir", "--json-out", "--quiet"},
    "daily_check_rescue.py": {"--date", "--dry", "--no-llm", "--no-feishu",
                              "--no-ledger", "--force", "--model", "--timeout",
                              "--selftest"},
}
_FLAG_RE = re.compile(r"(?<![\w-])(--?[a-zA-Z][\w-]*)")
_SCRIPT_RE = re.compile(r"([\w.-]+\.py)\b")

SYSTEM_PROMPT = """你是量化数据平台的抢险工程师。05:30 的每日检查已按责任部门发信，07:00 核验时该部门没回执。基于给定证据，给出当天盘前能执行的抢险方案。

约束：
1. 只依据给定证据推理。证据里没有的，写进 unknowns，不要假设。
2. 禁止建议改库、删数据、改计划任务、改配置、重装环境——一律写进 needs_human。
3. 命令必须是只读诊断或幂等重跑；写全，不留占位符。
4. **命令只能从「可用诊断命令」里原样选取，不得增删或改动任何参数**。
   需要的信息如果没有现成命令，写进 unknowns，不要自己编一个。
5. 区分上游给空 / 本地漏跑 / 架构停更；T+1 源落后 1 个交易日是正常的。
6. 同一 action 里只放一条命令，不要用 && 或 ; 串联。
7. confidence 按证据强度给，不足就 low，不要为了显得有用而拔高。

只输出 JSON，不要 markdown：
{"diagnosis":"一两句最可能发生了什么","likely_cause":"upstream_empty|local_missed|arch_stale|calendar_or_config|unknown","why":"引用证据里的具体字段值","actions":[{"step":1,"cmd":"命令","expect":"正常看到什么","if_abnormal":"看到什么说明是别的病因"}],"needs_human":["须人工决策的事项"],"unknowns":["证据不足的点"],"confidence":"high|medium|low","deadline_advice":"结合 08:10 出名单的一句话建议"}"""


# ---------------------------------------------------------------------------
# 回执核验
# ---------------------------------------------------------------------------
def check_receipts(rec: dict) -> list[dict]:
    """逐封查回执。三种结论分开记，合并会把「信丢了」说成「部门不理」。"""
    out: list[dict] = []
    for L in rec.get("letters") or []:
        msg_id = L.get("bridge_msg_id")
        item = {
            "dept": L.get("dept"), "target": L.get("target"),
            "priority": L.get("priority"), "n_items": L.get("n_items"),
            "title": L.get("title"), "msg_id": msg_id,
            "dispatch_action": L.get("action"),
        }
        if not msg_id:
            # DRY_RUN 或投递失败：没有可查的箱子，明确记下来，不算「已回执」
            item.update(verdict=NOT_SENT,
                        reason="本次派单没有 msg_id（action=%s），无回执可查"
                               % L.get("action"))
            out.append(item)
            continue
        try:
            env = inbox_gateway.receipt(msg_id)
        except Exception as e:  # noqa: BLE001
            item.update(verdict=LOST,
                        reason="回执查询异常：%s: %s" % (type(e).__name__, str(e)[:120]))
            out.append(item)
            continue
        data = env.get("data") or {}
        state = data.get("state")
        answered = bool(data.get("answered"))
        r = data.get("record") or {}
        if answered:
            verdict, reason = RESPONDED, "已回执"
        elif state == "inbox":
            verdict, reason = SILENT, "信在箱里但无人答复"
        elif state == "not_found":
            verdict, reason = LOST, "信封在库里查不到（投递链故障）"
        else:
            verdict, reason = SILENT, "state=%s 且无答复戳记" % state
        item.update(verdict=verdict, reason=reason, inbox_state=state,
                    answered_at=r.get("answered_at"),
                    answered_by=r.get("answered_by"),
                    answer_ref=r.get("answer_ref"))
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 云端抢险
# ---------------------------------------------------------------------------
def _strip_fence(s: str) -> str:
    t = (s or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    return t[a:b + 1] if a >= 0 and b > a else t


def _paths_in(text: str) -> list[str]:
    """抽出文本里像路径的片段，用于存在性校验（防模型编造入口）。"""
    pats = re.findall(r"[A-Za-z]:[\\/][^\s\"'，。；)）]+|[\w./-]+\.py\b", text or "")
    seen, out = set(), []
    for p in pats:
        p = p.strip().rstrip(".,;")
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _resolve(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else (ROOT / p)


def audit_actions(res: dict) -> dict:
    """给模型的每条动作补三件事：路径是否真实存在、参数是否臆造、是否踩红线。"""
    banned = ("drop ", "truncate ", "delete ", "rm ", "del ", "unregister",
              "set-scheduledtask", "register-scheduledtask", "remove-item")
    checked, flags = [], []
    for a in res.get("actions") or []:
        if not isinstance(a, dict):
            continue
        cmd = str(a.get("cmd") or "")
        low = cmd.lower()
        hit_ban = [b.strip() for b in banned if b in low]
        paths = {p: _resolve(p).exists() for p in _paths_in(cmd)}
        missing = [p for p, ok in paths.items() if not ok]
        # 参数臆造：按**命令里那个脚本**登记的参数表逐条比对
        scripts = _SCRIPT_RE.findall(cmd)
        known = set()
        unknown_script = []
        for s in scripts:
            if s in SCRIPT_FLAGS:
                known |= SCRIPT_FLAGS[s]
            else:
                unknown_script.append(s)
        bad_flags = sorted({f for f in _FLAG_RE.findall(cmd) if f not in known}) \
            if scripts and not unknown_script else []
        checked.append(dict(a, requires_human=True, path_exists=paths,
                            banned_hits=hit_ban, unknown_flags=bad_flags))
        if hit_ban:
            flags.append("动作含高危关键字 %s：%s" % (hit_ban, cmd[:120]))
        if missing:
            flags.append("动作引用了不存在的路径 %s：%s" % (missing, cmd[:120]))
        if bad_flags:
            flags.append("动作含该脚本未登记的参数 %s（可能臆造）：%s"
                         % (bad_flags, cmd[:120]))
        if unknown_script:
            flags.append("动作调用了未登记脚本 %s，其参数无法核对：%s"
                         % (unknown_script, cmd[:120]))
    return {"actions": checked, "flags": flags}


def rescue_one(item: dict, rec: dict, model: str, timeout: float) -> dict:
    """对单个未回执部门调云端模型。失败必须显式，不许返回空 dict。"""
    dept = item.get("dept")
    routes_path = Path(__file__).with_name("daily_check_routes.json")
    try:
        routes = json.loads(routes_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        routes = {}
    dep = (routes.get("departments") or {}).get(dept) or {}

    # 证据只取该部门的问题条目，避免把别部门的事混进提示词
    issues = []
    for L in rec.get("letters") or []:
        if L.get("dept") != dept:
            continue
        # 优先全文（2026-11-13 修：此前 dispatch 只给真实投递留 300 字预览，
        # 模型永远看不到「哪张表未达标」，只能给 unknown/low 的废方案）。
        body = L.get("body") or L.get("body_preview") or ""
        if len(body) > MAX_EVIDENCE_CHARS:
            # 截断必须显式：静默截断 = 把「证据不全」伪装成「证据就是这些」（R4 未知可见）
            issues.append(body[:MAX_EVIDENCE_CHARS]
                          + "\n…（正文原始 %d 字，此处截断至 %d 字，后半段未提供）"
                          % (len(body), MAX_EVIDENCE_CHARS))
        else:
            issues.append(body)
    payload = {
        "数据日": rec.get("target"),
        "执行日": rec.get("exec_day"),
        "整体判定": rec.get("verdict_cn"),
        "责任部门": {"键": dept, "名称": dep.get("name"), "席码": dep.get("target"),
                     "职责": dep.get("duty")},
        "未回执情形": item.get("verdict"),
        "说明": item.get("reason"),
        "派单时间": rec.get("checked_at"),
        "当前时间": datetime.now().astimezone().isoformat(timespec="seconds"),
        "派单正文": issues,
        "可用诊断命令（只能原样使用，参数一个字都不要改）":
            [t.format(target=rec.get("target") or "<数据日>") for t in CMD_TEMPLATES],
        "已登记的采集侧计划任务": ["TushareSync", "TushareTailSync", "TdxTailSync",
                                    "AdjFactorSync", "KgDailySync"],
    }
    user_msg = ("以下是 05:30 检查派给该部门、到 07:00 仍无回执的工单，"
                "请给出抢险方案：\n" + json.dumps(payload, ensure_ascii=False, indent=1))

    try:
        from engine.deepseek import DeepSeekClient, DeepSeekError
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "云端客户端不可用：%s: %s" % (type(e).__name__, str(e)[:120])}

    try:
        client = DeepSeekClient(model=model, timeout=timeout)
        # 必须 thinking=disabled。2026-11-13 同日 6 案对照（_probe_ds_long.py）：
        #   · 无开关 / reasoning_effort=minimal  → reasoning 烧满 2500，正文空，length
        #   · thinking={"type":"disabled"}       → reasoning=None，正文 1.6k 字，合法 JSON
        # reasoning_effort 对 deepseek-flash **无效**，别拿它省钱。
        # max_tokens 也要给足：推理模型 reasoning 与正文共用预算，默认 800 不够。
        res = client.chat_full(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": user_msg}],
            max_tokens=MAX_TOKENS, temperature=0.2,
            response_format={"type": "json_object"},
            extra={"thinking": {"type": "disabled"}},
        )
    except DeepSeekError as e:
        return {"ok": False, "error": "云端调用失败：%s" % str(e)[:200]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}

    txt = res.get("content") or ""
    usage = res.get("usage") or {}
    finish = res.get("finish_reason")
    if not txt.strip():
        # 空正文 = 不可用（既有判例：空回不是成功）
        return {"ok": False, "model": model, "finish_reason": finish, "usage": usage,
                "error": "云端返回空正文（finish_reason=%s，推理可能吃满预算）" % finish}

    raw = _strip_fence(txt)
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {"ok": False, "error": "模型未返回合法 JSON", "raw_excerpt": txt[:800],
                "model": model, "finish_reason": finish, "usage": usage}

    audited = audit_actions(parsed)
    return {"ok": True, "model": model, "parsed": parsed,
            "actions": audited["actions"], "flags": audited["flags"],
            "finish_reason": finish, "usage": usage,
            "raw_excerpt": txt[:400]}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def render_letter(item: dict, rs: dict, meta: dict) -> tuple[str, str]:
    """回给该部门（并抄运行体部）的加急信。"""
    p = rs.get("parsed") or {}
    head = "[07:00 抢险] %s →%s 未回执，已启动云端方案" % (
        item.get("verdict"), item.get("target"))
    lines = [
        "【回执核验 · 07:00】数据日 %s / 执行日 %s" % (meta["target"], meta["exec_day"]),
        "派单时间 %s，截至 07:00 未见回执（%s）。" % (meta["sent_at"], item.get("reason")),
        "08:10 出名单，剩余约 %s 分钟。" % meta.get("left_min"),
        "-" * 40,
    ]
    if not rs.get("ok"):
        lines += ["云端抢险未成功：%s" % rs.get("error"),
                  "请立即人工介入，不要等第二次提醒。"]
    else:
        lines += [
            "云端模型（%s）判读：" % rs.get("model"),
            "最可能成因：%s —— %s" % (p.get("likely_cause"), p.get("diagnosis")),
            "依据：%s" % p.get("why"),
            "把握度：%s ｜ 时限建议：%s" % (p.get("confidence"), p.get("deadline_advice")),
            "", "抢险动作（人工确认后执行，系统未代为执行任何命令）：",
        ]
        for a in rs.get("actions") or []:
            lines.append("%s) %s" % (a.get("step"), a.get("cmd")))
            if a.get("expect"):
                lines.append("   正常：%s" % a.get("expect"))
            if a.get("if_abnormal"):
                lines.append("   异常：%s" % a.get("if_abnormal"))
            bad = ((a.get("banned_hits") or [])
                   + [x for x, ok in (a.get("path_exists") or {}).items() if not ok]
                   + (a.get("unknown_flags") or []))
            if bad:
                lines.append("   ⚠ 待核（含高危/不存在路径/未登记参数）：%s" % bad)
        nh = p.get("needs_human") or []
        if nh:
            lines += ["", "须人工决策（模型明确不建议自动做）："] + ["- " + x for x in nh]
        un = p.get("unknowns") or []
        if un:
            lines += ["", "证据不足："] + ["- " + x for x in un]
        if rs.get("flags"):
            lines += ["", "⚠ 方案自检告警："] + ["- " + x for x in rs["flags"]]
    lines += [
        "-" * 40,
        "回执方式：inbox_gateway.consume(msg_id, answered_by=席码, answer_ref=修复证据路径)",
        "本信由 07:00 回执核验自动发出；未回执会照实写进当日报告。",
    ]
    return head[:120], "\n".join(lines)[:4000]


def run(args: argparse.Namespace) -> dict:
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    out: dict = {"schema": "preopen/daily_check_rescue/v1", "exec_day": today,
                 "ran_at": now.astimezone().isoformat(timespec="seconds"),
                 "dry": bool(args.dry), "enabled": not args.no_llm, "items": []}

    # 找台账：默认取今天执行日里最近一份，允许 --date 指定数据日
    if args.date:
        p = LEDGER_DIR / ("check_%s.json" % args.date)
    else:
        cands = sorted(LEDGER_DIR.glob("check_*.json"), key=lambda x: x.stat().st_mtime)
        p = cands[-1] if cands else None
    if not p or not p.is_file():
        out.update(state="NO_LEDGER", reason="找不到 05:30 检查台账，未核验（不是「没人没反应」）")
        print("NO_LEDGER %s" % out["reason"])
        return out

    doc = json.loads(p.read_text(encoding="utf-8"))
    runs = doc.get("runs") or []
    # 只认今天执行日的实跑记录；昨天那封已经过期的信不重复追
    same_day = [r for r in runs if r.get("exec_day") == today and not r.get("dry")]
    if not same_day:
        out.update(state="NO_RUN_TODAY",
                   reason="台账里没有 %s 的实跑记录（仅有预演或旧批次），未核验" % today)
        print("NO_RUN_TODAY %s" % out["reason"])
        return out
    rec = same_day[-1]
    out["target"] = rec.get("target")
    out["sent_at"] = rec.get("checked_at")
    out["dispatch_n"] = len(rec.get("letters") or [])

    items = check_receipts(rec)
    out["items"] = items
    n_resp = sum(1 for i in items if i["verdict"] == RESPONDED)
    n_silent = sum(1 for i in items if i["verdict"] == SILENT)
    n_lost = sum(1 for i in items if i["verdict"] == LOST)
    n_notsent = sum(1 for i in items if i["verdict"] == NOT_SENT)
    out["summary"] = {"n_responded": n_resp, "n_silent": n_silent, "n_lost": n_lost,
                      "n_not_sent": n_notsent, "n_total": len(items)}

    todo = [i for i in items if i["verdict"] in (SILENT, LOST)]
    if not todo:
        out["state"] = "ALL_RESPONDED"
        print("ALL_RESPONDED 数据日 %s：%d 封派单全部已回执（%d 未发出）"
              % (out.get("target"), n_resp, n_notsent))
        return out

    if args.dry:
        out["state"] = "DRY_RUN"
        print("DRY_RUN 数据日 %s：待升级 %d 封（无回执 %d / 投递丢失 %d）；未调模型未发信"
              % (out.get("target"), len(todo), n_silent, n_lost))
        return out

    # ---- 抢险：逐封，幂等 ----
    prior = {}
    rp = LEDGER_DIR / ("rescue_%s.json" % out.get("target"))
    if rp.is_file():
        try:
            prev = json.loads(rp.read_text(encoding="utf-8"))
            for it in prev.get("items") or []:
                if (it.get("rescue") or {}).get("ok"):
                    prior[str(it.get("msg_id"))] = it["rescue"]
        except (ValueError, OSError):
            pass

    meta = {"target": out.get("target"), "exec_day": today, "sent_at": out.get("sent_at")}
    n_ok = n_fail = 0
    for it in todo:
        key = str(it.get("msg_id"))
        if key in prior and not args.force:
            it["rescue"] = dict(prior[key], reused=True)
            n_ok += 1
        else:
            it["rescue"] = rescue_one(it, rec, args.model, args.timeout)
            n_ok += 1 if it["rescue"].get("ok") else 0
            n_fail += 0 if it["rescue"].get("ok") else 1
        # 剩余时间：按固定的 08:10 出名单 deadline 算
        try:
            dl = datetime.strptime(today + "0810", "%Y%m%d%H%M")
            meta["left_min"] = int((dl - datetime.now()).total_seconds() // 60)
        except ValueError:
            meta["left_min"] = None
        title, body = render_letter(it, it["rescue"], meta)
        dk = "preopen-rescue-%s-%s" % (out.get("target"), it.get("dept"))
        try:
            env = internal_notify.emit(
                target=it.get("target"), title=title, body=body,
                kind="task_request", source="preopen/daily_check_rescue",
                actor="houfeng-preopen", dedupe_key=dk,
                created_at=str(out.get("target")) + "T07:00:00+0800",
            )
            data = env.get("data") or {}
            it["letter"] = {"ok": bool(env.get("ok")), "error": env.get("error"),
                            "action": ("REPLAYED" if data.get("replayed")
                                       else "SENT" if env.get("ok") else "FAILED"),
                            "bridge_msg_id": data.get("id")}
            if not env.get("ok"):
                out.setdefault("delivery_errors", []).append(
                    {"dept": it.get("dept"), "error": env.get("error")})
        except Exception as e:  # noqa: BLE001
            it["letter"] = {"ok": False, "action": "FAILED",
                            "error": "%s: %s" % (type(e).__name__, str(e)[:160])}
            out.setdefault("delivery_errors", []).append(
                {"dept": it.get("dept"), "error": it["letter"]["error"]})

    out["summary"]["n_rescue_ok"] = n_ok
    out["summary"]["n_rescue_failed"] = n_fail

    # 投递链自己坏了 = 运行体部的事，且必须惊动人
    red_reason = ""
    if n_lost:
        red_reason = ("%s 有 %d 封派单在库里查不到（投递链故障），"
                      "06:00 的部门提醒可能整批没送到" % (out.get("target"), n_lost))
    elif n_fail:
        red_reason = ("%s 有 %d 个部门无回执且云端抢险失败，08:10 前须人工介入"
                      % (out.get("target"), n_fail))
    elif n_silent:
        red_reason = ("%s 有 %d 个部门至 07:00 未回执（已发云端抢险方案）"
                      % (out.get("target"), n_silent))
    out["feishu"] = {"attempted": False, "reason": "非阻断"}
    if red_reason and not args.no_feishu:
        try:
            from notify_hub import notify_red
            out["feishu"] = {"attempted": True,
                             "ok": bool(notify_red("preopen_daily_rescue", red_reason,
                                                   key="daily-rescue-" + str(out.get("target"))))}
        except Exception as e:  # noqa: BLE001
            out["feishu"] = {"attempted": True, "ok": False,
                             "error": "%s: %s" % (type(e).__name__, str(e)[:120])}

    out["state"] = "RESCUED" if not n_fail else "RESCUE_PARTIAL"
    print("%s 数据日 %s：回执 %d / 无反应 %d / 投递丢失 %d ｜ 抢险成功 %d 失败 %d%s"
          % (out["state"], out.get("target"), n_resp, n_silent, n_lost,
             n_ok, n_fail, "" if not red_reason else " ｜ 已发红告警"))
    return out


def write_ledger(rec: dict) -> Path:
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    p = LEDGER_DIR / ("rescue_%s.json" % (rec.get("target") or "no_target"))
    old = {"schema": "preopen/daily_check_rescue/v1", "runs": []}
    if p.is_file():
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    old.setdefault("runs", []).append(rec)
    # 顶层 items 保留最近一次的，便于人直接看
    old["items"] = rec.get("items") or []
    old["target"] = rec.get("target")
    old["n_runs"] = len(old["runs"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return p


def main() -> int:
    a = argparse.ArgumentParser(description="07:00 回执核验 + 云端抢险")
    a.add_argument("--date", default=None, help="数据日 YYYYMMDD；缺省取最近一份台账")
    a.add_argument("--dry", action="store_true", help="只核验回执，不调模型不发信")
    a.add_argument("--no-llm", action="store_true", help="不调云端模型（只核验+提醒）")
    a.add_argument("--no-feishu", action="store_true", help="不发达人红告警")
    a.add_argument("--no-ledger", action="store_true", help="不写台账")
    a.add_argument("--force", action="store_true", help="已抢险过的也重跑")
    a.add_argument("--model", default="deepseek-flash")
    a.add_argument("--timeout", type=float, default=120.0)
    a.add_argument("--selftest", action="store_true", help="只测云端通道连通性")
    args = a.parse_args()

    if args.selftest:
        try:
            from engine.deepseek import DeepSeekClient, DeepSeekError
            r = DeepSeekClient(model=args.model, timeout=args.timeout).ping()
            ok = bool(r.get("ok"))
            print("CLOUD %s model=%s reply=%r" % ("OK" if ok else "FAIL",
                                                  args.model, str(r.get("reply"))[:40]))
            return 0 if ok else 3
        except Exception as e:  # noqa: BLE001
            print("CLOUD FAIL %s: %s" % (type(e).__name__, str(e)[:160]))
            return 3

    try:
        rec = run(args)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print("BLOCKED 抢险脚本异常（不是「一切正常」）")
        return 3
    if not args.no_ledger:
        try:
            print("台账 %s" % write_ledger(rec).relative_to(ROOT))
        except Exception as e:  # noqa: BLE001
            print("LEDGER_WRITE_ERR %s" % e)
            return 3
    if rec.get("delivery_errors"):
        print("DELIVERY_FAILED %d 封加急信未送达（fail-visible）" % len(rec["delivery_errors"]))
        return 3
    fs = rec.get("feishu") or {}
    if fs.get("attempted") and fs.get("ok") is False:
        print("FEISHU_FAILED 红告警未送达（fail-visible）")
        return 3
    if rec.get("summary", {}).get("n_rescue_failed"):
        # 抢险没抢成：业务上要人，但不是「没跑完」；写独立行，退出码仍 0
        print("RESCUE_UNAVAILABLE %d 个部门未拿到云端方案"
              % rec["summary"]["n_rescue_failed"])
    print("BUSINESS_STATE=%s N_SILENT=%d N_LOST=%d N_RESCUED=%d"
          % (rec.get("state"), rec.get("summary", {}).get("n_silent", 0),
             rec.get("summary", {}).get("n_lost", 0),
             rec.get("summary", {}).get("n_rescue_ok", 0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
