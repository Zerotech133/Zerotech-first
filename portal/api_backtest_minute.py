# -*- coding: utf-8 -*-
"""api_backtest 拆分 B1（2026-10-30 P68）：minute 框架（注册表/校验/重放/产物）。

自 portal/api_backtest.py L5200-5638 纯移动；依赖单向：本模块不 import api_backtest。
"""
import datetime
import os
import re
import threading as _threading
from api_common import log_exc, STATION
from job_spec_registry import JobSpecDescriptor, JobSpecRegistry, utc_now_iso


# ── K-STOCK-285b 回测框架 v2（bt2 参数化回测：登记→daemon 线程跑引擎→进度落盘→轮询）──
# 动线：前端 POST /api/bt2_run（参数表单）→ 本模块校验+登记 → daemon 线程调
# quant_backtest/backtest_engine_v2.run_job（进度回调写 state json）→ 前端
# GET /api/bt2_jobs 轮询 → done 后 GET /api/bt2_job?id= 取全量账本渲染。
# 引擎跑在 portal 进程内 daemon 线程（books 只读并发安全；cal 全局窗口切换
# 互斥——全局锁串行，一期单并发足够）。冻结口径：工程验收动线，10-16 开闸
# 后接正式跑批（与 275 cbt 队列分开，互不影响）。

_CALENDAR_VERSION_PATTERN = re.compile(r"[0-9a-f]{64}")

_BT2_DIR = os.path.join(STATION, "quant_backtest", "data", "bt2")
_BT2_STATE_DIR = os.path.join(STATION, "quant_backtest", "data", "bt2_state")
_BT2_LOCK = _threading.Lock()
# Explicitly registered, process-local minute sources. JSON clients may only
# reference these opaque ids; arbitrary filesystem paths/connections are denied.
_MINUTE_SOURCE_REGISTRY = {}
_MINUTE_SOURCE_FACTORIES = {}
# Fee calculators/providers and fill models remain process-local; descriptors only
# carry opaque refs and immutable schedule identity metadata.
# Calendar providers are trusted, process-local runtime objects.  Only their
# opaque identity is persisted in JobSpec descriptors.
_MINUTE_CALENDAR_FACTORIES = {}
_MINUTE_FEE_FACTORIES = {}
_MINUTE_FILL_MODELS = {}
_MINUTE_MARKET_RULE_FACTORIES = {}
_MINUTE_MARKET_POLICIES = {
    "CN": {"timezone": "Asia/Shanghai", "t_plus_one": True, "policy_ref": "registry:market-cn", "lot_size": 100},
    "US": {"timezone": "America/New_York", "t_plus_one": False, "policy_ref": "registry:market-us"},
    "HK": {"timezone": "Asia/Hong_Kong", "t_plus_one": False, "policy_ref": "registry:market-hk"},
    "CRYPTO": {"timezone": "UTC", "t_plus_one": False, "policy_ref": "registry:market-crypto"},
}
_JOB_SPEC_CATALOG = os.path.join(STATION, "data", "runtime", "minute_job_specs.json")
_JOB_SPEC_REGISTRY = JobSpecRegistry(_JOB_SPEC_CATALOG)


def _calendar_error_code(exc):
    text = str(exc)
    if "CALENDAR_MISMATCH" in text:
        return "CALENDAR_MISMATCH"
    return "CALENDAR_UNAVAILABLE"


def _minute_error_code(exc):
    """Map worker failures to stable public codes without leaking exceptions."""
    text = str(exc or "")
    stable_codes = (
        "FEE_CONFIG_UNSUPPORTED", "FEE_SCHEDULE_CHANGED", "FEE_SCHEDULE_UNAVAILABLE",
        "CALENDAR_MISMATCH", "CALENDAR_UNAVAILABLE", "POLICY_MISMATCH",
        "PARTIAL_FILL_UNSUPPORTED", "STATUS_UNKNOWN", "LIMIT_UNKNOWN", "SOURCE_UNAVAILABLE", "ARTIFACT_UNAVAILABLE",
        "INVALID_TIF_CONFIG", "FEE_MODE_INVALID", "FEE_CONFIG_CONFLICT",
    )
    for code in stable_codes:
        if code in text:
            return code
    if isinstance(exc, (ValueError, TypeError, OverflowError)):
        return "INVALID_ARGUMENT"
    return "EXECUTION_ERROR"


def _descriptor_failure(descriptor):
    error = str(getattr(descriptor, "error", "") or "")
    if error in {"CALENDAR_UNAVAILABLE", "CALENDAR_MISMATCH", "FEE_CONFIG_UNSUPPORTED", "FEE_SCHEDULE_UNAVAILABLE", "FEE_SCHEDULE_CHANGED"}:
        return error
    return "SOURCE_UNAVAILABLE"


def _minute_transaction_policy(descriptor, spec=None, params=None):
    """Return a JSON-safe canonical transaction policy projection.

    Runtime callables remain in ``spec`` only; this projection is safe to persist
    in job state/artifacts and is stable across start/worker/result layers.
    """
    runtime = spec if isinstance(spec, dict) else {}
    request = params if isinstance(params, dict) else {}
    return {
        "fee_mode": str(getattr(descriptor, "fee_mode", request.get("fee_mode", "PER_FILL")) or "PER_FILL").upper(),
        "fee_schedule_ref": getattr(descriptor, "fee_schedule_ref", None),
        "fee_schedule_version": getattr(descriptor, "fee_schedule_version", None),
        "fee_schedule_hash": getattr(descriptor, "fee_schedule_hash", None),
        "fee_provider_ref": getattr(descriptor, "fee_provider_ref", None),
        "fee_calculator_ref": getattr(descriptor, "fee_calculator_ref", None),
        "time_in_force": str(getattr(descriptor, "time_in_force", request.get("time_in_force", "DAY")) or "DAY").upper(),
        "max_attempts": getattr(descriptor, "max_attempts", request.get("max_attempts")),
        "expire_at": getattr(descriptor, "expire_at", request.get("expire_at")),
        "partial_fill_params": getattr(descriptor, "partial_fill_params", request.get("partial_fill_params")),
        "market_rule_ref": getattr(descriptor, "market_rule_ref", request.get("market_rule_ref")),
        "market_rule_version": getattr(descriptor, "market_rule_version", request.get("market_rule_version")),
        "calendar_ref": getattr(descriptor, "calendar_ref", request.get("calendar_ref")),
        "calendar_version": getattr(descriptor, "calendar_version", request.get("calendar_version")),
        "calendar_required": bool(getattr(descriptor, "calendar_ref", None)),
        "market_rules_required": runtime.get("market_rule_provider") is not None,
    }


def _validate_minute_order_policy(descriptor, spec):
    """Validate the entire runtime batch against the persisted execution policy.

    No intent or descriptor is rewritten. Partial-fill descriptors are unsupported
    until a public, identity-bound fill-model registration contract exists.
    """
    from execution_engine import OrderIntent

    tif = getattr(descriptor, "time_in_force", None)
    attempts = getattr(descriptor, "max_attempts", None)
    expiry = getattr(descriptor, "expire_at", None)
    if tif not in ("DAY", "GTC"):
        raise ValueError("INVALID_TIF_CONFIG: descriptor time_in_force")
    if attempts is not None and (type(attempts) is not int or attempts <= 0):
        raise ValueError("INVALID_TIF_CONFIG: max_attempts")

    def utc_expiry(value):
        if value is None:
            return None
        if isinstance(value, str):
            try:
                parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("INVALID_TIF_CONFIG: expire_at") from exc
        elif isinstance(value, datetime.datetime):
            parsed = value
        else:
            raise ValueError("INVALID_TIF_CONFIG: expire_at")
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("INVALID_TIF_CONFIG: expire_at must be timezone-aware")
        return parsed.astimezone(datetime.timezone.utc)

    descriptor_expiry = utc_expiry(expiry)
    if tif == "DAY" and (attempts is not None or expiry is not None):
        raise ValueError("INVALID_TIF_CONFIG: DAY cannot retry or expire")
    if not isinstance(spec, dict):
        raise ValueError("POLICY_MISMATCH: runtime source spec missing")
    intents = spec.get("order_intents")
    if not isinstance(intents, (tuple, list)) or not intents:
        raise ValueError("POLICY_MISMATCH: order batch missing")
    for intent in intents:
        if not isinstance(intent, OrderIntent):
            raise ValueError("POLICY_MISMATCH: batch contains non-OrderIntent")
        intent_tif = intent.time_in_force
        intent_attempts = intent.max_attempts
        if intent_tif not in ("DAY", "GTC"):
            raise ValueError("INVALID_TIF_CONFIG: intent time_in_force")
        if intent_attempts is not None and (type(intent_attempts) is not int or intent_attempts <= 0):
            raise ValueError("INVALID_TIF_CONFIG: intent max_attempts")
        intent_expiry = utc_expiry(intent.expire_at)
        if intent_tif == "DAY" and (intent_attempts is not None or intent.expire_at is not None):
            raise ValueError("INVALID_TIF_CONFIG: DAY intent cannot retry or expire")
        if (intent_tif != tif or intent_attempts != attempts or
                intent_expiry != descriptor_expiry):
            raise ValueError("POLICY_MISMATCH: runtime order policy differs")
    if descriptor.partial_fill_params is not None:
        raise ValueError("PARTIAL_FILL_UNSUPPORTED: no identity-bound fill model")


def _minute_execution_config(params, descriptor, spec):
    """Construct one complete execution config for every worker path."""
    from execution_engine import ExecutionConfig
    fee_mode = str(getattr(descriptor, "fee_mode", params.get("fee_mode", "PER_FILL")) or "PER_FILL").upper()
    fee_provider = spec.get("fee_provider")
    fee_calculator = spec.get("fee_calculator")
    if fee_mode == "PER_ORDER" and fee_provider is None and fee_calculator is None:
        raise ValueError("FEE_CONFIG_UNSUPPORTED")
    return ExecutionConfig(
        initial_cash=float(params.get("initial_cash", 0.0) or 0.0),
        fee_bps=(0.0 if fee_mode == "PER_ORDER" else float(params.get("fee_bps", 0.0) or 0.0)),
        slippage_bps=float(params.get("slippage_bps", 0.0) or 0.0),
        lot_size=int(params.get("lot_size", 1) or 1),
        market=params.get("market"), timeframe=params.get("timeframe"),
        t_plus_one=params.get("t_plus_one", False),
        policy_ref=params.get("policy_ref"),
        fee_mode=fee_mode, fee_provider=fee_provider, fee_calculator=fee_calculator,
        calendar_ref=getattr(descriptor, "calendar_ref", None),
        calendar_version=getattr(descriptor, "calendar_version", None),
        calendar_required=bool(getattr(descriptor, "calendar_ref", None)),
        market_rules_required=spec.get("market_rule_provider") is not None,
        fill_model=spec.get("fill_model"),
    )


def _minute_output_root():
    """Resolve the engine-owned minute output directory at request time."""
    try:
        import backtest_engine_v2 as engine
        return str(engine.MINUTE_OUT_DIR)
    except Exception:
        return os.path.join(STATION, "quant_backtest", "data", "minute_exec")


def _safe_minute_artifact(path, job_id, root):
    """Return an artifact only when its real path stays under the output root."""
    expected = "minute_%s.json" % str(job_id)
    root_real = os.path.realpath(str(root))
    candidate = os.path.realpath(str(path or os.path.join(root_real, expected)))
    try:
        inside = os.path.commonpath([candidate, root_real]) == root_real
    except ValueError:
        inside = False
    if not inside or os.path.basename(candidate) != expected:
        return None
    return candidate


def _calendar_provider_for(source_ref, descriptor):
    factory = _MINUTE_CALENDAR_FACTORIES.get(str(source_ref))
    if factory is None:
        raise ValueError("CALENDAR_UNAVAILABLE: provider factory unavailable")
    provider = factory(descriptor)
    if provider is None:
        raise ValueError("CALENDAR_UNAVAILABLE: provider unavailable")
    return provider


def _validate_runtime_calendar(source_ref, descriptor, spec):
    if not descriptor.calendar_ref:
        raise ValueError("CALENDAR_UNAVAILABLE: calendar identity missing")
    provider = spec.get("calendar_provider")
    if provider is None:
        provider = _calendar_provider_for(source_ref, descriptor)
        spec["calendar_provider"] = provider
    from calendar_provider import require_production_provider
    identity = require_production_provider(provider)
    if identity.calendar_ref != descriptor.calendar_ref or identity.version != descriptor.calendar_version:
        raise ValueError("CALENDAR_MISMATCH: calendar identity differs")
    store = spec.get("bar_store")
    if hasattr(store, "validate_calendar_identity"):
        store.validate_calendar_identity(identity.calendar_ref, identity.version)
    return provider


def _rehydrate_minute_source(source_ref):
    """Rebuild one process-local source through an explicitly registered factory."""
    ref = str(source_ref)
    descriptor = _JOB_SPEC_REGISTRY.get(ref)
    if descriptor is None:
        return False, "SOURCE_UNAVAILABLE"
    if _JOB_SPEC_REGISTRY.catalog_status == "invalid":
        return False, "SOURCE_UNAVAILABLE"
    try:
        # Unavailable descriptors may be retried after a trusted factory is
        # registered; expiry remains terminal until a new descriptor is saved.
        if _JOB_SPEC_REGISTRY.is_expired(ref):
            _JOB_SPEC_REGISTRY.mark_unavailable(ref, "SOURCE_UNAVAILABLE")
            return False, "SOURCE_UNAVAILABLE"
        factory = _MINUTE_SOURCE_FACTORIES.get(ref)
        if factory is None:
            _JOB_SPEC_REGISTRY.mark_unavailable(ref, "SOURCE_UNAVAILABLE")
            return False, "SOURCE_UNAVAILABLE"
        spec = factory(descriptor)
        if not isinstance(spec, dict):
            raise TypeError("factory must return source spec")
        store = spec.get("bar_store")
        bars = store.all() if hasattr(store, "all") else ()
        if bars:
            actual_market = str(getattr(bars[0], "market", "")).upper()
            actual_timeframe = str(getattr(bars[0], "timeframe", "")).lower()
            if actual_market != str(descriptor.market).upper() or actual_timeframe != str(descriptor.timeframe).lower():
                raise ValueError("SOURCE_UNAVAILABLE")
        _validate_minute_order_policy(descriptor, spec)
        if descriptor.calendar_ref:
            _validate_runtime_calendar(ref, descriptor, spec)
        _MINUTE_SOURCE_REGISTRY[ref] = spec
        _JOB_SPEC_REGISTRY.mark_available(ref)
        return True, None
    except Exception as exc:
        log_exc(exc)
        _MINUTE_SOURCE_REGISTRY.pop(ref, None)
        code = _minute_error_code(exc)
        if code not in {"POLICY_MISMATCH", "INVALID_TIF_CONFIG", "PARTIAL_FILL_UNSUPPORTED"}:
            code = _calendar_error_code(exc) if descriptor.calendar_ref else "SOURCE_UNAVAILABLE"
        _JOB_SPEC_REGISTRY.mark_unavailable(ref, code)
        return False, code


def _rehydrate_catalog_sources():
    """Attempt startup recovery; unavailable sources remain visible, never dropped."""
    for source_ref in _JOB_SPEC_REGISTRY.descriptors():
        _rehydrate_minute_source(source_ref)


# Startup recovery is intentionally conservative: no factory means unavailable.
_rehydrate_catalog_sources()


def register_minute_calendar_factory(source_ref, factory):
    """Bind a trusted calendar provider factory to a source (runtime only)."""
    ref = str(source_ref or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ref) or not callable(factory):
        raise ValueError("invalid calendar factory")
    _MINUTE_CALENDAR_FACTORIES[ref] = factory
    descriptor = _JOB_SPEC_REGISTRY.get(ref)
    if descriptor is None or not descriptor.calendar_ref:
        return False, "CALENDAR_UNAVAILABLE"
    try:
        spec = _MINUTE_SOURCE_REGISTRY.get(ref)
        if isinstance(spec, dict):
            try:
                _validate_minute_order_policy(descriptor, spec)
            except (ValueError, TypeError) as exc:
                code = _minute_error_code(exc)
                _MINUTE_SOURCE_REGISTRY.pop(ref, None)
                _JOB_SPEC_REGISTRY.mark_unavailable(ref, code)
                return False, code
            _validate_runtime_calendar(ref, descriptor, spec)
            _JOB_SPEC_REGISTRY.mark_available(ref)
            return True, None
        return _rehydrate_minute_source(ref)
    except Exception as exc:
        code = _calendar_error_code(exc)
        _JOB_SPEC_REGISTRY.mark_unavailable(ref, code)
        return False, code


def register_minute_factory(source_ref, factory):
    """Register a trusted in-process rehydration factory (never persisted)."""
    ref = str(source_ref or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ref) or not callable(factory):
        raise ValueError("invalid source factory")
    _MINUTE_SOURCE_FACTORIES[ref] = factory
    return _rehydrate_minute_source(ref)


def register_minute_source(source_ref, bar_store, order_intents=(),
                           status_provider=None, limit_provider=None,
                           market_rule_provider=None,
                           market="CN", timezone="Asia/Shanghai", timeframe="1m",
                           policy_ref=None, expires_at=None, calendar_ref=None,
                           calendar_version=None, calendar_provider=None,
                           calendar_factory=None, fee_mode="PER_FILL",
                           fee_calculator=None, fee_provider=None,
                           fee_provider_ref=None, fee_calculator_ref=None,
                           fee_schedule_ref=None, fee_schedule_version=None,
                           fee_schedule_hash=None, time_in_force="DAY",
                           max_attempts=None, expire_at=None,
                           partial_fill_params=None, market_rule_ref=None,
                           market_rule_version=None):
    """Register a complete server-side minute job spec under an opaque id."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(source_ref or "")):
        raise ValueError("invalid source_ref")
    from bar_store import BarStore
    if not isinstance(bar_store, BarStore):
        raise TypeError("bar_store must be BarStore")
    from execution_engine import OrderIntent
    intents = tuple(order_intents or ())
    if not all(isinstance(item, OrderIntent) for item in intents):
        raise TypeError("order_intents must contain OrderIntent")
    if status_provider is None or limit_provider is None:
        raise ValueError("status_provider and limit_provider are required")
    market = str(market or "CN").upper()
    policy = _MINUTE_MARKET_POLICIES.get(market)
    if policy is None:
        raise ValueError("INVALID_ARGUMENT: unsupported market policy")
    timezone = str(timezone or policy["timezone"])
    if timezone != policy["timezone"]:
        raise ValueError("INVALID_ARGUMENT: timezone conflicts with market policy")
    timeframe = str(timeframe or "1m").lower()
    if not re.fullmatch(r"[A-Za-z_]+(?:/[A-Za-z_]+)+", timezone):
        raise ValueError("invalid timezone")
    if not market or not timeframe:
        raise ValueError("market/timeframe required")
    if (calendar_ref is None) != (calendar_version is None):
        raise ValueError("CALENDAR_UNAVAILABLE: calendar_ref/calendar_version required together")
    if calendar_ref is not None and not re.fullmatch(r"registry:[A-Za-z0-9_-]{1,64}", str(calendar_ref)):
        raise ValueError("CALENDAR_UNAVAILABLE: invalid calendar_ref")
    if calendar_version is not None and not _CALENDAR_VERSION_PATTERN.fullmatch(str(calendar_version)):
        raise ValueError("CALENDAR_UNAVAILABLE: invalid calendar_version")
    fee_mode = str(fee_mode or "PER_FILL").upper()
    if fee_mode not in {"PER_FILL", "PER_ORDER"}:
        raise ValueError("FEE_MODE_INVALID")
    if fee_mode == "PER_ORDER" and fee_calculator is None and fee_provider is None:
        raise ValueError("FEE_CONFIG_UNSUPPORTED")
    if fee_calculator is not None and fee_provider is not None:
        raise ValueError("FEE_CONFIG_CONFLICT")
    if market_rule_provider is not None and not callable(getattr(market_rule_provider, "decide", None)):
        raise ValueError("INVALID_ARGUMENT: market_rule_provider must implement decide()")
    if time_in_force not in {"DAY", "GTC"}:
        raise ValueError("INVALID_TIF_CONFIG")
    if time_in_force == "DAY" and (max_attempts is not None or expire_at is not None):
        raise ValueError("INVALID_TIF_CONFIG")
    spec = {
        "bar_store": bar_store,
        "order_intents": intents,
        "status_provider": status_provider,
        "limit_provider": limit_provider,
        "market_rule_provider": market_rule_provider,
        "calendar_provider": calendar_provider,
        "fee_mode": fee_mode,
        "fee_calculator": fee_calculator,
        "fee_provider": fee_provider,
        "fill_model": _MINUTE_FILL_MODELS.get(str(source_ref)),
        "market_rule_ref": market_rule_ref,
        "market_rule_version": market_rule_version,
    }
    ref = str(source_ref)
    canonical_policy_ref = policy["policy_ref"]
    if policy_ref is not None and str(policy_ref) != canonical_policy_ref:
        raise ValueError("POLICY_MISMATCH")
    descriptor = JobSpecDescriptor(
        source_ref=ref, source_version="v1", source_kind="bar_store",
        market=market, timezone=timezone, timeframe=timeframe,
        store_config_ref=f"runtime:{ref}",
        intent_batch_ref=f"runtime:{ref}-intents",
        provider_policy_ref=str(policy_ref or policy["policy_ref"]),
        registered_at=utc_now_iso(), expires_at=expires_at,
        calendar_ref=str(calendar_ref) if calendar_ref is not None else None,
        calendar_version=str(calendar_version) if calendar_version is not None else None,
        fee_mode=fee_mode,
        fee_schedule_ref=str(fee_schedule_ref) if fee_schedule_ref is not None else None,
        fee_schedule_version=str(fee_schedule_version) if fee_schedule_version is not None else None,
        fee_schedule_hash=str(fee_schedule_hash) if fee_schedule_hash is not None else None,
        fee_provider_ref=(str(fee_provider_ref) if fee_provider_ref is not None else None),
        fee_calculator_ref=(str(fee_calculator_ref) if fee_calculator_ref is not None else None),
        time_in_force=str(time_in_force).upper(), max_attempts=max_attempts,
        expire_at=expire_at, partial_fill_params=partial_fill_params,
        market_rule_ref=market_rule_ref, market_rule_version=market_rule_version)
    _validate_minute_order_policy(descriptor, spec)
    if calendar_factory is not None and not callable(calendar_factory):
        raise ValueError("invalid calendar factory")
    if calendar_ref is not None:
        # Resolve only the provider/factory supplied with this registration.
        # A previously registered factory for the same ref cannot authorize it.
        if spec["calendar_provider"] is None:
            if calendar_factory is None:
                raise ValueError("CALENDAR_UNAVAILABLE: provider factory unavailable")
            spec["calendar_provider"] = calendar_factory(descriptor)
            if spec["calendar_provider"] is None:
                raise ValueError("CALENDAR_UNAVAILABLE: provider unavailable")
        _validate_runtime_calendar(ref, descriptor, spec)
        # Calendar factory/store callbacks are untrusted to preserve this
        # candidate batch; reject late mutation before persisting any identity.
        _validate_minute_order_policy(descriptor, spec)
    _JOB_SPEC_REGISTRY.register(descriptor)
    _MINUTE_SOURCE_REGISTRY[ref] = spec
    # The closure and calendar factory remain process-local; no callback can
    # mark a source available before all registration validations succeed.
    _MINUTE_SOURCE_FACTORIES[ref] = lambda _descriptor: dict(spec)
    if calendar_factory is not None:
        _MINUTE_CALENDAR_FACTORIES[ref] = calendar_factory
    return descriptor.as_dict()
