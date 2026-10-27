"""每日数据仓接口；仅接受服务端冻结计划，不整体导入门户或启动采集。"""

import copy
from datetime import date
import json
import math
import re
import threading
from urllib.parse import urlsplit
import uuid


MAX_BODY = 65536
SERVICES = {
    "store",
    "planner",
    "registry",
    "launcher",
    "daily_view",
    "process_probe",
    "clock",
}
ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
RUN_ID = re.compile(r"^[a-f0-9]{32}$")
TABLE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
SECRET = re.compile(r"password|passwd|secret|token|authorization|cookie|api_key", re.I)


class PlanConflict(ValueError):
    """计划过期或基线改变，需要重新预览。"""


class PlanService:
    """本进程五分钟计划缓存；重启后旧计划失效，不接受客户端计划正文。"""

    def __init__(
        self,
        preview,
        validate,
        *,
        allowed_hosts=("127.0.0.1", "localhost", "::1"),
        allowed_ports=(80, 8080, 8091),
    ):
        self.preview_callback, self.validate_callback = preview, validate
        self.allowed_hosts, self.allowed_ports = (
            frozenset(allowed_hosts),
            frozenset(allowed_ports),
        )
        self._plans, self._lock = {}, threading.Lock()

    def preview(self, request, *, now):
        plan = self.preview_callback(copy.deepcopy(request), now=now)
        if not isinstance(plan, dict) or not isinstance(plan.get("plan_digest"), str):
            raise ValueError("计划生成器未返回冻结计划")
        plan_id = uuid.uuid4().hex
        with self._lock:
            self._plans = {
                key: item for key, item in self._plans.items() if now <= item[0] + 300
            }
            if len(self._plans) >= 1000:
                raise PlanConflict("待确认计划过多，请稍后重试")
            self._plans[plan_id] = (now, copy.deepcopy(plan))
        return {
            "plan_id": plan_id,
            "expires_at": now + 300,
            "plan": copy.deepcopy(plan),
        }

    def load(self, plan_id, digest, *, now):
        with self._lock:
            item = self._plans.get(plan_id)
            if item is None or not item[0] <= now <= item[0] + 300:
                raise PlanConflict("计划不存在或已过期，请重新预览")
            plan = copy.deepcopy(item[1])
        if plan["plan_digest"] != digest:
            raise PlanConflict("计划摘要不匹配")
        try:
            self.validate_callback(copy.deepcopy(plan), now=now)
        except (ValueError, RuntimeError) as exc:
            raise PlanConflict(str(exc)) from None
        return plan


def safe(value):
    """自由文本出现认证标签时整段隐藏，避免保留Basic、Cookie或JSON密钥。"""
    if isinstance(value, dict):
        return {
            key: safe(item)
            for key, item in value.items()
            if not SECRET.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [safe(item) for item in value]
    if isinstance(value, str):
        if SECRET.search(value) or re.search(
            r"(?i)\b(?:basic|digest|bearer)\s+", value
        ):
            return "[认证信息已隐藏]"
        return re.sub(r"https?://[^\s]+", "[URL已隐藏]", value)
    return value


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("日期格式须为YYYY-MM-DD")
    date.fromisoformat(value)
    return value


def _one(query, key, default=None):
    value = query.get(key, default)
    if isinstance(value, list):
        if len(value) != 1:
            raise ValueError("查询参数不能重复")
        value = value[0]
    return value


def _integer(value, *, minimum, maximum):
    if (
        isinstance(value, bool)
        or not isinstance(value, (str, int))
        or not re.fullmatch(r"\d+", str(value))
    ):
        raise ValueError("分页参数不合法")
    value = int(value)
    if not minimum <= value <= maximum:
        raise ValueError("分页范围不合法")
    return value


def _same_origin(origin, host):
    if origin is None:
        return True
    if not isinstance(origin, str) or not isinstance(host, str):
        return False
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.netloc == host
        and parsed.username is None
        and parsed.password is None
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )


def _trusted_host(host, planner):
    try:
        parsed = urlsplit("//" + host)
        return (
            parsed.hostname in planner.allowed_hosts
            and (parsed.port or 80) in planner.allowed_ports
            and parsed.username is None
            and parsed.password is None
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
        )
    except (AttributeError, ValueError, TypeError):
        return False


def _request_plan(body, services):
    allowed = {"source", "market", "date", "selected_tables", "retry_of", "retry_units"}
    if set(body) - allowed or not {"source", "market", "date"} <= body.keys():
        raise ValueError("计划请求字段不合法")
    request = dict(selected_tables=None, retry_of=None, retry_units=[], **{})
    request.update(body)
    source, market = request["source"], request["market"]
    if (
        source not in {"tdx", "tushare"}
        or market not in {"all", "cn", "hk", "us"}
        or source == "tdx"
        and market not in {"all", "cn"}
    ):
        raise ValueError("来源或市场不合法")
    _date(request["date"])
    registry = (
        services["registry"]()
        if callable(services["registry"])
        else services["registry"]
    )
    tables = {
        table
        for row in registry.get("physical_tables", [])
        for table in row["physical_tables"]
    }
    selected = request["selected_tables"]
    if selected is not None and (
        not isinstance(selected, list)
        or not selected
        or len(set(selected)) != len(selected)
        or any(
            not isinstance(t, str) or not TABLE.fullmatch(t) or t not in tables
            for t in selected
        )
    ):
        raise ValueError("表选择不合法")
    retry = request["retry_of"]
    units = request["retry_units"]
    if not isinstance(units, list) or any(
        not isinstance(u, str) or not ID.fullmatch(u) for u in units
    ):
        raise ValueError("重试单元不合法")
    if retry is not None:
        if not isinstance(retry, str) or not RUN_ID.fullmatch(retry) or not units:
            raise ValueError("重试任务不合法")
        try:
            original = services["store"].get_run(retry)
        except KeyError:
            raise ValueError("原重试任务不存在") from None
        request["retry_of"] = original
    elif units:
        raise ValueError("重试单元必须关联原任务")
    return request


def _run_response(run, *, started=False):
    return dict(
        run_id=run["run_id"],
        status=run["status"],
        pid=run["pid"],
        started=started,
        running=run["status"] in {"queued", "running", "verifying"},
        exit=run["exit_code"],
        error=None,
        tail="",
        process_status=run["process_status"],
    )


def handle_request(method, path, query, body, *, origin, host, authenticated, services):
    """返回(status,JSON)；GET不调用launcher，所有副作用由注入服务执行。"""
    if not authenticated:
        return 403, {"error": "禁止访问：认证或同源校验失败"}
    if not isinstance(services, dict) or set(services) != SERVICES:
        return 503, {"error": "每日服务未就绪"}
    if (
        not _trusted_host(host, services["planner"])
        or method == "POST"
        and not _same_origin(origin, host)
    ):
        return 403, {"error": "禁止访问：Host或同源校验失败"}
    try:
        now = services["clock"]()
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(now)
        ):
            raise ValueError("时钟不合法")
        store = services["store"]
        if method == "GET":
            store.reconcile(services["process_probe"], now=now)
            if path == "/api/data/daily":
                selected = _date(_one(query, "date"))
                runs = store.list_runs(selected)
                return 200, safe(services["daily_view"](selected, runs))
            if path.startswith("/api/data/runs/"):
                run_id = path.removeprefix("/api/data/runs/")
                if not RUN_ID.fullmatch(run_id):
                    raise ValueError("任务标识不合法")
                after = _integer(
                    _one(query, "after_seq", "0"), minimum=0, maximum=2**63 - 1
                )
                limit = _integer(_one(query, "limit", "100"), minimum=1, maximum=500)
                run = store.get_run(run_id, after_seq=after, limit=limit)
                if "log_offset" in query:
                    offset = _integer(
                        _one(query, "log_offset"), minimum=0, maximum=2**63 - 1
                    )
                    size = _integer(
                        _one(query, "log_limit", "4096"), minimum=1, maximum=65536
                    )
                    logger = getattr(services["launcher"], "read_log", None)
                    run["stdout"] = (
                        logger(run_id, offset=offset, limit=size)
                        if logger
                        else {
                            "available": False,
                            "association": "unavailable",
                            "text": "",
                            "next_offset": offset,
                            "has_more": False,
                        }
                    )
                return 200, safe(run)
            return 404, {"error": "接口不存在"}
        if method != "POST" or path not in {
            "/api/data/update-plan",
            "/api/data/update",
        }:
            return 405, {"error": "请求方法不支持"}
        if not isinstance(body, bytes):
            raise ValueError("仅接受JSON字节正文")
        if len(body) > MAX_BODY:
            return 413, {"error": "请求正文超过64KiB"}
        request = json.loads(body)
        if not isinstance(request, dict):
            raise ValueError("正文必须为JSON对象")
        planner = services["planner"]
        if path == "/api/data/update-plan":
            request = _request_plan(request, services)
            return 200, safe(planner.preview(request, now=now))
        if set(request) != {"plan_id", "plan_digest", "request_id"} or any(
            not isinstance(v, str) or not ID.fullmatch(v) for v in request.values()
        ):
            raise ValueError("启动请求只接受计划ID、摘要与幂等标识")
        plan = planner.load(request["plan_id"], request["plan_digest"], now=now)
        run_id = store.create_run(
            plan, entry="manual", request_id=request["request_id"], now=now
        )
        if not store.reserve_launch(run_id, reservation_id=uuid.uuid4().hex, now=now):
            run = store.get_run(run_id)
            return (202 if run["status"] == "queued" else 200), _run_response(run)
        try:
            identity = services["launcher"](copy.deepcopy(plan), run_id, entry="manual")
        except Exception as exc:
            store.event(
                run_id,
                "error",
                {"stage": "launch", "error_kind": "launch_failed", "message": str(exc)},
                now=now,
            )
            store.finish(run_id, "failed", exit_code=None, now=now)
            result = _run_response(store.get_run(run_id))
            result["error"] = "采集进程启动失败"
            return 503, safe(result)
        try:
            if not isinstance(identity, dict) or identity.get("created_at") is None:
                raise ValueError("进程创建身份尚未核实")
            store.claim(
                run_id, pid=identity["pid"], created_at=identity["created_at"], now=now
            )
        except Exception:
            # Popen成功后不能假定它已死而释锁；子进程可通过共同入口绑定身份。
            run = store.get_run(run_id)
            result = _run_response(run, started=True)
            result["error"] = "进程身份待核实，已保留任务锁"
            return 202, safe(result)
        return 200, safe(_run_response(store.get_run(run_id), started=True))
    except PlanConflict as exc:
        return 409, {"error": safe(str(exc))}
    except KeyError:
        return 404, {"error": "任务或登记项不存在"}
    except (ValueError, TypeError, UnicodeError) as exc:
        return 400, {"error": safe(str(exc))}
    except Exception as exc:
        if type(exc).__name__ == "RunBusy":
            return 409, {"error": "同一数据仓已有任务，请先核对原任务"}
        return 503, {"error": "每日服务暂不可用，未确认启动结果"}
