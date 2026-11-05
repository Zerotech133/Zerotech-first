# -*- coding: utf-8 -*-
"""P1 · 语料规范化导出：video_channel_map / videos / comments。

- 流式读取 948 个 ZIP 内 observations/*.jsonl.gz；
- video_metadata：按 video_id 保留**最新 collected_at** 的元信息；
- video_comments：按 video_id 保留最新一次观察的评论（扁平化为评论行）；
- 生成：
    10_规范化/video_channel_map.jsonl    (video_id, channel_id)
    10_规范化/videos.jsonl               (latest metadata)
    10_规范化/comments.jsonl             (评论+回复，author 哈希脱敏)
    10_规范化/*.parquet                  (转换)
    90_报告/p1_export_report.{json,md}
- 不改动原始 ZIP；PII：作者仅存 sha256 前 16 位；正文（标题/简介/评论）
  在写出前经 intel-kg `tools/pii_policy` 做 scrub+normalize 收敛清洗，
  并落 `90_报告/p1_export_pii_receipt.json` 凭证（W2，2026-11-06）。

用法：python tools/kb_corpus/export.py [--limit-zips N] [--no-pii-clean]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

KB = Path(r"C:\项目\知识库")
SUB = KB / "01_数据分包"
OUTD = KB / "10_规范化"
REP = KB / "90_报告"


def author_hash(x) -> str:
    return hashlib.sha256(str(x or "").encode("utf-8")).hexdigest()[:16]


_PII_MODULE = None
_PII_RULES: dict = {}


def _find_intel_kg_root() -> Path | None:
    """定位 intel-kg 根（pii_policy 唯一来源）。"""
    env = os.environ.get("INTEL_KG_ROOT")
    here = Path(__file__).resolve()
    cands: list[Path] = []
    if env:
        cands.append(Path(env))
    for up in (2, 3, 4):
        try:
            base = here.parents[up]
        except IndexError:
            continue
        cands += [base.parent / "intel-kg", base / "intel-kg"]
    cands.append(Path(r"C:\项目\intel-kg"))
    seen: set[Path] = set()
    for c in cands:
        try:
            c = c.resolve()
        except OSError:
            continue
        if c in seen:
            continue
        seen.add(c)
        if (c / "tools" / "pii_policy" / "policy.py").is_file():
            return c
    return None


def load_pii_policy():
    """加载 pii_policy；返回 (module, rules) 或 (None, {})。"""
    root = _find_intel_kg_root()
    if root is None:
        return None, {}
    tools = str(root / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from pii_policy import policy  # type: ignore
    return policy, policy.load_rules()


def clean_field(text: str, stats: dict) -> tuple[str, bool]:
    """对单字段做 closed_clean，累计统计；返回 (新文本, 是否变化)。"""
    text = str(text or "")
    if not text or _PII_MODULE is None:
        return text, False
    stats["fields_scanned"] += 1
    new, hits = _PII_MODULE.closed_clean(text, rules=_PII_RULES)
    for k, v in hits.items():
        stats["hit_rules"][k] = stats["hit_rules"].get(k, 0) + v
    changed = new != text
    if changed:
        stats["fields_changed"] += 1
    return new, changed


def zips() -> list[Path]:
    z = sorted(SUB.glob("*.zip")) + sorted((SUB / "补充更正_v2").glob("*.zip"))
    return z


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-zips", type=int, default=0)
    ap.add_argument("--no-pii-clean", action="store_true",
                    help="仅排障用：跳过正文 PII 清洗（默认强制清洗）")
    args = ap.parse_args(argv)
    global _PII_MODULE, _PII_RULES
    if not args.no_pii_clean:
        _PII_MODULE, _PII_RULES = load_pii_policy()
        if _PII_MODULE is None:
            print("[pii] 未找到 intel-kg/tools/pii_policy；拒绝导出（可用 --no-pii-clean 仅排障）",
                  file=sys.stderr)
            return 2
    pii_stats = {"enabled": _PII_MODULE is not None, "policy_version": "", "rules_digest": "",
                 "fields_scanned": 0, "fields_changed": 0, "hit_rules": {},
                 "videos_changed": 0, "comments_changed": 0}
    if _PII_MODULE is not None:
        pii_stats["policy_version"] = _PII_MODULE.POLICY_VERSION
        pii_stats["rules_digest"] = _PII_MODULE.rules_digest(_PII_RULES)
    OUTD.mkdir(parents=True, exist_ok=True)
    REP.mkdir(parents=True, exist_ok=True)
    all_zips = zips()
    if args.limit_zips:
        all_zips = all_zips[: args.limit_zips]
    meta: dict[str, dict] = {}       # video_id -> latest metadata row
    comments: dict[str, dict] = {}   # video_id -> latest comments snapshot {collected_at, rows}
    t0 = time.time()
    obs_files = 0
    line_total = 0

    for zi, z in enumerate(all_zips, 1):
        with zipfile.ZipFile(z) as zf:
            names = [n for n in zf.namelist() if n.endswith(".jsonl.gz")]
            for n in names:
                obs_files += 1
                with zf.open(n) as f:
                    data = gzip.decompress(f.read())
                for raw in data.splitlines():
                    if not raw.strip():
                        continue
                    line_total += 1
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        continue
                    et = row.get("entity_type")
                    payload = row.get("payload") or {}
                    vid = payload.get("video_id")
                    if not vid:
                        continue
                    ca = str(row.get("collected_at") or "")
                    if et == "video_metadata":
                        info = payload.get("info") or {}
                        prev = meta.get(vid)
                        if prev is None or ca >= prev.get("collected_at", ""):
                            meta[vid] = {
                                "video_id": vid,
                                "channel_id": info.get("channel_id") or "",
                                "title": info.get("title") or "",
                                "description": (info.get("description") or "")[:2000],
                                "upload_date": str(info.get("upload_date") or ""),
                                "duration": info.get("duration"),
                                "view_count": info.get("view_count"),
                                "like_count": info.get("like_count"),
                                "comment_count": info.get("comment_count"),
                                "tags": (info.get("tags") or [])[:30],
                                "language": info.get("language") or "",
                                "collected_at": ca,
                                "snapshot_id": row.get("snapshot_id") or "",
                            }
                    elif et == "video_comments":
                        prev = comments.get(vid)
                        if prev is None or ca >= prev.get("collected_at", ""):
                            rows = []
                            for c in (payload.get("comments") or []):
                                rows.append({
                                    "video_id": vid,
                                    "comment_id": str(c.get("id") or ""),
                                    "parent": str(c.get("parent") or "root"),
                                    "text": (c.get("text") or "")[:4000],
                                    "author_hash": author_hash(c.get("author_id") or c.get("author") or ""),
                                    "like_count": c.get("like_count"),
                                    "published_at": str(c.get("published_at") or c.get("time_text") or ""),
                                    "collected_at": ca,
                                    "snapshot_id": row.get("snapshot_id") or "",
                                })
                            comments[vid] = {"collected_at": ca, "rows": rows}
        if zi % 50 == 0:
            print(f"zips {zi}/{len(all_zips)} obs_files={obs_files} lines={line_total} "
                  f"videos={len(meta)} comments_videos={len(comments)} {time.time()-t0:.0f}s", flush=True)

    # 写出
    mp = OUTD / "video_channel_map.jsonl"
    with mp.open("w", encoding="utf-8") as f:
        for vid in sorted(meta):
            ch = meta[vid].get("channel_id") or ""
            if ch:
                f.write(json.dumps({"video_id": vid, "channel_id": ch}, ensure_ascii=False) + "\n")
    vp = OUTD / "videos.jsonl"
    with vp.open("w", encoding="utf-8") as f:
        for vid in sorted(meta):
            row = dict(meta[vid])
            title, c1 = clean_field(row.get("title") or "", pii_stats)
            desc, c2 = clean_field(row.get("description") or "", pii_stats)
            row["title"] = title
            row["description"] = desc[:2000]
            if c1 or c2:
                pii_stats["videos_changed"] += 1
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    cp = OUTD / "comments.jsonl"
    n_comments = 0
    with cp.open("w", encoding="utf-8") as f:
        for vid in sorted(comments):
            for r in comments[vid]["rows"]:
                row = dict(r)
                text, c = clean_field(row.get("text") or "", pii_stats)
                row["text"] = text[:4000]
                if c:
                    pii_stats["comments_changed"] += 1
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                n_comments += 1
    print(f"written map={sum(1 for _ in mp.open(encoding='utf-8'))} videos={len(meta)} comments={n_comments}", flush=True)

    # PII 清洗凭证（写出后，记录真实产出侧统计）
    pii_receipt = {
        "receipt_id": f"pii-export-{datetime.now().strftime('%Y%m%dT%H%M%S')}",
        "action": "export_pii_clean",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "policy_version": pii_stats["policy_version"],
        "rules_digest": pii_stats["rules_digest"],
        "enabled": pii_stats["enabled"],
        "outputs": {"videos": str(vp), "comments": str(cp)},
        "stats": {k: v for k, v in pii_stats.items() if k not in ("policy_version", "rules_digest")},
    }
    (REP / "p1_export_pii_receipt.json").write_text(
        json.dumps(pii_receipt, ensure_ascii=False, indent=1), encoding="utf-8")

    # 质量检查
    chans = {r["channel_id"] for r in meta.values() if r.get("channel_id")}
    report = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "zips": len(all_zips), "obs_files": obs_files, "lines": line_total,
              "videos": len(meta), "videos_with_channel": sum(1 for r in meta.values() if r.get("channel_id")),
              "channels": len(chans), "comments": n_comments,
              "seconds": round(time.time() - t0, 1),
              "outputs": {"map": str(mp), "videos": str(vp), "comments": str(cp)},
              "pii": {"enabled": pii_stats["enabled"],
                      "policy_version": pii_stats["policy_version"],
                      "videos_changed": pii_stats["videos_changed"],
                      "comments_changed": pii_stats["comments_changed"],
                      "fields_changed": pii_stats["fields_changed"],
                      "hit_rules": pii_stats["hit_rules"],
                      "receipt": str(REP / "p1_export_pii_receipt.json")}}
    (REP / "p1_export_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["# P1 · 语料规范化导出报告", "",
         f"- 时间：{report['generated_at']}；ZIP {report['zips']}；观察文件 {report['obs_files']}；行 {report['lines']}",
         f"- 视频 **{report['videos']}**（含频道 {report['videos_with_channel']}）；频道 **{report['channels']}**；评论/回复 **{report['comments']}**",
         f"- 耗时 {report['seconds']}s；输出目录 `C:\\项目\\知识库\\10_规范化\\`",
         f"- 正文 PII 清洗：policy `{pii_stats['policy_version'] or '-'}`；视频变更 {pii_stats['videos_changed']}、"
         f"评论变更 {pii_stats['comments_changed']}、字段命中 {pii_stats['hit_rules'] or 0}；"
         f"凭证 `90_报告/p1_export_pii_receipt.json`。",
         "- 评论作者字段仅存 sha256 前 16 位；原始 ZIP 未改动。"]
    (REP / "p1_export_report.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
