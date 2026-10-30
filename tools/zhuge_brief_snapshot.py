# -*- coding: utf-8 -*-
"""zhuge_brief_snapshot.py — 诸葛亮双锚取数：本地只读快照 /api/zhuge/brief。

- 只读：GET http://127.0.0.1:8091/api/zhuge/brief（本机 HTTP，不落第二本账）；
- 输出：work/seat_brief_zhuge/zhuge_brief_<ts>.json|.md（来源+时间戳+ok/error+suggestions 摘要）；
- 不联网（除本机 8091）、不发消息、不写业务；供诸葛亮席位双锚定时任务使用。

用法：
  python -X utf8 tools/zhuge_brief_snapshot.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo_paths import repo_root  # noqa: E402

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

REPO = repo_root(Path(__file__))
DEFAULT_URL = "http://127.0.0.1:8091/api/zhuge/brief"
DEFAULT_OUT = REPO / "work" / "seat_brief_zhuge"


def fetch(url: str, timeout: int = 30) -> dict:
    """只读拉取本机接口；失败如实返回 ok=False，不抛穿。"""
    try:
        with safe_urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return {"ok": True, "http": resp.status, "payload": json.loads(raw)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": repr(exc), "payload": None}


def build(fetched: dict, url: str) -> dict:
    raw_payload = fetched.get("payload")
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    ok = bool(fetched.get("ok")) and isinstance(raw_payload, dict)
    error = fetched.get("error")
    if fetched.get("ok") and not isinstance(raw_payload, dict):
        error = "upstream_payload_not_dict"
    return {
        "schema": "zhuge_brief_snapshot/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "seat": "zhuge",
        "source": url,
        "ok": ok,
        "http": fetched.get("http"),
        "error": error,
        "business_day": payload.get("business_day"),
        "upstream_generated_at": payload.get("generated_at"),
        "engine": payload.get("engine"),
        "model_invoked": payload.get("model_invoked"),
        "dispatched": payload.get("dispatched"),
        "suggestions": [
            {"id": s.get("id"), "domain": s.get("domain"),
             "priority": s.get("priority"),
             "reason": str(s.get("reason") or "")[:300],
             "source": s.get("source"), "as_of": s.get("as_of")}
            for s in (payload.get("suggestions") or [])
        ],
        "boundaries": ["只读本机接口", "不发消息", "不写业务", "不落第二本账"],
    }


def render_md(snap: dict) -> str:
    lines = ["# 诸葛亮证据快照（双锚取数，%s）" % snap["generated_at"], "",
             "- 来源：`%s`；ok=%s http=%s %s"
             % (snap["source"], snap["ok"], snap.get("http"),
                ("error=" + str(snap["error"])) if snap.get("error") else ""),
             "- business_day=%s engine=%s model_invoked=%s"
             % (snap.get("business_day"), snap.get("engine"), snap.get("model_invoked")),
             "", "## 建议摘要", ""]
    for s in snap.get("suggestions") or []:
        lines.append("- P%s `%s` [%s] %s（source=%s as_of=%s）"
                     % (s.get("priority"), s.get("id"), s.get("domain"),
                        (s.get("reason") or "")[:120], s.get("source"), s.get("as_of")))
    lines += ["", "> 只读取数快照；建议采纳/派单仍走人工审批。"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="诸葛亮双锚取数：本地只读快照")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    snap = build(fetch(args.url), args.url)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    jp = out / ("zhuge_brief_%s.json" % ts)
    mp = out / ("zhuge_brief_%s.md" % ts)
    jp.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    mp.write_text(render_md(snap), encoding="utf-8")
    if not args.quiet:
        print(json.dumps({"ok": snap["ok"], "json": str(jp), "md": str(mp),
                          "suggestions": len(snap["suggestions"])},
                         ensure_ascii=False))
    return 0 if snap["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
