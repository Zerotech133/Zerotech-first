# -*- coding: utf-8 -*-
"""统一发布器 learning_release（发布门强制 · task-132）。

`release --project <pid> --proposals <path> [--apply]`，**默认 dry-run**：

    ① gate publish 预检（learning_gate.run stage=publish）
    ② 校验 proposals 有 approved 卡（review.checkboxes.pass）
    ③ card_merge（dry-run 预览 / --apply 备份+写盘）—— --no-preview，避免旁写
    ④ [apply] learning_digest run
    ⑤ [apply] gate publish --strict 后置门禁
    ⑥ 回执 research/reports/learning_release_<pid>_<date>.{json,md}

任一步失败即停止（stopped_at 记录），不执行后续步骤。**不实际重新合并**（本工具默认 dry-run；
apply 路径测试用 mock，不在本任务真实执行）。

CLI::
    python tools/kb_corpus/learning_release.py release --project <pid> --proposals <json>
    python tools/kb_corpus/learning_release.py release --project <pid> --proposals <json> --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research" / "reports"
S = REPO / "portal" / "static"
CARDS = S / "kb_learning_cards.json"
GATE = REPO / "tools" / "kb_corpus" / "learning_gate.py"
MERGE = REPO / "tools" / "kb_corpus" / "card_merge.py"
DIGEST = REPO / "tools" / "kb_corpus" / "learning_digest.py"
DEFAULT_KB = Path(r"C:\项目\知识库")
DATE = "20261102"
SCHEMA = 1


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cards_snapshot(path: Path = CARDS) -> dict:
    try:
        return {"count": len(load(path).get("cards") or []), "sha256": sha(path)}
    except Exception as exc:  # noqa: BLE001
        return {"count": None, "sha256": None, "error": f"{type(exc).__name__}: {exc}"}


def default_gate_runner(stage: str = "publish") -> dict:
    """执行 learning_gate.run(stage)（进程内导入，避免 stdout 截断）；返回 {ok, fail, rep}。"""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("lr_learning_gate", GATE)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["lr_learning_gate"] = mod
        spec.loader.exec_module(mod)
        rep = mod.run(Path(DEFAULT_KB), stage=stage)
        fail = int((rep.get("summary") or {}).get("fail") or 0)
        return {"ok": fail == 0, "fail": fail,
                "summary": rep.get("summary"), "projects": len(rep.get("projects") or [])}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "fail": None, "error": f"{type(exc).__name__}: {exc}"}


def default_run(cmd: list[str], timeout: int = 300) -> dict:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, cwd=str(REPO))
        return {"rc": proc.returncode, "stdout": proc.stdout or "", "stderr": proc.stderr or ""}
    except Exception as exc:  # noqa: BLE001
        return {"rc": -1, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}


def parse_json_out(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        # 容忍前后打印行：取首个 { 到末尾
        i = text.find("{")
        if i >= 0:
            try:
                return json.loads(text[i:])
            except Exception:  # noqa: BLE001
                return None
    return None


def approved_count(proposals_doc: dict) -> tuple[int, int]:
    cards = proposals_doc.get("cards") or proposals_doc.get("candidates") or proposals_doc.get("proposals") or []
    cards = [c for c in cards if isinstance(c, dict)]
    approved = sum(1 for c in cards
                   if ((c.get("review") or {}).get("checkboxes") or {}).get("pass")
                   or str(c.get("status") or "") == "approved")
    return len(cards), approved


def render_md(rec: dict) -> str:
    lines = [
        f"# learning_release 回执 · {rec['project']}（{rec['date']}）", "",
        f"> 模式：**{'APPLY' if rec['apply'] else 'DRY-RUN'}** · 生成 {rec['generated_at']}",
        f"> proposals：`{rec['proposals']['path']}`（{rec['proposals']['total']} 卡 / approved {rec['proposals']['approved']}）",
        "", "## 步骤结果", "", "| # | 步骤 | 结果 | 详情 |", "|---|---|---|---|"]
    for i, st in enumerate(rec["steps"], 1):
        lines.append(f"| {i} | {st['name']} | {'✅' if st['ok'] else '❌'} | {st.get('detail') or ''} |")
    lines += ["", "## 合并统计（card_merge）", "", "| 项 | 值 |", "|---|---|"]
    for k, v in (rec.get("merge_stats") or {}).items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## 卡池", "", "| 项 | 值 |", "|---|---|",
              f"| 发布前 | {rec['cards']['before']['count']}（sha {str(rec['cards']['before']['sha256'])[:16]}…） |",
              f"| 发布后 | {rec['cards']['after']['count']}（sha {str(rec['cards']['after']['sha256'])[:16]}…） |",
              "", f"**结果**：{'成功' if rec['ok'] else '失败'}（stopped_at={rec.get('stopped_at') or '—'}）", ""]
    if rec.get("rollback"):
        lines += ["## 回滚", "", f"- {rec['rollback']}", ""]
    lines += [f"> {rec['disclaimer']}", ""]
    return "\n".join(lines)


def release(*, project: str, proposals: str | Path, apply: bool = False,
            date: str = DATE, out_dir: str | Path = R,
            gate_fn=None, run_fn=None, digest_fn=None) -> dict:
    """统一发布流程；任一步失败即停。返回回执 payload（并写 json/md）。"""
    gate_fn = gate_fn or default_gate_runner
    run_fn = run_fn or default_run
    out = Path(out_dir)
    before = cards_snapshot()
    rec = {
        "schema_version": SCHEMA, "date": date,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": "tools/kb_corpus/learning_release.py（统一发布器，发布门强制）",
        "project": project, "apply": bool(apply), "mode": "apply" if apply else "dry-run",
        "proposals": {"path": str(proposals), "sha256": sha(Path(proposals)) if Path(proposals).is_file() else None,
                      "total": 0, "approved": 0},
        "steps": [], "merge_stats": {}, "digest": None,
        "cards": {"before": before, "after": None},
        "ok": False, "stopped_at": None, "reason": None,
        "rollback": None,
        "disclaimer": "统一发布器：dry-run 默认；--apply 需人工审核后执行；不构成投资建议。",
    }

    def step(name: str, ok: bool, detail: str = "", **extra) -> bool:
        rec["steps"].append({"name": name, "ok": bool(ok), "detail": detail, **extra})
        if not ok:
            rec["stopped_at"] = name
            rec["reason"] = detail or name
            return False
        return True

    # ① gate publish 预检
    pre = gate_fn("publish")
    if not step("gate_publish_precheck", pre.get("ok", False),
                f"fail={pre.get('fail')} summary={json.dumps(pre.get('summary'), ensure_ascii=False)}",
                gate=pre):
        return _finish(rec, out)
    # ② proposals 校验
    try:
        doc = load(Path(proposals))
        total, approved = approved_count(doc)
        rec["proposals"].update({"total": total, "approved": approved})
        if approved == 0:
            step("validate_approved", False, "proposals 无 approved 卡（review.checkboxes.pass 全 false）")
            return _finish(rec, out)
        step("validate_approved", True, f"{approved}/{total} approved")
    except Exception as exc:  # noqa: BLE001
        step("validate_approved", False, f"读取 proposals 失败：{type(exc).__name__}: {exc}")
        return _finish(rec, out)
    # ③ card_merge（dry-run / --apply），--no-preview 避免旁写
    cmd = [sys.executable, "-X", "utf8", str(MERGE), "merge",
           "--proposals", str(proposals), "--no-preview", "--json"]
    if apply:
        cmd.append("--apply")
    res = run_fn(cmd)
    parsed = parse_json_out(res.get("stdout") or "")
    stats = (parsed or {}).get("stats") or {}
    merge_ok = res.get("rc") == 0 and parsed is not None
    rec["merge_stats"] = stats
    if not step("card_merge" + ("_apply" if apply else "_dry_run"), merge_ok,
                f"rc={res.get('rc')} would_add={stats.get('would_add')} "
                f"resulting={stats.get('resulting_cards')} backup={(parsed or {}).get('backup')}",
                command=cmd, stdout_tail=(res.get("stdout") or "")[-400:],
                stderr_tail=(res.get("stderr") or "")[-300:]):
        return _finish(rec, out)
    if apply:
        # ④ digest
        dres = (digest_fn or (lambda: default_run([sys.executable, "-X", "utf8", str(DIGEST), "run"])))()
        if not step("learning_digest", dres.get("rc") == 0,
                    f"rc={dres.get('rc')}", stdout_tail=(dres.get("stdout") or "")[-200:]):
            return _finish(rec, out)
        # ⑤ gate publish --strict 后置
        post = gate_fn("publish")
        if not step("gate_publish_post_strict", post.get("ok", False),
                    f"fail={post.get('fail')}（后置门禁失败：已合并需人工处理）", gate=post):
            rec["rollback"] = f"若需回滚：从 card_merge 备份恢复 {CARDS}（见回执 merge_stats/backup）"
            return _finish(rec, out)
    rec["ok"] = True
    return _finish(rec, out)


def _finish(rec: dict, out: Path) -> dict:
    rec["cards"]["after"] = cards_snapshot()
    out.mkdir(parents=True, exist_ok=True)
    jp = out / f"learning_release_{rec['project']}_{rec['date']}.json"
    mp = out / f"learning_release_{rec['project']}_{rec['date']}.md"
    rec["receipt"] = {"json": str(jp), "md": str(mp)}
    jp.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    mp.write_text(render_md(rec), encoding="utf-8")
    return rec


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass
    parser = argparse.ArgumentParser(description="统一发布器（发布门强制；默认 dry-run）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    rel = sub.add_parser("release", help="发布：gate→approved→card_merge→digest→gate strict→回执")
    rel.add_argument("--project", required=True, help="项目 id/名称")
    rel.add_argument("--proposals", required=True, help="审核稿 JSON（card_merge 兼容）")
    rel.add_argument("--apply", action="store_true", help="真实合并（默认 dry-run）")
    rel.add_argument("--date", default=DATE)
    rel.add_argument("--out-dir", default=str(R))
    rel.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    rec = release(project=args.project, proposals=args.proposals, apply=args.apply,
                  date=args.date, out_dir=args.out_dir)
    if args.json:
        print(json.dumps({k: v for k, v in rec.items() if k != "receipt"}, ensure_ascii=False, indent=2))
    else:
        print(f"[learning_release] {rec['mode'].upper()} | project={rec['project']} "
              f"| approved={rec['proposals']['approved']} | would_add={rec.get('merge_stats', {}).get('would_add')} "
              f"| ok={rec['ok']} stopped_at={rec.get('stopped_at')}")
        for st in rec["steps"]:
            print(f"  {'✅' if st['ok'] else '❌'} {st['name']}: {st.get('detail') or ''}")
        print(f"  回执：{rec['receipt']['json']}")
    return 0 if rec["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
