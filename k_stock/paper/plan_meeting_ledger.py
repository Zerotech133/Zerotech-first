# -*- coding: utf-8 -*-
"""D-4+E-4 计划与会议版本账（WO-QT-20261020-006，老田 2026-10-20 金融游戏定纲）。

会议不是形式批准单，而是把分歧变成可复用策略资产的机制：会前保留各自
判断（投研原计划 vs 盯盘判断 vs 操盘倾向各自的立场+理由），会后记录
维持/修订/否决了什么及为什么——**只存共识=丢掉最有价值的反例与分歧**。
快照引用的必须是「信号时刻版本」：信号触发时已知道什么，就只能引用
当时已生效的预案版本。

两张账本，均 append-only（SQLite 触发器层拒绝 UPDATE/DELETE）：

- plan_versions：个股预案版本链。同 (combo_id, stock) 可挂多版本
  （保守/激进等风险画像），生效时点各不相同；按「信号时刻」可反查
  当时生效版本（as_of_plan）。
- meeting_records：会议账。议题引用信号 id 或股票+日期，记录会前分歧
  与会后结论（维持/修订/否决）；修订指向新 plan_version，全链可追溯。

禁回写三道防线（本单核心验收）：
  1. 决策快照 capture 关联 plan_version 指针时（decision_snapshot，
     WO-QT-20261020-006 加法演进），强制 plan 生效时点 ≤ 快照时刻——
     「用会后版本冒充信号时刻版本」在写入时即被拒（closed on failure）；
  2. 审计断言 API assert_snapshot_plan：对既有快照（含本功能上线前的
     存量/外部写入的快照）断言其引用的 plan 版本生效时点 ≤ 快照时刻，
     违例即拒并留痕；
  3. 会议账写入时强制会后生效时点 > 议题引用的信号/快照时间
     （topic_time），且修订指向的新版本生效时点 ≥ 会议生效时点。

append-only 语义选择（拒绝改写，不选「允许追加更正」二选一中的前者+
更正记录）：UPDATE/DELETE 由触发器直接 ABORT——理由：改写会抹掉
「曾经错着」的证据，违背定纲「反例与分歧是最有价值的资产」；需要更正
时追加一条新记录（meeting_records.corrects_meeting_id 指向被更正记录，
plan_versions 追加新版本 prev_version 指向前序），账本完整保留演变史。

失败语义 closed on failure：任何校验不合法显式抛 PlanLedgerError/
PlanBackdateError，禁静默降级；禁回写违例先留痕再抛（留痕 jsonl
append-only，与 -005 signal_rejections 同风格）。

未接线说明（如实挂账）：批 3 D-4 会议议题自动化将从本账
meeting_records.dissent_json 取「会前分歧」料；engine.exec_run→capture
的 plan_version 透传尚未接线（引擎不给该参数=零变化），属后续工单。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

PLAN_LEDGER_SCHEMA_VERSION = 1

# 会议结论白名单（closed on failure）
CONCLUSIONS = ("maintain", "revise", "veto")
TOPIC_TYPES = ("signal", "stock_date")

# 禁回写留痕文件（append-only jsonl；测试注入临时路径）
DEFAULT_BACKDATE_LOG = (
    Path(__file__).resolve().parent.parent / "data" / "plan_backdate_attempts.jsonl"
)


class PlanLedgerError(Exception):
    """版本账校验失败（缺字段/找不到版本/结构非法等）。携带 reason_code。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(f"[{reason_code}] {message}")
        self.reason_code = reason_code


class PlanBackdateError(PlanLedgerError):
    """禁回写违例（会后版本冒充信号时刻版本/会议生效时点早于议题时刻）。

    抛出前已留痕（append-only jsonl）。"""


def _parse_iso(s: str, what: str) -> datetime:
    """解析 ISO 时刻文本；aware 一律折算为本地 naive 时刻再比较（口径统一）。"""
    if not isinstance(s, str) or not s.strip():
        raise PlanLedgerError("time_invalid", f"{what} 须为非空 ISO 时刻文本: {s!r}")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise PlanLedgerError(
            "time_invalid", f"{what} 不是可解析 ISO 时刻: {s!r}"
        ) from e
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


_DDL = """
CREATE TABLE IF NOT EXISTS plan_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    combo_id TEXT NOT NULL,
    stock TEXT NOT NULL,
    plan_version TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    risk_profile TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    source TEXT NOT NULL,
    prev_version TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(combo_id, stock, plan_version)
);
CREATE TABLE IF NOT EXISTS meeting_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT NOT NULL UNIQUE,
    topic_type TEXT NOT NULL,
    topic_ref TEXT NOT NULL,
    topic_time TEXT NOT NULL,
    dissent_json TEXT NOT NULL,
    conclusion TEXT NOT NULL,
    revised_plan_version TEXT,
    effective_at TEXT NOT NULL,
    attendees_json TEXT NOT NULL,
    corrects_meeting_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS plan_versions_no_update BEFORE UPDATE ON plan_versions
BEGIN SELECT RAISE(ABORT, 'plan_versions is append-only (update forbidden)'); END;
CREATE TRIGGER IF NOT EXISTS plan_versions_no_delete BEFORE DELETE ON plan_versions
BEGIN SELECT RAISE(ABORT, 'plan_versions is append-only (delete forbidden)'); END;
CREATE TRIGGER IF NOT EXISTS meeting_records_no_update BEFORE UPDATE ON meeting_records
BEGIN SELECT RAISE(ABORT, 'meeting_records is append-only (update forbidden)'); END;
CREATE TRIGGER IF NOT EXISTS meeting_records_no_delete BEFORE DELETE ON meeting_records
BEGIN SELECT RAISE(ABORT, 'meeting_records is append-only (delete forbidden)'); END;
"""


def ensure_table(con) -> None:
    """建表+触发器（幂等）。

    短路规则（WO-QT-20261020-012 缺陷修复）：两表与四触发器齐备时**不执行
    任何 DDL**——Python sqlite3 在 DDL/executescript 前会隐式提交当前未决
    事务；若在落单—快照同事务窗口内建表，落单会被提前提交，破坏 D-1
    「快照失败=落单一并失败」的 fail-visible 语义。缺任一对象时才建。
    """
    need_tables = ("plan_versions", "meeting_records")
    need_triggers = (
        "plan_versions_no_update",
        "plan_versions_no_delete",
        "meeting_records_no_update",
        "meeting_records_no_delete",
    )
    have_tables = {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?)",
            need_tables,
        ).fetchall()
    }
    have_triggers = {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name IN (?,?,?,?)",
            need_triggers,
        ).fetchall()
    }
    if have_tables.issuperset(need_tables) and have_triggers.issuperset(need_triggers):
        return
    con.executescript(_DDL)


def append_backdate_trace(log_path: Path | str, entry: dict) -> dict:
    """禁回写违例留痕：append-only 追加一行 jsonl（不 update 不 delete）。"""
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _backdate_entry(stage: str, **kw) -> dict:
    e = {
        "ts": _now(),
        "kind": "plan_backdate_attempt",
        "stage": stage,
        "ledger_schema_version": PLAN_LEDGER_SCHEMA_VERSION,
    }
    e.update(kw)
    return e


# ---------- plan_versions：个股预案版本链 ----------


def add_plan_version(
    con,
    *,
    combo_id: str,
    stock: str,
    plan_version: str,
    plan: dict,
    risk_profile: str,
    effective_at: str,
    source: str,
    prev_version: str | None = None,
) -> dict:
    """追加一条预案版本（append-only：同 (combo_id, stock, plan_version)
    重复即拒，无 update 路径）。

    prev_version 给定时必须指向同 (combo_id, stock) 的既有版本（版本链
    不断裂；前序版本须先入库——append-only 追加顺序即演变顺序）。
    """
    ensure_table(con)
    for k, v in (
        ("combo_id", combo_id),
        ("stock", stock),
        ("plan_version", plan_version),
        ("risk_profile", risk_profile),
        ("source", source),
    ):
        if not isinstance(v, str) or not v.strip():
            raise PlanLedgerError(
                "field_invalid", f"plan_versions.{k} 须为非空字符串: {v!r}"
            )
    if not isinstance(plan, dict) or not plan:
        raise PlanLedgerError(
            "plan_invalid", f"plan 须为非空 dict（买卖点/仓位方案）: {plan!r}"
        )
    _parse_iso(effective_at, "plan_versions.effective_at")
    if prev_version is not None:
        if not isinstance(prev_version, str) or not prev_version.strip():
            raise PlanLedgerError(
                "prev_invalid", f"prev_version 须为非空字符串或 None: {prev_version!r}"
            )
        row = con.execute(
            "SELECT 1 FROM plan_versions WHERE combo_id=? AND stock=? AND plan_version=?",
            (combo_id, stock, prev_version),
        ).fetchone()
        if row is None:
            raise PlanLedgerError(
                "prev_not_found",
                f"prev_version {prev_version!r} 在 ({combo_id}, {stock}) 版本链中不存在"
                "（版本链不可断裂，前序须先入库）",
            )
    if con.execute(
        "SELECT 1 FROM plan_versions WHERE combo_id=? AND stock=? AND plan_version=?",
        (combo_id, stock, plan_version),
    ).fetchone():
        raise PlanLedgerError(
            "duplicate_plan_version",
            f"({combo_id}, {stock}, {plan_version}) 已存在——append-only 禁覆盖，"
            "新预案请用新 plan_version 追加",
        )
    row = {
        "combo_id": combo_id,
        "stock": stock,
        "plan_version": plan_version,
        "plan_json": json.dumps(plan, ensure_ascii=False),
        "risk_profile": risk_profile,
        "effective_at": effective_at,
        "source": source,
        "prev_version": prev_version,
        "created_at": _now(),
    }
    con.execute(
        "INSERT INTO plan_versions(combo_id,stock,plan_version,plan_json,"
        "risk_profile,effective_at,source,prev_version,created_at) "
        "VALUES(:combo_id,:stock,:plan_version,:plan_json,:risk_profile,"
        ":effective_at,:source,:prev_version,:created_at)",
        row,
    )
    return {**row, "plan": plan}


def as_of_plan(
    con, combo_id: str, stock: str, at_time: str, risk_profile: str | None = None
) -> dict | None:
    """按「信号时刻」反查当时生效的预案版本。

    取生效时点 ≤ at_time 的版本中生效最晚者（同票多版本/多画像时，
    「信号触发时已知道」的版本）；risk_profile 给定时只在该画像内反查。
    无任何已生效版本 → None（信号时刻早于所有版本生效，属合法时序）。"""
    ensure_table(con)
    at = _parse_iso(at_time, "at_time")
    rows = con.execute(
        "SELECT * FROM plan_versions WHERE combo_id=? AND stock=?"  # nosec B608 - 值参数化/内部标识符（已抽查）
        + (" AND risk_profile=?" if risk_profile else ""),
        (combo_id, stock, risk_profile) if risk_profile else (combo_id, stock),
    ).fetchall()
    best, best_key = None, None
    for r in rows:
        eff = _parse_iso(r["effective_at"], "plan_versions.effective_at")
        if eff <= at and (best_key is None or (eff, r["id"]) > best_key):
            best, best_key = dict(r), (eff, r["id"])
    if best is None:
        return None
    best["plan"] = json.loads(best["plan_json"])
    return best


def assert_plan_effective(
    con,
    combo_id: str,
    stock: str,
    plan_version: str,
    at_time: str,
    log_path: Path | str | None = None,
    stage: str = "assert",
) -> dict:
    """断言某预案版本在 at_time（信号/快照时刻）已生效：effective_at ≤ at_time。

    版本不存在 → PlanLedgerError(plan_not_found)；
    生效时点晚于 at_time（用会后版本冒充信号时刻版本）→ 先留痕再抛
    PlanBackdateError（closed on failure，禁静默放过）。
    合法 → 返回该版本行。"""
    ensure_table(con)
    row = con.execute(
        "SELECT * FROM plan_versions WHERE combo_id=? AND stock=? AND plan_version=?",
        (combo_id, stock, plan_version),
    ).fetchone()
    if row is None:
        raise PlanLedgerError(
            "plan_not_found",
            f"plan_versions 无 ({combo_id}, {stock}, {plan_version})——快照引用的"
            "预案版本必须先入库（closed on failure）",
        )
    eff = _parse_iso(row["effective_at"], "plan_versions.effective_at")
    at = _parse_iso(at_time, "at_time")
    if eff > at:
        entry = append_backdate_trace(
            log_path or DEFAULT_BACKDATE_LOG,
            _backdate_entry(
                stage,
                combo_id=combo_id,
                stock=stock,
                plan_version=plan_version,
                at_time=at_time,
                effective_at=row["effective_at"],
                reason=f"禁回写：plan 生效时点 {row['effective_at']} 晚于信号/快照时刻 "
                f"{at_time}——不得用会后版本冒充信号时刻版本",
            ),
        )
        raise PlanBackdateError(
            "plan_backdate",
            f"禁回写违例：plan_version={plan_version} 生效时点 {row['effective_at']} "
            f"> 信号/快照时刻 {at_time}（已留痕 {entry['ts']}）",
        )
    d = dict(row)
    d["plan"] = json.loads(d["plan_json"])
    return d


def assert_snapshot_plan(
    con, snapshot, log_path: Path | str | None = None
) -> dict | None:
    """审计断言：给定决策快照（by_ref 产物或含 ts/context_json 的行），
    断言其引用（context_json["plan_version"]）的预案版本生效时点 ≤ 快照时刻。

    快照未关联 plan_version → None（零变化路径，无断言对象）；
    违例 → 先留痕再抛 PlanBackdateError。适用于本功能上线前存量快照与
    任何外部写入快照的追溯审计。"""
    if not isinstance(snapshot, dict):
        snapshot = dict(snapshot)
    ctx = snapshot.get("context_json")
    if isinstance(ctx, str):
        ctx = json.loads(ctx or "{}")
    ctx = ctx or {}
    stamp = ctx.get("plan_version")
    if not stamp:
        return None
    if not isinstance(stamp, dict):
        raise PlanLedgerError(
            "plan_stamp_invalid",
            f"快照 plan_version 印记须为 dict: {type(stamp).__name__}",
        )
    return assert_plan_effective(
        con,
        stamp.get("combo_id"),
        stamp.get("stock"),
        stamp.get("plan_version"),
        at_time=snapshot.get("ts"),
        log_path=log_path,
        stage="assert_snapshot",
    )


# ---------- meeting_records：会议账 ----------


def add_meeting_record(
    con,
    *,
    meeting_id: str,
    topic_type: str,
    topic_ref: str,
    topic_time: str,
    dissent: list,
    conclusion: str,
    effective_at: str,
    attendees: list,
    revised_plan_version: str | None = None,
    corrects_meeting_id: str | None = None,
    log_path: Path | str | None = None,
) -> dict:
    """追加一条会议记录（append-only：同 meeting_id 重复即拒，无 update 路径）。

    禁回写（定纲核心）：会后记录的生效时点必须**晚于**其引用的信号/
    快照时间（topic_time）——effective_at ≤ topic_time 即拒并留痕。
    结论=revise 时 revised_plan_version 必填，且该版本须已存在于
    plan_versions、其生效时点 ≥ 会议生效时点（修订产生的新版本不得
    早于会议生效——否则又是一处回写口子）。
    更正语义：原记录不可改（触发器拒绝 UPDATE），更正=追加新记录，
    corrects_meeting_id 指向被更正记录。"""
    ensure_table(con)
    if not isinstance(meeting_id, str) or not meeting_id.strip():
        raise PlanLedgerError(
            "field_invalid", f"meeting_id 须为非空字符串: {meeting_id!r}"
        )
    if topic_type not in TOPIC_TYPES:
        raise PlanLedgerError(
            "topic_type_invalid", f"topic_type 只认 {TOPIC_TYPES}: {topic_type!r}"
        )
    if not isinstance(topic_ref, str) or not topic_ref.strip():
        raise PlanLedgerError(
            "field_invalid", f"topic_ref 须为非空字符串: {topic_ref!r}"
        )
    topic_dt = _parse_iso(topic_time, "meeting_records.topic_time")
    # 会前分歧：定纲——只存共识=丢掉最有价值的反例与分歧，分歧记录必填
    if not isinstance(dissent, list) or not dissent:
        raise PlanLedgerError(
            "dissent_missing",
            "dissent 须为非空 list（会前分歧记录必填——定纲："
            "只存共识=丢掉最有价值的反例与分歧）",
        )
    for i, d in enumerate(dissent):
        if not isinstance(d, dict):
            raise PlanLedgerError("dissent_invalid", f"dissent[{i}] 须为 dict: {d!r}")
        for k in ("role", "stance", "reason"):
            if not isinstance(d.get(k), str) or not d[k].strip():
                raise PlanLedgerError(
                    "dissent_invalid",
                    f"dissent[{i}].{k} 须为非空字符串（立场+理由必录）",
                )
    if conclusion not in CONCLUSIONS:
        raise PlanLedgerError(
            "conclusion_invalid", f"conclusion 只认 {CONCLUSIONS}: {conclusion!r}"
        )
    if (
        not isinstance(attendees, list)
        or not attendees
        or not all(isinstance(a, str) and a.strip() for a in attendees)
    ):
        raise PlanLedgerError("attendees_invalid", "attendees 须为非空角色字符串 list")
    eff_dt = _parse_iso(effective_at, "meeting_records.effective_at")
    # 禁回写防线③：会后生效时点必须晚于议题引用的信号/快照时间
    if eff_dt <= topic_dt:
        entry = append_backdate_trace(
            log_path or DEFAULT_BACKDATE_LOG,
            _backdate_entry(
                "meeting",
                meeting_id=meeting_id,
                topic_ref=topic_ref,
                topic_time=topic_time,
                effective_at=effective_at,
                reason=f"禁回写：会议生效时点 {effective_at} 未晚于议题引用时刻 "
                f"{topic_time}（会后结论不得追溯为议题时刻已生效）",
            ),
        )
        raise PlanBackdateError(
            "meeting_backdate",
            f"禁回写违例：meeting {meeting_id} 生效时点 {effective_at} ≤ 议题引用"
            f"时刻 {topic_time}（已留痕 {entry['ts']}）",
        )
    if conclusion == "revise":
        if not revised_plan_version:
            raise PlanLedgerError(
                "revise_without_plan",
                "conclusion=revise 须给 revised_plan_version（修订后指向哪个版本）",
            )
        row = con.execute(
            "SELECT effective_at FROM plan_versions WHERE plan_version=?",
            (revised_plan_version,),
        ).fetchone()
        if row is None:
            raise PlanLedgerError(
                "plan_not_found",
                f"revised_plan_version {revised_plan_version!r} 不在 plan_versions"
                "（修订指向的版本须先入库）",
            )
        rev_eff = _parse_iso(row["effective_at"], "plan_versions.effective_at")
        if rev_eff < eff_dt:
            raise PlanLedgerError(
                "revise_plan_early",
                f"修订指向版本 {revised_plan_version} 生效时点 {row['effective_at']} "
                f"早于会议生效时点 {effective_at}——防回写：新版本不得早于会议生效",
            )
    if (
        corrects_meeting_id is not None
        and not con.execute(
            "SELECT 1 FROM meeting_records WHERE meeting_id=?", (corrects_meeting_id,)
        ).fetchone()
    ):
        raise PlanLedgerError(
            "corrects_not_found", f"corrects_meeting_id {corrects_meeting_id!r} 不存在"
        )
    if con.execute(
        "SELECT 1 FROM meeting_records WHERE meeting_id=?", (meeting_id,)
    ).fetchone():
        raise PlanLedgerError(
            "duplicate_meeting_id",
            f"meeting_id {meeting_id!r} 已存在——append-only 禁覆盖，"
            "更正请追加新记录并指 corrects_meeting_id",
        )
    row = {
        "meeting_id": meeting_id,
        "topic_type": topic_type,
        "topic_ref": topic_ref,
        "topic_time": topic_time,
        "dissent_json": json.dumps(dissent, ensure_ascii=False),
        "conclusion": conclusion,
        "revised_plan_version": revised_plan_version,
        "effective_at": effective_at,
        "attendees_json": json.dumps(attendees, ensure_ascii=False),
        "corrects_meeting_id": corrects_meeting_id,
        "created_at": _now(),
    }
    con.execute(
        "INSERT INTO meeting_records(meeting_id,topic_type,topic_ref,topic_time,"
        "dissent_json,conclusion,revised_plan_version,effective_at,attendees_json,"
        "corrects_meeting_id,created_at) VALUES(:meeting_id,:topic_type,:topic_ref,"
        ":topic_time,:dissent_json,:conclusion,:revised_plan_version,:effective_at,"
        ":attendees_json,:corrects_meeting_id,:created_at)",
        row,
    )
    return {**row, "dissent": dissent, "attendees": attendees}


def meeting_by_id(con, meeting_id: str) -> dict | None:
    """按 meeting_id 取会议记录（含 dissent/attendees 已解析），供议题自动化取料。"""
    ensure_table(con)
    row = con.execute(
        "SELECT * FROM meeting_records WHERE meeting_id=?", (meeting_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["dissent"] = json.loads(d["dissent_json"])
    d["attendees"] = json.loads(d["attendees_json"])
    return d
