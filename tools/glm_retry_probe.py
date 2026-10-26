# -*- coding: utf-8 -*-
"""glm_retry_probe.py — GLM-5.3 额度恢复探测（仓库版，替代临时 work 脚本）。

用途：作为 GlmJudgeRetry 计划任务的执行体，每 30 分钟探测一次：
- 若 `JUDGMENT_READY.txt` 已存在 → 直接跳过（不再调用 API，rc=0）；
- 否则用最新证据包调一次 `glm/glm-5.3`：
  * 成功 → 写 `judgment_<ts>.json|.md` + `JUDGMENT_READY.txt`，rc=0；
  * 仍 429/不可用 → 追加 `retry_log.jsonl`，**rc=3**（= 上游拒跑，不计失败；
    避免审计指出的“rc=0 假绿”）；
- 只读证据包；只写 `work/glm_probe/`；不联网（除 GLM 调用）、不发消息、不写业务。

用法：
  python -X utf8 tools/glm_retry_probe.py
  python -X utf8 tools/glm_retry_probe.py --out C:\\aios\\work\\glm_probe
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _repo_paths import repo_root  # noqa: E402

REPO = repo_root(HERE)
DEFAULT_PACKETS = REPO / "work" / "seat_brief"
DEFAULT_OUT = REPO / "work" / "glm_probe"
MODEL = "glm/glm-5.3"

SYSTEM = (
    "你是数字军团参谋长田丰的判读智能层（GLM-5.3）。基于给定证据包输出判读草稿："
    "一句话结论/关键证据（来源+时间戳）/风险与未覆盖/下一步动作与负责项；"
    "不得编造；不得写入任何系统；不得发消息；末尾标'草稿·待老田签'。"
)


def _chat(user: str):
    """默认 GLM 调用；测试可 monkeypatch 本函数。"""
    from gateway import llm_gateway  # 延迟导入，便于离线测试
    return llm_gateway.chat(binding=MODEL, system=SYSTEM, user=user)


def latest_packet(root: Path | None = None) -> Path | None:
    root = root or DEFAULT_PACKETS
    files = sorted(root.glob("evidence_packet_*.md"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def probe(out_dir: Path | None = None, packets_dir: Path | None = None,
          chat_fn=None) -> int:
    """执行一次探测；返回 rc（0=成功/跳过，3=上游仍不可用，2=无输入）。"""
    out = Path(out_dir or DEFAULT_OUT)
    out.mkdir(parents=True, exist_ok=True)
    ready = out / "JUDGMENT_READY.txt"
    log = out / "retry_log.jsonl"
    if ready.exists():
        print(json.dumps({"ok": True, "skip": "already_ready", "marker": str(ready)},
                         ensure_ascii=False))
        return 0
    packet = latest_packet(Path(packets_dir) if packets_dir else None)
    if not packet:
        print(json.dumps({"ok": False, "error": "no_evidence_packet"}, ensure_ascii=False))
        return 2
    user = packet.read_text(encoding="utf-8", errors="replace")[:12000]
    fn = chat_fn or _chat
    t0 = time.time()
    try:
        env = fn(user)
    except Exception as exc:  # noqa: BLE001
        env = {"ok": False, "error": {"message": "%s: %s" % (type(exc).__name__, exc)}}
    dt = round(time.time() - t0, 1)
    ok = bool((env or {}).get("ok"))
    text = str(((env or {}).get("data") or {}).get("text") or "")
    err = ((env or {}).get("error") or {}).get("message") or None
    ts = time.strftime("%Y%m%d-%H%M%S")
    if ok:
        rec = {"schema": "glm_judgment_sample/v1",
               "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "packet": str(packet), "binding": MODEL, "elapsed_s": dt,
               "envelope": env}
        (out / ("judgment_%s.json" % ts)).write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / ("judgment_%s.md" % ts)).write_text("\n".join([
            "# GLM 判读草稿（glm_retry_probe，%s）" % ts, "",
            "- 证据包：`%s`" % packet, "- 模型：`%s`；耗时 %ss" % (MODEL, dt), "",
            "## 判读草稿（待老田签，未写入任何系统）", "", text or "(空)", "",
            "> 判读单源：a0_agent/GLM-5.3；本件只是证据包样例。"]), encoding="utf-8")
        ready.write_text(ts, encoding="utf-8")
        print(json.dumps({"ok": True, "judgment": str(out / ("judgment_%s.md" % ts)),
                          "elapsed_s": dt, "text_head": text[:300]}, ensure_ascii=False))
        return 0
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            "ok": False, "elapsed_s": dt, "error": err},
                           ensure_ascii=False) + "\n")
    print(json.dumps({"ok": False, "error": err, "elapsed_s": dt,
                      "note": "rc=3 表示探测完成但 GLM 仍不可用（上游拒跑，不计任务失败）"},
                     ensure_ascii=False))
    return 3


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GLM 额度恢复探测（单次）")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--packets", default=str(DEFAULT_PACKETS))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    return probe(out_dir=Path(args.out), packets_dir=Path(args.packets))


if __name__ == "__main__":
    raise SystemExit(main())
