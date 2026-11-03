# -*- coding: utf-8 -*-
"""知识单元重复 cohort 合并（W0-2 / task-138）。

规则：读 `30_知识单元/<pid>_units_draft.jsonl`，按**归一化 claim（取前 14 个 CJK 字符）**
分组；重复组保留首条，合并 evidence 列表（记录 merged_count / merged_unit_ids / evidence_list）。
默认 **dry-run**（只出报告不写）；`--apply` 才写回并先备份到 `research/reports/backups/`。
默认跳过抽取元数据行（时长/字数/无语音/共 N 条）以免把每视频时间戳合并丢失；
`--include-boilerplate` 可切换为字面全量规则。

输出：research/reports/knowledge_dedupe_20261103.{json,md}（duplicates/merged/backup/前后数量）
**不修改知识卡/其他项目**。

CLI::
    python tools/kb_corpus/knowledge_dedupe.py build --input <jsonl> [--apply]
    python tools/kb_corpus/knowledge_dedupe.py build --input <jsonl> --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research" / "reports"
BACKUPS = R / "backups"
OUT_JSON = R / "knowledge_dedupe_20261103.json"
OUT_MD = R / "knowledge_dedupe_20261103.md"
DATE = "20261103"
KEY_LEN = 14
CJK = re.compile(r"[\u4e00-\u9fff]")
PUNCT = re.compile(r"[\s\u3000（）()【】\[\]《》「」『』“”\"'’·、，,。.：:；;？?！!—-]+")
BOILER = re.compile(r"^[\s_\-*>]*(?:时长\s*[:：]|字数\s*[:：]|共\s*\d+\s*条)|无语音|转写为空")


def load_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def normalize_key(claim, limit: int = KEY_LEN) -> str:
    """归一化 claim：仅 CJK 字符，取前 limit 个字。"""
    return "".join(CJK.findall(str(claim or "")))[:limit]


def is_boilerplate(claim) -> bool:
    s = str(claim or "").strip()
    if not s:
        return True
    if BOILER.search(s):
        return True
    return not normalize_key(s)


def dedupe(units: list[dict], *, skip_boilerplate: bool = True) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    kept = []
    boiler = 0
    for u in units:
        if skip_boilerplate and is_boilerplate(u.get("claim")):
            boiler += 1
            kept.append(u)
            continue
        groups[normalize_key(u.get("claim"))].append(u)
    dup_groups = []
    merged_units = {}
    for key, grp in groups.items():
        first = grp[0]
        if len(grp) == 1:
            kept.append(first)
            continue
        evidence_list, seen = [], set()
        unit_ids = []
        for u in grp:
            unit_ids.append(u.get("unit_id"))
            ev = u.get("evidence")
            sig = json.dumps(ev, ensure_ascii=False, sort_keys=True)
            if sig not in seen:
                seen.add(sig)
                evidence_list.append(ev)
        merged = dict(first)
        merged["merged_count"] = len(grp)
        merged["merged_unit_ids"] = unit_ids
        merged["evidence_list"] = evidence_list
        merged_units[unit_ids[0]] = merged
        kept.append(merged)
        dup_groups.append({"key": key, "size": len(grp), "kept_unit_id": unit_ids[0],
                           "merged_unit_ids": unit_ids, "evidence_count": len(evidence_list)})
    # 保持原顺序：按首次出现位置输出
    order = {id(u): i for i, u in enumerate(units)}
    kept.sort(key=lambda u: order.get(id(u), 10**9))
    return {"units": kept, "dup_groups": dup_groups, "boilerplate": boiler,
            "input": len(units), "output": len(kept)}


def _pid_slug(payload: dict) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(payload.get("project") or "unknown"))[:40]


def write_report(payload: dict, out_dir: Path | None = None) -> dict:
    out = Path(out_dir) if out_dir else R
    out.mkdir(parents=True, exist_ok=True)
    pid = _pid_slug(payload)
    jp = out / f"knowledge_dedupe_{payload['date']}_{pid}.json"
    mp = out / f"knowledge_dedupe_{payload['date']}_{pid}.md"
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    s = payload["stats"]
    lines = [
        "# 知识单元重复 cohort 合并报告（W0-2 · 2026-11-03）", "",
        f"> 输入：`{payload['source']['path']}` · 单元 **{s['input']}** · dry_run={payload['dry_run']}",
        f"> 规则：按归一化 claim 前 {KEY_LEN} 个 CJK 字符分组；保留首条 + 合并 evidence；"
        f"跳过抽取元数据={payload['policy']['skip_boilerplate']}",
        f"> 结果：重复组 **{s['dup_groups']}** · 重复行 **{s['duplicate_rows']}** · "
        f"合并后 **{s['output']}**（合并 {s['merged']}）· 跳过元数据 {s['boilerplate']}",
        "", "## 重复组样例（前 15）", "",
        "| key | 组大小 | 保留 unit_id | 合并条数 | evidence 数 |", "|---|---|---|---|---|",
    ]
    for g in payload["duplicates"][:15]:
        lines.append(f"| {g['key']} | {g['size']} | {g['kept_unit_id']} | "
                     f"{g['size'] - 1} | {g['evidence_count']} |")
    if not payload["duplicates"]:
        lines.append("| — | — | — | — | — |")
    lines += ["", "## 说明", "",
              f"- 预计变化：{s['input']} → {s['output']}（-{s['duplicate_rows']}）",
              f"- 备份：{payload['backup'] or '（dry-run 未写/无备份）'}",
              "- 默认 dry-run；--apply 才写回并先备份；重复为 0 时 apply 不写（幂等）。",
              "- 本工具不修改知识卡/其他项目；仅作用于输入 units 文件。",
              "", f"> {payload['disclaimer']}", ""]
    mp.write_text("\n".join(lines), encoding="utf-8")
    # 旧文件名兼容：knowledge_dedupe_<date>.{json,md} = 最近一次运行
    legacy_jp = out / f"knowledge_dedupe_{payload['date']}.json"
    legacy_mp = out / f"knowledge_dedupe_{payload['date']}.md"
    legacy_jp.write_text(jp.read_text(encoding="utf-8"), encoding="utf-8")
    legacy_mp.write_text(mp.read_text(encoding="utf-8"), encoding="utf-8")
    return {"json": str(jp), "md": str(mp),
            "legacy": {"json": str(legacy_jp), "md": str(legacy_mp)}}


def build(*, input_path: str | Path, apply: bool = False, date: str = DATE,
          skip_boilerplate: bool = True, out_dir: str | Path = R,
          backup_dir: str | Path | None = None) -> dict:
    inp = Path(input_path)
    units = load_jsonl(inp)
    result = dedupe(units, skip_boilerplate=skip_boilerplate)
    dup_groups = result["dup_groups"]
    duplicate_rows = sum(g["size"] - 1 for g in dup_groups)
    stats = {
        "input": result["input"], "output": result["output"],
        "dup_groups": len(dup_groups), "duplicate_rows": duplicate_rows,
        "merged": duplicate_rows, "boilerplate": result["boilerplate"],
        "by_group_size": dict(Counter(g["size"] for g in dup_groups)),
    }
    payload = {
        "schema_version": 1, "date": date,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": "tools/kb_corpus/knowledge_dedupe.py（W0-2 重复单元合并）",
        "mode": "apply" if apply else "dry-run",
        "dry_run": not apply,
        "project": inp.name.split("_units_draft")[0],
        "source": {"path": str(inp), "sha256": hashlib.sha256(inp.read_bytes()).hexdigest(),
                   "bytes": inp.stat().st_size},
        "policy": {"key": f"normalize_claim_first_{KEY_LEN}_cjk",
                   "skip_boilerplate": skip_boilerplate,
                   "merge": "保留首条 + merged_count/merged_unit_ids/evidence_list",
                   "write": "--apply 才写；写前备份 research/reports/backups/"},
        "stats": stats,
        "duplicates": dup_groups,
        "kept_sample": [u.get("unit_id") for u in result["units"][:10]],
        "notes": [],
        "backup": None,
        "applied": False,
        "disclaimer": "W0-2 重复单元合并：默认 dry-run；不修改知识卡/其他项目；不构成投资建议。",
    }
    if payload["project"].startswith("视频号学习_8博主"):
        payload["notes"].append(
            "任务预期 -218 基于旧审计的 1018 单元快照；当前 30_知识单元 文件经 re-extraction 扩充为 "
            f"{stats['input']} 行（其中元数据行 {stats['boilerplate']}），实际重复 {stats['duplicate_rows']} 行"
            f"（占比 {stats['duplicate_rows'] / max(1, stats['input'] - stats['boilerplate']):.1%}，"
            "与旧审计 21.4% 一致）；按比例口径当前应约 -407，故实际值与旧预期 218 不同。")
    if apply:
        backup = None
        if duplicate_rows > 0:
            bdir = Path(backup_dir) if backup_dir else BACKUPS
            bdir.mkdir(parents=True, exist_ok=True)
            backup = bdir / f"{inp.name}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.v1bak"
            backup.write_bytes(inp.read_bytes())
            # 写回去重后 units（原子：先写 tmp 再 replace）
            tmp = inp.with_name(inp.name + ".tmp")
            tmp.write_text("\n".join(json.dumps(u, ensure_ascii=False) for u in result["units"]) + "\n",
                           encoding="utf-8")
            tmp.replace(inp)
            payload["applied"] = True
        payload["backup"] = str(backup) if backup else None
        payload["stats"]["output_sha256"] = hashlib.sha256(inp.read_bytes()).hexdigest()
        payload["idempotent_noop"] = duplicate_rows == 0
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload["_outputs"] = write_report(payload, out)
    return payload


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass
    parser = argparse.ArgumentParser(description="知识单元重复 cohort 合并（默认 dry-run）")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--input", required=True, help="<pid>_units_draft.jsonl 路径")
    parser.add_argument("--apply", action="store_true", help="真实写回（默认 dry-run）")
    parser.add_argument("--include-boilerplate", action="store_true",
                        help="字面全量规则（也合并时长/字数等元数据行）")
    parser.add_argument("--date", default=DATE)
    parser.add_argument("--out-dir", default=str(R))
    parser.add_argument("--backup-dir", default=str(BACKUPS))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = build(input_path=args.input, apply=args.apply, date=args.date,
                    skip_boilerplate=not args.include_boilerplate,
                    out_dir=args.out_dir, backup_dir=args.backup_dir)
    s = payload["stats"]
    if args.json:
        print(json.dumps({k: v for k, v in payload.items() if k != "_outputs"}, ensure_ascii=False, indent=2))
    else:
        print(f"[knowledge_dedupe] {payload['mode']} | {payload['source']['path']}")
        print(f"  单元 {s['input']} → {s['output']}（-{s['duplicate_rows']}；重复组 {s['dup_groups']}；"
              f"跳过元数据 {s['boilerplate']}）| applied={payload['applied']} backup={payload['backup']}")
        for name, path in (payload.get("_outputs") or {}).items():
            print(f"  输出({name})：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
