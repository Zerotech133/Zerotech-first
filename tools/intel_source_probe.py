# -*- coding: utf-8 -*-
"""IL-08 情报源健康盘点 · 只读探针 helper（供 tools/intel_source_health.py 增量调用）。

只做**事实盘点**：三源（雪球 / 股吧 / TDX）的
  - 在册（in_registry，以现存 registry 为准，不新建第二真源）；
  - 水位（watermark：sqlite 表 max(时间列) / 目录最新非空文件记录 ts）；
  - owner / 许可（registry 登记的 owner / authorization 文本，未登记则为 null 并计缺口）；
  - 探针可得性（probe_available：本机只读能否取到水位，不联网、不宣称在线健康）。

纪律
----
- 只读：sqlite 一律 ``file:<abs>?mode=ro`` + ``PRAGMA query_only=1``；目录只 stat/读最新
  非空文件末行；**不写库、不写盘、不联网、不起停服务/任务**；
- **不读取/不打印任何凭据**（token/cookie/secret 等）；输出仅路径/大小/mtime/表计数/水位；
- 无权威源注册表时标 ``no_authoritative_registry=true``（fail-visible），只做事实盘点，
  **不新建第二真源**、不伪造"在册"；
- 坏输入 fail-visible：路径缺失、sqlite 打不开、目录全 0 字节、时间解析失败 → 写
  ``errors`` 并保留原文，不静默吞。

本模块不 import `tools/intel_source_health.py`（避免循环依赖）；由后者做增量集成。
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

SCHEMA = "intel_source_health_il08/v1"
KIND = "intel_source_health_il08"

HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[1]


def main_worktree_root(repo_root: Path = REPO_ROOT) -> Optional[Path]:
    """worktree 内 ``.git`` 为文件（gitdir: <main>/.git/worktrees/<id>）→ 主工作树路径。

    只读解析 `.git` 指针；失败返回 None（调用方退回候选列表）。不联网/不写盘。
    """
    gitfile = Path(repo_root) / ".git"
    try:
        if not gitfile.is_file():
            return None
        text = gitfile.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    raw = text.split(":", 1)[1].strip()
    try:
        gitdir = Path(raw)
    except (OSError, ValueError):
        return None
    # <main>/.git/worktrees/<id> → parents[2] 即主工作树根
    try:
        return gitdir.parents[2]
    except IndexError:
        return None


def default_data_roots() -> tuple[str, ...]:
    """数据根候选顺序：主工作树（唯一 live 数据根）> AIOS_ROOT > 当前 worktree。

    AGENTS §8：live 数据根唯一 = 主仓 ``quant_backtest/data``；worktree 内同名目录可能
    是历史副本（不可当权威水位），故放最后兜底。
    """
    roots: list[Path] = []
    main = main_worktree_root()
    if main is not None:
        roots.append(main)
    aios = os.environ.get("AIOS_ROOT")
    if aios:
        roots.append(Path(aios))
    roots.append(Path(r"C:\aios"))
    roots.append(Path(r"C:\项目\老田的AI助手"))
    roots.append(REPO_ROOT)
    out: list[str] = []
    for root in roots:
        cand = str(root / "quant_backtest" / "data")
        if cand not in out:
            out.append(cand)
    return tuple(out)


DEFAULT_DATA_ROOTS = default_data_roots()

#: 三源本地数据仓相对路径
XUEQIU_DB_REL = "xueqiu/xueqiu_info.db"
GUBA_DIR_REL = "guba"
TDX_DB_REL = "tdx/tdx_info.db"

#: 每源登记表 source_id 候选（现存 agent_platform/data/intel/sources.json 口径）
REGISTRY_IDS = {
    "xueqiu": ("xueqiu", "xq"),
    "guba": ("guba",),
    "tdx_market": ("tdx_market", "tdx"),
}

#: 水位时间列（来源真实 schema）
XUEQIU_TABLES = (("xq_status", "created_at"), ("xq_status_classified", "labeled_at"))
TDX_TABLES = (("stock_notice", "notice_date"),
              ("stock_news", "pub_date"),
              ("stock_report", "pub_date"))

DEFAULT_STALE_DAYS = 2.0
_TIME_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
_TS_KEYS = ("ts", "created_at", "pub_time", "pub_date", "time", "datetime")

SOURCE_NAMES = {"xueqiu": "雪球", "guba": "股吧", "tdx_market": "TDX（通达信行情/公告仓）"}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def now_naive(now: Any = None) -> datetime:
    """注入时钟（测试/回放）：返回 naive datetime。"""
    if now is None:
        return datetime.now()
    if isinstance(now, datetime):
        return now.astimezone().replace(tzinfo=None) if now.tzinfo else now
    text = str(now).strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("now 不是合法 ISO8601: %r" % (now,)) from exc
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def iso(dt: Optional[datetime]) -> Optional[str]:
    return None if dt is None else dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(value: Any) -> Optional[datetime]:
    """把字符串/epoch 解析为 naive datetime；失败返回 None（fail-visible 由调用方处理）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    raw = text.replace("Z", "+00:00").replace(" ", "T", 1) if "T" not in text else text
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        for fmt in _TIME_FMTS:
            n = min(len(text), len(datetime.now().strftime(fmt)))
            try:
                return datetime.strptime(text[:n], fmt)
            except ValueError:
                continue
        return None
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def age_days(now: datetime, dt: Optional[datetime]) -> Optional[float]:
    if dt is None:
        return None
    secs = (now - dt).total_seconds()
    if secs < 0:  # 未来水位：不伪造负数天数，记 0 并由 notes 标 future
        return 0.0
    return round(secs / 86400.0, 2)


def resolve_data_root(overrides: Optional[dict] = None) -> Path:
    """定位数据根：显式 override > 存在默认候选 > 第一个候选（便于报错显示路径）。"""
    over = overrides or {}
    if over.get("data_root"):
        return Path(str(over["data_root"]))
    for cand in DEFAULT_DATA_ROOTS:
        if Path(cand).is_dir():
            return Path(cand)
    return Path(DEFAULT_DATA_ROOTS[0])


def source_paths(overrides: Optional[dict] = None) -> dict[str, Path]:
    """三源本地路径（可用 --xueqiu-db/--guba-dir/--tdx-db 或 data_root override 注入）。"""
    over = dict(overrides or {})
    root = resolve_data_root(over)
    out = {
        "xueqiu": Path(over.get("xueqiu_db") or (root / XUEQIU_DB_REL)),
        "guba": Path(over.get("guba_dir") or (root / GUBA_DIR_REL)),
        "tdx_market": Path(over.get("tdx_db") or (root / TDX_DB_REL)),
    }
    return out


# ---------------------------------------------------------------------------
# 只读探针：sqlite / 目录
# ---------------------------------------------------------------------------
def _open_ro(db_path: Path) -> sqlite3.Connection:
    uri = "file:%s?mode=ro" % Path(db_path).resolve().as_posix()
    con = sqlite3.connect(uri, uri=True)
    con.execute("PRAGMA query_only=1")
    return con


def probe_sqlite(db_path: Path, tables: tuple, now: datetime,
                 stale_days: float) -> dict[str, Any]:
    """只读探测 sqlite 源：文件 stat + 每表 count/max(时间列) → 水位。"""
    p = Path(db_path)
    out: dict[str, Any] = {
        "kind": "sqlite", "path": str(p), "uri": "file:%s?mode=ro" % p.resolve().as_posix(),
        "exists": p.is_file(),
        "size_bytes": None, "mtime": None, "probe_available": False,
        "tables": [], "rows_total": 0, "latest": None, "latest_source": None,
        "watermark_at": None, "age_days": None, "stale_days": float(stale_days),
        "stale": None, "notes": [], "error": None,
    }
    if not p.is_file():
        out["error"] = "path_missing"
        out["notes"].append("数据仓路径不存在（fail-visible）")
        return out
    st = p.stat()
    out["size_bytes"] = st.st_size
    out["mtime"] = iso(datetime.fromtimestamp(st.st_mtime))
    if st.st_size == 0:
        out["error"] = "file_zero_bytes"
        out["notes"].append("数据仓文件为 0 字节（fail-visible）")
        return out
    try:
        con = _open_ro(p)
        con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()  # 触发坏文件校验
    except sqlite3.Error as exc:
        out["error"] = "sqlite_open_failed: %s" % exc
        out["notes"].append("sqlite 只读打开失败（fail-visible）; error=sqlite_open_failed")
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass
        return out
    table_errors = 0
    latest_dt: Optional[datetime] = None
    latest_table: Optional[str] = None
    try:
        for table, col in tables:
            entry: dict[str, Any] = {"table": table, "time_column": col,
                                     "rows": None, "latest": None, "error": None}
            try:
                rows = int(con.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0])
                entry["rows"] = rows
                out["rows_total"] += rows
                latest_raw = con.execute("SELECT MAX(%s) FROM %s" % (col, table)).fetchone()[0]
                entry["latest"] = None if latest_raw is None else str(latest_raw)
                if latest_raw is not None:
                    dt = parse_dt(latest_raw)
                    if dt is not None and (latest_dt is None or dt > latest_dt):
                        latest_dt = dt
                        latest_table = table
            except sqlite3.Error as exc:
                entry["error"] = str(exc)
                table_errors += 1
            out["tables"].append(entry)
    finally:
        con.close()
    out["latest_source"] = latest_table
    out["latest"] = iso(latest_dt)
    if latest_table is None and latest_dt is None:
        if table_errors:
            out["error"] = "sqlite_query_failed"
            out["notes"].append("sqlite 查询失败（fail-visible）; error=sqlite_query_failed")
        else:
            out["notes"].append("表存在但无时间列数据（空水位）")
    elif latest_dt is not None:
        if latest_dt > now:
            out["notes"].append("watermark_in_future（水位晚于当前时钟，按 0 天保守处理）")
        out["watermark_at"] = iso(latest_dt)
        out["mtime"] = out["mtime"] or iso(datetime.fromtimestamp(st.st_mtime))
        out["age_days"] = age_days(now, latest_dt)
        out["stale"] = bool(out["age_days"] is not None
                            and out["age_days"] > float(stale_days))
    out["probe_available"] = table_errors == 0 or bool(out["tables"])
    if out["error"]:
        out["probe_available"] = False
    return out


def _last_json_record(path: Path) -> Optional[dict]:
    """读取 JSONL 最新一行（从末尾找最近一条可解析 JSON 对象；只读）。"""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def probe_dir(dir_path: Path, now: datetime, stale_days: float,
              pattern: str = "*.jsonl") -> dict[str, Any]:
    """只读探测目录源（股吧）：文件计数/0 字节/最新非空文件记录 ts 水位。"""
    p = Path(dir_path)
    out: dict[str, Any] = {
        "kind": "directory", "path": str(p), "uri": None, "exists": p.is_dir(),
        "files": 0, "files_nonempty": 0, "files_zero_bytes": 0,
        "size_bytes": None, "newest_file": None, "newest_mtime": None,
        "probe_available": False, "latest": None, "watermark_at": None,
        "age_days": None, "stale_days": float(stale_days), "stale": None,
        "notes": [], "error": None,
    }
    if not p.is_dir():
        out["error"] = "path_missing"
        out["notes"].append("数据仓目录不存在（fail-visible）")
        return out
    files = sorted(p.glob(pattern))
    out["files"] = len(files)
    nonempty: list[Path] = []
    total = 0
    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        out["size_bytes"] = (out["size_bytes"] or 0) + st.st_size
        total += st.st_size
        if st.st_size > 0:
            nonempty.append(f)
        else:
            out["files_zero_bytes"] += 1
    out["files_nonempty"] = len(nonempty)
    out["size_bytes"] = total
    if not files:
        out["error"] = "dir_empty"
        out["notes"].append("数据仓目录无匹配文件（fail-visible）; error=dir_empty")
        return out
    if not nonempty:
        out["error"] = "all_files_zero_bytes"
        out["notes"].append("数据仓文件全为 0 字节（fail-visible）; error=all_files_zero_bytes")
        return out
    newest = max(nonempty, key=lambda f: f.stat().st_mtime)
    try:
        st = newest.stat()
        out["newest_file"] = newest.name
        out["newest_mtime"] = iso(datetime.fromtimestamp(st.st_mtime))
    except OSError:
        pass
    rec = _last_json_record(newest)
    rec_dt = None
    rec_ts = None
    if rec:
        for key in _TS_KEYS:
            if rec.get(key) not in (None, ""):
                rec_dt = parse_dt(rec.get(key))
                if rec_dt is not None:
                    rec_ts = str(rec.get(key))
                    break
    if rec_dt is not None:
        if rec_dt > now:
            out["notes"].append("watermark_in_future（记录 ts 晚于当前时钟，按 0 天保守处理）")
        out["watermark_at"] = iso(rec_dt)
        out["latest"] = rec_ts or iso(rec_dt)
        out["age_days"] = age_days(now, rec_dt)
        out["stale"] = bool(out["age_days"] is not None
                            and out["age_days"] > float(stale_days))
    else:
        # 记录字段不可解析时退回文件 mtime（显式标注来源，不冒称记录水位）
        mtime = datetime.fromtimestamp(newest.stat().st_mtime)
        out["watermark_at"] = iso(mtime)
        out["latest"] = out["newest_mtime"]
        out["age_days"] = age_days(now, mtime)
        out["stale"] = bool(out["age_days"] is not None
                            and out["age_days"] > float(stale_days))
        out["notes"].append("record_ts_unparsed：水位退回文件 mtime")
    out["probe_available"] = True
    return out


# ---------------------------------------------------------------------------
# 注册表事实（不新建第二真源）
# ---------------------------------------------------------------------------
def _registry_entries(registry: Optional[dict]) -> dict[str, dict]:
    """把 registry sources 归一为 {source_id: entry}（仅读，不改原数据）。"""
    out: dict[str, dict] = {}
    if not isinstance(registry, dict):
        return out
    for src in registry.get("sources") or []:
        if isinstance(src, dict) and src.get("source_id"):
            out[str(src["source_id"])] = src
    return out


def find_registry_entry(entries: dict[str, dict], source_id: str) -> Optional[dict]:
    for candidate in REGISTRY_IDS.get(source_id, (source_id,)):
        if candidate in entries:
            return entries[candidate]
    return None


def _license_of(entry: Optional[dict]) -> Optional[str]:
    if not entry:
        return None
    text = entry.get("authorization") or entry.get("license") or ""
    text = str(text).strip()
    return text[:240] + ("…" if len(text) > 240 else "") if text else None


def build_il08_inventory(*, registry: Optional[dict], registry_path: Any,
                         registry_ok: bool = True, registry_error: Any = None,
                         now: Any = None, stale_days: float = DEFAULT_STALE_DAYS,
                         overrides: Optional[dict] = None) -> dict[str, Any]:
    """构建 IL-08 三源事实盘点 JSON（只读；不新建注册表、不宣称在线健康）。"""
    clk = now_naive(now)
    over = dict(overrides or {})
    paths = source_paths(over)
    entries = _registry_entries(registry)
    errors: list[dict] = []
    gaps: list[dict] = []

    registry_block = {
        "path": str(registry_path),
        "ok": bool(registry_ok),
        "error": registry_error,
        "registry_id": (registry or {}).get("registry_id") if isinstance(registry, dict) else None,
        "schema_version": (registry or {}).get("schema_version") if isinstance(registry, dict) else None,
        "updated_at": (registry or {}).get("updated_at") if isinstance(registry, dict) else None,
        "phase": (registry or {}).get("phase") if isinstance(registry, dict) else None,
        "source_count": len(entries),
        "authoritative": False,
        "covers_three_sources": False,
        "reason": ("无权威三源源注册表：现存 registry 为 Phase 2『登记+只读采样』表，"
                   "未覆盖雪球，且自述登记≠接入/采样≠发布；本工具只做事实盘点，"
                   "不新建第二真源。"),
    }

    sources: list[dict] = []
    for source_id in ("xueqiu", "guba", "tdx_market"):
        entry = find_registry_entry(entries, source_id)
        in_registry = entry is not None
        row: dict[str, Any] = {
            "source_id": source_id,
            "name": SOURCE_NAMES[source_id],
            "in_registry": in_registry,
            "registry_source_id": str(entry["source_id"]) if entry else None,
            "owner": (str(entry.get("owner")) if entry and entry.get("owner") else None),
            "license": _license_of(entry),
            "mode": (str(entry.get("mode")) if entry and entry.get("mode") else None),
            "enabled": (entry.get("enabled") if entry else None),
            "registry_note": (str(entry.get("note"))[:200] if entry and entry.get("note") else None),
            "stale_days": float(stale_days),
            "probe_available": False,
            "watermark": None,
            "watermark_at": None,
            "age_days": None,
            "stale": None,
            "notes": "",
            "gaps": [],
        }
        if source_id == "guba":
            probe = probe_dir(paths["guba"], clk, stale_days)
        else:
            tables = XUEQIU_TABLES if source_id == "xueqiu" else TDX_TABLES
            probe = probe_sqlite(paths[source_id], tables, clk, stale_days)
        row["probe_available"] = bool(probe["probe_available"])
        row["watermark"] = {
            "kind": probe["kind"], "path": probe["path"], "uri": probe.get("uri"),
            "exists": probe["exists"], "size_bytes": probe.get("size_bytes"),
            "mtime": probe.get("mtime"), "tables": probe.get("tables"),
            "files": probe.get("files"), "files_nonempty": probe.get("files_nonempty"),
            "files_zero_bytes": probe.get("files_zero_bytes"),
            "newest_file": probe.get("newest_file"),
            "rows_total": probe.get("rows_total"),
            "latest": probe.get("latest"), "latest_source": probe.get("latest_source"),
            "error": probe.get("error"),
        }
        row["watermark_at"] = probe.get("watermark_at")
        row["age_days"] = probe.get("age_days")
        row["stale"] = probe.get("stale")
        notes = list(probe.get("notes") or [])
        if entry and entry.get("note"):
            notes.append("registry_note=%s" % str(entry.get("note"))[:120])
        row["notes"] = "; ".join(notes) if notes else ""
        if probe.get("error"):
            errors.append({"source_id": source_id, "error": probe["error"],
                           "path": probe["path"]})
        if not in_registry:
            row["gaps"].append("not_in_registry")
            gaps.append({"source_id": source_id, "gap": "not_in_registry",
                         "detail": "现存源注册表未登记该源（事实缺口，不新建第二真源、不伪造在册）"})
        if entry is None:
            row["gaps"].append("owner_unknown")
            row["gaps"].append("license_unknown")
            gaps.append({"source_id": source_id, "gap": "owner_unknown",
                         "detail": "无 registry 登记 → owner 未知"})
            gaps.append({"source_id": source_id, "gap": "license_unknown",
                         "detail": "无 registry 授权/许可文本 → 许可未知"})
        if probe.get("error"):
            row["gaps"].append("probe_error")
            gaps.append({"source_id": source_id, "gap": "probe_error",
                         "detail": "只读探针失败: %s" % probe["error"]})
        sources.append(row)

    in_registry_n = sum(1 for s in sources if s["in_registry"])
    probe_ok_n = sum(1 for s in sources if s["probe_available"])
    stale_n = sum(1 for s in sources if s["stale"])
    inventory = {
        "schema": SCHEMA,
        "kind": KIND,
        "readonly": True,
        "network": False,
        "online_health_claimed": False,
        "generated_at": iso(clk),
        "now": iso(clk),
        "no_authoritative_registry": True,
        "registry": registry_block,
        "thresholds": {"stale_days": float(stale_days)},
        "sources": sources,
        "summary": {
            "total": len(sources),
            "in_registry": in_registry_n,
            "not_in_registry": [s["source_id"] for s in sources if not s["in_registry"]],
            "probe_available": probe_ok_n,
            "probe_unavailable": [s["source_id"] for s in sources
                                  if not s["probe_available"]],
            "stale": stale_n,
            "errors": len(errors),
            "gaps": len(gaps),
        },
        "gaps": gaps,
        "errors": errors,
        "policy": {
            "readonly": True,
            "network": False,
            "writes_db": False,
            "writes_files": False,
            "changes_collect_config": False,
            "starts_stops_services": False,
            "no_second_registry_source": True,
            "credentials_read": False,
        },
        "ok": bool(probe_ok_n == len(sources) and not errors),
    }
    return inventory


# ---------------------------------------------------------------------------
# Markdown 渲染（stdout 用，不落盘）
# ---------------------------------------------------------------------------
def format_il08_md(inv: dict[str, Any]) -> str:
    if not isinstance(inv, dict) or inv.get("schema") != SCHEMA:
        return "# IL-08 情报源健康盘点\n\n- error: inventory unavailable (%r)\n" % (
            (inv or {}).get("error") if isinstance(inv, dict) else inv,)
    lines = [
        "# IL-08 情报源健康盘点（三源，只读事实）",
        "",
        "- schema: `%s`" % inv["schema"],
        "- generated_at: %s  now: %s" % (inv["generated_at"], inv["now"]),
        "- `no_authoritative_registry=true`（现存 registry 非权威三源注册表；只做事实盘点，不新建第二真源）",
        "- 口径：本地只读探针（sqlite `mode=ro`/query_only、目录 stat）；**不联网、不宣称在线健康、"
        "不读凭据、不写库/盘、不改采集配置、不启停服务/任务**",
        "- stale 阈值：%s days" % inv["thresholds"]["stale_days"],
        "",
        "| 源 | in_registry | 水位 | age_days | stale | owner | 许可 | probe_available | notes |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in inv["sources"]:
        wm = s.get("watermark") or {}
        latest = s.get("watermark_at") or "-"
        if wm.get("kind") == "directory":
            latest = "%s（%s/%s 非空/0字节，newest=%s）" % (
                latest, wm.get("files_nonempty"), wm.get("files_zero_bytes"),
                wm.get("newest_file"))
        else:
            latest = "%s（tables=%s, rows=%s）" % (
                latest, ",".join(t["table"] for t in (wm.get("tables") or [])),
                wm.get("rows_total"))
        owner = s.get("owner") or "-"
        lic = (s.get("license") or "-").replace("|", "/")
        if len(lic) > 60:
            lic = lic[:60] + "…"
        notes = (s.get("notes") or "").replace("|", "/")
        if len(notes) > 70:
            notes = notes[:70] + "…"
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            s["name"], "true" if s["in_registry"] else "false", latest,
            s.get("age_days"), s.get("stale"), owner, lic,
            "true" if s["probe_available"] else "false", notes))
    lines += ["", "## 注册表事实", "",
              "- path: `%s`" % inv["registry"]["path"],
              "- registry_id/phase: %s / %s" % (
                  inv["registry"]["registry_id"], inv["registry"]["phase"]),
              "- updated_at/source_count: %s / %s" % (
                  inv["registry"]["updated_at"], inv["registry"]["source_count"]),
              "- authoritative/covers_three_sources: false / false",
              "- reason: %s" % inv["registry"]["reason"],
              "", "## 缺口（fail-visible）", ""]
    if inv.get("gaps"):
        for g in inv["gaps"]:
            lines.append("- [%s] %s: %s" % (g["source_id"], g["gap"], g["detail"]))
    else:
        lines.append("- 无")
    lines += ["", "## 错误（fail-visible）", ""]
    if inv.get("errors"):
        for e in inv["errors"]:
            lines.append("- [%s] %s (%s)" % (e["source_id"], e["error"], e["path"]))
    else:
        lines.append("- 无")
    sm = inv["summary"]
    lines += ["", "## 汇总", "",
              "- total=%s in_registry=%s not_in_registry=%s probe_available=%s stale=%s "
              "errors=%s gaps=%s ok=%s" % (
                  sm["total"], sm["in_registry"], ",".join(sm["not_in_registry"]) or "-",
                  sm["probe_available"], sm["stale"], sm["errors"], sm["gaps"],
                  "true" if inv.get("ok") else "false"),
              "", "## 未做 / 边界", "",
              "- 本工具只盘点事实；采集适配器改动、断流告警（<15min）、portal 接入、"
              "INTEL0 ACK 台账属 IL-08 后续，需另单/审批。",
              "- 未读取任何凭据文件；未写注册表；未启停任何服务/计划任务。",
              ""]
    return "\n".join(lines)
