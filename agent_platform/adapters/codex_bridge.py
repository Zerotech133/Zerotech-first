# -*- coding: utf-8 -*-
"""军团 Codex bridge（M1 静态校验器 + M2 试点注册 + M3 子进程沙箱试跑）。

拍板（2026-10-18 老田）：①import 白名单制（黑名单追不上绕过手法）
②军团-M1/M2/M3 批准按序施工 ③首个试点策略 = SMA20+RSI14（codex_ 前缀）。

红线（设计稿 §1 平移）：单向通道（R1）/ Codex 零凭据（R2）/ 零执行权
（R3，策略只产建议）/ 插件只读 propose（R4）/ 注册权在 AP 侧（R5）。

流水线（§3）：Codex 提交物 → 静态校验（AST，M1 + H6 属性链禁）→ 子进程沙箱
试跑（M3，§2.3）→ 宿主**隔离子进程代理**实例化 → StrategyRegistry.register
（同键异值=漂移拒绝平移）。拒绝路径 fail-visible 落档
H6（2026-11-01，审计 v2）：**主进程不再 exec 插件代码**——register_from_source
只在一次性子进程内执行/试跑插件，宿主拿到 Verdict dict 后由可信宿主代码构造
Verdict；propose() 每次也委托子进程。AST 属性链禁止表是**纵深防御，不是安全
边界**（真正的边界=插件代码只在隔离子进程跑；Windows Job Object/AppContainer
级隔离见架构建议单，本单未接入）。拒绝路径 fail-visible 落档
rejects/<YYYYMMDD>/<submit_id>.md（按日归档，§5 风险 6），
禁止静默丢弃。受理侧另设 submit_id 工单化按日限频（daily_limit，
registrations+rejects 同日合计口径），默认不限频向后兼容。

M3 口径：静态过关后在一次性子进程内以合成数据试跑 propose——运行时炸/
死循环/非法 Verdict 在注册前拦截；Windows 无 resource rlimit，死循环由
墙钟超时击杀（语义覆盖 CPU 上限场景），真 rlimit/Job Object 留 Linux
部署轮。试跑耗时基线随注册事件入档（registrations.jsonl.sandbox_ms）。

本模块自身纪律：零网络、除 rejects/processed/registrations 落档外零写；
H6（2026-11-01）后主进程零 exec 插件代码——插件只在一次性隔离子进程内
执行/试跑，宿主仅接收结果并构造 Verdict。AST 属性链禁/受限 globals 是
**纵深防御，不是安全边界**；真正的边界=插件跨进程隔离（Windows
Job Object/AppContainer 待独立架构单，本单未接入）。
"""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    from strategy_registry import (
        RECOMMENDATIONS,
        STANCES,
        StrategyAgent,
        StrategyError,
        StrategyRegistry,  # noqa: F401
        Verdict,  # noqa: F401
    )  # noqa: F401
except ImportError:  # 跨目录直接调用时兜底定位 engine/
    _here = Path(__file__).resolve()
    sys.path.insert(0, str(_here.parent.parent / "engine"))
    from strategy_registry import RECOMMENDATIONS, STANCES, StrategyAgent, StrategyError

# ---- 拍板①：import 白名单制（§2.2 建议 → 定稿） ----
# 七件套纯计算库 + 宿主契约模块 strategy_registry（插件基类/Verdict 来源）。
# 白名单外一律拒：黑名单永远追不上绕过手法（设计稿风险 §5.1）。
IMPORT_ALLOWLIST = frozenset(
    {
        "math",
        "statistics",
        "collections",
        "itertools",
        "functools",
        "typing",
        "dataclasses",
        "strategy_registry",  # 宿主契约模块（StrategyAgent/Verdict）
    }
)

# 危险字面调用（AST Call 扫 Name/Attribute）：命中即拒
DENY_CALLS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "system",
        "popen",
        "write_text",
        "write_bytes",  # attribute 尾名
    }
)

# H6（2026-11-01）：属性链/反射访问禁止表（AST 全扫 Attribute）。
# 审计 v2 实证：受限 globals 仍可 (1).__class__.__mro__[1].__subclasses__() 触达
# 316 子类 → 本表拦常见逃逸链。**仅纵深防御**：不是安全边界，真正边界是
# register_from_source/propose 不在主进程执行插件代码（见模块 docstring）。
DENY_ATTRS = frozenset({
    "__class__", "__mro__", "__bases__", "__base__", "__subclasses__",
    "__globals__", "__code__", "__builtins__", "__dict__",
    "__getattribute__", "__getattr__", "__setattr__", "__delattr__",
    "__reduce__", "__reduce_ex__", "__subclasshook__", "__init_subclass__",
    "__loader__", "__spec__", "__self__", "__func__", "__closure__",
    "__defaults__", "__kwdefaults__", "__weakref__",
})

# 凭据形态（R2）
CRED_PATTERN = re.compile(r"(?i)(api[_-]?key|secret|token|password|credential)\s*=")

MAX_LINES = 300
MAX_BYTES = 16 * 1024

# exec 沙箱 builtins：纯计算常用集 + 受限 __import__（双保险，静态门之外）
_SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "ZeroDivisionError": ZeroDivisionError,
    "ArithmeticError": ArithmeticError,
    "Exception": Exception,
    "__build_class__": __build_class__,  # class 语句依赖
    "__name__": "codex_plugin",  # 缺省模块名（exec globals 会覆盖）
}


def _safe_import(name, *args, **kwargs):
    """受限 __import__：仅放行白名单模块（exec 沙箱第二道防线）。"""
    top = name.split(".")[0]
    if top not in IMPORT_ALLOWLIST:
        raise ImportError("codex 插件仅允许白名单模块: %s" % name)
    return __builtins_import__(name, *args, **kwargs)


__builtins_import__ = __import__


def _exec_globals():
    g = dict(_SAFE_BUILTINS)
    g["__builtins__"] = dict(_SAFE_BUILTINS)
    g["__builtins__"]["__import__"] = _safe_import
    g["__name__"] = "codex_plugin"
    return g


# ---- M1 静态校验器 ----


def validate_source(source, max_lines=MAX_LINES, max_bytes=MAX_BYTES):
    """静态校验 Codex 提交物。返回拒绝原因列表（空=通过）。

    只读（AST 解析不执行任何插件代码），零网络零写。
    """
    reasons = []
    text_src = source if isinstance(source, str) else source.decode("utf-8", "replace")
    nlines = text_src.count("\n") + 1
    nbytes = len(text_src.encode("utf-8"))
    if nlines > max_lines:
        reasons.append("行数 %d 超上限 %d" % (nlines, max_lines))
    if nbytes > max_bytes:
        reasons.append("字节 %d 超上限 %d" % (nbytes, max_bytes))

    tree = None
    try:
        tree = ast.parse(text_src)
    except SyntaxError as e:
        reasons.append("语法错误：%s（line %d）" % (e.msg, e.lineno or -1))

    if tree is not None:
        # 1) import 白名单
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in IMPORT_ALLOWLIST:
                        reasons.append(
                            "import 白名单外模块：%s（line %d）"
                            % (alias.name, node.lineno)
                        )
            elif isinstance(node, ast.ImportFrom):
                top = (node.module or "").split(".")[0]
                if top not in IMPORT_ALLOWLIST:
                    reasons.append(
                        "from import 白名单外模块：%s（line %d）"
                        % (node.module, node.lineno)
                    )
        # 2) 危险字面调用
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name) and fn.id in DENY_CALLS:
                    reasons.append("禁用调用 %s()（line %d）" % (fn.id, node.lineno))
                elif isinstance(fn, ast.Attribute) and fn.attr in DENY_CALLS:
                    reasons.append("禁用方法 .%s()（line %d）" % (fn.attr, node.lineno))
        # 2b) H6：属性链/反射访问（全扫 Attribute；不依赖调用形态）
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in DENY_ATTRS:
                reasons.append(
                    "禁用属性链 .%s（line %d）：可疑沙箱逃逸/反射访问"
                    % (node.attr, node.lineno)
                )
            # 裸 dunder 名（如 __builtins__）也拒（不进 globals 命名空间）
            elif isinstance(node, ast.Name) and node.id in (
                    "__builtins__", "__loader__", "__spec__"):
                reasons.append(
                    "禁用 dunder 名称 %s（line %d）：可疑沙箱逃逸"
                    % (node.id, node.lineno)
                )
        # 3) 接口签名：恰好一个 StrategyAgent 子类
        subclasses = [
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef)
            and any(getattr(b, "id", None) == "StrategyAgent" for b in n.bases)
        ]
        if len(subclasses) != 1:
            reasons.append(
                "文件必须恰好定义一个 StrategyAgent 子类，实得 %d" % len(subclasses)
            )
        else:
            cls = subclasses[0]
            methods = {
                n.name
                for n in cls.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            assigns = {}
            for n in cls.body:
                val = None
                if (
                    isinstance(n, ast.Assign)
                    and len(n.targets) == 1
                    and isinstance(n.targets[0], ast.Name)
                ):
                    val = n.value
                elif (
                    isinstance(n, ast.AnnAssign)
                    and isinstance(n.target, ast.Name)
                    and n.value
                ):
                    val = n.value
                if val is not None:
                    tgt = n.target if isinstance(n, ast.AnnAssign) else n.targets[0]
                    assigns[tgt.id] = val
            if "__init__" in methods:
                reasons.append("禁止定义 __init__（插件须无状态，§2.2）")
            if "propose" not in methods:
                reasons.append("缺少 propose(self, context) 实现")
            sid = assigns.get("strategy_id")
            if not (
                isinstance(sid, ast.Constant)
                and isinstance(sid.value, str)
                and sid.value.startswith("codex_")
            ):
                reasons.append("strategy_id 必须为 codex_ 前缀字符串（审计来源隔离）")
            st = assigns.get("stance")
            if not (
                isinstance(st, ast.Constant) and st.value in ("bull", "bear", "neutral")
            ):
                reasons.append("stance 必须为 bull/bear/neutral 类属性")

    # 4) 凭据形态（对全文，R2）
    m = CRED_PATTERN.search(text_src)
    if m:
        reasons.append("命中凭据形态 %r（R2：Codex 零凭据）" % m.group(1))
    return reasons


SANDBOX_TIMEOUT_S = 10.0


def _static_plugin_identity(source):
    """从已通过静态校验的源码解析 strategy_id/stance（只读 AST，不执行）。"""
    text = source if isinstance(source, str) else source.decode("utf-8", "replace")
    module = ast.parse(text)
    for node in module.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(getattr(b, "id", None) == "StrategyAgent" for b in node.bases):
            continue
        sid = stance = None
        for item in node.body:
            val = tgt = None
            if (isinstance(item, ast.Assign) and len(item.targets) == 1
                    and isinstance(item.targets[0], ast.Name)):
                val, tgt = item.value, item.targets[0].id
            elif (isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
                  and item.value):
                val, tgt = item.value, item.target.id
            if tgt == "strategy_id" and isinstance(val, ast.Constant):
                sid = str(val.value)
            elif tgt == "stance" and isinstance(val, ast.Constant):
                stance = str(val.value)
        if sid and stance:
            return sid, stance
    raise StrategyError("插件身份解析失败：未找到 strategy_id/stance 类属性")


class _IsolatedPluginAgent(StrategyAgent):
    """宿主代理：**插件代码永不在主进程执行**。

    - strategy_id/stance 来自静态 AST（validate_source 已校验）；
    - propose(context) 委托一次性隔离子进程（sandbox_trial），宿主只接收
      经出口校验的 Verdict dict，再由可信宿主代码构造 Verdict；
    - 失败 fail-closed：子进程失败/超时/非法 Verdict → StrategyError。
    """

    def __init__(self, source, strategy_id, stance, timeout_s=SANDBOX_TIMEOUT_S):
        self._source = source
        self.strategy_id = strategy_id
        self.stance = stance
        self._timeout_s = float(timeout_s)

    def propose(self, context):
        result = sandbox_trial(self._source, context=context,
                               timeout_s=self._timeout_s)
        if not result.get("ok"):
            raise StrategyError("隔离试跑失败：%s" % result.get("reason"))
        v = dict(result.get("verdict") or {})
        try:
            return Verdict(
                strategy_id=str(v["strategy_id"]),
                symbol=str(v["symbol"]),
                recommendation=str(v["recommendation"]),
                confidence=float(v["confidence"]),
                stance=str(v.get("stance") or "neutral"),
                score=float(v.get("score") or 0.0),
                rationale=str(v.get("rationale") or ""),
                trace_id=str(v.get("trace_id") or ""),
                extras=dict(v.get("extras") or {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StrategyError("隔离结果无法转为 Verdict：%s" % exc) from exc


def register_from_source(source, registry, skip_trial=False):
    """校验通过后注册**隔离子进程代理**；主进程零 exec 插件代码。

    - 默认先在一次性子进程跑一次 propose 试跑（fail-closed：静态过但运行时
      炸/死循环/非法 Verdict → 拒绝注册）；调用方已自行试跑时可 skip_trial。
    - 漂移拒绝平移：同 strategy_id 二次注册 → StrategyRegistry 抛
      StrategyError（不覆盖、不静默替换，§2.4）。
    - 返回 _IsolatedPluginAgent（isinstance(StrategyAgent) 契约不变）。
    """
    reasons = validate_source(source)
    if reasons:
        raise StrategyError("静态校验拒绝：%s" % "; ".join(reasons))
    sid, stance = _static_plugin_identity(source)
    if not skip_trial:
        trial = sandbox_trial(source, timeout_s=SANDBOX_TIMEOUT_S)
        if not trial["ok"]:
            raise StrategyError("隔离试跑失败（拒绝注册）：%s" % trial["reason"])
    agent = _IsolatedPluginAgent(source, sid, stance)
    registry.register(agent)
    return agent


# ---- 军团-M3：子进程沙箱试跑（§2.3） ----

# 试跑合成 context：40 点匀速缓跌，覆盖「样本充足/趋势明确」路径；
# 插件应按自身口径产出合法 Verdict（含降级 hold）。
SANDBOX_TRIAL_CONTEXT = {
    "symbol": "TRIAL/USDT",
    "closes": [100.0 - 0.5 * i for i in range(40)],
}

_RESULT_MARK = "__BRIDGE_RESULT__"

# 独立子进程 runner：不 import codex_bridge，受限 globals 同表内嵌
# （宿主静态门 + 本进程受限 globals = 两道独立防线跨进程生效）。
# 协议：stdin 收 {"source", "context"} UTF-8 JSON；stdout 打
# "<_RESULT_MARK>{...}" 单标记行；BaseException 一律转标记行 fail-visible。
_SANDBOX_RUNNER = r"""
# -*- coding: utf-8 -*-
# 军团-M3 沙箱试跑 runner（独立子进程）。
import builtins as _b
import json
import sys

from strategy_registry import StrategyAgent, Verdict

_ALLOW = frozenset({
    "math", "statistics", "collections", "itertools", "functools",
    "typing", "dataclasses", "strategy_registry",
})
_real_import = _b.__import__


def _safe_import(name, *args, **kwargs):
    if name.split(".")[0] not in _ALLOW:
        raise ImportError("codex 插件仅允许白名单模块: %s" % name)
    return _real_import(name, *args, **kwargs)


_G = {n: getattr(_b, n) for n in (
    "abs all any bool dict divmod enumerate filter float int len list "
    "max min range reversed round set sorted str sum tuple zip "
    "ValueError TypeError KeyError IndexError ZeroDivisionError "
    "ArithmeticError Exception __build_class__").split()}
_G["__builtins__"] = dict(_G)
_G["__builtins__"]["__import__"] = _safe_import
_G["__name__"] = "codex_plugin"


def _emit(payload):
    # 2026-10-23 修：必须写 UTF-8 **字节**，不能走 sys.stdout.write。
    # Windows 上子进程 stdout 被重定向时默认编码是 cp936，而父进程
    # （sandbox_trial L365）按 utf-8 + errors="replace" 解码 —— 中文错误信息
    # 全部变成 U+FFFD，`非 Verdict` 这类断言随即失败（长期红的根因）。
    data = ("__BRIDGE_RESULT__" + json.dumps(payload, ensure_ascii=False) + "\n")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(data.encode("utf-8"))
        buffer.flush()
    else:  # 极端情形（stdout 被替换成非字节流），退回文本写
        sys.stdout.write(data)
        sys.stdout.flush()


def main():
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    code = compile(payload["source"], "<codex_plugin>", "exec")
    ns = dict(_G)
    exec(code, ns)
    classes = [v for v in ns.values()
               if isinstance(v, type) and issubclass(v, StrategyAgent)
               and v is not StrategyAgent]
    if len(classes) != 1:
        _emit({"ok": False,
               "error": "实例化后 StrategyAgent 子类数=%d（应恰为 1）"
                        % len(classes)})
        return 2
    verdict = classes[0]().propose(payload["context"])
    if not isinstance(verdict, Verdict):
        _emit({"ok": False,
               "error": "propose 返回 %r，非 Verdict"
                        % type(verdict).__name__})
        return 2
    verdict.validate()
    from dataclasses import asdict
    _emit({"ok": True, "verdict": asdict(verdict)})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001  转标记行，宿主 fail-visible
        _emit({"ok": False,
               "error": "%s: %s" % (type(e).__name__, str(e)[:300])})
        sys.exit(1)
""".strip()


def _verdict_dict_issues(v):
    """宿主侧出口再验（防御 runner 输出与契约脱钩的第二道网）。"""
    issues = []
    if not str(v.get("strategy_id", "")).startswith("codex_"):
        issues.append("strategy_id=%r 非 codex_ 前缀" % v.get("strategy_id"))
    if not v.get("symbol"):
        issues.append("symbol 缺失")
    if v.get("recommendation") not in RECOMMENDATIONS:
        issues.append("recommendation=%r 不在五档" % v.get("recommendation"))
    try:
        if not (0.0 <= float(v.get("confidence")) <= 1.0):
            issues.append("confidence=%r 越界 [0,1]" % v.get("confidence"))
    except (TypeError, ValueError):
        issues.append("confidence=%r 非数值" % v.get("confidence"))
    if v.get("stance") not in STANCES:
        issues.append("stance=%r 不在三档" % v.get("stance"))
    return issues


def _close_sandbox_job(job) -> None:
    if not job:
        return
    try:
        try:
            from legion_ide.exec_sandbox import close_job
        except ImportError:
            from agent_platform.legion_ide.exec_sandbox import close_job
        close_job(job)
    except Exception:  # noqa: BLE001 - 清理失败不覆盖主结果
        pass


def sandbox_trial(source, context=None, timeout_s=10.0):
    """M3：静态过关后在一次性子进程内受限试跑 propose。

    - 子进程 runner 复刻宿主受限 globals（白名单 builtins + 白名单
      __import__），静态门与运行时沙箱跨进程双防线。
    - H6（2026-11-01）：一次性子进程挂 Windows Job Object（内存/进程数/
      KILL_ON_JOB_CLOSE）；挂载失败 fail-closed，不执行未隔离的插件代码。
      这仍不是 AppContainer/网络/文件系统级隔离；AST 仍只是纵深防御。
    - fail-visible：超时/崩溃/无标记/非法 Verdict 一律 ok=False 且
      reason 可读；不抛裸异常。
    返回 {"ok", "reason", "elapsed_ms", "killed", "verdict", "isolation"}。
    """
    ctx = dict(context) if context else dict(SANDBOX_TRIAL_CONTEXT)
    payload = json.dumps({"source": source, "context": ctx}, ensure_ascii=False)
    engine_dir = str(Path(__file__).resolve().parent.parent / "engine")
    platform_dir = str(Path(__file__).resolve().parent.parent)
    repo_root = str(Path(__file__).resolve().parents[2])
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [engine_dir, platform_dir, repo_root, env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    # 宿主环境可能启用 PYTHONSAFEPATH=1（PYTHONPATH 被忽略），
    # 因此把导入路径直接注入子进程代码，双保险。
    runner_code = (
        "import sys as _sys_boot; _sys_boot.path[:0] = %r\n"
        % ([engine_dir, platform_dir, repo_root],)
    ) + _SANDBOX_RUNNER
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [sys.executable, "-c", runner_code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    job = None
    if os.name == "nt":
        try:
            try:
                from legion_ide.exec_sandbox import attach_job
            except ImportError:
                from agent_platform.legion_ide.exec_sandbox import attach_job
            job = attach_job(proc)
        except Exception as exc:  # noqa: BLE001 - OS 隔离失败必须 fail-closed
            proc.kill()
            proc.communicate()
            return {
                "ok": False,
                "killed": True,
                "isolation": "failed",
                "reason": "OS Job Object 挂载失败（fail-closed）：%s: %s"
                % (type(exc).__name__, str(exc)[:200]),
                "elapsed_ms": int(round((time.perf_counter() - t0) * 1000)),
                "verdict": None,
            }
    try:
        out, err = proc.communicate(payload.encode("utf-8"), timeout=timeout_s)
    except subprocess.TimeoutExpired:
        # WO-GATE-FLAKE-20261031（flake 根因，2026-10-31）：
        # subprocess.communicate 的截止钟是 time.monotonic（Windows =
        # GetTickCount64，粒度 15.625ms）：内部先 _time()+timeout 算出
        # endtime，再 _time() 算 remaining 去 join 读线程；两次读数一旦
        # 跨 tick 边界，实际等待会比标称 timeout_s **短最多一个 tick**。
        # 而 elapsed_ms 用 perf_counter（QPC，100ns）从 Popen 前测量，
        # 扣除 spawn/kill 开销（约 11~20ms）后，并行 gate 负载下曾出现
        # 真实等待 2984ms + 开销 12ms = 2996ms < 3000ms 的假超时红
        # （test_loop_passes_static_gate_but_killed_by_sandbox）。
        # 沙箱契约是「墙钟 timeout_s 后击杀」，故这里用 perf_counter 补齐
        # 到真正截止时刻再杀：补偿上限 = 一个 monotonic tick（15.625ms），
        # 不改变击杀语义，只保证 elapsed_ms 确定性覆盖 timeout_s。
        deadline = t0 + timeout_s
        while True:
            remain = deadline - time.perf_counter()
            if remain <= 0:
                break
            time.sleep(min(remain, 0.002))
        proc.kill()
        proc.communicate()
        _close_sandbox_job(job)
        return {
            "ok": False,
            "killed": True,
            "isolation": "job" if job else "plain",
            "reason": "试跑超时 %.0fs 被击杀（墙钟口径，Windows 无 "
            "rlimit，语义覆盖 CPU 上限）" % timeout_s,
            "elapsed_ms": int(round((time.perf_counter() - t0) * 1000)),
            "verdict": None,
        }
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    text = out.decode("utf-8", "replace")
    line = next((l for l in text.splitlines() if l.startswith(_RESULT_MARK)), None)
    if line is None:
        tail = err.decode("utf-8", "replace").strip()[-300:]
        _close_sandbox_job(job)
        return {
            "ok": False,
            "killed": False,
            "isolation": "job" if job else "plain",
            "reason": "子进程无结果标记（exit=%s）stderr 尾: %s"
            % (proc.returncode, tail or "<empty>"),
            "elapsed_ms": elapsed_ms,
            "verdict": None,
        }
    try:
        data = json.loads(line[len(_RESULT_MARK) :])
    except ValueError as e:
        _close_sandbox_job(job)
        return {
            "ok": False,
            "killed": False,
            "isolation": "job" if job else "plain",
            "reason": "结果标记行 JSON 不可解析：%s" % e,
            "elapsed_ms": elapsed_ms,
            "verdict": None,
        }
    if not data.get("ok"):
        _close_sandbox_job(job)
        return {
            "ok": False,
            "killed": False,
            "isolation": "job" if job else "plain",
            "reason": "试跑失败：%s" % data.get("error", "<no error>"),
            "elapsed_ms": elapsed_ms,
            "verdict": None,
        }
    v = data.get("verdict") or {}
    bad = _verdict_dict_issues(v)
    if bad:
        _close_sandbox_job(job)
        return {
            "ok": False,
            "killed": False,
            "isolation": "job" if job else "plain",
            "reason": "试跑产出非法 Verdict：%s" % "; ".join(bad),
            "elapsed_ms": elapsed_ms,
            "verdict": None,
        }
    _close_sandbox_job(job)
    return {
        "ok": True,
        "killed": False,
        "isolation": "job" if job else "plain",
        "reason": "",
        "elapsed_ms": elapsed_ms,
        "verdict": v,
    }


# ---- fail-visible 落档（§3.1，rejects 按日归档 §5 风险 6） ----


def _today():
    """本地日期口径（限频计数与 rejects 归档共用）。"""
    return time.strftime("%Y%m%d")


def write_reject(bridge_dir, submit_id, stage, reasons):
    """拒绝原因落档 rejects/<YYYYMMDD>/<submit_id>.md（按日归档）。
    返回档案路径。
    """
    rj = Path(bridge_dir) / "rejects" / _today()
    rj.mkdir(parents=True, exist_ok=True)
    path = rj / ("%s.md" % submit_id)
    lines = [
        "# bridge 拒绝档：%s" % submit_id,
        "",
        "- **阶段**：%s" % stage,
        "- **时间**：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "- **命中规则**：",
        "",
    ]
    lines += ["%d. %s" % (i + 1, r) for i, r in enumerate(reasons)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---- 处理管线（§3） ----


def default_bridge_dir():
    """仓库内 bridge_inbox 根（DATA_ROOT 之外，§3.2）。"""
    return Path(__file__).resolve().parent.parent / "bridge_inbox"


def _daily_usage(bridge_dir, data_root):
    """当日已受理数（限频口径）：registrations.jsonl 当日条目数 +
    rejects 当日落档数（md 档计数，一提交一档）。本地日期。

    旧单文件形态兼容读：aba47e39 之前 write_reject 落
    rejects/<submit_id>.md 顶层散档（无日期子目录）。按 mtime 属于
    本地当日才计入限频口径——同日新旧形态无缝衔接，跨日旧档自动
    失效不永久占用额度。
    """
    used = 0
    rej_root = Path(bridge_dir) / "rejects"
    rej_dir = rej_root / _today()
    if rej_dir.is_dir():
        used += sum(1 for _ in rej_dir.glob("*.md"))
    legacy_today = time.mktime(time.strptime(_today(), "%Y%m%d"))
    if rej_root.is_dir():
        used += sum(
            1 for p in rej_root.glob("*.md") if p.stat().st_mtime >= legacy_today
        )
    if data_root:
        ledger = Path(data_root) / "data" / "codex_bridge" / "registrations.jsonl"
        if ledger.exists():
            today_iso = time.strftime("%Y-%m-%d")
            with ledger.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue  # 坏行不计（fail-visible 留待审计）
                    if str(row.get("registered_at", "")).startswith(today_iso):
                        used += 1
    return used


def process_submission(
    bridge_dir,
    submit_id,
    source,
    registry,
    data_root=None,
    sandbox_timeout_s=10.0,
    daily_limit=None,
):
    """单次提交全流程：inbox 落件 → 校验 → 沙箱试跑 → 注册 → 移档。

    data_root 显式时登记注册事件（data/codex_bridge/registrations.jsonl，
    幂等键=submit_id，行含 sandbox_ms 试跑耗时基线）；无 data_root 只
    注册不落账（stderr 提示 dry-run），对齐 M5 刀 2「无显式 root 拒写」
    纪律。
    军团-M3：静态门之后、注册之前跑子进程沙箱试跑——运行时炸/死循环/
    非法 Verdict 在此拦截，拒绝落档 stage=沙箱试跑。
    daily_limit（§5 风险 6 工单化限频）：显式传入时按日限频——当日
    registrations+rejects 合计已达上限则拒绝受理（stage=限频，
    fail-visible 落档，inbox 不动）；默认 None=不限频，向后兼容。
    返回 (ok, reasons)。
    """
    bdir = Path(bridge_dir)
    if daily_limit is not None:
        used = _daily_usage(bdir, data_root)
        if used >= daily_limit:
            reasons = [
                "当日受理已达上限 %d（registrations+rejects 合计 "
                "已用 %d，本地日期 %s），限频拒绝受理" % (daily_limit, used, _today())
            ]
            write_reject(bdir, submit_id, "限频", reasons)
            return False, reasons

    inbox = bdir / "inbox" / submit_id
    inbox.mkdir(parents=True, exist_ok=True)
    src_path = inbox / ("strategy_%s.py" % submit_id)
    src_path.write_text(source, encoding="utf-8")

    reasons = validate_source(source)
    if reasons:
        write_reject(bdir, submit_id, "静态校验", reasons)
        shutil.move(str(inbox), str(bdir / "rejects" / _today() / submit_id))
        return False, reasons

    trial = sandbox_trial(source, timeout_s=sandbox_timeout_s)
    if not trial["ok"]:
        write_reject(bdir, submit_id, "沙箱试跑", [trial["reason"]])
        shutil.move(str(inbox), str(bdir / "rejects" / _today() / submit_id))
        return False, [trial["reason"]]

    agent = register_from_source(source, registry, skip_trial=True)
    shutil.move(str(inbox), str(bdir / "processed" / submit_id))
    if data_root:
        reg_dir = Path(data_root) / "data" / "codex_bridge"
        reg_dir.mkdir(parents=True, exist_ok=True)
        row = {
            "submit_id": submit_id,
            "strategy_id": agent.strategy_id,
            "registered_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sandbox_ms": trial["elapsed_ms"],
        }
        with (reg_dir / "registrations.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    else:
        print(
            "[codex-bridge] dry-run：未提供 data_root，注册事件不落账", file=sys.stderr
        )
    return True, []


def load_registered(registry, bridge_dir=None):
    """把 processed/ 下已注册插件装载进 registry（fail-open）。

    幂等：同 strategy_id 已注册 → 视为重复装载跳过（计数 skipped）。
    单插件失败不拖垮其余（对齐影子主链 fail-open 纪律）。
    口径（H6 起更新）：processed/ 件已过受理期 M3 试跑，装配**不再**重复试跑
    （零额外延迟，保持旧契约）；但注册的仍是隔离子进程代理——主进程零 exec，
    之后 propose() **每次**走子进程（延迟换安全，失败 fail-closed）。
    返回 (loaded, skipped)。
    """
    bdir = Path(bridge_dir) if bridge_dir else default_bridge_dir()
    processed = bdir / "processed"
    if not processed.is_dir():
        return 0, 0
    loaded = skipped = 0
    for src in sorted(processed.glob("*/strategy_*.py")):
        try:
            source = src.read_text(encoding="utf-8")
            # H6：装配不重复试跑（processed 已过 M3）；代理 propose 仍隔离执行
            register_from_source(source, registry, skip_trial=True)
            loaded += 1
        except StrategyError as e:
            if "重复注册" in str(e):
                skipped += 1
            else:
                print(
                    "[warn] codex 插件装载跳过 %s：%s"
                    % (src.parent.name, str(e)[:120]),
                    file=sys.stderr,
                )
        except Exception as e:  # noqa: BLE001
            print(
                "[warn] codex 插件装载跳过 %s：%s" % (src.parent.name, str(e)[:120]),
                file=sys.stderr,
            )
    return loaded, skipped
