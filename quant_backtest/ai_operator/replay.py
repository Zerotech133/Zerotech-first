"""简化缠论算法的逐根前缀回放；观测时间不承诺结构永不重绘。"""

import copy
import hashlib
import json
import math

from .contracts import canonical_json, normalize_time


BAR_FIELDS = frozenset({"dt", "o", "h", "l", "c", "v", "close_at", "available_at"})
KINDS = ("fractals", "bis", "segments", "pivots", "signals")
PIVOT_FIELDS = {"fractals": "dt", "bis": "e_dt", "segments": "e_dt",
                "pivots": "e_dt", "signals": "d"}
COUNT_FIELDS = frozenset({"merged_n", "top_n", "bottom_n"})
MAX_BARS = 2000


def _validate_bars(bars):
    if type(bars) is not list:
        raise ValueError("bars 必须是 JSON 列表")
    if len(bars) > MAX_BARS:
        raise ValueError("逐根回放最多支持 2000 根 K 线")
    canonical_json(bars)
    validated = []
    seen = set()
    previous_close = previous_available = None
    for bar in bars:
        if type(bar) is not dict or set(bar) != BAR_FIELDS:
            raise ValueError("K 线必须仅包含 dt/o/h/l/c/v/close_at/available_at")
        dt = bar["dt"]
        if type(dt) is not str or not dt.strip() or dt in seen:
            raise ValueError("dt 必须是非空且唯一的字符串")
        for key in ("o", "h", "l", "c", "v"):
            value = bar[key]
            if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
                raise ValueError(f"{key} 必须是有限数值且不能是布尔值")
            if value < 0 or (key != "v" and value == 0):
                raise ValueError("OHLC 必须为正数，v 必须非负")
        if not bar["l"] <= min(bar["o"], bar["c"]) <= max(bar["o"], bar["c"]) <= bar["h"]:
            raise ValueError("OHLC 高低价关系非法")
        if type(bar["close_at"]) is not str or type(bar["available_at"]) is not str:
            raise ValueError("close_at 和 available_at 必须是带时区 ISO 字符串")
        close_at = normalize_time(bar["close_at"])
        available_at = normalize_time(bar["available_at"])
        if available_at < close_at:
            raise ValueError("available_at 不能早于 close_at")
        if previous_close is not None and (close_at <= previous_close or available_at <= previous_available):
            raise ValueError("close_at 和 available_at 必须分别严格递增")
        normalized = dict(bar, close_at=close_at, available_at=available_at)
        validated.append(normalized)
        seen.add(dt)
        previous_close, previous_available = close_at, available_at
    return validated


def _default_analyzer(prefix):
    try:
        from chanlun_bi import analyze
    except ModuleNotFoundError as exc:
        raise RuntimeError("本地结构算法依赖不可用，请配置 chanlun_bi 及其依赖的模块路径") from exc
    return analyze(prefix, closes=[bar["c"] for bar in prefix])


def _freeze_structures(raw, visible_dts):
    result = json.loads(canonical_json(raw))
    if type(result) is not dict or not set(result).intersection(KINDS):
        raise ValueError("算法输出必须是包含已知结构集合的 JSON 对象")
    if set(result) - set(KINDS) - COUNT_FIELDS:
        raise ValueError("算法输出包含不支持的字段")
    for field in COUNT_FIELDS.intersection(result):
        if type(result[field]) is not int or result[field] < 0:
            raise ValueError("算法计数字段必须是非负整数")
    for kind in KINDS:
        entries = result.get(kind, [])
        if type(entries) is not list:
            raise ValueError(f"算法 {kind} 必须是 JSON 列表")
        for entry in entries:
            if type(entry) is not dict:
                raise ValueError(f"算法 {kind} 的结构必须是 JSON 对象")
            pivot = entry.get(PIVOT_FIELDS[kind])
            if type(pivot) is not str or pivot not in visible_dts:
                raise ValueError(f"算法 {kind} 的结构定位必须指向当前可见 K 线")
            for field in ("dt", "s_dt", "e_dt", "d"):
                if field in entry and (type(entry[field]) is not str or entry[field] not in visible_dts):
                    raise ValueError("算法结构引用了未来或未知 K 线")
    return result


def replay_bars(bars: list[dict], analyzer=None,
                algorithm_id: str = "local-chanlun-bi-v1") -> dict:
    """每次仅分析当前已可用前缀，并保留结构新增、撤销的时点记录。

    默认算法采用既有简化笔、线段、中枢和买卖点口径。confirmed_at 只记录
    本次连续出现期间首次观测的 available_at，status 始终是 observed。
    默认算法按 dt 字典序计算 MACD 区间，故要求 dt 也严格字典序递增。
    """
    if type(algorithm_id) is not str or not algorithm_id.strip():
        raise ValueError("algorithm_id 必须是非空字符串")
    if analyzer is not None and not callable(analyzer):
        raise ValueError("analyzer 必须可调用")
    validated = _validate_bars(bars)
    if analyzer is None and any(left["dt"] >= right["dt"]
                                for left, right in zip(validated, validated[1:])):
        raise ValueError("默认本地算法要求 dt 严格按字典序递增")
    analyze = _default_analyzer if analyzer is None else analyzer
    snapshots = []
    active = {}
    visible_dts = set()
    for index, bar in enumerate(validated):
        visible_dts.add(bar["dt"])
        as_of = bar["available_at"]
        structures = _freeze_structures(analyze(copy.deepcopy(validated[:index + 1])), visible_dts)
        current = {}
        added = []
        for kind in KINDS:
            for data in structures.get(kind, []):
                version_id = hashlib.sha256(canonical_json({"kind": kind, "data": data}).encode("utf-8")).hexdigest()
                if version_id in current:
                    raise ValueError("算法输出包含重复结构版本")
                if version_id in active:
                    record = active[version_id]
                else:
                    record = dict(kind=kind, version_id=version_id,
                                  pivot_at=data[PIVOT_FIELDS[kind]], confirmed_at=as_of,
                                  observed_at=as_of, status="observed", data=copy.deepcopy(data))
                    added.append(record)
                current[version_id] = record
        # 撤销形成新的观测记录，避免回写先前快照和首次观测时间。
        withdrawn = [dict(copy.deepcopy(record), observed_at=as_of)
                     for version_id, record in active.items() if version_id not in current]
        snapshots.append(dict(as_of=as_of, bar_dt=bar["dt"], visible_count=index + 1,
                              structures=structures, changes=dict(added=added, withdrawn=withdrawn)))
        active = current
    result = dict(schema_version=1, algorithm_id=algorithm_id,
                  bar_count=len(validated), snapshots=snapshots)
    return json.loads(canonical_json(result))
