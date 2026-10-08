# -*- coding: utf-8 -*-
"""cowbridge — CowAgent 三层记忆 ↔ Hermes 执行记忆 桥接器（影子模式 v0.1）。

K272 主线一：记忆层打通。两侧记忆均为 Markdown 小节形态（## header + 内容），
桥=小节级搬运 + provenance 标注 + 内容哈希去重。

影子纪律（K270 整改确立）：
  - 所有写入只落 shadow/，两侧生产记忆文件零接触
  - to-cowagent/  = Hermes 记忆 → CowAgent 候选（candidates.jsonl + daily 样例）
  - to-hermes/    = CowAgent 记忆 → Hermes 增量（MEMORY.increment.md）
  - review-*.md   = 人审对照报告；人审拍板后才允许接生产写（后续版本）

契约（源码级核实）：
  - CowAgent: workspace/memory/{MEMORY.md, daily 或小节流, long-term/index.db}
    （agent/memory/{config,storage,summarizer}.py + common/state_dir.py，
    默认 workspace=~/cow；MEMORY.md 注入系统提示词须精炼）
  - Hermes: $HERMES_HOME/memory/{MEMORY.md, USER.md}（BuiltinMemoryProvider，
    双文件+字符上限）

用法：
  python cowbridge.py selftest              # 合成样本全链路自测
  python cowbridge.py export-hermes         # 真实 Hermes → CowAgent 候选
  python cowbridge.py import-cow            # CowAgent → Hermes 候选（需源）
  python cowbridge.py report                # 人审对照报告
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERMES_HOME = Path(r"C:\Users\Administrator\.hermes")
COW_WORKSPACE = Path.home() / "cow"           # CowAgent 默认 workspace
BRIDGE_DIR = Path(__file__).resolve().parent
SHADOW = BRIDGE_DIR / "shadow"

_SEC_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_TITLE_LINE = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)   # Markdown 标题行
PREAMBLE = "(preamble)"


def sha1_12(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _is_struct_noise(it: dict) -> bool:
    """纯标题 preamble（剔除标题行后无实质内容）＝结构噪音，非记忆单元。

    每个带 H1 的文件都会产出一条，放进人审材料只会制造噪音；
    有实质内容的 preamble（如 USER.md 的主题描述行）保留，人审可判。
    """
    return it["header"] == PREAMBLE and not _TITLE_LINE.sub("", it["body"]).strip()


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


def read_cow_memory(cow_root: Path | None = None) -> list[dict]:
    """CowAgent 三层记忆（只读）：MEMORY.md（长期）+ daily/*.md（天级）。

    短期上下文在会话内，不跨进程——不在桥的范围。
    """
    root = (cow_root or COW_WORKSPACE) / "memory"
    items = []
    mem_md = root / "MEMORY.md"
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
    # Hermes 已有小节去重
    seen = {i["sha1"] for i in read_hermes_memory()}
    cow_items = [i for i in read_cow_memory(cow_root) if not _is_struct_noise(i)]
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


# ── 自测与人审 ───────────────────────────────────────────────

def selftest() -> int:
    """合成 CowAgent workspace + 真实 Hermes 侧全链路。"""
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

    # 1) import 方向：合成 cow → increment
    r1 = import_cow_to_hermes(cow_root=fx)
    inc = (SHADOW / "to-hermes" / "MEMORY.increment.md").read_text(encoding="utf-8")
    # 有效候选小节 = 2 小节 × 2 文件 = 4（纯标题 preamble 已排除）
    assert r1["cow_total"] == 4, r1
    assert r1["new"] == 4, r1
    assert "(preamble)" not in inc, "纯标题 preamble 不应出现在候选里"
    assert "provenance: cow:daily/2026-10-08.md" in inc
    assert "tier:long" in inc and "tier:mid" in inc

    # 2) 幂等：同源再跑一次 → new 回落为与去重集一致
    r1b = import_cow_to_hermes(cow_root=fx)
    assert r1b["new"] == r1["new"], f"幂等破坏: {r1} vs {r1b}"

    # 3) export 方向：真实 Hermes → 候选 jsonl + daily 样例
    r2 = export_hermes_to_cow(cow_root=fx)
    assert r2["total"] >= 1, "Hermes 记忆为空？"
    jl = (SHADOW / "to-cowagent" / "candidates.jsonl").read_text(encoding="utf-8")
    assert all(json.loads(l)["sha1"] for l in jl.splitlines() if l.strip())

    print(f"[selftest] PASS import cow_total={r1['cow_total']} new={r1['new']} "
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
