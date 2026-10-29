# -*- coding: utf-8 -*-
"""黄忠 · 验收守门官 v1（观察模式，**不阻断 merge**）。

输入：一个 commit（默认 HEAD）+ 证据（测试基线、红队报告）。
输出：PASS / REJECT / OBSERVE 验收单（work/huangzhong/acceptance_<sha8>.json/md）。

证据口径（2026-10-30 老田 D3/A 拍板，对应决策包 R2-A）：
  · 来源：默认读「全局最新」证据；可用 --baseline <path> / --redteam <file|dir>
    显式指定 commit 级证据（验收单 evidence_scope = explicit / default_latest / mixed）；
  · 年龄：以证据 generated_at 计龄；生成时间缺失/不可解析时回退文件 mtime 计龄
    （age_source 标记），年龄 > --max-evidence-age-hours（默认 48h）即「超龄」；
  · 判定：基线 failed>0/error>0、红队 pass_rate<0.8 → REJECT（只报告，不阻断）；
    证据缺失/不可读/超龄 → OBSERVE（不拿旧证据判 PASS）；全部满足且证据新鲜 → PASS；
    commit 不可读 → OBSERVE；可读证据出现硬失败信号时 REJECT 优先于 OBSERVE。
边界：只读；不写业务代码、不改 org/bundle、不代批；最终准入由老田/merge 门决定。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "agent_platform" / "data" / "test_baseline" / "latest.json"
REDTEAM_DIR = ROOT / "work" / "p1" / "redteam"
OUT = ROOT / "work" / "huangzhong"
MIN_REDTEAM_RATE = 0.8
MAX_EVIDENCE_AGE_HOURS = 48.0


def _run(cmd: list[str], cwd: Path | None = None, runner=None) -> dict:
    fn = runner or subprocess.run
    try:
        r = fn(cmd, cwd=str(cwd or ROOT), capture_output=True, text=True,
               encoding="utf-8", errors="replace", timeout=60)
        return {"ok": getattr(r, "returncode", 1) == 0,
                "stdout": getattr(r, "stdout", "") or "",
                "stderr": getattr(r, "stderr", "") or ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stdout": "", "stderr": "%s: %s" % (type(e).__name__, e)}


def _commit_info(sha: str, runner=None) -> dict:
    r = _run(["git", "show", "-s", "--format=%H%n%an%n%ci%n%s", sha], runner=runner)
    if not r["ok"]:
        return {"ok": False, "error": "git_show_failed", "detail": r["stderr"][-200:]}
    lines = r["stdout"].strip().splitlines()
    return {"ok": True, "sha": lines[0], "author": lines[1] if len(lines) > 1 else "",
            "date": lines[2] if len(lines) > 2 else "",
            "subject": lines[3] if len(lines) > 3 else ""}


def _now() -> dt.datetime:
    """当前时间（UTC，带时区）；测试可 monkeypatch 以获得确定年龄。"""
    return dt.datetime.now(dt.timezone.utc)


def _parse_ts(raw) -> dt.datetime | None:
    """解析证据 generated_at：ISO8601（含 Z/偏移）、常见空格格式、epoch 秒。"""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(raw), tz=dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
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
    if parsed.tzinfo is None:  # 无时区按本机时区（仓库环境为 Asia/Shanghai）解释
        parsed = parsed.astimezone()
    return parsed


def _freshness(path: str | Path, generated_at_raw, max_age_hours: float,
               now: dt.datetime | None = None) -> dict:
    """证据新鲜度：返回 generated_at/age_hours/age_source/stale/max_age_hours。

    generated_at 不可解析时回退文件 mtime 计龄（age_source=mtime）；两者都取不到
    → age_hours=None、stale=None（年龄不可判定，由判定层转 OBSERVE）。
    """
    now = now or _now()
    parsed = _parse_ts(generated_at_raw)
    age_source = "generated_at"
    if parsed is None:
        try:
            parsed = dt.datetime.fromtimestamp(Path(path).stat().st_mtime,
                                               tz=dt.timezone.utc)
            age_source = "mtime"
        except OSError:
            return {"generated_at": generated_at_raw, "age_hours": None,
                    "age_source": None, "stale": None,
                    "max_age_hours": float(max_age_hours)}
    age = round((now - parsed).total_seconds() / 3600.0, 2)
    if age < 0:  # 证据生成时间晚于本机时钟（时钟漂移）→ 按 0 处理，不误判超龄
        age = 0.0
    return {"generated_at": generated_at_raw,
            "generated_at_parsed": parsed.isoformat(timespec="seconds"),
            "age_hours": age, "age_source": age_source,
            "stale": age > float(max_age_hours),
            "max_age_hours": float(max_age_hours)}


def _load_evidence(kind: str, path: str | Path, source: str,
                   max_age_hours: float, now: dt.datetime | None = None) -> dict:
    """读一份证据（baseline/redteam）→ 证据对象（含来源/年龄/超龄标记）。

    ok=False 时 error 为错误码（*_missing / *_unreadable）；ok=True 时 baseline 的
    error 字段沿用旧口径 = 基线 error 数，redteam 的 failed 同理。
    """
    p = Path(path)
    ev: dict = {"ok": False, "kind": kind, "path": str(p), "source": source,
                "generated_at": None, "age_hours": None, "age_source": None,
                "stale": None, "max_age_hours": float(max_age_hours),
                "error": None}
    if kind == "redteam" and p.is_dir():
        try:
            files = sorted(p.glob("redteam_*.json"),
                           key=lambda x: x.stat().st_mtime)
        except OSError as e:
            ev["error"] = "redteam_unreadable"
            ev["detail"] = "%s: %s" % (type(e).__name__, e)
            return ev
        if not files:
            ev["error"] = "redteam_missing"
            ev["detail"] = "目录下无 redteam_*.json"
            return ev
        p = files[-1]
        ev["path"] = str(p)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        ev["error"] = "baseline_missing" if kind == "baseline" else "redteam_missing"
        return ev
    except OSError as e:
        ev["error"] = "baseline_unreadable" if kind == "baseline" else "redteam_unreadable"
        ev["detail"] = "%s: %s" % (type(e).__name__, e)
        return ev
    try:
        data = json.loads(text)
    except ValueError as e:
        ev["error"] = "baseline_unreadable" if kind == "baseline" else "redteam_unreadable"
        ev["detail"] = "JSON 解析失败: %s" % e
        return ev
    if not isinstance(data, dict):
        ev["error"] = "baseline_unreadable" if kind == "baseline" else "redteam_unreadable"
        ev["detail"] = "JSON 顶层不是对象"
        return ev
    ev.update(_freshness(ev["path"], data.get("generated_at"), max_age_hours, now=now))
    if kind == "baseline":
        summary = data.get("summary")
        if not isinstance(summary, dict):
            ev["error"] = "baseline_unreadable"
            ev["detail"] = "缺少 summary 字段"
            return ev
        ev.update({"ok": True, "passed": summary.get("passed"),
                   "failed": summary.get("failed"),
                   "error": summary.get("error"),
                   "summary": summary})
    else:
        rate = data.get("pass_rate")
        total, passed = data.get("total"), data.get("passed")
        derived = False
        if rate is None and isinstance(total, (int, float)) and total:
            rate, derived = (passed or 0) / float(total), True
        ev.update({"ok": True, "total": total, "passed": passed,
                   "failed": data.get("failed"), "pass_rate": rate,
                   "pass_rate_derived": derived})
    return ev


def _baseline(path=None, max_age_hours: float | None = None,
              now: dt.datetime | None = None) -> dict:
    """测试基线证据；path 缺省读全局最新（保持旧默认行为）。"""
    max_age = MAX_EVIDENCE_AGE_HOURS if max_age_hours is None else float(max_age_hours)
    explicit = path is not None and str(path).strip() != ""
    return _load_evidence("baseline", path if explicit else BASELINE,
                          "explicit" if explicit else "default", max_age, now=now)


def _redteam(path=None, max_age_hours: float | None = None,
             now: dt.datetime | None = None) -> dict:
    """红队证据；path 可为 JSON 文件或目录（目录取最新 one）。

    缺省读 REDTEAM_DIR（保持旧默认行为）。
    """
    max_age = MAX_EVIDENCE_AGE_HOURS if max_age_hours is None else float(max_age_hours)
    explicit = path is not None and str(path).strip() != ""
    return _load_evidence("redteam", path if explicit else REDTEAM_DIR,
                          "explicit" if explicit else "default", max_age, now=now)


def _latest_redteam(max_age_hours: float | None = None,
                    now: dt.datetime | None = None) -> dict:
    """兼容旧入口：读默认红队目录下最新报告。"""
    return _redteam(None, max_age_hours=max_age_hours, now=now)


def _is_stale(ev: dict, max_age_hours: float) -> bool:
    """超龄判定：有 age_hours 时按本次上限重算（支持显式放宽/收紧）；否则看 stale 标记。"""
    age = ev.get("age_hours")
    if isinstance(age, (int, float)):
        return age > float(max_age_hours)
    return ev.get("stale") is True


def _stale_reason(label: str, ev: dict, max_age_hours: float) -> str:
    gen = ev.get("generated_at")
    gen = gen if gen not in (None, "") else "-"
    age = ev.get("age_hours")
    if isinstance(age, (int, float)):
        return ("%s证据超龄 %.1f h > 上限 %.1f h（generated_at=%s）"
                % (label, age, max_age_hours, gen))
    return ("%s证据超龄（年龄不可判定）> 上限 %.1f h（generated_at=%s）"
            % (label, max_age_hours, gen))


def _verdict(base: dict, rt: dict,
             max_age_hours: float | None = None) -> tuple[str, list[str]]:
    """判定：硬失败信号 → REJECT；证据不可用/超龄 → OBSERVE；否则 PASS。

    纯函数，不读盘（测试可直接传构造的证据对象）。
    """
    max_age = MAX_EVIDENCE_AGE_HOURS if max_age_hours is None else float(max_age_hours)
    hard: list[str] = []   # REJECT（可读证据显示质量失败）
    soft: list[str] = []   # OBSERVE（证据缺失/不可读/超龄）
    if not base.get("ok"):
        soft.append("测试基线缺失/不可读（%s）" % (base.get("error") or "unknown"))
    else:
        if _is_stale(base, max_age):
            soft.append(_stale_reason("基线", base, max_age))
        if (base.get("failed") or 0) > 0 or (base.get("error") or 0) > 0:
            hard.append("基线失败：failed=%s error=%s"
                        % (base.get("failed"), base.get("error")))
    if not rt.get("ok"):
        soft.append("红队报告缺失/不可读（%s）" % (rt.get("error") or "unknown"))
    else:
        if _is_stale(rt, max_age):
            soft.append(_stale_reason("红队", rt, max_age))
        rate = rt.get("pass_rate")
        if rate is None:
            soft.append("红队报告缺 pass_rate，无法判定")
        elif rate < MIN_REDTEAM_RATE:
            hard.append("红队通过率 %.0f%% < %.0f%%"
                        % (rate * 100, MIN_REDTEAM_RATE * 100))
    if hard:  # 硬失败不因证据过期失效；过期只影响「能否判 PASS」
        return "REJECT", hard + soft
    if soft:
        return "OBSERVE", soft
    return "PASS", []


def _scope(base: dict, rt: dict) -> tuple[str, dict]:
    detail = {"baseline": base.get("source") or "default",
              "redteam": rt.get("source") or "default"}
    n_explicit = sum(1 for v in detail.values() if v == "explicit")
    if n_explicit == 2:
        return "explicit", detail
    if n_explicit == 0:
        return "default_latest", detail
    return "mixed", detail


def _fmt_int(v) -> str:
    return "?" if v is None else str(v)


def _evidence_line(label: str, ev: dict) -> str:
    if not ev.get("ok"):
        return "- %s：不可用（%s；来源：%s；path=%s）" % (
            label, ev.get("error") or "unknown",
            ev.get("source") or "?", ev.get("path") or "-")
    if ev.get("kind") == "baseline":
        head = "%s passed / %s failed / %s error" % (
            _fmt_int(ev.get("passed")), _fmt_int(ev.get("failed")),
            _fmt_int(ev.get("error")))
    else:
        rate = ev.get("pass_rate")
        head = "%.1f%%" % (rate * 100) if isinstance(rate, (int, float)) else "pass_rate=?"
        if ev.get("total") is not None:
            head += "（%s/%s）" % (_fmt_int(ev.get("passed")), _fmt_int(ev.get("total")))
    gen = ev.get("generated_at")
    gen_txt = gen if gen not in (None, "") else "-"
    if ev.get("age_source") == "mtime":
        gen_txt += "（不可解析，按文件 mtime 计龄）"
    age = ev.get("age_hours")
    age_txt = "不可判定" if age is None else "%.1f h" % age
    if ev.get("stale") is True:
        fresh_txt = "**超龄**（> %.1f h 上限）" % ev.get("max_age_hours", 0)
    elif ev.get("stale") is False:
        fresh_txt = "新鲜"
    else:
        fresh_txt = "年龄不可判定"
    return ("- %s：%s；来源：%s（%s）；生成于：%s；年龄：%s；%s"
            % (label, head, ev.get("source") or "?", ev.get("path") or "-",
               gen_txt, age_txt, fresh_txt))


def accept(commit: str = "HEAD", out_dir: Path | None = None, runner=None,
           baseline=None, redteam=None,
           max_age_hours: float | None = None,
           now: dt.datetime | None = None) -> dict:
    """出验收单：commit + 证据（显式/默认最新）+ 年龄门 → PASS/REJECT/OBSERVE。"""
    max_age = (MAX_EVIDENCE_AGE_HOURS if max_age_hours is None
               else float(max_age_hours))
    now = now or _now()
    info = _commit_info(commit, runner=runner)
    base = _baseline(baseline, max_age_hours=max_age, now=now)
    rt = _redteam(redteam, max_age_hours=max_age, now=now)
    if not info.get("ok"):
        verdict, reasons = "OBSERVE", ["commit 无法读取"]
    else:
        verdict, reasons = _verdict(base, rt, max_age)
    evidence_scope, scope_detail = _scope(base, rt)
    scope_note = {"explicit": "commit 级显式证据",
                  "default_latest": "全局最新证据",
                  "mixed": "混合：显式证据 + 全局最新"}[evidence_scope]
    rep = {
        "ok": True,
        "acceptance_id": "HZ-%s-%s" % (
            (info.get("sha") or "unknown")[:8],
            dt.datetime.now().strftime("%Y%m%d%H%M%S")),
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "observe",
        "blocking": False,
        "commit": info,
        "evidence": {"baseline": base, "redteam": rt},
        "evidence_scope": evidence_scope,
        "evidence_scope_detail": scope_detail,
        "evidence_scope_note": "explicit=--baseline/--redteam 显式 commit 级证据；"
                               "default_latest=全局最新证据；mixed=两者混合",
        "max_evidence_age_hours": max_age,
        "evidence_age": {"baseline": base.get("age_hours"),
                         "redteam": rt.get("age_hours"),
                         "max_age_hours": max_age,
                         "unit": "hours",
                         "stale": {"baseline": base.get("stale"),
                                   "redteam": rt.get("stale")}},
        "verdict": verdict,
        "reasons": reasons,
        "rollback_point": ("git revert %s" % info["sha"][:12]
                           if info.get("ok") else "无（commit 不可读）"),
        "closed_by": "老田（人工）",
        "read_only": True,
        "note": "观察模式 v1：只出验收单，不阻断 merge；证据口径=显式/最新证据 + "
                "年龄门（D3/A，2026-10-30）",
    }
    out = Path(out_dir) if out_dir else OUT
    out.mkdir(parents=True, exist_ok=True)
    tag = (info.get("sha") or "unknown")[:8]
    jp = out / ("acceptance_%s.json" % tag)
    jp.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    md = ["# 黄忠验收单（观察模式 v1）", "",
          "- 验收号：%s" % rep["acceptance_id"],
          "- Commit：%s %s" % (info.get("sha", "")[:12] if info.get("ok") else "?",
                               info.get("subject", "") if info.get("ok") else ""),
          "- 结论：**%s**（blocking=%s）" % (verdict, rep["blocking"]),
          "- 证据口径：%s（%s）；证据年龄上限：%.1f h"
          % (evidence_scope, scope_note, max_age),
          _evidence_line("基线", base),
          _evidence_line("红队", rt),
          "",
          "## 理由", *(["- " + x for x in reasons] or ["- 全部证据满足且新鲜（PASS）"]),
          "", "## 回滚点", "- " + rep["rollback_point"],
          "",
          "> 观察模式：不阻断 merge；最终准入由老田人工决定。"
          "证据口径 D3/A（2026-10-30）：显式/最新证据 + 年龄门。"]
    mp = out / ("acceptance_%s.md" % tag)
    mp.write_text("\n".join(md), encoding="utf-8")
    rep["report_json"] = str(jp)
    rep["report_md"] = str(mp)
    return rep


def _nonneg_hours(text: str) -> float:
    try:
        v = float(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("需要数字（小时），如 48")
    if v < 0:
        raise argparse.ArgumentTypeError("证据年龄上限不能为负")
    return v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="黄忠验收守门 v1（观察模式）")
    ap.add_argument("--commit", default="HEAD", help="待验 commit（默认 HEAD）")
    ap.add_argument("--out-dir", default="", help="验收单输出目录（默认 work/huangzhong）")
    ap.add_argument("--baseline", default="",
                    help="测试基线 JSON 路径（commit 级显式证据；缺省读全局最新）")
    ap.add_argument("--redteam", "--redteam-dir", dest="redteam", default="",
                    help="红队报告 JSON 文件或目录（显式证据；缺省读全局最新）")
    ap.add_argument("--max-evidence-age-hours", type=_nonneg_hours,
                    default=MAX_EVIDENCE_AGE_HOURS,
                    help="证据年龄上限（小时，默认 48）；超龄即 OBSERVE")
    ap.add_argument("--json", action="store_true", help="输出完整验收单 JSON")
    args = ap.parse_args(argv)
    rep = accept(args.commit, Path(args.out_dir) if args.out_dir else None,
                 baseline=args.baseline or None,
                 redteam=args.redteam or None,
                 max_age_hours=args.max_evidence_age_hours)
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json
          else "验收：%s（%s）→ %s" % (rep["verdict"], rep["commit"].get("sha", "")[:8],
                                       rep["report_md"]))
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
