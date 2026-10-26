# -*- coding: utf-8 -*-
"""glm_retry_probe.py — GLM-5.3 额度探测（两阶段，外发最小化；审计 B M-03/S-01 收口）。

阶段 1（默认，计划任务用）：只发**中性 ping**（不含证据包/内部数据）探测额度；
  - 成功 → 写 `GLM_AVAILABLE.txt`（时间戳），**不发送任何证据内容**，rc=0；
  - 仍 429/不可用 → 追加 `retry_log.jsonl`，rc=3（上游拒跑，不计任务失败）。
阶段 2（显式 `--with-evidence`，仅在外发追认/人工批准后使用）：
  - ping 成功后再读最新证据包，调 GLM 产出判读草稿 → `judgment_<ts>.json|.md` +
    `JUDGMENT_READY.txt`，rc=0；失败 rc=3。

边界：只读证据包；只写 `work/glm_probe/`；只有 GLM 调用出网；不发消息、不写业务。
用法：
  python -X utf8 tools/glm_retry_probe.py                      # 仅 ping（默认）
  python -X utf8 tools/glm_retry_probe.py --with-evidence      # 发证据判读（需批准）
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
AGENT_PLATFORM = REPO / "agent_platform"
if str(AGENT_PLATFORM) not in sys.path:
    # S4U/计划任务环境下没有该路径（cwd=system32）；GLM 网关在 agent_platform/gateway。
    sys.path.insert(0, str(AGENT_PLATFORM))
DEFAULT_PACKETS = REPO / "work" / "seat_brief"
DEFAULT_OUT = REPO / "work" / "glm_probe"
MODEL = "glm/glm-5.3"

PING_USER = "只回复 OK 两个字；不要调用工具、不要读取文件、不要引用任何内部数据。"
JUDGE_SYSTEM = (
    "你是数字军团参谋长田丰的判读智能层（GLM-5.3）。基于给定证据包输出判读草稿："
    "一句话结论/关键证据（来源+时间戳）/风险与未覆盖/下一步动作与负责项；"
    "不得编造；不得写入任何系统；不得发消息；末尾标'草稿·待老田签'。"
)


def _chat(user: str, system: str | None = None):
    """默认 GLM 调用；测试可 monkeypatch 本函数。"""
    from gateway import llm_gateway  # 延迟导入，便于离线测试
    return llm_gateway.chat(binding=MODEL, system=system or PING_USER, user=user)


def latest_packet(root: Path | None = None) -> Path | None:
    root = root or DEFAULT_PACKETS
    files = sorted(root.glob("evidence_packet_*.md"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _log(out: Path, rec: dict) -> None:
    with (out / "retry_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def probe(out_dir: Path | None = None, packets_dir: Path | None = None,
          chat_fn=None, with_evidence: bool = False) -> int:
    """一次探测：默认只 ping；with_evidence=True 才发送证据包。"""
    out = Path(out_dir or DEFAULT_OUT)
    out.mkdir(parents=True, exist_ok=True)
    fn = chat_fn or _chat
    if (out / "JUDGMENT_READY.txt").exists():
        print(json.dumps({"ok": True, "skip": "already_ready"}, ensure_ascii=False))
        return 0
    t0 = time.time()
    try:
        ping = fn(PING_USER)
    except Exception as exc:  # noqa: BLE001
        ping = {"ok": False, "error": {"message": "%s: %s" % (type(exc).__name__, exc)}}
    dt = round(time.time() - t0, 1)
    ping_ok = bool((ping or {}).get("ok"))
    err = ((ping or {}).get("error") or {}).get("message") or None
    if not ping_ok:
        _log(out, {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "mode": "ping",
                   "ok": False, "elapsed_s": dt, "error": err})
        print(json.dumps({"ok": False, "mode": "ping", "error": err, "elapsed_s": dt,
                          "note": "rc=3=上游拒跑（未发送证据内容）"}, ensure_ascii=False))
        return 3
    (out / "GLM_AVAILABLE.txt").write_text(
        time.strftime("%Y-%m-%dT%H:%M:%S%z"), encoding="utf-8")
    # 外发追认（老田 2026-10-26 22:4x，追踪单 §5）后允许：批准标记存在时，
    # 计划任务默认动作在 ping 成功后自动发一次证据判读；无标记仍只 ping。
    approved = with_evidence or (out / "EVIDENCE_APPROVED.txt").exists()
    if not approved:
        print(json.dumps({"ok": True, "mode": "ping", "available": True,
                          "note": "仅探测，未发送证据；发判读需 --with-evidence 或 EVIDENCE_APPROVED.txt"},
                         ensure_ascii=False))
        return 0

    packet = latest_packet(Path(packets_dir) if packets_dir else None)
    if not packet:
        print(json.dumps({"ok": False, "mode": "evidence", "error": "no_evidence_packet"},
                         ensure_ascii=False))
        return 2
    user = packet.read_text(encoding="utf-8", errors="replace")[:12000]
    t1 = time.time()
    try:
        env = fn(user)
    except Exception as exc:  # noqa: BLE001
        env = {"ok": False, "error": {"message": "%s: %s" % (type(exc).__name__, exc)}}
    dt1 = round(time.time() - t1, 1)
    ok = bool((env or {}).get("ok"))
    text = str(((env or {}).get("data") or {}).get("text") or "")
    err1 = ((env or {}).get("error") or {}).get("message") or None
    if not ok:
        _log(out, {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "mode": "evidence",
                   "ok": False, "elapsed_s": dt1, "error": err1,
                   "packet": str(packet)})
        print(json.dumps({"ok": False, "mode": "evidence", "error": err1,
                          "elapsed_s": dt1}, ensure_ascii=False))
        return 3
    ts = time.strftime("%Y%m%d-%H%M%S")
    rec = {"schema": "glm_judgment_sample/v1",
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "packet": str(packet), "binding": MODEL, "elapsed_s": dt1, "envelope": env}
    (out / ("judgment_%s.json" % ts)).write_text(
        json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / ("judgment_%s.md" % ts)).write_text("\n".join([
        "# GLM 判读草稿（glm_retry_probe，%s）" % ts, "",
        "- 证据包：`%s`" % packet, "- 模型：`%s`；耗时 %ss" % (MODEL, dt1), "",
        "## 判读草稿（待老田签，未写入任何系统）", "", text or "(空)", "",
        "> 判读单源：a0_agent/GLM-5.3；本件只是证据包样例。"]), encoding="utf-8")
    (out / "JUDGMENT_READY.txt").write_text(ts, encoding="utf-8")
    print(json.dumps({"ok": True, "mode": "evidence",
                      "judgment": str(out / ("judgment_%s.md" % ts)),
                      "elapsed_s": dt1}, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GLM 额度探测（默认仅 ping；证据判读需显式开关）")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--packets", default=str(DEFAULT_PACKETS))
    ap.add_argument("--with-evidence", action="store_true",
                    help="ping 成功后发送最新证据包做判读（外发追认/人工批准后使用）")
    args = ap.parse_args(argv)
    return probe(out_dir=Path(args.out), packets_dir=Path(args.packets),
                 with_evidence=args.with_evidence)


if __name__ == "__main__":
    raise SystemExit(main())
