#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qa_run.py —— QA 岗实跑验证工具（K-ORG-090 / T3，QA-RUN 五步闭环）。

背景：数字军团 QA 岗过去是纸面评审，T3 要求「实跑验证」——对交付物
（本地 HTTP 服务/脚本）起真服务、探真端口、断真行为，结论入 dev_events 轨迹。

五步闭环：
  1. 起服务   subprocess.Popen 启动目标命令（list 形式），stdout/stderr 落
              临时日志文件；目标端口已监听则跳过（幂等，支持对已运行服务 QA）。
  2. 探活     HTTP 直连 GET，最多 30 次 x 1s 轮询。
  3. 行为断言 按 targets 配置逐项 GET/POST，断言状态码 / JSON 路径值 / 正文包含。
  4. 日志扫描 读服务日志，扫 ERROR/Traceback 关键行（关键词表可配），
              命中列出 <=5 行。命中只 WARN 不判 FAIL（供人工研判）。
  5. 结论入账 追加到 portal/data/dev_events.jsonl（kind=qa_run），fail-open：
              账本写失败只 WARN 不炸主流程。

用法示例：
  python tools/qa_run.py --cmd "python server.py --port 8094" --port 8094 --path /
  python tools/qa_run.py --port 8094 --path / \\
      --assert-json '[{"method":"GET","path":"/api/x","expect_status":200,
                       "expect_json":{"ok":true}}]' \\
      --task K-ORG-090

判例依据（8093 战役探活配方）：
  - 探活必须 HTTP 直连 127.0.0.1，用 urllib.request.build_opener(
    urllib.request.ProxyHandler({})) 绕过系统代理；系统代理会把 127.0.0.1
    请求转发到代理上，导致「服务明明活着却探死」的假阴性。
  - 长跑子进程日志必须落盘：Popen(stdout=open(LOG,'wb'))；python 子进程
    缺 -u 时补 -u，否则 stdout 块缓冲导致日志滞后、日志扫描扫不到实时错误。
  - 教训：验 pandas 依赖页必须用系统 3.11 解释器起服务/验证——工位 venv
    缺 pandas 会把依赖问题误判成交付物缺陷（环境错位假阳性）。

退出码：全 PASS=0，任一 FAIL=1（供脚本/CI 判定）。
"""

from __future__ import annotations

import argparse
import json
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

# K-ORG-090/T3：账本路径固定在仓库 portal/data 下，与本工具位置强约定
ROOT = Path(__file__).resolve().parent.parent
DEV_EVENTS = ROOT / "portal" / "data" / "dev_events.jsonl"

DEFAULT_LOG_KEYWORDS = ["Traceback", "ERROR"]
PROBE_ATTEMPTS = 30  # 探活轮询上限
PROBE_INTERVAL = 1.0  # 每轮间隔秒


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def port_listening(port: int) -> bool:
    """TCP 探测端口是否已监听（幂等判断用）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def direct_get(
    url: str, data: bytes | None = None, timeout: float = 5.0
) -> tuple[int, str]:
    """8093 判例配方：build_opener(ProxyHandler({})) 直连，禁系统代理。

    系统代理会劫持 127.0.0.1 请求造成假阴性，故每次请求都用空代理表。
    返回 (status, body_text)；连接失败返回 (0, 错误说明)。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=data)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:  # 4xx/5xx 也要拿到状态码供断言
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:  # 连接拒绝/超时等
        return 0, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------- 步骤 1：起服务
def start_service(cmd: list[str], port: int) -> tuple[Any, Path, bool]:
    """启动目标服务，返回 (proc 或 None, 日志路径, 是否已启动)。

    长跑落盘判例（8093）：Popen(stdout=open(LOG,'wb'))；python 子进程无 -u
    时补 -u，避免块缓冲导致日志滞后。端口已监听则幂等跳过（不起进程）。
    """
    if port_listening(port):
        return None, Path("<端口已监听，未起服务，无日志可扫>"), False
    # python 解释器子进程无 -u 时补 -u，保证日志实时落盘
    exe = Path(cmd[0]).name.lower()
    if exe.startswith(("python", "py")) and "-u" not in cmd:
        cmd = [cmd[0], "-u"] + cmd[1:]
    log_path = Path(tempfile.gettempdir()) / f"qa_run_{port}_{int(time.time())}.log"
    # 判例：日志句柄打开后交给 Popen，父进程侧随即关闭（子进程持有副本）
    with open(log_path, "wb") as fh:
        proc = subprocess.Popen(
            cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=str(ROOT)
        )
    return proc, log_path, True


# --------------------------------------------------------------- 步骤 2：探活
def probe_once(port: int, path: str) -> tuple[bool, str]:
    """单次直连探活（8093 判例：禁系统代理，见 direct_get）。"""
    status, body = direct_get(f"http://127.0.0.1:{port}{path}", timeout=3.0)
    return status > 0, f"-> {status} {body[:60]}"


def probe(port: int, path: str, timeout: float) -> tuple[bool, str]:
    """轮询探活：最多 30 次 x 1s，且不超过 timeout 总预算。"""
    last = ""
    deadline = time.time() + timeout
    for i in range(PROBE_ATTEMPTS):
        ok, last = probe_once(port, path)
        if ok or time.time() >= deadline:
            return ok, f"第 {i + 1} 次尝试 {last}"
        time.sleep(PROBE_INTERVAL)
    return False, f"{PROBE_ATTEMPTS} 次轮询均失败，最后: {last}"


# --------------------------------------------------------------- 步骤 3：行为断言
def run_asserts(port: int, targets: list[dict]) -> tuple[bool, list[str]]:
    """逐项 GET/POST 并断言，返回 (全过?, 明细行列表)。

    断言三类：expect_status（默认 200）/ expect_json（键支持 a.b.c 点路径）
    / expect_contains（正文子串）。本步骤是唯一影响 verdict 的断言环节。
    """
    lines: list[str] = []
    ok_all = True
    for i, t in enumerate(targets):
        method = str(t.get("method", "GET")).upper()
        path = t.get("path", "/")
        data = None
        if method == "POST":
            raw = t.get("data", {})
            data = json.dumps(raw).encode("utf-8") if raw else b""
        status, body = direct_get(f"http://127.0.0.1:{port}{path}", data=data)
        bad: list[str] = []
        want_status = t.get("expect_status", 200)
        if status != want_status:
            bad.append(f"状态码 {status}!={want_status}")
        if "expect_json" in t:
            try:
                obj = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                bad.append("响应非合法 JSON")
            else:
                for key, want in t["expect_json"].items():
                    node: Any = obj
                    for part in str(key).split("."):
                        node = node.get(part) if isinstance(node, dict) else None
                        if node is None:
                            break
                    if node != want:
                        bad.append(f"json[{key}]={node!r}!={want!r}")
        if "expect_contains" in t and t["expect_contains"] not in body:
            bad.append(f"正文不含 {t['expect_contains']!r}")
        ok = not bad
        ok_all = ok_all and ok
        lines.append(
            f"{'PASS' if ok else 'FAIL'} #{i} {method} {path} -> {status}"
            + ("; " + "; ".join(bad) if bad else " 符合预期")
        )
    return ok_all, lines


# --------------------------------------------------------------- 步骤 4：日志扫描
def scan_log(log_path: Path, keywords: list[str]) -> tuple[list[str], int]:
    """扫服务日志关键行，返回 (命中行<=5, 命中总数)。"""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [f"<日志文件不可读: {log_path}>"], 0
    hits = [ln.rstrip() for ln in text.splitlines() if any(k in ln for k in keywords)]
    return hits[:5], len(hits)


# --------------------------------------------------------------- 步骤 5：入账
def append_event(record: dict) -> bool:
    """结论追加到 dev_events.jsonl；fail-open：失败只 WARN 返回 False。"""
    try:
        DEV_EVENTS.parent.mkdir(parents=True, exist_ok=True)
        with open(DEV_EVENTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception as e:  # fail-open 判例：账本炸不能炸 QA
        print(f"[WARN] dev_events 写入失败（fail-open 忽略）: {e}", file=sys.stderr)
        return False


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="QA-RUN 五步闭环实跑验证工具")
    ap.add_argument("--cmd", default="", help="启动命令字符串（shlex 拆分为 list）")
    ap.add_argument("--port", type=int, required=True, help="目标服务端口")
    ap.add_argument("--path", default="/", help="探活路径")
    ap.add_argument(
        "--assert-json", default="",
        help='断言配置 JSON：list 或单个对象，如 \'{"path":"/api/x",'
        '"expect_status":200,"expect_json":{"ok":true}}\'',
    )
    ap.add_argument("--log-grep", default=",".join(DEFAULT_LOG_KEYWORDS),
                    help="日志扫描关键词，逗号分隔（默认 ERROR,Traceback）")
    ap.add_argument("--task", default="", help="工单号")
    ap.add_argument("--timeout", type=int, default=60,
                    help="探活总预算秒（默认 60，覆盖 30x1s 上限）")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    t0 = time.time()
    steps: dict[str, Any] = {}
    keywords = [k for k in args.log_grep.split(",") if k]

    proc = None       # 自己起的服务进程；没起则保持 None（finally 不动它）
    log_path = Path("<无>")
    started = False
    try:
        # ---- 步骤 1：起服务（幂等）----
        if args.cmd:
            cmd_list = shlex.split(args.cmd, posix=False)
            cmd_list = [a.strip('"') for a in cmd_list if a.strip()]
            proc, log_path, started = start_service(cmd_list, args.port)
            steps["start"] = (
                f"已启动 pid={proc.pid} 日志={log_path}"
                if started else f"SKIP（端口 {args.port} 已监听，幂等跳过）"
            )
        elif port_listening(args.port):
            log_path = Path("<未起服务：复用已监听端口，无日志可扫>")
            steps["start"] = "SKIP（--cmd 缺省且端口已监听，直接探活）"
        else:
            print(f"[FAIL] --cmd 缺省且端口 {args.port} 未监听", file=sys.stderr)
            return 1

        # ---- 步骤 2：探活 ----
        probe_ok, probe_msg = probe(args.port, args.path, float(args.timeout))
        steps["probe"] = ("PASS" if probe_ok else "FAIL") + f" {probe_msg}"

        # ---- 步骤 3：行为断言 ----
        targets: list[dict] = []
        if args.assert_json:
            parsed = json.loads(args.assert_json)
            targets = parsed if isinstance(parsed, list) else [parsed]
        if not targets:
            targets = [{"method": "GET", "path": args.path, "expect_status": 200}]
        assert_ok, assert_lines = run_asserts(args.port, targets)
        steps["assert"] = (
            ("PASS" if assert_ok else "FAIL") + " | " + " ; ".join(assert_lines)
        )

        # ---- 步骤 4：日志扫描（命中只 WARN，不判 FAIL，供人工研判）----
        if started:
            hits, total = scan_log(log_path, keywords)
            steps["log_scan"] = (
                f"{'PASS' if total == 0 else 'WARN'} 命中 {total} 处"
                + (" | " + " ; ".join(hits) if hits else "")
            )
        else:
            steps["log_scan"] = "SKIP（复用已监听服务，无日志可扫）"
    finally:
        # 结束清理：自己起的服务 terminate+wait；没起的不动
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    elapsed = round(time.time() - t0, 2)
    verdict = "PASS" if (probe_ok and assert_ok) else "FAIL"

    # ---- 步骤 5：结论入轨迹（fail-open）----
    record = {
        "ts": _ts(),
        "day": _ts()[:10],
        "kind": "qa_run",
        "task": args.task,
        "agent": "qa_run",
        "ide": "",
        "model": "",
        "detail": {
            "target": f"127.0.0.1:{args.port}",
            "verdict": verdict,
            "steps": steps,
            "elapsed": elapsed,
        },
    }
    append_event(record)

    # ---- 人类可读摘要（每步 ✓/✗）----
    print("=" * 62)
    print(f"QA-RUN 结论: {verdict}  (127.0.0.1:{args.port}, 耗时 {elapsed}s)")
    print("=" * 62)
    for key in ("start", "probe", "assert", "log_scan"):
        mark = "✓" if not steps[key].startswith(("FAIL",)) else "✗"
        print(f"  {mark} {key:<9} {steps[key]}")
    print(f"  入账: {DEV_EVENTS} kind=qa_run verdict={verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
