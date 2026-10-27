# -*- coding: utf-8 -*-
"""军团员工编号契约（v1）——工号与编制号的唯一发号器

两条编号并行，互不替代：

  ① 全局工号  EMP 6 位：``000001`` 起，只增不减，**永不复用**（离职不回收）。
     用途：跨部门、跨年份的唯一身份，报表/审计/合同都用它。
  ② 当日编制号 ``20261023-情-01``：年月日(8) + 部门字(1) + 当日序号(2)。
     用途：一眼看出"哪天、哪个部门、当天第几个入职"。
     规则：**每个部门每天从 01 开始**；全军团每天最多 100 个号；两位序号上限 99。

三条硬规矩：
  1. **不碰既有席位 ID**：A0 / ST1 / DEV3 这些代号被 tasks.json 的 owner、意图卡契约链、
     org 引用共同使用，改号会断 313 单任务与全部链条。本模块只**新增** emp_no / enroll_no
     两个字段，既有 id 保持不动。
  2. **发号必须留痕**：每次分配记 票号(ticket) + 放行人(signed_by) + 时间；
     缺一项即拒绝（沿用 org_enroll 的纪律，无单不写）。
  3. **失败关闭**：超日限、部门字未知、序号宽度溢出、工号复用，一律抛错，
     绝不"顺手往下排一个"——编号错位比没有编号更难查。

部门字表按 org.json 17 个部门的正式名称定，保证**单字唯一**（情/量/研/交/智/产/质/回/治/设/市/法/运/院/通/参/风）。
改表就要升版本，并同步 test_emp_numbering.py。
"""

from __future__ import annotations

import json
import pathlib
import re
import time

SCHEMA = "legion.emp.ledger/v1"
RULES_VERSION = "1.0"

EMP_WIDTH = 6
EMP_MAX = 10**EMP_WIDTH - 1  # 999999
DATE_LEN = 8
SEQ_WIDTH = 2
SEQ_PER_DEPT_MAX = 10**SEQ_WIDTH - 1  # 99（两位位宽决定的物理上限）
DAILY_CAP_GLOBAL = 100  # 全军团每天不超过 100 个号

# 部门字：与 org.json departments[].dept 一一对应，单字唯一
DEPT_CHAR = {
    "intel": "情",
    "invest": "量",
    "dev": "研",
    "trading": "交",
    "data": "智",
    "product": "产",
    "quality": "质",
    "proj_backtest": "回",
    "proj_data": "治",
    "proj_next": "设",
    "marketing": "市",
    "legal": "法",
    "ops_runtime": "运",
    "academy": "院",
    "comm": "通",
    "chief_line": "参",
    "risk": "风",
}
CHAR_DEPT = {v: k for k, v in DEPT_CHAR.items()}
assert len(CHAR_DEPT) == len(DEPT_CHAR), "部门字必须单字唯一"

EMP_RE = re.compile(r"^\d{%d}$" % EMP_WIDTH)
ENROLL_RE = re.compile(r"^(\d{8})-([\u4e00-\u9fa5])-(\d{%d})$" % SEQ_WIDTH)

# 票号格式（老田 2026-10-23 22:53 定）：前缀-年月日-部门字-第几号
# 例：LEGION-20261023-参-01 / WO-20261023-情-03 / K-20261023-量-08
TICKET_RE = re.compile(
    r"^(WO|K|LEGION)-(\d{8})-([\u4e00-\u9fa5])-(\d{%d})$" % SEQ_WIDTH
)
TICKET_PREFIXES = ("WO", "K", "LEGION")


def make_ticket(prefix: str, date: str, dept: str, seq: int) -> str:
    """按 前缀-年月日-部门字-序号 生成票号。"""
    p = str(prefix or "").strip().upper()
    if p not in TICKET_PREFIXES:
        raise NumberingError(
            "BAD_TICKET_PREFIX", "票号前缀须为 WO / K / LEGION：%r" % prefix
        )
    ch = dept_char(dept)
    if not re.match(r"^\d{8}$", str(date or "")):
        raise NumberingError("BAD_DATE", "日期须为 YYYYMMDD：%r" % date)
    if not (1 <= int(seq) <= SEQ_PER_DEPT_MAX):
        raise NumberingError("SEQ_OUT_OF_RANGE", "序号须在 01–%02d" % SEQ_PER_DEPT_MAX)
    return "%s-%s-%s-%0*d" % (p, date, ch, SEQ_WIDTH, int(seq))


def parse_ticket(ticket: str) -> dict:
    m = TICKET_RE.match(str(ticket or "").strip())
    if not m:
        raise NumberingError(
            "BAD_TICKET",
            "票号格式应为 前缀-YYYYMMDD-部门字-序号（如 LEGION-20261023-参-01）：%r"
            % (ticket,),
        )
    pre, date, ch, seq = m.group(1), m.group(2), m.group(3), int(m.group(4))
    if ch not in CHAR_DEPT:
        raise NumberingError("UNKNOWN_CHAR", "票号里的部门字 %r 不在表中" % ch)
    return {"prefix": pre, "date": date, "char": ch, "dept": CHAR_DEPT[ch], "seq": seq}


class NumberingError(Exception):
    def __init__(self, code: str, msg: str):
        super().__init__("%s: %s" % (code, msg))
        self.code = code
        self.msg = msg


def dept_char(dept: str) -> str:
    ch = DEPT_CHAR.get(str(dept or ""))
    if not ch:
        raise NumberingError(
            "UNKNOWN_DEPT", "未知部门 %r，未在部门字表中登记" % (dept,)
        )
    return ch


def validate_emp_no(no: str) -> str:
    s = str(no or "").strip()
    if not EMP_RE.match(s):
        raise NumberingError("BAD_EMP_NO", "工号必须是 %d 位数字：%r" % (EMP_WIDTH, no))
    if int(s) == 0:
        raise NumberingError("BAD_EMP_NO", "工号不能从 000000 开始，首号为 000001")
    return s


def validate_enroll_no(no: str) -> dict:
    m = ENROLL_RE.match(str(no or "").strip())
    if not m:
        raise NumberingError(
            "BAD_ENROLL_NO", "编制号格式应为 YYYYMMDD-部门字-%02d：%r" % (SEQ_WIDTH, no)
        )
    date, ch, seq = m.group(1), m.group(2), int(m.group(3))
    if ch not in CHAR_DEPT:
        raise NumberingError("UNKNOWN_CHAR", "部门字 %r 不在表中" % ch)
    if not (1 <= seq <= SEQ_PER_DEPT_MAX):
        raise NumberingError("SEQ_OUT_OF_RANGE", "序号须在 01–%02d" % SEQ_PER_DEPT_MAX)
    return {"date": date, "char": ch, "dept": CHAR_DEPT[ch], "seq": seq}


def empty_ledger() -> dict:
    return {
        "schema": SCHEMA,
        "rules_version": RULES_VERSION,
        "next_emp": 1,
        "daily": {},
        "employees": {},
        "retired": {},
    }


def load_ledger(path) -> dict:
    p = pathlib.Path(path)
    if not p.exists():
        return empty_ledger()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise NumberingError("LEDGER_UNREADABLE", "编号台账无法解析：%s" % str(e)[:120])
    if d.get("schema") != SCHEMA:
        raise NumberingError(
            "SCHEMA_MISMATCH", "台账 schema 不符：%r" % d.get("schema")
        )
    for k in ("next_emp", "daily", "employees"):
        d.setdefault(k, empty_ledger()[k])
    d.setdefault("retired", {})
    return d


def _today() -> str:
    return time.strftime("%Y%m%d")


def peek(ledger: dict, seat_id: str, dept: str, date: str | None = None) -> dict:
    """只算不发：返回下一个工号与编制号，不修改台账。"""
    d = date or _today()
    if not re.match(r"^\d{8}$", d):
        raise NumberingError("BAD_DATE", "日期须为 YYYYMMDD：%r" % d)
    ch = dept_char(dept)
    if seat_id in ledger["employees"]:
        raise NumberingError(
            "ALREADY_NUMBERED",
            "%s 已有编号 %s，不重复发放"
            % (seat_id, ledger["employees"][seat_id]["emp_no"]),
        )
    nxt = int(ledger.get("next_emp") or 1)
    if nxt > EMP_MAX:
        raise NumberingError("EMP_EXHAUSTED", "6 位工号已用尽（上限 %d）" % EMP_MAX)
    day = (ledger.get("daily") or {}).get(d) or {"total": 0, "depts": {}}
    if int(day.get("total") or 0) >= DAILY_CAP_GLOBAL:
        raise NumberingError(
            "DAILY_CAP",
            "当日已发 %d 个号，达到全军团日上限 %d"
            % (day.get("total"), DAILY_CAP_GLOBAL),
        )
    used = int((day.get("depts") or {}).get(ch) or 0)
    if used >= SEQ_PER_DEPT_MAX:
        raise NumberingError(
            "DEPT_DAY_CAP",
            "%s 部门当日已发 %d 号，两位序号位宽上限 %d"
            % (dept, used, SEQ_PER_DEPT_MAX),
        )
    emp = "%0*d" % (EMP_WIDTH, nxt)
    enroll = "%s-%s-%0*d" % (d, ch, SEQ_WIDTH, used + 1)
    return {
        "seat": seat_id,
        "dept": dept,
        "emp_no": emp,
        "enroll_no": enroll,
        "date": d,
        "dept_char": ch,
        "seq": used + 1,
        "daily_total_after": int(day.get("total") or 0) + 1,
    }


def assign(
    ledger: dict,
    seat_id: str,
    dept: str,
    *,
    ticket: str,
    signed_by: str,
    date: str | None = None,
    batch: str = "",
) -> dict:
    """正式发号：校验票号格式与对应关系、放行人，再落台账。

    注意（重要）：票号格式是「前缀-年月日-部门字-序号」，与编制号同构，
    所以票据本身<b>可以被人推导</b>，它只能证明"这次发号的依据是哪一条编号"，
    **不能单独构成授权**。授权落在 signed_by（放行人）与 batch（批次）上。
    要真正防越权，批次号必须由归口方独立给出，不能从编号反推。
    """
    tk = parse_ticket(ticket)
    if not str(signed_by or "").strip():
        raise NumberingError("NO_SIGNER", "缺放行人：发号须显式签字")

    p = peek(ledger, seat_id, dept, date)
    if (tk["date"], tk["char"], tk["seq"]) != (p["date"], p["dept_char"], p["seq"]):
        raise NumberingError(
            "TICKET_MISMATCH",
            "票号与本次编号不对应：票 %s-%s-%s-%02d ｜ 号 %s-%s-%02d（防止张冠李戴）"
            % (
                tk["prefix"],
                tk["date"],
                tk["char"],
                tk["seq"],
                p["date"],
                p["dept_char"],
                p["seq"],
            ),
        )

    emp_i = int(p["emp_no"])
    ledger["next_emp"] = emp_i + 1
    day = ledger["daily"].setdefault(p["date"], {"total": 0, "depts": {}})
    day["total"] = int(day.get("total") or 0) + 1
    day["depts"][p["dept_char"]] = (
        int((day.get("depts") or {}).get(p["dept_char"]) or 0) + 1
    )
    rec = {
        "emp_no": p["emp_no"],
        "enroll_no": p["enroll_no"],
        "dept": dept,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ticket": str(ticket).strip(),
        "signed_by": str(signed_by).strip(),
        "batch": str(batch or "").strip(),
    }
    ledger["employees"][seat_id] = rec
    return rec


def retire(
    ledger: dict, seat_id: str, *, ticket: str, signed_by: str, reason: str = ""
) -> dict:
    """注销：工号进 retired 归档，**不回收、不复用**。

    票号只校验格式（注销单不针对某个"当日第几号"，故不要求与编号对应），
    但**必须**有票号与放行人——注销同样是无单不写。
    """
    parse_ticket(ticket)
    if not str(signed_by or "").strip():
        raise NumberingError("NO_SIGNER", "缺放行人：注销须显式签字")
    if seat_id not in ledger["employees"]:
        raise NumberingError("NOT_NUMBERED", "%s 尚未发号" % seat_id)
    rec = ledger["employees"].pop(seat_id)
    rec.update(
        {
            "retired_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "retire_ticket": str(ticket).strip(),
            "retire_by": str(signed_by).strip(),
            "reason": reason[:120],
        }
    )
    ledger["retired"][seat_id] = rec
    return rec


def rehire(ledger: dict, seat_id: str, *, ticket: str, signed_by: str) -> dict:
    """复职：**恢复原工号**，不占用新号。

    为什么单列一个函数：如果复职走 assign，会拿到一个全新工号，等于同一个人有两个号，
    "工号永不复用"就变成了"同人可多号"，人事档案立刻分叉。
    """
    parse_ticket(ticket)
    if not str(signed_by or "").strip():
        raise NumberingError("NO_SIGNER", "缺放行人：复职须显式签字")
    if seat_id in ledger["employees"]:
        raise NumberingError("ALREADY_ACTIVE", "%s 在职，无需复职" % seat_id)
    old = (ledger.get("retired") or {}).pop(seat_id, None)
    if not old:
        raise NumberingError("NOT_RETIRED", "%s 不在注销档案里，无号可复" % seat_id)
    rec = {
        k: v
        for k, v in old.items()
        if k not in ("retired_at", "retire_ticket", "retire_by", "reason")
    }
    rec["rehired_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    rec["rehire_ticket"] = str(ticket).strip()
    rec["rehire_by"] = str(signed_by).strip()
    ledger["employees"][seat_id] = rec
    return rec


def summary(ledger: dict, date: str | None = None) -> dict:
    d = date or _today()
    day = (ledger.get("daily") or {}).get(d) or {"total": 0, "depts": {}}
    return {
        "date": d,
        "issued_today": int(day.get("total") or 0),
        "daily_cap": DAILY_CAP_GLOBAL,
        "by_dept": dict(day.get("depts") or {}),
        "next_emp": "%0*d" % (EMP_WIDTH, int(ledger.get("next_emp") or 1)),
        "active": len(ledger.get("employees") or {}),
        "retired": len(ledger.get("retired") or {}),
    }
