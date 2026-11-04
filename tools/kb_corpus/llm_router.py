# -*- coding: utf-8 -*-
"""LLM 三级降级路由器 + 调用成本监控（task-183）。

降级链（按顺序，前级失败自动降级）：
  1) Bailian v4d（DashScope OpenAI 兼容）  model=env RAG_MODEL_V4D
  2) Bailian v2                            model=env RAG_MODEL_V2
  3) 本地 Ollama qwen3.5:9b（127.0.0.1:11434）

降级触发：超时 / HTTP 429 / HTTP 5xx / 网络错误（URLError/ConnectionError）；
每级记录耗时、状态、错误类型；全部失败返回 ok=False（调用方决定兜底话术）。

密钥纪律：复用 C:/项目/_vault/secrets/dashscope_api_key.txt 与 dashscope_base_url.txt，
只读不写；异常统一脱敏（sk-****），日志/返回值不含密钥。

成本：每次成功调用追加一行到 research/reports/llm_cost_<date>.jsonl
（可用 LLM_COST_LOG 覆盖）；日累计超 LLM_DAILY_BUDGET_CNY（默认 20 元）→ budget_warning=True。

离线/测试：所有 HTTP 走可注入 transport，测试 100% mock，不联网。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SECRETS = Path(r"C:/项目/_vault/secrets")
KEY_FILE = SECRETS / "dashscope_api_key.txt"
BASE_FILE = SECRETS / "dashscope_base_url.txt"
REPORTS = REPO / "research" / "reports"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
OLLAMA_DEFAULT = "http://127.0.0.1:11434"

TIER_V4D = "bailian_v4d"
TIER_V2 = "bailian_v2"
TIER_OLLAMA = "ollama"

_SK_RE = re.compile(r"sk-[A-Za-z0-9_\-]{4,}")


def redact(text) -> str:
    """错误信息脱敏：密钥片段 → sk-****。"""
    return _SK_RE.sub("sk-****", str(text or ""))[:500]


def load_api_key() -> str:
    """只读密钥：环境变量 DASHSCOPE_API_KEY 优先，其次私有文件；不回显。"""
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if key:
        return key
    try:
        return KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def load_base_url() -> str:
    for cand in (os.environ.get("DASHSCOPE_BASE_URL", "").strip(),):
        if cand:
            return cand.rstrip("/")
    try:
        val = BASE_FILE.read_text(encoding="utf-8").strip()
        if val:
            return val.rstrip("/")
    except OSError:
        pass
    return DEFAULT_BASE_URL


def tiers() -> list[dict]:
    """降级链定义（每次调用时读取 env，便于测试覆盖）。"""
    base = load_base_url()
    key = load_api_key()
    return [
        {"tier": 1, "provider": TIER_V4D,
         "model": os.environ.get("RAG_MODEL_V4D", "qwen2.5-14b-instruct-a57a3521e4e0"),
         "base_url": base, "api_key": key,
         "timeout": float(os.environ.get("RAG_TIER_TIMEOUT", "60"))},
        {"tier": 2, "provider": TIER_V2,
         "model": os.environ.get("RAG_MODEL_V2", "qwen2.5-14b-instruct-e383f32d42f2"),
         "base_url": base, "api_key": key,
         "timeout": float(os.environ.get("RAG_TIER_TIMEOUT", "60"))},
        {"tier": 3, "provider": TIER_OLLAMA,
         "model": os.environ.get("RAG_OLLAMA_MODEL", "qwen3.5:9b"),
         "base_url": os.environ.get("OLLAMA_HOST", OLLAMA_DEFAULT).rstrip("/"),
         "api_key": "", "timeout": float(os.environ.get("RAG_OLLAMA_TIMEOUT", "120"))},
    ]


def _http_post(url: str, headers: dict, payload: dict, timeout: float) -> tuple[int, str]:
    """默认 transport（测试注入替换，不联网）。返回 (status, body_text)。"""
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body = str(exc)
        return int(exc.code), body


def _bailian_payload(model: str, prompt: str, system: str | None,
                     temperature: float, max_tokens: int) -> dict:
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return {"model": model, "messages": msgs, "temperature": temperature,
            "max_tokens": max_tokens, "stream": False}


def _ollama_payload(model: str, prompt: str, system: str | None,
                    temperature: float, max_tokens: int) -> dict:
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return {"model": model, "stream": False, "think": False, "messages": msgs,
            "options": {"temperature": temperature, "num_predict": max_tokens}}


def _estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    pin = float(os.environ.get("LLM_PRICE_IN_PER_1K", "0.002"))
    pout = float(os.environ.get("LLM_PRICE_OUT_PER_1K", "0.006"))
    return round(prompt_tokens / 1000.0 * pin + completion_tokens / 1000.0 * pout, 6)


def _cost_log_path(now: datetime | None = None) -> Path:
    """成本日志路径：默认按本地日期分文件（llm_cost_YYYYMMDD.jsonl），LLM_COST_LOG 可覆盖。"""
    override = os.environ.get("LLM_COST_LOG", "").strip()
    if override:
        return Path(override)
    day = (now or datetime.now().astimezone()).strftime("%Y%m%d")
    return REPORTS / f"llm_cost_{day}.jsonl"


def load_cost_log(path: Path | None = None) -> list[dict]:
    p = Path(path) if path else _cost_log_path()
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def daily_cost(day: str | None = None, path: Path | None = None) -> float:
    day = day or datetime.now().astimezone().strftime("%Y-%m-%d")
    total = 0.0
    for row in load_cost_log(path):
        ts = str(row.get("ts") or "")
        if ts.startswith(day):
            total += float(row.get("cost_cny") or 0.0)
    return round(total, 6)


def budget_cny() -> float:
    return float(os.environ.get("LLM_DAILY_BUDGET_CNY", "20"))


def _append_cost(row: dict, path: Path | None = None) -> None:
    """追加一行成本流水；跨进程加锁，避免 Windows CRT seek-to-end 竞态丢行。"""
    p = Path(path) if path else _cost_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        if os.name == "nt":
            import msvcrt
            # 锁文件首字节；O_APPEND 下写入前统一加锁，写完释放。
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            try:
                os.write(fd, line)
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                os.write(fd, line)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _safe_daily_cost(day: str, path: Path | None) -> tuple[float, str | None]:
    """读成本日志失败不应影响主流程（审计：记账失败曾导致已付费答案被丢弃/抛异常）。"""
    try:
        return daily_cost(day, path), None
    except Exception as exc:  # noqa: BLE001
        return 0.0, redact(f"cost_log_read_error:{type(exc).__name__}:{exc}")


def generate(prompt: str, *, system: str | None = None, temperature: float = 0.2,
             max_tokens: int = 800, transport=None, cost_log: str | Path | None = None,
             purpose: str = "", now: datetime | None = None) -> dict:
    """按三级链生成；返回 {ok,text,tier,provider,model,attempts,usage,cost_cny,budget_warning,...}。"""
    now = now or datetime.now().astimezone()
    transport = transport or _http_post
    attempts: list[dict] = []
    for t in tiers():
        started = time.monotonic()
        prov, model = t["provider"], t["model"]
        try:
            if prov == TIER_OLLAMA:
                url = t["base_url"] + "/api/chat"
                body = _ollama_payload(model, prompt, system, temperature, max_tokens)
                headers = {"Content-Type": "application/json"}
            else:
                url = t["base_url"] + "/chat/completions"
                body = _bailian_payload(model, prompt, system, temperature, max_tokens)
                headers = {"Content-Type": "application/json",
                           "Authorization": f"Bearer {t['api_key']}"}
            status, text = transport(url, headers, body, t["timeout"])
            elapsed = round((time.monotonic() - started) * 1000, 1)
            if status == 200:
                data = json.loads(text)
                if prov == TIER_OLLAMA:
                    content = str((data.get("message") or {}).get("content") or "")
                    pt = int(data.get("prompt_eval_count") or 0)
                    ct = int(data.get("eval_count") or 0)
                    cost = 0.0
                else:
                    content = str(((data.get("choices") or [{}])[0].get("message") or {})
                                  .get("content") or "")
                    usage = data.get("usage") or {}
                    pt = int(usage.get("prompt_tokens") or 0)
                    ct = int(usage.get("completion_tokens") or 0)
                    cost = _estimate_cost(pt, ct)
                attempts.append({"tier": t["tier"], "provider": prov, "model": model,
                                 "status": 200, "elapsed_ms": elapsed})
                log_path = cost_log or _cost_log_path()
                row = {"ts": now.isoformat(timespec="seconds"), "provider": prov,
                       "model": model, "tier": t["tier"],
                       "prompt_tokens": pt, "completion_tokens": ct,
                       "total_tokens": pt + ct, "cost_cny": cost,
                       "purpose": purpose or "rag"}
                cost_log_error = None
                if not content:
                    # 审计修复：200 但空内容已经消耗 token，必须落账，再降级。
                    attempts[-1]["error"] = "empty_content"
                    try:
                        _append_cost({**row, "error": "empty_content"}, log_path)
                    except Exception as exc:  # noqa: BLE001
                        cost_log_error = redact(f"cost_log_write_error:{type(exc).__name__}")
                    continue
                # 审计修复：记账失败只记 cost_log_error，不能丢弃已付费的 200 答案。
                try:
                    _append_cost(row, log_path)
                except Exception as exc:  # noqa: BLE001
                    cost_log_error = redact(f"cost_log_write_error:{type(exc).__name__}")
                spent, read_error = _safe_daily_cost(now.strftime("%Y-%m-%d"), log_path)
                budget = budget_cny()
                return {"ok": True, "text": content.strip(), "tier": t["tier"],
                        "provider": prov, "model": model, "attempts": attempts,
                        "usage": {"prompt_tokens": pt, "completion_tokens": ct},
                        "cost_cny": cost, "daily_cost_cny": spent, "budget_cny": budget,
                        "budget_warning": spent >= budget, "elapsed_ms": elapsed,
                        "cost_log_error": cost_log_error or read_error}
            # 非 200：429/5xx/网络类可降级；其余 4xx 也降级（可用性优先）并记录
            attempts.append({"tier": t["tier"], "provider": prov, "model": model,
                             "status": status, "elapsed_ms": elapsed,
                             "error": redact(f"http_{status}")})
        except urllib.error.URLError as exc:
            attempts.append({"tier": t["tier"], "provider": prov, "model": model,
                             "status": None, "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                             "error": redact(f"URLError:{exc.reason}")})
        except (TimeoutError, OSError) as exc:
            attempts.append({"tier": t["tier"], "provider": prov, "model": model,
                             "status": None, "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                             "error": redact(f"{type(exc).__name__}:{exc}")})
        except Exception as exc:  # noqa: BLE001 - JSON/未知错误同样降级
            attempts.append({"tier": t["tier"], "provider": prov, "model": model,
                             "status": None, "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                             "error": redact(f"{type(exc).__name__}:{exc}")})
    budget = budget_cny()
    spent, read_error = _safe_daily_cost(now.strftime("%Y-%m-%d"), cost_log)
    return {"ok": False, "text": "", "tier": None, "provider": None, "model": None,
            "attempts": attempts, "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "cost_cny": 0.0, "daily_cost_cny": spent, "budget_cny": budget,
            "budget_warning": spent >= budget, "error": "all_tiers_failed",
            "cost_log_error": read_error}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="LLM 三级降级路由器（默认联网；测试请走 mock transport）")
    ap.add_argument("cmd", choices=["ask", "cost", "tiers"], nargs="?", default="tiers")
    ap.add_argument("--prompt", default="")
    ap.add_argument("--purpose", default="cli")
    a = ap.parse_args(argv)
    if a.cmd == "ask":
        print(json.dumps(generate(a.prompt, purpose=a.purpose), ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "cost":
        print(json.dumps({"daily_cost_cny": daily_cost(), "budget_cny": budget_cny(),
                          "log": str(_cost_log_path()),
                          "entries": len(load_cost_log())}, ensure_ascii=False, indent=1))
        return 0
    print(json.dumps([{k: t[k] for k in ("tier", "provider", "model")} for t in tiers()],
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
