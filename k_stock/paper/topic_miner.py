# -*- coding: utf-8 -*-
"""会议议题提炼（WO-QT-20261020-016，来源条目 3.1 / 缺口 D-4 的可开工部分）。

定纲（老田 2026-10-20）：**会议不是形式批准单，而是把分歧变成可复用策略资产的
机制**。故议题不拍脑袋定，而是从「最赚/最亏样本对」与「分支决策偏差」自动提炼
——样本对回答「这套决策凭什么赚/凭什么亏」，分支偏差回答「同一信号两条路径为何
分道扬镳」，两者都是会上真正值得吵的东西。

本单产出**议题草案**（append-only 议题账 jsonl），每条含：
  topic_id / source_type（来源类型）/ 样本引用 / 偏差类型 / 证据摘要（可回放
  引用）/ 建议复盘对象 / 状态（draft）。

三类来源（本单实现两类 + 预留一类）：
  - most_profitable / most_lossful：-004 决策快照样本对 + 后验收益。后验未
    回填 → **该样本不可用**（不参与排序），样本不足成对则不出议题（不造数）；
  - branch_divergence：-008 compare_reviews 的分支决策偏差（plan_conflict /
    conf_floor 等放弃原因码、no_plan_reference 等 deviation.type）；
  - riskguard_reserved：**本单只留接口不实现**（RiskGuard 校准 1.1 尚待老田
    a-d 拍板，未接入时显式标 not_implemented_pending_1_1——不假装有数据）。

**不自动发起会议**：发起权归人/司马懿，属后续工单。本模块对 -006 只读（纯
SELECT + mode=ro 连接），零写入路径——测试断言全链路不新增 meeting_records。

去重：同一议题（来源类型 + 标的 + 偏差类型指纹）在账内已存在 → 不重复提，
返回 existing（不写行、不重开讨论）。

两率口径（口径澄清落地，呼应 -014 divergence_formula 风格，算式写进输出字段）：
  - **分歧保全率** = 有会前分歧记录的会议数 / 会议总数。诚实提示：-006 写入时
    已强制 dissent 非空，故经正规写入口入账的会议该率恒为 1.0（结构性），该
    指标真正咬得住的是「-006 强制之前的历史行 / 外部写入行 / dissent_json 被
    写成空列表」——低于 1.0 即为保全破口，故仍照实计算并出 note；
  - **分歧转化率** = 结论为 revise/veto 的会议数 / 会议总数（分歧导致结论改动
    的比例）。分母 0 → 两率均记 null + note，**不填 0 充数**。

只读复用（不改他人工单领地）：plan_meeting_ledger（-006 会议账：会前分歧 /
会后结论 / 已讨论议题）、strategy_branch（-008 compare_reviews 输出形态）、
decision_snapshot（-004 快照表）、combo_review（-013 议题的组合侧语义参照）。
**-006 的 dissent 字段语义与 append-only 触发器一字未动**（本模块只读）。

未接线说明（汇报口径）：
  - **发起会议**属后续工单（发起权归人/司马懿）——本模块不写 meeting_records；
  - **RiskGuard 误杀专题提炼**待 1.1 校准拍板后实现（本单只留契约与入口）；
  - **后验收益自动回填**（快照 run_ref → 结算后验）属后续工单，当前仅显式/
    人工喂入一条路径，喂入即录来源与单位，提炼器不猜收益；
  - **重复分歧的复现次数未在账内累积**（去重后不再写行），跨批次累计属后续工单。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import decision_snapshot
import plan_meeting_ledger

TOPIC_SCHEMA_VERSION = 1

# 议题账（append-only jsonl；测试注入临时路径）
DEFAULT_TOPIC_LEDGER = Path(__file__).resolve().parent.parent / "data" / "meeting_topics.jsonl"

# 来源类型白名单（closed on failure）
SRC_MOST_PROFITABLE = "most_profitable"
SRC_MOST_LOSSFUL = "most_lossful"
SRC_BRANCH_DIVERGENCE = "branch_divergence"
SRC_RISKGUARD_RESERVED = "riskguard_reserved"
SOURCE_TYPES = (SRC_MOST_PROFITABLE, SRC_MOST_LOSSFUL, SRC_BRANCH_DIVERGENCE,
                SRC_RISKGUARD_RESERVED)

# 样本对议题的偏差类型（-008 的 deviation 语义不适用于样本对，用固定标签）
DEVIATION_POSTERIOR_EXTREME = "posterior_extreme"

# 议题状态：本模块只产出草案，发起权不归本模块
STATUS_DRAFT = "draft"

# RiskGuard 预留入口的显式状态（不假装有数据）
RISKGUARD_NOT_IMPLEMENTED = "not_implemented_pending_1_1"

# RiskGuard 校准账（1.1）契约：规则级误杀/放行记录 + 后验收益的必填键
RISKGUARD_CALIBRATION_LOG = (Path(__file__).resolve().parent.parent / "data" /
                             "risk_guard_calibration.jsonl")
RISKGUARD_RECORD_REQUIRED = ("ts", "rule_id", "rule_version", "decision",
                             "posterior_return")

# 后验收益未回填的样本状态（不造数：不可用、不参与排序、不出议题）
POSTERIOR_PENDING = "pending_backfill"


class TopicMinerError(Exception):
    """议题提炼契约违例（closed on failure）。携带 reason_code 供归因。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(f"[{reason_code}] {message}")
        self.reason_code = reason_code


# ---------- 通用工具 ----------

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _round(v: float) -> float:
    return round(float(v), 6)


def _entry_digest(entry: dict) -> str:
    """条目自校验指纹（不含 entry_sha256 自身；append-only 防改写锚点）。"""
    blob = json.dumps({k: v for k, v in entry.items() if k != "entry_sha256"},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ---------- 议题账（append-only jsonl，无 update/delete 路径） ----------

def _read_ledger(ledger_path: Path | str) -> list[dict]:
    """整本读入并逐行校验（行损坏/被改写 → 显式抛错，closed on failure）。"""
    p = Path(ledger_path)
    if not p.exists():
        return []
    entries = []
    with p.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError as err:
                raise TopicMinerError(
                    "ledger_corrupt",
                    f"议题账第 {lineno} 行不是合法 JSON（closed on failure）: {p}") from err
            if not isinstance(e, dict) or "entry_sha256" not in e:
                raise TopicMinerError(
                    "ledger_corrupt",
                    f"议题账第 {lineno} 行缺 entry_sha256（closed on failure）: {p}")
            if _entry_digest(e) != e["entry_sha256"]:
                raise TopicMinerError(
                    "ledger_corrupt",
                    f"议题账第 {lineno} 行内容与其自校验 sha256 不符——append-only 被改写"
                    f"（closed on failure）: {p}")
            entries.append(e)
    return entries


def _append_topic(ledger_path: Path | str, entry: dict) -> dict:
    """向议题账追加一行（append-only）。返回入账条目（含自校验 entry_sha256）。"""
    p = Path(ledger_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = dict(entry)
    row["entry_sha256"] = _entry_digest(row)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def read_topics(ledger_path: Path | str | None = None) -> list[dict]:
    """读回议题账全部议题（入账序）。"""
    return _read_ledger(Path(ledger_path) if ledger_path is not None
                        else DEFAULT_TOPIC_LEDGER)


def topic_by_id(topic_id: str, ledger_path: Path | str | None = None) -> dict | None:
    """按 topic_id 取议题（去重/回放用）。"""
    for e in read_topics(ledger_path):
        if e.get("topic_id") == topic_id:
            return e
    return None


# ---------- 去重指纹（来源类型 + 标的 + 偏差类型） ----------

def topic_fingerprint(source_type: str, combo_id, stock, branch_id,
                      deviation_type: str) -> str:
    """议题去重指纹：来源类型 + 标的（组合/股票/分支）+ 偏差类型。

    标的**不含 signal_id**：同一分支在同一标的上重复出现同一偏差 = 同一个议题
    （重复分歧正是更该开会的原因），不因换信号而变成新议题。"""
    blob = json.dumps([str(source_type), str(combo_id or ""), str(stock or ""),
                       str(branch_id or ""), str(deviation_type)],
                      ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _topic_id(source_type: str, fingerprint: str) -> str:
    return f"tp-{source_type}-{fingerprint[:16]}"


# ---------- 输入一：-004 决策快照样本对 + 后验收益 ----------

def _validate_posterior(posterior) -> dict:
    """校验后验收益喂入，返回 {snapshot_id: {value, unit, source}}。

    喂入契约（posterior 参数）：

        {"snapshots": {"<snapshot_id>": {"unit": "pct",   # 必填：不声明单位即不造数
                                         "value": 0.12,   # 必填：数值
                                         "source": "settle:run-xxx"}}}

    未出现在此映射里的快照 = 后验未回填 → 该样本不可用（标 pending_backfill，
    不参与最赚/最亏排序）。空映射 = 全部待回填（合法「无数据」，非契约违例）。
    """
    if posterior is None:
        return {}
    if not isinstance(posterior, dict):
        raise TopicMinerError("posterior_invalid",
                              f"posterior 须为 dict 或 None: {type(posterior).__name__}")
    snaps = posterior.get("snapshots")
    if snaps is None:
        return {}
    if not isinstance(snaps, dict):
        raise TopicMinerError("posterior_invalid",
                              f"posterior.snapshots 须为 dict: {type(snaps).__name__}")
    out = {}
    for sid, item in snaps.items():
        if not _nonempty_str(sid):
            raise TopicMinerError("posterior_invalid",
                                  f"posterior.snapshots 键须为非空 snapshot_id: {sid!r}")
        if not isinstance(item, dict):
            raise TopicMinerError("posterior_invalid",
                                  f"posterior.snapshots[{sid!r}] 须为 dict: {type(item).__name__}")
        unit, value = item.get("unit"), item.get("value")
        if not _nonempty_str(unit):
            raise TopicMinerError(
                "posterior_invalid",
                f"posterior.snapshots[{sid!r}].unit 必填（非空字符串）——单位不声明即不造数")
        if not _is_number(value):
            raise TopicMinerError(
                "posterior_invalid",
                f"posterior.snapshots[{sid!r}].value 须为数值（不得为 None/字符串/bool）: "
                f"{value!r}")
        out[sid] = {"value": _round(value), "unit": unit.strip(), "source": item.get("source")}
    return out


def _snapshot_stock(ctx: dict, orders) -> str | None:
    """从快照印记取标的股票：signal.stock → plan_version.stock → 首条 orders.stock。

    取不到即 None（不臆造标的；指纹以空标的参与，仍可去重）。"""
    for key in ("signal", "plan_version"):
        v = ctx.get(key)
        if isinstance(v, dict) and _nonempty_str(v.get("stock")):
            return v["stock"]
    if isinstance(orders, list):
        for o in orders:
            if isinstance(o, dict) and _nonempty_str(o.get("stock")):
                return o["stock"]
    return None


def _read_snapshots(snapshot_db) -> list[dict]:
    """只读 -004 快照表（mode=ro + 纯 SELECT，**不调 ensure_table**——那是写路径）。"""
    if snapshot_db is None:
        return []
    p = Path(snapshot_db)
    if not p.exists():
        raise TopicMinerError("snapshot_db_missing",
                              f"决策快照库不存在（closed on failure）: {p}")
    uri = f"file:{p.resolve().as_posix()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        raise TopicMinerError("snapshot_db_unreadable",
                              f"决策快照库不可读（closed on failure）: {p} ({e})") from e
    try:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                          ("decision_snapshots",)).fetchone()
        if row is None:
            raise TopicMinerError("snapshot_table_missing",
                                  f"决策快照库无 decision_snapshots 表（closed on failure）: {p}")
        rows = con.execute(
            "SELECT snapshot_id, ts, schema_version, combo_id, account_id, strategy, "
            "run_ref, run_path, orders_json, context_json FROM decision_snapshots "
            "ORDER BY snapshot_id").fetchall()
    except sqlite3.Error as e:
        raise TopicMinerError("snapshot_query_failed",
                              f"-004 快照表查询失败（closed on failure）: {p} ({e})") from e
    finally:
        con.close()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("orders_json", "context_json"):
            try:
                d[k] = json.loads(d[k] or ("[]" if k == "orders_json" else "{}"))
            except ValueError as err:
                raise TopicMinerError(
                    "snapshot_corrupt",
                    f"快照 {d.get('snapshot_id')} 的 {k} 非法（closed on failure）") from err
        out.append(d)
    return out


def _sample_topics(snapshots: list[dict], posterior: dict) -> tuple[list[dict], dict]:
    """样本对议题：最赚 / 最亏各一条（后验未回填的样本不可用，不造数）。

    成对门槛：**至少 2 个可用样本**才构成「样本对」——单样本自称既最赚又最亏
    是退化输出，宁可不出议题（返回 note 说明），不造数。
    """
    usable, pending = [], []
    for s in snapshots:
        sid = s.get("snapshot_id")
        post = posterior.get(sid)
        ctx = s.get("context_json") if isinstance(s.get("context_json"), dict) else {}
        stock = _snapshot_stock(ctx, s.get("orders_json"))
        base = {"snapshot_id": sid, "ts": s.get("ts"), "run_ref": s.get("run_ref"),
                "run_path": s.get("run_path"), "combo_id": s.get("combo_id"),
                "account_id": s.get("account_id"), "strategy": s.get("strategy"),
                "stock": stock}
        if post is None:
            # 后验未回填 → 样本不可用：显式留痕（可回放引用齐备），不参与排序
            pending.append({**base, "posterior_status": POSTERIOR_PENDING,
                            "note": "后验收益未回填 → 样本不可用（不参与最赚/最亏排序，不造数）"})
            continue
        usable.append({**base, "posterior": post})
    if len(usable) < 2:
        return [], {"samples_total": len(snapshots), "samples_usable": len(usable),
                    "samples_pending_backfill": len(pending), "pending_samples": pending,
                    "note": (f"可用样本 {len(usable)} 个 < 2——不构成样本对，不出议题"
                             f"（不造数）；待回填 {len(pending)} 个")}

    ranked = sorted(usable, key=lambda r: (r["posterior"]["value"], r["snapshot_id"]))
    best, worst = ranked[-1], ranked[0]
    topics = []
    for source_type, sample, reading in (
            (SRC_MOST_PROFITABLE, best, "最赚样本：决策依据链值得固化为可复用策略资产"),
            (SRC_MOST_LOSSFUL, worst, "最亏样本：反例是最有价值的训练资产，须复盘归因")):
        fp = topic_fingerprint(source_type, sample["combo_id"], sample["stock"],
                               None, DEVIATION_POSTERIOR_EXTREME)
        topics.append({
            "topic_schema_version": TOPIC_SCHEMA_VERSION,
            "topic_id": _topic_id(source_type, fp),
            "source_type": source_type,
            "fingerprint": fp,
            "subject": {"combo_id": sample["combo_id"], "stock": sample["stock"],
                        "branch_id": None, "signal_id": None,
                        "snapshot_id": sample["snapshot_id"]},
            "sample_ref": {"snapshot_id": sample["snapshot_id"],
                           "run_ref": sample["run_ref"], "stock": sample["stock"],
                           "combo_id": sample["combo_id"], "branch_id": None},
            "deviation_type": DEVIATION_POSTERIOR_EXTREME,
            "deviation_type_source": "topic_miner.fixed_label",
            "evidence": {
                "snapshot_id": sample["snapshot_id"], "run_ref": sample["run_ref"],
                "run_path": sample["run_path"], "ts": sample["ts"],
                "combo_id": sample["combo_id"], "account_id": sample["account_id"],
                "strategy": sample["strategy"], "stock": sample["stock"],
                "snapshot_schema_version": decision_snapshot.SNAPSHOT_SCHEMA_VERSION,
                "posterior": sample["posterior"],
                "orders": sample.get("orders"),
                "plan_version": (sample.get("context_json") or {}).get("plan_version"),
                "signal": (sample.get("context_json") or {}).get("signal"),
                "reading": reading,
            },
            "suggested_review_target": {
                "role": "combo_owner_research",
                "combo_id": sample["combo_id"], "branch_id": None,
                "reason": reading},
            "status": STATUS_DRAFT,
            "meeting_initiated": False,
            "mined_at": _now(),
        })
    return topics, {"samples_total": len(snapshots), "samples_usable": len(usable),
                    "samples_pending_backfill": len(pending), "pending_samples": pending,
                    "note": None}


# ---------- 输入二：-008 分支复盘（分歧提炼） ----------

def _normalize_reviews(reviews) -> list[dict]:
    """归一化 -008 compare_reviews 输出（单条或多条 → 列表）。"""
    if reviews is None:
        return []
    items = reviews if isinstance(reviews, list) else [reviews]
    out = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise TopicMinerError("reviews_invalid",
                                  f"reviews[{i}] 须为 dict（-008 compare_reviews 输出）: "
                                  f"{type(item).__name__}")
        if not _nonempty_str(item.get("signal_id")):
            raise TopicMinerError("reviews_invalid",
                                  f"reviews[{i}] 缺 signal_id（-008 输出必带）")
        rv = item.get("reviews")
        if not isinstance(rv, dict):
            raise TopicMinerError("reviews_invalid",
                                  f"reviews[{i}].reviews 须为 dict（-008 输出必带）")
        for bid, one in rv.items():
            if not isinstance(one, dict):
                raise TopicMinerError("reviews_invalid",
                                      f"reviews[{i}].reviews[{bid!r}] 须为 dict")
        out.append({"signal_id": item["signal_id"], "combo_id": item.get("combo_id"),
                    "reviews": dict(rv)})
    return out


def _divergence_type(one: dict) -> tuple[str | None, str | None]:
    """判定分支偏差类型 + 其来源标签。

    取值口径（可审计）：
      - 分支**放弃**（decided_action=abstain）→ 偏差类型取 **reason_code**
        （plan_conflict / conf_floor 等放弃原因码才是真正可讨论的分歧语义；
        -008 激进分支的 deviation.type 恒为 no_plan_reference，会盖掉
        conf_floor 这一可分辨信息）；
      - 分支**执行** → 取 deviation.type（no_plan_reference 等观察项）；
      - 两者皆无 → 无偏差类型（不出议题，不臆造）。

    返回 (deviation_type, deviation_type_source)；无偏差返回 (None, None)。
    """
    abstained = one.get("decided_action") == "abstain"
    reason = one.get("reason_code")
    dev = one.get("deviation")
    dev_type = dev.get("type") if isinstance(dev, dict) else None
    if abstained and _nonempty_str(reason):
        return reason, "reason_code"
    if _nonempty_str(dev_type):
        return dev_type, "deviation.type"
    if _nonempty_str(reason):
        return reason, "reason_code"
    return None, None


def _branch_topics(review_items: list[dict]) -> list[dict]:
    """分支分歧议题：逐 (信号, 分支) 的偏差类型 → 议题草案。"""
    topics = []
    for item in review_items:
        for bid, one in item["reviews"].items():
            dtype, dsource = _divergence_type(one)
            if not dtype:
                continue
            stock = one.get("stock") if _nonempty_str(one.get("stock")) else None
            fp = topic_fingerprint(SRC_BRANCH_DIVERGENCE, item.get("combo_id"),
                                   stock, bid, dtype)
            topics.append({
                "topic_schema_version": TOPIC_SCHEMA_VERSION,
                "topic_id": _topic_id(SRC_BRANCH_DIVERGENCE, fp),
                "source_type": SRC_BRANCH_DIVERGENCE,
                "fingerprint": fp,
                "subject": {"combo_id": item.get("combo_id"), "stock": stock,
                            "branch_id": bid, "signal_id": item["signal_id"],
                            "snapshot_id": None},
                "sample_ref": {"snapshot_id": None, "run_ref": None, "stock": stock,
                               "combo_id": item.get("combo_id"), "branch_id": bid},
                "deviation_type": dtype,
                "deviation_type_source": dsource,
                "evidence": {
                    "signal_id": item["signal_id"], "combo_id": item.get("combo_id"),
                    "branch_id": bid, "decided_action": one.get("decided_action"),
                    "action": one.get("action"), "reason_code": one.get("reason_code"),
                    "reason": one.get("reason"), "plan_version": one.get("plan_version"),
                    "deviation": one.get("deviation"), "basis_chain": one.get("basis_chain"),
                    "reading": "同一信号下分支决策分歧：分歧即资产，须复盘定性",
                },
                "suggested_review_target": {
                    "role": "branch_owner",
                    "combo_id": item.get("combo_id"), "branch_id": bid,
                    "reason": "分支决策与预案/门槛的分歧需定性（-008 平行比较口径）"},
                "status": STATUS_DRAFT,
                "meeting_initiated": False,
                "mined_at": _now(),
            })
    return topics


# ---------- 输入三：-006 会议账（已讨论议题 + 两率） ----------

def _discussed_refs(plan_db) -> tuple[set, dict]:
    """已讨论议题引用集（-006 meeting_records，只读）。

    返回 (refs, info)：refs 收「这个信号/这个标的已经上过会」的判重锚点——
      - topic_type=signal → topic_ref 即 signal_id，原样入集；
      - topic_type=stock_date → 引用文本含日期后缀，按分隔符取首段作标的代码
        入集（-006 未固化该格式，取首段是保守的**候选**匹配，宁可多判重、
        也不把已上过会的议题重新提一遍）。
    """
    if plan_db is None:
        return set(), {"available": False, "reason": "plan_db_not_provided"}
    p = Path(plan_db)
    if not p.exists():
        return set(), {"available": False, "reason": "plan_db_missing"}
    uri = f"file:{p.resolve().as_posix()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        return set(), {"available": False, "reason": f"plan_db_unreadable: {e}"}
    try:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                          ("meeting_records",)).fetchone()
        if row is None:
            return set(), {"available": False, "reason": "meeting_records_table_missing"}
        rows = con.execute("SELECT meeting_id, topic_type, topic_ref, conclusion, "
                           "dissent_json FROM meeting_records ORDER BY meeting_id").fetchall()
    except sqlite3.Error as e:
        raise TopicMinerError("plan_ledger_query_failed",
                              f"-006 会议账查询失败（closed on failure）: {p} ({e})") from e
    finally:
        con.close()

    refs, dissent_items = set(), 0
    with_dissent = 0
    for row in rows:
        ref = row["topic_ref"]
        if _nonempty_str(ref):
            refs.add(ref)
            # stock_date 口径：引用文本首个字段即标的（历史遗留格式，容忍）
            refs.add(str(ref).split("@")[0].split("|")[0])
        try:
            dissent = json.loads(row["dissent_json"])
        except (TypeError, ValueError) as e:
            raise TopicMinerError(
                "dissent_corrupt",
                f"会议 {row['meeting_id']} 的 dissent_json 非法（closed on failure）: "
                f"{row['dissent_json']!r}") from e
        if not isinstance(dissent, list):
            raise TopicMinerError("dissent_corrupt",
                                  f"会议 {row['meeting_id']} 的 dissent_json 非 list")
        if dissent:
            with_dissent += 1
            dissent_items += len(dissent)
    conclusions: dict[str, int] = {}
    for row in rows:
        c = str(row["conclusion"])
        conclusions[c] = conclusions.get(c, 0) + 1
    return refs, {"available": True, "reason": None, "meetings_total": len(rows),
                  "meetings_with_dissent": with_dissent, "dissent_items_total": dissent_items,
                  "conclusions": dict(sorted(conclusions.items()))}


def dissent_metrics(plan_db=None, info: dict | None = None) -> dict:
    """两率并列（口径澄清落地），算式写进输出字段。

    - 分歧保全率 preservation_rate = 有会前分歧记录的会议数 / 会议总数；
    - 分歧转化率 conversion_rate   = 结论 revise/veto 的会议数 / 会议总数；
    - 分母 0 → 两率均 null + note（**不填 0 充数**）。

    诚实提示：-006 写入时已强制 dissent 非空，经正规写入口入账的会议保全率
    结构性恒为 1.0；该指标真正咬住的是「-006 强制之前的历史行 / 外部写入行 /
    dissent_json 被写成空列表」，低于 1.0 即为分歧保全破口。"""
    if info is None:
        _, info = _discussed_refs(plan_db)
    if not info.get("available"):
        return {"status": "not_wired", "available": False, "reason": info.get("reason"),
                "meetings_total": None, "meetings_with_dissent": None,
                "preservation_rate": None, "conversion_rate": None,
                "preservation_formula": "meetings_with_dissent / meetings_total",
                "conversion_formula": "conclusions(revise+veto) / meetings_total",
                "note": "-006 会议账未接入 → 两率不可计算（不以 0 充数）"}
    total = int(info["meetings_total"])
    with_dissent = int(info["meetings_with_dissent"])
    changed = sum(v for k, v in info["conclusions"].items()
                  if k in ("revise", "veto"))
    zero_note = ("会议场次为 0 → 两率均不可计算（不以 0 充数）" if total == 0 else None)
    return {
        "status": "available" if total else "empty",
        "available": True,
        "reason": None,
        "meetings_total": total,
        "meetings_with_dissent": with_dissent,
        "dissent_items_total": int(info["dissent_items_total"]),
        "conclusions": dict(info["conclusions"]),
        "conclusion_changed_meetings": changed,
        "preservation_rate": round(with_dissent / total, 6) if total else None,
        "conversion_rate": round(changed / total, 6) if total else None,
        "preservation_formula": "meetings_with_dissent / meetings_total",
        "conversion_formula": "conclusions(revise+veto) / meetings_total",
        "note": zero_note,
        "preservation_tautology_note": (
            "-006 写入时强制 dissent 非空，故正规写入口入账的保全率结构性为 1.0；"
            "低于 1.0 说明存在 -006 强制之前的历史行/外部写入行/空 dissent——"
            "该指标即用于咬住这处保全破口"),
        "conclusion_whitelist": list(plan_meeting_ledger.CONCLUSIONS),
    }


# ---------- 输入四：RiskGuard 误杀专题（预留，本单不实现） ----------

def riskguard_reserved_topics(records=None,
                              calibration_log: Path | str | None = None) -> dict:
    """RiskGuard 误杀议题入口：**本单不实现提炼逻辑**，显式标未实现。

    RiskGuard 校准（WO 工单 1.1）尚待老田 a-d 拍板，误杀/放行数据源未接入——
    此处**不假装有数据、不返回任何议题**。函数只负责：声明数据契约 + 回报
    not_implemented_pending_1_1。

    契约（1.1 拍板后按此喂入 risk_guard_calibration.jsonl）：
      每行一条规则级记录，必填键 RISKGUARD_RECORD_REQUIRED：
        ts            判定时刻（ISO）
        rule_id       规则标识
        rule_version  规则版本
        decision      blocked / passed（误杀=blocked 且事后证明该放行）
        posterior_return  该次判定的后验收益（用于区分「真误杀」与「正确拦截」）
      实现时的提炼口径（1.1 拍板后确认）：误杀 = decision=blocked 且
      posterior_return > 0，按 (rule_id, rule_version) 归集为议题。
    """
    seen = None
    if records is not None:
        if not isinstance(records, (list, tuple)):
            raise TopicMinerError("riskguard_records_invalid",
                                  f"records 须为 list/tuple 或 None: {type(records).__name__}")
        seen = len(records)
    return {
        "status": RISKGUARD_NOT_IMPLEMENTED,
        "implemented": False,
        "source_type": SRC_RISKGUARD_RESERVED,
        "topic_schema_version": TOPIC_SCHEMA_VERSION,
        "topics": [],
        "input_contract": {
            "calibration_log": str(Path(calibration_log) if calibration_log is not None
                                    else RISKGUARD_CALIBRATION_LOG),
            "record_required_keys": list(RISKGUARD_RECORD_REQUIRED),
            "false_kill_definition": "decision=blocked 且 posterior_return > 0",
            "group_by": ["rule_id", "rule_version"],
        },
        "input_seen": records is not None,
        "input_count": seen,
        "note": ("RiskGuard 校准（1.1）待老田 a-d 拍板，误杀数据源未接入——"
                 "本单只留接口与契约，未实现提炼逻辑，不产出任何议题"),
    }


# ---------- 主入口：mine_topics（提炼议题草案） ----------

def mine_topics(*, snapshot_db=None, posterior=None, reviews=None,
                plan_db=None, ledger_path=None, persist: bool = True) -> dict:
    """从三类输入提炼会议议题草案，写入 append-only 议题账。

    参数：
      snapshot_db  -004 决策快照库路径（None=无快照输入）
      posterior    后验收益（显式/人工喂入；见 _validate_posterior 契约）
      reviews      -008 compare_reviews 输出（单条或列表；None=无分支输入）
      plan_db      -006 计划与会议版本账路径（None=已讨论议题与两率不可得）
      ledger_path  议题账路径（None=默认 data/meeting_topics.jsonl）
      persist      False=只提炼不入账（干跑，供 -009 汇报器预览）

    返回：{topics（本轮新提）, existing（去重命中）, suppressed（已讨论/待回填）,
    dissent（两率）, riskguard（预留入口状态）, meeting_initiated（恒 False）}

    **不自动发起会议**：发起权归人/司马懿，属后续工单；本模块对 -006 零写入。
    不造数：后验未回填的样本不参与排序、不出议题（显式标 pending_backfill）。
    closed on failure：后验喂入非法 / 复盘结构非法 / 快照库损坏 / 会议账损坏
    → 显式抛 TopicMinerError（带 reason_code），不静默跳过。
    """
    post_map = _validate_posterior(posterior)
    review_items = _normalize_reviews(reviews)
    snapshots = _read_snapshots(snapshot_db)

    sample_topics, sample_info = _sample_topics(snapshots, post_map)
    branch_topics = _branch_topics(review_items)
    candidates = sample_topics + branch_topics

    ledger = Path(ledger_path) if ledger_path is not None else DEFAULT_TOPIC_LEDGER
    existing_entries = _read_ledger(ledger)
    by_fp = {}
    for e in existing_entries:
        by_fp.setdefault(e.get("fingerprint"), e)

    discussed_refs, disc_info = _discussed_refs(plan_db)

    def _already_discussed(t: dict) -> bool:
        subj = t.get("subject") or {}
        return any(str(v) in discussed_refs for v in
                   (subj.get("signal_id"), subj.get("stock")) if _nonempty_str(v))

    topics, existing, suppressed_discussed = [], [], []
    seen_this_round: dict[str, dict] = {}
    for t in candidates:
        fp = t["fingerprint"]
        if fp in seen_this_round:
            # 本轮内重复 → 只提一次。**先于账内查重判**：本轮自己刚写进去的
            # 行不是「既有议题」，标错会让汇报口径误读为历史上已开过题。
            existing.append({**seen_this_round[fp], "dedup_hit": True,
                             "dedup_reason": "duplicate_candidate_in_this_round"})
            continue
        if fp in by_fp:                      # 账内已存在 → 不重复提
            hit = dict(by_fp[fp])
            hit["dedup_hit"] = True
            hit["dedup_reason"] = "same_fingerprint_in_ledger"
            existing.append(hit)
            continue
        if _already_discussed(t):            # -006 已讨论议题 → 不重复提
            suppressed_discussed.append({
                "topic_id": t["topic_id"], "fingerprint": fp,
                "source_type": t["source_type"], "deviation_type": t["deviation_type"],
                "subject": t["subject"],
                "suppress_reason": "already_discussed_in_meeting_ledger"})
            continue
        seen_this_round[fp] = t
        row = _append_topic(ledger, t) if persist else {**t, "persisted": False}
        by_fp[fp] = row
        topics.append(row)

    return {
        "topic_schema_version": TOPIC_SCHEMA_VERSION,
        "generated_at": _now(),
        "ledger_path": str(ledger),
        "persisted": bool(persist),
        "counts": {"candidates": len(candidates), "topics_new": len(topics),
                   "existing": len(existing), "suppressed_already_discussed":
                       len(suppressed_discussed)},
        "by_source_type": {
            SRC_MOST_PROFITABLE: sum(1 for t in topics if t["source_type"] == SRC_MOST_PROFITABLE),
            SRC_MOST_LOSSFUL: sum(1 for t in topics if t["source_type"] == SRC_MOST_LOSSFUL),
            SRC_BRANCH_DIVERGENCE: sum(1 for t in topics if t["source_type"] == SRC_BRANCH_DIVERGENCE),
            SRC_RISKGUARD_RESERVED: 0,
        },
        "topics": topics,
        "existing": existing,
        "suppressed": {"already_discussed": suppressed_discussed,
                       "posterior_pending": sample_info["pending_samples"],
                       "sample_info": {k: v for k, v in sample_info.items()
                                       if k != "pending_samples"}},
        "dissent": dissent_metrics(plan_db, disc_info),
        "discussed": {"available": disc_info.get("available"),
                      "reason": disc_info.get("reason"),
                      "refs_count": len(discussed_refs)},
        "riskguard": riskguard_reserved_topics(),
        "meeting_initiated": False,
        "notes": [
            "议题来源：最赚/最亏样本对（-004 快照 + 后验收益）、分支决策偏差（-008 平行比较）",
            "RiskGuard 误杀专题：not_implemented_pending_1_1（1.1 校准待老田 a-d 拍板）",
            "本模块不自动发起会议（发起权归人/司马懿，属后续工单）",
            "后验未回填的样本不出议题（pending_backfill），不造数",
        ],
    }


# ---------- Markdown 渲染（同一份数据，两种呈现） ----------

def _fmt(v):
    return "—" if v is None else str(v)


def render_markdown(mined: dict) -> str:
    """议题草案 → Markdown（可直接贴会议议程）。"""
    d = mined["dissent"]
    lines = [f"# 会议议题草案（{mined['counts']['topics_new']} 条待议）", "",
             f"- 提炼时刻：{mined['generated_at']}（schema v{mined['topic_schema_version']}）",
             f"- 议题账：{mined['ledger_path']}"
             f"（{'已入账' if mined['persisted'] else '干跑未入账'}）",
             f"- 去重命中：{mined['counts']['existing']}｜已讨论故不提："
             f"{mined['counts']['suppressed_already_discussed']}",
             "", "## 一、议题清单", ""]
    if not mined["topics"]:
        lines.append("（本轮无可用样本/分支偏差——见下方「未接线与不造数」）")
    else:
        lines += ["| topic_id | 来源 | 标的 | 偏差类型 | 建议复盘对象 | 状态 |",
                  "|---|---|---|---|---|---|"]
        for t in mined["topics"]:
            s = t["subject"]
            target = (t["suggested_review_target"]["combo_id"] or "—")
            if t["suggested_review_target"].get("branch_id"):
                target = f"{target}@{t['suggested_review_target']['branch_id']}"
            lines.append(
                f"| {t['topic_id']} | {t['source_type']} | "
                f"{s.get('stock') or s.get('combo_id') or '—'} | {t['deviation_type']} | "
                f"{target} | {t['status']} |")
    pend = mined["suppressed"]["posterior_pending"]
    lines += ["", "## 二、未接线与不造数", ""]
    lines.append(f"- 后验待回填样本：{len(pend)} 个"
                 f"（不出议题、不参与最赚/最亏排序）")
    lines.append(f"- RiskGuard 误杀专题：{mined['riskguard']['status']}"
                 f"——{mined['riskguard']['note']}")
    lines.append("- 会议发起权归人/司马懿，本模块不写 meeting_records")
    lines += ["", "## 三、会议账两率（-006）", "",
              f"- 分歧保全率：{_fmt(d.get('preservation_rate'))}"
              f"（口径 {d.get('preservation_formula')}）",
              f"- 分歧转化率：{_fmt(d.get('conversion_rate'))}"
              f"（口径 {d.get('conversion_formula')}）"]
    if d.get("note"):
        lines.append(f"- 说明：{d['note']}")
    return "\n".join(lines)