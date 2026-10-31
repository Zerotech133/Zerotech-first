# -*- coding: utf-8 -*-
"""黄忠盲审抽样包生成器（P3-C，2026-11-01，只读生成，不发送/不通知）。

用途：从 `work/huangzhong/acceptance_*.json` 的验收样本中，按 seed **确定性**随机
抽取 1–N 张（默认 2）用于盲审，生成两份独立材料：
  1) 盲审包 md：只含 commit sha / 改动摘要（git show --stat，经模块级 `_run` 可注入）/
     证据路径 / 黄忠六条对应的待审问题清单；**不展示验收结论（verdict）与理由**；
  2) 密封答案 key json：样本=验收单文件、真实 verdict、证据年龄、复核人建议位。
     （默认与盲审包分开两个文件；盲审提交前不得公开 key）

抽样口径：
  · 仅取窗口（--days，默认 7 天，按验收单 generated_at）内的 PASS 样本；
  · 默认排除演练样本（drill）与 legacy(v0，缺 evidence 年龄字段)；可用
    --include-legacy 纳入 legacy；坏 JSON/超窗/非 PASS/重复 sha 计入 skipped；
  · 同 commit.sha 多单只保留最新（按 generated_at，不可解析按文件 mtime）；
  · seed 缺省 = 当日日期 + 固定盐（同一天可复现）；给定 --seed 则完全可复现；
  · 排序键 = sha256("<seed>:<验收单文件名>")，取前 --samples 个（跨 Python 版本稳定）。

安全边界：只读验收单/work 数据；不写 work/；不发送消息、不通知赵云；不带 confirm
写动作。默认落盘到 deliverables（盲审包 + 答案 key 两文件）；--no-write 只打印。

CLI：--json / --samples 2 / --seed / --days 7 / --work-dir / --out-dir
     （默认平台级 deliverables）/ --no-write / --include-legacy。
退出码：0 正常；2 数据错误（work 目录缺失/样本不足/写盘失败——fail-visible，不编造）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]
PATTERN = "acceptance_*.json"
DEFAULT_SAMPLES = 2
DEFAULT_DAYS = 7
DEFAULT_SEED_SALT = "huangzhong-blind-audit-v1"
GIT_TIMEOUT = 60
OUT_PREFIX = "黄忠盲审包"
KEY_PREFIX = "黄忠盲审答案key"

#: 黄忠六条对应的盲审问题清单（不含 verdict 字样，避免泄露结论口径）
CHECKLIST = (
    "① 验收样本量：窗口内验收样本是否达到门槛（≥10 单）？",
    "② 受控拒收演练：是否有受控拒收/退回演练记录且符合要求？",
    "③ 误拒绝率：被退回工单中误拒比例是否 <5%？",
    "④ 漏放：满窗样本中是否存在漏放（放行后出问题）？",
    "⑤ 证据链与年龄：证据链是否完整、基线与红队证据年龄是否 ≤48h？",
    "⑥ 独立抽查：独立抽查结论是否与验收结论一致？",
)


# ------------------------------------------------------------------ 基础工具

def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _parse_ts(raw) -> dt.datetime | None:
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    parsed = None
    try:
        parsed = dt.datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = dt.datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _main_repo() -> Path:
    """主仓根（worktree 内开发时经 git-common-dir 定位；失败退回脚本所在仓根）。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(HERE.parent), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            common = Path(r.stdout.strip())
            if not common.is_absolute():
                common = (HERE.parent / common).resolve()
            return common.parent
    except Exception:  # noqa: BLE001 —— 定位失败退回本仓根
        pass
    return ROOT


def _repo_root(arg=None) -> Path:
    if arg:
        return Path(arg)
    raw = str(os.environ.get("HUANGZHONG_REPO_ROOT") or "").strip()
    return Path(raw) if raw else _main_repo()


def _work_dir(arg=None) -> Path:
    if arg:
        return Path(arg)
    raw = str(os.environ.get("HUANGZHONG_WORK_DIR") or "").strip()
    return Path(raw) if raw else (_repo_root() / "work" / "huangzhong")


def _default_out_dir() -> Path:
    raw = str(os.environ.get("HUANGZHONG_DELIVERABLES_DIR") or "").strip()
    if raw:
        return Path(raw)
    return _main_repo().parent / "deliverables"


def _run(cmd, cwd=None, timeout=None) -> dict:
    """执行子进程（模块级可注入）：失败不抛出，返回 ok=False + error（fail-visible）。"""
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return {"ok": r.returncode == 0, "returncode": r.returncode,
                "stdout": r.stdout or "", "stderr": r.stderr or ""}
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else ""
        return {"ok": False, "returncode": None, "stdout": out,
                "stderr": "超时（>%ss）" % timeout, "error": "timeout"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "returncode": None, "stdout": "",
                "stderr": "%s: %s" % (type(e).__name__, e),
                "error": "%s: %s" % (type(e).__name__, e)}


def _is_legacy(data: dict) -> bool:
    """legacy(v0)：缺 evidence.baseline/redteam 的 age_hours 或 stale 字段。"""
    ev = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    for kind in ("baseline", "redteam"):
        rec = ev.get(kind)
        if not isinstance(rec, dict):
            return True
        if "age_hours" not in rec or "stale" not in rec:
            return True
    return False


def _short_sha(sha) -> str:
    return (sha or "")[:8]


def _seed_fingerprint(seed: str) -> str:
    return hashlib.sha256(str(seed).encode("utf-8")).hexdigest()[:8]


def _sha_of(data: dict):
    commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
    sha = commit.get("sha")
    return sha.strip().lower() if isinstance(sha, str) and sha.strip() else None


def _evidence_paths(data: dict) -> dict:
    ev = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    out = {}
    for kind in ("baseline", "redteam"):
        rec = ev.get(kind) if isinstance(ev.get(kind), dict) else {}
        out[kind] = {"path": rec.get("path"),
                     "generated_at": rec.get("generated_at"),
                     "age_hours": rec.get("age_hours"),
                     "stale": rec.get("stale")}
    return out


# ------------------------------------------------------------------ 读取候选

def _load_candidates(work: Path, days: int, now: dt.datetime,
                     include_legacy: bool = False) -> tuple[list[dict], dict]:
    """扫描验收单 → (候选列表, skipped 计数/明细)。只读。"""
    skipped = {"bad": 0, "not_pass": 0, "drill": 0, "legacy": 0,
               "window": 0, "no_sha": 0, "dup": 0}
    records: list[dict] = []
    for p in sorted(work.glob(PATTERN), key=lambda x: x.name):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            skipped["bad"] += 1
            continue
        if not isinstance(data, dict):
            skipped["bad"] += 1
            continue
        if data.get("verdict") != "PASS":
            skipped["not_pass"] += 1
            continue
        if data.get("drill") is True or data.get("mode") == "drill":
            skipped["drill"] += 1
            continue
        gen = _parse_ts(data.get("generated_at"))
        if gen is None or gen < now - dt.timedelta(days=days):
            skipped["window"] += 1
            continue
        if not include_legacy and _is_legacy(data):
            skipped["legacy"] += 1
            continue
        sha = _sha_of(data)
        if not sha:
            skipped["no_sha"] += 1
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        records.append({"file": p.name, "path": str(p), "sha": sha,
                        "generated_at": data.get("generated_at"),
                        "gen_ts": gen, "mtime": mtime, "data": data})
    # 同 sha 去重保留最新（generated_at 优先，否则文件 mtime）
    by_sha: dict[str, list[dict]] = {}
    for rec in records:
        by_sha.setdefault(rec["sha"], []).append(rec)
    candidates: list[dict] = []
    for sha, recs in by_sha.items():
        if len(recs) > 1:
            skipped["dup"] += len(recs) - 1
            recs = sorted(recs, key=lambda r: (r["gen_ts"], r["mtime"]))
            candidates.append(recs[-1])
        else:
            candidates.append(recs[0])
    candidates.sort(key=lambda r: r["file"])
    return candidates, skipped


# ------------------------------------------------------------------ git 改动摘要

_STAT_RE = re.compile(
    r"(\d+) files? changed"
    r"(?:, (\d+) insertions?\(\+\))?"
    r"(?:, (\d+) deletions?\(-\))?")


def _git_stat(sha: str, repo: Path, runner) -> dict:
    """跑 git show --stat（经 runner/_run 注入）；失败 fail-visible 不编造。"""
    cmd = ["git", "-C", str(repo), "-c", "core.quotepath=false",
           "--no-pager", "show", "--stat", "--oneline", sha]
    try:
        r = runner(cmd, cwd=str(repo), timeout=GIT_TIMEOUT)
    except Exception as e:  # noqa: BLE001 —— 外部 runner 异常也 fail-visible
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:150]),
                "summary": None, "files": [], "raw_tail": None}
    if not r.get("ok"):
        return {"ok": False,
                "error": (r.get("stderr") or "git show 非零退出").strip()[-200:],
                "summary": None, "files": [], "raw_tail": None}
    raw = (r.get("stdout") or "").replace("\r\n", "\n")
    lines = [ln.rstrip() for ln in raw.splitlines() if ln.strip()]
    summary = None
    files: list[str] = []
    for ln in lines:
        m = _STAT_RE.search(ln)
        if m:
            summary = {"files_changed": int(m.group(1)),
                       "insertions": int(m.group(2) or 0),
                       "deletions": int(m.group(3) or 0)}
            continue
        if "|" in ln or "=>" in ln:  # --stat 文件行： "<path> | <n> +++---"
            name = ln.split("|", 1)[0].strip()
            if name:
                files.append(name)
    files = files[:10]
    return {"ok": True, "summary": summary, "files": files,
            "raw_tail": raw.strip()[-600:] or None}


# ------------------------------------------------------------------ 盲审材料

def _pick(candidates: list[dict], samples: int, seed: str) -> list[dict]:
    """确定性抽样：sha256("<seed>:<文件名>") 排序取前 N（跨版本稳定）。"""
    def rank(rec):
        return hashlib.sha256(("%s:%s" % (seed, rec["file"]))
                              .encode("utf-8")).hexdigest()
    ordered = sorted(candidates, key=rank)
    return ordered[:samples]


def _build_blind_md(rep: dict) -> str:
    lines = [
        "# 盲审抽样包（P3-C · 盲审材料，隐藏验收结论）", "",
        "- 生成时间：%s" % rep["generated_at"],
        "- 抽样窗口：近 %d 天；样本数：%d（候选 %d）"
        % (rep["days"], rep["samples_actual"], rep["candidates"]),
        "- seed 指纹：`%s`（同 seed 可复现；seed 原文不在本包公开）"
        % rep["seed_fingerprint"],
        "- 盲审说明：本包**不含验收结论与理由**；请独立复核下列材料后填写结论建议，"
        "与密封答案 key 对照（key 由验收方密封保管，盲审提交前不得公开）。",
        "- 数据范围：`%s`（只读；本包不引用验收单文件本身）"
        % rep["work_dir"],
        "",
        "## 待审问题清单（黄忠六条对应）", "",
    ]
    for item in CHECKLIST:
        lines.append("- [ ] " + item)
    lines.append("")
    for sample in rep["samples"]:
        lines += [
            "## 样本 %s" % sample["label"], "",
            "- Commit：`%s`（完整 sha：`%s`）"
            % (sample["short_sha"], sample["sha"]),
            "- 改动摘要（`git show --stat`）：", "",
        ]
        stat = sample.get("stat") or {}
        if stat.get("ok"):
            sm = stat.get("summary") or {}
            lines.append("  ```text")
            lines.append("  %d files changed, %d insertions(+), %d deletions(-)"
                         % (sm.get("files_changed") or 0,
                            sm.get("insertions") or 0,
                            sm.get("deletions") or 0))
            for name in stat.get("files") or []:
                lines.append("  " + name)
            lines += ["  ```", ""]
        else:
            lines += ["> 改动摘要不可读（fail-visible，未编造）：%s"
                      % (stat.get("error") or "unknown"), ""]
        ev = sample.get("evidence") or {}
        lines += [
            "- 证据路径：",
            "  - 测试基线：`%s`（generated_at=%s；age=%s h；stale=%s）"
            % ((ev.get("baseline") or {}).get("path") or "-",
               (ev.get("baseline") or {}).get("generated_at") or "-",
               (ev.get("baseline") or {}).get("age_hours"),
               (ev.get("baseline") or {}).get("stale")),
            "  - 红队报告：`%s`（generated_at=%s；age=%s h；stale=%s）"
            % ((ev.get("redteam") or {}).get("path") or "-",
               (ev.get("redteam") or {}).get("generated_at") or "-",
               (ev.get("redteam") or {}).get("age_hours"),
               (ev.get("redteam") or {}).get("stale")),
            "",
            "- 待审问题清单（黄忠六条对应）：",
        ]
        for item in CHECKLIST:
            lines.append("  - [ ] " + item)
        lines += ["", "- 复核结论建议（人工填写）：通过 / 退回 / 存疑；"
                      "理由：________________", ""]
    lines += ["> 盲审包自动生成：只读工具，不发送、不通知任何席位；"
              "结论以密封 key 对照为准。", ""]
    return "\n".join(lines)


def _build_key(rep: dict) -> dict:
    return {
        "sealed": True,
        "note": "密封答案：含真实验收结论（verdict）与证据年龄；盲审提交前不得公开",
        "generated_at": rep["generated_at"],
        "seed": rep["seed"],
        "seed_source": rep["seed_source"],
        "days": rep["days"],
        "work_dir": rep["work_dir"],
        "samples": [
            {"label": s["label"],
             "acceptance_file": s["acceptance_file"],
             "commit_sha": s["sha"],
             "short_sha": s["short_sha"],
             "verdict": s["verdict"],
             "generated_at": s["generated_at"],
             "evidence_age": {"baseline": ((s.get("evidence") or {})
                                           .get("baseline") or {}).get("age_hours"),
                              "redteam": ((s.get("evidence") or {})
                                          .get("redteam") or {}).get("age_hours")},
             "stale": {"baseline": ((s.get("evidence") or {})
                                    .get("baseline") or {}).get("stale"),
                       "redteam": ((s.get("evidence") or {})
                                   .get("redteam") or {}).get("stale")},
             "suggested_reviewer_slot": ""}
            for s in rep["samples"]
        ],
    }


# ------------------------------------------------------------------ 主流程

def build(samples: int = DEFAULT_SAMPLES, seed=None, days: int = DEFAULT_DAYS,
          work_dir=None, out_dir=None, write: bool = True,
          include_legacy: bool = False, repo_root=None,
          runner=None, now: dt.datetime | None = None) -> dict:
    """生成盲审抽样包（默认落盘；write=False 只返回内容，不写盘）。"""
    now = now or _now()
    run_fn = runner or _run
    wd = _work_dir(work_dir)
    od = Path(out_dir) if out_dir else _default_out_dir()
    repo = _repo_root(repo_root)
    samples = max(1, int(samples))
    days = max(1, int(days))
    seed_source = "cli" if seed else "default"
    seed = str(seed) if seed else ("%s:%s" % (DEFAULT_SEED_SALT,
                                              now.date().isoformat()))
    fp = _seed_fingerprint(seed)
    rep: dict = {
        "ok": True,
        "generated_at": now.isoformat(timespec="seconds"),
        "mode": "write" if write else "no_write",
        "read_only": True,
        "work_dir": str(wd), "repo_root": str(repo), "out_dir": str(od),
        "days": days, "samples_requested": samples, "samples_actual": 0,
        "seed": seed, "seed_source": seed_source, "seed_fingerprint": fp,
        "candidates": 0, "skipped": {}, "insufficient": False,
        "warnings": [], "samples": [],
        "blind_md": None, "sealed_key": None,
        "blind_md_path": None, "key_path": None, "written": False,
    }
    if not wd.is_dir():
        rep.update(ok=False, error="work 目录不存在：%s" % wd)
        return rep
    candidates, skipped = _load_candidates(wd, days, now, include_legacy)
    rep["skipped"] = skipped
    rep["candidates"] = len(candidates)
    if not candidates:
        rep.update(ok=False, error=(
            "窗口内无符合条件的验收样本（需 PASS/近 %d 天/非演练%s，"
            "skip=%s）" % (days, "" if include_legacy else "/非 legacy",
                           json.dumps(skipped, ensure_ascii=False))))
        return rep
    if len(candidates) < samples:
        rep.update(ok=False, insufficient=True, error=(
            "样本不足：需要 %d 个，可用 %d 个（fail-visible，未生成半成品包）"
            % (samples, len(candidates))))
        return rep
    picked = _pick(candidates, samples, seed)
    sample_rows = []
    for i, rec in enumerate(picked):
        data = rec["data"]
        stat = _git_stat(rec["sha"], repo, run_fn)
        if not stat.get("ok"):
            rep["warnings"].append("样本 %s 的改动摘要不可读：%s"
                                   % (chr(65 + i), stat.get("error")))
        sample_rows.append({
            "label": chr(65 + i),
            "acceptance_file": rec["file"],
            "sha": rec["sha"],
            "short_sha": _short_sha(rec["sha"]),
            "generated_at": rec["generated_at"],
            "verdict": data.get("verdict"),
            "evidence": _evidence_paths(data),
            "stat": stat,
        })
    rep["samples"] = sample_rows
    rep["samples_actual"] = len(sample_rows)
    stamp = "%s_%s_%s" % (now.strftime("%Y-%m-%d"), OUT_PREFIX, fp)
    rep["blind_md_path"] = str(od / (stamp + ".md"))
    rep["key_path"] = str(od / ("%s_%s_%s.json"
                                % (now.strftime("%Y-%m-%d"), KEY_PREFIX, fp)))
    rep["blind_md"] = _build_blind_md(rep)
    rep["sealed_key"] = _build_key(rep)
    if not write:
        rep["written"] = False
        return rep
    try:
        od.mkdir(parents=True, exist_ok=True)
        Path(rep["blind_md_path"]).write_text(rep["blind_md"], encoding="utf-8")
        Path(rep["key_path"]).write_text(
            json.dumps(rep["sealed_key"], ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
        rep["written"] = True
    except OSError as e:
        rep.update(ok=False, written=False,
                   error="写盘失败：%s: %s" % (type(e).__name__, e))
    return rep


def _positive_int(text: str) -> int:
    try:
        v = int(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("需要正整数")
    if v < 1:
        raise argparse.ArgumentTypeError("必须 ≥1")
    return v


def _summary_line(rep: dict) -> str:
    if not rep.get("ok"):
        return "盲审抽样：失败 → %s" % rep.get("error", "unknown")
    labels = ["%s=%s" % (s["label"], s["short_sha"]) for s in rep["samples"]]
    return ("盲审抽样：候选 %d → 抽样 %d（%s）｜seed指纹=%s（%s）｜"
            "written=%s｜包=%s｜key=%s"
            % (rep["candidates"], rep["samples_actual"], ", ".join(labels),
               rep["seed_fingerprint"], rep["seed_source"], rep["written"],
               rep.get("blind_md_path") or "-", rep.get("key_path") or "-"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="黄忠盲审抽样包生成器（只读生成；隐藏验收结论 + 密封答案 key，P3-C）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--samples", type=_positive_int, default=DEFAULT_SAMPLES,
                    help="抽样数量（默认 2，建议 1–2）")
    ap.add_argument("--seed", default="", help="确定性随机 seed（缺省=当日+固定盐）")
    ap.add_argument("--days", type=_positive_int, default=DEFAULT_DAYS,
                    help="抽样窗口天数（默认 7）")
    ap.add_argument("--work-dir", default="",
                    help="数据目录（默认 <主仓>/work/huangzhong；env HUANGZHONG_WORK_DIR）")
    ap.add_argument("--out-dir", default="",
                    help="输出目录（默认平台级 deliverables；env HUANGZHONG_DELIVERABLES_DIR）")
    ap.add_argument("--no-write", action="store_true",
                    help="只打印/返回内容，不落盘（只读冒烟用）")
    ap.add_argument("--include-legacy", action="store_true",
                    help="纳入 legacy(v0) 验收单（默认排除）")
    args = ap.parse_args(argv)
    try:
        rep = build(samples=args.samples, seed=(args.seed or None),
                    days=args.days, work_dir=(args.work_dir or None),
                    out_dir=(args.out_dir or None), write=not args.no_write,
                    include_legacy=args.include_legacy)
    except Exception as e:  # noqa: BLE001 —— 内部错误也 fail-visible（rc2）
        rep = {"ok": False, "error": "内部错误：%s: %s" % (type(e).__name__, e),
               "samples": [], "written": False}
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json
          else _summary_line(rep))
    return 0 if rep.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
