# -*- coding: utf-8 -*-
"""cowbridge — CowAgent 三层记忆 ↔ Hermes 执行记忆 桥接器（影子模式 v0.1.2）。

K272 主线一：记忆层打通。两侧记忆均为 Markdown 小节形态（## header + 内容），
桥=小节级搬运 + provenance 标注 + 内容哈希去重。

v0.1.2（2026-10-08 审计整改）：
  - selftest 补 index.db 主路径 fixture（审计 P1-2）：合成 chunks/messages
    最小 schema，断言 tier=long 记忆单元与 tier=dialog 会话转录均入候选
  - 头注释契约对齐 v0.1.1 真实结构（审计 P2-3）

v0.1.1（K273 实测适配）：
  - CowAgent 读取对齐真实存储：根部 MEMORY.md + memory/long-term/index.db
    （chunks source='memory' 记忆单元 + messages 会话转录 tier=dialog）
  - 噪音过滤增强：纯模板 preamble（标题+斜体+分隔线）不入候选
  - 双层去重：Cow 侧同 sha1 合并；影子增量文件 sha1 回读（重跑 new=0）

影子纪律（K270 整改确立）：
  - 所有写入只落 shadow/，两侧生产记忆文件零接触
  - to-cowagent/  = Hermes 记忆 → CowAgent 候选（candidates.jsonl + daily 样例）
  - to-hermes/    = CowAgent 记忆 → Hermes 增量（MEMORY.increment.md）
  - review-*.md   = 人审对照报告；人审拍板后才允许接生产写（后续版本）

契约（源码级核实，v0.1.1 实测修订）：
  - CowAgent: ~/cow 下根部 MEMORY.md（长期层，实测常为空模板，K273）+
    memory/long-term/index.db（chunks source='memory' 记忆单元 tier=long +
    messages 会话转录 tier=dialog——K273 起的主数据源）+
    memory/daily/*.md（兼容保留）
    （agent/memory/{config,storage,summarizer}.py + common/state_dir.py；
    MEMORY.md 注入系统提示词须精炼）
  - Hermes: $HERMES_HOME/memory/{MEMORY.md, USER.md}（BuiltinMemoryProvider，
    双文件+字符上限）

用法：
  python cowbridge.py selftest              # 合成样本（含 index.db fixture）全链路自测
  python cowbridge.py export-hermes         # 真实 Hermes → CowAgent 候选
  python cowbridge.py import-cow            # CowAgent → Hermes 候选（需源）
  python cowbridge.py report                # 人审对照报告
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

HERMES_HOME = Path(os.getenv("HERMES_HOME") or str(Path.home() / ".hermes"))
COW_WORKSPACE = Path.home() / "cow"           # CowAgent 默认 workspace
BRIDGE_DIR = Path(__file__).resolve().parent
SHADOW = BRIDGE_DIR / "shadow"

_SEC_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_TITLE_LINE = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)   # Markdown 标题行
_ITALIC_LINE = re.compile(r"^\*[^*]+\*$", re.MULTILINE)    # 整行斜体（模板说明）
_HR_LINE = re.compile(r"^ {0,3}(?:-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)  # 分隔线
PREAMBLE = "(preamble)"


def sha1_12(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _is_struct_noise(it: dict) -> bool:
    """纯结构 preamble（剔除标题/整行斜体/分隔线后无实质内容）＝模板噪音。

    MEMORY.md 模板的斜体说明行属此类（K273 实测泄漏）；
    有实质内容的 preamble（如 USER.md 的「用户偏好与画像记录」文本行）保留。
    """
    stripped = _TITLE_LINE.sub("", it["body"])
    stripped = _ITALIC_LINE.sub("", stripped)
    stripped = _HR_LINE.sub("", stripped)
    return it["header"] == PREAMBLE and not stripped.strip()


def split_sections(md_text: str) -> list[tuple[str, str]]:
    """Markdown 按 ## 小节切分 → [(header, body)]。无小节时整文归 '( preamble)'。"""
    matches = list(_SEC_RE.finditer(md_text))
    if not matches:
        body = md_text.strip()
        return [("(preamble)", body)] if body else []
    out = []
    pre = md_text[: matches[0].start()].strip()
    if pre:
        out.append(("(preamble)", pre))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        out.append((m.group(1).strip(), md_text[m.end():end].strip()))
    return out


# ── 读侧 ────────────────────────────────────────────────────

def read_hermes_memory() -> list[dict]:
    """Hermes 生产记忆（只读）：MEMORY.md + USER.md → 小节列表。"""
    items = []
    for name in ("MEMORY.md", "USER.md"):
        p = HERMES_HOME / "memory" / name
        if not p.exists():
            continue
        for header, body in split_sections(p.read_text(encoding="utf-8")):
            items.append({
                "side": "hermes", "file": name, "header": header,
                "body": body, "sha1": sha1_12(header + "\n" + body),
            })
    return items


def _read_cow_indexdb(root: Path) -> list[dict]:
    """CowAgent 长期库 index.db（只读 URI 防写锁）：

    - chunks 表 source='memory' 的记忆单元（按小节切）
    - messages 表对话记录（按 session 聚合为转录节，tier=dialog）

    实测结构（K273 冒烟）：根部 MEMORY.md 长期为空模板时，真实数据源是本库。
    """
    import sqlite3

    db = root / "memory" / "long-term" / "index.db"
    if not db.exists():
        return []
    items = []
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        for _cid, _source, path, text in con.execute(
            "SELECT id, source, path, text FROM chunks WHERE source='memory'"
        ):
            if not (text or "").strip():
                continue
            for header, body in split_sections(text):
                items.append({
                    "side": "cow", "file": f"indexdb:{path}", "tier": "long",
                    "header": header, "body": body,
                    "sha1": sha1_12(header + "\n" + body),
                })
        rows = con.execute(
            "SELECT session_id, seq, role, content FROM messages ORDER BY session_id, seq"
        ).fetchall()
        by_sess: dict[str, list[tuple[str, str]]] = {}
        for session_id, _seq, role, content in rows:
            try:
                parts = json.loads(content)
                text = "\n".join(
                    p.get("text", "") for p in parts if isinstance(p, dict)
                )
            except Exception:
                text = str(content)
            if text.strip():
                by_sess.setdefault(session_id, []).append((role, text))
        for session_id, msgs in by_sess.items():
            trans = "\n".join(
                f"{'用户' if r == 'user' else 'Cow'}: {t}" for r, t in msgs
            )[:4000]
            header = f"会话 {session_id}"
            items.append({
                "side": "cow", "file": f"indexdb:sessions/{session_id}",
                "tier": "dialog", "header": header, "body": trans,
                "sha1": sha1_12(header + "\n" + trans),
            })
    finally:
        con.close()
    return items


def read_cow_memory(cow_root: Path | None = None) -> list[dict]:
    """CowAgent 三层记忆（只读）。

    v0.1.1 实测路径（K273）：长期 = 根部 MEMORY.md（不在 memory/ 下）+ index.db；
    天级 daily/*.md 保留兼容；短期上下文在会话内，不跨进程——不在桥的范围。
    """
    base = cow_root or COW_WORKSPACE
    root = base / "memory"
    items = []
    # 根部 MEMORY.md（实测实际位置）+ 旧契约路径（兼容）
    for mem_md in (base / "MEMORY.md", root / "MEMORY.md"):
        if mem_md.exists():
            for header, body in split_sections(mem_md.read_text(encoding="utf-8")):
                items.append({
                    "side": "cow", "file": "MEMORY.md", "tier": "long",
                    "header": header, "body": body,
                    "sha1": sha1_12(header + "\n" + body),
                })
    for daily in sorted(root.glob("daily/*.md")):
        for header, body in split_sections(daily.read_text(encoding="utf-8")):
            items.append({
                "side": "cow", "file": "daily/" + daily.name, "tier": "mid",
                "header": header, "body": body,
                "sha1": sha1_12(header + "\n" + body),
            })
    items.extend(_read_cow_indexdb(base))
    return items


# ── 桥（影子写）─────────────────────────────────────────────

def export_hermes_to_cow(cow_root: Path | None = None) -> dict:
    """Hermes → CowAgent 候选：小节→jsonl + CowAgent daily 格式样例。"""
    out_dir = SHADOW / "to-cowagent"
    out_dir.mkdir(parents=True, exist_ok=True)
    # CowAgent 已有内容（真实或 fixtures）用于去重
    seen = {i["sha1"] for i in read_cow_memory(cow_root)}
    hermes_items = [i for i in read_hermes_memory() if not _is_struct_noise(i)]
    rows, new_items = [], []
    for it in hermes_items:
        row = dict(it, ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   action="new" if it["sha1"] not in seen else "dup")
        rows.append(row)
        if row["action"] == "new":
            new_items.append(it)
    (out_dir / "candidates.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
        encoding="utf-8")
    # CowAgent daily 格式样例（天级记忆形态，## header 小节流）
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"## Hermes 同步（{today}）\n"]
    for it in new_items:
        lines.append(f"- **[{it['file']}#{it['header']}]** {it['body'][:200]}\n")
    (out_dir / f"daily-{today}.sample.md").write_text(
        "\n".join(lines), encoding="utf-8")
    return {"total": len(rows), "new": len(new_items),
            "dup": len(rows) - len(new_items), "out": str(out_dir)}


def import_cow_to_hermes(cow_root: Path | None = None) -> dict:
    """CowAgent → Hermes 候选：生成 MEMORY.increment.md（影子，不碰生产）。"""
    out_dir = SHADOW / "to-hermes"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Hermes 已有小节去重 + 影子增量已导出去重（重跑不重复候选）
    seen = {i["sha1"] for i in read_hermes_memory()}
    inc_path = out_dir / "MEMORY.increment.md"
    if inc_path.exists():
        seen.update(re.findall(r"sha1:([0-9a-f]{12})", inc_path.read_text(encoding="utf-8")))
    cow_items, cow_seen = [], set()
    for i in read_cow_memory(cow_root):
        if _is_struct_noise(i) or i["sha1"] in cow_seen:
            continue
        cow_seen.add(i["sha1"])
        cow_items.append(i)
    blocks, n_new = [], 0
    for it in cow_items:
        if it["sha1"] in seen:
            continue
        n_new += 1
        blocks.append(
            f"### {it['header']}  \n"
            f"<!-- provenance: cow:{it['file']} sha1:{it['sha1']} tier:{it['tier']} -->\n"
            f"{it['body']}\n")
    inc = ["# Hermes MEMORY 增量候选（影子，待人审）\n",
           f"> 来源: {cow_root or COW_WORKSPACE} / memory/ · "
           f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · "
           f"新增 {n_new}/{len(cow_items)} 小节\n\n"] + blocks
    (out_dir / "MEMORY.increment.md").write_text("\n".join(inc), encoding="utf-8")
    return {"cow_total": len(cow_items), "new": n_new, "out": str(out_dir)}


# ── 生产写（v0.3 转正，2026-10-10 老田拍板）─────────────────
#
# 影子纪律仍然成立：生产写只在 provider 层 shadow_mode=false 且
# allow_write_cow=true 时被调用（见插件 __init__.py）；CLI 主流程
# （export/import/report）恒为影子候选，零生产写入。

def write_turn_to_cow(user_content: str, assistant_content: str,
                      session_id: str = "", cow_root: Path | None = None) -> dict:
    """生产写：一轮对话 → CowAgent index.db messages 层。

    写入后可被 read_cow_memory 以 tier=dialog 回读（闭合验证）。
    session_id 加 hermes- 前缀防与 CowAgent 原生会话撞号。
    """
    import sqlite3

    base = cow_root or COW_WORKSPACE
    db = base / "memory" / "long-term" / "index.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    sid = f"hermes-{session_id or datetime.now().strftime('%Y%m%d-%H%M%S')}"
    con = sqlite3.connect(str(db))
    try:
        con.executescript(
            "CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source TEXT, path TEXT, text TEXT);\n"
            "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id TEXT, seq INTEGER, role TEXT, content TEXT);")
        seq0 = con.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM messages WHERE session_id=?",
            (sid,)).fetchone()[0]
        for i, (role, text) in enumerate(
                [("user", user_content), ("assistant", assistant_content)]):
            con.execute(
                "INSERT INTO messages(session_id, seq, role, content) "
                "VALUES (?, ?, ?, ?)",
                (sid, seq0 + 1 + i, role, json.dumps(
                    [{"type": "text", "text": text}], ensure_ascii=False)))
        con.commit()
    finally:
        con.close()
    return {"written": 2, "session_id": sid, "db": str(db)}


def write_memory_to_cow(md_text: str, cow_root: Path | None = None) -> dict:
    """生产写：Hermes 记忆文本 → CowAgent daily 层（小节化 + sha1 去重）。

    去重范围 = CowAgent 全量已有小节（read_cow_memory）+ 目标文件已有小节；
    纯结构 preamble 不写。返回实际追加的小节数。
    """
    base = cow_root or COW_WORKSPACE
    daily_dir = base / "memory" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    f = daily_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    seen = {i["sha1"] for i in read_cow_memory(base)}
    added = []
    for header, body in split_sections(md_text):
        if _is_struct_noise({"header": header, "body": body}):
            continue
        s = sha1_12(header + "\n" + body)
        if s in seen:
            continue
        seen.add(s)
        added.append((header, body))
    if added:
        prefix = "" if f.exists() else "# Cow Daily\n"
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(prefix)
            for header, body in added:
                fh.write(f"\n## {header}\n{body}\n")
    return {"written": len(added), "file": str(f)}


# ── 自测与人审 ───────────────────────────────────────────────

def _make_indexdb_fixture(db_path: Path) -> None:
    """合成 CowAgent index.db（审计 P1-2：主路径补单测，2026-10-08）。

    只造桥读取所依赖的最小 schema：chunks(id, source, path, text) +
    messages(session_id, seq, role, content)。sessions 表与 chunks_fts
    （FTS5 虚表）桥不读取，fixture 省略。
    """
    import sqlite3
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(
            "CREATE TABLE chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source TEXT, path TEXT, text TEXT);\n"
            "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id TEXT, seq INTEGER, role TEXT, content TEXT);")
        con.execute(
            "INSERT INTO chunks(source, path, text) VALUES ('memory', ?, ?)",
            ("MEMORY.md", "## 桥上约定\n- fixture 记忆单元（indexdb 直读）"))
        for seq, (role, text) in enumerate([
            ("user", "(fixture) 把日报口径定成值@日期"),
            ("assistant", "(fixture) 好的，已记录"),
        ]):
            con.execute(
                "INSERT INTO messages(session_id, seq, role, content) "
                "VALUES (?, ?, ?, ?)",
                ("fixture_s1", seq, role, json.dumps(
                    [{"type": "text", "text": text}], ensure_ascii=False)))
        con.commit()
    finally:
        con.close()


def selftest() -> int:
    """合成 CowAgent workspace（含 index.db fixture）+ 真实 Hermes 侧全链路。"""
    import shutil
    fx = SHADOW / "fixtures" / "cow-workspace"
    if fx.parent.exists():
        shutil.rmtree(fx.parent)
    mem_dir = fx / "memory" / "daily"
    mem_dir.mkdir(parents=True)
    (mem_dir.parent / "MEMORY.md").write_text(
        "# Cow Memory\n\n## 用户偏好\n- 偏好简体中文回复\n\n"
        "## 项目约定\n- 台账用「值@日期」格式\n", encoding="utf-8")
    (mem_dir / "2026-10-08.md").write_text(
        "# Daily\n\n## 10-08 晨会\n- 拍板暴露面收敛\n\n## 10-08 午后\n- K272 影子桥开工\n",
        encoding="utf-8")

    # 0) index.db 主路径（审计 P1-2）：合成库直读——记忆单元 + 会话转录
    _make_indexdb_fixture(fx / "memory" / "long-term" / "index.db")
    idx = [i for i in read_cow_memory(cow_root=fx)
           if i["file"].startswith("indexdb:")]
    assert any(i["tier"] == "long" and i["header"] == "桥上约定" for i in idx), idx
    dlg = next(i for i in idx if i["tier"] == "dialog"
               and i["file"] == "indexdb:sessions/fixture_s1")
    assert "用户: (fixture) 把日报口径" in dlg["body"], dlg
    assert "Cow: (fixture) 好的" in dlg["body"], dlg

    # 1) import 方向：合成 cow（MEMORY.md + daily + index.db）→ increment
    r1 = import_cow_to_hermes(cow_root=fx)
    inc = (SHADOW / "to-hermes" / "MEMORY.increment.md").read_text(encoding="utf-8")
    # 有效候选小节 = MEMORY.md 2 + daily 2 + indexdb long 1 + indexdb dialog 1 = 6
    assert r1["cow_total"] == 6, r1
    assert r1["new"] == 6, r1
    assert "(preamble)" not in inc, "纯标题 preamble 不应出现在候选里"
    assert "provenance: cow:daily/2026-10-08.md" in inc
    assert "provenance: cow:indexdb:sessions/fixture_s1" in inc, \
        "index.db 会话转录应入候选"
    assert "tier:long" in inc and "tier:mid" in inc and "tier:dialog" in inc

    # 2) 幂等：同源再跑一次 → 已导出 sha1 全部回落，new=0（真幂等）
    r1b = import_cow_to_hermes(cow_root=fx)
    assert r1b["new"] == 0, f"幂等破坏: {r1} vs {r1b}"

    # 3) export 方向：真实 Hermes → 候选 jsonl + daily 样例
    r2 = export_hermes_to_cow(cow_root=fx)
    assert r2["total"] >= 1, "Hermes 记忆为空？"
    jl = (SHADOW / "to-cowagent" / "candidates.jsonl").read_text(encoding="utf-8")
    assert all(json.loads(l)["sha1"] for l in jl.splitlines() if l.strip())

    print(f"[selftest] PASS indexdb_items={len(idx)} "
          f"import cow_total={r1['cow_total']} new={r1['new']} "
          f"| export hermes_total={r2['total']} new={r2['new']} dup={r2['dup']}")
    return 0


def report() -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    p = SHADOW / f"review-{ts}.md"
    hermes = [i for i in read_hermes_memory() if not _is_struct_noise(i)]
    cow = [i for i in read_cow_memory() if not _is_struct_noise(i)]
    lines = [f"# cowbridge 人审对照报告 {ts}\n",
             f"- Hermes 生产记忆小节: {len(hermes)}（~/.hermes/memory/）",
             f"- CowAgent 记忆小节: {len(cow)}"
             f"（{COW_WORKSPACE}/memory/，未部署时为 0 属预期）\n",
             "## Hermes → CowAgent 候选", ""]
    for it in hermes:
        lines.append(f"- [{it['file']}#{it['header']}] sha1:{it['sha1']} · "
                     f"{it['body'][:80].replace(chr(10), ' ')}…")
    lines += ["", "## CowAgent → Hermes 候选", ""]
    if not cow:
        lines.append("- （CowAgent 未部署，无数据源；契约已就绪，"
                     "部署后重跑 import-cow 即出候选）")
    for it in cow:
        lines.append(f"- [{it['file']}#{it['header']}] tier:{it['tier']} "
                     f"sha1:{it['sha1']}")
    lines += ["", "## 人审清单（拍板项）", "",
              "1. Hermes 小节哪些可进 CowAgent 长期记忆（须精炼、有价值）",
              "2. increment 中的 CowAgent 条目是否放行写入 Hermes MEMORY.md",
              "3. 放行口径拍板后，生产写路由接入（v0.2）"]
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


def main() -> int:
    ap = argparse.ArgumentParser(description="CowAgent↔Hermes 记忆桥（影子）")
    ap.add_argument("cmd", choices=["selftest", "export-hermes",
                                    "import-cow", "report"])
    ap.add_argument("--cow-root", default=None, help="CowAgent workspace 根（默认 ~/cow）")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "export-hermes":
        print(json.dumps(export_hermes_to_cow(
            Path(a.cow_root) if a.cow_root else None), ensure_ascii=False))
        return 0
    if a.cmd == "import-cow":
        print(json.dumps(import_cow_to_hermes(
            Path(a.cow_root) if a.cow_root else None), ensure_ascii=False))
        return 0
    print("report:", report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
