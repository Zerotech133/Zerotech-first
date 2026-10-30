# -*- coding: utf-8 -*-
"""OSLINK-W4 8795 军团网关隔离验收探针（K-QUANT-20261026-OSLEG-8795PREP）。

用途：在不碰生产 store、不接消费者、不发消息的前提下，验证"W4 契约 ↔ HTTP 8795 通道"
的传输层根基：
- 默认模式（dry-run）：只做本地自检（通道端点对照表、隔离目录可写、8795 是否空闲），不监听端口；
- --run（需老田批准窗口）：在 127.0.0.1:8795 起【隔离】gateway 实例
  （gateway.inbox_gateway.set_store 到临时目录），用真实 HTTP 发 submit/receipt，
  校验信封/幂等/回执，随后 shutdown，并断言真实 agent_platform/bridge_inbox 前后零变化。

红线：不启动生产网关（不写真实 store）、不接消费者、不发真实消息；--run 单次运行 ≤120s。
证据：%OSLEG_PROBE_ROOT%（默认 %LOCALAPPDATA%\\aios\\osleg_probe，非 Windows 回落 %TEMP%）下 run-<stamp>/evidence.json。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import socket
import sys
import threading
import time
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest
import logging
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

REPO = Path(__file__).resolve().parents[2]
PLATFORM = REPO / "agent_platform"
_DEFAULT_WORK = (
    Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    / "aios"
    / "osleg_probe"
)
WORK = Path(os.environ.get("OSLEG_PROBE_ROOT") or _DEFAULT_WORK)

# 通道②（契约 CH2 定义）端点 vs 现役 gateway/serve.py 实际端点：
# 真实接线时必须在接线单里补这张翻译（gateway 当前只暴露 submit/receipt 两个 HTTP 端点）。
ENDPOINT_TRANSLATION = (
    ("POST", "/osleg/v1/submit", "POST", "/inbox/submit"),
    ("POST", "/osleg/v1/consume", "POST", "/(未实现：gateway 无 consume HTTP 端点)"),
    ("POST", "/osleg/v1/answer", "POST", "/(未实现：answer 走同进程 API)"),
    ("POST", "/osleg/v1/deliver", "POST", "/(未实现：deliver 走同进程 API)"),
    ("GET", "/osleg/v1/receipt", "GET", "/inbox/receipt/<id>"),
)


def _sha_listing(root: Path) -> dict:
    """目录快照：文件数、总字节、排序后的 (相对路径, 大小, sha256) 摘要。"""
    files = []
    total = 0
    if root.exists():
        for p in sorted(x for x in root.rglob("*") if x.is_file()):
            data = p.read_bytes()
            total += len(data)
            files.append(
                [
                    str(p.relative_to(root)),
                    len(data),
                    hashlib.sha256(data).hexdigest()[:16],
                ]
            )
    return {
        "exists": root.exists(),
        "count": len(files),
        "bytes": total,
        "digest": hashlib.sha256(
            json.dumps(files, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16],
    }


def real_store_snapshot() -> dict:
    """真实 gateway store（agent_platform/bridge_inbox）快照，用于"零变化"断言。"""
    return _sha_listing(PLATFORM / "bridge_inbox")


def port_in_use(host: str, port: int, timeout: float = 0.5) -> bool:
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def _work_dir_writable() -> bool:
    try:
        WORK.mkdir(parents=True, exist_ok=True)
        probe = WORK / (".write-probe-%d" % int(time.time()))
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def dry_run() -> dict:
    checks = [
        {
            "name": "endpoint_translation_nonempty",
            "ok": bool(ENDPOINT_TRANSLATION),
            "detail": "%d 条通道②→gateway 对照" % len(ENDPOINT_TRANSLATION),
        },
        {
            "name": "platform_dir_exists",
            "ok": PLATFORM.is_dir(),
            "detail": str(PLATFORM),
        },
        {"name": "work_dir_writable", "ok": _work_dir_writable(), "detail": str(WORK)},
        {
            "name": "port_8795_free",
            "ok": not port_in_use("127.0.0.1", 8795),
            "detail": "127.0.0.1:8795",
        },
    ]
    return {"mode": "dry-run", "ok": all(c["ok"] for c in checks), "checks": checks}


def _http_json(method: str, url: str, body=None, timeout: int = 10):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urlrequest.Request(url, data=data, headers=headers, method=method)
    try:
        with safe_urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urlerror.HTTPError as exc:  # 4xx/5xx 也要拿 body 取证
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return exc.code, {"ok": False, "error": "http_%d" % exc.code}


def run_probe(port: int = 8795) -> dict:
    """隔离验收：临时启 8795（隔离 store）→ 真 HTTP submit/receipt → 立即关闭。"""
    if port != 8795:
        raise SystemExit("隔离验收固定 8795（契约端口），不接受 %r" % port)
    if port_in_use("127.0.0.1", port):
        raise SystemExit("8795 已被占用，拒绝启动隔离实例（fail-closed）")
    WORK.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = WORK / ("run-" + stamp)
    run_dir.mkdir(parents=True, exist_ok=True)
    store = run_dir / "gateway_store"
    real_before = real_store_snapshot()

    sys.path.insert(0, str(PLATFORM))
    from gateway import inbox_gateway
    from gateway.serve import build_server

    inbox_gateway.set_store(store)  # 隔离：绝不落真实 bridge_inbox
    srv = build_server(host="127.0.0.1", port=port)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % port
    result = {
        "mode": "run",
        "port": port,
        "store": str(store),
        "endpoint_translation": [list(row) for row in ENDPOINT_TRANSLATION],
        "started_at": stamp,
        "steps": [],
        "ok": False,
    }
    try:
        idem = "osleg-probe-%s" % stamp
        payload = {
            "q": "[probe] osleg 8795 isolation acceptance",
            "report_id": "QR-PROBE-1",
            "trace_id": "tr-probe-1",
        }
        st1, b1 = _http_json(
            "POST",
            base + "/inbox/submit",
            {
                "kind": "question",
                "payload": payload,
                "source": "osleg_probe",
                "idempotency_key": idem,
            },
        )
        result["steps"].append(
            {
                "step": "submit_1",
                "http": st1,
                "ok": bool(isinstance(b1, dict) and b1.get("ok")),
                "body": b1,
            }
        )
        assert st1 == 200 and isinstance(b1, dict) and b1.get("ok"), b1
        msg_id = str((b1.get("data") or {}).get("id") or "")
        assert msg_id.startswith("lg-"), msg_id

        st2, b2 = _http_json(
            "POST",
            base + "/inbox/submit",
            {
                "kind": "question",
                "payload": payload,
                "source": "osleg_probe",
                "idempotency_key": idem,
            },
        )
        replay_id = str((b2.get("data") or {}).get("id") or "")
        result["steps"].append(
            {
                "step": "submit_replay",
                "http": st2,
                "replayed": bool((b2.get("data") or {}).get("replayed")),
                "same_id": replay_id == msg_id,
                "body": b2,
            }
        )
        assert st2 == 200 and replay_id == msg_id, b2
        assert (b2.get("data") or {}).get("replayed") is True, b2

        st3, b3 = _http_json("GET", base + "/inbox/receipt/" + msg_id)
        state = str((b3.get("data") or {}).get("state") or "")
        result["steps"].append(
            {"step": "receipt", "http": st3, "state": state, "body": b3}
        )
        assert st3 == 200 and state == "inbox", b3
        result["msg_id"] = msg_id
        result["ok"] = True
    finally:
        srv.shutdown()
        srv.server_close()
        try:
            inbox_gateway._DEFAULT_STORE = None
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/tools/osleg_http_probe.py", exc)
        time.sleep(0.2)
        result["port_closed"] = not port_in_use("127.0.0.1", port)
        real_after = real_store_snapshot()
        result["real_store_before"] = real_before
        result["real_store_after"] = real_after
        result["real_store_unchanged"] = real_before == real_after
        result["ok"] = (
            bool(result.get("ok"))
            and result["port_closed"]
            and result["real_store_unchanged"]
        )
        evidence = run_dir / "evidence.json"
        evidence.write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        result["evidence"] = str(evidence)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="OSLINK-W4 8795 军团网关隔离验收探针")
    ap.add_argument(
        "--run",
        action="store_true",
        help="窗口内启动隔离 8795 实例（≤120s；需老田批准）",
    )
    args = ap.parse_args(argv)
    if args.run:
        out = run_probe()
    else:
        out = dry_run()
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
