# -*- coding: utf-8 -*-
"""情报实体链补齐 · news.db 实体抽取前置工具 v0（只读 + staging，不写库）。

背景
----
情报板块 news.db 的 ``news.stocks`` 列覆盖率长期偏低（约 9.4%），
``/api/intel/evidence-targets`` 只能依赖少量已标注新闻，实体链（证据—标的）
上游供给不足。本工具做「回填前置」：只读扫 news 表，从 title/content 抽
A 股代码 + 名称→代码候选，产出可复跑的统计与 staging JSONL，供后续回填/
证据链消费。**本工具绝不写 news.db**。

口径（与 intel_collector/core/canonical.py 对齐）
------------------------------------------------
* A 股归一：60/68/90 → SH；00/30 → SZ；8/4 开头 → BJ；其余返回 None
  （例：SH600519 / 600519.SH / 600519 都归一为 SH600519）。
* exact（高置信）：显式代码形态——``SH600519`` / ``600519.SH`` / ``600519SH``
  / 任务规定的 ``\\b(?:60|68|90|00|30|8|4)\\d{4}\\b``。
* weak（补充、不计入 exact）：中文紧贴代码（如「证券代码002475」）——
  任务规定的 ``\\b`` 在 Python 里把汉字算 word 字符，会漏这类；本工具单独
  记为 weak 层，可用 ``--no-cjk-adjacent`` 关闭。weak 有数量词误报风险
  （如「907065手」），故不升级为 exact。
* candidate（候选）：由本地名称表命中股票名得到代码，**只作候选、绝不升级为
  exact**；一行命中多个不同代码记为 ambiguous。
* before：``stocks`` 列 JSON 解析后为非空列表（``[]``/NULL/空串算未覆盖）；
  坏 JSON / 非列表等记 bad_rows（fail-visible，不中断）。
* after（主口径）：``before ∪ exact``；另报包含 weak 的敏感口径。

只读保证
--------
sqlite 以 ``file:<path>?mode=ro`` URI 打开，并执行 ``PRAGMA query_only=1``；
本模块不含任何 INSERT/UPDATE/DELETE/DDL 语句；staging 只写 ``--staging-out``
指定的文件（原子替换）。

用法
----
    python -X utf8 tools/intel_entity_extract.py --dry-run            # 默认，只打印统计
    python -X utf8 tools/intel_entity_extract.py --limit 5000 --dry-run
    python -X utf8 tools/intel_entity_extract.py --staging-out out.jsonl [--limit N]
    python -X utf8 tools/intel_entity_extract.py --db path/news.db --no-name-map

默认 DB 解析顺序：``<repo>/news_collector/news.db`` → 主 worktree（git worktree
list 首项）下的同名文件 → 报错提示 ``--db``。名称表默认在
``<repo>/quant_backtest/data/stock_names.json``（gitignored 数据根，故 worktree
内需回落到主 worktree 路径；也可 ``--name-map`` 显式指定）。

退出码：0 成功；2 参数/数据源错误（DB 不存在、news 表缺失、staging 写失败、
--dry-run 与 --staging-out 冲突等）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional

TOOL_VERSION = "v0.1"
HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[1]

#: A 股板块前缀 → 交易所（与 intel_collector/core/canonical.py 一致）
_BOARD_BY_PREFIX2 = {"60": "SH", "68": "SH", "90": "SH", "00": "SZ", "30": "SZ"}
_PREFIX_TRIM_RE = re.compile(r"^(SH|SZ|BJ)\s*")
_CODE6_RE = re.compile(r"\d{6}")

#: A 股 6 位代码的板前缀口径（与 normalize_symbol 一致；60/68/90→SH、00/30→SZ、8/4→BJ）
#: 注：任务书给出的字面正则 ``\b(?:60|68|90|00|30|8|4)\d{4}\b`` 中，8/4 为单字符
#: 分支 + 4 位数字只有 5 位，按字面无法命中 6 位北交所代码（如 830799/430047）；
#: 本工具按"A 股 6 位代码"的实质口径实现为 ``8/4 + 5 位数字``，其余完全相同。
_CODE_ALT = r"(?:(?:60|68|90|00|30)\d{4}|[84]\d{5})"
#: 词边界形态（高置信 exact 之一；等价于任务书正则的修正版）
RE_CODE_WORD = re.compile(r"\b" + _CODE_ALT + r"\b")
#: 前缀形态：SH600519 / sz 600519
RE_CODE_PREFIX = re.compile(r"(?i)(?<![A-Za-z0-9])(?:SH|SZ|BJ)\s*(" + _CODE_ALT + r")(?![0-9])")
#: 后缀形态：600519.SH / 600519SH / 600519 SH
RE_CODE_SUFFIX = re.compile(r"(?i)(?<!\d)(" + _CODE_ALT + r")\s*[.\-]?\s*(?:SH|SZ|BJ)(?![A-Za-z0-9])")
#: 中文紧贴补充形态（weak 层，不计入 exact）
RE_CODE_CJK = re.compile(r"(?<!\d)" + _CODE_ALT + r"(?!\d)")

#: 名称候选每行最多保留个数（与 guojia_reconcile 的 ENTS_MAX 口径一致）
MAX_CANDIDATES_PER_ROW = 8
#: 单行只做一次名称扫描的行数上限用于可复跑的确定性（无需）
DEFAULT_MAX_SAMPLES = 5


# ---------------------------------------------------------------------------
# A 股归一化（与 intel_collector/core/canonical.py 保持一致，测试含 parity 用例）
# ---------------------------------------------------------------------------
def normalize_symbol(raw: Optional[str]) -> Optional[str]:
    """任意形态 A 股代码 → ``{SH|SZ|BJ}{CODE6}``；非 A 股/无法判定返回 None。"""
    if not raw:
        return None
    s = str(raw).strip().upper()
    s = _PREFIX_TRIM_RE.sub("", s)                     # 去已有板前缀
    m = _CODE6_RE.search(s)
    if not m:
        return None
    code = m.group(0)
    lead2 = code[:2]
    if lead2 in _BOARD_BY_PREFIX2:
        board = _BOARD_BY_PREFIX2[lead2]
    elif code[0] in ("8", "4"):                        # 北交所
        board = "BJ"
    else:
        return None
    return f"{board}{code}"


def extract_entities(text: Optional[str]) -> dict[str, Any]:
    """从文本抽 A 股实体。

    返回 ``{"exact": [...], "weak": [...], "rules": {sym: rule}, "rejected": n}``：
    * exact：高置信显式代码（prefix/suffix/词边界三种形态），保序去重；
    * weak：中文紧贴补充（不含已进 exact 的）；
    * rejected：形似代码但按 A 股口径无法归一的 token 数（fail-visible）。
    """
    text = text or ""
    exact: list[str] = []
    rules: dict[str, str] = {}
    rejected = 0

    def _add(sym: Optional[str], rule: str) -> None:
        nonlocal rejected
        if sym is None:
            rejected += 1
            return
        if sym not in rules:
            rules[sym] = rule
            exact.append(sym)

    for m in RE_CODE_PREFIX.finditer(text):
        _add(normalize_symbol(m.group(0)), "prefix")
    for m in RE_CODE_SUFFIX.finditer(text):
        _add(normalize_symbol(m.group(0)), "suffix")
    for m in RE_CODE_WORD.finditer(text):
        _add(normalize_symbol(m.group(0)), "code")

    weak: list[str] = []
    for m in RE_CODE_CJK.finditer(text):
        sym = normalize_symbol(m.group(0))
        if sym is None:
            continue
        if sym not in rules and sym not in weak:
            weak.append(sym)
    return {"exact": exact, "weak": weak, "rules": rules, "rejected": rejected}


# ---------------------------------------------------------------------------
# 名称→代码候选：Aho-Corasick 多模式匹配（11k+ 名称，逐行子串扫描太慢）
# ---------------------------------------------------------------------------
class AhoCorasick:
    """最小 Aho-Corasick 自动机：literal 多模式匹配，返回命中的 payload 列表。"""

    def __init__(self, patterns: Iterable[tuple[str, Any]]):
        self.goto: list[dict[str, int]] = [{}]
        self.fail: list[int] = [0]
        self.out: list[list[Any]] = [[]]
        for word, payload in patterns:
            if not word:
                continue
            node = 0
            for ch in word:
                nxt = self.goto[node].get(ch)
                if nxt is None:
                    self.goto.append({})
                    self.fail.append(0)
                    self.out.append([])
                    nxt = len(self.goto) - 1
                    self.goto[node][ch] = nxt
                node = nxt
            self.out[node].append(payload)
        self._build_fail()

    def _build_fail(self) -> None:
        queue: list[int] = []
        for nxt in self.goto[0].values():
            queue.append(nxt)
        head = 0
        while head < len(queue):
            r = queue[head]
            head += 1
            for ch, u in self.goto[r].items():
                queue.append(u)
                f = self.fail[r]
                while f and ch not in self.goto[f]:
                    f = self.fail[f]
                cand = self.goto[f].get(ch, 0)
                self.fail[u] = 0 if cand == u else cand
                if self.out[self.fail[u]]:
                    self.out[u] = self.out[u] + self.out[self.fail[u]]

    def find(self, text: str) -> list[Any]:
        node = 0
        hits: list[Any] = []
        for ch in text:
            while node and ch not in self.goto[node]:
                node = self.fail[node]
            node = self.goto[node].get(ch, 0)
            if self.out[node]:
                hits.extend(self.out[node])
        return hits


class NameMatcher:
    """名称表 → 代码候选匹配器（candidate 层，绝不升级 exact）。"""

    def __init__(self, name_to_symbol: dict[str, str]):
        self.name_to_symbol = dict(name_to_symbol)
        # AC payload 用名称本身（命中后回查 symbol；未映射名称可见）
        pairs = [(name, name) for name in sorted(self.name_to_symbol)]
        self._ac = AhoCorasick(pairs) if pairs else None

    def __bool__(self) -> bool:
        return self._ac is not None

    def find_symbols(self, text: str) -> tuple[list[tuple[str, str]], list[str]]:
        """返回 ([(name, symbol), ...] 去重保序, [未映射名称, ...])。"""
        if not self._ac or not text:
            return [], []
        seen: set[str] = set()
        matched: list[tuple[str, str]] = []
        unmapped: list[str] = []
        for name in self._ac.find(text):
            if name in seen:
                continue
            seen.add(name)
            symbol = self.name_to_symbol.get(name)
            if symbol:
                matched.append((name, symbol))
            else:
                unmapped.append(name)
        return matched, unmapped


def load_name_map(path: Path) -> dict[str, Any]:
    """加载本地名称表（stock_names.json：name2code 优先，code2name 兜底）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    raw: dict[str, str] = {}
    if isinstance(data, dict):
        if isinstance(data.get("name2code"), dict):
            raw = {str(k): str(v) for k, v in data["name2code"].items()}
        elif isinstance(data.get("code2name"), dict):
            raw = {str(v): str(k) for k, v in data["code2name"].items()}
    elif isinstance(data, list):                       # 兼容 [{"name","code"}]
        for item in data:
            if isinstance(item, dict) and item.get("name") and item.get("code"):
                raw[str(item["name"])] = str(item["code"])
    name_to_symbol: dict[str, str] = {}
    skipped = 0
    for name, code in raw.items():
        name = str(name).strip()
        if len(name) < 2:                              # 单字名称误报风险过高
            skipped += 1
            continue
        symbol = normalize_symbol(code)
        if symbol is None:                             # 如 92 开头北交所新码，按口径不支持
            skipped += 1
            continue
        name_to_symbol[name] = symbol
    return {
        "path": str(path),
        "entries": len(name_to_symbol),
        "raw_entries": len(raw),
        "skipped_unsupported": skipped,
        "name_to_symbol": name_to_symbol,
        "generated_at": str(data.get("generated_at") or "") if isinstance(data, dict) else "",
    }


# ---------------------------------------------------------------------------
# 数据源定位（worktree 内 news.db / 名称表均为 gitignored，需回落主 worktree）
# ---------------------------------------------------------------------------
def git_worktrees(repo_root: Path) -> list[Path]:
    """返回 git worktree 列表（首个通常为主工作树）；git 不可用返回 []。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    out: list[Path] = []
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            try:
                out.append(Path(line[len("worktree "):].strip()).resolve())
            except OSError:
                continue
    return out


def _candidate_roots(repo_root: Path) -> list[Path]:
    roots = [repo_root]
    for wt in git_worktrees(repo_root):
        if wt not in roots:
            roots.append(wt)
    return roots


def resolve_default_db(repo_root: Path) -> Optional[Path]:
    for root in _candidate_roots(repo_root):
        cand = root / "news_collector" / "news.db"
        if cand.is_file():
            return cand
    return None


def resolve_default_name_map(repo_root: Path) -> Optional[Path]:
    for root in _candidate_roots(repo_root):
        cand = root / "quant_backtest" / "data" / "stock_names.json"
        if cand.is_file():
            return cand
    return None


# ---------------------------------------------------------------------------
# 只读 sqlite / news 表读取
# ---------------------------------------------------------------------------
def open_readonly(db_path: Path) -> sqlite3.Connection:
    """以 mode=ro URI 打开 sqlite 并设 query_only，任何写操作都会被拒绝。"""
    resolved = Path(db_path).resolve()
    uri = "file:%s?mode=ro" % resolved.as_posix()
    con = sqlite3.connect(uri, uri=True)
    con.execute("PRAGMA query_only=1")
    return con


def inspect_news_columns(con: sqlite3.Connection) -> list[str]:
    cols = [str(row[1]) for row in con.execute("PRAGMA table_info(news)").fetchall()]
    return cols


def _select_sql(columns: list[str], limit: int) -> str:
    def col(name: str) -> str:
        return name if name in columns else "NULL"

    base = (
        "SELECT {id}, {ts}, {source}, title, content, {stocks} FROM news "
    ).format(id=col("id"), ts=col("ts"), source=col("source"), stocks=col("stocks"))
    if limit > 0:
        return base + "ORDER BY ts DESC, id ASC LIMIT ?"
    return base + "ORDER BY rowid ASC"


def parse_stocks(raw: Any) -> tuple[list[str], Optional[str]]:
    """解析 news.stocks 列；返回 (非空名称列表, 坏行原因)。"""
    if raw is None:
        return [], None
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw if str(x).strip()], None
    s = str(raw).strip()
    if not s:
        return [], None
    try:
        val = json.loads(s)
    except (ValueError, TypeError):
        return [], "invalid_stocks_json"
    if val is None:
        return [], None
    if isinstance(val, list):
        return [str(x) for x in val if str(x).strip()], None
    return [], "stocks_not_list"


def _pct(part: int, total: int) -> float:
    return round(part * 100.0 / total, 2) if total else 0.0


# ---------------------------------------------------------------------------
# 主扫描
# ---------------------------------------------------------------------------
def scan(
    con: sqlite3.Connection,
    columns: list[str],
    *,
    limit: int = 0,
    matcher: Optional[NameMatcher] = None,
    cjk_adjacent: bool = True,
    max_candidates: int = MAX_CANDIDATES_PER_ROW,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """只读扫描 news 表，返回 (统计 report, staging 行列表)。"""
    total_rows = int(con.execute("SELECT COUNT(*) FROM news").fetchone()[0])
    sql = _select_sql(columns, limit)
    cur = con.execute(sql, (limit,)) if limit > 0 else con.execute(sql)

    scanned = 0
    before_rows = exact_rows = after_rows = 0
    weak_rows = after_weak_rows = 0
    candidate_rows = ambiguous_rows = candidate_unmapped_rows = 0
    bad_rows = 0
    empty_text_rows = 0
    rejected_tokens = 0
    exact_symbols_total = 0
    distinct_exact: set[str] = set()
    distinct_candidate: set[str] = set()
    candidate_symbols_total = 0
    bad_samples: list[dict[str, Any]] = []
    staging: list[dict[str, Any]] = []

    for row in cur:
        nid, ts, source, title, content, stocks_raw = row
        scanned += 1
        title_s = "" if title is None else str(title)
        content_s = "" if content is None else str(content)
        text = title_s + "\n" + content_s
        current, bad_reason = parse_stocks(stocks_raw)
        if bad_reason:
            bad_rows += 1
            if len(bad_samples) < DEFAULT_MAX_SAMPLES:
                bad_samples.append({
                    "id": nid, "reason": bad_reason,
                    "raw": (stocks_raw if isinstance(stocks_raw, str) else str(stocks_raw))[:120],
                })
        is_before = bool(current)
        before_rows += is_before

        ent = extract_entities(text)
        exact = ent["exact"]
        weak = ent["weak"] if cjk_adjacent else []
        rejected_tokens += ent["rejected"]
        if not title_s.strip() and not content_s.strip():
            empty_text_rows += 1
        exact_rows += 1 if exact else 0
        exact_symbols_total += len(exact)
        distinct_exact.update(exact)
        if weak:
            weak_rows += 1
        after_rows += 1 if (is_before or exact) else 0
        after_weak_rows += 1 if (is_before or exact or weak) else 0

        candidates: list[dict[str, Any]] = []
        unmapped_names: list[str] = []
        if exact:
            level = "exact"
        else:
            matched, unmapped_names = matcher.find_symbols(text) if matcher else ([], [])
            dedup: dict[str, str] = {}
            for name, symbol in matched:
                dedup.setdefault(symbol, name)
            if unmapped_names:
                candidate_unmapped_rows += 1
            for symbol in sorted(dedup)[:max_candidates]:
                candidates.append({"symbol": symbol, "name": dedup[symbol]})
            if candidates:
                candidate_rows += 1
                candidate_symbols_total += len(candidates)
                distinct_candidate.update(c["symbol"] for c in candidates)
                level = "ambiguous" if len({c["symbol"] for c in candidates}) > 1 else "candidate"
                if level == "ambiguous":
                    ambiguous_rows += 1
            elif weak:
                level = "weak"
            else:
                level = "none"

        if level != "none" or is_before:
            extracted: list[dict[str, Any]] = []
            for symbol in exact:
                extracted.append({
                    "symbol": symbol, "name": None,
                    "level": "exact", "rule": ent["rules"].get(symbol),
                })
            for symbol in weak:
                extracted.append({
                    "symbol": symbol, "name": None, "level": "weak", "rule": "cjk_adjacent",
                })
            for cand in candidates:
                extracted.append({
                    "symbol": cand["symbol"], "name": cand["name"],
                    "level": level, "rule": "name_map",
                })
            staging.append({
                "id": nid,
                "ts": ts,
                "source": source,
                "current_stocks": current,
                "extracted": extracted,
                "exact": list(exact),
                "candidates": list(candidates),
                "weak": list(weak),
                "level": level,
            })

    report: dict[str, Any] = {
        "tool_version": TOOL_VERSION,
        "db_mode": "ro",
        "table": "news",
        "columns": columns,
        "total_rows": total_rows,
        "scanned_rows": scanned,
        "limit": limit,
        "before_rows": before_rows,
        "before_pct": _pct(before_rows, scanned),
        "exact_rows": exact_rows,
        "after_rows": after_rows,
        "after_pct": _pct(after_rows, scanned),
        "delta_pp": round(_pct(after_rows, scanned) - _pct(before_rows, scanned), 2),
        "weak_rows": weak_rows,
        "after_incl_weak_rows": after_weak_rows,
        "after_incl_weak_pct": _pct(after_weak_rows, scanned),
        "candidate_rows": candidate_rows,
        "ambiguous_rows": ambiguous_rows,
        "candidate_unmapped_rows": candidate_unmapped_rows,
        "candidate_symbols_total": candidate_symbols_total,
        "distinct_candidate_symbols": len(distinct_candidate),
        "exact_symbols_total": exact_symbols_total,
        "distinct_exact_symbols": len(distinct_exact),
        "rejected_tokens": rejected_tokens,
        "bad_rows": bad_rows,
        "bad_samples": bad_samples,
        "empty_text_rows": empty_text_rows,
        "staging_rows": len(staging),
    }
    return report, staging


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def write_staging(rows: list[dict[str, Any]], path: Path) -> int:
    """原子写 staging JSONL（确定性序列化：键排序、无时间戳），可复跑幂等。"""
    path = Path(path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent or "."), suffix=".staging.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(rows)


def _fmt_bool(v: bool) -> str:
    return "true" if v else "false"


def format_report(report: dict[str, Any], *, dry_run: bool, name_map: dict[str, Any],
                  warnings: list[str]) -> str:
    lines: list[str] = []
    lines.append("[metadata]")
    lines.append("tool_version=%s" % report.get("tool_version", ""))
    lines.append("db=%s" % report.get("db_path", ""))
    lines.append("db_mode=%s" % report.get("db_mode", "ro"))
    lines.append("columns=%s" % ",".join(report.get("columns") or []))
    lim = report.get("limit", 0)
    lines.append("limit=%s" % (str(lim) if lim else "0(all)"))
    lines.append("[coverage]")
    lines.append("total_rows=%d" % report["total_rows"])
    lines.append("scanned_rows=%d" % report["scanned_rows"])
    lines.append("before_rows=%d" % report["before_rows"])
    lines.append("before_pct=%.2f%%" % report["before_pct"])
    lines.append("exact_rows=%d" % report["exact_rows"])
    lines.append("after_rows=%d" % report["after_rows"])
    lines.append("after_pct=%.2f%%" % report["after_pct"])
    lines.append("delta_pp=%.2f" % report["delta_pp"])
    lines.append("weak_rows=%d" % report["weak_rows"])
    lines.append("after_incl_weak_pct=%.2f%%" % report["after_incl_weak_pct"])
    lines.append("[entities]")
    lines.append("exact_symbols_total=%d" % report["exact_symbols_total"])
    lines.append("distinct_exact_symbols=%d" % report["distinct_exact_symbols"])
    lines.append("candidate_rows=%d" % report["candidate_rows"])
    lines.append("candidate_symbols_total=%d" % report["candidate_symbols_total"])
    lines.append("distinct_candidate_symbols=%d" % report["distinct_candidate_symbols"])
    lines.append("ambiguous_rows=%d" % report["ambiguous_rows"])
    lines.append("candidate_unmapped_rows=%d" % report["candidate_unmapped_rows"])
    lines.append("rejected_tokens=%d" % report["rejected_tokens"])
    lines.append("[quality]")
    lines.append("bad_rows=%d" % report["bad_rows"])
    lines.append("empty_text_rows=%d" % report["empty_text_rows"])
    lines.append("[name_map]")
    lines.append("name_map_loaded=%s" % _fmt_bool(bool(name_map.get("loaded"))))
    lines.append("name_map_path=%s" % (name_map.get("path") or ""))
    lines.append("name_map_entries=%d" % int(name_map.get("entries") or 0))
    lines.append("name_map_skipped_unsupported=%d" % int(name_map.get("skipped_unsupported") or 0))
    lines.append("[result]")
    lines.append("dry_run=%s" % _fmt_bool(dry_run))
    lines.append("staging_out=%s" % (report.get("staging_out") or ""))
    lines.append("staging_rows=%d" % report.get("staging_rows", 0))
    for w in warnings:
        lines.append("WARN %s" % w)
    for sample in report.get("bad_samples") or []:
        lines.append("BAD_ROW id=%s reason=%s raw=%s" % (
            sample.get("id"), sample.get("reason"), sample.get("raw")))
    lines.append("[done]")
    lines.append("rc=0")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="intel_entity_extract.py",
        description="news.db 情报实体抽取前置工具（只读 + staging，绝不写库）",
    )
    p.add_argument("--db", help="news.db 路径（默认自动定位主 worktree 的 news_collector/news.db）")
    p.add_argument("--limit", type=int, default=0,
                   help="抽样行数（默认 0=全量）；按 ts DESC, id ASC 取前 N 行，结果确定")
    p.add_argument("--dry-run", action="store_true",
                   help="只打印统计不落盘（默认行为；与 --staging-out 互斥）")
    p.add_argument("--staging-out", help="staging JSONL 输出路径（只写该文件，绝不写 news.db）")
    p.add_argument("--name-map", help="名称→代码表 JSON（默认自动找 stock_names.json）")
    p.add_argument("--no-name-map", action="store_true", help="跳过名称候选（candidate=0）")
    p.add_argument("--no-cjk-adjacent", action="store_true", help="关闭中文紧贴 weak 层补充抽取")
    p.add_argument("--max-candidates", type=int, default=MAX_CANDIDATES_PER_ROW,
                   help="每行名称候选上限（默认 %d）" % MAX_CANDIDATES_PER_ROW)
    p.add_argument("--json-out", help="可选：把统计 JSON 写到指定路径（排障/证据用）")
    return p


def _err(msg: str) -> int:
    sys.stderr.write("ERROR %s\n" % msg)
    sys.stderr.write("rc=2\n")
    return 2


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    warnings: list[str] = []

    if args.dry_run and args.staging_out:
        return _err("--dry-run 与 --staging-out 不能同时使用（默认即 dry-run）")
    if args.limit < 0:
        return _err("--limit 不能为负数")
    dry_run = not args.staging_out

    # 1) 数据源
    if args.db:
        db_path = Path(args.db)
        if not db_path.is_file():
            return _err("--db 指向的文件不存在: %s" % db_path)
    else:
        db_path = resolve_default_db(REPO_ROOT)
        if db_path is None:
            return _err(
                "未找到 news_collector/news.db（可显式传 --db <path>；worktree 内该文件 gitignored）"
            )

    # 2) 名称表（可选）
    name_map: dict[str, Any] = {"loaded": False, "path": "", "entries": 0, "skipped_unsupported": 0}
    matcher: Optional[NameMatcher] = None
    if not args.no_name_map:
        if args.name_map:
            nm_path: Optional[Path] = Path(args.name_map)
            if not nm_path.is_file():
                return _err("--name-map 指向的文件不存在: %s" % nm_path)
        else:
            nm_path = resolve_default_name_map(REPO_ROOT)
        if nm_path is None:
            warnings.append("name_map_not_found:candidate=0（可 --name-map 指定名称表）")
        else:
            try:
                loaded = load_name_map(nm_path)
            except (OSError, ValueError) as exc:
                warnings.append("name_map_load_failed:%s candidate=0" % str(exc)[:160])
            else:
                name_map = {
                    "loaded": True,
                    "path": loaded["path"],
                    "entries": loaded["entries"],
                    "skipped_unsupported": loaded["skipped_unsupported"],
                    "generated_at": loaded["generated_at"],
                }
                matcher = NameMatcher(loaded["name_to_symbol"]) if loaded["entries"] else None
    if matcher is None:
        warnings.append("candidate_off:名称候选未启用（不影响 exact 抽取）")

    # 3) 只读连接 + 表检查
    try:
        con = open_readonly(db_path)
    except sqlite3.Error as exc:
        return _err("无法以只读模式打开 DB: %s (%s)" % (db_path, exc))
    try:
        columns = inspect_news_columns(con)
        if not columns:
            return _err("news 表不存在（PRAGMA table_info(news) 为空）: %s" % db_path)
        missing = [c for c in ("id", "title", "content") if c not in columns]
        if missing:
            return _err("news 表缺少必需列: %s（实际列: %s）" % (",".join(missing), ",".join(columns)))
        if "stocks" not in columns:
            warnings.append("stocks_column_missing:before_rows=0")
        if "ts" not in columns:
            warnings.append("ts_column_missing")
        if "source" not in columns:
            warnings.append("source_column_missing")

        report, staging = scan(
            con, columns,
            limit=args.limit,
            matcher=matcher,
            cjk_adjacent=not args.no_cjk_adjacent,
            max_candidates=max(1, args.max_candidates),
        )
    finally:
        con.close()

    report["db_path"] = str(db_path)
    report["name_map"] = name_map
    report["cjk_adjacent"] = not args.no_cjk_adjacent
    report["staging_out"] = str(args.staging_out) if args.staging_out else ""

    # 4) staging 落盘（只写 --staging-out，绝不写 DB）
    if args.staging_out:
        try:
            written = write_staging(staging, Path(args.staging_out))
        except OSError as exc:
            return _err("staging 写入失败: %s (%s)" % (args.staging_out, exc))
        if written != len(staging):
            return _err("staging 行数不一致: written=%d expected=%d" % (written, len(staging)))
        report["staging_rows"] = written

    # 5) 统计输出
    sys.stdout.write(format_report(report, dry_run=dry_run, name_map=name_map, warnings=warnings))
    if args.json_out:
        try:
            out_path = Path(args.json_out)
            if out_path.parent and not out_path.parent.exists():
                out_path.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(report)
            payload["dry_run"] = dry_run
            payload["warnings"] = warnings
            out_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            return _err("--json-out 写入失败: %s (%s)" % (args.json_out, exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
