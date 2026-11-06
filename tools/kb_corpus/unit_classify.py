# -*- coding: utf-8 -*-
r"""unit 内容分型（M/T/U）入库打标工具 —— 规则带版本号，默认 dry-run。

背景：单元每天新增约 1.6 条，"内容分型"原先靠人工临时判断，无法解释历史判定、
无法批量重算。本工具把分型固化为可版本化规则，入库即打标。

用法（Git Bash / Windows，只用标准库）：
  python tools/kb_corpus/unit_classify.py --input <jsonl>                        # dry-run
  python tools/kb_corpus/unit_classify.py --input <jsonl> --write                # 落盘到同名 _classified.jsonl
  python tools/kb_corpus/unit_classify.py --input <jsonl> --write --out <new>    # 落盘到指定新文件
  python tools/kb_corpus/unit_classify.py --input <jsonl> --write --rule-version v2 --reclassify
  python tools/kb_corpus/unit_classify.py --input <jsonl> --json                 # 机器可读摘要
  python tools/kb_corpus/unit_classify.py --input <jsonl> --id-map <map.jsonl>   # 用 id 迁移表补 pit

存量 unit 的 evidence.file 只有 `<项目>.md`、unit_id 只有序号，单独跑无法推出时点，
故支持 `--id-map` 读取 id-migrator 产出的 `_unit_id_map.jsonl`
（字段 legacy_unit_id / unit_id / video_key / seq），按 legacy_unit_id 取 video_key 再解析 pit。
映射表缺失或损坏不致命：退回原有解析顺序并告警。

写入字段（原有字段全部保留，只追加）：
  unit_class              M / T / U
  classify_rule_version   规则版本，默认 v1
  pit                     时点；来源见下
  expire_at               T 类 = pit + 3 天；其余 null（pit 为 null 时亦为 null）
  content_hash            sha256(claim)[:16]

pit 解析顺序：unit 自带 pit(self) → --id-map 映射(unit_id_map) → evidence.file → unit_id。
自带 pit 优先于映射表：记录自身的时点是自述事实，避免被外部映射表静默改写；
两者不一致时计入 `pit_conflicts` 告警。

`--id-map` 默认指向 `C:/项目/知识库/35_知识提炼/_unit_id_map.jsonl`（id-migrator 产出）。
默认表尚未生成时静默退回（摘要里 `id_map.error=映射表不存在`）；显式指定的表缺失/损坏则告警。
映射表按 `legacy_unit_id`（主键）与 `unit_id`（兜底键）**全等**匹配，不做前缀/模糊匹配，
因此 `legacy_whole_video`（整篇粒度，带 legacy 标记）与段级哈希命名空间的 unit 不会被混淆。

--reclassify 语义：全量重算且不丢历史 —— 已有 unit_class 先搬进 unit_class_prev，
旧规则版本搬进 classify_rule_version_prev，每行写 reclassified_at。

安全约束：默认 dry-run；只有 --write 才落盘，且只写 --out 指定的新文件；
不覆盖输入文件、不覆盖已存在的输出文件；不做删除；不触碰数据库。

已知缺陷（v1，待 v2 规则修）
--------------------------------
规则 v1 已冻结为基线锚点（拆条器 / 段切分器 / 健康报表共同引用 20/83/5），
以下缺陷**不在本版本修**，仅登记备查：改动判定逻辑会让"规则变了"与"管道变了"无法归因。
  1. `..._0063` t=6/k=6 落 U，实为方法论 —— `k>t` 与 `t>k` 双严格不等留下数词平局缝隙
  2. `..._0032` 含"第…课"硬优先压过 T（t=3>k=2）
  3. "禅中说禅第五时刻"类中文数字漏检（`第\s*\d+\s*课` 未覆盖汉字数字）
  4. `t>=3 且 k>=3` 占 50/108（46%），判定实际由 `t>k` 单点决定，长文本（中位 1247 字）
     天然 TIME 词更多 → 系统性偏 T
  5. 规则无"中性/过渡"档，所有非 M 非 T 都落 U，U 被当成垃圾桶
  6. 合并单元按主视频立类，不取成员最高优先级。实测 `..._0063` 主 U 而成员 1002_2058 为 M，
     导致该 M 内容在 legacy 口径不进抽取队列。v2 段切分按 114 条全量、不合并，该视频独立成段
     并被正确判 M，故只在 legacy 口径存在，**不修**。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover - 非 utf8 终端兜底
    pass

RULE_VERSION_DEFAULT = "v1"
EXPIRE_DAYS_T = 3
TZ_CN = timezone(timedelta(hours=8))
DEFAULT_ID_MAP = "C:/项目/知识库/35_知识提炼/_unit_id_map.jsonl"

# ---------------------------------------------------------------- 分型规则 v1
# 规则固定，一行一字不得改动；判定顺序也是规则的一部分（先 M，再 T，最后 U）。
TIME = re.compile(
    r"(今天|上午|下午|收盘|开盘|放量|缩量|账户|个股红|赚钱效应|承接|低开高走|高开低走|板块上|指数|日复盘)"
)
METH = re.compile(
    r"(第\s*\d+\s*课|核心内容|方法论|规则|条件|如果.{0,20}就|操作要点|口诀|标准|定义|意味着)"
)


def classify_v1(claim: str) -> str:
    """v1：M=方法论 / T=时效复盘 / U=未定。"""
    t = len(TIME.findall(claim))
    k = len(METH.findall(claim))
    if ("第" in claim and "课" in claim) or (k >= 3 and k > t):
        unit_class = "M"  # 方法论
    elif t > k:
        unit_class = "T"  # 时效复盘
    else:
        unit_class = "U"  # 未定
    return unit_class


# 版本注册表。未注册的版本不臆造语义：沿用 v1 判定逻辑，仅标签不同，并在摘要里告警。
RULES = {"v1": classify_v1}
FALLBACK_RULE = "v1"

# -------------------------------------------------------------------- 时点
VIDEO_KEY = re.compile(r"(20\d{2})(\d{2})(\d{2})_(\d{2})(\d{2})")


def _parse_dt(text: str):
    """把 ISO 风格时间串解析为带 +08:00 的 datetime，失败返回 None。"""
    s = str(text).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_CN)
    return dt.astimezone(TZ_CN)


def _fmt_dt(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _evidence_file(rec: dict) -> str:
    ev = rec.get("evidence")
    if isinstance(ev, dict):
        val = ev.get("file")
        return val if isinstance(val, str) else ""
    return ""


def _pit_from_video_key(text: str):
    """从含 YYYYMMDD_HHMM 的串解析时点，失败返回 None。"""
    m = VIDEO_KEY.search(text or "")
    if not m:
        return None
    y, mo, d, hh, mm = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, hh, mm, tzinfo=TZ_CN)
    except ValueError:
        return None


def load_id_map(path):
    """读取 id-migrator 的 _unit_id_map.jsonl。

    返回 (by_legacy, by_unit, meta)。任何异常都不抛出——退回空映射并记录到 meta["error"]，
    由调用方告警后按原有顺序解析，保证单点坏文件不会让整批打标失败。
    """
    meta = {"path": str(path), "loaded": False, "rows": 0, "invalid": 0, "error": None}
    by_legacy, by_unit = {}, {}
    p = Path(path)
    try:
        if not p.is_file():
            meta["error"] = "映射表不存在"
            return by_legacy, by_unit, meta
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                meta["rows"] += 1
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    meta["invalid"] += 1
                    continue
                if not isinstance(row, dict):
                    meta["invalid"] += 1
                    continue
                vk = row.get("video_key")
                if not isinstance(vk, str) or not VIDEO_KEY.search(vk):
                    meta["invalid"] += 1
                    continue
                legacy = row.get("legacy_unit_id")
                new_id = row.get("unit_id")
                if isinstance(legacy, str) and legacy:
                    by_legacy.setdefault(legacy, vk)
                if isinstance(new_id, str) and new_id:
                    by_unit.setdefault(new_id, vk)
        meta["loaded"] = bool(by_legacy or by_unit)
        if not meta["loaded"]:
            meta["error"] = "映射表无可用的 legacy_unit_id + video_key 行"
    except OSError as exc:
        meta["error"] = f"映射表读取失败: {exc}"
        return {}, {}, meta
    return by_legacy, by_unit, meta


def resolve_pit(rec: dict, id_map=None):
    """解析时点。返回 (pit_str_or_None, source, pit_dt_or_None)。

    顺序：unit 自带 pit(self) → id_map 映射(unit_id_map) → evidence.file → unit_id → unresolved。
    自带 pit 若无法解析，原值照搬保留（不丢数据），但 pit_dt 为 None，且不再用映射表覆盖。
    """
    raw = rec.get("pit")
    if isinstance(raw, str) and raw.strip():
        dt = _parse_dt(raw)
        if dt is not None:
            return _fmt_dt(dt), "self", dt
        return raw.strip(), "self(unparsed)", None
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo else raw.replace(tzinfo=TZ_CN)
        return _fmt_dt(dt), "self", dt

    if id_map is not None:
        by_legacy, by_unit = id_map
        uid = str(rec.get("unit_id") or "")
        legacy = rec.get("legacy_unit_id")
        vk = None
        for key in (legacy if isinstance(legacy, str) else None, uid):
            if not key:
                continue
            vk = by_legacy.get(key) or by_unit.get(key)
            if vk:
                break
        if vk:
            dt = _pit_from_video_key(vk)
            if dt is not None:
                return _fmt_dt(dt), "unit_id_map", dt

    for source, text in (
        ("evidence_file", _evidence_file(rec)),
        ("unit_id", str(rec.get("unit_id") or "")),
    ):
        dt = _pit_from_video_key(text)
        if dt is not None:
            return _fmt_dt(dt), source, dt
    return None, "unresolved", None


# ------------------------------------------------------------------ 单条打标
def annotate(rec: dict, rule_version: str, reclassify: bool, now_iso: str, stats: dict, id_map=None) -> dict:
    """返回追加字段后的新记录（不改动入参）。"""
    out = dict(rec)  # 保留原有字段与顺序
    claim = rec.get("claim")
    claim = claim if isinstance(claim, str) else ""

    impl_version = rule_version if rule_version in RULES else FALLBACK_RULE
    if rule_version not in RULES:
        stats["unregistered_rule_versions"].add(rule_version)
    unit_class = RULES[impl_version](claim)

    pit_str, pit_source, pit_dt = resolve_pit(rec, id_map)
    if pit_str is None:
        stats["pit_failed"] += 1
    else:
        stats["pit_ok"] += 1
    stats["pit_sources"][pit_source] = stats["pit_sources"].get(pit_source, 0) + 1

    # 自带 pit 与映射表 video_key 不一致时留痕（不覆盖，只告警）
    if id_map is not None and pit_source == "self":
        by_legacy, by_unit = id_map
        uid = str(rec.get("unit_id") or "")
        legacy = rec.get("legacy_unit_id")
        vk = None
        for key in (legacy if isinstance(legacy, str) else None, uid):
            if key and (by_legacy.get(key) or by_unit.get(key)):
                vk = by_legacy.get(key) or by_unit.get(key)
                break
        map_dt = _pit_from_video_key(vk) if vk else None
        if map_dt is not None and pit_dt is not None and map_dt != pit_dt:
            stats["pit_conflicts"].append((rec.get("unit_id"), _fmt_dt(pit_dt), _fmt_dt(map_dt)))

    expire_at = None
    if unit_class == "T":
        if pit_dt is not None:
            expire_at = _fmt_dt(pit_dt + timedelta(days=EXPIRE_DAYS_T))
            stats["expire_generated"] += 1
        else:
            stats["t_expire_missing"] += 1
            stats["warn_rows"].append((rec.get("unit_id"), "T 类 pit 无法解析，expire_at 置 null"))

    if reclassify:
        prev = rec.get("unit_class")
        if isinstance(prev, str) and prev:
            out["unit_class_prev"] = prev
            stats["prev_kept"] += 1
            prev_ver = rec.get("classify_rule_version")
            if isinstance(prev_ver, str) and prev_ver and prev_ver != rule_version:
                out["classify_rule_version_prev"] = prev_ver
        out["reclassified_at"] = now_iso
        stats["reclassified"] += 1

    out["unit_class"] = unit_class
    out["classify_rule_version"] = rule_version
    out["pit"] = pit_str
    out["expire_at"] = expire_at
    out["content_hash"] = hashlib.sha256(claim.encode("utf-8")).hexdigest()[:16]
    stats["counts"][unit_class] = stats["counts"].get(unit_class, 0) + 1
    return out


# ---------------------------------------------------------------------- 输出
def _pct(n: int, total: int) -> str:
    return f"{(n / total * 100):.1f}%" if total else "0.0%"


def print_report(res: dict, total: int, samples: dict) -> None:
    counts = res["counts"]
    print("=" * 66)
    print(f"unit 内容分型 · 规则 {res['rule_version']} · {'写入模式' if res['write'] else 'DRY-RUN（未落盘）'}")
    print("=" * 66)
    print(f"输入      : {res['input']}")
    print(f"记录数    : {total}")
    print(f"输出      : {res['out'] if res['write'] else '(dry-run 不产生文件)'}")
    print("-" * 66)
    print("分型计数：")
    for cls in ("M", "T", "U"):
        n = counts.get(cls, 0)
        label = {"M": "方法论", "T": "时效复盘", "U": "未定"}[cls]
        print(f"  {cls} {label:<6} {n:>4} 条   占比 {_pct(n, total)}")
    print("-" * 66)
    print(f"pit 解析成功 : {res['pit_ok']} / {total}  ({_pct(res['pit_ok'], total)})")
    print(f"pit 解析失败 : {res['pit_failed']}")
    labels = (
        ("self", "自带 pit"),
        ("unit_id_map", "id 迁移表"),
        ("evidence_file", "evidence.file"),
        ("unit_id", "unit_id"),
        ("self(unparsed)", "自带 pit 脏值"),
        ("unresolved", "解析不出"),
    )
    print("pit 来源分布 : " + ", ".join(f"{k}={res['pit_sources'].get(k, 0)}" for k, _ in labels))
    print(f"expire_at 生成 : {res['expire_generated']} 条（T 类 pit+{EXPIRE_DAYS_T} 天）")
    if res["t_expire_missing"]:
        print(f"⚠ T 类但 pit 缺失 : {res['t_expire_missing']} 条 —— expire_at 置 null")
    if res["id_map"]:
        im = res["id_map"]
        if im["loaded"]:
            print(
                f"id-map        : {im['path']}（{im['rows']} 行，可用 legacy/unit 键映射"
                f"{'，无效行 ' + str(im['invalid']) if im['invalid'] else ''}）"
            )
        else:
            print(f"⚠ id-map 不可用 : {im['path']} —— {im['error']}（已退回原有解析顺序）")
    if res["pit_conflicts"]:
        print(f"⚠ 自带 pit 与映射表不一致 : {len(res['pit_conflicts'])} 条（保留自带值）")
    if res["reclassified"]:
        print(f"reclassify    : 重算 {res['reclassified']} 条，沿用旧 unit_class {res['prev_kept']} 条")
    if res["unregistered_rule_versions"]:
        print(
            "⚠ 未注册规则版本 "
            + ", ".join(sorted(res["unregistered_rule_versions"]))
            + f"：沿用 {FALLBACK_RULE} 判定逻辑，仅标签不同"
        )
    print("-" * 66)
    for cls in ("M", "T", "U"):
        print(f"样例 {cls}：")
        rows = samples.get(cls, [])
        if not rows:
            print("  (无)")
        for uid, head in rows:
            print(f"  - {uid} | {head}")
    print("=" * 66)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="unit 内容分型（M/T/U）打标，默认 dry-run。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--input", required=True, help="输入 jsonl")
    ap.add_argument("--out", default=None, help="输出 jsonl（新文件），默认 <输入名>_classified.jsonl")
    ap.add_argument("--write", action="store_true", help="真正落盘；缺省仅 dry-run")
    ap.add_argument("--rule-version", default=RULE_VERSION_DEFAULT, help="规则版本，默认 v1")
    ap.add_argument("--reclassify", action="store_true", help="全量重算并保留旧判定")
    ap.add_argument(
        "--id-map",
        default=None,
        help="id-migrator 的 _unit_id_map.jsonl，用于补 pit（缺省用 " + DEFAULT_ID_MAP + "）",
    )
    ap.add_argument("--json", action="store_true", help="以 JSON 输出摘要")
    ap.add_argument("--samples", type=int, default=3, help="每类样例条数，默认 3")
    args = ap.parse_args(argv)

    src = Path(args.input)
    if not src.is_file():
        print(f"[错误] 输入文件不存在：{src}", file=sys.stderr)
        return 2

    out_path = Path(args.out) if args.out else src.with_name(f"{src.stem}_classified.jsonl")
    # 安全：绝不写输入文件本身（含符号链接/大小写差异）
    same = out_path.resolve() == src.resolve()
    if not same and out_path.exists():
        try:
            same = out_path.samefile(src)
        except OSError:
            same = False
    if same:
        print(f"[错误] --out 不能指向输入文件：{out_path}", file=sys.stderr)
        return 2
    if args.write and out_path.exists():
        print(f"[错误] 输出文件已存在，拒绝覆盖：{out_path}\n       请换一个新路径。", file=sys.stderr)
        return 2

    records = []
    bad_lines = []
    with src.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                bad_lines.append((i, str(exc)))
                continue
            if isinstance(obj, dict):
                records.append(obj)
            else:
                bad_lines.append((i, "非 JSON 对象"))
    if bad_lines:
        print(f"[错误] 输入存在无法解析的行（{len(bad_lines)} 条），先修数据再跑：", file=sys.stderr)
        for i, msg in bad_lines[:5]:
            print(f"  第 {i} 行: {msg}", file=sys.stderr)
        return 2

    stats = {
        "counts": {},
        "pit_ok": 0,
        "pit_failed": 0,
        "pit_sources": {},
        "expire_generated": 0,
        "t_expire_missing": 0,
        "reclassified": 0,
        "prev_kept": 0,
        "unregistered_rule_versions": set(),
        "warn_rows": [],
        "pit_conflicts": [],
    }
    id_map = None
    id_map_meta = None
    # 缺省即读约定路径（集成后自动生效）；默认表尚未生成属正常状态，静默退回；
    # 用户显式指定的表缺失/损坏才告警，避免"还没生成"被当成配置错误反复刷屏。
    id_map_path = args.id_map if args.id_map else DEFAULT_ID_MAP
    id_map_explicit = bool(args.id_map)
    by_legacy, by_unit, id_map_meta = load_id_map(id_map_path)
    if id_map_meta["loaded"]:
        id_map = (by_legacy, by_unit)
    elif id_map_explicit:
        print(
            "⚠ [告警] id-map 不可用：{} —— {}，退回原有解析顺序".format(
                id_map_meta["path"], id_map_meta["error"]
            ),
            file=sys.stderr,
        )

    now_iso = _fmt_dt(datetime.now(TZ_CN))
    annotated = [
        annotate(r, args.rule_version, args.reclassify, now_iso, stats, id_map) for r in records
    ]

    samples = {}
    for rec in annotated:
        cls = rec["unit_class"]
        bucket = samples.setdefault(cls, [])
        if len(bucket) < args.samples:
            claim = rec.get("claim") if isinstance(rec.get("claim"), str) else ""
            bucket.append((rec.get("unit_id", "<no unit_id>"), claim[:40]))

    total = len(annotated)
    result = {
        "rule_version": args.rule_version,
        "write": bool(args.write),
        "input": str(src),
        "out": str(out_path),
        "total": total,
        "counts": {k: stats["counts"].get(k, 0) for k in ("M", "T", "U")},
        "ratio": {k: round(stats["counts"].get(k, 0) / total, 4) if total else 0.0 for k in ("M", "T", "U")},
        "pit_ok": stats["pit_ok"],
        "pit_failed": stats["pit_failed"],
        "pit_rate": round(stats["pit_ok"] / total, 4) if total else 0.0,
        "pit_sources": {
            k: stats["pit_sources"].get(k, 0)
            for k in ("self", "unit_id_map", "evidence_file", "unit_id", "self(unparsed)", "unresolved")
        },
        "expire_generated": stats["expire_generated"],
        "t_expire_missing": stats["t_expire_missing"],
        "id_map": id_map_meta,
        "pit_conflicts": [
            {"unit_id": u, "self": s, "id_map": m} for u, s, m in stats["pit_conflicts"]
        ],
        "reclassified": stats["reclassified"],
        "prev_kept": stats["prev_kept"],
        "unregistered_rule_versions": sorted(stats["unregistered_rule_versions"]),
        "warnings": [{"unit_id": uid, "msg": msg} for uid, msg in stats["warn_rows"]],
        "samples": {k: [{"unit_id": u, "claim_head": h} for u, h in v] for k, v in samples.items()},
        "written": False,
    }

    if args.write:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8", newline="\n") as f:
            for rec in annotated:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        result["written"] = True
        result["bytes"] = out_path.stat().st_size

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result, total, samples)
        if result["warnings"]:
            shown = result["warnings"][:5]
            print(f"告警（{len(result['warnings'])} 条，展示前 {len(shown)}）：")
            for w in shown:
                print(f"  - {w['unit_id']}: {w['msg']}")
            print("=" * 66)

    return 0


if __name__ == "__main__":
    sys.exit(main())
