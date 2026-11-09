#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""把 KB 知识单元的顺序型 unit_id 迁移为内容派生的稳定 ID。

旧 ID:  learn_{project_id}_{n:04d}     (n 为单次运行累加计数, 每次新增会重排)

新 ID(本脚本, 整篇视频粒度的历史数据):
        learn_{project_id}_{sha1("{project_id}|{video_key}|legacy|{seq}")[:10]}
        中间那段 `legacy` 是**粒度标记**, 见下方说明。

新 ID(段切分产物, 由 units_v2 负责, 本脚本不生成):
        位置寻址(现实现): learn_{project_id}_{sha1("{pid}|{video_key}|{seq}")[:10]}
        内容寻址(新契约): learn_{project_id}_{sha1("{pid}|{video_key}|{text_key}")[:10]}
                          text_key = sha1(claim.strip())[:10]

两个命名空间的稳定性语义**不同**, 不要混为一谈:
    legacy  = 位置锚定 + 冻结。存量是历史快照, 永不重切, seq 恒为 1 (1 视频 1 条 unit),
              所以用位置做锚点是稳定的。改动它只会让已发出的映射表作废, 收益为零。
    segment = 内容锚定 + 可变(新契约; 现实现仍是位置锚定)。段会被重切、重转写,
              位置会漂移, 只有内容才能认出"还是不是那条断言"。
    因此下游若问"这条断言是否还是我上次看到的那条", 单看 unit_id 不够,
    必须再比 content_hash / (video_key, claim)。

为什么必须有 `legacy` 标记:
    存量 unit 是"1 个视频 = 1 条 unit", seq 恒为 1; 段切分后同一视频的第 1 段
    seq 也是 1。若两者都用 `{pid}|{video_key}|{seq}`, 则"整篇 unit"与"第 1 段"
    会哈希出完全相同的 unit_id —— 这是必然碰撞, 不是 10 hex 位的概率碰撞。
    因此约定: 历史整篇 unit 走 `legacy` 命名空间, 未来段切分占用不带标记的
    规范命名空间。

video_key 形如 20260823_2151, seq 为该 unit 在其所属视频内的序号
(按 evidence.line 升序, 从 1 开始)。

默认 dry-run, 只有显式 --write 才落盘, 且只写映射表这一个文件。

用法:
    python tools/kb_corpus/migrate_unit_id.py                 # dry-run
    python tools/kb_corpus/migrate_unit_id.py --json          # dry-run + JSON
    python tools/kb_corpus/migrate_unit_id.py --write         # 写映射表
    python tools/kb_corpus/migrate_unit_id.py --rollback      # 只校验映射表
    python tools/kb_corpus/migrate_unit_id.py --map <path>    # 指定映射表路径

映射表解析约定(重要, 下游必读):
    只允许
        ^learn_(?P<pid>.+)_(?P<hash>[0-9a-f]{10})(?:_(?P<n>\d+))?$
    禁止用 rsplit('_', 1) 取末段当 seq —— 10 hex 位碰撞时本脚本会生成 `..._2` 后缀,
    裸 split 会把 `_2` 误读成序号。

本脚本不修改任何既有文件, 不删除任何内容, 不触碰数据库。
"""

import argparse
import bisect
import hashlib
import json
import os
import re
import sys
from datetime import datetime

DEFAULT_UNITS = "C:/项目/知识库/30_知识单元/视频号学习_北斗股术1688_units_effective.jsonl"
DEFAULT_SOURCE = "C:/项目/知识库/05_学习源件/视频号学习_北斗股术1688/source/视频号学习_北斗股术1688.md"
DEFAULT_MAP = "C:/项目/知识库/35_知识提炼/_unit_id_map.jsonl"

HEADER_RE = re.compile(r"^##\s+(\d{8})_(\d{4})")
HASH_LEN = 10

# 粒度标记: 历史整篇 unit 专用, 用于和段切分产物隔离命名空间
LEGACY_MARKER = "legacy"
LEGACY_GRANULARITY = "legacy_whole_video"
SEGMENT_GRANULARITY = "segment"
# seq 的作用域: 该 unit 只在"主视频"内编号, 不是覆盖范围内的段号
SEQ_SCOPE = "primary_video"


# ---------------------------------------------------------------- 基础工具

def _utf8_stdout():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


UNIT_ID_PARSE_RE = re.compile(r"^learn_(?P<pid>.+)_(?P<hash>[0-9a-f]{10})(?:_(?P<n>\d+))?$")


def _assert_safe_token(value, label):
    """哈希输入以 `|` 分隔, 任一字段含 `|` 都会造成拼接歧义, 直接抛错而非静默通过。"""
    if not isinstance(value, str):
        raise TypeError("{} 必须是 str, 实际 {}".format(label, type(value).__name__))
    if not value:
        raise ValueError("{} 不能为空".format(label))
    if "|" in value:
        raise ValueError(
            "{} 含分隔符 '|', 会破坏哈希输入的唯一性: {!r}".format(label, value))


def compute_unit_id(project_id, video_key, seq, marker=None):
    """按规范生成稳定 ID。

    marker=None  -> 段切分命名空间: sha1("{pid}|{video_key}|{seq}")
    marker="legacy" -> 历史整篇命名空间: sha1("{pid}|{video_key}|legacy|{seq}")
    """
    _assert_safe_token(project_id, "project_id")
    _assert_safe_token(video_key, "video_key")
    parts = [str(project_id), str(video_key)]
    if marker:
        parts.append(str(marker))
    parts.append(str(seq))
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return "learn_{}_{}".format(project_id, digest[:HASH_LEN])


def compute_legacy_unit_id(project_id, video_key, seq):
    return compute_unit_id(project_id, video_key, seq, marker=LEGACY_MARKER)


def compute_segment_unit_id_seq(project_id, video_key, seq):
    """段级·位置寻址公式(units_v2 现实现): sha1("{pid}|{video_key}|{seq}")。"""
    return compute_unit_id(project_id, video_key, seq, marker=None)


def compute_text_key(text):
    """内容锚点: sha1(claim.strip())[:10]。"""
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:HASH_LEN]


def compute_segment_unit_id_text(project_id, video_key, text):
    """段级·内容寻址公式(新契约): sha1("{pid}|{video_key}|{text_key}")。

    seq 退出哈希, 只作展示字段。段被重切/重转写后仍能按内容认回。
    """
    _assert_safe_token(project_id, "project_id")
    _assert_safe_token(video_key, "video_key")
    text_key = compute_text_key(text)
    digest = hashlib.sha1(
        "{}|{}|{}".format(project_id, video_key, text_key).encode("utf-8")
    ).hexdigest()
    return "learn_{}_{}".format(project_id, digest[:HASH_LEN])


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 源 md 索引

def build_video_spans(md_lines):
    """扫描源 md 的 `## YYYYMMDD_HHMM` 标题, 返回 [(起始行号, 结束行号, video_key)]。

    区间采用左闭右开 [start, next_start): 标题行本身归属该视频,
    因此落在 `##` 行上的 line 会被判给"它开启的那个视频";
    落在两个视频之间的 `---` / 空行 / 元数据行, 归属其上方视频。
    """
    heads = []
    for idx, text in enumerate(md_lines, start=1):
        m = HEADER_RE.match(text)
        if m:
            heads.append((idx, "{}_{}".format(m.group(1), m.group(2))))

    spans = []
    for i, (start, key) in enumerate(heads):
        end = heads[i + 1][0] - 1 if i + 1 < len(heads) else len(md_lines)
        spans.append((start, end, key))
    return spans


def locate_video(line_no, spans, md_line_count):
    """用 evidence.line 反查所属 video_key; 无法归属时返回 (None, 原因)。"""
    if not isinstance(line_no, int) or isinstance(line_no, bool):
        return None, "evidence.line 不是整数"
    if line_no < 1:
        return None, "evidence.line 小于 1"
    if line_no > md_line_count:
        return None, "evidence.line 超出源 md 总行数 {}".format(md_line_count)

    starts = [s[0] for s in spans]
    pos = bisect.bisect_right(starts, line_no) - 1
    if pos < 0:
        return None, "落在首个视频标题(第 {} 行)之前".format(starts[0] if starts else "?")
    start, end, key = spans[pos]
    if start <= line_no <= end:
        return key, None
    return None, "落在视频区间 [{}..{}] 之外".format(start, end)


# ---------------------------------------------------------------- 读输入

def load_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rows.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                raise SystemExit("解析失败 {}:{} -> {}".format(path, lineno, exc))
    return rows


def load_md(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read().splitlines()


# ---------------------------------------------------------------- 迁移

def build_plan(units, spans, md_line_count):
    """返回 (mappings, unresolved, stats)。mappings 已按 video_key/seq 赋值并做过碰撞检测。"""
    project_hint = {}
    staged = []       # [(unit, primary_vk, evidence_line, 原始下标, evidence_vks)]
    unresolved = []
    evidence_warnings = []

    for idx, unit in enumerate(units):
        legacy = unit.get("unit_id")
        pid = unit.get("project_id")
        evidence = unit.get("evidence") or {}
        line_no = evidence.get("line")

        if not pid:
            unresolved.append({"legacy_unit_id": legacy, "evidence_line": line_no,
                               "reason": "缺少 project_id"})
            continue
        project_hint[pid] = project_hint.get(pid, 0) + 1

        video_key, reason = locate_video(line_no, spans, md_line_count)
        if video_key is None:
            unresolved.append({"legacy_unit_id": legacy, "evidence_line": line_no,
                               "reason": reason})
            continue

        # 合并单元: evidence(primary) 只指向主视频, 被吸收的成员在 evidence_list 里。
        # 逐个解析成 video_key, 去重保序, 保证成员关系不丢。
        ev_list = unit.get("evidence_list") or []
        ev_keys = []
        for item in ev_list:
            member_line = (item or {}).get("line")
            member_key, member_reason = locate_video(member_line, spans, md_line_count)
            if member_key is None:
                evidence_warnings.append({
                    "legacy_unit_id": legacy, "evidence_line": member_line,
                    "reason": "evidence_list 成员无法归属: {}".format(member_reason)})
                continue
            if member_key not in ev_keys:
                ev_keys.append(member_key)

        if not ev_keys:
            ev_keys = [video_key]
        if ev_keys[0] != video_key:
            evidence_warnings.append({
                "legacy_unit_id": legacy, "evidence_line": line_no,
                "reason": "evidence_list 首项 {} != 主视频 {}".format(ev_keys[0], video_key)})

        staged.append((unit, video_key, line_no, idx, ev_keys))

    # 同一 video_key 内按 evidence.line 升序编号; 同行时用原始顺序兜底保证稳定
    groups = {}
    for item in staged:
        groups.setdefault(item[1], []).append(item)

    mappings = []
    collisions = []
    used = {}
    for video_key in sorted(groups):
        rows = sorted(groups[video_key], key=lambda it: (it[2], it[3]))
        for seq, (unit, _vk, line_no, _idx, ev_keys) in enumerate(rows, start=1):
            base = compute_legacy_unit_id(unit["project_id"], video_key, seq)
            new_id = base
            if new_id in used:
                # 10 hex 位碰撞: 追加 _2, _3 ... 直到唯一, 并记录告警
                n = 2
                while "{}_{}".format(base, n) in used:
                    n += 1
                new_id = "{}_{}".format(base, n)
                collisions.append({"base": base, "assigned": new_id,
                                   "conflict_with": used[base]})
            used[new_id] = unit.get("unit_id")

            claim = unit.get("claim") or ""
            mappings.append({
                "legacy_unit_id": unit.get("unit_id"),
                "unit_id": new_id,
                "project_id": unit["project_id"],
                "video_key": video_key,
                "seq": seq,
                "seq_scope": SEQ_SCOPE,
                "granularity": LEGACY_GRANULARITY,
                "is_merged": len(ev_keys) > 1,
                "evidence_video_keys": ev_keys,
                "evidence_video_count": len(ev_keys),
                "claim_chars": len(claim),
                "evidence_line": line_no,
                "migrated_at": None,
            })

    stats = {
        "units_total": len(units),
        "mapped": len(mappings),
        "unresolved": len(unresolved),
        "videos_in_source": len(spans),
        "video_keys_covered": len(groups),
        "project_ids": project_hint,
        "collisions": collisions,
        "granularity": LEGACY_GRANULARITY,
        "merged_units": sum(1 for r in mappings if r["is_merged"]),
        "evidence_warnings": evidence_warnings,
    }
    return mappings, unresolved, stats


def evidence_coverage(mappings):
    """evidence_video_keys 的并集统计, 用于校验"漏掉被合并吸收的成员视频"。"""
    union = set()
    merged = []
    for row in mappings:
        union.update(row["evidence_video_keys"])
        if row["is_merged"]:
            merged.append({
                "legacy_unit_id": row["legacy_unit_id"],
                "unit_id": row["unit_id"],
                "video_key": row["video_key"],
                "evidence_video_keys": row["evidence_video_keys"],
                "evidence_video_count": row["evidence_video_count"],
            })
    return {
        "evidence_videos_union": len(union),
        "evidence_video_union_keys": sorted(union),
        "merged_units": len(merged),
        "merged_detail": merged,
    }


MAX_SEQ_PROBE = 20


def load_segment_texts(source_path):
    """借 units_v2 的 parse_md/segment_text 还原真实段文本, 供内容寻址自检使用。

    只读调用, 不落盘。导入失败(模块不存在/接口变更)时返回 (None, 原因), 自检降级。
    """
    v2_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "units_v2.py")
    if not os.path.isfile(v2_path):
        return None, "找不到 units_v2.py: {}".format(v2_path)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_units_v2_probe", v2_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        videos = mod.parse_md(open(source_path, "r", encoding="utf-8").read())
        texts = []
        for v in videos:
            segs, _c, _n = mod.segment_text(v.get("body") or "")
            for seg in segs:
                texts.append((v.get("video_key"), seg))
        return texts, None
    except Exception as exc:                                    # noqa: BLE001
        return None, "units_v2 调用失败: {!r}".format(exc)


def check_namespace(mappings, segment_texts=None, legacy_claims=None, max_seq=MAX_SEQ_PROBE):
    """跨命名空间自检: legacy 整篇 ID 不得与段切分 ID 相同。

    段切分产物由 units_v2 生成, 公式为 sha1("{pid}|{video_key}|{seq}")(无 legacy 标记),
    每个视频 1~N 段。这里对该项目所有 (video_key, seq in 1..max_seq) 组合推演段级 ID,
    与本次生成的 legacy ID 求交集, 理论上应为空。
    """
    legacy_ids = {row["unit_id"]: row["legacy_unit_id"] for row in mappings}
    video_keys = sorted({vk for row in mappings for vk in row["evidence_video_keys"]})
    project_ids = sorted({row["project_id"] for row in mappings})

    # A. 位置寻址公式(units_v2 现实现): 枚举 seq
    seq_probed = 0
    seq_conflicts = []
    for pid in project_ids:
        for video_key in video_keys:
            for seq in range(1, max_seq + 1):
                seg_id = compute_segment_unit_id_seq(pid, video_key, seq)
                seq_probed += 1
                if seg_id in legacy_ids:
                    seq_conflicts.append({
                        "formula": "seq", "segment_unit_id": seg_id,
                        "video_key": video_key, "seq": seq,
                        "legacy_unit_id": legacy_ids[seg_id],
                    })

    # B. 内容寻址公式(新契约): 用真实段文本 + legacy 自身 claim 双路推演
    text_probed = 0
    text_conflicts = []
    candidates = []
    if segment_texts:
        candidates.extend(segment_texts)
    # legacy claim 也作为候选: 覆盖"某视频只有 1 段且该段就是全文"的情形
    if legacy_claims:
        candidates.extend(legacy_claims)

    seen_pairs = set()
    for video_key, text in candidates:
        if not text:
            continue
        for pid in project_ids:
            key = (pid, video_key, compute_text_key(text))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            seg_id = compute_segment_unit_id_text(pid, video_key, text)
            text_probed += 1
            if seg_id in legacy_ids:
                text_conflicts.append({
                    "formula": "text", "segment_unit_id": seg_id,
                    "video_key": video_key, "text_key": key[2],
                    "legacy_unit_id": legacy_ids[seg_id],
                })

    return {
        "probed_segment_ids": seq_probed,
        "probe_seq_range": "1..{}".format(max_seq),
        "probed_seq_ids": seq_probed,
        "probed_text_ids": text_probed,
        "segment_text_source": "units_v2 真实段文本" if segment_texts else "仅 legacy claim",
        "namespace_conflicts": seq_conflicts + text_conflicts,
        "seq_formula_conflicts": len(seq_conflicts),
        "text_formula_conflicts": len(text_conflicts),
    }


def uniqueness_report(mappings):
    seen = {}
    dup_new = []
    dup_legacy = []
    for row in mappings:
        seen.setdefault(row["unit_id"], []).append(row["legacy_unit_id"])
    for new_id, legacies in seen.items():
        if len(legacies) > 1:
            dup_new.append({"unit_id": new_id, "legacy": legacies})

    seen_legacy = {}
    for row in mappings:
        seen_legacy.setdefault(row["legacy_unit_id"], []).append(row["unit_id"])
    for legacy, news in seen_legacy.items():
        if len(news) > 1:
            dup_legacy.append({"legacy_unit_id": legacy, "unit_id": news})

    return {
        "new_id_unique": len(seen),
        "new_id_duplicates": len(dup_new),
        "new_id_dup_detail": dup_new[:5],
        "legacy_duplicates": len(dup_legacy),
        "legacy_dup_detail": dup_legacy[:5],
    }


# ---------------------------------------------------------------- 输出

def print_dry_run(mappings, unresolved, stats, uniq, samples, ns):
    print("== 迁移预演 (dry-run, 未写入任何文件) ==")
    print("单元总数      : {}".format(stats["units_total"]))
    print("成功映射      : {}".format(stats["mapped"]))
    print("未识别        : {}".format(stats["unresolved"]))
    print("源视频段数    : {}".format(stats["videos_in_source"]))
    print("主视频数(去重): {}".format(stats["video_keys_covered"]))
    print("粒度          : {} (哈希输入含 legacy 标记)".format(stats["granularity"]))
    print("project_id    : {}".format(", ".join(
        "{}={}".format(k, v) for k, v in stats["project_ids"].items())))
    print()
    print("-- 新 ID 唯一性 --")
    print("唯一新 ID 数  : {}".format(uniq["new_id_unique"]))
    print("新 ID 重复数  : {} (应为 0)".format(uniq["new_id_duplicates"]))
    print("legacy 重复数 : {} (应为 0)".format(uniq["legacy_duplicates"]))
    if stats["collisions"]:
        print("!! 哈希碰撞   : {} 处, 已追加后缀".format(len(stats["collisions"])))
        for c in stats["collisions"]:
            print("   {} -> {} (冲突 {})".format(c["base"], c["assigned"], c["conflict_with"]))
    else:
        print("哈希碰撞      : 0 (无 10 hex 位碰撞)")
    print()
    print("-- 证据覆盖 (evidence + evidence_list) --")
    print("覆盖视频段数  : {} / {} 源视频".format(
        stats["evidence_videos_union"], stats["videos_in_source"]))
    print("无证据视频数  : {} (应为 0)".format(stats["videos_without_evidence_count"]))
    if stats["videos_without_evidence"]:
        print("   !! {}".format(", ".join(stats["videos_without_evidence"][:10])))
    print("合并单元数    : {}".format(stats["merged_units"]))
    for m in stats["merged_detail"]:
        print("   {} ({}) count={} -> {}".format(
            m["legacy_unit_id"], m["unit_id"], m["evidence_video_count"],
            " / ".join(m["evidence_video_keys"])))
    print()
    print("-- 跨命名空间自检 (legacy 整篇 vs 段切分) --")
    print("A 位置寻址公式: 推演 {} 个 (seq {} x {} 证据视频), 冲突 {}".format(
        ns["probed_seq_ids"], ns["probe_seq_range"],
        stats["evidence_videos_union"], ns["seq_formula_conflicts"]))
    print("B 内容寻址公式: 推演 {} 个, 冲突 {}   [候选源: {}]".format(
        ns["probed_text_ids"], ns["text_formula_conflicts"], ns["segment_text_source"]))
    if ns.get("segment_text_note"):
        print("   注意: {} (已降级为仅用 legacy claim 推演)".format(ns["segment_text_note"]))
    for c in ns["namespace_conflicts"][:5]:
        print("   !! [{}] {} (video={}) 撞上 {}".format(
            c.get("formula"), c["segment_unit_id"], c["video_key"],
            c["legacy_unit_id"]))
    print()
    if unresolved:
        print("-- 未识别明细 --")
        for row in unresolved:
            print("   {} line={} :: {}".format(
                row["legacy_unit_id"], row["evidence_line"], row["reason"]))
        print()
    print("-- 前 5 条映射样例 --")
    for row in samples:
        print("   {} -> {}  [video={} seq={} line={} chars={}]".format(
            row["legacy_unit_id"], row["unit_id"], row["video_key"],
            row["seq"], row["evidence_line"], row["claim_chars"]))
    print()
    print("注: 需要落盘请加 --write (只写映射表, 不改单元文件)")


def cmd_rollback(map_path, source_path=None):
    if not os.path.isfile(map_path):
        print("[rollback] 映射表不存在: {}".format(map_path))
        return 1

    rows = load_jsonl(map_path)
    forward = {}
    reverse = {}
    problems = []
    suffix_hits = 0
    suffixed = []
    missing_granularity = 0
    gran_counter = {}
    missing_merge_fields = 0
    union = set()
    merged_seen = []

    for i, row in enumerate(rows, start=1):
        legacy = row.get("legacy_unit_id")
        new_id = row.get("unit_id")
        for field in ("legacy_unit_id", "unit_id", "project_id", "video_key", "seq"):
            if row.get(field) in (None, ""):
                problems.append("第 {} 行缺少字段 {}".format(i, field))
        if not legacy or not new_id:
            continue
        if legacy in forward and forward[legacy] != new_id:
            problems.append("legacy 重复且指向不同新 ID: {} -> {} / {}".format(
                legacy, forward[legacy], new_id))
        forward[legacy] = new_id
        if new_id in reverse and reverse[new_id] != legacy:
            problems.append("新 ID 被多个 legacy 占用: {} <- {} / {}".format(
                new_id, reverse[new_id], legacy))
        reverse[new_id] = legacy

        gran = row.get("granularity")
        if gran is None:
            missing_granularity += 1
            gran = LEGACY_GRANULARITY
        elif gran not in (LEGACY_GRANULARITY, SEGMENT_GRANULARITY):
            problems.append("第 {} 行 granularity 非法: {}".format(i, gran))
        gran_counter[gran] = gran_counter.get(gran, 0) + 1

        # 合并单元台账校验: 成员关系是唯一记录, 缺字段必须报错而不是放过
        ev_keys = row.get("evidence_video_keys")
        if ev_keys is None:
            missing_merge_fields += 1
            problems.append("第 {} 行缺少 evidence_video_keys (合并关系不可考)".format(i))
            union.add(row["video_key"])
        else:
            if not isinstance(ev_keys, list) or not ev_keys:
                problems.append("第 {} 行 evidence_video_keys 非法: {!r}".format(i, ev_keys))
                ev_keys = [row["video_key"]]
            if ev_keys[0] != row["video_key"]:
                problems.append("第 {} 行 evidence_video_keys 首项 {} != 主视频 {}".format(
                    i, ev_keys[0], row["video_key"]))
            cnt = row.get("evidence_video_count")
            if cnt != len(ev_keys):
                problems.append("第 {} 行 evidence_video_count={} 与 keys 长度 {} 不符".format(
                    i, cnt, len(ev_keys)))
            is_merged = row.get("is_merged")
            if is_merged is not (len(ev_keys) > 1):
                problems.append("第 {} 行 is_merged={} 与 count={} 不符".format(
                    i, is_merged, len(ev_keys)))
            if row.get("seq_scope") != SEQ_SCOPE:
                problems.append("第 {} 行 seq_scope={!r} 应为 {!r}".format(
                    i, row.get("seq_scope"), SEQ_SCOPE))
            union.update(ev_keys)
            if len(ev_keys) > 1:
                merged_seen.append((row["legacy_unit_id"], len(ev_keys)))

        # 粒度决定哈希输入: legacy 必须带 legacy 标记, segment 不带
        if gran == LEGACY_GRANULARITY:
            base = compute_legacy_unit_id(row["project_id"], row["video_key"], row["seq"])
        elif row.get("text_key"):
            # 段级·内容寻址(新契约): 台账存了 text_key 才能重算
            digest = hashlib.sha1("{}|{}|{}".format(
                row["project_id"], row["video_key"], row["text_key"]).encode("utf-8")).hexdigest()
            base = "learn_{}_{}".format(row["project_id"], digest[:HASH_LEN])
        else:
            base = compute_segment_unit_id_seq(row["project_id"], row["video_key"], row["seq"])
        if new_id != base:
            if new_id.startswith(base + "_"):
                suffix_hits += 1
                suffixed.append((new_id, base))
            else:
                problems.append("按字段重算不一致: {} != {} (granularity={})".format(
                    new_id, base, gran))

    closed = 0
    for legacy, new_id in forward.items():
        if reverse.get(new_id) == legacy:
            closed += 1
        else:
            problems.append("闭合失败: {} -> {} -> {}".format(
                legacy, new_id, reverse.get(new_id)))

    # 碰撞后缀必须确有对应: 只有当 base 本身也被占用时, base_N 才是合法的碰撞产物
    for new_id, base in suffixed:
        if base not in reverse:
            problems.append("可疑碰撞后缀: {} 的 base {} 并未在表中出现".format(new_id, base))

    # 与源 md 的视频段集合对账: 这是唯一能发现"合并关系被抹平"的外部锚点
    source_check = None
    if source_path and os.path.isfile(source_path):
        spans = build_video_spans(load_md(source_path))
        expected = {sp[2] for sp in spans}
        missing = sorted(expected - union)
        extra = sorted(union - expected)
        source_check = {"source_videos": len(expected), "missing": missing, "extra": extra}
        if missing:
            problems.append("证据并集缺少源视频 {} 段: {}".format(
                len(missing), ", ".join(missing[:10])))
        if extra:
            problems.append("证据并集含源 md 中不存在的 {} 段: {}".format(
                len(extra), ", ".join(extra[:10])))

    print("== 映射表闭环校验 (只校验, 未写任何文件) ==")
    print("映射表      : {}".format(map_path))
    print("条目数      : {}".format(len(rows)))
    print("legacy 键数 : {} / 新 ID 键数 : {}".format(len(forward), len(reverse)))
    print("闭合条数    : {} / {} ".format(closed, len(forward)))
    print("碰撞后缀数  : {}".format(suffix_hits))
    print("粒度分布    : {}".format(", ".join(
        "{}={}".format(k, v) for k, v in sorted(gran_counter.items()))))
    print("证据视频并集: {}".format(len(union)))
    if source_check:
        print("源视频段数  : {} (并集与源集合对账: {})".format(
            source_check["source_videos"],
            "一致" if not source_check["missing"] and not source_check["extra"] else "不一致"))
    else:
        print("源视频段数  : 未提供/找不到源 md, 跳过并集对账")
    print("合并单元数  : {}".format(len(merged_seen)))
    for legacy, cnt in merged_seen:
        print("   {} count={}".format(legacy, cnt))
    if missing_merge_fields:
        print("缺失字段    : {} 条没有 evidence_video_keys (旧版台账, 合并关系不可考)".format(
            missing_merge_fields))
    if missing_granularity:
        print("提示        : {} 条缺少 granularity, 已按 {} 处理".format(
            missing_granularity, LEGACY_GRANULARITY))
    if problems:
        print("结果        : 失败, {} 处问题".format(len(problems)))
        for p in problems[:20]:
            print("   - {}".format(p))
        return 1
    print("结果        : 通过 (legacy -> new -> legacy 全部闭合, 重算哈希一致)")
    return 0


# ---------------------------------------------------------------- 入口

def main(argv=None):
    _utf8_stdout()
    ap = argparse.ArgumentParser(
        description="将顺序型 unit_id 迁移为内容派生稳定 ID (默认 dry-run)")
    ap.add_argument("--write", action="store_true", help="写映射表文件 (仅此一个文件)")
    ap.add_argument("--rollback", action="store_true", help="只校验映射表, 不写任何文件")
    ap.add_argument("--map", default=DEFAULT_MAP, help="映射表路径")
    ap.add_argument("--json", action="store_true", dest="as_json", help="输出 JSON")
    ap.add_argument("--units", default=DEFAULT_UNITS, help="存量单元 jsonl (只读)")
    ap.add_argument("--source", default=DEFAULT_SOURCE, help="源 md (只读)")
    args = ap.parse_args(argv)

    if args.rollback:
        return cmd_rollback(args.map, args.source)

    for path, label in ((args.units, "单元文件"), (args.source, "源 md")):
        if not os.path.isfile(path):
            print("[error] {}不存在: {}".format(label, path))
            return 2

    md_lines = load_md(args.source)
    spans = build_video_spans(md_lines)
    if not spans:
        print("[error] 源 md 中未找到 `## YYYYMMDD_HHMM` 标题")
        return 2

    units = load_jsonl(args.units)
    mappings, unresolved, stats = build_plan(units, spans, len(md_lines))
    uniq = uniqueness_report(mappings)
    seg_texts, seg_note = load_segment_texts(args.source)
    claim_pairs = []
    for u in units:
        line_no = (u.get("evidence") or {}).get("line")
        vk, _r = locate_video(line_no, spans, len(md_lines))
        if vk and u.get("claim"):
            claim_pairs.append((vk, u["claim"]))
    ns = check_namespace(mappings, segment_texts=seg_texts, legacy_claims=claim_pairs)
    ns["segment_text_note"] = seg_note
    cov = evidence_coverage(mappings)
    all_source_keys = [sp[2] for sp in spans]
    cov["videos_without_evidence"] = [k for k in all_source_keys
                                      if k not in set(cov["evidence_video_union_keys"])]
    cov["videos_without_evidence_count"] = len(cov["videos_without_evidence"])
    stats.update(uniq)
    stats.update(ns)
    stats.update(cov)
    samples = mappings[:5]

    if args.write:
        stamp = now_iso()
        for row in mappings:
            row["migrated_at"] = stamp
        parent = os.path.dirname(os.path.abspath(args.map))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with open(args.map, "w", encoding="utf-8", newline="\n") as fh:
            for row in mappings:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    if args.as_json:
        payload = {
            "mode": "write" if args.write else "dry-run",
            "units": args.units,
            "source": args.source,
            "map_path": args.map if args.write else None,
            "stats": stats,
            "samples": samples,
            "unresolved": unresolved,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_dry_run(mappings, unresolved, stats, uniq, samples, ns)
        if args.write:
            print()
            print("== 已写出映射表 ==")
            print("路径: {}".format(args.map))
            print("条数: {}".format(len(mappings)))
            print("migrated_at: {}".format(now_iso()))
            print("提示: 可用 --rollback 做闭环校验")
        elif unresolved:
            print("提示: 存在 {} 条未识别单元, --write 时它们不会出现在映射表中".format(
                len(unresolved)))

    if stats["mapped"] == 0:
        print("[error] 没有成功映射的单元, 拒绝执行")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
