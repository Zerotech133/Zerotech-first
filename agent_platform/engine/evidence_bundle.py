# -*- coding: utf-8 -*-
"""EvidenceBundle（2026-10-30 WO-D1-EVIDENCE，执行"无证据不调 D1"）。

从 make_context 的上下文段构造可核验证据引用：source/uri/as_of/hash/freshness。
不伪造来源；段为空就不进 bundle。

2026-10-31 WO-EVIDRAW（K-ORG-20261031-EVIDRAW）追加 **raw ref 扩展（schema v2）**：
- 旧版 ref 只有 `uri=make_context://<symbol>/<segment>` + `hash=sha256(摘要文本)[:16]`
  → 只能证明"同一段文本"，不能回查原始数据（W3 预检 B4 缺口）。
- v2 raw ref 指向**原始文件/记录**：`schema=evidence_ref/v2` + `kind`
  （kline_parquet/news_record/event_record/l4_record）+ `raw_uri`（相对仓库根的
  路径）+ `raw_sha256`（整文件 sha256）+ `raw_row_range`（parquet 行区间
  [start, end)，0-based 左闭右开）+ `raw_selector`（JSON/JSONL 记录选择器）。
- **兼容**：旧 ref dict 可直接 `EvidenceRef.from_dict()` 解析，旧字段
  source/uri/as_of/hash/freshness_s 原样保留；新字段默认值不改变旧语义。
- **不放松 `is_sufficient`**：raw ref 只能**按 source 原位升级**已有 ref；找不到
  同 source 时不追加（除 `l4_record` 进独立 `raw_refs_extra`，不参与充分性），
  因此 raw 挂载既不会放宽也不会收紧充分性判定。
- **不假成功**：`make_raw_ref` 缺文件/不可读/行区间越界/selector 不存在 → 抛
  `RawRefError`；`verify_raw_ref` 复核失败返回 `(False, reason)`；
  `attach_raw_refs` 把失败的 raw ref 拒绝并记入 `raw_refs_report.rejected`，
  不写入 refs、不标 verified。

边界：本模块只做**只读**核验（读文件/数行/算 sha），不写任何数据、不联网。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from l4_artifact import validate_artifact_ref

SEGMENTS = ("market_technical", "fundamental", "news_event", "final_decision")
REQUIRED = ("market_technical", "fundamental")

# ---- raw ref schema（v2） -------------------------------------------------
SUMMARY_SCHEMA = "evidence_ref/v1"
RAW_SCHEMA = "evidence_ref/v2"
SUMMARY_KIND = "summary"
RAW_KINDS = ("kline_parquet", "news_record", "event_record", "l4_record")
RAW_PREFIX = "raw://"
L4_KIND = "l4_record"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LINE_SELECTOR_RE = re.compile(r"^line:(\d+)$")

ENGINE_DIR = Path(__file__).resolve().parent
PLATFORM_ROOT = ENGINE_DIR.parent
REPO_ROOT = PLATFORM_ROOT.parent


class RawRefError(ValueError):
    """raw ref 构造/核验失败（缺文件、行越界、selector 不存在等）。

    调用方必须按"未核验"处理（不得当 raw 使用），不得吞掉冒充成功。
    """


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_path(uri, root=None):
    """把 raw_uri 解析为 root 内文件；返回 (Path|None, reason)。"""
    text = str(uri or "").strip()
    if not text:
        return None, "missing_uri"
    if text.lower().startswith(("http://", "https://", "file://")):
        return None, "url_not_local"
    if text.startswith(RAW_PREFIX):
        text = text[len(RAW_PREFIX):]
        # raw://<kind>/<relative path>：去掉第一段 kind
        parts = text.split("/", 1)
        text = parts[1] if len(parts) == 2 else parts[0]
    path = Path(text)
    if path.is_absolute():
        return None, "absolute_path_not_allowed"
    if ".." in path.parts:
        return None, "path_traversal"
    base = Path(root).resolve() if root else REPO_ROOT.resolve()
    candidate = (base / path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None, "outside_root"
    return candidate, ""


def _parquet_rows(path: Path):
    """parquet 行数（pyarrow 惰性导入；不可用返回 None）。"""
    try:
        import pyarrow.parquet as pq  # noqa: PLC0415  惰性：无 pyarrow 时降级
    except Exception:  # noqa: BLE001
        return None
    try:
        return int(pq.ParquetFile(path).metadata.num_rows)
    except Exception:  # noqa: BLE001
        return None


def _selector_exists(path: Path, selector: str):
    """校验记录选择器存在：``line:<n>``（JSONL）或 JSON 点路径 a.b.0.c。

    返回 (True, "") / (False, reason)。
    """
    selector = str(selector or "").strip()
    if not selector:
        return True, ""
    try:
        raw = path.read_bytes()
    except OSError:
        return False, "read_error"
    line_match = _LINE_SELECTOR_RE.match(selector)
    if line_match:
        index = int(line_match.group(1))
        lines = [ln for ln in raw.decode("utf-8", "replace").splitlines()
                 if ln.strip()]
        if index < 0 or index >= len(lines):
            return False, "selector_not_found"
        return True, ""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False, "selector_not_found"
    cur = doc
    for part in selector.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return False, "selector_not_found"
    return True, ""


def make_raw_ref(*, source, path, kind, as_of=None, root=None,
                 row_range=None, selector="") -> "EvidenceRef":
    """构造**已核验**的 raw ref（文件不存在/越界/selector 缺失 → RawRefError）。

    - `path` 可为绝对或相对；存入 ref 的 `raw_uri` 一律是相对 root 的 POSIX
      路径（root 默认仓库根），便于跨机/跨 worktree 复核；
    - `row_range` 仅对 parquet 有意义，格式 `[start, end)`；必须落在真实行数内；
    - `selector` 仅对 JSON/JSONL 文件有意义，必须能在文件中解析到。
    """
    kind = str(kind or "").strip()
    if kind not in RAW_KINDS:
        raise RawRefError("kind_not_allowed:%s" % kind)
    if not str(source or "").strip():
        raise RawRefError("source_required")
    base = Path(root).resolve() if root else REPO_ROOT.resolve()
    raw = Path(str(path or ""))
    candidate = raw if raw.is_absolute() else base / raw
    try:
        candidate = candidate.resolve()
    except OSError as exc:  # noqa: BLE001
        raise RawRefError("path_unresolvable:%s" % exc) from exc
    if not candidate.is_file():
        raise RawRefError("not_found:%s" % candidate)
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise RawRefError("outside_root:%s" % candidate) from exc
    relative = candidate.relative_to(base).as_posix()
    sha = _sha256_file(candidate)
    size = candidate.stat().st_size
    rows = None
    if row_range is not None:
        if not isinstance(row_range, (list, tuple)) or len(row_range) != 2:
            raise RawRefError("bad_row_range")
        try:
            start, end = int(row_range[0]), int(row_range[1])
        except (TypeError, ValueError) as exc:
            raise RawRefError("bad_row_range") from exc
        if start < 0 or end <= start:
            raise RawRefError("bad_row_range")
        if kind == "kline_parquet":
            rows = _parquet_rows(candidate)
            if rows is None:
                raise RawRefError("row_count_unavailable")
            if end > rows:
                raise RawRefError("row_range_out_of_bounds:%d>%d"
                                  % (end, rows))
            row_range = [start, end]
    if selector:
        ok, reason = _selector_exists(candidate, selector)
        if not ok:
            raise RawRefError(reason)
    now = as_of or time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    return EvidenceRef(
        source=str(source),
        uri="%s%s/%s" % (RAW_PREFIX, kind, relative),
        as_of=now,
        hash=sha[:16],
        freshness_s=0,
        schema=RAW_SCHEMA,
        kind=kind,
        raw_uri=relative,
        raw_sha256=sha,
        raw_row_range=list(row_range) if row_range else [],
        raw_selector=str(selector or ""),
        raw_bytes=size,
        raw_verified=True,
        raw_verify_error="",
    )


def verify_raw_ref(ref, root=None):
    """复核 raw ref 指针对应的原始文件/记录；返回 ``(ok, reason)``。

    摘要 ref（旧 schema）一律 ``(False, "not_raw")``——**摘要 hash 不得冒充 raw**。
    """
    try:
        item = ref if isinstance(ref, EvidenceRef) else EvidenceRef.from_dict(ref)
    except (TypeError, ValueError):
        return False, "invalid_ref"
    if not item.is_raw:
        return False, "not_raw"
    candidate, reason = _resolve_path(item.raw_uri, root)
    if candidate is None:
        return False, reason
    if not candidate.is_file():
        return False, "not_found"
    try:
        actual = _sha256_file(candidate)
    except OSError:
        return False, "read_error"
    if actual != item.raw_sha256:
        return False, "sha256_mismatch"
    if item.raw_row_range:
        rows = _parquet_rows(candidate)
        if rows is None:
            return False, "row_count_unavailable"
        end = int(item.raw_row_range[-1])
        if end > rows:
            return False, "row_range_out_of_bounds"
    if item.raw_selector:
        ok, sel_reason = _selector_exists(candidate, item.raw_selector)
        if not ok:
            return False, sel_reason
    return True, ""


@dataclass
class EvidenceRef:
    source: str
    uri: str
    as_of: str
    hash: str
    freshness_s: int = 0
    # ---- raw ref v2（旧 ref 缺省即摘要 ref，不变语义） ----
    schema: str = SUMMARY_SCHEMA
    kind: str = SUMMARY_KIND
    raw_uri: str = ""
    raw_sha256: str = ""
    raw_row_range: list = field(default_factory=list)
    raw_selector: str = ""
    raw_bytes: int = 0
    raw_verified: bool = False
    raw_verify_error: str = ""

    @property
    def is_raw(self) -> bool:
        """结构意义上的 raw 声明（不代表已核验，核验看 raw_verified）。"""
        return (self.schema == RAW_SCHEMA
                and self.kind in RAW_KINDS
                and bool(str(self.raw_uri or "").strip())
                and bool(_SHA256_RE.match(str(self.raw_sha256 or ""))))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        """兼容旧 ref dict（缺新键 → 默认摘要 ref）与 v2 raw dict。"""
        if not isinstance(data, dict):
            raise RawRefError("invalid_ref")
        known = {f.name for f in fields(cls)}
        payload = {k: v for k, v in data.items() if k in known}
        for key in ("source", "uri", "as_of", "hash"):
            payload.setdefault(key, "")
        payload["source"] = str(payload.get("source") or "")
        payload["uri"] = str(payload.get("uri") or "")
        payload["as_of"] = str(payload.get("as_of") or "")
        payload["hash"] = str(payload.get("hash") or "")
        row_range = payload.get("raw_row_range")
        if isinstance(row_range, tuple):
            payload["raw_row_range"] = list(row_range)
        return cls(**payload)


@dataclass
class EvidenceBundle:
    symbol: str
    name: str
    refs: list = field(default_factory=list)
    l4_status: str = "missing"  # established|unresolved|missing|unknown
    l4_source: str = ""
    l4_reason: str = ""         # 机读原因：no_research_artifact 等（B1，WO-D1-B123）
    input_degraded: str = ""
    # ---- raw ref 挂载结果（不参与 is_sufficient） ----
    raw_refs_extra: list = field(default_factory=list)
    raw_refs_status: str = "none"   # none|partial|full|invalid
    raw_refs_report: dict = field(default_factory=dict)
    raw_refs_error: str = ""

    @classmethod
    def from_context(cls, symbol, name, ctx, now=None, l4=None, artifact_root=None):
        now = now or time.time()
        as_of = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now))
        refs = []
        texts = {}
        for seg in SEGMENTS:
            text = str((ctx or {}).get(seg) or "").strip()
            if not text:
                continue
            texts[seg] = text
            refs.append(EvidenceRef(
                source=seg,
                uri="make_context://%s/%s" % (symbol, seg),
                as_of=as_of,
                hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                freshness_s=0,
            ))
        l4_status, l4_source = _detect_l4(texts)
        l4_reason = ""
        if isinstance(l4, dict):
            status = str(l4.get("status") or "").strip().lower()
            l4_status = status if status in ("established", "unresolved", "missing", "unknown") else "unknown"
            l4_source = str(l4.get("source") or "") + (
                ":" + str(l4.get("as_of")) if l4.get("as_of") else "")
            l4_reason = str(l4.get("reason") or "").strip()
            # 2026-10-31 WO-L4-M4-HARDEN-20261031（7d P0）：消费侧二次防线——
            # 调用方直接塞 dict 绕过 l4_provider 时，established 仍须带可核验
            # artifact_ref，否则降级 unresolved（不静默信任上游 dict）。
            # 2026-10-31 WO-D1-B123-20261031（B1）：降级落机读原因
            # no_research_artifact，并把标记写进 l4_source 供账本/日报追溯。
            if l4_status == "established":
                ok, reason, _ = validate_artifact_ref(l4.get("artifact_ref"),
                                                      root=artifact_root)
                if not ok:
                    l4_status = "unresolved"
                    l4_reason = "no_research_artifact"
                    l4_source = "%s;no_research_artifact:%s" % (l4_source, reason)
        bundle = cls(symbol=str(symbol), name=str(name or ""), refs=refs,
                     l4_status=l4_status, l4_source=l4_source,
                     l4_reason=l4_reason)
        if not bundle.is_sufficient():
            bundle.input_degraded = "missing:" + ",".join(
                s for s in REQUIRED if s not in texts)
        return bundle

    # ---------------------------------------------------------- raw ref 挂载
    def attach_raw_refs(self, raw_refs, root=None, verify=True) -> dict:
        """挂载 raw refs：**只按 source 原位升级**已有 ref，失败即拒绝。

        规则（保证不放松 is_sufficient）：
        1. 非 raw（摘要 ref）→ rejected:not_raw，绝不进 refs；
        2. 核验失败（缺文件/哈希不符/行越界/selector 缺失）→ rejected + reason，
           不写 refs、不标 verified；
        3. 有同 source 的已有 ref → **原位替换**（refs 数量与 source 集合不变）；
        4. 无同 source 且 kind != l4_record → rejected:source_not_in_bundle
           （防止用新增 ref 补出缺失段、变相放松充分性）；
        5. l4_record 放入 raw_refs_extra（不参与充分性）。
        返回 report dict（同时写入 self.raw_refs_report / raw_refs_status）。
        """
        if isinstance(raw_refs, dict):
            raw_refs = [raw_refs]
        report = {"upgraded": [], "extra": [], "rejected": []}
        for item in raw_refs or []:
            try:
                ref = item if isinstance(item, EvidenceRef) else EvidenceRef.from_dict(item)
            except (RawRefError, TypeError, ValueError):
                report["rejected"].append({"uri": "", "reason": "invalid_ref"})
                continue
            if not ref.is_raw:
                report["rejected"].append({"uri": ref.uri,
                                           "source": ref.source,
                                           "reason": "not_raw"})
                continue
            if verify:
                ok, reason = verify_raw_ref(ref, root=root)
                if not ok:
                    report["rejected"].append({"uri": ref.raw_uri,
                                               "source": ref.source,
                                               "reason": reason})
                    continue
            ref.raw_verified = True
            ref.raw_verify_error = ""
            index = next((i for i, cur in enumerate(self.refs)
                          if cur.source == ref.source), None)
            if index is not None:
                self.refs[index] = ref
                report["upgraded"].append(ref.source)
            elif ref.kind == L4_KIND:
                self.raw_refs_extra.append(ref)
                report["extra"].append(ref.source)
            else:
                report["rejected"].append({"uri": ref.raw_uri,
                                           "source": ref.source,
                                           "reason": "source_not_in_bundle"})
        raw_inside = sum(1 for ref in self.refs if ref.is_raw)
        if report["rejected"] and not report["upgraded"]:
            self.raw_refs_status = "invalid"
        elif raw_inside == len(self.refs) and raw_inside:
            self.raw_refs_status = "full"
        elif raw_inside or self.raw_refs_extra:
            self.raw_refs_status = "partial"
        else:
            self.raw_refs_status = "none"
        self.raw_refs_report = report
        return report

    def raw_summary(self) -> dict:
        """raw ref 落账摘要（供 ledger row / 预检脚本核验）。"""
        inside = sum(1 for ref in self.refs if ref.is_raw)
        return {
            "schema": RAW_SCHEMA,
            "status": self.raw_refs_status,
            "refs": len(self.refs),
            "raw_refs_in_bundle": inside,
            "extra_raw_refs": len(self.raw_refs_extra),
            "upgraded": list((self.raw_refs_report or {}).get("upgraded") or []),
            "rejected": list((self.raw_refs_report or {}).get("rejected") or []),
            "error": self.raw_refs_error,
        }

    def is_sufficient(self):
        have = {r.source for r in self.refs}
        return len(self.refs) >= 3 and all(s in have for s in REQUIRED)

    def to_dict(self):
        d = asdict(self)
        d["refs"] = [asdict(r) for r in self.refs]
        d["raw_refs_extra"] = [asdict(r) for r in self.raw_refs_extra]
        return d

    @classmethod
    def from_dict(cls, data):
        """旧 bundle dict 兼容（缺 raw 字段 → 摘要 ref / raw 状态 none）。"""
        if not isinstance(data, dict):
            raise RawRefError("invalid_bundle")
        payload = {k: v for k, v in data.items()
                   if k in {f.name for f in fields(cls)}}
        payload["refs"] = [EvidenceRef.from_dict(r) for r in
                           (data.get("refs") or [])]
        payload["raw_refs_extra"] = [EvidenceRef.from_dict(r) for r in
                                     (data.get("raw_refs_extra") or [])]
        payload.setdefault("symbol", "")
        payload.setdefault("name", "")
        payload.setdefault("l4_status", "missing")
        payload.setdefault("l4_source", "")
        payload.setdefault("input_degraded", "")
        return cls(**payload)


def _detect_l4(texts):
    """从上下文里识别 L4 状态；不猜：找不到就 missing。"""
    blob = "\n".join(texts.values())
    if "L4" not in blob:
        return "missing", ""
    for seg, text in texts.items():
        for line in text.splitlines():
            if "L4" not in line:
                continue
            if any(k in line for k in ("存疑", "观察", "散手")):
                return "unresolved", "%s:%s" % (seg, line.strip()[:80])
            if any(k in line for k in ("成立", "定性成立", "确定")):
                return "established", "%s:%s" % (seg, line.strip()[:80])
    return "unknown", ""
