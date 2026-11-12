# -*- coding: utf-8 -*-
"""每日数据检查（05:30）+ 按责任部门发信要求处理。

## 为什么单独有一个 05:30 检查，而不是把 08:10 整体挪早

08:10 `preopen_daily` 的职责是「出名单」；05:30 的职责是「早发现、早派单」。
中间 2 小时 40 分钟是留给责任部门的处理窗口 —— 检查出问题当天就能修，
而不是等名单出来才发现数据没接上。两个任务互不替代：
即使 08:10 出名单任务被停用，本检查与派单仍独立有效，
SLA 用「当日 08:10 盘前截止」这个固定时点，不依赖任何计划任务是否启用。

## 判定纪律（都是既有判例，别改回去）

1. **目标日由交易日历给定**，不取被测表自己的 max(date)。
   用「今天之前的最后一个开市日」当目标日，逐源断言 `max >= target`。
2. **「数据未就绪」与「无事可做」必须分成两种输出**。
   拿不到交易日历 / 连个股日线都没有 → 明确 `BLOCKED`，不是「一切正常」。
3. **告警项与阻断项分开派单**：阻断项要求 08:10 前回执；告警项当日盘前处理、
   写进报告。不因为「只有告警」就静默，也不因为「天天有告警」就一律发飞书。
4. **上游给空 / 本地漏跑 / 架构停更是三件事**，证据里分开写，不合并成「停更」。
5. **入箱 ≠ 已处理**：部门信带回执要求，回执方式写进信里。
6. **退出码是「任务跑完了吗」，不是「业务结论好不好」**。
   正常完成一律 0（业务判定写台账与日志独立行）；投递失败 / 无法确定目标日 = 3。

## 通道纪律（老田 2026-11-10 裁定）

- 给部门的信：`internal_notify.emit` → bridge_inbox 内部闭环（AI↔AI，不进飞书）；
- 只有阻断级判定经 `notify_hub` 红告警到人（健康检查家族既有自动外发，4h 去重）。

用法：
  python tools/preopen/daily_check_dispatch.py --dry          # 预演，不投信不发送
  python tools/preopen/daily_check_dispatch.py                # 实跑（由 05:30 任务调用）
  python tools/preopen/daily_check_dispatch.py --date 20261112 --no-feishu
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "tools" / "preopen", ROOT / "tools", ROOT / "agent_platform"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import audit_preopen as ap  # noqa: E402
from gateway import internal_notify  # noqa: E402

ROUTES_PATH = Path(__file__).with_name("daily_check_routes.json")
LEDGER_DIR = ROOT / "var" / "preopen" / "daily_check"
DH = ROOT / "portal" / "daily_report" / "data_health.py"
PY = sys.executable

BLOCKER = "blocker"
WARNING = "warning"
BAD_STATES = ("empty_local", "warn", "missed")


# ---------------------------------------------------------------------------
# 纯函数（可单测）
# ---------------------------------------------------------------------------
def load_routes(path: Path | str | None = None) -> dict:
    p = Path(path) if path else ROUTES_PATH
    d = json.loads(Path(p).read_text(encoding="utf-8"))
    assert d.get("schema") == "preopen/daily_check_routes/v1", "映射表 schema 不符"
    for k, v in (d.get("departments") or {}).items():
        assert v.get("target"), "%s 缺负责人席码" % k
    return d


def resolve_target(explicit: str | None, cal: list[str], today: str) -> tuple[str | None, str]:
    """目标日 = 今天之前的最后一个开市日。由日历给定，不由被测表自证。"""
    if explicit:
        return str(explicit), "cli"
    if not cal:
        return None, "no_trade_calendar"
    prior = [d for d in cal if d < today]
    if not prior:
        return None, "no_prior_session"
    return prior[-1], "trade_cal"


def classify(text: str, routes: dict) -> tuple[str, str]:
    """把一条问题文本映射到部门。优先按源名前缀，其次关键词，最后兜底。"""
    by_source = routes.get("by_source") or {}
    for sp in ap.SPECS:
        if text.startswith(sp["name"] + "：") or text.startswith(sp["name"] + ":"):
            dept = by_source.get(sp["id"])
            if dept:
                return dept, "source:" + sp["id"]
    for rule in routes.get("by_keyword") or []:
        if rule.get("match") and rule["match"] in text:
            return rule["dept"], "keyword:" + rule["match"]
    return routes.get("fallback") or "ops_runtime", "fallback"


def source_evidence(res: dict, text: str) -> dict:
    """给问题文本补上结构化证据（源状态 / 最新日 / 落后天数 / 分片诊断）。"""
    for sp in ap.SPECS:
        if not (text.startswith(sp["name"] + "：") or text.startswith(sp["name"] + ":")):
            continue
        for s in res.get("sources") or []:
            if s.get("id") == sp["id"]:
                return {k: s.get(k) for k in
                        ("id", "name", "role", "state", "max_date",
                         "lag_trading_days", "note", "empty_days", "absent_days")
                        if s.get(k) is not None}
    return {}


def build_issues(res: dict, routes: dict, aux: dict | None = None) -> list[dict]:
    """从校对结果生成待派单条目。阻断项在前，同部门内保持原顺序。"""
    items: list[dict] = []
    for sev, bucket in ((BLOCKER, "problems"), (WARNING, "warns")):
        for text in res.get(bucket) or []:
            dept, matched = classify(text, routes)
            items.append({
                "severity": sev, "text": text, "dept": dept, "matched_by": matched,
                "evidence": source_evidence(res, text),
            })
    # 结构坏价即使没进 problems（阈值未触发），也留一条可观测记录给质量部
    st = res.get("structure") or {}
    bad = {k: st.get(k) for k in ("nan_close", "nonpositive_close", "bad_high",
                                  "bad_low", "pct_extreme", "zero_vol")
           if st.get(k)}
    if bad:
        items.append({
            "severity": WARNING,
            "text": "目标日结构体检存在非零异常计数：%s" % json.dumps(bad, ensure_ascii=False),
            "dept": "quality", "matched_by": "structure", "evidence": {"structure": bad},
        })
    cc = (res.get("limit") or {}).get("cross_check") or {}
    if cc.get("official_empty") or not cc.get("is_target_day", True):
        items.append({
            "severity": WARNING,
            "text": "涨跌停官方名单当日无法交叉验证：%s" %
                    ("官方分片 0 行" if cc.get("official_empty")
                     else "最新只到 %s，落后 %s 个交易日" % (cc.get("day"), cc.get("lag_days"))),
            "dept": "quality", "matched_by": "cross_check", "evidence": {"cross_check": cc},
        })
    # 盘后增量链路作为独立观测，不并入主链判断
    if aux and aux.get("verdict") not in (None, "ok"):
        cfg = routes.get("aux_chain") or {}
        rows = [x for x in (aux.get("sets") or [])
                if x.get("verdict") in ("zero_collect", "incomplete", "ahead",
                                        "missing_file", "unreadable", "empty_file")]
        names = [(x.get("table") or x.get("name") or x.get("id") or "?") + ":" + str(x.get("verdict"))
                 for x in rows]
        items.append({
            "severity": WARNING,
            "text": "盘后增量链路逐表巡检 verdict=%s（%d/%d 张表未达标）；"
                    "该链路含 T+1 正常项，须按三件齐复核，不得直接判上游断供"
                    % (aux.get("verdict"), len(rows), len(aux.get("sets") or [])),
            "dept": cfg.get("dept") or "proj_data", "matched_by": "aux_chain",
            "evidence": {"tables": names[:20], "summary": aux.get("summary"),
                         "aux_note": cfg.get("note")},
        })
    return items


def group_by_dept(issues: list[dict], routes: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for it in issues:
        dept = it["dept"] if it["dept"] in (routes.get("departments") or {}) else \
            (routes.get("fallback") or "ops_runtime")
        out.setdefault(dept, []).append(it)
    return out


def dedupe_key(target: str, dept: str, issues: list[dict]) -> str:
    """同日同部门同内容只投一次；内容变了就是另一封（要求重新处理）。"""
    fp = hashlib.sha256("\n".join(i["text"] for i in issues).encode("utf-8")).hexdigest()[:10]
    return "preopen-check-%s-%s-%s" % (target, dept, fp)


def build_letter(dept: str, issues: list[dict], meta: dict, routes: dict) -> tuple[str, str]:
    d = routes["departments"][dept]
    n_blk = sum(1 for i in issues if i["severity"] == BLOCKER)
    pri = BLOCKER if n_blk else WARNING
    title = "[05:30 每日检查] →%s %s：%d 项待处理（%s）" % (
        d["target"], d["name"], len(issues),
        "含 %d 项阻断" % n_blk if n_blk else "告警级")
    lines = [
        "【每日数据检查 · 05:30】数据日 %s / 执行日 %s / 判定 %s" %
        (meta["target"], meta["exec_day"], meta["verdict_cn"]),
        "责任部门：%s（%s）｜职责：%s" % (d["name"], d["target"], d["duty"]),
        "优先级：%s" % ("立即处理" if pri == BLOCKER else "当日盘前处理"),
        "时限：%s" % routes["sla"][pri],
        "-" * 40,
        "本部门待处理 %d 项：" % len(issues),
    ]
    for n, i in enumerate(issues, 1):
        lines.append("%d) [%s] %s" % (n, "阻断" if i["severity"] == BLOCKER else "告警", i["text"]))
        ev = i.get("evidence") or {}
        if ev:
            lines.append("   证据：%s" % json.dumps(ev, ensure_ascii=False)[:600])
        lines.append("   分派依据：%s" % i["matched_by"])
    lines += [
        "-" * 40,
        "统一要求：接到本信的部门立即启动处理，并按本部门时限回执（不得压到收盘后才动）。",
        "回执方式：%s" % routes["reply"]["how"],
        routes["reply"]["note"],
        "复跑验证：python tools/preopen/audit_preopen.py --date %s --json" % meta["target"],
        "口径提醒：上游给空 / 本地漏跑 / 架构停更是三件事，须分开定性；"
        "T+1 源落后 1 日属正常，不得报成停更。",
    ]
    return title[:120], "\n".join(lines)[:4000]


# ---------------------------------------------------------------------------
# 外部链路
# ---------------------------------------------------------------------------
def run_data_health(target: str, out_path: Path) -> dict:
    """盘后增量链路独立巡检。失败要显式记录，不能静默当成「没问题」。"""
    try:
        r = subprocess.run(
            [PY, "-X", "utf8", str(DH), "--target", target,
             "--json-out", str(out_path), "--quiet"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, cwd=str(ROOT),
        )
        if out_path.is_file():
            d = json.loads(out_path.read_text(encoding="utf-8"))
            d["_rc"] = r.returncode
            return d
        return {"verdict": "env_error", "error": "data_health 未产出 JSON",
                "rc": r.returncode, "stderr": (r.stderr or "")[-300:]}
    except Exception as e:  # noqa: BLE001
        return {"verdict": "env_error", "error": "%s: %s" % (type(e).__name__, str(e)[:160])}


def notify_people(reason: str, target: str) -> dict:
    """阻断级判定才红告警到人；沿用健康检查家族既有出口（4h 去重）。"""
    try:
        from notify_hub import notify_red
        ok = notify_red("preopen_daily_check", reason, key="daily-check-fail-" + target)
        return {"attempted": True, "ok": bool(ok)}
    except Exception as e:  # noqa: BLE001
        return {"attempted": True, "ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160])}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run(args: argparse.Namespace) -> dict:
    routes = load_routes(args.routes)
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    cal = ap.trade_cal()
    target, tsource = resolve_target(args.date, cal, today)
    rec: dict = {
        "schema": "preopen/daily_check/v1", "exec_day": today,
        "checked_at": now.astimezone().isoformat(timespec="seconds"),
        "target": target, "target_source": tsource,
        "dry": bool(args.dry), "letters": [], "delivery_errors": [],
    }
    if not target:
        rec.update(state="BLOCKED", reason="无法确定目标日（%s）；未做检查，也未发信" % tsource)
        print("BLOCKED 无法确定目标日：%s（数据未就绪 ≠ 一切正常）" % tsource)
        return rec

    res = ap.audit(target)
    rec["verdict"] = res["verdict"]
    rec["verdict_cn"] = {"ok": "通过", "warn": "有条件通过", "fail": "不通过"}[res["verdict"]]
    rec["audit"] = {k: res.get(k) for k in
                    ("checked_at", "target", "target_source", "sources", "structure",
                     "limit", "consensus", "problems", "warns", "verdict")}

    # 连主源都没有：这是「数据未就绪」，不能落成「无事可做」
    if any("连个股日线都没有" in p for p in (res.get("problems") or [])):
        rec["state"] = "DATA_NOT_READY"
    else:
        rec["state"] = "CHECKED"

    aux = None
    if not args.no_aux:
        out = LEDGER_DIR / ("data_health_%s.json" % target)
        aux = run_data_health(target, out)
        rec["aux_chain"] = {"verdict": aux.get("verdict"), "summary": aux.get("summary"),
                            "error": aux.get("error")}

    issues = build_issues(res, routes, aux)
    rec["n_issues"] = len(issues)
    groups = group_by_dept(issues, routes)
    rec["departments"] = {k: len(v) for k, v in groups.items()}
    meta = {"target": target, "exec_day": today, "verdict_cn": rec["verdict_cn"]}

    for dept in sorted(groups):
        title, body = build_letter(dept, groups[dept], meta, routes)
        key = dedupe_key(target, dept, groups[dept])
        letter = {"dept": dept, "target": routes["departments"][dept]["target"],
                  "priority": BLOCKER if any(i["severity"] == BLOCKER for i in groups[dept]) else WARNING,
                  "n_items": len(groups[dept]), "title": title,
                  "dedupe_key": key, "body_preview": body[:300]}
        if args.dry:
            letter["action"] = "DRY_RUN"
            letter["body"] = body
        else:
            try:
                env = internal_notify.emit(
                    target=letter["target"], title=title, body=body,
                    kind="task_request", source="preopen/daily_check",
                    link=str((LEDGER_DIR / ("check_%s.json" % target)).relative_to(ROOT)),
                    actor="houfeng-preopen", dedupe_key=key,
                    # payload 里的 created_at 必须按数据日固定，否则同日重跑会因
                    # 「同幂等键、不同 payload」被判 idempotency_conflict（11-13 判例）。
                    created_at=target + "T05:30:00+0800",
                )
                data = env.get("data") or {}
                letter["envelope"] = {"ok": bool(env.get("ok")),
                                      "error": env.get("error"), "data": data}
                letter["action"] = ("REPLAYED" if data.get("replayed")
                                    else "SENT" if env.get("ok") else "FAILED")
                letter["bridge_msg_id"] = data.get("id")
                if not env.get("ok"):
                    rec["delivery_errors"].append({"dept": dept, "error": env.get("error")})
            except Exception as e:  # noqa: BLE001
                letter["action"] = "FAILED"
                letter["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
                rec["delivery_errors"].append({"dept": dept, "error": letter["error"]})
        rec["letters"].append(letter)

    # 飞书只给阻断级；告警项已进部门工单，避免告警疲劳
    rec["feishu"] = {"attempted": False, "reason": "非阻断级或未启用"}
    if res["verdict"] == "fail" and not args.dry and not args.no_feishu:
        reason = ("%s 检查不通过：%d 项阻断，已派单 %s；请督促 08:10 前回执"
                  % (target, len(res.get("problems") or []),
                     "、".join(sorted(groups)) or "无"))
        rec["feishu"] = notify_people(reason, target)
    elif args.dry and res["verdict"] == "fail":
        rec["feishu"]["reason"] = "DRY_RUN（阻断级本应红告警到人）"

    # 无事可做必须明确，且只在真的没有待办时出现
    if not issues:
        rec["state"] = "CLEAN"
        print("CLEAN 数据日 %s 全绿，无待处理项，未发信" % target)
    else:
        print("%s 数据日 %s / 判定 %s / %d 项待处理 / 派单部门 %s%s"
              % (rec["state"], target, rec["verdict_cn"], len(issues),
                 ",".join(sorted(groups)), "（DRY_RUN）" if args.dry else ""))
    return rec


def write_ledger(rec: dict) -> Path:
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    p = LEDGER_DIR / ("check_%s.json" % (rec.get("target") or "no_target"))
    old = {"schema": "preopen/daily_check/v1", "runs": []}
    if p.is_file():
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    old.setdefault("runs", []).append(rec)
    old["target"] = rec.get("target")
    old["n_runs"] = len(old["runs"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return p


def main() -> int:
    a = argparse.ArgumentParser(description="05:30 每日数据检查与部门派单")
    a.add_argument("--date", default=None, help="目标日 YYYYMMDD；缺省取今天之前的最后一个开市日")
    a.add_argument("--routes", default=None, help="责任映射表路径")
    a.add_argument("--dry", action="store_true", help="预演：只算不投信、不发送")
    a.add_argument("--no-feishu", action="store_true", help="不发达人红告警")
    a.add_argument("--no-aux", action="store_true", help="跳过盘后增量链路巡检")
    a.add_argument("--no-ledger", action="store_true", help="不写台账（单测用）")
    args = a.parse_args()
    try:
        rec = run(args)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print("BLOCKED 检查脚本异常，未派单（不是「一切正常」）")
        return 3
    if not args.no_ledger:
        try:
            p = write_ledger(rec)
            print("台账 %s" % p.relative_to(ROOT))
        except Exception as e:  # noqa: BLE001
            print("LEDGER_WRITE_ERR %s" % e)
            return 3
    if rec.get("state") == "BLOCKED":
        return 3
    if rec.get("delivery_errors"):
        print("DELIVERY_FAILED %d 个部门未送达（fail-visible，不静默）"
              % len(rec["delivery_errors"]))
        return 3
    if rec.get("feishu", {}).get("attempted") and rec["feishu"].get("ok") is False:
        print("FEISHU_FAILED 红告警未送达（fail-visible）")
        return 3
    # 业务判定写台账与日志独立行；任务退出码只回答「跑完了吗」
    print("BUSINESS_VERDICT=%s STATE=%s N_ISSUES=%d"
          % (rec.get("verdict"), rec.get("state"), rec.get("n_issues", 0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
