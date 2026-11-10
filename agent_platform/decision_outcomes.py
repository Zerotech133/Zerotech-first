# -*- coding: utf-8 -*-
"""D1 决策 outcome 台账（只读 sidecar，2026-10-29 · 鲁肃复盘整改 #2）。

读影子账本 ``agent_platform/data/shadow/shadow_ledger.jsonl``，按决策日后的
首个交易日收盘价计算次日收益/方向命中，写 sidecar
``agent_platform/data/shadow/decision_outcomes.jsonl``（**不改原账本**）。

字段：ts, run_id, decision_id, date, symbol, recommendation, confidence,
      price_at_decision, next_trade_date, next_close, next_day_return, hit,
      horizon_days, model, fallback（legacy 行可能为 null；legacy 行无
      decision_id 不报错，按缺失处理）。

PIT / raw refs（K-ORG-20261101-D1RAWPIT，2026-11-01 schema 向后兼容增量）：
      **`as_of` 是采集时刻，不是数据时点**。新增两个分账字段（旧行缺省=null，
      读取方按缺失处理，不推断）：
      - `pit_as_of`：决策输入的数据时点——优先取 shadow_ledger 行 evidence_refs
        中 raw ref 的 `pit_as_of`（最新 K 线交易日），无 raw ref 时回退到账本
        `date`（数据到该决策日；`pit_source=decision_date`，如实标注非 raw）；
      - `raw_refs_status` / `raw_refs_in_bundle`：从账本行
        `evidence_raw_refs_summary`（缺失时顶层同名字段）拷贝，值为
        none/partial/full/invalid 与纳入 bundle 的 raw ref 数。
      `pit_source` 记录来源（raw_ref:<kind> / decision_date），不伪造。历史行只在
      **账本行确有证据接线字段**时才补上述字段（require_evidence），避免为
      无证据旧行凭空补时点导致无意义重写。

decision_id（K-ORG-20261031-D1WRITER）：与 ``engine/decision_policy.py::
decision_id`` 同一公式 ``D1-<yyyymmdd>-sha256("symbol|date|run_id")[:10]``；
延迟 import 单一真源，引擎不可用时回退同公式本地实现。重复运行按 run_id
幂等去重，不重复追加。

IFS F5c（2026-11-04 WO-IFS-OUTCOME-WIRE-20261104）：outcome 行补 **outcome_id +
六 ID 链**（decision/handoff/watch_plan/confirmation/trade/outcome）——
生成方复用 ``agent_platform.training.outcome_chain.new_outcome_id``（IFS-04 单一
生成方，``out-<yyyymmdd>-<8HEX>``），不在本模块另造格式。六 ID 键**必在**且缺失
显式 null（不拿 run_id/report 号冒充上游）；legacy 行（无 outcome_id）回填时
只补空值、不覆盖原字段、不伪造行情；同 run_id 重复回填确定性同 ID（幂等）。
补齐的行与既有 decision_id/model/PIT 回填分离计数（``ids_repaired``/``id_fields``），
不影响旧 ``repaired`` 语义。

``--since YYYY-MM-DD``（2026-10-31 WO-D1-OUTCOME-DAILY）：只回填该日期（含）
之后缺失的 outcome，用于日常增量；不带则补全历史缺失。输出新增行数 ``new``
与按日期分布 ``dates``。历史 outcome 行缺 ``decision_id``/``model`` 时按下表
从 shadow_ledger 同 run_id 补齐（decision_id 优先取账本行同名字段，否则按同
公式复算；model 取账本行值）；账本行缺失/字段不全 → 落 null，**不伪造**。
写盘改为「临时文件 + os.replace」原子重写，只在有新增或补字段时落盘。

用法::

    python -X utf8 agent_platform/decision_outcomes.py --compute
    python -X utf8 agent_platform/decision_outcomes.py --report --week 2026-W44
    python -X utf8 agent_platform/decision_outcomes.py --json
    python -X utf8 agent_platform/decision_outcomes.py --since 2026-10-30
    python -X utf8 agent_platform/decision_outcomes.py --since 2026-10-30 --no-write
"""
from __future__ import annotations

import logging
import argparse
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LEDGER = os.path.join(HERE, "data", "shadow", "shadow_ledger.jsonl")
DEFAULT_OUT = os.path.join(HERE, "data", "shadow", "decision_outcomes.jsonl")
DIRECTION = {"BUY": 1, "STRONG_BUY": 1, "SELL": -1, "STRONG_SELL": -1}
# PIT/raw 增量字段（旧 outcome 行缺省=null，schema 向后兼容）
PIT_FIELDS = ("pit_as_of", "pit_source", "raw_refs_status",
              "raw_refs_in_bundle")
_PIT_TEXT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?$")

_POLICY_DECISION_ID = None

#: IFS-04 六 ID 键（decision → handoff → watch_plan → confirmation → trade → outcome）。
SIX_ID_KEYS = ("decision_id", "handoff_id", "watch_plan_id",
               "confirmation_id", "trade_id", "outcome_id")
_UPSTREAM_ID_KEYS = SIX_ID_KEYS[:-1]

#: IFS F5c：outcome_id 生成方复用 training.outcome_chain（单一生成方）。
#: 直接脚本运行（python agent_platform/decision_outcomes.py）时用 training.*
#: 兼容导入；两者都不可用才回退同公式本地实现（不改变 ID 格式/确定性）。
try:  # 包导入（repo 根在 sys.path）
    from agent_platform.training import outcome_chain as _outcome_chain  # noqa: E402
except ImportError:  # 测试/脚本上下文：agent_platform 目录在 sys.path
    try:
        from training import outcome_chain as _outcome_chain  # noqa: E402
    except ImportError:  # pragma: no cover - 极端裁剪环境兜底（同公式）
        _outcome_chain = None

if _outcome_chain is not None:
    SIX_ID_KEYS = tuple(getattr(_outcome_chain, "SIX_ID_KEYS", SIX_ID_KEYS))
    _UPSTREAM_ID_KEYS = SIX_ID_KEYS[:-1]


def _id_or_none(value):
    """ID 归一：空/空白 → None（缺失显式 null，不造值）。"""
    text = str(value or "").strip()
    return text or None


def _record_ids(row):
    """取六 ID（优先复用 outcome_chain.record_ids 单一实现；不可用时本地回退）。"""
    if _outcome_chain is not None:
        try:
            return dict(_outcome_chain.record_ids(row))
        except Exception:  # noqa: BLE001 - 回退实现同语义，不改变结果
            logging.getLogger(__name__).exception("swallowed exception at line 110")
            pass
    row = row if isinstance(row, dict) else {}
    nested = row.get("ids") if isinstance(row.get("ids"), dict) else {}

    def pick(*keys):
        for key in keys:
            value = row.get(key)
            if value in (None, ""):
                value = nested.get(key)
            if value not in (None, ""):
                return _id_or_none(value)
        return None

    return {
        "decision_id": pick("decision_id", "source_decision_id"),
        "handoff_id": pick("handoff_id", "source_handoff_id", "receipt_id"),
        "watch_plan_id": pick("watch_plan_id", "source_watch_plan_id"),
        "confirmation_id": pick("confirmation_id"),
        "trade_id": pick("trade_id"),
        "outcome_id": pick("outcome_id"),
    }


def _fallback_outcome_id(trade_id="", run_id="", as_of=""):
    """outcome_chain 不可用时的同公式回退（格式/确定性完全一致，勿另立生成方）。"""
    upstream = _id_or_none(trade_id) or _id_or_none(run_id)
    match = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})",
                     str(as_of or "").strip())
    if not upstream or match is None:
        return None
    day = match.group(1) + match.group(2) + match.group(3)
    digest = hashlib.sha256(("%s|%s" % (upstream, 1)).encode("utf-8"))
    return "out-%s-%s" % (day, digest.hexdigest().upper()[:8])


def new_outcome_id(trade_id="", run_id="", as_of="", horizon_days=None):
    """生成 outcome_id：调用 IFS F5 单一生成方 ``outcome_chain.new_outcome_id``。

    上游键优先 trade_id（标准链），legacy 行回退 run_id；缺上游/日期 → None。
    """
    if _outcome_chain is not None:
        return _outcome_chain.new_outcome_id(
            trade_id=trade_id, run_id=run_id, as_of=as_of,
            horizon_days=horizon_days)
    return _fallback_outcome_id(trade_id=trade_id, run_id=run_id, as_of=as_of)


def fill_outcome_ids(row, ledger_row=None):
    """给 outcome 行补 outcome_id + 六 ID 键 + ``ids``（幂等，只补空值）。

    - 上游 ID 优先取行内值，其次取同 run_id 的 shadow_ledger 行（若带，如未来
      WIRE 账本行的 handoff/watch_plan/confirmation/trade）；
    - 六个键**必在**：缺失显式 ``None``（不拿 run_id/report 号冒充）；
    - ``outcome_id`` 已有不换；缺失时按 trade_id→run_id + 决策日生成（确定性）；
    - 不改任何已有非空字段（不破坏 legacy 行）。
    返回实际填充的字段名列表（用于 ``ids_repaired``/``id_fields`` 计数）。
    """
    if not isinstance(row, dict):
        raise TypeError("row 须为 dict: %r" % (row,))
    filled = []
    upstream = _record_ids(ledger_row) if isinstance(ledger_row, dict) else {}
    for key in _UPSTREAM_ID_KEYS:
        if _id_or_none(row.get(key)) is None:
            candidate = _id_or_none(upstream.get(key))
            if candidate:
                row[key] = candidate
                filled.append(key)
        row.setdefault(key, None)
    if _id_or_none(row.get("outcome_id")) is None:
        outcome_id = new_outcome_id(
            trade_id=row.get("trade_id") or "",
            run_id=row.get("run_id") or "",
            as_of=row.get("date") or row.get("as_of")
            or row.get("trade_date"))
        row["outcome_id"] = outcome_id
        if outcome_id:
            filled.append("outcome_id")
    row.setdefault("outcome_id", None)
    ids = {key: _id_or_none(row.get(key)) for key in SIX_ID_KEYS}
    if row.get("ids") != ids:
        row["ids"] = ids
        filled.append("ids")
    return filled



def _fallback_decision_id(symbol, date, run_id):
    """engine/decision_policy.py 不可用时的同公式回退（tests 断言两者一致）。"""
    raw = "%s|%s|%s" % (symbol, date, run_id)
    return "D1-%s-%s" % (str(date).replace("-", ""),
                         hashlib.sha256(raw.encode()).hexdigest()[:10])


def decision_id(symbol, date, run_id):
    """D1 decision_id：优先复用 engine/decision_policy.py 单一真源，失败回退同公式。

    延迟 import（脚本/测试无需预先配置 engine 路径）；不写盘、不联网。
    """
    global _POLICY_DECISION_ID
    if _POLICY_DECISION_ID is None:
        engine = os.path.join(HERE, "engine")
        if engine not in sys.path:
            sys.path.insert(0, engine)
        try:
            from decision_policy import decision_id as _policy_fn  # noqa: E402
        except Exception:  # noqa: BLE001 - 引擎目录不可用时按同公式回退
            _policy_fn = _fallback_decision_id
        _POLICY_DECISION_ID = _policy_fn
    return _POLICY_DECISION_ID(symbol, date, run_id)


def _ledger_by_run_id(rows):
    """shadow_ledger 行按 run_id 建索引（同 run_id 后写覆盖；空 run_id 跳过）。"""
    index = {}
    for row in rows or []:
        rid = str(row.get("run_id") or "")
        if rid:
            index[rid] = row
    return index


def _decision_id_from_ledger_row(ledger_row):
    """用 shadow_ledger 行复算 decision_id；字段不全/异常时返回 None（不伪造）。"""
    if not isinstance(ledger_row, dict):
        return None
    did = ledger_row.get("decision_id")
    if did not in (None, ""):
        return str(did)
    symbol = str(ledger_row.get("symbol") or "")
    date = str(ledger_row.get("date") or "")
    run_id = str(ledger_row.get("run_id") or "")
    if not (symbol and date and run_id):
        return None
    try:
        return decision_id(symbol, date, run_id)
    except Exception:  # noqa: BLE001 - 引擎异常不外溢（缺就 null）
        return None


def _valid_pit_text(value):
    """数据时点字符串校验（日期/日期时间，可带时区）；非法返回 ""（不伪造）。"""
    text = str(value or "").strip()
    if not text or not _PIT_TEXT_RE.match(text):
        return ""
    return text


def derive_pit_fields(ledger_row, *, require_evidence=False):
    """从 shadow_ledger 行派生 PIT / raw refs 字段（只读推导，不伪造）。

    返回 dict（可为空，调用方不得补默认值）：
      - ``pit_as_of``/``pit_source``：优先 raw evidence_refs 的 `pit_as_of`
        （取最大者，source=raw_ref:<kind>）；否则回退账本 ``date``
        （source=decision_date，**诚实标注是日期回退而非 raw**）；
      - ``raw_refs_status``/``raw_refs_in_bundle``：取账本行
        ``evidence_raw_refs_summary``（兼容顶层同名字段）。

    ``require_evidence=True``（历史 outcome 回放）时，账本行必须**确有**证据
    接线字段（evidence_refs / evidence_raw_refs_summary / raw_refs_status）才
    派生，避免给无证据旧行凭空补时点引发无意义重写。
    """
    if not isinstance(ledger_row, dict):
        return {}
    has_evidence = ("evidence_refs" in ledger_row
                    or "evidence_raw_refs_summary" in ledger_row
                    or "raw_refs_status" in ledger_row)
    if require_evidence and not has_evidence:
        return {}
    refs = ledger_row.get("evidence_refs")
    candidates = []
    if isinstance(refs, list):
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            pit = _valid_pit_text(ref.get("pit_as_of"))
            if pit:
                candidates.append((pit, "raw_ref:%s"
                                   % (str(ref.get("kind") or "ref"))))
    out = {}
    if candidates:
        candidates.sort()
        out["pit_as_of"], out["pit_source"] = candidates[-1]
    if not out and not require_evidence:
        date = _valid_pit_text(ledger_row.get("date"))
        if date:
            out["pit_as_of"] = date
            out["pit_source"] = "decision_date"
    summary = ledger_row.get("evidence_raw_refs_summary")
    status = ledger_row.get("raw_refs_status")
    if status in (None, "") and isinstance(summary, dict):
        status = summary.get("status")
    if status not in (None, ""):
        out["raw_refs_status"] = str(status)
    raw_in = ledger_row.get("raw_refs_in_bundle")
    if raw_in in (None, "") and isinstance(summary, dict):
        raw_in = summary.get("raw_refs_in_bundle")
    if isinstance(raw_in, int) and not isinstance(raw_in, bool):
        out["raw_refs_in_bundle"] = raw_in
    return out


def enrich_outcome_row(row, ledger_index):
    """按 run_id 从 shadow_ledger 补 outcome 行缺的 decision_id/model/PIT/raw 字段。

    - 只补空值（None/缺键），已有值一律不动；
    - decision_id 优先取账本行同名字段，否则按同公式复算；
    - model 取账本行值；账本行缺失/字段不全 → 落 null，**绝不编造**；
    - PIT/raw 字段仅当账本行确有证据接线字段时才补（derive_pit_fields
      require_evidence=True），否则保持缺失（不凭空补时点）。
    返回实际补到值的字段名列表（用于 repaired 计数）。
    """
    filled = []
    rid = str(row.get("run_id") or "")
    ledger_row = ledger_index.get(rid) if rid else None
    if row.get("decision_id") in (None, ""):
        computed = _decision_id_from_ledger_row(ledger_row)
        if "decision_id" not in row or row.get("decision_id") != computed:
            row["decision_id"] = computed
        if computed is not None:
            filled.append("decision_id")
    if row.get("model") in (None, ""):
        model = (ledger_row.get("model")
                 if isinstance(ledger_row, dict) else None)
        if model not in (None, ""):
            row["model"] = model
            filled.append("model")
        elif "model" not in row:
            row["model"] = None
    derived = derive_pit_fields(ledger_row, require_evidence=True)
    for key in PIT_FIELDS:
        if key not in derived:
            continue
        if row.get(key) in (None, ""):
            row[key] = derived[key]
            filled.append(key)
    return filled


def repair_outcomes(out_rows, ledger_rows):
    """补历史 outcome 行缺失字段（行数/顺序不变）；返回 (rows, report)。

    IFS F5c：除既有 decision_id/model/PIT 回填外，还给每行补 outcome_id + 六 ID
    + ``ids``（``fill_outcome_ids``，只补空值、确定性幂等）；ID 回填单独计数
    （``ids_repaired``/``id_fields``），不改旧 ``repaired``（decision/model/PIT）语义。
    """
    index = _ledger_by_run_id(ledger_rows)
    fields = {"decision_id": 0, "model": 0}
    repaired = 0
    pit_repaired = 0
    ids_repaired = 0
    id_fields = {}
    rows = []
    for row in out_rows or []:
        new_row = dict(row)
        filled = enrich_outcome_row(new_row, index)
        if filled:
            repaired += 1
            if any(name in PIT_FIELDS for name in filled):
                pit_repaired += 1
            for name in filled:
                fields[name] = fields.get(name, 0) + 1
        rid = str(new_row.get("run_id") or "")
        ledger_row = index.get(rid) if rid else None
        id_filled = fill_outcome_ids(new_row, ledger_row=ledger_row)
        if id_filled:
            ids_repaired += 1
            for name in id_filled:
                id_fields[name] = id_fields.get(name, 0) + 1
        rows.append(new_row)
    return rows, {"repaired": repaired, "fields": fields,
                  "pit_repaired": pit_repaired,
                  "ids_repaired": ids_repaired, "id_fields": id_fields}


def _write_jsonl_atomic(path, rows):
    """原子重写 JSONL（同目录临时文件 + os.replace），避免半行/半文件。"""
    path = str(path)
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _append_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def first_next_bar(bars, date):
    """bars: [[date, open, close, high, low, volume], ...]；返回 date 后首个交易日。"""
    for b in bars or []:
        if len(b) >= 3 and str(b[0]) > str(date):
            try:
                return str(b[0]), float(b[2])
            except (TypeError, ValueError):
                continue
    return None, None


def compute_outcome(row, price_fetcher):
    """单行 outcome；无 run_id/价格/次日 bar 时返回 None。"""
    run_id = str(row.get("run_id") or "")
    date = str(row.get("date") or "")
    symbol = str(row.get("symbol") or "")
    if not run_id or not date or not symbol:
        return None
    try:
        price = float(row.get("price_at_decision"))
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    try:
        bars = price_fetcher(symbol, 60) or []
    except Exception:  # noqa: BLE001
        return None
    next_date, next_close = first_next_bar(bars, date)
    if not next_date or next_close is None:
        return None
    ret = (next_close / price) - 1.0
    rec = str(row.get("recommendation") or "").upper()
    d = DIRECTION.get(rec)
    hit = None
    if d is not None:
        hit = 1 if (ret > 0 if d > 0 else ret < 0) else 0
    try:
        conf = float(row.get("confidence"))
    except (TypeError, ValueError):
        conf = None
    outcome = {
        "ts": time.time(),
        "run_id": run_id,
        "decision_id": decision_id(symbol, date, run_id),
        "date": date,
        "symbol": symbol,
        "recommendation": rec or None,
        "confidence": conf,
        "price_at_decision": price,
        "next_trade_date": next_date,
        "next_close": round(next_close, 4),
        "next_day_return": round(ret, 6),
        "hit": hit,
        "horizon_days": 1,
        "model": row.get("model"),
        "fallback": row.get("fallback"),
    }
    # PIT / raw refs（schema 向后兼容增量；旧读取方忽略未知键即可）
    pit = derive_pit_fields(row)
    outcome["pit_as_of"] = pit.get("pit_as_of")
    outcome["pit_source"] = pit.get("pit_source")
    outcome["raw_refs_status"] = pit.get("raw_refs_status")
    outcome["raw_refs_in_bundle"] = pit.get("raw_refs_in_bundle")
    # IFS F5c：六 ID 链（outcome_id 由 outcome_chain 单一生成方确定性生成；
    # 账本行若带 handoff/watch_plan/confirmation/trade ID 则透传，缺则显式 null）
    fill_outcome_ids(outcome, ledger_row=row)
    return outcome


def compute_all(rows, price_fetcher, existing_run_ids=None, since=None):
    """按 run_id 幂等计算 outcome；since（YYYY-MM-DD）只算该日（含）之后的行。"""
    done_ids = set(existing_run_ids or [])
    out = []
    for r in rows:
        if r.get("status") != "done":
            continue
        if since and str(r.get("date") or "") < str(since):
            continue
        rid = str(r.get("run_id") or "")
        if not rid or rid in done_ids:
            continue
        o = compute_outcome(r, price_fetcher)
        if o is not None:
            out.append(o)
            done_ids.add(rid)
    return out


def run_backfill(ledger_path, out_path, since=None, price_fetcher=None,
                 write=True, repair=True, week=None):
    """回填 outcome sidecar：只补缺失（run_id 幂等）+ 补历史行缺字段。

    IFS F5c：新 outcome 行与历史回填行都会补 outcome_id + 六 ID + ``ids``；旧
    legacy 行只补空值（不覆盖原字段、不伪造行情）。缺行情/价格的行不生成、
    不写（不造数）。

    返回 {'new','dates','since','repaired','repaired_pit','repaired_fields',
    'ids_repaired','id_fields','written','out_path','outcomes','summary'}；
    ``write=False`` 时零写盘（dry-run，但仍报告「本可补行数」便于运维预演）。
    """
    ledger_rows = _load_jsonl(ledger_path)
    out_rows = _load_jsonl(out_path)
    out_path = str(out_path)
    fetcher = price_fetcher if price_fetcher is not None else _default_price_fetcher
    existing = {str(o.get("run_id")) for o in out_rows if o.get("run_id")}
    new_rows = compute_all(ledger_rows, fetcher, existing, since=since)
    merged = list(out_rows) + new_rows
    repaired_report = {"repaired": 0, "pit_repaired": 0,
                       "fields": {"decision_id": 0, "model": 0},
                       "ids_repaired": 0, "id_fields": {}}
    if repair:
        merged, repaired_report = repair_outcomes(merged, ledger_rows)
    ids_repaired = repaired_report.get("ids_repaired", 0)
    written = False
    if write and (new_rows or repaired_report["repaired"] or ids_repaired):
        _write_jsonl_atomic(out_path, merged)
        written = True
    summary = summarize(merged, ledger_rows, week=week)
    dates = {}
    for row in new_rows:
        day = str(row.get("date") or "")
        dates[day] = dates.get(day, 0) + 1
    return {"new": len(new_rows), "dates": dict(sorted(dates.items())),
            "since": since, "repaired": repaired_report["repaired"],
            "repaired_pit": repaired_report.get("pit_repaired", 0),
            "repaired_fields": repaired_report["fields"],
            "ids_repaired": ids_repaired,
            "id_fields": dict(repaired_report.get("id_fields") or {}),
            "written": written, "out_path": out_path,
            "outcomes": len(merged), "summary": summary}


def summarize(outcomes, ledger_rows=None, week=None):
    ledger_rows = ledger_rows or []
    done = [r for r in ledger_rows if r.get("status") == "done"]
    directional = [o for o in outcomes if o.get("hit") is not None]
    hits = sum(1 for o in directional if o["hit"] == 1)
    rets = [o["next_day_return"] for o in outcomes
            if isinstance(o.get("next_day_return"), (int, float))]
    brier_terms = []
    for o in directional:
        c = o.get("confidence")
        if isinstance(c, (int, float)):
            brier_terms.append((float(c) - o["hit"]) ** 2)
    lat = sorted(float(r["latency_s"]) for r in done
                 if isinstance(r.get("latency_s"), (int, float)))

    def _pct(arr, p):
        if not arr:
            return None
        i = min(len(arr) - 1, max(0, int(round((len(arr) - 1) * p))))
        return round(arr[i], 1)

    rec_counts = {}
    for o in outcomes:
        k = o.get("recommendation") or "UNKNOWN"
        rec_counts[k] = rec_counts.get(k, 0) + 1
    pit_rows = [o for o in outcomes if str(o.get("pit_as_of") or "").strip()]
    raw_status_counts = {}
    for o in outcomes:
        status = str(o.get("raw_refs_status") or "").strip()
        if status:
            raw_status_counts[status] = raw_status_counts.get(status, 0) + 1
    model_counts = {}
    fallback_counts = {"true": 0, "false": 0, "unknown": 0}
    for r in done:
        m = str(r.get("model") or "unknown")
        model_counts[m] = model_counts.get(m, 0) + 1
        fb = r.get("fallback")
        if fb is True:
            fallback_counts["true"] += 1
        elif fb is False:
            fallback_counts["false"] += 1
        else:
            fallback_counts["unknown"] += 1
    return {
        "week": week,
        "ledger_done": len(done),
        "outcomes": len(outcomes),
        "coverage": round(len(outcomes) / len(done), 3) if done else None,
        "parse_ok_rate": round(
            sum(1 for r in done if r.get("parse_ok")) / len(done), 3) if done else None,
        "directional_n": len(directional),
        "hit_rate": round(hits / len(directional), 3) if directional else None,
        "avg_return": round(sum(rets) / len(rets), 6) if rets else None,
        "brier": round(sum(brier_terms) / len(brier_terms), 4) if brier_terms else None,
        "watch_ratio": round(
            sum(1 for r in done if r.get("recommendation") == "WATCH") / len(done), 3)
        if done else None,
        "latency_p50": _pct(lat, 0.5),
        "latency_p95": _pct(lat, 0.95),
        "by_recommendation": rec_counts,
        "models": model_counts,
        "fallback": fallback_counts,
        # PIT/raw refs 覆盖（2026-11-01 D1RAWPIT；旧行缺省不计数、不推断）
        "pit": {"outcomes_with_pit_as_of": len(pit_rows),
                "outcomes_with_raw_status": sum(raw_status_counts.values()),
                "raw_refs_status": raw_status_counts},
        # IFS F5c：六 ID 覆盖率（只计数，不推断；旧行缺失计 0）
        "id_chain": {
            "outcomes_with_outcome_id": sum(
                1 for o in outcomes if _id_or_none(o.get("outcome_id"))),
            "outcomes_with_decision_id": sum(
                1 for o in outcomes if _id_or_none(o.get("decision_id"))),
            "outcomes_with_trade_id": sum(
                1 for o in outcomes if _id_or_none(o.get("trade_id"))),
            "six_id_keys": list(SIX_ID_KEYS),
        },
        "warn_only": True,
    }


def render_markdown(s):
    lines = ["## D1 决策 outcome（只读 sidecar，不阻断）", ""]
    lines.append("> 周：**%s** ｜ 账本 done=**%d** ｜ outcome=**%d** ｜ 覆盖率=**%s**" % (
        s.get("week") or "—", s.get("ledger_done") or 0, s.get("outcomes") or 0,
        "—" if s.get("coverage") is None else "%.0f%%" % (s["coverage"] * 100)))
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---:|")
    lines.append("| parse_ok 率 | %s |" % ("—" if s.get("parse_ok_rate") is None
                                          else "%.1f%%" % (s["parse_ok_rate"] * 100)))
    lines.append("| 方向命中率 | %s |" % ("—" if s.get("hit_rate") is None
                                         else "%.1f%%（n=%d）" % (s["hit_rate"] * 100,
                                                                  s.get("directional_n") or 0)))
    lines.append("| 平均次日收益 | %s |" % ("—" if s.get("avg_return") is None
                                          else "%.3f%%" % (s["avg_return"] * 100)))
    lines.append("| Brier（越低越好） | %s |" % ("—" if s.get("brier") is None
                                              else "%.4f" % s["brier"]))
    lines.append("| WATCH 占比 | %s |" % ("—" if s.get("watch_ratio") is None
                                        else "%.0f%%" % (s["watch_ratio"] * 100)))
    lines.append("| 决策时延 P50 / P95 | %s / %s s |" % (
        s.get("latency_p50") or "—", s.get("latency_p95") or "—"))
    lines.append("")
    lines.append("**处理纪律**：只读统计，不自动改决策/不阻断；命中率仅对有方向决策有意义（HOLD/WATCH 不计 hit）。")
    return "\n".join(lines)


def _default_price_fetcher(symbol, n=60):
    engine = os.path.join(HERE, "engine")
    if engine not in sys.path:
        sys.path.insert(0, engine)
    from tools import kline_daily  # noqa: E402
    return kline_daily(symbol, n)


def _since_arg(value):
    """--since 参数校验（YYYY-MM-DD；非法即 argparse 报错退出 2）。"""
    try:
        time.strptime(str(value), "%Y-%m-%d")
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            "--since 需 YYYY-MM-DD 格式（如 2026-10-30）")
    return str(value)


def main(argv=None):
    ap = argparse.ArgumentParser(description="D1 决策 outcome 台账（只读 sidecar）")
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--week", default=None)
    ap.add_argument("--since", type=_since_arg, default=None, metavar="YYYY-MM-DD",
                    help="只回填该日期（含）之后缺失的 outcome（默认补全历史缺失）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true", help="只报告，不写 sidecar")
    # 兼容文档里的旧入口（2026-10-31 审计：文档写 --compute/--report，实际不支持）
    ap.add_argument("--compute", action="store_true", help="兼容旧入口（默认即计算）")
    ap.add_argument("--report", action="store_true", help="兼容旧入口（默认即报告）")
    args = ap.parse_args(argv)
    rep = run_backfill(args.ledger, args.out, since=args.since,
                       write=not args.no_write, week=args.week)
    summary = rep["summary"]
    if args.json:
        payload = {"new": rep["new"], "dates": rep["dates"],
                   "since": rep["since"], "repaired": rep["repaired"],
                   "repaired_fields": rep["repaired_fields"],
                   "repaired_pit": rep.get("repaired_pit", 0),
                   "ids_repaired": rep.get("ids_repaired", 0),
                   "id_fields": rep.get("id_fields") or {},
                   "written": rep["written"], "summary": summary}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        days = ",".join("%s:%d" % (d, n) for d, n in rep["dates"].items()) or "-"
        state = ("（--no-write 预演，未写盘）" if args.no_write
                 else ("已写 sidecar" if rep["written"] else "无新增（幂等）"))
        print("[decision_outcomes] since=%s new=%d dates=%s repaired=%d ids=%d "
              "%s：%s"
              % (args.since or "-", rep["new"], days, rep["repaired"],
                 rep.get("ids_repaired", 0), state, args.out))
        print(render_markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
