# -*- coding: utf-8 -*-
"""黄忠认证 v2 六条一键复核（只读）。

用途：2026-11-04 观察期复核 / 黄忠认证评审的「六条硬指标」一键复核，
输出 JSON + Markdown 报告（默认 work/huangzhong/review_<ts>.json/md）。
判据来源：docs/plans/2026-10-30_黄忠认证标准v2与验收工具证据口径拍板_v1.md 第二节；
口径补丁来源：docs/plans/2026-10-30_黄忠v0验收单豁免与复核口径补丁_v1.md
（WO-HZ-POLICY-20261030，工具版本 v1.1）。

六条（每条状态 = ✓ / ✗ / pending / tracking，仅六条全 ✓ 才 overall_pass=True）：
  ① PASS 样本量：acceptance_*.json 中 verdict=PASS 按 commit sha 去重（同 commit
     取最新），排除演练单（drill 台账 drill_ledger_*.json 列出 / drill 标记）；≥10 达标。
  ② 受控拒收演练：drill 台账中 verdict∈{REJECT,OBSERVE} 的条目 ≥1 达标；
     台账缺失（或不可读）→ pending（等 Lead/黄忠演练后补台账再跑）。
  ③ 误拒绝率（v1.1 口径修正）：自然 REJECT 单 <20 → 按「逐单人工复核」口径：
     work/huangzhong/reject_review*.json 覆盖全部 REJECT（自然+演练）且推翻=0
     → ✓；清单不完整 → pending；有推翻 → ✗。自然 REJECT≥20 时读复核清单计算
     推翻率（分母=REJECT 总单含演练单），<5% 达标（原逻辑不变）。
  ④ 漏放：PASS 单满 7 天窗口判定（generated_at + 7d），窗口内与该 commit 相关
     事故数 = 0 达标；无满窗单 → tracking（认证后跟踪项）；可选读
     work/huangzhong/incidents.jsonl。
  ⑤ 证据链：每份 PASS 单 8 类必需字段完整率 100%（commit.sha/verdict/mode/
     blocking/rollback_point/evidence.baseline 与 .redteam 的 generated_at+age+
     stale），且记录的证据年龄 ≤48h（含 stale=False）达标。
     **legacy v0 豁免（v1.1）**：缺证据年龄字段（evidence.baseline.age_hours/
     stale、或顶层 max_evidence_age_hours 缺失）的旧验收单判为 legacy，默认
     不计入⑤分母与分子（老田 2026-10-30 拍板，--legacy-v0-policy exempt）；
     count = 旧行为（legacy 计入分母，缺字段照判 ✗）。无非 legacy 单 → pending。
  ⑥ 赵云抽查：work/huangzhong/audit_sample_3.json 3/3 一致达标；缺失 → pending。

边界：只读。不写业务代码/验收单/证据，只落复核报告；不代批（最终由老田签字）。
兼容：所有输入缺失/坏行一律容错并在 warnings 记录，不中断复核。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS_TARGET = 10              # ① PASS 样本达标线
DRILL_TARGET = 1              # ② 受控拒收演练达标线
NATURAL_REJECT_MIN = 20       # ③ 5% 阈值的自然 REJECT 最小样本量
MISREJECT_RATE_MAX = 0.05     # ③ 误拒绝率上限（<5%）
EVIDENCE_MAX_AGE_HOURS = 48.0  # ⑤ 认证口径证据年龄门（工具默认门）
MATURITY_DAYS = 7             # ④ 漏放窗口（PASS 后 7 天）
# ⑤ legacy v0 口径：exempt=豁免（老田 2026-10-30 拍板，默认）；count=旧行为
LEGACY_V0_POLICY_DEFAULT = "exempt"
LEGACY_V0_POLICIES = ("exempt", "count")

OK, FAIL, PENDING, TRACKING = "✓", "✗", "pending", "tracking"
STATUS_LABEL = {OK: "✓ 达标", FAIL: "✗ 未达标", PENDING: "pending 待补证",
                TRACKING: "tracking 跟踪中"}
DRILL_VERDICTS = ("REJECT", "OBSERVE")
REQUIRED_FIELDS = (
    "commit.sha", "verdict", "mode=observe", "blocking=false", "rollback_point",
    "evidence.baseline.generated_at", "evidence.baseline.age_hours",
    "evidence.baseline.stale",
    "evidence.redteam.generated_at", "evidence.redteam.age_hours",
    "evidence.redteam.stale",
)
MAX_LIST = 50  # 明细列表截断（防报告膨胀）


# ------------------------------------------------------------------ 基础工具

def _now() -> dt.datetime:
    """当前时间（本机时区，aware）；测试可注入 now 以获得确定性。"""
    return dt.datetime.now().astimezone()


def _iso(ts: dt.datetime | None) -> str | None:
    return ts.isoformat(timespec="seconds") if isinstance(ts, dt.datetime) else None


def _parse_ts(raw) -> dt.datetime | None:
    """解析时间戳：ISO8601（含 Z/偏移）、常见空格格式、epoch 秒；失败返回 None。

    无时区按本机时区解释（仓库环境 Asia/Shanghai），与 huangzhong_accept.py 口径一致。
    """
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
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "drill", "演练")
    return False


def _read_text(path: Path) -> tuple[str | None, str | None]:
    """读文本（容忍 UTF-8 BOM）；失败返回 (None, error)。"""
    try:
        return path.read_text(encoding="utf-8-sig"), None
    except FileNotFoundError:
        return None, "missing"
    except OSError as e:
        return None, "%s: %s" % (type(e).__name__, e)


def _read_json(path: Path) -> tuple[object | None, str | None]:
    text, err = _read_text(path)
    if text is None:
        return None, err or "unreadable"
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, "JSON 解析失败: %s" % e


def _read_jsonl(path: Path) -> dict:
    """读 JSONL（可选文件）：{exists, rows, bad_lines, error}；坏行容错。"""
    text, err = _read_text(path)
    if text is None:
        return {"path": str(path), "exists": Path(path).exists() and err != "missing",
                "rows": [], "bad_lines": 0, "error": err}
    rows, bad = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            bad += 1
    return {"path": str(path), "exists": True, "rows": rows,
            "bad_lines": bad, "error": None}


def _cap(rows: list, limit: int = MAX_LIST) -> list:
    return rows[:limit]


def _match_sha(a: str, b: str) -> bool:
    """commit 前缀匹配（至少 8 位；不足 8 位退化为全等）。"""
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return False
    if len(a) < 8 or len(b) < 8:
        return a == b
    return a[:8] == b[:8]


# ------------------------------------------------------------ 输入：验收单/台账

def _sheet_ts(sheet: dict) -> dt.datetime | None:
    """验收单时间（generated_at；缺失回退文件 mtime）。"""
    ts = _parse_ts(sheet.get("generated_at"))
    if ts is not None:
        return ts
    try:
        return dt.datetime.fromtimestamp(Path(sheet["path"]).stat().st_mtime,
                                         tz=dt.timezone.utc)
    except OSError:
        return None


def load_acceptance(work_dir: Path) -> dict:
    """扫 work/huangzhong/acceptance_*.json → {sheets, invalid, files}；坏文件容错。"""
    sheets, invalid = [], []
    files = sorted(Path(work_dir).glob("acceptance_*.json"))
    for p in files:
        data, err = _read_json(p)
        if err or not isinstance(data, dict):
            invalid.append({"file": p.name, "error": err or "JSON 顶层不是对象"})
            continue
        commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
        evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
        sheets.append({
            "file": p.name,
            "path": str(p),
            "sha": (commit.get("sha") or "").strip() if isinstance(
                commit.get("sha"), str) else "",
            "verdict": data.get("verdict") if isinstance(data.get("verdict"), str) else None,
            "mode": data.get("mode"),
            "blocking": data.get("blocking"),
            "rollback_point": data.get("rollback_point"),
            "generated_at": data.get("generated_at"),
            "evidence": evidence,
            "evidence_scope": data.get("evidence_scope"),
            "max_evidence_age_hours": data.get("max_evidence_age_hours"),
            "drill_flag": _truthy(data.get("drill")) or data.get("mode") == "drill",
        })
    return {"files": [p.name for p in files], "sheets": sheets, "invalid": invalid}


def _ledger_entries_from_file(path: Path) -> dict:
    """读一个 drill 台账文件：支持 JSON（dict/list）与 JSONL 两种写法。"""
    data, err = _read_json(path)
    if err is None:
        if isinstance(data, list):
            return {"entries": data, "error": None}
        if isinstance(data, dict):
            for key in ("drills", "entries", "records", "items", "ledger"):
                val = data.get(key)
                if isinstance(val, list):
                    return {"entries": val, "error": None}
            # 单条台账（dict 自身即一条演练记录）
            return {"entries": [data], "error": None}
        return {"entries": [], "error": "JSON 顶层不是对象/数组"}
    # 退路：JSONL（每行一条台账）
    text, _ = _read_text(path)
    entries, bad = [], 0
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(obj, dict):
            entries.append(obj)
        else:
            bad += 1
    if entries:
        return {"entries": entries, "error": None,
                "note": "按 JSONL 解析（%d 坏行跳过）" % bad}
    return {"entries": [], "error": err}


def load_drill_ledger(work_dir: Path) -> dict:
    """读 work/huangzhong/drills/drill_ledger_*.json（兼容 work/huangzhong/ 根）。

    返回 {files, readable, entries, bad, sha8, files_lower}；
    entries 为归一化演练条目（file/sha/verdict/generated_at/source）。
    """
    work_dir = Path(work_dir)
    paths: list[Path] = []
    for d in (work_dir / "drills", work_dir):
        if d.is_dir():
            for p in sorted(d.glob("drill_ledger_*.json")):
                if all(p.resolve() != q.resolve() for q in paths):
                    paths.append(p)
    entries, bad, readable = [], [], False
    for p in paths:
        got = _ledger_entries_from_file(p)
        if got.get("error"):
            bad.append({"file": p.name, "error": got["error"]})
            continue
        readable = True
        for raw in got.get("entries") or []:
            if not isinstance(raw, dict):
                bad.append({"file": p.name, "error": "条目不是对象"})
                continue
            f = raw.get("file") or raw.get("path") or raw.get("acceptance_file") \
                or raw.get("acceptance_report") or raw.get("acceptance") \
                or raw.get("report") or raw.get("report_json") or raw.get("json")
            fname = Path(str(f)).name if f else ""
            commit = raw.get("commit") if isinstance(raw.get("commit"), dict) else {}
            sha = raw.get("sha") or raw.get("commit_sha") or commit.get("sha") \
                or raw.get("target_commit") or raw.get("commit")
            sha = sha.strip() if isinstance(sha, str) else ""
            kind = raw.get("kind") if isinstance(raw.get("kind"), str) else ""
            verdict = raw.get("verdict") if isinstance(raw.get("verdict"), str) else ""
            if not verdict and kind.strip().upper() in DRILL_VERDICTS:
                verdict = kind.strip()  # 部分台账用 kind=REJECT/OBSERVE 标注
            if not sha and re.match(r"^acceptance_([0-9a-fA-F]{8,40})\.json$", fname):
                sha = re.match(r"^acceptance_([0-9a-fA-F]{8,40})\.json$",
                               fname).group(1)
            entries.append({
                "source": p.name,
                "file": fname,
                "sha": sha,
                "verdict": verdict.strip().upper() if verdict else "",
                "generated_at": raw.get("generated_at") or raw.get("ts")
                or raw.get("date") or raw.get("result_at")
                or raw.get("evidence_generated_at"),
                "method": raw.get("method") or raw.get("means"),
                "witness": raw.get("witness") or raw.get("reviewer"),
                "raw": {k: v for k, v in raw.items()
                        if k not in ("file", "path", "commit")},
            })
    return {"files": [str(p) for p in paths], "readable": readable,
            "entries": entries, "bad": bad,
            "sha8": {e["sha"][:8].lower() for e in entries if len(e["sha"]) >= 8},
            "files_lower": {e["file"].lower() for e in entries if e["file"]}}


def _norm_ledger_verdicts(drills: dict, sheets: list[dict]) -> None:
    """台账条目 verdict 缺失时，按文件名/commit 从验收单回填（就地修改）。"""
    by_file = {s["file"].lower(): s for s in sheets}
    for e in drills["entries"]:
        if e["verdict"]:
            continue
        hit = by_file.get(e["file"].lower()) if e["file"] else None
        if hit is None and e["sha"]:
            hit = next((s for s in sheets if _match_sha(s["sha"], e["sha"])), None)
        if hit and isinstance(hit.get("verdict"), str) and hit["verdict"].strip():
            e["verdict"] = hit["verdict"].strip().upper()
            e["verdict_inferred"] = True


def _is_drill(sheet: dict, drills: dict) -> bool:
    """是否演练单：台账列出（文件名/commit sha8）或验收单自带 drill 标记。"""
    if sheet.get("drill_flag"):
        return True
    fname = (sheet.get("file") or "").lower()
    if fname and fname in drills.get("files_lower", set()):
        return True
    sha8 = (sheet.get("sha") or "").lower()[:8]
    return bool(sha8) and sha8 in drills.get("sha8", set())


# --------------------------------------------------------------- 六条：单项判定

def _latest_sheets(sheets: list[dict]) -> list[dict]:
    """同 commit sha 去重（取最新一单）；无 sha 的按文件名各自保留。"""
    chosen: dict[str, dict] = {}
    for s in sheets:
        key = (s.get("sha") or "").strip().lower() or ("file:" + s["file"])
        cur = chosen.get(key)
        if cur is None:
            chosen[key] = s
            continue
        ts, cur_ts = _sheet_ts(s), _sheet_ts(cur)
        if ts is not None and (cur_ts is None or ts > cur_ts):
            chosen[key] = s
    out = list(chosen.values())
    out.sort(key=lambda s: _sheet_ts(s) or dt.datetime.min.replace(
        tzinfo=dt.timezone.utc), reverse=True)
    return out


def _pass_samples(acc: dict, drills: dict) -> dict:
    raw = [s for s in acc["sheets"] if (s.get("verdict") or "").upper() == "PASS"]
    excluded = [s for s in raw if _is_drill(s, drills)]
    eligible = [s for s in raw if not _is_drill(s, drills)]
    samples = _latest_sheets(eligible)
    return {"raw": raw, "excluded": excluded, "eligible": eligible,
            "samples": samples}


def item1_pass_samples(acc: dict, drills: dict) -> dict:
    got = _pass_samples(acc, drills)
    count = len(got["samples"])
    status = OK if count >= PASS_TARGET else FAIL
    detail = {
        "pass_sheets_raw": len(got["raw"]),
        "excluded_drills": _cap([{"file": s["file"], "sha8": s["sha"][:8]}
                                 for s in got["excluded"]]),
        "deduped_out": len(got["eligible"]) - count,
        "samples": _cap([{
            "file": s["file"], "sha8": s["sha"][:8],
            "generated_at": s["generated_at"],
            "evidence_scope": s.get("evidence_scope"),
        } for s in got["samples"]]),
        "count": count,
        "target": PASS_TARGET,
        "dedup_rule": "按 commit sha 去重，同一 commit 取 generated_at 最新一单；"
                      "排除演练单（drill 台账/ drill 标记）",
    }
    return {"id": "①", "title": "PASS 验收样本量", "status": status,
            "target": "≥%d 单 PASS" % PASS_TARGET,
            "measured": "%d/%d 单" % (count, PASS_TARGET),
            "value": {"count": count, "target": PASS_TARGET},
            "detail": detail}


def item2_drill(drills: dict) -> dict:
    if not drills["files"] or not drills["readable"]:
        detail = {
            "ledger_files": drills["files"],
            "ledger_missing": not drills["files"],
            "ledger_unreadable": bool(drills["files"]) and not drills["readable"],
            "expected_glob": "work/huangzhong/drills/drill_ledger_*.json",
            "ledger_schema_hint": {
                "drills": [{
                    "file": "acceptance_<sha8>.json",
                    "verdict": "REJECT|OBSERVE",
                    "commit": "<full sha>",
                    "generated_at": "<验收单生成时间>",
                    "method": "受控拒断手段（历史失败证据显式绑定等）",
                    "witness": "赵云/老田",
                }],
                "alternative": "顶层数组 [ {...}, ... ] 亦可；verdict 缺失时按 file/commit 从验收单回填",
            },
            "note": "台账未生成（或不可读）→ ② 判 pending；演练后写台账重跑即可",
        }
        return {"id": "②", "title": "受控拒收演练", "status": PENDING,
                "target": "≥%d 次（verdict∈{REJECT,OBSERVE}）" % DRILL_TARGET,
                "measured": "台账缺失" if not drills["files"] else "台账不可读",
                "value": {"hits": 0, "target": DRILL_TARGET},
                "detail": detail}
    seen, hits = set(), []
    for e in drills["entries"]:
        v = (e.get("verdict") or "").upper()
        if v not in DRILL_VERDICTS:
            continue
        key = (e["file"].lower(), e["sha"][:8].lower()) if e["file"] or e["sha"] \
            else (e["source"], v)
        if key in seen:
            continue
        seen.add(key)
        hits.append(e)
    count = len(hits)
    status = OK if count >= DRILL_TARGET else FAIL
    by_verdict = {v: sum(1 for e in hits if e["verdict"] == v)
                  for v in DRILL_VERDICTS}
    unknown = [e for e in drills["entries"]
               if (e.get("verdict") or "").upper() not in DRILL_VERDICTS]
    detail = {
        "ledger_files": drills["files"],
        "ledger_entries": len(drills["entries"]),
        "hits": _cap([{"file": e["file"], "sha8": e["sha"][:8],
                       "verdict": e["verdict"], "method": e.get("method"),
                       "witness": e.get("witness"),
                       "generated_at": e.get("generated_at"),
                       "source": e.get("source")} for e in hits]),
        "by_verdict": by_verdict,
        "unknown_verdict_entries": _cap([{"file": e["file"], "verdict": e["verdict"]}
                                         for e in unknown]),
        "bad_entries": _cap(drills.get("bad") or []),
        "note": "演练单不计入 ① PASS 样本（按台账文件名/commit 排除）",
    }
    return {"id": "②", "title": "受控拒收演练", "status": status,
            "target": "≥%d 次（verdict∈{REJECT,OBSERVE}）" % DRILL_TARGET,
            "measured": "%d 次（REJECT=%d OBSERVE=%d）"
                        % (count, by_verdict["REJECT"], by_verdict["OBSERVE"]),
            "value": {"hits": count, "target": DRILL_TARGET,
                      "by_verdict": by_verdict},
            "detail": detail}


def _verdict_sheets(acc: dict, verdict: str) -> list[dict]:
    return [s for s in acc["sheets"] if (s.get("verdict") or "").upper() == verdict]


def _review_key(row: dict) -> str:
    """复核清单记录 → 匹配键（commit sha8 优先，其次验收单文件名）。"""
    sha = row.get("sha") or row.get("commit_sha") or row.get("commit")
    if isinstance(sha, dict):
        sha = sha.get("sha")
    if isinstance(sha, str) and len(sha.strip()) >= 8:
        return "sha:" + sha.strip().lower()[:8]
    f = row.get("file") or row.get("path") or row.get("acceptance_file") \
        or row.get("report")
    if f:
        return "file:" + Path(str(f)).name.lower()
    return ""


def _sheet_key(sheet: dict) -> str:
    sha8 = (sheet.get("sha") or "").lower()[:8]
    return "sha:" + sha8 if sha8 else "file:" + sheet["file"].lower()


def _reject_targets(natural: list[dict], drill_reject: list[dict]) -> list[dict]:
    """REJECT 目标清单（自然验收单 + 演练台账），按复核匹配键去重、保序。

    v1.1 ③ 小样本口径用它判断「复核清单是否覆盖全部 REJECT（自然+演练）」。
    """
    raw: list[dict] = []
    for s in natural:
        raw.append({"key": _sheet_key(s), "label": s["file"],
                    "sha8": s["sha"][:8], "source": "natural"})
    for e in drill_reject:
        key = ("sha:" + e["sha"][:8].lower() if len(e["sha"]) >= 8
               else "file:" + e["file"].lower())
        raw.append({"key": key, "label": e["file"] or "(台账条目)",
                    "sha8": e["sha"][:8], "source": "drill"})
    out, seen = [], set()
    for t in raw:
        if t["key"] in seen:
            continue
        seen.add(t["key"])
        out.append(t)
    return out


def _overturned(rec: dict) -> bool | None:
    """复核记录 → 是否推翻（True=误拒 / False=确认拒收 / None=无法判定）。"""
    if isinstance(rec.get("overturned"), bool):
        return rec["overturned"]
    if isinstance(rec.get("推翻"), bool):
        return rec["推翻"]
    for key in ("result", "conclusion", "review", "verdict"):
        v = rec.get(key)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("overturned", "推翻", "误拒", "误拒绝", "false_reject",
                     "invalid_reject", "reject_invalid"):
                return True
            if s in ("confirmed", "有效", "确认", "拒绝有效", "correct", "valid",
                     "keep_reject"):
                return False
    return None


def load_reject_reviews(work_dir: Path) -> dict:
    """读 work/huangzhong/reject_review*.json / .jsonl → {files, rows, bad, error}。"""
    work_dir = Path(work_dir)
    paths: list[Path] = []
    for pat in ("reject_review*.json", "reject_review*.jsonl", "reject_review*.md"):
        for p in sorted(work_dir.glob(pat)):
            if p.suffix.lower() == ".md":
                continue
            if all(p.resolve() != q.resolve() for q in paths):
                paths.append(p)
    rows, bad = [], []
    for p in paths:
        data, err = _read_json(p)
        if err is None and isinstance(data, list):
            rows.extend([r for r in data if isinstance(r, dict)])
            continue
        if err is None and isinstance(data, dict):
            found = None
            for key in ("reviews", "records", "items", "rejects", "ledger"):
                if isinstance(data.get(key), list):
                    found = data[key]
                    break
            if found is None and any(k in data for k in
                                     ("file", "sha", "commit", "overturned")):
                found = [data]
            if found is not None:
                rows.extend([r for r in found if isinstance(r, dict)])
                continue
            bad.append({"file": p.name, "error": "未找到复核记录数组"})
            continue
        # JSONL 退路
        text, _ = _read_text(p)
        got = 0
        for line in (text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                bad.append({"file": p.name, "error": "坏行"})
                continue
            if isinstance(obj, dict):
                rows.append(obj)
                got += 1
        if not got and err:
            bad.append({"file": p.name, "error": err})
    return {"files": [str(p) for p in paths], "rows": rows, "bad": bad}


def item3_misreject(acc: dict, drills: dict, work_dir: Path) -> dict:
    natural = [s for s in _verdict_sheets(acc, "REJECT") if not _is_drill(s, drills)]
    drill_reject = [e for e in drills["entries"]
                    if (e.get("verdict") or "").upper() == "REJECT"]
    total_reject = len(natural) + len(drill_reject)
    review = load_reject_reviews(Path(work_dir))
    verdict_map = {}
    for row in review["rows"]:
        key = _review_key(row)
        if not key:
            continue
        val = _overturned(row)
        if val is not None:
            verdict_map[key] = val
    detail = {
        "natural_reject_sheets": len(natural),
        "natural_reject_samples": _cap([{"file": s["file"], "sha8": s["sha"][:8],
                                         "generated_at": s.get("generated_at")}
                                        for s in natural]),
        "drill_reject_entries": len(drill_reject),
        "total_reject": total_reject,
        "natural_min_for_rate": NATURAL_REJECT_MIN,
        "rate_max": MISREJECT_RATE_MAX,
        "manual_review_glob": "work/huangzhong/reject_review*.json",
        "manual_review_files": review["files"],
        "manual_review_rows": len(review["rows"]),
        "bad": _cap(review["bad"]),
    }
    base = {"id": "③", "title": "误拒绝率 <5%", "detail": detail}
    if len(natural) < NATURAL_REJECT_MIN:
        # v1.1 口径（老田 2026-10-30 拍板补丁 b）：<20 时按「逐单人工复核，
        # 确认无误拒」判定——复核清单须覆盖全部 REJECT（自然+演练）且 0 推翻。
        targets = _reject_targets(natural, drill_reject)
        unreviewed = [t for t in targets if t["key"] not in verdict_map]
        overturned = sum(1 for t in targets if verdict_map.get(t["key"]) is True)
        reviewed = len(targets) - len(unreviewed)
        detail.update({"review_mode": "manual_per_sheet",
                       "reject_total_unique": len(targets),
                       "reviewed": reviewed,
                       "unreviewed": _cap(unreviewed),
                       "overturned": overturned})
        target_txt = ("逐单人工复核 0 推翻（自然 REJECT<%d 时）；≥%d 单时按 <%s%% "
                      "推翻率硬指标"
                      % (NATURAL_REJECT_MIN, NATURAL_REJECT_MIN,
                         int(MISREJECT_RATE_MAX * 100)))
        value = {"natural_reject": len(natural), "rate": None,
                 "overturned": overturned, "total_reject": total_reject,
                 "reviewed": reviewed}
        if not targets:
            detail["note"] = ("自然 REJECT 0 单、演练 REJECT 0 单：无 REJECT 样本可复核"
                              " → pending（等收到首批 REJECT/OBSERVE 后按逐单复核口径判定）")
            base.update({"status": PENDING, "target": target_txt,
                         "measured": "自然 REJECT 0 单（<20）；无 REJECT 样本可复核",
                         "value": value})
            return base
        if overturned:
            detail["note"] = ("逐单复核推翻 %d 单（已复核 %d/%d 单）→ ✗；误拒单须作废重验/"
                              "修正后重新复核（自然 REJECT<20 口径要求 0 推翻）"
                              % (overturned, reviewed, len(targets)))
            base.update({"status": FAIL, "target": target_txt,
                         "measured": "自然 REJECT %d 单（<20）；逐单复核推翻 %d/%d 单"
                                     % (len(natural), overturned, len(targets)),
                         "value": value})
            return base
        if unreviewed:
            detail["note"] = ("复核清单不完整（%d/%d 单未见结论）→ pending；补齐 "
                              "reject_review*.json（谁复核/结论/证据依据）后重跑"
                              % (len(unreviewed), len(targets)))
            base.update({"status": PENDING, "target": target_txt,
                         "measured": "自然 REJECT %d 单（<20）；逐单复核 %d/%d 单，清单不完整"
                                     % (len(natural), reviewed, len(targets)),
                         "value": value})
            return base
        detail["note"] = ("自然 REJECT<20：按 v2 §2.2③「逐单人工复核，确认无误拒」口径，"
                          "复核清单覆盖全部 REJECT（自然+演练）且 0 推翻 → ✓")
        base.update({"status": OK, "target": target_txt,
                     "measured": "自然 REJECT %d<20；逐单复核 0 推翻（清单 %d 条）"
                                 % (len(natural), len(targets)),
                     "value": value})
        return base
    # ≥20：按复核清单计算推翻率（分母=REJECT 总单，含演练单，v2 口径）——原逻辑不变
    rejects = [{"key": _sheet_key(s), "label": s["file"], "sha8": s["sha"][:8]}
               for s in natural]
    rejects += [{"key": "sha:" + e["sha"][:8].lower() if len(e["sha"]) >= 8
                 else "file:" + e["file"].lower(),
                 "label": e["file"] or "(台账条目)", "sha8": e["sha"][:8]}
                for e in drill_reject]
    unreviewed = [r for r in rejects if r["key"] not in verdict_map]
    overturned = sum(1 for r in rejects if verdict_map.get(r["key"]) is True)
    detail.update({"review_mode": "rate",
                   "reviewed": len(rejects) - len(unreviewed),
                   "unreviewed": _cap(unreviewed),
                   "overturned": overturned})
    if unreviewed:
        detail["note"] = ("复核清单不完整（%d/%d 未见结论）→ pending；补齐 "
                          "reject_review*.json 后重跑"
                          % (len(unreviewed), len(rejects)))
        base.update({"status": PENDING,
                     "target": "<%s 推翻率（自然 REJECT≥%d）"
                               % (int(MISREJECT_RATE_MAX * 100), NATURAL_REJECT_MIN),
                     "measured": "复核 %d/%d 单，清单不完整"
                                 % (len(rejects) - len(unreviewed), len(rejects)),
                     "value": {"natural_reject": len(natural), "rate": None}})
        return base
    rate = round(overturned / total_reject, 4) if total_reject else None
    status = OK if rate is not None and rate < MISREJECT_RATE_MAX else FAIL
    detail["note"] = "误拒绝率 = 复核推翻 REJECT / REJECT 总单（含演练单）"
    base.update({"status": status,
                 "target": "<%s 推翻率" % int(MISREJECT_RATE_MAX * 100),
                 "measured": "推翻 %d/%d = %.1f%%" % (overturned, total_reject,
                                                       (rate or 0) * 100),
                 "value": {"natural_reject": len(natural), "rate": rate,
                           "overturned": overturned, "total_reject": total_reject}})
    return base


def _incident_ts(rec: dict) -> dt.datetime | None:
    for key in ("ts", "generated_at", "date", "time", "at", "timestamp"):
        ts = _parse_ts(rec.get(key))
        if ts is not None:
            return ts
    return None


def _incident_commit(rec: dict) -> str:
    for key in ("commit", "sha", "commit_sha", "target_commit", "reverted_commit"):
        v = rec.get(key)
        if isinstance(v, dict):
            v = v.get("sha")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def item4_leakage(acc: dict, drills: dict, work_dir: Path, now: dt.datetime) -> dict:
    samples = _pass_samples(acc, drills)["samples"]
    window = dt.timedelta(days=MATURITY_DAYS)
    incidents = _read_jsonl(Path(work_dir) / "incidents.jsonl")
    parsed = []
    for rec in incidents["rows"]:
        ts = _incident_ts(rec)
        parsed.append({"ts": ts, "commit": _incident_commit(rec),
                       "kind": rec.get("kind") or rec.get("type"),
                       "desc": rec.get("desc") or rec.get("detail") or rec.get("title")})
    mature, tracking, unknown = [], [], []
    leaks = []
    for s in samples:
        gen = _parse_ts(s.get("generated_at"))
        if gen is None:
            unknown.append({"file": s["file"], "sha8": s["sha"][:8],
                            "reason": "generated_at 不可解析"})
            continue
        due = gen + window
        row = {"file": s["file"], "sha8": s["sha"][:8],
               "generated_at": _iso(gen), "window_end": _iso(due)}
        if now < due:
            tracking.append(row)
            continue
        hit = []
        for inc in parsed:
            ts = inc["ts"]
            if ts is None or not (gen <= ts <= due):
                continue
            if inc["commit"] and _match_sha(inc["commit"], s["sha"]):
                hit.append({"ts": _iso(ts), "commit": inc["commit"],
                            "kind": inc["kind"], "desc": inc["desc"]})
        row["leaks"] = _cap(hit)
        row["leak"] = bool(hit)
        mature.append(row)
        leaks.extend(hit)
    unattributed = []
    for inc in parsed:
        ts = inc["ts"]
        if ts is None or inc["commit"]:
            continue
        for s in samples:
            gen = _parse_ts(s.get("generated_at"))
            if gen is not None and gen <= ts <= gen + window:
                unattributed.append({"ts": _iso(ts), "kind": inc["kind"],
                                     "desc": inc["desc"]})
                break
    detail = {
        "window_days": MATURITY_DAYS,
        "mature_count": len(mature),
        "tracking_count": len(tracking),
        "unknown_maturity": _cap(unknown),
        "mature_samples": _cap(mature),
        "tracking_samples": _cap(tracking),
        "incidents_file": incidents["path"],
        "incidents_exists": incidents["exists"],
        "incidents_rows": len(parsed),
        "incidents_bad_lines": incidents["bad_lines"],
        "leak_count": len(leaks),
        "unattributed_incidents": _cap(unattributed),
        "note": ("漏放 = 满 %d 天窗口 PASS 单中、与 commit 相关事故数；未满窗单列认证后"
                 "跟踪；incidents.jsonl 为可选台账（缺失按无已知事故计，需人工确认）"
                 % MATURITY_DAYS),
    }
    if not samples:
        status, measured = PENDING, "无 PASS 样本可判"
    elif not mature:
        status = TRACKING
        measured = "满窗 0/%d 单（%d 单跟踪中）" % (len(samples), len(tracking))
    elif leaks:
        status = FAIL
        measured = "满窗 %d 单，漏放 %d 起" % (len(mature), len(leaks))
    else:
        status, measured = OK, "满窗 %d 单，漏放 0" % len(mature)
    return {"id": "④", "title": "漏放 = 0", "status": status,
            "target": "满 7 天窗口 PASS 单事故数 = 0",
            "measured": measured,
            "value": {"mature": len(mature), "leaks": len(leaks),
                      "tracking": len(tracking)},
            "detail": detail}


def _required_checks(sheet: dict) -> tuple[list[str], dict]:
    ev = sheet.get("evidence") if isinstance(sheet.get("evidence"), dict) else {}
    base = ev.get("baseline") if isinstance(ev.get("baseline"), dict) else {}
    rt = ev.get("redteam") if isinstance(ev.get("redteam"), dict) else {}
    checks = [
        ("commit.sha", _nonempty_str(sheet.get("sha"))),
        ("verdict", _nonempty_str(sheet.get("verdict"))),
        ("mode=observe", sheet.get("mode") == "observe"),
        ("blocking=false", sheet.get("blocking") is False),
        ("rollback_point", _nonempty_str(sheet.get("rollback_point"))),
        ("evidence.baseline.generated_at", _nonempty_str(base.get("generated_at"))),
        ("evidence.baseline.age_hours", _num(base.get("age_hours"))),
        ("evidence.baseline.stale", isinstance(base.get("stale"), bool)),
        ("evidence.redteam.generated_at", _nonempty_str(rt.get("generated_at"))),
        ("evidence.redteam.age_hours", _num(rt.get("age_hours"))),
        ("evidence.redteam.stale", isinstance(rt.get("stale"), bool)),
    ]
    missing = [name for name, passed in checks if not passed]
    return missing, {"baseline": base, "redteam": rt}


def _reconstructed_age(sheet: dict, key: str) -> float | None:
    """按「验收单 generated_at − 证据 generated_at」重建判定时证据年龄（仅报告用）。"""
    ev = sheet.get("evidence") if isinstance(sheet.get("evidence"), dict) else {}
    one = ev.get(key) if isinstance(ev.get(key), dict) else {}
    gen_ev = _parse_ts(one.get("generated_at"))
    gen_sheet = _parse_ts(sheet.get("generated_at"))
    if gen_ev is None or gen_sheet is None:
        return None
    return round((gen_sheet - gen_ev).total_seconds() / 3600.0, 2)


def _legacy_v0_missing(sheet: dict) -> list[str]:
    """v0 旧验收单判定：返回缺失的「证据年龄字段」列表（空 = 非 legacy）。

    legacy 口径（老田 2026-10-30 拍板，WO-HZ-POLICY-20261030）：PASS 单缺
    evidence.baseline.age_hours / evidence.baseline.stale，或顶层
    max_evidence_age_hours（v0 工具不落年龄字段，无法按 48h 年龄门复核）。
    """
    ev = sheet.get("evidence") if isinstance(sheet.get("evidence"), dict) else {}
    base = ev.get("baseline") if isinstance(ev.get("baseline"), dict) else {}
    missing = []
    if not _num(base.get("age_hours")):
        missing.append("evidence.baseline.age_hours")
    if not isinstance(base.get("stale"), bool):
        missing.append("evidence.baseline.stale")
    if not _num(sheet.get("max_evidence_age_hours")):
        missing.append("max_evidence_age_hours")
    return missing


def item5_evidence(acc: dict, drills: dict,
                   legacy_v0_policy: str = LEGACY_V0_POLICY_DEFAULT) -> dict:
    policy = (legacy_v0_policy if legacy_v0_policy in LEGACY_V0_POLICIES
              else LEGACY_V0_POLICY_DEFAULT)
    samples = _pass_samples(acc, drills)["samples"]
    legacy_rows, active = [], []
    for s in samples:
        missing_age = _legacy_v0_missing(s)
        if missing_age:
            legacy_rows.append({"file": s["file"], "sha8": s["sha"][:8],
                                "missing_age_fields": missing_age})
        else:
            active.append(s)
    # exempt（老田拍板默认）= legacy 不进⑤分子分母；count = 旧行为（计入分母）
    judged = active if policy == "exempt" else samples
    excluded = legacy_rows if policy == "exempt" else []
    rows, complete_n, fresh_n = [], 0, 0
    for s in judged:
        missing, ev = _required_checks(s)
        complete = not missing
        base, rt = ev["baseline"], ev["redteam"]
        fresh = None
        max_age = None
        if complete:
            complete_n += 1
            ages = [float(base["age_hours"]), float(rt["age_hours"])]
            max_age = max(ages)
            fresh = (max_age <= EVIDENCE_MAX_AGE_HOURS
                     and base.get("stale") is False and rt.get("stale") is False)
            if fresh:
                fresh_n += 1
        rows.append({
            "file": s["file"], "sha8": s["sha"][:8],
            "complete": complete, "missing": missing,
            "fresh": fresh, "max_age_hours": max_age,
            "baseline_age_hours": base.get("age_hours") if complete else None,
            "redteam_age_hours": rt.get("age_hours") if complete else None,
            "baseline_stale": base.get("stale") if complete else None,
            "redteam_stale": rt.get("stale") if complete else None,
            "reconstructed_age_hours": {
                "baseline": _reconstructed_age(s, "baseline"),
                "redteam": _reconstructed_age(s, "redteam"),
            },
            "evidence_scope": s.get("evidence_scope"),
        })
    total = len(judged)
    completeness = round(complete_n / total, 4) if total else None
    if not samples:
        status, measured, rate = PENDING, "无 PASS 样本可判", None
    elif not judged:
        status, rate = PENDING, None
        measured = ("无非 legacy PASS 单可判（legacy v0 豁免 %d 单）"
                    % len(legacy_rows))
    else:
        status = OK if (complete_n == total and fresh_n == complete_n) else FAIL
        rate = completeness
        measured = "完整率 %.1f%%（%d/%d），新鲜 %d/%d，年龄门 ≤%.0fh" % (
            (completeness or 0) * 100, complete_n, total, fresh_n, total,
            EVIDENCE_MAX_AGE_HOURS)
        if excluded:
            measured += "；legacy v0 豁免 %d 单" % len(excluded)
    required = {
        "count": len(REQUIRED_FIELDS),
        "fields": list(REQUIRED_FIELDS),
        "note": "mode/blocking 同时校验取值（observe / false）；age/stale 取验收单"
                "工具落库值，48h 为认证口径证据年龄门（recorded age）",
    }
    detail = {"complete_count": complete_n, "total": total,
              "completeness_rate": rate,
              "fresh_count": fresh_n,
              "max_age_hours": EVIDENCE_MAX_AGE_HOURS,
              "legacy_policy": policy,
              "legacy_detected_count": len(legacy_rows),
              "legacy_excluded": {
                  "count": len(excluded),
                  "files": _cap([r["file"] for r in excluded]),
                  "samples": _cap(excluded),
              },
              "samples": _cap(rows), "required": required}
    if policy == "exempt" and legacy_rows and judged:
        detail["note"] = ("legacy v0 豁免：%d 单缺证据年龄字段，不计入⑤分母"
                          "（老田 2026-10-30 拍板；--legacy-v0-policy count 回旧行为）"
                          % len(legacy_rows))
    elif policy == "exempt" and legacy_rows and not judged:
        detail["note"] = ("无非 legacy PASS 单可判（legacy v0 豁免 %d 单）→ pending；"
                          "等带证据年龄字段的新口径 PASS 单入库后重跑"
                          % len(legacy_rows))
    elif policy == "count" and legacy_rows:
        detail["note"] = ("legacy v0 检出 %d 单：按 count 旧口径计入⑤分母，"
                          "缺字段照判 ✗（--legacy-v0-policy exempt 恢复豁免）"
                          % len(legacy_rows))
    return {"id": "⑤", "title": "证据链完整率 100% + 年龄 ≤48h",
            "status": status,
            "target": "%d 项必需字段完整率 100%%；证据年龄 ≤%.0fh"
                      % (len(REQUIRED_FIELDS), EVIDENCE_MAX_AGE_HOURS),
            "measured": measured,
            "value": {"complete": complete_n, "total": total,
                      "fresh": fresh_n, "completeness_rate": rate,
                      "legacy_excluded": len(excluded),
                      "legacy_policy": policy},
            "detail": detail}


def _audit_entries(data) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("samples", "units", "records", "audit", "items", "checks",
                    "results"):
            val = data.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
    return []


def _audit_consistent(rec: dict) -> bool | None:
    for key in ("consistent", "一致", "match", "matched", "pass", "ok"):
        if isinstance(rec.get(key), bool):
            return rec[key]
    for key in ("result", "conclusion", "verdict", "status"):
        v = rec.get(key)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("一致", "consistent", "pass", "ok", "通过", "match"):
                return True
            if s in ("不一致", "inconsistent", "fail", "不通过", "mismatch", "作废"):
                return False
    return None


def item6_audit(work_dir: Path) -> dict:
    path = Path(work_dir) / "audit_sample_3.json"
    detail = {"path": str(path), "expected": "赵云抽查 3 单一致记录（抽签种子+核对表）"}
    if not path.exists():
        detail["note"] = "抽查记录缺失 → ⑥ pending（11-03 赵云抽查 3 单后写入）"
        return {"id": "⑥", "title": "赵云抽查 3/3 一致", "status": PENDING,
                "target": "3 单抽查一致（3/3）", "measured": "抽查记录缺失",
                "value": {"consistent": 0, "total": 0}, "detail": detail}
    data, err = _read_json(path)
    if err is not None:
        detail["error"] = err
        detail["note"] = "抽查记录存在但不可读 → pending（需修复文件）"
        return {"id": "⑥", "title": "赵云抽查 3/3 一致", "status": PENDING,
                "target": "3 单抽查一致（3/3）", "measured": "记录不可读",
                "value": {"consistent": 0, "total": 0}, "detail": detail}
    entries = _audit_entries(data)
    seed = data.get("seed") if isinstance(data, dict) else None
    rows, consistent, inconsistent = [], 0, 0
    for rec in entries:
        val = _audit_consistent(rec)
        sha = rec.get("sha") or rec.get("commit")
        if isinstance(sha, dict):
            sha = sha.get("sha")
        rows.append({"file": rec.get("file") or rec.get("acceptance_file"),
                     "sha8": (sha or "")[:8] if isinstance(sha, str) else "",
                     "consistent": val})
        if val is True:
            consistent += 1
        elif val is False:
            inconsistent += 1
    total = len(rows)
    if not rows and isinstance(data, dict):
        # 允许汇总式记录：{"consistent": 3, "total": 3} 或 {"result": "3/3"}
        c = data.get("consistent")
        t = data.get("total") or data.get("count")
        if isinstance(c, int) and isinstance(t, int):
            total, consistent = t, c
        elif isinstance(data.get("result"), str):
            m = re.search(r"(\d+)\s*/\s*(\d+)", data["result"])
            if m:
                consistent, total = int(m.group(1)), int(m.group(2))
    detail.update({"seed": seed, "samples": _cap(rows),
                   "consistent": consistent, "inconsistent": inconsistent,
                   "total": total,
                   "note": "3/3 一致达标；任一项不一致 → 该单作废重验（本工具判 ✗）"})
    if total <= 0:
        status, measured = PENDING, "记录无可解析抽查条目"
    elif consistent >= 3 and inconsistent == 0:
        status, measured = OK, "%d/%d 一致" % (consistent, total)
    else:
        status, measured = FAIL, "%d/%d 一致（不一致 %d）" % (consistent, total,
                                                              inconsistent)
    return {"id": "⑥", "title": "赵云抽查 3/3 一致", "status": status,
            "target": "3 单抽查一致（3/3）", "measured": measured,
            "value": {"consistent": consistent, "total": total}, "detail": detail}


# --------------------------------------------------------------------- KPI 摘要

def _kpi(repo_root: Path, work_dir: Path, days: int, now: dt.datetime) -> dict:
    cutoff = now - dt.timedelta(days=days)
    daily = _read_jsonl(Path(work_dir) / "daily.jsonl")
    rows = []
    for rec in daily["rows"]:
        ts = _parse_ts(rec.get("ts"))
        if ts is not None and ts >= cutoff:
            rows.append(rec)
    counts = {"PASS": 0, "REJECT": 0, "OBSERVE": 0}
    for rec in rows:
        v = (rec.get("verdict") or "").upper()
        if v in counts:
            counts[v] += 1
    runs = len(rows)
    latest = max((r.get("ts") for r in rows), default=None)
    events = _read_jsonl(Path(repo_root) / "portal" / "data" / "dev_events.jsonl")
    merge_done = 0
    for rec in events["rows"]:
        if rec.get("kind") != "merge_done":
            continue
        ts = _parse_ts(rec.get("ts"))
        if ts is not None and ts >= cutoff:
            merge_done += 1
    coverage = round(runs / merge_done, 4) if merge_done else 0.0
    return {"window_days": days, "cutoff": _iso(cutoff),
            "runs": runs, "counts": counts,
            "pass_rate": round(counts["PASS"] / runs, 4) if runs else 0.0,
            "merge_done": merge_done,
            "merge_done_available": events["exists"],
            "coverage_rate": coverage,
            "daily_file": daily["path"], "daily_bad_lines": daily["bad_lines"],
            "dev_events_file": events["path"],
            "dev_events_bad_lines": events["bad_lines"],
            "latest_run": latest,
            "kpi_note": "覆盖率 = 窗口内 runs / merge_done（抽检回填单不计，"
                        "沿用决策包第六节口径）；误拒绝/漏放见 ③④"}


# ------------------------------------------------------------------ 报告渲染

def _summary(items: list[dict]) -> dict:
    passed = [it for it in items if it["status"] == OK]
    blocking = [{"id": it["id"], "title": it["title"], "status": it["status"],
                 "measured": it["measured"]}
                for it in items if it["status"] != OK]
    return {"overall_pass": len(passed) == len(items),
            "passed": len(passed), "total": len(items),
            "items": [{"id": it["id"], "title": it["title"],
                       "status": it["status"], "measured": it["measured"]}
                      for it in items],
            "blocking_items": blocking}


def _render_md(rep: dict) -> str:
    s = rep["summary"]
    md = ["# 黄忠认证 v2 六条复核报告", "",
          "- 生成时间：%s" % rep["generated_at"],
          "- 数据根：`%s`；工作目录：`%s`；窗口：%d 天"
          % (rep["repo_root"], rep["work_dir"], rep["window_days"]),
          "- 只读工具：不写业务代码/验收单/证据，仅落本复核报告",
          "",
          "## 一、六条总览", "",
          "| # | 指标 | 状态 | 实测 | 达标线 |",
          "|---|---|---|---|---|"]
    for it in rep["items"]:
        md.append("| %s | %s | %s | %s | %s |"
                  % (it["id"], it["title"], it["status"], it["measured"],
                     it["target"]))
    md += ["", "**综合：overall_pass = %s（%d/%d 条 ✓）**"
           % (s["overall_pass"], s["passed"], s["total"])]
    if s["blocking_items"]:
        md.append("")
        md.append("未达标/待补证项：")
        for b in s["blocking_items"]:
            md.append("- %s %s：%s（%s）" % (b["id"], b["title"], b["status"],
                                            b["measured"]))
    md += ["", "## 二、逐条明细"]
    for it in rep["items"]:
        md += ["", "### %s %s（%s）" % (it["id"], it["title"], it["status"]),
               "- 实测：%s；达标线：%s" % (it["measured"], it["target"])]
        d = it.get("detail") or {}
        if it["id"] == "①":
            md.append("- PASS 单 %d 份；排除演练单 %d 份；去重剔除 %d 份"
                      % (d.get("pass_sheets_raw", 0), len(d.get("excluded_drills") or []),
                         d.get("deduped_out", 0)))
            for row in d.get("samples") or []:
                md.append("  - %s %s（%s；scope=%s）"
                          % (row["sha8"], row["file"], row["generated_at"],
                             row.get("evidence_scope") or "-"))
        elif it["id"] == "②":
            if d.get("ledger_missing") or d.get("ledger_unreadable"):
                md.append("- 台账：%s" % ("缺失" if d.get("ledger_missing")
                                          else "不可读"))
                md.append("- 期望路径：`%s`" % d.get("expected_glob"))
                hint = d.get("ledger_schema_hint") or {}
                if hint:
                    md.append("- 台账 schema 示例：`%s`"
                              % json.dumps(hint, ensure_ascii=False))
            else:
                md.append("- 台账文件：%s；条目 %d"
                          % ("、".join(d.get("ledger_files") or []),
                             d.get("ledger_entries", 0)))
                for row in d.get("hits") or []:
                    md.append("  - %s（%s）：%s"
                              % (row.get("file") or "-", row.get("sha8") or "-",
                                 row.get("verdict")))
        elif it["id"] == "③":
            md.append("- 自然 REJECT %d 单；演练 REJECT %d 单；REJECT 总单 %d"
                      % (d.get("natural_reject_sheets", 0),
                         d.get("drill_reject_entries", 0), d.get("total_reject", 0)))
            md.append("- 人工复核清单：`%s`（已发现 %d 份、%d 行）"
                      % (d.get("manual_review_glob"),
                         len(d.get("manual_review_files") or []),
                         d.get("manual_review_rows", 0)))
            if d.get("reviewed") is not None:
                mode_txt = ("逐单人工复核（自然 REJECT<20）"
                            if d.get("review_mode") == "manual_per_sheet"
                            else "推翻率（自然 REJECT≥20）")
                md.append("- 口径=%s：已复核 %d/%d 单；推翻 %d 单"
                          % (mode_txt, d.get("reviewed", 0),
                             d.get("reject_total_unique") or d.get("total_reject", 0),
                             d.get("overturned", 0)))
            if d.get("note"):
                md.append("- %s" % d["note"])
        elif it["id"] == "④":
            md.append("- 满窗 PASS 单 %d；跟踪中 %d；漏放 %d 起（窗口 %d 天）"
                      % (d.get("mature_count", 0), d.get("tracking_count", 0),
                         d.get("leak_count", 0), d.get("window_days", 7)))
            for row in (d.get("mature_samples") or [])[:10]:
                md.append("  - 满窗 %s %s：%s"
                          % (row.get("sha8") or "-", row.get("window_end") or "-",
                             "漏放 %d" % len(row.get("leaks") or [])
                             if row.get("leak") else "无事故"))
            for row in (d.get("tracking_samples") or [])[:10]:
                md.append("  - 跟踪 %s：%s 满窗"
                          % (row.get("sha8") or "-", row.get("window_end") or "-"))
            if not d.get("incidents_exists"):
                md.append("- 注意：`incidents.jsonl` 缺失，按无已知事故计（需人工确认）")
        elif it["id"] == "⑤":
            md.append("- 证据链完整率 %s%%（%d/%d）；新鲜 %d/%d；年龄门 ≤%.0fh"
                      % (round((d.get("completeness_rate") or 0) * 100, 1),
                         d.get("complete_count", 0), d.get("total", 0),
                         d.get("fresh_count", 0), d.get("total", 0),
                         d.get("max_age_hours", 48)))
            legacy = d.get("legacy_excluded") or {}
            if legacy.get("count"):
                md.append("- legacy v0 豁免 %d 单（不计入⑤分母；口径=%s）：%s"
                          % (legacy.get("count"), d.get("legacy_policy"),
                             "、".join(legacy.get("files") or []) or "-"))
            elif d.get("legacy_detected_count"):
                md.append("- legacy v0 检出 %d 单（口径=%s：计入⑤分母）"
                          % (d.get("legacy_detected_count"), d.get("legacy_policy")))
            for row in d.get("samples") or []:
                if row.get("complete") and row.get("fresh"):
                    continue
                md.append("  - %s %s：缺字段=%s；max_age=%s；新鲜=%s"
                          % (row.get("sha8") or "-", row.get("file"),
                             "、".join(row.get("missing") or []) or "-",
                             row.get("max_age_hours"), row.get("fresh")))
            if d.get("note"):
                md.append("- %s" % d["note"])
        elif it["id"] == "⑥":
            md.append("- 抽查记录：`%s`（seed=%s）"
                      % (d.get("path"), d.get("seed")))
            if d.get("note"):
                md.append("- %s" % d["note"])
    kpi = rep["kpi"]
    md += ["", "## 三、本周 KPI 摘要（daily.jsonl）",
           "- 窗口 %d 天；runs=%d；PASS=%d REJECT=%d OBSERVE=%d"
           % (kpi["window_days"], kpi["runs"], kpi["counts"]["PASS"],
              kpi["counts"]["REJECT"], kpi["counts"]["OBSERVE"]),
           "- merge_done（分母）=%d（%s）；验收覆盖率=%.2f%%"
           % (kpi["merge_done"],
              "可读" if kpi["merge_done_available"] else "不可读",
              kpi["coverage_rate"] * 100),
           "- 口径：%s" % kpi["kpi_note"]]
    if rep.get("warnings"):
        md += ["", "## 四、输入与容错", ""]
        md += ["- " + w for w in rep["warnings"]]
    md += ["", "## 五、复跑命令", "",
           "```powershell",
           "cd C:\\项目\\老田的AI助手",
           "C:\\项目\\py312\\python.exe -X utf8 tools\\huangzhong_review.py --json",
           "```",
           "",
           "> 判据：docs/plans/2026-10-30_黄忠认证标准v2与验收工具证据口径拍板_v1.md 第二节；"
           "仅六条全 ✓ 才 overall_pass=True；pending/tracking 均不通过。", ""]
    return "\n".join(md)


# ------------------------------------------------------------------ 主流程

def review(work_dir: Path | str | None = None, out_dir: Path | str | None = None,
           days: int = 7, now: dt.datetime | None = None,
           repo_root: Path | str | None = None,
           legacy_v0_policy: str = LEGACY_V0_POLICY_DEFAULT) -> dict:
    """执行六条复核并落 JSON/MD 报告（只读数据，只写报告）。

    work_dir 缺省 = repo_root/work/huangzhong（repo_root 缺省=工具所在仓库根）；
    out_dir 缺省 = work_dir；now 可注入以获得确定性（测试用）；
    legacy_v0_policy=exempt（默认，老田 2026-10-30 拍板口径）| count（旧行为）。
    """
    now = now or _now()
    root = Path(repo_root) if repo_root else ROOT
    work = Path(work_dir) if work_dir else root / "work" / "huangzhong"
    out = Path(out_dir) if out_dir else work
    days = int(days) if int(days) > 0 else 7
    policy = (legacy_v0_policy if legacy_v0_policy in LEGACY_V0_POLICIES
              else LEGACY_V0_POLICY_DEFAULT)
    warnings: list[str] = []

    acc = load_acceptance(work)
    drills = load_drill_ledger(work)
    _norm_ledger_verdicts(drills, acc["sheets"])
    for row in acc["invalid"]:
        warnings.append("验收单不可读已跳过：%s（%s）" % (row["file"], row["error"]))
    for row in drills["bad"]:
        warnings.append("drill 台账条目/文件异常：%s（%s）"
                        % (row.get("file") or "-", row.get("error")))
    if not drills["files"]:
        warnings.append("drill 台账缺失：② 判 pending（work/huangzhong/drills/"
                        "drill_ledger_*.json 未生成）")
    elif not drills["readable"]:
        warnings.append("drill 台账全部不可读：② 判 pending")

    items = [
        item1_pass_samples(acc, drills),
        item2_drill(drills),
        item3_misreject(acc, drills, work),
        item4_leakage(acc, drills, work, now),
        item5_evidence(acc, drills, policy),
        item6_audit(work),
    ]
    kpi = _kpi(root, work, days, now)
    if kpi["daily_bad_lines"]:
        warnings.append("daily.jsonl 坏行 %d 行已跳过" % kpi["daily_bad_lines"])
    if kpi["dev_events_bad_lines"]:
        warnings.append("portal/data/dev_events.jsonl 坏行 %d 行已跳过"
                        % kpi["dev_events_bad_lines"])
    leak_detail = items[3]["detail"]
    if leak_detail.get("incidents_bad_lines"):
        warnings.append("incidents.jsonl 坏行 %d 行已跳过"
                        % leak_detail["incidents_bad_lines"])
    if leak_detail.get("mature_count") and not leak_detail.get("incidents_exists"):
        warnings.append("incidents.jsonl 缺失：④ 按无已知事故计，需人工确认事故台账")
    if leak_detail.get("unattributed_incidents"):
        warnings.append("incidents.jsonl 有 %d 条窗口内事故未标注 commit，未计入漏放"
                        "（需人工核对）" % len(leak_detail["unattributed_incidents"]))
    rep = {
        "ok": True,
        "tool": "huangzhong_review.py",
        "version": "v1.1 (2026-10-30)",
        "generated_at": _iso(now),
        "window_days": days,
        "repo_root": str(root),
        "work_dir": str(work),
        "read_only": True,
        "legacy_v0_policy": policy,
        "targets": {"pass_samples": PASS_TARGET, "drills": DRILL_TARGET,
                    "natural_reject_min": NATURAL_REJECT_MIN,
                    "misreject_rate_max": MISREJECT_RATE_MAX,
                    "evidence_max_age_hours": EVIDENCE_MAX_AGE_HOURS,
                    "maturity_days": MATURITY_DAYS,
                    "legacy_v0_policy": policy},
        "items": items,
        "summary": _summary(items),
        "kpi": kpi,
        "inputs": {
            "acceptance_files": len(acc["files"]),
            "acceptance_invalid": acc["invalid"],
            "drill_ledger_files": drills["files"],
            "incidents_file": str(Path(work) / "incidents.jsonl"),
            "audit_file": str(Path(work) / "audit_sample_3.json"),
            "daily_file": kpi["daily_file"],
            "dev_events_file": kpi["dev_events_file"],
        },
        "warnings": warnings,
    }
    out.mkdir(parents=True, exist_ok=True)
    ts = now.strftime("%Y%m%d_%H%M%S")
    jp = out / ("review_%s.json" % ts)
    mp = out / ("review_%s.md" % ts)
    jp.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    mp.write_text(_render_md(rep), encoding="utf-8")
    rep["report_json"] = str(jp)
    rep["report_md"] = str(mp)
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="黄忠认证 v2 六条一键复核（只读）")
    ap.add_argument("--json", action="store_true", help="输出完整复核报告 JSON")
    ap.add_argument("--out-dir", default="", help="报告输出目录（默认 work/huangzhong）")
    ap.add_argument("--days", type=int, default=7, help="KPI 窗口天数（默认 7）")
    ap.add_argument("--work-dir", default="",
                    help="数据目录（默认 <repo-root>/work/huangzhong）")
    ap.add_argument("--repo-root", default="",
                    help="仓库根（默认工具所在仓库；worktree 内 dry-run 主仓时显式指定）")
    ap.add_argument("--legacy-v0-policy", choices=list(LEGACY_V0_POLICIES),
                    default=LEGACY_V0_POLICY_DEFAULT,
                    help="⑤ v0 旧验收单（缺证据年龄字段）口径：exempt=豁免不计入"
                         "⑤分母（老田 2026-10-30 拍板，默认）；count=旧行为（计入分母）")
    args = ap.parse_args(argv)
    rep = review(work_dir=args.work_dir or None,
                 out_dir=args.out_dir or None,
                 days=args.days,
                 repo_root=args.repo_root or None,
                 legacy_v0_policy=args.legacy_v0_policy)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print("六条复核：%s → overall_pass=%s（%d/%d ✓）；报告：%s"
              % (" ".join("%s%s" % (it["id"], it["status"]) for it in rep["items"]),
                 rep["summary"]["overall_pass"], rep["summary"]["passed"],
                 rep["summary"]["total"], rep.get("report_md")))
    return 0 if rep["summary"]["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
