# -*- coding: utf-8 -*-
"""a0_evidence_judge.py — A0 判读常驻流程（离线件，不挂 automation）。

流程：读最新证据包（tools/seat_evidence_packet.py 产物）→ 拼 A0 判读 prompt
→ `--live` 时经 engine.llm 调 glm/glm-5.3（10-18 参谋长层定案）→ 落
`agent_platform/data/shadow/a0_evidence_judgment_<ts>.json|.md`。

纪律：
  - 默认 dry-run（只打印摘要，不调模型、不写文件）；
  - 只读证据包；只写 data/shadow 判读件；不发消息、不改业务、不挂定时；
  - 模型失败如实落 ok=false（LLMError/异常都归一），不伪装成功。

用法：
  python -X utf8 agent_platform/a0_evidence_judge.py                # dry-run
  python -X utf8 agent_platform/a0_evidence_judge.py --live         # 真调 GLM
  python -X utf8 agent_platform/a0_evidence_judge.py --live --model ollama/qwen3.5:9b
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
REPO = BASE.parent
DEFAULT_PACKETS = REPO / "work" / "seat_brief"
DEFAULT_OUT = BASE / "data" / "shadow"
PERSONA = BASE / "prompts" / "a0_chief.md"
DEFAULT_MODEL = "glm/glm-5.3"

BOUNDARIES = ["只读证据包", "不联网(除模型调用)", "不发消息", "不改业务", "不挂定时"]


def latest_packet(root: Path | None = None) -> Path | None:
    root = root or DEFAULT_PACKETS
    files = sorted(root.glob("evidence_packet_*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _load_persona() -> str:
    if PERSONA.is_file():
        try:
            return PERSONA.read_text(encoding="utf-8")
        except OSError:
            pass
    return ("你是数字军团 A0 参谋长。基于给定证据包输出判读：一句话结论、关键证据"
            "（来源+时间戳）、风险与未覆盖、下一步动作与负责项；不得编造；末尾标'草稿·待老田签'。")


def build_prompt(packet: dict, max_chars: int = 12000) -> str:
    tk = packet.get("tickets") or {}
    exp = packet.get("experience") or {}
    lines = [
        "【证据包】schema=%s generated_at=%s" % (packet.get("schema"), packet.get("generated_at")),
        "【来源】" + "; ".join(
            "%s(sha256=%s,exists=%s)" % (s.get("path"), (s.get("sha256") or "")[:12], s.get("exists"))
            for s in (packet.get("sources") or [])),
        "【工单】总数=%s 状态=%s" % (tk.get("total"), json.dumps(tk.get("counts") or {}, ensure_ascii=False)),
    ]
    for r in (tk.get("doing_review") or [])[:10]:
        lines.append("- %s [%s] %s（owner=%s）" % (r.get("tid"), r.get("status"),
                                                  (r.get("desc") or "")[:160], r.get("owner")))
    lines.append("【经验】total=%s" % exp.get("total"))
    for e in (exp.get("entries") or [])[-6:]:
        lines.append("- %s confidence=%s cue=%s evidence=%s" % (
            e.get("id"), e.get("confidence"), (e.get("cue") or "")[:100],
            (e.get("evidence") or "")[:100]))
    tail = (packet.get("health") or {}).get("tail") or []
    if tail:
        lines += ["【健康卡原文尾段】"] + [str(x) for x in tail[-30:]]
    text = "\n".join(lines)
    return text[:max_chars]


def judge(packet: dict, model: str = DEFAULT_MODEL, live: bool = False,
          out_dir: Path | None = None, llm=None, max_chars: int = 12000) -> dict:
    """dry-run 或真调模型；返回结果 dict（ok/model/elapsed/prompt_len/text/out）。"""
    prompt = build_prompt(packet, max_chars=max_chars)
    res = {"schema": "a0_evidence_judgment/v1",
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "packet_generated_at": packet.get("generated_at"),
           "model": model, "live": bool(live), "prompt_len": len(prompt),
           "boundaries": BOUNDARIES, "ok": False, "text": "", "error": None}
    if not live:
        res["ok"] = True
        res["dry_run"] = True
        res["prompt_head"] = prompt[:600]
        return res
    t0 = time.time()
    try:
        if llm is None:
            sys.path.insert(0, str(BASE / "engine"))
            from llm import make_llm  # noqa: E402
            llm = make_llm(model)
        text, status = llm.chat(_load_persona(), prompt)
        res.update({"ok": True, "text": str(text or ""), "status": status,
                    "elapsed_s": round(time.time() - t0, 1)})
    except Exception as exc:  # noqa: BLE001
        res.update({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc),
                    "elapsed_s": round(time.time() - t0, 1)})
    out = Path(out_dir) if out_dir else DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    jp = out / ("a0_evidence_judgment_%s.json" % ts)
    mp = out / ("a0_evidence_judgment_%s.md" % ts)
    jp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    md = ["# A0 证据包判读（%s）" % res["generated_at"], "",
          "- 证据包生成时间：%s" % res.get("packet_generated_at"),
          "- 模型：`%s`；live=%s；ok=%s；耗时=%ss" % (model, live, res["ok"], res.get("elapsed_s")),
          "", "## 判读草稿（待老田签，未写入任何系统）", "",
          res.get("text") or ("（未产出：%s）" % res.get("error")), "",
          "> 边界：" + " / ".join(BOUNDARIES)]
    mp.write_text("\n".join(md), encoding="utf-8")
    res["out_json"], res["out_md"] = str(jp), str(mp)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A0 证据包判读（默认 dry-run）")
    ap.add_argument("--packet", default=None, help="证据包 JSON；默认取 work/seat_brief 最新")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--live", action="store_true", help="真调模型（默认 dry-run）")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--max-chars", type=int, default=12000)
    args = ap.parse_args(argv)
    pkt_path = Path(args.packet) if args.packet else latest_packet()
    if not pkt_path or not pkt_path.is_file():
        print(json.dumps({"ok": False, "error": "evidence_packet_not_found"}, ensure_ascii=False))
        return 2
    packet = json.loads(pkt_path.read_text(encoding="utf-8"))
    res = judge(packet, model=args.model, live=args.live,
                out_dir=Path(args.out_dir), max_chars=args.max_chars)
    res["packet"] = str(pkt_path)
    print(json.dumps({k: res.get(k) for k in
                      ("ok", "dry_run", "model", "packet", "prompt_len",
                       "elapsed_s", "error", "text", "prompt_head", "out_json", "out_md")},
                     ensure_ascii=False, indent=1)[:4000])
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
