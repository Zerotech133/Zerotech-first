# -*- coding: utf-8 -*-
"""monthly_report.py —— 军团月报生成器（W37，账本投影器，只读）。

口径（承《2026-10-19_周报月报立项预研施工单》f5e112ca W37 条款 +
2026-10-19 实装授权书）：

- 三大投影段（五段 schema）：
  ①月概览 ②成本勾稽表 ③当月施工盘点 ④编制差集表 ⑤三态声明与缺件清单。
- 成本勾稽（budget_ledger，真源在 digital-legion/paperclip，跨仓只读）：
  对结算月内全部 WO 流水验证恒等式——
    D1 预扣（wo:<id>:pre-hold）+ ΣD2 补扣（wo:<id>:meter）
    + D3a 差额（settle-extra 正 / settle-refund 负）+ D3b 全退（abort-refund 负）
    ≡ 实际净成本；rejected 单恒等式：D1 预扣 = |D3b 全退|；
  余额链：逐行 balance_after 与前行差额 = 当行 delta_cents（db.py/app.py 口径）。
  跨月工单按结算月归段（账本行 created_at 归月）。
  审计哈希链本地复算作旁证（app.py /audit/verify 同构，只读不改账）。
  成本段只出对账结果表，不出「成本分析」「趋势解读」——解读是账本没有的字段。
- 判例/施工盘点：git log --since=当月1日 -- docs/legion/ 全列原文转抄。
- 编制对账：registry/org.json（departments[].agents + chief）vs hr/ 目录双向
  差集——差集如实列示，不做合并建议（合并属组织动作，须过会）；
  口径引用 HR 盘点单 541b1690（只读盘点）。
- budget_ledger 缺失/当月零流水 → 成本段 INCONCLUSIVE「零流水月/账本缺失」，
  不是 OK（判例 6：空集通过=假绿）。

CLI：
    python monthly_report.py [--month YYYY-MM] [--out <path>]
缺省（无 --month）：月一日自动投影最近完整自然月（上月）；非月一日自跳过
（exit 0 不写文件，参照 weekly_report 非周一守卫范式，调度器 daily 挂载口径）。

库文件路径（paperclip/db.py:62-64 口径）：环境变量 PAPERCLIP_DB_PATH，
缺省 digital-legion/paperclip/paperclip.db（本地未落库时如实 INCONCLUSIVE）。

本脚本零账本写：sqlite 只读连接（mode=ro）、git log 只读、目录只读；
唯一落盘 = --out 指定的报告产物。零依赖（标准库）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA_VERSION = "monthly.v1"
TRI_EXPLAIN = ("三态释义：INCONCLUSIVE=如实缺据，见缺件清单；"
               "DEGRADED=账实不符，见双列数据。")
TRI_ORDER = {"OK": 0, "INCONCLUSIVE": 1, "DEGRADED": 2}
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ap_root() -> Path:
    return Path(__file__).resolve().parents[1]


def worst(*states: str) -> str:
    return max(states, key=lambda s: TRI_ORDER.get(s, 2))


def month_window(month: str | None, today: date | None = None) -> str:
    """解析 --month（YYYY-MM）；缺省=月一日自动投影最近完整自然月（上月），
    非月一日自跳过（exit 0 不写文件，参照 weekly_report 非周一守卫范式，
    调度器 daily 挂载口径）。--month 显式给参时（离线/补跑）不做守卫。"""
    if month:
        if not MONTH_RE.match(month):
            raise SystemExit(f"--month 格式非法：{month}（应为 YYYY-MM）")
        return month
    today = today or date.today()
    if today.day != 1:
        print(f"[skip] 今日 {today} 非月一日，月报生成器自跳过（--month 可显式指定月）")
        raise SystemExit(0)
    prev_end = today.replace(day=1) - timedelta(days=1)
    return f"{prev_end.year:04d}-{prev_end.month:02d}"


# ---------------------------------------------------------------- ①/② 成本勾稽

def paperclip_db_path(root: Path) -> Path:
    env = os.environ.get("PAPERCLIP_DB_PATH")
    if env:
        return Path(env)
    return root / "digital-legion" / "paperclip" / "paperclip.db"


def _open_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def ledger_month_rows(conn: sqlite3.Connection, month: str) -> list[dict]:
    """结算月归段：按账本行 created_at 前缀过滤（跨月工单按结算月归段）。"""
    rows = conn.execute(
        "SELECT l.id, l.account_id, l.delta_cents, l.reason, l.wo_id, "
        "l.balance_after, l.created_at FROM budget_ledger l "
        "ORDER BY l.account_id, l.id").fetchall()
    return [dict(r) for r in rows if str(r["created_at"])[:7] == month]


def verify_balance_chain(rows: list[dict]) -> list[dict]:
    """逐账户余额链：balance_after[i] - balance_after[i-1] == delta_cents[i]。"""
    fails: list[dict] = []
    prev: dict[int, int] = {}
    for r in rows:
        acct = r["account_id"]
        expected_delta = (r["balance_after"] - prev[acct]) if acct in prev else r["balance_after"]
        if expected_delta != r["delta_cents"]:
            fails.append({"ledger_id": r["id"], "account_id": acct,
                          "balance_after": r["balance_after"],
                          "prev_balance_after": prev.get(acct),
                          "delta_cents": r["delta_cents"],
                          "expect_delta": expected_delta})
        prev[acct] = r["balance_after"]
    return fails


def verify_wo_identities(rows: list[dict]) -> list[dict]:
    """逐单恒等式（成本口径：账本 delta 扣减为负，此处换算为成本正数，
    与施工单恒等式「D1+ΣD2+D3a 差额+D3b ≡ actual_cost」同向）：
    - done：净成本 = D1 + ΣD2 + extra - refund ≥ 0；settle-extra 与
      settle-refund 不并存；done 单不得出现 abort-refund；
      refund 不得超过已扣（D1+ΣD2+extra）。
    - rejected：D1 = abort-refund 全退；出现 meter/settle 行=勾稽 FAIL。
    - executing：仅允许 D1/meter 行，出现结算行=FAIL。
    符号纪律（账实相符）：pre-hold/meter/settle-extra 账本 delta 必须为负
    （_try_spend_conn 扣减），settle-refund/abort-refund 必须为正
    （_credit_conn 入账）——符号反挂=勾稽 FAIL。
    """
    wo: dict[str, dict] = {}
    for r in rows:
        wid = r["wo_id"] or "(无工单号)"
        d = wo.setdefault(wid, {"pre": 0, "meter": 0, "extra": 0, "refund": 0,
                                "abort": 0, "rows": 0, "sign_bad": []})
        reason = str(r["reason"])
        delta = r["delta_cents"]
        d["rows"] += 1
        if reason.endswith(":pre-hold"):
            d["pre"] += abs(delta)
            if delta >= 0:
                d["sign_bad"].append(f"{reason} delta={delta} 应为负（扣减）")
        elif reason.endswith(":meter"):
            d["meter"] += abs(delta)
            if delta >= 0:
                d["sign_bad"].append(f"{reason} delta={delta} 应为负（扣减）")
        elif reason.endswith(":settle-extra"):
            d["extra"] += abs(delta)
            if delta >= 0:
                d["sign_bad"].append(f"{reason} delta={delta} 应为负（补扣）")
        elif reason.endswith(":settle-refund"):
            d["refund"] += abs(delta)
            if delta <= 0:
                d["sign_bad"].append(f"{reason} delta={delta} 应为正（退差额）")
        elif reason.endswith(":abort-refund"):
            d["abort"] += abs(delta)
            if delta <= 0:
                d["sign_bad"].append(f"{reason} delta={delta} 应为正（全退）")
    out = []
    for wid, d in sorted(wo.items()):
        problems = list(d["sign_bad"])
        net = d["pre"] + d["meter"] + d["extra"] - d["refund"] - d["abort"]
        if d["extra"] > 0 and d["refund"] > 0:
            problems.append("settle-extra 与 settle-refund 并存（恒等式歧义）")
        if d["abort"] > 0:
            if d["pre"] != d["abort"]:
                problems.append(f"rejected 恒等式不成立：D1 预扣 {d['pre']} != |D3b 全退| {d['abort']}")
            if d["meter"] or d["extra"] or d["refund"]:
                problems.append("abort 单含 meter/settle 行（与 D3b 全退口径冲突）")
        else:
            if d["refund"] > d["pre"] + d["meter"] + d["extra"]:
                problems.append(f"settle-refund {d['refund']} 超过已扣 {d['pre'] + d['meter'] + d['extra']}")
            if net < 0:
                problems.append(f"净成本为负：{net}")
        out.append({"wo_id": wid, "pre": d["pre"], "meter": d["meter"],
                    "extra": d["extra"], "refund": d["refund"], "abort": d["abort"],
                    "net": net, "verdict": "FAIL" if problems else "PASS",
                    "problems": problems})
    return out


def verify_audit_chain(conn: sqlite3.Connection) -> dict:
    """审计哈希链本地复算（/audit/verify 只读同构旁证）。"""
    GENESIS = "0" * 64
    rows = conn.execute("SELECT seq, ts, actor, event_type, wo_id, payload, "
                        "prev_hash, hash FROM audit_event ORDER BY seq").fetchall()
    prev = GENESIS
    bad = 0
    for r in rows:
        if r["prev_hash"] != prev:
            bad += 1
        material = f"{r['seq']}|{r['ts']}|{r['actor']}|{r['event_type']}|{r['wo_id']}|{r['payload']}|{r['prev_hash']}"
        # payload 已是 sort_keys 序列化串（app.py audit_append 落盘口径）
        h = hashlib.sha256(material.encode("utf-8")).hexdigest()
        if h != r["hash"]:
            bad += 1
        prev = r["hash"]
    return {"rows": len(rows), "bad": bad,
            "verdict": "PASS" if (bad == 0 and rows) else
                       ("INCONCLUSIVE" if not rows else "FAIL")}


def cost_section(root: Path, month: str) -> tuple[str, str]:
    """返回 (段文本, 段三态)。"""
    dbf = paperclip_db_path(root)
    if not dbf.is_file():
        txt = (f"## ②成本勾稽表\n> 锚: digital-legion/paperclip/paperclip.db | 态: INCONCLUSIVE"
               f"（账本缺失：{dbf} 不存在，PAPERCLIP_DB_PATH 未指向实存库）\n"
               f"- {month} 月无 budget_ledger 可勾稽——零流水月/账本缺失，如实 INCONCLUSIVE，"
               f"不报 OK（判例 6：空集通过=假绿），不编文\n")
        return txt, "INCONCLUSIVE"
    try:
        conn = _open_ro(dbf)
    except sqlite3.Error as e:
        return (f"## ②成本勾稽表\n> 锚: {dbf} | 态: INCONCLUSIVE（账本不可读：{e}）\n",
                "INCONCLUSIVE")
    try:
        rows = ledger_month_rows(conn, month)
        if not rows:
            return (f"## ②成本勾稽表\n> 锚: {dbf} | 态: INCONCLUSIVE（零流水月："
                    f"{month} 无账本行——空集不构成 PASS 证据）\n"), "INCONCLUSIVE"
        chain_fails = verify_balance_chain(rows)
        wo_rows = verify_wo_identities(rows)
        audit = verify_audit_chain(conn)
        fail_wo = [w for w in wo_rows if w["verdict"] == "FAIL"]
        state = "OK"
        notes = []
        if chain_fails:
            state = "DEGRADED"
            notes.append(f"余额链断点 {len(chain_fails)} 处")
        if fail_wo:
            state = "DEGRADED"
            notes.append(f"恒等式 FAIL 单 {len(fail_wo)} 张")
        if audit["verdict"] != "PASS":
            state = worst(state, "DEGRADED")
            notes.append(f"审计链 {audit['verdict']}（bad={audit['bad']}）")
        note_s = "；".join(notes) if notes else "恒等式与余额链全过"
        L = [f"## ②成本勾稽表\n> 锚: {dbf} | 态: {state}（{note_s}；"
             f"结算月归段=账本行 created_at 前缀 {month}）"]
        L.append(f"- 账本行：{len(rows)} 行；工单：{len(wo_rows)} 张；"
                 f"扣减点口径=pre-hold/meter/settle-extra/settle-refund/abort-refund"
                 f"（paperclip/app.py D1/D2/D3a/D3b）")
        L.append(f"- 审计哈希链旁证（本地复算 prev_hash 咬合+sha256）：{audit['verdict']}"
                 f"（{audit['rows']} 行 / bad={audit['bad']}）")
        for w in wo_rows:
            verdict = w["verdict"]
            line = (f"- {w['wo_id']}：D1={w['pre']} D2={w['meter']} "
                    f"extra={w['extra']} refund={w['refund']} abort={w['abort']} "
                    f"净额={w['net']} -> {verdict}")
            if w["problems"]:
                line += "；问题：" + "；".join(w["problems"])
            L.append(line + f"（出处：budget_ledger reason=wo:{w['wo_id']}:*）")
        for f_ in chain_fails:
            L.append(f"- 余额链 FAIL：ledger_id={f_['ledger_id']} account={f_['account_id']} "
                     f"balance_after={f_['balance_after']} 前行={f_['prev_balance_after']} "
                     f"delta={f_['delta_cents']} 期望={f_['expect_delta']}")
        return "\n".join(L) + "\n", state
    finally:
        conn.close()


# ---------------------------------------------------------------- ③ 施工盘点

def build_inventory_section(root: Path, month: str) -> tuple[str, str]:
    """git log --since 当月1日 -- docs/legion/ 全列原文转抄。"""
    since = f"{month}-01"
    y, m = int(month[:4]), int(month[5:7])
    nxt = date(y + (m == 12), (m % 12) + 1, 1).isoformat()
    try:
        p = subprocess.run(
            ["git", "-C", str(root), "log", f"--since={since}",
             f"--until={nxt}", "--oneline", "--", "docs/legion/"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60)
        lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    except (OSError, subprocess.TimeoutExpired) as e:
        return (f"## ③当月施工盘点\n> 锚: docs/legion/ | 态: INCONCLUSIVE"
                f"（git 不可用：{e}）\n"), "INCONCLUSIVE"
    if not lines:
        return (f"## ③当月施工盘点\n> 锚: docs/legion/ | 态: INCONCLUSIVE"
                f"（当月零入库——空集不报 OK；取证命令 git log --since={since}）\n"
                ), "INCONCLUSIVE"
    L = [f"## ③当月施工盘点\n> 锚: docs/legion/ | 态: OK"
         f"（{len(lines)} 条原文转抄，零增删改写；取证命令 git log --since={since} "
         f"--until={nxt} --oneline -- docs/legion/）"]
    for ln in lines:
        L.append(f"- {ln}")
    return "\n".join(L) + "\n", "OK"


# ---------------------------------------------------------------- ④ 编制对账

def build_org_section(root: Path) -> tuple[str, str]:
    """root=仓根。org/hr 事实源在 agent_platform/ 下。
    对账口径引用 HR 盘点单 541b1690（hr/ resume 授名 diff 实核与处置分档，
    只读盘点）：差集如实列示，收编/清退属组织动作，不做合并建议（须过会）。"""
    org_f = root / "agent_platform" / "registry" / "org.json"
    hr_d = root / "agent_platform" / "hr"
    if not org_f.is_file() or not hr_d.is_dir():
        return (f"## ④编制差集表\n> 锚: agent_platform/registry/org.json ; agent_platform/hr/ | "
                f"态: INCONCLUSIVE（事实源缺失）\n"), "INCONCLUSIVE"
    try:
        j = json.loads(org_f.read_text(encoding="utf-8"))
    except ValueError as e:
        return (f"## ④编制差集表\n> 锚: agent_platform/registry/org.json ; agent_platform/hr/ | "
                f"态: INCONCLUSIVE（org.json 不可解析：{e}——不编文）\n"), "INCONCLUSIVE"
    org: set[str] = set()
    for dep in j.get("departments", []):
        for a in dep.get("agents", []):
            org.add(str(a))
    chief = j.get("chief")
    if isinstance(chief, dict) and chief.get("agent_id"):
        org.add(str(chief["agent_id"]))
    hr = {p.name for p in hr_d.iterdir() if p.is_dir()}
    org_no_hr = sorted(org - hr)   # 登记缺 profile
    hr_no_org = sorted(hr - org)   # 幽灵目录
    L = [f"## ④编制差集表\n> 锚: agent_platform/registry/org.json ; agent_platform/hr/ | "
         f"态: OK（双向 diff 投影，口径=HR 盘点单 541b1690；差集如实列示，处置归过会）"]
    L.append(f"- org.json 在编引用：{len(org)} 个；hr/ 实存目录：{len(hr)} 个"
             f"（出处：org.json departments[].agents+chief.agent_id、hr/ dir 列表）")
    L.append(f"- org 有 hr 无（登记缺 profile）：{('、'.join(org_no_hr)) if org_no_hr else '（空）'}")
    L.append(f"- hr 有 org 无（幽灵目录）：{('、'.join(hr_no_org)) if hr_no_org else '（空）'}")
    return "\n".join(L) + "\n", "OK"


# ---------------------------------------------------------------- 数字反查钩子

def recheck(text: str, base: Path) -> dict:
    """W38 查3 复算钩子（check_report_isolation 调用）：对月报全部派生数字
    与逐行转抄重算比对。返回 {violations, checked, notes}。base=仓根。"""
    import check_report_isolation as cri

    rep = cri.parse_report(text)
    secs = {s["title"]: "\n".join(s["lines"]) for s in rep["sections"]}

    def sec_of(prefix: str) -> str:
        for t, body in secs.items():
            if t.startswith(prefix):
                return body
        return ""

    violations: list[str] = []
    checked = 0
    notes: list[str] = []
    m = re.search(r"# 军团月报 (\d{4}-\d{2})", text)
    if not m:
        return {"violations": ["查3：报告头缺月份标题，无法复算"], "checked": 0,
                "notes": notes}
    month = m.group(1)

    # ②成本勾稽
    s2 = sec_of("## ②")
    dbf = paperclip_db_path(base)
    if not dbf.is_file():
        checked += 1
        if "账本缺失" not in s2:
            violations.append("查3-②：账本缺失但②段未如实声明 INCONCLUSIVE")
    else:
        try:
            conn = _open_ro(dbf)
        except sqlite3.Error as e:
            violations.append(f"查3-②：账本不可读 -> {e}")
            conn = None
        if conn is not None:
            try:
                rows = ledger_month_rows(conn, month)
                if not rows:
                    checked += 1
                    if "零流水月" not in s2:
                        violations.append("查3-②：零流水月未如实声明")
                else:
                    chain_fails = verify_balance_chain(rows)
                    wo_rows = verify_wo_identities(rows)
                    audit = verify_audit_chain(conn)
                    for w in wo_rows:
                        exp = (f"- {w['wo_id']}：D1={w['pre']} D2={w['meter']} "
                               f"extra={w['extra']} refund={w['refund']} "
                               f"abort={w['abort']} 净额={w['net']} -> {w['verdict']}")
                        if exp in s2:
                            checked += 1
                        else:
                            violations.append(f"查3-②：逐单恒等式行不符 -> {w['wo_id']}")
                    exp_audit = f"审计哈希链旁证（本地复算 prev_hash 咬合+sha256）：{audit['verdict']}"
                    if exp_audit in s2:
                        checked += 1
                    else:
                        violations.append("查3-②：审计链旁证行不符")
                    if not chain_fails and "余额链 FAIL" in s2:
                        violations.append("查3-②：报告列了余额链 FAIL 但复算全过")
                    checked += 1
            finally:
                conn.close()

    # ③当月施工盘点（git log 原文逐行 diff）
    s3 = sec_of("## ③")
    since = f"{month}-01"
    y, mo = int(month[:4]), int(month[5:7])
    nxt = date(y + (mo == 12), (mo % 12) + 1, 1).isoformat()
    try:
        p = subprocess.run(
            ["git", "-C", str(base), "log", f"--since={since}", f"--until={nxt}",
             "--oneline", "--", "docs/legion/"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60)
        glines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    except (OSError, subprocess.TimeoutExpired):
        glines = None
    if glines is None:
        notes.append("查3-③：git 不可用，盘点段未复算")
    else:
        for ln in glines:
            checked += 1
            if f"- {ln}" not in s3:
                violations.append(f"查3-③：盘点清单与 git log 原文 diff≠0 -> {ln[:50]}")

    # ④编制对账
    s4 = sec_of("## ④")
    org_f = base / "agent_platform" / "registry" / "org.json"
    hr_d = base / "agent_platform" / "hr"
    if org_f.is_file() and hr_d.is_dir():
        j = json.loads(org_f.read_text(encoding="utf-8"))
        org: set[str] = set()
        for dep in j.get("departments", []):
            for a in dep.get("agents", []):
                org.add(str(a))
        chief = j.get("chief")
        if isinstance(chief, dict) and chief.get("agent_id"):
            org.add(str(chief["agent_id"]))
        hr = {q.name for q in hr_d.iterdir() if q.is_dir()}
        exp_cnt = f"org.json 在编引用：{len(org)} 个；hr/ 实存目录：{len(hr)} 个"
        if exp_cnt in s4:
            checked += 1
        else:
            violations.append(f"查3-④：编制计数不符 -> {exp_cnt}")
        for label, diff in (("org 有 hr 无", sorted(org - hr)),
                            ("hr 有 org 无", sorted(hr - org))):
            exp_l = f"- {label}（{'登记缺 profile' if 'org 有' in label else '幽灵目录'}）："
            exp_l += ("、" .join(diff)) if diff else "（空）"
            if exp_l in s4:
                checked += 1
            else:
                violations.append(f"查3-④：差集行不符 -> {label}: {diff}")
    else:
        notes.append("查3-④：org.json/hr 缺失，编制段未复算")

    # ①月概览（状态行与②③④锚行一致性）
    s1 = sec_of("## ①")
    for tag, prefix in (("成本勾稽段", "## ②"), ("施工盘点段", "## ③"),
                        ("编制对账段", "## ④")):
        sec = next((s for s in rep["sections"] if s["title"].startswith(prefix)), None)
        if sec is None:
            continue
        _a, _paths, state = cri.section_anchor(sec)
        if state is None:
            continue
        checked += 1
        if f"- {tag}：{state}" not in s1:
            violations.append(f"查3-①：概览状态与{tag}不一致（{tag}段锚态={state}）")
    return {"violations": violations, "checked": checked, "notes": notes}


# ---------------------------------------------------------------- 主装配

def build_report(root: Path, month: str) -> str:
    cost_txt, cost_state = cost_section(root, month)
    inv_txt, inv_state = build_inventory_section(root, month)
    org_txt, org_state = build_org_section(root)

    hdr = worst(cost_state, inv_state, org_state)
    L = [f"# 军团月报 {month}",
         f"> report_schema_version: {SCHEMA_VERSION} | 报告级三态: {hdr} | "
         f"生成时刻: {datetime.now().isoformat(sep=' ', timespec='seconds')}",
         f"> {TRI_EXPLAIN}", ""]
    # ① 月概览（三段各一行，出处即下列各段锚）
    n_inv = len(re.findall(r"^- ", inv_txt, flags=re.M))
    L.append(f"## ①月概览\n> 锚: 本报告②③④段 | 态: {hdr}（三段计数汇总，"
             f"出处即下列各段锚）")
    L.append(f"- 成本勾稽段：{cost_state}（逐单明细见②段锚行与行项）")
    L.append(f"- 施工盘点段：{inv_state}（git log 条目 {n_inv} 条，原文见③段）")
    L.append(f"- 编制对账段：{org_state}")
    L.append("")
    L.append(cost_txt)
    L.append(inv_txt)
    L.append(org_txt)
    L.append(f"## ⑤三态声明与缺件清单\n> 锚: 本报告各段 | 态: {hdr}")
    L.append(f"> {TRI_EXPLAIN}")
    if hdr != "OK":
        L.append(f"- 非全绿声明：报告级 {hdr}，各段三态见逐段锚行；缺件清单以各段锚行括注为准")
    return "\n".join(L).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="军团月报生成器（账本投影，只读）")
    ap.add_argument("--month", help="结算月 YYYY-MM（账本行 created_at 归月）；"
                                    "缺省=月一日自动投影最近完整自然月，非月一日自跳过")
    ap.add_argument("--out", help="产物路径；缺省 agent_platform/data/reports/monthly_<月>.md")
    args = ap.parse_args(argv)
    month = month_window(args.month)
    out = Path(args.out) if args.out else ap_root() / "data" / "reports" / f"monthly_{month}.md"
    text = build_report(repo_root(), month)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"[ok] 月报已投影 -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
