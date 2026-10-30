# -*- coding: utf-8 -*-
"""JEV 只读 MCP Server（stdio）——把 JEV 判定查询/校准状态暴露给 Hermes agent。

定位（K-ORG-20261026-JEVAPI）：
  · Hermes 在对话/任务中按需问「今天 JEV 抓到哪些利空、哪些有后验命中」；
  · 本 server 零业务逻辑、零直连库：数据面全部经 127.0.0.1:8091 的
    `GET /api/jev/query`（只读接口）与本地 `jev_audit/latest.json`（只读报告）。

协议（沿用 stock_data_mcp.py 基线）：
  · stdin 逐行 JSON-RPC 2.0，stdout 逐行回包；日志一律 stderr（不污染协议通道）。
  · 支持 initialize / tools/list / tools/call / ping；未知方法 -32601。
  · tools/call 失败返回 isError + 修复指引，绝不静默返回空数据。

工具：
  1) jev_query        查询 JEV 已判新闻（时间窗/方向/公司/事件类型/后验）
  2) jev_calibration  JEV 判读校准状态（方向准确率 vs 基线、neutral 带宽、可用性）

环境变量：
  · JEV_API_BASE    门户基址，默认 http://127.0.0.1:8091
  · JEV_CALIB_PATH  校准报告，默认 <repo>/agent_platform/data/jev_audit/latest.json
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import logging
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

try:  # ENCPROT：非 UTF-8 控制台
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "tools/jev_mcp.py", exc)

SERVER_NAME = "jev-news"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"
API_BASE = os.environ.get("JEV_API_BASE", "http://127.0.0.1:8091").rstrip("/")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALIB_PATH = os.environ.get(
    "JEV_CALIB_PATH",
    os.path.join(REPO_ROOT, "agent_platform", "data", "jev_audit", "latest.json"))

_HTTP_TIMEOUT = 20

TOOLS = [
    {
        "name": "jev_query",
        "description": (
            "查询 JEV（新闻结构化判读）已判结果：可按时间窗/方向(bull,bear,neutral)/"
            "关联 A 股代码/事件类型筛选，可选联查 T+1 后验（ret/bench/excess/hit）。"
            "用于回答『今天 JEV 抓到哪些利空/利多』『某只股票最近被 JEV 判了什么』。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "description": "时间窗小时数，默认 24，范围 1-720"},
                "bias": {"type": "string", "description": "方向过滤，逗号分隔：bull,bear,neutral"},
                "code": {"type": "string", "description": "6 位 A 股代码，如 600519"},
                "event_type": {"type": "string", "description": "事件类型过滤，逗号分隔：policy/earnings/price/equity/supply/tech/risk/other"},
                "with_posterior": {"type": "boolean", "description": "true=联查 T+1 后验（命中/超额收益）"},
                "limit": {"type": "integer", "description": "返回条数上限，默认 50，最大 200"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "jev_calibration",
        "description": (
            "JEV 判读校准状态：方向准确率 vs 全判 bull 基线、neutral 带宽命中率、"
            "bull/bear 可用性判定、conf 是否入决策。用于回答『JEV 判断现在准不准、能不能用』。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


class _InvalidParams(Exception):
    """tools/call 参数非法 → JSON-RPC -32602（不把 Python 异常原文抛给模型）。"""


def _log(msg: str) -> None:
    print("[jev-mcp] " + str(msg), file=sys.stderr, flush=True)


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _http_get_json(path: str, params: dict[str, str]) -> dict:
    qs = urllib.parse.urlencode(params)
    url = API_BASE + path + ("?" + qs if qs else "")
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with safe_urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError("HTTP %d %s：%s" % (exc.code, url, body))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(
            "门户不可达 %s（%s）。修复：确认 8091 在跑（python tools/check_ports.py）"
            % (url, exc))
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("接口返回非 JSON（%s）：%s" % (url, exc))
    if not isinstance(data, dict) or not data.get("ok"):
        raise RuntimeError("接口返回异常：%s" % raw[:300])
    return data


def _fmt_query(data: dict) -> str:
    sw = data.get("summary_window") or {}
    lines = [
        "JEV 查询（近 %s 小时，schema=%s）：窗口内已判 %s 条（利多 %s / 利空 %s / 中性 %s），"
        "本次返回 %s 条%s"
        % (data.get("window_hours"), data.get("schema_version") or "jev.v2",
           sw.get("total", 0), sw.get("bull", 0),
           sw.get("bear", 0), sw.get("neutral", 0), data.get("returned", 0),
           "（含 T+1 后验）" if data.get("posterior_band_pct") is not None else ""),
    ]
    low_types = data.get("low_signal_event_types") or []
    if low_types:
        lines.append("低信号事件（不参与方向信号，仅作参考）：%s" % "、".join(low_types))
    items = data.get("items") or []
    for it in items[:15]:
        bits = ["%s [%s]" % (str(it.get("ts") or "")[5:16], it.get("bias_cn") or it.get("bias"))]
        bits.append(it.get("title") or "")
        ev = it.get("event_cn") or it.get("event_type")
        imp = it.get("impact_cn") or it.get("impact")
        if ev or imp:
            bits.append("（%s/%s）" % (ev, imp))
        codes = it.get("codes") or []
        if codes:
            bits.append(" 关联:" + ",".join(codes[:4]))
        conf = it.get("conf")
        if conf is not None:
            bits.append(" conf=%s" % conf)
        if it.get("low_signal"):
            bits.append(" [低信号事件]")
        post = it.get("posterior")
        if post:
            bits.append(" 后验:T+1超额=%s hit=%s" % (post.get("excess"), post.get("hit")))
            if post.get("highvol"):
                bits.append(" [高波动]")
        lines.append("- " + "".join(str(b) for b in bits))
    if len(items) > 15:
        lines.append("…另有 %d 条（可加 limit/filters 精确查）" % (len(items) - 15))
    if not items:
        lines.append("（无匹配；可放宽 hours 或去掉过滤条件）")
    return "\n".join(lines)


def _fmt_calibration(cal: dict) -> str:
    d = (cal or {}).get("directional") or {}
    a = (cal or {}).get("assessment") or {}
    n = d.get("n")
    rate = d.get("rate")
    base = (d.get("baseline_all_bull") or {}).get("rate")
    lines = [
        "JEV 判读校准（%s）：方向类 n=%s，准确率 %s%%，全判 bull 基线 %s%%，增量 %s pp"
        % (cal.get("generated_at"), n,
           round((rate or 0) * 100, 1), round((base or 0) * 100, 1),
           round(((a.get("directional") or {}).get("edge_vs_baseline") or 0) * 100, 1)),
    ]
    neutral = a.get("neutral") or {}
    if neutral:
        lines.append("neutral 带宽 %s%%：命中 %s%%，高波动 %s%%（只作波动提示，不作方向）"
                     % (neutral.get("band_pct"),
                        round((neutral.get("band_hit_rate") or 0) * 100),
                        round((neutral.get("highvol_share") or 0) * 100)))
    bull, bear = a.get("bull") or {}, a.get("bear") or {}
    lines.append("bull 可用=%s（%s）｜bear 可用=%s（%s）"
                 % (bull.get("usable"), bull.get("reason"),
                    bear.get("usable"), bear.get("reason")))
    conf = a.get("confidence") or {}
    lines.append("conf 入决策权重=%s（%s）"
                 % (conf.get("decision_weight"), conf.get("note")))
    d_d = a.get("directional") or {}
    gate = d_d.get("gate_pass")
    if gate is not None:
        lines.append("门控通过=%s（条件：edge≥2pp 且 Wilson 下界>基线 且 ≥20 交易日；"
                     "当前 n_days=%s、Wilson下界=%s、data_through=%s、落后=%s 交易日）"
                     % (gate, (cal or {}).get("n_trade_days"),
                        d_d.get("wilson95_lower"), (cal or {}).get("data_through"),
                        (cal or {}).get("stale_trade_days")))
    low = (a.get("low_signal_event_types") or [])
    if low:
        lines.append("低信号事件（不参与方向信号）：%s" % "、".join(low))
    return "\n".join(lines)


def _call_tool(name: str, args: dict) -> str:
    if not isinstance(args, dict):
        raise _InvalidParams("arguments 必须是对象")
    if name == "jev_query":
        allowed = {"hours", "bias", "code", "event_type", "with_posterior", "limit"}
        unknown = set(args) - allowed
        if unknown:
            raise _InvalidParams("未知参数：%s（可用：%s）"
                                 % (",".join(sorted(unknown)), ",".join(sorted(allowed))))
        params: dict[str, str] = {}
        if args.get("hours") is not None:
            params["hours"] = str(_as_int(args["hours"], "hours", 1, 720))
        if args.get("limit") is not None:
            params["limit"] = str(_as_int(args["limit"], "limit", 1, 200))
        for key, valid in (("bias", ("bull", "bear", "neutral")),
                           ("event_type", None)):
            val = args.get(key)
            if val:
                text = str(val).strip()
                for token in [x.strip() for x in text.split(",") if x.strip()]:
                    if valid is not None and token not in valid:
                        raise _InvalidParams("%s 非法：%s（可选 %s）"
                                             % (key, token, "/".join(valid)))
                params[key] = text
        if args.get("code"):
            code = str(args["code"]).strip()
            if not (code.isdigit() and len(code) == 6):
                raise _InvalidParams("code 非法：需 6 位 A 股代码（如 600519）: %r" % (code,))
            params["code"] = code
        if args.get("with_posterior"):
            if not isinstance(args["with_posterior"], bool):
                raise _InvalidParams("with_posterior 必须是布尔值")
            params["with_posterior"] = "1"
        return _fmt_query(_http_get_json("/api/jev/query", params))
    if name == "jev_calibration":
        if args:
            raise _InvalidParams("jev_calibration 不接受参数")
        try:
            with open(CALIB_PATH, "r", encoding="utf-8") as fh:
                cal = json.load(fh)
        except OSError as exc:
            raise RuntimeError(
                "校准报告不存在：%s（%s）。修复：等 JevDriftDaily 07:10 P4 生成，"
                "或手动 python -B tools/jev_audit.py --days 45" % (CALIB_PATH, exc))
        except ValueError as exc:
            raise RuntimeError("校准报告 JSON 损坏：%s（%s）" % (CALIB_PATH, exc))
        return _fmt_calibration(cal)
    raise _InvalidParams("未知工具：%s（可用：jev_query / jev_calibration）" % (name,))


def _as_int(value, name: str, lo: int, hi: int) -> int:
    try:
        val = int(value)
    except (TypeError, ValueError):
        raise _InvalidParams("%s 必须是整数: %r" % (name, value))
    if val < lo or val > hi:
        raise _InvalidParams("%s 超出范围 [%d, %d]: %d" % (name, lo, hi, val))
    return val


def _handle(req: dict) -> dict | None:
    if not isinstance(req, dict):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        try:
            text = _call_tool(name, args)
        except _InvalidParams as exc:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": str(exc)}}
        except Exception as exc:  # noqa: BLE001
            _log("tool %s error: %s" % (name, exc))
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": str(exc)}], "isError": True}}
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": text}], "isError": False}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    # 通知（无 id）或未知方法
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": "Method not found: %s" % method}}


def main() -> int:
    _log("start name=%s api=%s calib=%s" % (SERVER_NAME, API_BASE, CALIB_PATH))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            print(_dumps({"jsonrpc": "2.0", "id": None,
                          "error": {"code": -32700, "message": "Parse error"}}), flush=True)
            continue
        try:
            resp = _handle(req)
        except Exception as exc:  # noqa: BLE001
            _log("handler error: %s" % exc)
            resp = {"jsonrpc": "2.0", "id": req.get("id") if isinstance(req, dict) else None,
                    "error": {"code": -32603, "message": "Internal error: %s" % exc}}
        if resp is not None:
            print(_dumps(resp), flush=True)
    _log("stdin closed, exit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
