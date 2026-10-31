# -*- coding: utf-8 -*-
"""candidate_evidence_guard.py — W3 候选证据包只读校验器（2026-10-31）。

用途：11-08「ACAD1 候选冻结」前的机器可读闸门——防止「无数据版本 / 无 PIT /
无 raw 证据 / 无样本外 / 无失效条件 / 无 L4 记录 / 无假设与结论」的候选进入冻结。
只校验**证据包本身**，不选候选、不批冻结、不代签、不改任何账本。

输入（只读）：候选证据包 JSON / YAML / MD（MD 需含 ```json 代码块或 YAML front-matter）。
  兼容三种形态：
    1) 单候选对象（如 research_artifact/v2：顶层含 candidate + data + backtest ...）；
    2) 包装对象 `{"candidates": [ ... ]}`；
    3) 顶层数组 `[ ... ]`。

校验项（每项 pass/fail/unknown）：
  candidate_id / symbols / data_version(+sha256) / freeze_receipt(存在+sha匹配) /
  as_of / PIT(A|B) / evidence_refs(≥1 raw ref 且可复核) / backtest+OOS(样本外交易数≥阈值) /
  invalidation(失效条件) / L4(status 合法；established 需 artifact_ref 有效) /
  hypothesis / decision(verdict)。

输出：JSON + Markdown（stdout 或 --out 目录）；退出码 0=全 pass / 1=有 fail /
2=unknown（缺文件/坏格式/用法错误）。**失败项一律标 unknown 并保留错误原文，不伪造 pass。**

安全边界：不联网、不读写数据库、不写冻结回执/任务板/approvals；只读文件 +
stdout/--out 落盘（--out 仅写 `<date>_候选证据包校验_v1.md/.json` 两个文件）。

用法：
    python -X utf8 tools/candidate_evidence_guard.py --input <候选包.json>
    python -X utf8 tools/candidate_evidence_guard.py --input a.json b.yaml c.md --json
    python -X utf8 tools/candidate_evidence_guard.py --input x.json --out deliverables
    # 阈值（W3 原文 ≥10；ACAD1 复评更严 ≥30）：
    python -X utf8 tools/candidate_evidence_guard.py --input x.json --min-oos-trades 30

退出码：0=全部 pass；1=任一 fail；2=unknown/用法错误（argparse 亦为 2）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA = "candidate_evidence_guard/v1"
DEFAULT_MIN_OOS_TRADES = 10
DEFAULT_DATE_FMT = "%Y-%m-%d"
STATUS_LABEL = {"pass": "PASS", "fail": "FAIL", "unknown": "UNKNOWN"}
PIT_OK = ("A", "B")
L4_STATUSES = ("established", "unresolved", "missing", "unknown")
DECISION_VALUES = ("go", "no-go", "nogo", "no_go", "pending", "hold", "deferred",
                   "rejected", "approved")
REQUIRED_FIELDS = (
    "candidate_id", "symbols", "data_version(+sha256)", "freeze_receipt", "as_of",
    "PIT(A|B)", "evidence_refs(≥1 raw ref)", "backtest+OOS(交易数阈值)",
    "invalidation", "L4(status；established 需 artifact_ref)", "hypothesis", "decision(verdict)",
)
NOTES = (
    "只读校验：不联网、不读写数据库、不写冻结回执/任务板/approvals、不代签",
    "失败项（缺文件/坏格式/字段缺失/raw_ref 复核失败）一律如实标 fail/unknown 并保留错误原文，不伪造",
    "raw ref 复核复用 agent_platform/engine/evidence_bundle.verify_raw_ref（文件存在+sha256+行区间+selector）",
    "L4 established 的 artifact_ref 复核复用 agent_platform/engine/l4_artifact.validate_artifact_ref；unresolved 允许（候选可为 No-Go）",
    "本工具只判「证据包是否满足冻结前置」；Go/No-Go 与冻结由 ACAD1/D1/A0 决定",
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SHA_ANY_RE = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{64})(?![0-9a-fA-F])")
_SYMBOL_RE = re.compile(r"^\d{6}(\.(?:SH|SZ|BJ))?$")
_MD_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.S)
_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def repo_root(start=None) -> Path:
    """主工作树根（worktree 内回落主仓，复用 tools/_repo_paths.py）。"""
    here = Path(start or __file__).resolve()
    try:
        sys.path.insert(0, str(here.parent))
        from _repo_paths import repo_root as _rr  # type: ignore

        return Path(_rr(here))
    except Exception:  # noqa: BLE001 - 兜底按相对根推断
        return here.parents[1]
    finally:
        try:
            sys.path.pop(0)
        except IndexError:
            pass


def _resolve_path(ref, roots) -> tuple[Path | None, str]:
    """把仓库相对/绝对路径解析为文件（候选路径在多个 root 下按序尝试）。"""
    text = str(ref or "").strip()
    if not text:
        return None, "missing_path"
    p = Path(text)
    if p.is_absolute():
        return (p, "") if p.is_file() else (None, "not_found:%s" % p)
    for root in roots:
        cand = Path(root) / p
        if cand.is_file():
            return cand, ""
    return None, "not_found:%s" % text


def _load_engine(root: Path):
    """惰性导入 engine 模块（evidence_bundle / l4_artifact）；失败返回 (None, None, err)。

    优先 ``<root>/agent_platform/engine``；root 不是完整仓库时（测试/离线 mock）
    回落本工具所在主仓，保证 raw ref / artifact_ref 复核能力不因 root 切换丢失。
    """
    candidates = []
    for cand in (Path(root), repo_root()):
        try:
            resolved = Path(cand).resolve()
        except OSError:
            continue
        if resolved not in candidates:
            candidates.append(resolved)
    engine_dir = None
    for cand in candidates:
        probe = cand / "agent_platform" / "engine"
        if probe.is_dir():
            engine_dir = probe
            break
    if engine_dir is None:
        return None, None, "engine 目录不存在（已尝试 %s）" % ", ".join(map(str, candidates))
    sys.path.insert(0, str(engine_dir))
    try:
        import evidence_bundle  # type: ignore
        import l4_artifact  # type: ignore

        return evidence_bundle, l4_artifact, ""
    except Exception as exc:  # noqa: BLE001 - 导入失败按 unknown 处理，不伪造
        return None, None, "engine 导入失败：%s: %s" % (type(exc).__name__, str(exc)[:200])
    finally:
        try:
            sys.path.pop(0)
        except IndexError:
            pass



# ---------------------------------------------------------------------------
# 输入加载（JSON / YAML / MD）
# ---------------------------------------------------------------------------
def load_candidate_file(path) -> dict:
    """读候选证据包；返回 {ok, mode, payload|error}。坏格式/缺文件 → ok=False。"""
    p = Path(path)
    meta = {"path": str(p), "ok": False, "mode": "", "payload": None, "error": ""}
    if not p.is_file():
        meta["error"] = "输入文件不存在：%s" % p
        return meta
    try:
        text = p.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        meta["error"] = "输入文件不可读：%s: %s" % (type(exc).__name__, str(exc)[:160])
        return meta
    suffix = p.suffix.lower()
    if suffix == ".json":
        try:
            meta["payload"] = json.loads(text)
            meta["mode"] = "json"
            meta["ok"] = True
        except ValueError as exc:
            meta["error"] = "JSON 解析失败：%s" % str(exc)[:200]
        return meta
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore

            payload = yaml.safe_load(text)
            meta["payload"] = payload
            meta["mode"] = "yaml"
            meta["ok"] = True
        except ImportError:
            meta["error"] = "PyYAML 不可用，无法解析 YAML"
        except Exception as exc:  # noqa: BLE001
            meta["error"] = "YAML 解析失败：%s: %s" % (type(exc).__name__, str(exc)[:160])
        return meta
    if suffix == ".md":
        match = _MD_JSON_RE.search(text)
        if match:
            try:
                meta["payload"] = json.loads(match.group(1))
                meta["mode"] = "md_json_block"
                meta["ok"] = True
            except ValueError as exc:
                meta["error"] = "MD 内 JSON 代码块解析失败：%s" % str(exc)[:200]
            return meta
        front = _FRONT_MATTER_RE.search(text)
        if front:
            try:
                import yaml  # type: ignore

                meta["payload"] = yaml.safe_load(front.group(1))
                meta["mode"] = "md_front_matter"
                meta["ok"] = True
            except ImportError:
                meta["error"] = "MD front-matter 需 PyYAML（不可用）"
            except Exception as exc:  # noqa: BLE001
                meta["error"] = "MD front-matter 解析失败：%s: %s" % (
                    type(exc).__name__, str(exc)[:160])
            return meta
        meta["error"] = "MD 内无 ```json 代码块或 YAML front-matter，无法机器校验"
        return meta
    meta["error"] = "不支持的后缀（仅 .json/.yaml/.yml/.md）：%s" % p.suffix
    return meta


def normalize_candidates(payload) -> list:
    """把三种输入形态归一为候选上下文字典列表（保持原对象引用，便于定位字段）。"""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    inner = payload.get("candidates")
    if isinstance(inner, list):
        return [x for x in inner if isinstance(x, dict)]
    return [payload]


# ---------------------------------------------------------------------------
# 字段提取
# ---------------------------------------------------------------------------
def _dig(ctx, *keys, default=None):
    cur = ctx
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur if cur is not None else default


def _first_non_empty(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value not in (None, "", [], {}):
            return value
    return None


def _normalize_symbols(candidate: dict, ctx: dict) -> list:
    raw = _first_non_empty(candidate.get("symbols"), candidate.get("symbol"),
                           ctx.get("symbols"), ctx.get("symbol"), ctx.get("code"))
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    out = []
    for item in items:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def extract_view(ctx: dict) -> dict:
    """抽取校验所需视图（不修改输入）。"""
    candidate = ctx.get("candidate") if isinstance(ctx.get("candidate"), dict) else ctx
    data = ctx.get("data") if isinstance(ctx.get("data"), dict) else {}
    backtest = ctx.get("backtest") if isinstance(ctx.get("backtest"), dict) else {}
    view = {
        "candidate": candidate,
        "data": data,
        "backtest": backtest,
        "candidate_id": _first_non_empty(candidate.get("candidate_id"),
                                         ctx.get("candidate_id"),
                                         candidate.get("id"), ctx.get("id")),
        "symbols": _normalize_symbols(candidate, ctx),
        "data_version": _first_non_empty(data.get("data_version"),
                                         ctx.get("data_version")),
        "data_sha256": _first_non_empty(data.get("sha256"), data.get("data_sha256"),
                                        data.get("data_sha"), ctx.get("sha256")),
        "freeze_receipt": _first_non_empty(data.get("freeze_receipt"),
                                           ctx.get("freeze_receipt")),
        "as_of": _first_non_empty(data.get("frozen_at"), data.get("as_of"),
                                  ctx.get("as_of"), ctx.get("frozen_at")),
        "pit_verdict": _first_non_empty(data.get("pit_verdict"), ctx.get("pit_verdict"),
                                        data.get("pit"), ctx.get("pit")),
        "evidence_refs": _first_non_empty(ctx.get("evidence_refs"), ctx.get("evidence"),
                                          candidate.get("evidence_refs")),
        "sample_out": _first_non_empty(data.get("sample_out"), backtest.get("sample_out"),
                                       backtest.get("oos_window"), ctx.get("sample_out")),
        "invalidation": _first_non_empty(ctx.get("falsification_conditions"),
                                         ctx.get("invalidations"),
                                         ctx.get("invalidation"),
                                         ctx.get("invalidation_conditions"),
                                         ctx.get("fail_conditions")),
        "l4": _first_non_empty(ctx.get("l4"), ctx.get("l4_status")),
        "hypothesis": _first_non_empty(ctx.get("hypothesis"),
                                       _dig(ctx, "strategy_spec", "hypothesis")),
        "decision": _first_non_empty(ctx.get("verdict"), ctx.get("decision"),
                                     ctx.get("go_no_go"), candidate.get("verdict"),
                                     ctx.get("conclusion")),
    }
    return view


# ---------------------------------------------------------------------------
# 单项校验
# ---------------------------------------------------------------------------
def _check(cid: str, title: str, status: str, detail: str,
           required: bool = True, evidence=None) -> dict:
    return {"id": cid, "title": title, "required": required,
            "status": status, "detail": str(detail or "")[:400],
            "evidence": evidence if evidence is not None else {}}


def check_candidate_id(view: dict) -> dict:
    value = view.get("candidate_id")
    if not isinstance(value, str) or not value.strip():
        return _check("candidate_id", "candidate_id", "fail", "缺少 candidate_id")
    return _check("candidate_id", "candidate_id", "pass", value)


def check_symbols(view: dict) -> dict:
    symbols = view.get("symbols") or []
    if not symbols:
        return _check("symbols", "symbols", "fail", "缺少 symbol(s)")
    bad = [s for s in symbols if not _SYMBOL_RE.match(s)]
    if bad:
        return _check("symbols", "symbols", "fail",
                      "symbol 格式不合法（需 6 位代码[.SH|SZ|BJ]）：%s" % ",".join(bad[:5]),
                      evidence={"symbols": symbols})
    return _check("symbols", "symbols", "pass", ",".join(symbols))


def _sha_from_version(data_version: str) -> str:
    match = _SHA_ANY_RE.search(str(data_version or ""))
    return match.group(1) if match else ""


def check_data_version(view: dict) -> dict:
    """data_version：非空且含 64 位 sha256（冻结指纹）。"""
    data_version = view.get("data_version")
    data_sha = str(view.get("data_sha256") or "").strip()
    version_sha = _sha_from_version(data_version or "")
    sha = data_sha if _SHA256_RE.match(data_sha) else version_sha
    if not data_version:
        return _check("data_version", "data_version(+sha256)", "fail",
                      "缺少 data.data_version")
    if not sha:
        return _check("data_version", "data_version(+sha256)", "fail",
                      "data_version/sha256 中未找到 64 位 sha256")
    return _check("data_version", "data_version(+sha256)", "pass",
                  "sha256=%s…" % sha[:16], evidence={"sha256": sha})



def check_freeze_receipt(view: dict, repo_roots) -> tuple[dict, dict]:
    """freeze_receipt：文件存在 + JSON 可读 + data_version 内 sha 与候选 sha 一致。"""
    receipt_ref = view.get("freeze_receipt")
    extra = {"path": "", "sha": ""}
    if not receipt_ref:
        return _check("freeze_receipt", "freeze_receipt(存在+sha匹配)", "fail",
                      "缺少 freeze_receipt（候选冻结必须引用冻结回执）"), extra
    path, reason = _resolve_path(receipt_ref, repo_roots)
    if path is None:
        return _check("freeze_receipt", "freeze_receipt(存在+sha匹配)", "fail",
                      "冻结回执不可解析：%s" % reason), extra
    extra["path"] = str(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, ValueError) as exc:
        return _check("freeze_receipt", "freeze_receipt(存在+sha匹配)", "fail",
                      "冻结回执不可读/非 JSON：%s" % str(exc)[:160]), extra
    if not isinstance(doc, dict):
        return _check("freeze_receipt", "freeze_receipt(存在+sha匹配)", "fail",
                      "冻结回执顶层不是对象"), extra
    receipt_sha = ""
    for key in ("data_version", "sha256", "data_sha"):
        value = doc.get(key)
        if isinstance(value, str):
            match = _SHA_ANY_RE.search(value)
            if match:
                receipt_sha = match.group(1)
                break
    extra["sha"] = receipt_sha
    status = str(doc.get("status") or "").strip().lower()
    if not receipt_sha:
        return _check("freeze_receipt", "freeze_receipt(存在+sha匹配)", "fail",
                      "冻结回执无 sha256（四字段/指纹缺失）", evidence={"path": str(path)}), extra
    cand_sha = str(view.get("data_sha256") or "") or _sha_from_version(view.get("data_version"))
    if cand_sha and receipt_sha.lower() != cand_sha.lower():
        return _check("freeze_receipt", "freeze_receipt(存在+sha匹配)", "fail",
                      "回执 sha 与候选 data sha 不一致：%s vs %s"
                      % (receipt_sha[:16], cand_sha[:16]),
                      evidence={"path": str(path)}), extra
    if status and status not in ("final", "frozen", "signed"):
        return _check("freeze_receipt", "freeze_receipt(存在+sha匹配)", "fail",
                      "回执 status=%s（非 final/frozen/signed）" % status,
                      evidence={"path": str(path)}), extra
    return _check("freeze_receipt", "freeze_receipt(存在+sha匹配)", "pass",
                  "%s（sha=%s…）" % (Path(str(receipt_ref)).name, receipt_sha[:16]),
                  evidence={"path": str(path)}), extra


def _parse_time(text):
    value = str(text or "").strip()
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    patterns = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
    for fmt in patterns:
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def check_as_of(view: dict) -> dict:
    as_of = view.get("as_of")
    if not as_of:
        return _check("as_of", "as_of", "fail", "缺少 as_of/frozen_at")
    if _parse_time(as_of) is None:
        return _check("as_of", "as_of", "fail", "as_of 无法解析为时间：%r" % str(as_of)[:80])
    return _check("as_of", "as_of", "pass", str(as_of))


def check_pit(view: dict) -> dict:
    text = str(view.get("pit_verdict") or "")
    if not text:
        return _check("pit", "PIT(A|B)", "fail", "缺少 pit_verdict（PIT 证据等级）")
    match = re.search(r"(?i)(?:^|[^A-Za-z])([ABC])(?![A-Za-z])", text)
    grade = match.group(1).upper() if match else ""
    if grade == "A":
        return _check("pit", "PIT(A|B)", "pass", "PIT=A（字节级可核验）")
    if grade in PIT_OK:
        return _check("pit", "PIT(A|B)", "pass",
                      "PIT=%s（B：窗口/抓取时点可核验；无字节级快照，记缺口）" % grade)
    if grade == "C":
        return _check("pit", "PIT(A|B)", "fail", "PIT=C（仅文件级/不可核验），不足冻结")
    return _check("pit", "PIT(A|B)", "unknown",
                  "pit_verdict 无法归类 A/B/C：%r" % text[:80])


def _raw_ref_of(ref: dict) -> bool:
    if not isinstance(ref, dict):
        return False
    schema = str(ref.get("schema") or "")
    has_uri = bool(str(ref.get("raw_uri") or "").strip())
    has_sha = bool(_SHA256_RE.match(str(ref.get("raw_sha256") or "")))
    return (schema == "evidence_ref/v2" and has_uri and has_sha) or (has_uri and has_sha)


def check_evidence_refs(view: dict, raw_root, engine) -> dict:
    refs = view.get("evidence_refs")
    if isinstance(refs, dict):
        refs = refs.get("refs") or refs.get("items") or list(refs.values())
    if not isinstance(refs, list) or not refs:
        return _check("evidence_refs", "evidence_refs(≥1 raw ref)", "fail",
                      "缺少 evidence_refs（不得只凭摘要/文本自证）")
    raw_refs = [r for r in refs if _raw_ref_of(r)]
    if not raw_refs:
        return _check("evidence_refs", "evidence_refs(≥1 raw ref)", "fail",
                      "evidence_refs 有 %d 条但**无 raw ref**（仅摘要 ref，不满足 raw 优先）"
                      % len(refs), evidence={"refs_total": len(refs), "raw": 0})
    if engine is None:
        return _check("evidence_refs", "evidence_refs(≥1 raw ref)", "unknown",
                      "engine(evidence_bundle) 不可用，无法复核 raw ref",
                      evidence={"refs_total": len(refs), "raw": len(raw_refs)})
    evidence_bundle, _l4 = engine
    if evidence_bundle is None:
        return _check("evidence_refs", "evidence_refs(≥1 raw ref)", "unknown",
                      "engine 导入失败，无法复核 raw ref")
    verified, failures = 0, []
    for index, ref in enumerate(raw_refs):
        try:
            ok, reason = evidence_bundle.verify_raw_ref(ref, root=raw_root)
        except Exception as exc:  # noqa: BLE001 - 复核异常按 failed 记录，不伪造成功
            ok, reason = False, "verify_exception:%s" % type(exc).__name__
        if ok:
            verified += 1
        else:
            failures.append({"index": index,
                             "source": str((ref or {}).get("source") or ""),
                             "raw_uri": str((ref or {}).get("raw_uri") or "")[:160],
                             "reason": str(reason)[:160]})
    evidence = {"refs_total": len(refs), "raw": len(raw_refs),
                "verified": verified, "failures": failures[:5]}
    if failures:
        return _check("evidence_refs", "evidence_refs(≥1 raw ref)", "fail",
                      "%d/%d raw ref 复核失败（%s）" % (
                          len(failures), len(raw_refs),
                          ", ".join(sorted({f["reason"] for f in failures}))[:200]),
                      evidence=evidence)
    return _check("evidence_refs", "evidence_refs(≥1 raw ref)", "pass",
                  "%d 条 ref，其中 raw=%d 全部复核通过（sha/行区间/selector）"
                  % (len(refs), verified), evidence=evidence)


def _oos_trades(backtest: dict) -> tuple:
    for key in ("executable_trades_out_sample", "trades_out_sample",
                "oos_trades", "executable_oos_trades"):
        value = backtest.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value), key
    trades = backtest.get("trades")
    if isinstance(trades, list):
        return len(trades), "trades(len)"
    return None, ""


def check_backtest(view: dict, min_oos_trades: int) -> dict:
    backtest = view.get("backtest") or {}
    if not isinstance(backtest, dict) or not backtest:
        return _check("backtest", "backtest+OOS", "fail", "缺少 backtest（样本外回测）")
    sample_out = view.get("sample_out")
    if not sample_out:
        return _check("backtest", "backtest+OOS", "fail",
                      "缺少样本外窗口 sample_out/oos_window")
    trades, source = _oos_trades(backtest)
    if trades is None:
        return _check("backtest", "backtest+OOS", "fail",
                      "缺少样本外可执行交易数（executable_trades_out_sample 等）")
    has_ret = any(isinstance(backtest.get(k), (int, float)) and
                  not isinstance(backtest.get(k), bool)
                  for k in ("avg_ret", "cum_ret", "mean_ret", "median_ret"))
    evidence = {"oos_trades": trades, "source": source,
                "min_oos_trades": min_oos_trades, "sample_out": str(sample_out)[:160]}
    if not has_ret:
        return _check("backtest", "backtest+OOS", "fail",
                      "缺少样本外收益指标（avg_ret/cum_ret 等）", evidence=evidence)
    if trades < int(min_oos_trades):
        return _check("backtest", "backtest+OOS", "fail",
                      "样本外可执行交易 %d < 阈值 %d（W3 条件②）"
                      % (trades, min_oos_trades), evidence=evidence)
    return _check("backtest", "backtest+OOS", "pass",
                  "OOS 交易 %d≥%d；窗口=%s" % (trades, min_oos_trades,
                                               str(sample_out)[:80]), evidence=evidence)


def check_invalidation(view: dict) -> dict:
    value = view.get("invalidation")
    if isinstance(value, dict):
        value = value.get("conditions") or list(value.values())
    if not isinstance(value, list) or not [x for x in value if x]:
        return _check("invalidation", "invalidation(失效条件)", "fail",
                      "缺少失效/证伪条件（invalidation/falsification_conditions）")
    ids, triggered = [], []
    for index, item in enumerate(value):
        if isinstance(item, dict):
            cid = str(item.get("id") or item.get("condition") or "")
            ids.append(cid or "cond-%d" % index)
            if item.get("triggered") is True:
                triggered.append(cid or "cond-%d" % index)
        elif isinstance(item, str) and item.strip():
            ids.append("cond-%d" % index)
        else:
            return _check("invalidation", "invalidation(失效条件)", "fail",
                          "失效条件条目非法（应为对象/字符串）：index=%d" % index)
    evidence = {"count": len(ids), "ids": ids[:20], "triggered": triggered[:20]}
    detail = "共 %d 条" % len(ids)
    if triggered:
        detail += "；已触发 %d 条（%s）→ 候选结论应为准入 No-Go/待补证，不阻断本项" % (
            len(triggered), ",".join(triggered[:5]))
    return _check("invalidation", "invalidation(失效条件)", "pass", detail,
                  evidence=evidence)


def check_l4(view: dict, artifact_root, engine) -> dict:
    l4 = view.get("l4")
    if isinstance(l4, str):
        l4 = {"status": l4}
    if not isinstance(l4, dict) or not l4:
        return _check("l4", "L4(status)", "fail", "缺少 L4 状态（l4.status）")
    status = str(l4.get("status") or "").strip().lower()
    if status not in L4_STATUSES:
        return _check("l4", "L4(status)", "fail",
                      "L4 status 非法：%r（允许 established/unresolved/missing/unknown）"
                      % status)
    evidence = {"status": status, "verdict": l4.get("verdict"),
                "source": l4.get("source"), "as_of": l4.get("as_of"),
                "artifact_ref": l4.get("artifact_ref") or ""}
    if status != "established":
        return _check("l4", "L4(status)", "pass",
                      "L4=%s（非 established，允许进冻结但方向仍被 DecisionPolicy 锁）；verdict=%s"
                      % (status, l4.get("verdict") or "-"), evidence=evidence)
    ref = str(l4.get("artifact_ref") or "").strip()
    if not ref:
        return _check("l4", "L4(status)", "fail",
                      "L4=established 但缺 artifact_ref（P1 政策要求）", evidence=evidence)
    if engine is None:
        return _check("l4", "L4(status)", "unknown",
                      "engine(l4_artifact) 不可用，无法复核 artifact_ref", evidence=evidence)
    _ev, l4_artifact = engine
    if l4_artifact is None:
        return _check("l4", "L4(status)", "unknown",
                      "engine 导入失败，无法复核 artifact_ref", evidence=evidence)
    try:
        ok, reason, detail = l4_artifact.validate_artifact_ref(
            ref, root=artifact_root)
    except Exception as exc:  # noqa: BLE001
        ok, reason, detail = False, "validate_exception:%s" % type(exc).__name__, ""
    evidence["artifact_detail"] = str(detail)[:200]
    if not ok:
        return _check("l4", "L4(status)", "fail",
                      "L4=established 但 artifact_ref 无效：%s" % reason, evidence=evidence)
    return _check("l4", "L4(status)", "pass",
                  "L4=established；artifact_ref 有效（%s）" % Path(ref).name,
                  evidence=evidence)


def check_hypothesis_decision(view: dict) -> list:
    checks = []
    hypothesis = view.get("hypothesis")
    if not isinstance(hypothesis, str) or len(hypothesis.strip()) < 10:
        checks.append(_check("hypothesis", "hypothesis", "fail",
                             "缺少可证伪假设 hypothesis（<10 字符）"))
    else:
        checks.append(_check("hypothesis", "hypothesis", "pass",
                             hypothesis.strip()[:200]))
    decision = str(view.get("decision") or "").strip()
    if not decision:
        checks.append(_check("decision", "decision(verdict)", "fail",
                             "缺少结论 decision/verdict（Go/No-Go/Pending…）"))
    else:
        low = decision.lower().replace(" ", "")
        hit = any(v in low for v in DECISION_VALUES)
        checks.append(_check("decision", "decision(verdict)", "pass", decision[:160],
                             evidence={"normalized": low, "recognized": hit}))
    return checks


# ---------------------------------------------------------------------------
# 候选级校验
# ---------------------------------------------------------------------------
def validate_candidate(ctx: dict, *, repo_roots, raw_root, artifact_root,
                       engine, min_oos_trades: int) -> dict:
    view = extract_view(ctx)
    checks = []
    checks.append(check_candidate_id(view))
    checks.append(check_symbols(view))
    checks.append(check_data_version(view))
    checks.append(check_freeze_receipt(view, repo_roots)[0])
    checks.append(check_as_of(view))
    checks.append(check_pit(view))
    checks.append(check_evidence_refs(view, raw_root, engine))
    checks.append(check_backtest(view, min_oos_trades))
    checks.append(check_invalidation(view))
    checks.append(check_l4(view, artifact_root, engine))
    checks.extend(check_hypothesis_decision(view))
    statuses = [c["status"] for c in checks]
    if "fail" in statuses:
        verdict = "fail"
    elif "unknown" in statuses:
        verdict = "unknown"
    else:
        verdict = "pass"
    gaps = [{"id": c["id"], "status": c["status"], "detail": c["detail"]}
            for c in checks if c["status"] != "pass"]
    return {
        "candidate_id": view.get("candidate_id") or "",
        "symbols": view.get("symbols") or [],
        "verdict": verdict,
        "checks": checks,
        "gaps": gaps,
    }


def validate_report(inputs, *, repo_root_path=None, raw_root=None, artifact_root=None,
                    min_oos_trades: int = DEFAULT_MIN_OOS_TRADES,
                    date: str | None = None, now: dt.datetime | None = None) -> dict:
    """校验一组候选证据包（只读），返回报告 dict（不写任何文件）。"""
    started = dt.datetime.now().astimezone() if now is None else now
    repo = Path(repo_root_path or repo_root()).resolve()
    raw = Path(raw_root or repo).resolve()
    artifact = Path(artifact_root) if artifact_root else (
        repo / "agent_platform" / "data" / "academy" / "artifacts")
    engine = _load_engine(repo)
    engine_tuple = (engine[0], engine[1]) if engine[2] == "" else (None, None)
    engine_error = engine[2]
    repo_roots = [raw, repo]
    candidates = []
    inputs_meta = []
    for item in inputs:
        loaded = load_candidate_file(item)
        inputs_meta.append({"path": loaded["path"], "ok": loaded["ok"],
                            "mode": loaded["mode"], "error": loaded["error"]})
        if not loaded["ok"]:
            candidates.append({"candidate_id": "", "symbols": [], "verdict": "unknown",
                               "checks": [], "gaps": [{"id": "input",
                                                       "status": "unknown",
                                                       "detail": loaded["error"]}],
                               "source": loaded["path"]})
            continue
        normalized = normalize_candidates(loaded["payload"])
        if not normalized:
            candidates.append({"candidate_id": "", "symbols": [], "verdict": "unknown",
                               "checks": [], "gaps": [{"id": "input",
                                                       "status": "unknown",
                                                       "detail": "输入未包含候选对象（对象/数组为空）"}],
                               "source": loaded["path"]})
            continue
        for ctx in normalized:
            result = validate_candidate(
                ctx, repo_roots=repo_roots, raw_root=raw, artifact_root=artifact,
                engine=engine_tuple if engine_error == "" else None,
                min_oos_trades=int(min_oos_trades))
            result["source"] = loaded["path"]
            candidates.append(result)
    statuses = [c["verdict"] for c in candidates]
    if "fail" in statuses:
        verdict = "fail"
    elif "unknown" in statuses:
        verdict = "unknown"
    else:
        verdict = "pass"
    summary = {"total": len(candidates),
               "pass": statuses.count("pass"),
               "fail": statuses.count("fail"),
               "unknown": statuses.count("unknown")}
    gaps = []
    for candidate in candidates:
        for gap in candidate.get("gaps") or []:
            gaps.append({"source": candidate.get("source", ""),
                         "candidate_id": candidate.get("candidate_id", ""),
                         **gap})
    return {
        "schema": SCHEMA,
        "generated_at": started.isoformat(timespec="seconds"),
        "date": str(date or started.date().isoformat()),
        "repo_root": str(repo),
        "raw_root": str(raw),
        "artifact_root": str(artifact),
        "policy": {
            "min_oos_trades": int(min_oos_trades),
            "pit_allow": list(PIT_OK),
            "required_fields": list(REQUIRED_FIELDS),
            "l4_established_requires_artifact_ref": True,
            "raw_ref_required": True,
        },
        "verdict": verdict,
        "passed": verdict == "pass",
        "summary": summary,
        "candidates": candidates,
        "gaps": gaps,
        "inputs": inputs_meta,
        "engine": {"ok": engine_error == "", "error": engine_error},
        "notes": list(NOTES),
    }


# ---------------------------------------------------------------------------
# 渲染 / 落盘
# ---------------------------------------------------------------------------
def _md_cell(text) -> str:
    return str(text if text is not None else "-").replace("|", "\\|").replace("\n", " ").strip()


def _label(status) -> str:
    return STATUS_LABEL.get(str(status), "UNKNOWN")


def render_markdown(report: dict) -> str:
    summary = report.get("summary") or {}
    policy = report.get("policy") or {}
    lines = [
        "# 候选证据包只读校验（W3 11-08 候选冻结前置）v1",
        "",
        "- 生成时间：%s ｜ 日期：%s" % (report.get("generated_at"), report.get("date")),
        "- 仓库：`%s`" % report.get("repo_root"),
        "- raw_root：`%s`" % report.get("raw_root"),
        "- artifact_root：`%s`" % report.get("artifact_root"),
        "- 阈值：min_oos_trades=%s · PIT allow=%s" % (
            policy.get("min_oos_trades"), "/".join(policy.get("pit_allow") or [])),
        "",
        "**判定：%s**（候选 pass %s / fail %s / unknown %s，共 %s）" % (
            _label(report.get("verdict")), summary.get("pass"), summary.get("fail"),
            summary.get("unknown"), summary.get("total")),
        "",
        "## 候选汇总",
        "",
        "| # | candidate_id | symbols | 判定 | 缺口数 | 来源 |",
        "|---|---|---|---|---:|---|",
    ]
    for index, candidate in enumerate(report.get("candidates") or [], 1):
        lines.append("| %d | %s | %s | %s | %d | %s |" % (
            index, _md_cell(candidate.get("candidate_id") or "-"),
            _md_cell(",".join(candidate.get("symbols") or []) or "-"),
            _label(candidate.get("verdict")), len(candidate.get("gaps") or []),
            _md_cell(Path(str(candidate.get("source") or "-")).name)))
    for index, candidate in enumerate(report.get("candidates") or [], 1):
        lines += ["", "## 候选 %d：%s" % (
            index, candidate.get("candidate_id") or "(无 candidate_id)"), "",
            "| 校验项 | 必需 | 状态 | 说明 |", "|---|---|---|---|"]
        for check in candidate.get("checks") or []:
            lines.append("| %s | %s | %s | %s |" % (
                _md_cell(check.get("title") or check.get("id")),
                "是" if check.get("required", True) else "否",
                _label(check.get("status")), _md_cell(check.get("detail"))))
        gaps = candidate.get("gaps") or []
        if gaps:
            lines += ["", "**缺口：**"]
            for gap in gaps:
                lines.append("- [%s] %s：%s" % (
                    _label(gap.get("status")), _md_cell(gap.get("id")),
                    _md_cell(gap.get("detail"))))
    lines += ["", "## 输入源", "",
              "| # | 路径 | 读取 | 模式 | 错误 |", "|---|---|---|---|---|"]
    for index, item in enumerate(report.get("inputs") or [], 1):
        lines.append("| %d | `%s` | %s | %s | %s |" % (
            index, _md_cell(item.get("path")), "OK" if item.get("ok") else "FAIL",
            _md_cell(item.get("mode")), _md_cell(item.get("error") or "-")))
    lines += ["", "## 强制字段口径（未决口径见下）", ""]
    for field in policy.get("required_fields") or []:
        lines.append("- %s" % field)
    lines += [
        "",
        "## 未决口径（需 ACAD1/D1/ARCH1 拍板）",
        "",
        "1. **OOS 阈值**：默认 %s（W3 条件②原文 ≥10）；ACAD1 复评口径为「≥30 笔独立交易 + 中位>0 + 样本外正期望」，"
        "若采用更严口径请 `--min-oos-trades 30` 并补「中位>0」校验（当前未强制）。" % policy.get("min_oos_trades"),
        "2. **PIT B 是否放行**：当前 B 放行并记缺口（无字节级快照）；若冻结要求 PIT=A（字节快照），需另定。",
        "3. **L4 unresolved 是否放行**：当前放行（候选可为 No-Go；方向仍被 DecisionPolicy 锁）；"
        "若冻结要求 L4=established，请改为强制。",
        "4. **MD/YAML 输入口径**：MD 需 ```json 代码块或 YAML front-matter；纯文本 MD 不支持（标 unknown）。",
        "5. **raw ref 范围**：要求 ≥1 条可复核 raw ref（schema evidence_ref/v2：raw_uri+raw_sha256，"
        "可选 raw_row_range/raw_selector）；仅摘要 ref 不通过。",
        "6. **candidate_id/decision 枚举**：decision 目前只要求非空且可识别（Go/No-Go/Pending…），"
        "若需白名单强制请定列表。",
        "",
        "## 说明",
        "",
    ]
    for note in report.get("notes") or []:
        lines.append("- %s" % note)
    lines.append("")
    return "\n".join(lines)


def output_paths(out_dir, date=None) -> tuple[Path, Path]:
    stamp = str(date).strip() or dt.date.today().strftime(DEFAULT_DATE_FMT)
    directory = Path(out_dir)
    return (directory / ("%s_候选证据包校验_v1.md" % stamp),
            directory / ("%s_候选证据包校验_v1.json" % stamp))


def write_report(report: dict, out_dir) -> dict:
    md_path, json_path = output_paths(out_dir, report.get("date"))
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    md_path.write_text(render_markdown(report) + "\n", encoding="utf-8")
    return {"markdown": str(md_path), "json": str(json_path)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="candidate_evidence_guard.py",
        description="W3 候选证据包只读校验器（11-08 冻结前置）：字段/raw ref/OOS/L4/PIT 校验。",
        epilog="退出码：0=全 pass；1=任一 fail；2=unknown（缺文件/坏格式/用法错误）。")
    parser.add_argument("--input", nargs="+", required=True,
                        help="候选证据包路径（.json/.yaml/.yml/.md，可多个）")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    output.add_argument("--markdown", action="store_true", help="stdout 输出 Markdown（默认）")
    parser.add_argument("--out", default="", help="可选：输出目录（写 <date>_候选证据包校验_v1.md/.json）")
    parser.add_argument("--date", default="", help="报告日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--repo-root", default="", help="主仓根（默认自动解析）")
    parser.add_argument("--raw-root", default="", help="raw ref 解析根（默认 repo-root）")
    parser.add_argument("--artifact-root", default="",
                        help="L4 artifact 根（默认 <repo>/agent_platform/data/academy/artifacts）")
    parser.add_argument("--min-oos-trades", type=int, default=DEFAULT_MIN_OOS_TRADES,
                        help="样本外可执行交易数阈值（默认 %s；ACAD1 严格口径可用 30）"
                             % DEFAULT_MIN_OOS_TRADES)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    ensure_utf8_stdout()
    if args.date:
        try:
            dt.datetime.strptime(args.date, DEFAULT_DATE_FMT)
        except ValueError:
            print("参数错误：--date 需为 YYYY-MM-DD（收到 %r）" % args.date,
                  file=sys.stderr)
            return 2
    try:
        report = validate_report(
            args.input,
            repo_root_path=(args.repo_root or None),
            raw_root=(args.raw_root or None),
            artifact_root=(args.artifact_root or None),
            min_oos_trades=int(args.min_oos_trades),
            date=(args.date or None),
        )
    except Exception as exc:  # noqa: BLE001 - 环境异常也要结构化 unknown
        report = {
            "schema": SCHEMA,
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "date": args.date or dt.date.today().isoformat(),
            "verdict": "unknown", "passed": False, "summary": {},
            "candidates": [], "gaps": [], "inputs": [],
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:300]),
        }
        print("[candidate-evidence-guard] 校验异常（unknown）：%s" % report["error"],
              file=sys.stderr)
    written = {}
    if args.out:
        try:
            written = write_report(report, args.out)
        except OSError as exc:
            print("[warn] 报告落盘失败（不影响判定）：%s" % exc, file=sys.stderr)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(render_markdown(report) if report.get("candidates") or report.get("inputs")
              else "候选证据包校验失败（unknown）：%s" % report.get("error"))
    if written:
        print("[candidate-evidence-guard] 已写入 %s / %s" % (
            written.get("markdown"), written.get("json")), file=sys.stderr)
    verdict = str(report.get("verdict") or "unknown")
    return 0 if verdict == "pass" else (1 if verdict == "fail" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
