# -*- coding: utf-8 -*-
"""R6 产线侧事件上报配方（emit_event 一段函数，设计稿 §5 镜像件）。

 legion.py 转调配方的镜像：HTTP 转调非 import、纯 stdlib、fail-visible、
 caller 注入、同机免鉴权。产线脚本尾部 1-2 行即可接入：

     from event_emit import emit_event
     emit_event("agent_shadow_daily", "task_fail", run_key,
                {"task": "AgentShadowDaily", "severity": "P1", "summary": "..."})

口径（docs/design/2026-10-18_事件总线R6设计稿.md §5/§7）：
- HTTP 转调：POST <hub>/api/events/ingest（默认 http://127.0.0.1:8091），
  不 import 引擎链、不直连 8091 内部模块；
- 幂等键客户端同式重算：sha256(source|type|run_key|ts_bucket) 截 24 hex
  （ts_bucket=日期段），服务端重算比对，客户端算错只会 422 不会错记；
- 本地节流（§7 风险 1 第①层）：60s 窗口同 source+type 去重，超窗放行；
- fail-visible 不重试风暴（§7 风险 5）：失败本地暂存 data/events_outbox/
  *.jsonl（上限 500 条/文件，超出轮转新文件），hub 恢复后 flush_outbox()
  补投；执行面零依赖事件链——emit 永不抛异常，永不阻塞主流程；
- 纯 stdlib：urllib/json/hashlib/time，产线脚本零新依赖。
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
import logging

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

HERE = Path(__file__).resolve().parent
_REPO_ROOT = HERE.parent

#: outbox 暂存目录（产线本地，M1 约定 data/events_outbox/）
OUTBOX_DIR = _REPO_ROOT / "data" / "events_outbox"

#: hub 上报端点（过渡期 hub = 8091 ingest，§5）
HUB_DEFAULT = "http://127.0.0.1:8091"

#: 信封版本（§2.1，与 portal/api_events.SCHEMA_VER 冻结一致）
SCHEMA_VER = "r6.v1"
TZ_CST = timezone(timedelta(hours=8))

#: 本地节流窗口（§7 风险 1：60s 同 source+type 去重）
THROTTLE_WINDOW = 60.0
#: outbox 单文件条数上限（超出轮转新文件，§7 风险 5）
OUTBOX_FILE_CAP = 500

# 进程内节流状态：{(source, type): 上次放行时刻 monotonic}
_THROTTLE = {}


def reset_throttle():
    """清本地节流状态（仅测试用）。"""
    _THROTTLE.clear()


def now_cst():
    """当前 +08:00 时刻（ISO8601 秒级，§2.1 ts 口径）。"""
    return datetime.now(TZ_CST).isoformat(timespec="seconds")


def compute_event_id(source, event_type, run_key, ts):
    """幂等键（与 portal/api_events.compute_event_id 同式冻结）。"""
    ts_bucket = str(ts)[:10]
    raw = "%s|%s|%s|%s" % (source, event_type, run_key, ts_bucket)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _post_json(url, body, timeout):
    """POST JSON → (status_code, resp_dict)。网络/HTTP 错一律抛给调用方分流。"""
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with safe_urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _outbox_current(outbox_dir):
    """当前写侧 outbox 文件：outbox_YYYYMMDD.jsonl；满 500 条轮转带时刻后缀。"""
    outbox_dir = Path(outbox_dir)
    outbox_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(TZ_CST).strftime("%Y%m%d")
    cur = outbox_dir / ("outbox_%s.jsonl" % today)
    n = 0
    try:
        with open(cur, "r", encoding="utf-8", errors="replace") as fh:
            n = sum(1 for ln in fh if ln.strip())
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/event_emit.py", exc)
    if n >= OUTBOX_FILE_CAP:
        cur = outbox_dir / ("outbox_%s_%s.jsonl"
                            % (today, datetime.now(TZ_CST).strftime("%H%M%S")))
    return cur


def _stage_outbox(outbox_dir, envelope):
    """失败暂存（append-only，永不抛出）；成功返回落盘文件路径，失败 None。"""
    try:
        cur = _outbox_current(outbox_dir)
        with open(cur, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(envelope, ensure_ascii=False) + "\n")
        return str(cur)
    except OSError:
        return None


def emit_event(source, event_type, run_key, payload, caller=None,
               hub=HUB_DEFAULT, timeout=3.0, ts=None, outbox_dir=None,
               _post=None):
    """产线上报一段式入口（永不抛异常；返回回执 dict）。

    参数：
      source/event_type/run_key —— 幂等键三元组（run_key=本次执行标识，
        重跑给新值即合法新事件）；payload 必含 task + severity（P0/P1/P2）。
      caller —— 归因（K-ORG-109）：缺省 task-<payload.task>，或环境变量
        R6_EVENT_CALLER 注入。
      ts —— 事件发生时刻（缺省当前 +08:00）；outbox 补投场景保留原 ts。
      _post —— 内部注入点（测试替身），签名同 _post_json。

    回执：ok / dedup / folded / throttled / staged + error 说明。
    """
    outbox_dir = Path(outbox_dir) if outbox_dir else OUTBOX_DIR
    try:
        payload = dict(payload or {})
        task = str(payload.get("task") or "").strip()
        if not task:
            return {"ok": False, "error": "payload.task 必填（缺省拒发不暂存）"}
        if payload.get("severity") not in ("P0", "P1", "P2"):
            return {"ok": False,
                    "error": "payload.severity 必须为 P0/P1/P2（缺省拒发不暂存）"}
        caller = (str(caller or "").strip()
                  or str(os.environ.get("R6_EVENT_CALLER") or "").strip()
                  or ("task-%s" % task))
        ts = str(ts) if ts else now_cst()
        run_key = str(run_key if run_key is not None else "")
        event_id = compute_event_id(source, event_type, run_key, ts)
        envelope = {
            "event_id": event_id, "type": event_type, "source": source,
            "ts": ts, "caller": caller, "payload": payload,
            "schema_ver": SCHEMA_VER,
        }

        # 本地节流（§7 风险 1①）：60s 窗口同 source+type 去重
        key = (source, event_type)
        last = _THROTTLE.get(key)
        now_mono = time.monotonic()
        if last is not None and now_mono - last < THROTTLE_WINDOW:
            return {"ok": False, "throttled": True,
                    "error": "60s 窗口同 source+type 本地节流（未发送未暂存）",
                    "retry_after": round(THROTTLE_WINDOW - (now_mono - last), 1)}
        _THROTTLE[key] = now_mono

        url = "%s/api/events/ingest" % hub.rstrip("/")
        post = _post if _post is not None else _post_json
        try:
            status, resp = post(url, envelope, timeout)
        except Exception as exc:
            staged_path = _stage_outbox(outbox_dir, envelope)
            return {"ok": False, "staged": staged_path is not None,
                    "staged_file": staged_path,
                    "error": "hub 不可达: %r" % exc}
        if 200 <= status < 300:
            out = {"ok": True, "event_id": event_id, "status": status}
            out.update(resp if isinstance(resp, dict) else {})
            return out
        # hub 明确拒（4xx/5xx）：先暂存再回执（fail-visible，不重试风暴）
        staged_path = _stage_outbox(outbox_dir, envelope)
        return {"ok": False, "status": status, "staged": staged_path is not None,
                "staged_file": staged_path,
                "error": "hub 拒绝: %s" % json.dumps(resp, ensure_ascii=False)[:200]}
    except Exception as exc:  # 底线：永不向产线主流程抛异常
        return {"ok": False, "error": "emit 内部失败: %r" % exc}


def flush_outbox(hub=HUB_DEFAULT, timeout=3.0, outbox_dir=None, _post=None):
    """outbox 补投（hub 恢复后调用；成功行移除，失败行保留，永不抛异常）。"""
    outbox_dir = Path(outbox_dir) if outbox_dir else OUTBOX_DIR
    flushed, kept = 0, 0
    post = _post if _post is not None else _post_json
    try:
        files = sorted(Path(outbox_dir).glob("outbox_*.jsonl"))
    except OSError:
        return {"ok": True, "flushed": 0, "kept": 0, "error": None}
    for f in files:
        survivors = []
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                lines = [ln for ln in fh if ln.strip()]
        except OSError:
            kept += 1
            continue
        for ln in lines:
            try:
                env = json.loads(ln)
                status, _resp = post("%s/api/events/ingest" % hub.rstrip("/"),
                                     env, timeout)
                if 200 <= status < 300:
                    flushed += 1
                    continue
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "tools/event_emit.py", exc)
            survivors.append(ln.rstrip("\n"))
            kept += 1
        try:
            with open(f, "w", encoding="utf-8") as fh:
                fh.write("\n".join(survivors) + ("\n" if survivors else ""))
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/event_emit.py", exc)
    return {"ok": True, "flushed": flushed, "kept": kept, "error": None}
