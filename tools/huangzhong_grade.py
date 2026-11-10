# -*- coding: utf-8 -*-
"""黄忠 W1 四件套自动批改（Rubric 100 分，≥80 通过）。

输入：提交目录（默认 docs/plans/2026-10-28_黄忠W1提交模板/ 或黄忠自建目录）
约定文件：
  criteria_*.md（≥3）     判据（每条含 证据/命令/通过线）
  gate_review_*.md（≥2）  七检解读（含七检项名）
  rollback_*.md（≥2）     回滚点（含 备份或 git revert + 重启 + 验证）
  self_review.md          自评（含分数、"问题"≥3）
输出：work/huangzhong/grade_<ts>.json/md；不修改提交文件（只读）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE_CANDIDATES = (
    ROOT / "docs" / "plans" / "2026-10-28_黄忠W1提交模板",
    ROOT / "docs" / "plans" / "_archive_20261110" / "2026-10-28_黄忠W1提交模板",
)
DEFAULT_DIR = next((p for p in _TEMPLATE_CANDIDATES if p.is_dir()), _TEMPLATE_CANDIDATES[0])
OUT = ROOT / "work" / "huangzhong"

GATE_ITEMS = ("compileall", "ports", "secrets", "names", "capabilities",
              "evals", "tests")


def _files(d: Path, pattern: str) -> list[Path]:
    return sorted(d.glob(pattern))


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _score_criteria(files: list[Path]) -> tuple[int, list[str]]:
    notes = []
    if len(files) < 3:
        notes.append("判据数量不足 3 份（实得 %d）" % len(files))
    has_ev = sum(1 for f in files if re.search(r"证据|路径|命令", _read(f)))
    has_line = sum(1 for f in files if re.search(r"通过线|阈值|≥|<=|ok=", _read(f)))
    score = 0
    if len(files) >= 3:
        score += 10
    score += min(8, has_ev * 3)          # 证据链
    score += min(7, has_line * 2)        # 可测性
    notes.append("含证据/命令的判据 %d 份；含通过线的 %d 份" % (has_ev, has_line))
    return min(25, score), notes


def _score_gate(files: list[Path]) -> tuple[int, list[str]]:
    notes = []
    if len(files) < 2:
        notes.append("七检解读不足 2 份（实得 %d）" % len(files))
    mentions = 0
    for f in files:
        t = _read(f)
        mentions += sum(1 for g in GATE_ITEMS if g in t)
    score = 0
    if len(files) >= 2:
        score += 10
    score += min(15, mentions)
    notes.append("七检项提及次数 %d（满分需覆盖 7 项以上）" % mentions)
    return min(25, score), notes


def _score_rollback(files: list[Path]) -> tuple[int, list[str]]:
    notes = []
    if len(files) < 2:
        notes.append("回滚点不足 2 个（实得 %d）" % len(files))
    complete = 0
    for f in files:
        t = _read(f)
        has_revert = bool(re.search(r"git revert|备份|恢复备份|Copy-Item", t))
        has_restart = bool(re.search(r"重启|Start-ScheduledTask|RESTART", t))
        has_verify = bool(re.search(r"验证|检查|health|/api/", t))
        if has_revert and has_restart and has_verify:
            complete += 1
    score = (10 if len(files) >= 2 else 0) + min(15, complete * 8)
    notes.append("回滚三步完整（备份/revert+重启+验证）的 %d/2" % complete)
    return min(25, score), notes


def _score_self(f: Path | None) -> tuple[int, list[str]]:
    if f is None:
        return 0, ["缺少 self_review.md"]
    t = _read(f)
    notes = []
    has_score = bool(re.search(r"自评|分数|\d{2,3}\s*分", t))
    q = len(re.findall(r"^[-*]\s*问题|^\d+\.\s*问题", t, re.M))
    score = (10 if has_score else 0) + min(15, q * 5)
    notes.append("自评分数=%s；问题条数=%d" % ("有" if has_score else "无", q))
    if q < 3:
        notes.append("'问题' 少于 3 条")
    return min(25, score), notes


def grade(submit_dir: Path | None = None, out_dir: Path | None = None) -> dict:
    d = Path(submit_dir) if submit_dir else DEFAULT_DIR
    out = Path(out_dir) if out_dir else OUT
    criteria = _files(d, "criteria_*.md")
    gates = _files(d, "gate_review_*.md")
    rollbacks = _files(d, "rollback_*.md")
    self_file = next(iter(_files(d, "self_review.md")), None)
    dims = {}
    for name, (score, notes) in {
        "可测性": _score_criteria(criteria),
        "证据链": _score_criteria(criteria),
        "七检解读": _score_gate(gates),
        "回滚完整": _score_rollback(rollbacks),
        "自评": _score_self(self_file),
    }.items():
        dims[name] = {"score": score, "notes": notes}
    total = sum(v["score"] for v in dims.values())
    # 归一化到 100：可测性/证据链来自同一判据，权重各 25；七检 25；回滚 25；自评作为加分/扣分项
    core = (dims["可测性"]["score"] + dims["证据链"]["score"]
            + dims["七检解读"]["score"] + dims["回滚完整"]["score"])
    bonus = dims["自评"]["score"] // 5  # 0-5 分加分
    total = min(100, core + bonus)
    # 模板/占位符识别：含 __ 或 填写 视为未完成提交，最多 60 分（防空模板过关）
    all_text = "".join(_read(f) for f in (criteria + gates + rollbacks
                                          + ([self_file] if self_file else [])))
    placeholder = ("__" in all_text) or ("填写" in all_text) or ("（示例）" in all_text)
    if placeholder:
        total = min(total, 60)
    passed = total >= 80
    rep = {
        "ok": True,
        "placeholder_detected": placeholder,
        "submit_dir": str(d),
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": {"criteria": len(criteria), "gate_review": len(gates),
                  "rollback": len(rollbacks),
                  "self_review": bool(self_file)},
        "dimensions": dims,
        "total": total,
        "pass": passed,
        "rubric": "可测性25+证据链25+七检25+回滚25 + 自评加分0-5；≥80 通过",
        "suggestions": [n for v in dims.values() for n in v["notes"]
                        if not n.startswith("含") or True],
        "read_only": True,
    }
    if placeholder:
        rep["suggestions"].append("检测到模板/占位符（__/填写/示例）：按未完成提交计，最高 60 分")
    out.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    jp = out / ("grade_%s.json" % ts)
    jp.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    md = ["# 黄忠 W1 批改单", "",
          "- 提交目录：%s" % d,
          "- 总分：**%d/100** → %s" % (total, "通过" if passed else "未通过"),
          "- 文件：判据 %d / 七检 %d / 回滚 %d / 自评 %s"
          % (len(criteria), len(gates), len(rollbacks), bool(self_file)), "",
          "## 分项"]
    for name, v in dims.items():
        md.append("- %s：%d 分（%s）" % (name, v["score"], "；".join(v["notes"])))
    md += ["", "## Rubric", "- " + rep["rubric"],
           "", "## 改进建议", *["- " + s for s in rep["suggestions"][:10]]]
    mp = out / ("grade_%s.md" % ts)
    mp.write_text("\n".join(md), encoding="utf-8")
    rep["report_json"] = str(jp)
    rep["report_md"] = str(mp)
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="黄忠 W1 批改")
    ap.add_argument("--dir", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = grade(Path(args.dir) if args.dir else None)
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json
          else "总分 %d/100 → %s（%s）" % (rep["total"],
                                          "通过" if rep["pass"] else "未通过",
                                          rep["report_md"]))
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
