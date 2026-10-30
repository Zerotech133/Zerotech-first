# -*- coding: utf-8 -*-
"""eval-harness：10 条金标任务 + 断言评分 + 回归报告。

用法：
  python -X utf8 tools/eval_harness.py list [--json]
  python -X utf8 tools/eval_harness.py run [--id ID] [--json] [--out path]
结果落 `work/eval/result-<ts>.json`。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
import logging

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from tools.safe_urlopen import safe_urlopen

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
GOLDEN = REPO / "eval" / "golden_tasks.json"
QUALITY = REPO / "eval" / "quality_tasks.json"
OUTDIR = REPO / "work" / "eval"


def load_tasks(path: Path | None = None) -> list:
    p = Path(path) if path else GOLDEN
    d = json.loads(p.read_text(encoding="utf-8"))
    return d.get("tasks") or []




GATEWAY = "http://127.0.0.1:8796/v1/chat/completions"

def _local_native(messages: list, timeout: int = 180) -> str:
    """本地 Ollama 原生 /api/chat + think=false：思考型模型也能拿到最终 content。"""
    import urllib.request
    body = json.dumps({"model": "qwen3.5:9b", "messages": messages,
                       "stream": False, "think": False}, ensure_ascii=False).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=body,
                                 method="POST", headers={"Content-Type": "application/json"})
    with safe_urlopen(req, timeout=timeout) as r:
        return ((json.loads(r.read().decode("utf-8", "replace")).get("message")) or {}).get("content") or ""


def _keypool_ask(messages: list):
    """优先走 Key 池（中转站→GLM→DeepSeek→本地）；返回 (text, channel)。"""
    try:
        sys.path.insert(0, str(REPO / "agent_platform"))
        from legion_command import keypool
        r = keypool.auto_chat(messages)
        text = str((r or {}).get("reply") or "").strip()
        if r.get("ok") and text:
            return text, str(r.get("channel") or "keypool")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/eval_harness.py", exc)
    return "", ""


def _ask(messages: list):
    text, ch = _keypool_ask(messages)
    if text:
        return text, ch
    try:
        text = _local_native(messages)
        if text.strip():
            return text, "local-native"
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/eval_harness.py", exc)
    return _reply(_post(GATEWAY, {"model": "auto", "messages": messages, "stream": False})), "gateway"





def _post(url: str, body: dict, timeout: int = 120) -> dict:
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    with safe_urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _reply(data: dict) -> str:
    return (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def _parse_score(text: str):
    import re
    m = re.search(r'\{[^{}]*"score"\s*:\s*([0-9.]+)[^{}]*\}', text)
    if not m:
        m = re.search(r"score\s*[:：]\s*([0-9.]+)", text, re.I)
    if not m:
        m = re.search(r"\b([0-5])\b", text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def run_quality(t: dict) -> dict:
    """先让被测通道回答，再让同一网关当裁判打分（0–5）。"""
    t0 = time.time()
    try:
        cand, cand_ch = _ask(t.get("input") or [])
        rubric = t.get("rubric") or "回答是否正确、完整、可执行"
        judge_prompt = (
            "你是严格的质量评审。请按评分标准给候选回答打 0-5 分。\n"
            "评分标准：%s\n\n候选回答：\n%s\n\n"
            "只输出一个 0-5 的数字，不要 JSON、不要解释。" % (rubric, cand))
        judge, judge_ch = _ask([{"role": "user", "content": judge_prompt}])
        score = _parse_score(judge)
        min_score = float(t.get("min_score", 3))
        ok = score is not None and score >= min_score
        return {"id": t.get("id"), "desc": t.get("desc"), "type": "quality",
                "rc": 0 if ok else 1, "ok": ok, "secs": round(time.time() - t0, 2),
                "score": score, "min_score": min_score,
                "candidate_channel": cand_ch, "judge_channel": judge_ch,
                "candidate_tail": cand[-800:], "judge_tail": judge[-500:],
                "stdout_tail": "score=%s reason=%s" % (score, judge[-200:]),
                "stderr_tail": ""}
    except Exception as e:  # noqa: BLE001
        return {"id": t.get("id"), "desc": t.get("desc"), "type": "quality",
                "rc": -1, "ok": False, "secs": round(time.time() - t0, 2),
                "score": None, "stdout_tail": "", "stderr_tail": str(e)[:300]}


def _subst(cmd: list) -> list:
    return [str(x).replace("{python}", sys.executable).replace("{repo}", str(REPO)) for x in cmd]


def run_task(t: dict, cwd: Path | None = None, timeout: int = 0) -> dict:
    if t.get("type") == "quality":
        return run_quality(t)
    cmd = _subst(t.get("cmd") or [])
    t0 = time.time()
    try:
        cp = subprocess.run(cmd, cwd=str(cwd or REPO), capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
                            timeout=timeout or int(t.get("timeout") or 120))
        rc, out, err = cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -9, "", "TIMEOUT"
    ok = rc == int(t.get("expect_rc", 0))
    if ok and t.get("expect_contains"):
        ok = str(t["expect_contains"]) in (out + err)
    return {"id": t.get("id"), "desc": t.get("desc"), "rc": rc, "ok": ok,
            "secs": round(time.time() - t0, 2),
            "stdout_tail": out[-1200:], "stderr_tail": err[-800:]}


def run_all(tasks_path: Path | None = None, only: str = "",
            include_quality: bool = True) -> dict:
    tasks = load_tasks(tasks_path)
    if tasks_path is None and include_quality and QUALITY.exists():
        tasks += load_tasks(QUALITY)
    results = [run_task(t) for t in tasks if not only or t.get("id") == only]
    passed = sum(1 for r in results if r["ok"])
    out = {"schema": "eval_result/v1", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "total": len(results), "passed": passed, "failed": len(results) - passed,
           "score": round(passed / len(results), 3) if results else 0.0,
           "results": results}
    OUTDIR.mkdir(parents=True, exist_ok=True)
    f = OUTDIR / ("result-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    out["result_path"] = str(f)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰 eval-harness（金标任务回归）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("list")
    l.add_argument("--json", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--id", default="")
    r.add_argument("--json", action="store_true")
    r.add_argument("--out", default="")
    r.add_argument("--file", default="", help="指定任务文件（如 eval/quality_tasks.json）")
    r.add_argument("--no-quality", action="store_true", help="跳过 LLM 质量任务")
    args = ap.parse_args(argv)
    if args.cmd == "list":
        tasks = load_tasks()
        print(json.dumps(tasks, ensure_ascii=False, indent=1) if args.json else
              "\n".join("%-22s %s" % (t.get("id"), t.get("desc")) for t in tasks))
        return 0
    res = run_all(tasks_path=Path(args.file) if args.file else None, only=args.id,
                  include_quality=not args.no_quality)
    if args.out:
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("passed=%d/%d score=%.3f" % (res["passed"], res["total"], res["score"]))
        for x in res["results"]:
            print("%s %-22s rc=%s %.2fs" % ("PASS" if x["ok"] else "FAIL", x["id"], x["rc"], x["secs"]))
        print("result", res["result_path"])
    return 0 if res["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
