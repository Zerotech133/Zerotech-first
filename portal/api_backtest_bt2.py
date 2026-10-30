# -*- coding: utf-8 -*-
"""api_backtest 拆分 B1（2026-10-30 P68）：bt2 参数化回测 / 我的策略 / minute 产物读取。

自 portal/api_backtest.py L5639-6394 纯移动；依赖单向：本模块不 import api_backtest。
"""
import datetime
import logging
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
import threading as _threading
from api_common import _read_json, log_exc, STATION
from api_backtest_minute import _BT2_DIR, _BT2_LOCK, _BT2_STATE_DIR, _JOB_SPEC_REGISTRY, _MINUTE_MARKET_POLICIES, _MINUTE_SOURCE_REGISTRY, _calendar_error_code, _descriptor_failure, _minute_error_code, _minute_execution_config, _minute_output_root, _minute_transaction_policy, _rehydrate_minute_source, _safe_minute_artifact, _validate_minute_order_policy, _validate_runtime_calendar




def _bt2_state_path(jid):
    return os.path.join(_BT2_STATE_DIR, "bt2_%s.state.json" % jid)


# ===== K-STOCK-357 我的策略（NL 建策略沉淀 · portal/data/my_strategies.json 单文件 ·
#       参数键白名单与 bt2_run 同语义 · 写操作走 _MUTATION_POSTS 同守卫） =====
_MY_STRAT_FILE = os.path.join(STATION, "portal", "data", "my_strategies.json")
_MY_STRAT_KEYS = frozenset((
    "strategy", "d0", "d1", "gate_mode", "min_score", "slot_pct", "top_n",
    "buy_filter", "universe", "hist_st", "limit_check", "runup_cap"))


def _my_strat_load():
    try:
        with open(_MY_STRAT_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _my_strat_save(lst):
    os.makedirs(os.path.dirname(_MY_STRAT_FILE), exist_ok=True)
    tmp = _MY_STRAT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lst, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _MY_STRAT_FILE)


def _api_my_strategies(req):
    """我的策略（NL 沉淀）：
    {"action":"list"}                    → {"ok":true,"strategies":[...]}
    {"action":"save","name","params",..} → {"ok":true,"id","total"}
    {"action":"delete","id"}             → {"ok":true,"total"}"""
    if not isinstance(req, dict):
        return {"ok": False, "error": "bad request"}
    act = str(req.get("action") or "list")
    if act == "list":
        return {"ok": True, "strategies": _my_strat_load()}
    if act == "save":
        name = str(req.get("name") or "").strip()[:40]
        note = str(req.get("note") or "").strip()[:200]
        params = req.get("params")
        if not name:
            return {"ok": False, "error": "策略名不能为空"}
        if not isinstance(params, dict) or not params.get("strategy"):
            return {"ok": False, "error": "参数集缺 strategy 字段"}
        clean = {k: params[k] for k in params if k in _MY_STRAT_KEYS}
        if not clean.get("strategy") or not (clean.get("d0") and clean.get("d1")):
            return {"ok": False, "error": "参数集不完整（缺策略/窗口）"}
        lst = _my_strat_load()
        # 368：带 id 且存在 → upsert（编辑后保存，保留原 id 与创建语义）
        sid_in = str(req.get("id") or "")
        if sid_in:
            for x in lst:
                if x.get("id") == sid_in:
                    x["name"] = name
                    x["params"] = clean
                    x["note"] = note
                    x["ts"] = time.strftime("%Y-%m-%d %H:%M") + " 编辑"
                    _my_strat_save(lst)
                    return {"ok": True, "id": sid_in, "total": len(lst), "updated": True}
            return {"ok": False, "error": "策略不存在"}
        sid = "my_" + time.strftime("%Y%m%d%H%M%S") + "_%d" % (len(lst) + 1)
        lst.append({"id": sid, "name": name, "params": clean, "note": note,
                    "ts": time.strftime("%Y-%m-%d %H:%M")})
        _my_strat_save(lst)
        return {"ok": True, "id": sid, "total": len(lst)}
    if act == "delete":
        sid = str(req.get("id") or "")
        lst = _my_strat_load()
        rest = [x for x in lst if x.get("id") != sid]
        if len(rest) == len(lst):
            return {"ok": False, "error": "策略不存在"}
        _my_strat_save(rest)
        return {"ok": True, "total": len(rest)}
    return {"ok": False, "error": "unknown action"}


def _bt2_write_state(jid, **kv):
    st = _read_json(_bt2_state_path(jid))
    if not isinstance(st, dict) or not st:
        st = {"id": jid}          # _read_json 缺文件返回 {}（falsy dict），必须补 id
    st.setdefault("id", jid)
    st.update(kv)
    st["ts"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if st.get("execution_mode") == "minute_store" or (isinstance(st.get("params"), dict) and
                                                       st["params"].get("execution_mode") == "minute_store"):
        # Minute readers may run concurrently with the worker. Never expose a
        # partially written state, and never silently report a failed done write.
        os.makedirs(_BT2_STATE_DIR, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=_BT2_STATE_DIR,
                prefix=f".bt2_{jid}.", suffix=".tmp", delete=False,
            ) as state_file:
                temporary = state_file.name
                json.dump(st, state_file, ensure_ascii=False)
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary, _bt2_state_path(jid))
            temporary = None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
        return
    try:
        os.makedirs(_BT2_STATE_DIR, exist_ok=True)
        with open(_bt2_state_path(jid), "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
    except Exception as _e:
        log_exc(_e)


def _bt2_start(rec):
    """POST /api/bt2_run: validate, register, and start one worker."""
    mode = str(rec.get("execution_mode") or "daily_legacy").strip()
    if mode not in ("daily_legacy", "minute_store"):
        return {"ok": False, "error": "execution_mode 须为 daily_legacy/minute_store"}
    if mode == "minute_store":
        source_ref = str(rec.get("source_ref") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", source_ref):
            return {"ok": False, "error": "minute_store requires valid source_ref"}
        descriptor = _JOB_SPEC_REGISTRY.get(source_ref)
        if descriptor is None:
            return {"ok": False, "error": "SOURCE_UNAVAILABLE: source_ref unavailable", "error_code": "SOURCE_UNAVAILABLE", "source_ref": source_ref, "source_availability": "source_unavailable", "source_error": "SOURCE_UNAVAILABLE"}
        if descriptor.availability != "available" or _JOB_SPEC_REGISTRY.is_expired(source_ref):
            failure = _descriptor_failure(descriptor)
            if descriptor.availability == "available":
                _JOB_SPEC_REGISTRY.mark_unavailable(source_ref, failure)
            return {"ok": False, "error": failure, "error_code": failure, "source_ref": source_ref, "source_availability": "source_unavailable", "source_error": failure}
        spec = _MINUTE_SOURCE_REGISTRY.get(source_ref)
        if not isinstance(spec, dict):
            ok, error = _rehydrate_minute_source(source_ref)
            spec = _MINUTE_SOURCE_REGISTRY.get(source_ref) if ok else None
            if not ok and error in {"POLICY_MISMATCH", "INVALID_TIF_CONFIG", "PARTIAL_FILL_UNSUPPORTED"}:
                return {"ok": False, "error": error, "error_code": error, "source_ref": source_ref,
                        "source_availability": "source_unavailable", "source_error": error}
        if isinstance(spec, dict):
            try:
                _validate_minute_order_policy(descriptor, spec)
            except (ValueError, TypeError) as exc:
                code = _minute_error_code(exc)
                return {"ok": False, "error": code, "error_code": code, "source_ref": source_ref,
                        "source_availability": "source_unavailable", "source_error": code}
        if not isinstance(spec, dict) or not spec.get("order_intents"):
            code = "FEE_CONFIG_UNSUPPORTED" if descriptor.fee_mode == "PER_ORDER" and not (spec or {}).get("fee_provider") and not (spec or {}).get("fee_calculator") else "SOURCE_UNAVAILABLE"
            _JOB_SPEC_REGISTRY.mark_unavailable(source_ref, code)
            return {"ok": False, "error": code, "error_code": code, "source_ref": source_ref, "source_availability": "source_unavailable", "source_error": code}
        if descriptor.fee_mode == "PER_ORDER" and spec.get("fee_provider") is None and spec.get("fee_calculator") is None:
            return {"ok": False, "error": "FEE_CONFIG_UNSUPPORTED", "error_code": "FEE_CONFIG_UNSUPPORTED", "source_ref": source_ref, "source_availability": "available", "source_error": "FEE_CONFIG_UNSUPPORTED"}
        if spec.get("status_provider") is None or spec.get("limit_provider") is None:
            _JOB_SPEC_REGISTRY.mark_unavailable(source_ref, "SOURCE_UNAVAILABLE")
            return {"ok": False, "error": "SOURCE_UNAVAILABLE", "error_code": "SOURCE_UNAVAILABLE", "source_ref": source_ref, "source_availability": "source_unavailable", "source_error": "SOURCE_UNAVAILABLE"}
        if descriptor.calendar_ref:
            for key in ("calendar_ref", "calendar_version"):
                if key in rec and rec.get(key) not in (None, "", getattr(descriptor, key)):
                    return {"ok": False, "error": "CALENDAR_MISMATCH", "error_code": "CALENDAR_MISMATCH"}
            try:
                _validate_runtime_calendar(source_ref, descriptor, spec)
            except Exception as exc:
                code = _calendar_error_code(exc)
                _JOB_SPEC_REGISTRY.mark_unavailable(source_ref, code)
                return {"ok": False, "error": code, "error_code": code, "source_ref": source_ref, "source_availability": "source_unavailable", "source_error": code}
        requested_timezone = str(rec.get("timezone") or descriptor.timezone)
        if requested_timezone != descriptor.timezone:
            return {"ok": False, "error": "TIMEZONE_MISMATCH", "error_code": "TIMEZONE_MISMATCH"}
        requested_market = str(rec.get("market") or descriptor.market).upper()
        requested_timeframe = str(rec.get("timeframe") or descriptor.timeframe).lower()
        if requested_market != descriptor.market or requested_timeframe != descriptor.timeframe:
            return {"ok": False, "error": "SOURCE_UNAVAILABLE"}
        if str(rec.get("strategy") or "minute_store") not in ("minute_store", "chanlun"):
            return {"ok": False, "error": "minute_store strategy unsupported"}
        # Descriptor is authoritative: client transaction fields may only repeat
        # the registered policy, never override runtime callables or identities.
        policy_projection = _minute_transaction_policy(descriptor, spec, rec)
        for key, expected in policy_projection.items():
            if key not in rec or rec.get(key) in (None, ""):
                continue
            actual = rec.get(key)
            if key in {"fee_mode", "time_in_force"}:
                actual = str(actual).upper()
            if actual != expected:
                return {"ok": False, "error": "POLICY_MISMATCH", "error_code": "POLICY_MISMATCH", "source_error": "POLICY_MISMATCH"}
    strategy = str(rec.get("strategy") or ("minute_store" if mode == "minute_store" else "chanlun")).strip()
    if mode == "minute_store" and strategy == "minute_store":
        pass
    elif strategy not in ("chanlun", "double_knife"):
        return {"ok": False, "error": "strategy 须为 chanlun/double_knife"}
    d0 = str(rec.get("d0") or "").replace("-", "").strip()
    d1 = str(rec.get("d1") or "").replace("-", "").strip()
    explicit_window = rec.get("start_at") not in (None, "") or rec.get("end_at") not in (None, "")
    if mode == "minute_store":
        if explicit_window:
            if rec.get("start_at") in (None, "") or rec.get("end_at") in (None, ""):
                return {"ok": False, "error": "TIME_INVALID: start_at/end_at required", "error_code": "TIME_INVALID"}
            if d0 or d1:
                return {"ok": False, "error": "TIME_INVALID: d0/d1 conflicts with explicit window", "error_code": "TIME_INVALID"}
        elif not (re.fullmatch(r"\d{8}", d0 or "") and re.fullmatch(r"\d{8}", d1 or "") and d0 <= d1):
            return {"ok": False, "error": "TIME_INVALID: d0/d1 required", "error_code": "TIME_INVALID"}
    elif not (re.fullmatch(r"\d{8}", d0 or "") and re.fullmatch(r"\d{8}", d1 or "") and d0 < d1):
        return {"ok": False, "error": "d0/d1 须为 8 位日期且 d0<d1"}

    def _ratio(key, lo, hi, default):
        """比例参数（slot/runup/stop）：兼容裸百分数（8→0.08、15→0.15、-8→-0.08）。"""
        try:
            v = float(rec.get(key))
        except (TypeError, ValueError):
            return default
        if abs(v) > 1:
            v = v / 100.0
        return max(lo, min(hi, v))

    def _intv(key, lo, hi, default):
        try:
            v = int(float(rec.get(key)))
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))

    params = {
        "execution_mode": mode, "strategy": strategy, "d0": d0, "d1": d1,
        "source_ref": str(rec.get("source_ref") or "") if mode == "minute_store" else None,
        "timezone": str(rec.get("timezone") or (_JOB_SPEC_REGISTRY.get(str(rec.get("source_ref") or "")).timezone if mode == "minute_store" and _JOB_SPEC_REGISTRY.get(str(rec.get("source_ref") or "")) else "Asia/Shanghai")) if mode == "minute_store" else None,
        "market": str(rec.get("market") or (_JOB_SPEC_REGISTRY.get(str(rec.get("source_ref") or "")).market if mode == "minute_store" and _JOB_SPEC_REGISTRY.get(str(rec.get("source_ref") or "")) else "CN")).upper() if mode == "minute_store" else None,
        "timeframe": str(rec.get("timeframe") or (_JOB_SPEC_REGISTRY.get(str(rec.get("source_ref") or "")).timeframe if mode == "minute_store" and _JOB_SPEC_REGISTRY.get(str(rec.get("source_ref") or "")) else "1m")).lower() if mode == "minute_store" else None,
        "top_n": _intv("top_n", 1, 50, 10),
        # 槽数×regime 联动（K-STOCK-317）：mid 档槽数（0=不联动）
        "top_n_mid": _intv("top_n_mid", 0, 50, 0),
        "slot_pct": _ratio("slot_pct", 0.01, 0.5, 0.08),
        # 单票仓位分配（K-STOCK-297：固定 X% / 区间随机 a~b% / 评分权重自动调仓）
        "slot_mode": rec.get("slot_mode") if rec.get("slot_mode") in ("fixed", "random", "score") else "fixed",
        "slot_min": _ratio("slot_min", 0.005, 0.5, 0.05),
        "slot_max": _ratio("slot_max", 0.01, 0.5, 0.10),
        # 手动指定票池（可手动添加）：逗号分隔代码，空=全自动评分前十
        "manual_picks": str(rec.get("manual_picks") or "")[:2000],
        # 总仓位（K-STOCK-295 老田：档位可自定义——regime 三档各自可配或固定 X%）
        "cap_mode": "fixed" if rec.get("cap_mode") == "fixed" else "regime_8050",
        "cap_high": _ratio("cap_high", 0.05, 1.0, 0.80),
        "cap_mid": _ratio("cap_mid", 0.05, 1.0, 0.50),
        "cap_low": _ratio("cap_low", 0.0, 1.0, 0.30),
        "cap_fixed": _ratio("cap_fixed", 0.05, 1.0, 0.80),
        # 补仓规则：信号有效期（确认后 N 日未入场放弃·0=不限）
        "sig_expiry": _intv("sig_expiry", 0, 60, 0),
        # 清仓规则：三买失效离场三件套（引擎里有、表单此前未暴露）
        "three_fail_exit": "false" if str(rec.get("three_fail_exit", "")).strip().lower() == "false" else "true",
        "fail_level": rec.get("fail_level") if rec.get("fail_level") in ("zg", "zd") else "zg",
        "fail_buffer": _ratio("fail_buffer", 0, 0.2, 0.0),
        "regime_gate": "true" if str(rec.get("regime_gate", "")).strip().lower() == "true" else "false",
        # 开新仓闸门三档（K-STOCK-301）：off=不设 / weak=弱市不开 / high=仅高位开（291 包 C·高位限定）
        "gate_mode": rec.get("gate_mode") if rec.get("gate_mode") in ("off", "weak", "high") else "off",
        # 评分门槛（K-STOCK-303）：三买评分低于此值不入场，0=不限（量能/背驰过滤落地形态）
        "min_score": _intv("min_score", 0, 100, 0),
        # 历史 ST 回溯（K-STOCK-311·默认开）：窗口内曾为 ST 的股票剔除（修幸存者偏差）
        "hist_st": "false" if str(rec.get("hist_st", "")).strip().lower() in ("false", "0", "no") else "true",
        # 智策对照移植（K-STOCK-305）：涨跌停可成交检查 + 移动止损
        # 涨跌停可成交检查（307 定稿默认开·305 智策对照移植）：显式传 false 可关
        "limit_check": "false" if str(rec.get("limit_check", "")).strip().lower() in ("false", "0", "no") else "true",
        "trail_activate": _ratio("trail_activate", 0, 0.5, 0.0),
        "trail_drawdown": _ratio("trail_drawdown", 0, 0.5, 0.0),
        "runup_cap": _ratio("runup_cap", 0, 0.5, 0.05 if strategy == "chanlun" else 0),
        "stop_pct": _ratio("stop_pct", -0.5, 0, 0.0),
        "holding_max_days": _intv("holding_max_days", 0, 60, 5 if strategy == "double_knife" else 0),
        # 缠论买点等级过滤（10-13 老田拍板：只看三买）
        "buy_filter": rec.get("buy_filter") if rec.get("buy_filter") in ("23", "2", "3", "all") else "3",
        # 样本域（10-13 老田拍板：全市场 5000+ 剔 ST/北交所，一二三买入池·仅三买交易）
        "universe": rec.get("universe") if rec.get("universe") in ("market", "pool76") else "market",
    }
    if mode == "minute_store":
        descriptor = _JOB_SPEC_REGISTRY.get(str(rec.get("source_ref") or ""))
        policy = _MINUTE_MARKET_POLICIES.get(str(params.get("market") or "CN").upper())
        if descriptor is not None and policy is not None and descriptor.provider_policy_ref != policy["policy_ref"]:
            return {"ok": False, "error": "POLICY_MISMATCH", "error_code": "POLICY_MISMATCH"}
        if "t_plus_one" in rec and type(rec.get("t_plus_one")) is not bool:
            return {"ok": False, "error": "POLICY_MISMATCH", "error_code": "POLICY_MISMATCH"}
        if "t_plus_one" in rec and rec.get("t_plus_one") != policy["t_plus_one"]:
            return {"ok": False, "error": "POLICY_MISMATCH", "error_code": "POLICY_MISMATCH"}
        if "lot_size" in rec and rec.get("lot_size") != policy.get("lot_size", 1):
            return {"ok": False, "error": "POLICY_MISMATCH", "error_code": "POLICY_MISMATCH"}
        params.update({
            "start_at": rec.get("start_at"),
            "end_at": rec.get("end_at"),
            "as_of": rec.get("as_of"),
            "closed_only": rec.get("closed_only", True),
            "initial_cash": rec.get("initial_cash", 0),
            "fee_bps": rec.get("fee_bps", 0),
            "slippage_bps": rec.get("slippage_bps", 0),
            "lot_size": policy.get("lot_size", 1),
            "t_plus_one": (policy["t_plus_one"] if policy is not None else True),
            "policy_ref": (policy["policy_ref"] if policy is not None else None),
            "calendar_ref": descriptor.calendar_ref if descriptor is not None else None,
            "calendar_version": descriptor.calendar_version if descriptor is not None else None,
            "calendar_required": bool(descriptor is not None and descriptor.calendar_ref),
            "execution_query": None,
            "transaction_policy": _minute_transaction_policy(descriptor, spec, rec) if descriptor is not None else {},
            "fee_mode": descriptor.fee_mode if descriptor is not None else "PER_FILL",
            "fee_schedule_ref": descriptor.fee_schedule_ref if descriptor is not None else None,
            "fee_schedule_version": descriptor.fee_schedule_version if descriptor is not None else None,
            "fee_schedule_hash": descriptor.fee_schedule_hash if descriptor is not None else None,
            "time_in_force": descriptor.time_in_force if descriptor is not None else "DAY",
            "max_attempts": descriptor.max_attempts if descriptor is not None else None,
            "expire_at": descriptor.expire_at if descriptor is not None else None,
            "partial_fill_params": descriptor.partial_fill_params if descriptor is not None else None,
            "market_rule_ref": descriptor.market_rule_ref if descriptor is not None else None,
            "market_rule_version": descriptor.market_rule_version if descriptor is not None else None,
        })
        try:
            qbt = os.path.join(STATION, "quant_backtest")
            if qbt not in sys.path:
                sys.path.insert(0, qbt)
            from backtest_engine_v2 import _minute_query_from_params
            normalized_query = _minute_query_from_params(
                params, market=params.get("market") or "CN",
                timezone_name=params.get("timezone") or "Asia/Shanghai")
            params["query_metadata"] = {
                "start": normalized_query.start.isoformat() if normalized_query.start else None,
                "end": normalized_query.end.isoformat() if normalized_query.end else None,
                "as_of": normalized_query.as_of.isoformat() if normalized_query.as_of else None,
                "closed_only": True,
            }
        except ValueError as exc:
            message = str(exc)
            code = "TIME_INVALID" if "TIME" in message or "time" in message or "window" in message else "INVALID_ARGUMENT"
            return {"ok": False, "error": message, "error_code": code}
        # A calendar/provider callback may have replaced the batch after the first gate.
        try:
            _validate_minute_order_policy(descriptor, spec)
        except (ValueError, TypeError) as exc:
            code = _minute_error_code(exc)
            return {"ok": False, "error": code, "error_code": code, "source_ref": source_ref,
                    "source_availability": "source_unavailable", "source_error": code}
    now = datetime.datetime.now()
    jid = now.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()
    reserved = False
    if mode == "minute_store":
        # Reserve the generated ID before writing any artifact: a prior done
        # state must never be reused even if the new state commit fails.
        try:
            os.makedirs(_BT2_STATE_DIR, exist_ok=True)
            if os.path.lexists(os.path.join(_minute_output_root(), f"minute_{jid}.json")):
                return {"ok": False, "error": "JOB_ID_COLLISION", "error_code": "JOB_ID_COLLISION"}
            descriptor_fd = os.open(_bt2_state_path(jid), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor_fd)
            reserved = True
        except FileExistsError:
            return {"ok": False, "error": "JOB_ID_COLLISION", "error_code": "JOB_ID_COLLISION"}
        except OSError:
            return _minute_artifact_unavailable()
    try:
        _bt2_write_state(jid, status="queued", params=params,
                         execution_mode=mode,
                         transaction_policy=params.get("transaction_policy", {}),
                         error_code=None, source_error=None,
                         submitted_at=now.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        if reserved:
            try:
                os.unlink(_bt2_state_path(jid))
            except FileNotFoundError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
        return _minute_artifact_unavailable()
    t = _threading.Thread(target=_bt2_worker, args=(jid, params), daemon=True)
    t.start()
    return {"ok": True, "transaction_policy": params.get("transaction_policy", {}),
            "job": {"id": jid, "status": "queued", "params": params,
                    "transaction_policy": params.get("transaction_policy", {})}}


def _bt2_worker(jid, params):
    if not _BT2_LOCK.acquire(timeout=1):
        # 已有任务在跑：标记排队等待（拿锁的线程跑完自动轮到下一个？一期简单拒绝）
        _bt2_write_state(jid, status="dead", error="已有回测任务在跑，请稍后再试（一期单并发）")
        return
    try:
        if params.get("execution_mode") == "minute_store":
            previous = _read_json(_bt2_state_path(jid))
            if isinstance(previous, dict) and previous.get("status") == "done":
                # Direct worker invocation must not overwrite a completed job.
                return
        _bt2_write_state(jid, status="running", stage="初始化", pct=1, note="引擎载入")

        def progress(stage, pct, note):
            _bt2_write_state(jid, status="running", stage=stage, pct=pct, note=note)

        qbt = os.path.join(STATION, "quant_backtest")
        if qbt not in sys.path:
            sys.path.insert(0, qbt)
        import backtest_engine_v2 as eng
        worker_params = dict(params)
        if params.get("execution_mode") == "minute_store":
            source_ref = params.get("source_ref")
            descriptor = _JOB_SPEC_REGISTRY.get(source_ref)
            if (descriptor is None or descriptor.availability != "available" or
                    _JOB_SPEC_REGISTRY.is_expired(source_ref)):
                _bt2_write_state(
                    jid, status="dead", error="SOURCE_UNAVAILABLE",
                    error_code="SOURCE_UNAVAILABLE", source_ref=source_ref,
                    source_availability="source_unavailable",
                    source_error="SOURCE_UNAVAILABLE",
                )
                return
            spec = _MINUTE_SOURCE_REGISTRY.get(source_ref)
            if not isinstance(spec, dict):
                ok, recovery_error = _rehydrate_minute_source(source_ref)
                spec = _MINUTE_SOURCE_REGISTRY.get(source_ref) if ok else None
                if not ok and recovery_error in {"POLICY_MISMATCH", "INVALID_TIF_CONFIG", "PARTIAL_FILL_UNSUPPORTED"}:
                    _bt2_write_state(jid, status="dead", error=recovery_error, error_code=recovery_error,
                                     source_ref=source_ref, source_availability="source_unavailable",
                                     source_error=recovery_error, fills=[],
                                     cash=float(params.get("initial_cash", 0.0) or 0.0), positions={}, inventory_lots=[])
                    return
            if not isinstance(spec, dict):
                _bt2_write_state(jid, status="dead", error="SOURCE_UNAVAILABLE", error_code="SOURCE_UNAVAILABLE", source_ref=source_ref, source_availability="source_unavailable", source_error="SOURCE_UNAVAILABLE")
                return
            try:
                _validate_minute_order_policy(descriptor, spec)
            except (ValueError, TypeError) as exc:
                code = _minute_error_code(exc)
                _bt2_write_state(jid, status="dead", error=code, error_code=code,
                                 source_ref=source_ref, source_availability="source_unavailable",
                                 source_error=code, transaction_policy=_minute_transaction_policy(descriptor, spec, worker_params),
                                 fills=[], cash=float(params.get("initial_cash", 0.0) or 0.0),
                                 positions={}, inventory_lots=[])
                return
            worker_params["bar_store"] = spec["bar_store"]
            worker_params["order_intents"] = spec["order_intents"]
            worker_params["status_provider"] = spec["status_provider"]
            worker_params["limit_provider"] = spec["limit_provider"]
            worker_params["fee_mode"] = descriptor.fee_mode
            worker_params["fee_schedule_ref"] = descriptor.fee_schedule_ref
            worker_params["fee_schedule_version"] = descriptor.fee_schedule_version
            worker_params["fee_schedule_hash"] = descriptor.fee_schedule_hash
            worker_params["time_in_force"] = descriptor.time_in_force
            worker_params["max_attempts"] = descriptor.max_attempts
            worker_params["expire_at"] = descriptor.expire_at
            worker_params["partial_fill_params"] = descriptor.partial_fill_params
            worker_params["fee_provider"] = spec.get("fee_provider")
            worker_params["fee_calculator"] = spec.get("fee_calculator")
            worker_params["fill_model"] = spec.get("fill_model")
            # 统一市场规则路径：注册了 market_rule_provider 的 source 强制走
            # 规则判定（market_rules_required=True），不静默回退旧双 provider 分支。
            rule_provider = spec.get("market_rule_provider")
            rules_required = rule_provider is not None
            if rules_required:
                worker_params["market_rule_provider"] = rule_provider
                worker_params["market_rules_required"] = True
            descriptor = _JOB_SPEC_REGISTRY.get(source_ref)
            if descriptor is not None and descriptor.calendar_ref:
                try:
                    worker_params["calendar_provider"] = _validate_runtime_calendar(source_ref, descriptor, spec)
                    worker_params["calendar_ref"] = descriptor.calendar_ref
                    worker_params["calendar_version"] = descriptor.calendar_version
                    worker_params["calendar_required"] = True
                except Exception as exc:
                    code = _calendar_error_code(exc)
                    _JOB_SPEC_REGISTRY.mark_unavailable(source_ref, code)
                    _bt2_write_state(jid, status="dead", error=code, error_code=code,
                                     source_ref=source_ref, source_availability="source_unavailable",
                                     source_error=code, transaction_policy=_minute_transaction_policy(descriptor, spec, worker_params),
                                     fills=[], cash=float(params.get("initial_cash", 0.0) or 0.0), positions={}, inventory_lots=[])
                    return
            try:
                worker_params["execution_config"] = _minute_execution_config(worker_params, descriptor, spec)
            except Exception as exc:
                code = _minute_error_code(exc)
                _bt2_write_state(jid, status="dead", error=code, error_code=code,
                                 source_ref=source_ref, source_availability="available",
                                 source_error=code, transaction_policy=_minute_transaction_policy(descriptor, spec, worker_params),
                                 fills=[], cash=float(params.get("initial_cash", 0.0) or 0.0), positions={}, inventory_lots=[])
                return

        policy = worker_params.get("transaction_policy")
        if policy is None and params.get("execution_mode") == "minute_store":
            policy = _minute_transaction_policy(descriptor, spec, worker_params)
        if policy is not None:
            worker_params["transaction_policy"] = policy
        res, err = eng.run_job(worker_params, jid, progress=progress)
        if err:
            str(err)
            stable = _minute_error_code(err)
            _bt2_write_state(jid, status="dead", error=stable, error_code=stable, source_error=stable,
                             source_ref=params.get("source_ref"), source_availability="available",
                             transaction_policy=policy or {}, fills=[], cash=float(params.get("initial_cash", 0.0) or 0.0),
                             positions={}, inventory_lots=[])
        else:
            if params.get("execution_mode") == "minute_store":
                try:
                    artifact = _safe_minute_artifact(res.get("artifact"), jid, eng.MINUTE_OUT_DIR)
                    if artifact is None:
                        raise ValueError("unsafe minute artifact path")
                    persisted, artifact_bytes, artifact_sha256 = _read_minute_artifact(artifact, jid)
                except (AttributeError, OSError, UnicodeError, ValueError, TypeError):
                    _bt2_write_state(jid, status="dead", error="ARTIFACT_UNAVAILABLE", error_code="ARTIFACT_UNAVAILABLE",
                                     source_error="ARTIFACT_UNAVAILABLE", transaction_policy=policy or {}, fills=[],
                                     cash=float(params.get("initial_cash", 0.0) or 0.0), positions={}, inventory_lots=[])
                    return
                try:
                    _bt2_write_state(
                        jid,
                        status="done",
                        pct=100,
                        stage="完成",
                        schema=persisted["schema"],
                        metrics=persisted["metrics"],
                        transaction_policy=policy or {},
                        error_code=None,
                        source_error=None,
                        result_file=artifact,
                        artifact_bytes=artifact_bytes,
                        artifact_sha256=artifact_sha256,
                    )
                except Exception:
                    # A failed done commit is not a successful job. The previous
                    # running state remains complete if even this retry fails.
                    _bt2_write_state(jid, status="dead", error="ARTIFACT_UNAVAILABLE",
                                     error_code="ARTIFACT_UNAVAILABLE", source_error="ARTIFACT_UNAVAILABLE")
            else:
                _bt2_write_state(
                    jid,
                    status="done",
                    pct=100,
                    stage="完成",
                    result=res["summary"],
                    result_file=os.path.join(_BT2_DIR, "bt2_%s.json" % jid),
                )
    except Exception as _e:
        log_exc(_e)
        code = _minute_error_code(_e)
        source_ref = params.get("source_ref") if isinstance(params, dict) else None
        descriptor = _JOB_SPEC_REGISTRY.get(source_ref) if source_ref else None
        policy = _minute_transaction_policy(descriptor, None, params) if descriptor is not None else (params.get("transaction_policy", {}) if isinstance(params, dict) else {})
        try:
            initial_cash = float(params.get("initial_cash", 0.0) or 0.0)
        except (TypeError, ValueError):
            initial_cash = 0.0
        _bt2_write_state(jid, status="dead", error=code, error_code=code, source_error=code,
                         source_ref=source_ref, source_availability="available",
                         transaction_policy=policy, fills=[], cash=initial_cash,
                         positions={}, inventory_lots=[])
    finally:
        _BT2_LOCK.release()


def _bt2_nl(rec):
    """POST /api/bt2_nl（K-STOCK-299）：自然语言 → 回测参数（只解析不执行）。
    返回 params 交前端回填表单，老田核对后走既有 /api/bt2_run（那边再做数值校验）——
    解析与执行分离，不黑箱开跑。"""
    text = str(rec.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "text 为空"}
    use_llm = rec.get("use_llm") is not False
    qbt = os.path.join(STATION, "quant_backtest")
    if qbt not in sys.path:
        sys.path.insert(0, qbt)
    import nl_backtest
    return nl_backtest.parse_nl(text, use_llm=use_llm)


def _bt2_status():
    """GET /api/bt2_jobs：最近 20 个任务（state 合并：进度/结果/错误）。
    K-STOCK-345 修复：bt2_state 目录混有 sig_events_*.json 事件缓存与 log_*.txt
    跑批日志（67 条目），旧逻辑 sorted(listdir)[:20] 按文件名倒序，'s'/'l' 前缀
    永远排在 'bt2_' 之前 ⇒ 前 20 名被日志/缓存占满、任务列表恒为空（前端轮询
    失明）。修复=先过滤 .state.json 再排序截断。"""
    out = []
    if os.path.isdir(_BT2_STATE_DIR):
        cands = [fn for fn in os.listdir(_BT2_STATE_DIR) if fn.endswith(".state.json")]
        for fn in sorted(cands, reverse=True)[:20]:
            if not fn.endswith(".state.json"):
                continue
            st = _read_json(os.path.join(_BT2_STATE_DIR, fn))
            if isinstance(st, dict) and st.get("id"):
                params = st.get("params") if isinstance(st.get("params"), dict) else {}
                source_ref = params.get("source_ref")
                descriptor = _JOB_SPEC_REGISTRY.get(source_ref) if source_ref else None
                if descriptor:
                    st["source_availability"] = descriptor.availability
                    st["source_error"] = st.get("source_error") or descriptor.error
                    st["source_descriptor"] = descriptor.as_dict()
                    st["calendar_ref"] = descriptor.calendar_ref
                    st["calendar_version"] = descriptor.calendar_version
                    st["transaction_policy"] = st.get("transaction_policy") or _minute_transaction_policy(descriptor)
                    if st.get("error_code") is None and st.get("source_error"):
                        st["error_code"] = st["source_error"]
                elif params.get("execution_mode") == "minute_store":
                    st["source_availability"] = "source_unavailable"
                    st["source_error"] = "SOURCE_UNAVAILABLE"
                out.append(st)
    return {"ok": True, "total": len(out), "jobs": out}


_MINUTE_ARTIFACT_MAX_BYTES = 16 * 1024 * 1024


def _read_minute_artifact(path, jid, expected_bytes=None, expected_sha256=None):
    """Read one bounded minute result, optionally checking write-time state evidence."""
    if expected_bytes is not None:
        if (type(expected_bytes) is not int or not 0 < expected_bytes <= _MINUTE_ARTIFACT_MAX_BYTES
                or not isinstance(expected_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None):
            raise ValueError("untrusted minute artifact evidence")
    with open(path, "rb") as artifact_file:
        artifact_bytes = artifact_file.read(_MINUTE_ARTIFACT_MAX_BYTES + 1)
    if not artifact_bytes or len(artifact_bytes) > _MINUTE_ARTIFACT_MAX_BYTES:
        raise ValueError("minute artifact size out of bounds")
    digest = hashlib.sha256(artifact_bytes).hexdigest()
    if expected_bytes is not None and (len(artifact_bytes) != expected_bytes
                                       or digest != expected_sha256):
        raise ValueError("minute artifact differs from write-time evidence")
    payload = json.loads(artifact_bytes)
    if not _valid_minute_payload(payload, jid):
        raise ValueError("invalid minute artifact payload")
    return payload, len(artifact_bytes), digest


def _minute_artifact_unavailable():
    """Return the stable public failure for an invalid minute artifact."""
    return {"ok": False, "error": "ARTIFACT_UNAVAILABLE",
            "error_code": "ARTIFACT_UNAVAILABLE",
            "source_error": "ARTIFACT_UNAVAILABLE"}


def _valid_minute_payload(payload, jid):
    """Check the engine's persisted minute result contract before projection."""
    if not isinstance(payload, dict):
        return False
    if payload.get("schema") != "minute-exec-v1" or payload.get("job_id") != jid:
        return False
    for key in ("fills", "audits", "orders"):
        if not isinstance(payload.get(key), list):
            return False
    for key in ("positions", "metrics", "source_metadata", "query_metadata"):
        if not isinstance(payload.get(key), dict):
            return False
    for key in ("cash", "equity"):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            if not math.isfinite(value):
                return False
        except (OverflowError, ValueError):
            return False
    return True


def _bt2_result(jid):
    """GET /api/bt2_job?id=: project state before reading a done artifact."""
    if not re.fullmatch(r"[0-9A-Za-z\-_]{4,64}", str(jid or "")):
        return {"ok": False, "error": "bad id"}
    state = _read_json(_bt2_state_path(jid))
    if not isinstance(state, dict) or not state:
        return {"ok": False, "error": "job not found"}
    raw_params = state.get("params", {})
    if not isinstance(raw_params, dict) and state.get("status") == "done":
        return _minute_artifact_unavailable()
    params = raw_params if isinstance(raw_params, dict) else {}
    mode = params.get("execution_mode", "daily_legacy")
    if state.get("status") == "done":
        # The top-level mode is written when a job is queued and survives later
        # params corruption. Older minute jobs also carry a minute schema/path.
        # Never route an identifiable minute artifact through the daily reader.
        result_file = state.get("result_file")
        minute_identity = (
            state.get("execution_mode") == "minute_store"
            or state.get("schema") == "minute-exec-v1"
            or (isinstance(result_file, str)
                and os.path.basename(result_file) == "minute_%s.json" % jid)
        )
        if ((minute_identity and mode != "minute_store")
                or (mode == "minute_store" and state.get("execution_mode") == "daily_legacy")):
            return _minute_artifact_unavailable()
    source_ref = state.get("source_ref") or params.get("source_ref")
    descriptor = _JOB_SPEC_REGISTRY.get(source_ref) if source_ref else None
    projected = {
        "source_ref": source_ref,
        "source_availability": descriptor.availability if descriptor else state.get("source_availability"),
        "source_error": state.get("source_error") or (descriptor.error if descriptor else None),
        "calendar_ref": descriptor.calendar_ref if descriptor else state.get("calendar_ref"),
        "calendar_version": descriptor.calendar_version if descriptor else state.get("calendar_version"),
        "source_descriptor": descriptor.as_dict() if descriptor else None,
        "error_code": state.get("error_code"),
        "transaction_policy": state.get("transaction_policy") or (_minute_transaction_policy(descriptor) if descriptor else {}),
    }
    if projected["error_code"] is None and projected["source_error"]:
        projected["error_code"] = projected["source_error"]
    if state.get("status") != "done":
        if projected["source_availability"] == "source_unavailable" or state.get("error_code") == "SOURCE_UNAVAILABLE":
            projected["ok"] = False
            projected["error"] = projected["source_error"] or "SOURCE_UNAVAILABLE"
            projected["error_code"] = "SOURCE_UNAVAILABLE"
            return projected
        return {**projected, "ok": False, "error": state.get("error", "job not completed")}
    has_result_file = bool(isinstance(state, dict) and state.get("result_file"))
    if has_result_file:
        candidate = str(state.get("result_file"))
        if mode == "minute_store":
            fp = _safe_minute_artifact(candidate, jid, _minute_output_root()) or ""
        elif os.path.isfile(candidate):
            fp = candidate
        else:
            fp = ""
    else:
        fp = ""
    if mode == "minute_store" and has_result_file and not fp:
        return _minute_artifact_unavailable()
    if not fp:
        if mode == "minute_store":
            fp = os.path.join(_minute_output_root(), "minute_%s.json" % jid)
        else:
            fp = os.path.join(_BT2_DIR, "bt2_%s.json" % jid)
    if not os.path.exists(fp):
        if mode == "minute_store":
            return _minute_artifact_unavailable()
        return {"ok": False, "error": "result not found"}
    if mode == "minute_store":
        safe_fp = _safe_minute_artifact(fp, jid, _minute_output_root())
        if safe_fp is None:
            return _minute_artifact_unavailable()
        # Older done states without write-time evidence are intentionally rejected.
        if (type(state.get("artifact_bytes")) is not int
                or not 0 < state["artifact_bytes"] <= _MINUTE_ARTIFACT_MAX_BYTES
                or not isinstance(state.get("artifact_sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", state["artifact_sha256"]) is None):
            return _minute_artifact_unavailable()
        try:
            payload, _, artifact_sha256 = _read_minute_artifact(
                safe_fp, jid, state["artifact_bytes"], state["artifact_sha256"])
        except (OSError, UnicodeError, ValueError):
            return _minute_artifact_unavailable()
    else:
        with open(fp, encoding="utf-8") as f:
            payload = json.load(f)
    payload["ok"] = True
    if mode == "minute_store":
        # Digest of bytes read, not an independently trusted release signature.
        payload["artifact_sha256"] = artifact_sha256
    payload.update({key: value for key, value in projected.items() if value is not None})
    if isinstance(state, dict):
        params = state.get("params") if isinstance(state.get("params"), dict) else {}
        source_ref = params.get("source_ref")
        descriptor = _JOB_SPEC_REGISTRY.get(source_ref) if source_ref else None
        if descriptor:
            payload["source_availability"] = descriptor.availability
            payload["source_error"] = descriptor.error
            payload["source_descriptor"] = descriptor.as_dict()
        elif params.get("execution_mode") == "minute_store":
            payload["source_availability"] = "source_unavailable"
            payload["source_error"] = "SOURCE_UNAVAILABLE"
    return payload
