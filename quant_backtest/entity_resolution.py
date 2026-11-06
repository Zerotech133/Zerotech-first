# -*- coding: utf-8 -*-
"""P2 实体解析（Entity Resolution）—— Person / Seat 跨表归并.

纯新增模块，不改生产代码，不写 Neo4j（回滚 = 删输出文件）。

三类实体:
  SEAT    营业部/机构席位   来源: ts_top_inst.exalter, ts_block_trade.buyer/seller
  LEGAL   法人股东          来源: top10_holders, top10_floatholders, stk_holdertrade, pledge_detail
  PERSON  自然人(股东/高管)  来源: 同上 + stk_managers —— 只规范化, 不跨名归并(重名风险)

方法(方法论 P2 承诺): 规则 + 阻断键 + 相似度, 置信度分级, 低置信不折叠.
  SEAT  阻断键 = 券商主体标准化名; 组内编辑距离 <= 2 且分支类型相同才折叠.
        分支号差异("第N营业部")永不自动折叠 —— 沿用 intel_seat_shadow 的 D1 纪律.
  LEGAL 阻断键 = 规范化名去括号去空格; QFII(纯ASCII)大写无空格比对.
  PERSON 只输出 canonical = 规范化名, confidence=0.5, method=norm_only.

输出: data/entity_map.parquet  (long format, 带 confidence/method)
指标: Seat 归并率 / 大宗席位覆盖 / 法人归并率  → build_report()
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------- #
# 规范化
# ---------------------------------------------------------------- #

_FULLWIDTH = str.maketrans({chr(c): chr(c - 0xFEE0) for c in range(0xFF01, 0xFF5F)})
_PAREN_OPEN = "([{（｛〔【"
_PAREN_CLOSE = ")]}）｝〕】"

_LEGAL_SUFFIX = re.compile(
    r"(股份有限公司|有限责任公司|有限公司|公司|集团有限公司$)"
)
# 判定法人实体的强词表(任一命中即法人)
_LEGAL_HINTS = re.compile(
    r"(有限公司|公司|证券|基金|银行|保险|信托|资管|资产管理|投资|控股|集团|"
    r"社保|汇金|证金|财政|国资|大学|研究所|事务所|中心|QFII|理财)"
)

# 特殊席位: 不是营业部, 是机构类别 —— 单独成实体, 不参与营业部归并
_SPECIAL_SEATS = {
    "机构专用", "沪股通专用", "深股通专用", "股转系统专用",
    "机构专用买入", "QFII专用",
}


def normalize_name(s: str) -> str:
    """通用规范化: 全角→半角, 统一括号, 去首尾/连续空白."""
    if s is None:
        return ""
    s = str(s).strip()
    s = s.translate(_FULLWIDTH)
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", "", s)
    return s


def _strip_parens(s: str) -> Tuple[str, str]:
    """返回 (去括号主体, 括号内备注). 括号统一成 ( )。"""
    s = re.sub(r"[{（｛〔【]", "(", s)
    s = re.sub(r"[}）｝〕】]", ")", s)
    m = re.match(r"^(.*?)\((.*?)\)(.*)$", s)
    if m:
        return m.group(1) + m.group(3), m.group(2)
    return s, ""


# ---------------------------------------------------------------- #
# Seat: 券商主体解析
# ---------------------------------------------------------------- #

# 知名券商头词(最长匹配优先), 避免主体切分歧义
_BROKER_HEADS = [
    "中信建投", "中信证券", "中国银河", "中国国际金融", "中金财富", "中泰证券",
    "中航证券", "中银国际", "中邮证券", "中原证券", "中天证券", "中山证券",
    "招商证券", "浙商证券", "长城国瑞", "长城证券", "长江证券", "粤开证券",
    "越秀资本", "兴业证券", "信达证券", "湘财证券", "西南证券", "西部证券",
    "首创证券", "首创证券", "申港证券", "申万宏源", "山西证券", "山证国际",
    "上海证券", "山西证券", "平安证券", "南京证券", "南京证券", "民生证券",
    "美锦证券", "联储证券", "华鑫证券", "华兴证券", "华西证券", "华福证券",
    "华创证券", "华宝证券", "华安证券", "华林证券", "华金证券", "华龙证券",
    "华泰联合", "华泰证券", "华菁证券", "恒泰长财", "恒泰证券", "恒投证券",
    "海通证券", "国新证券", "国信证券", "国海水证", "国融证券", "国金证券",
    "国海证券", "国都证券", "国盛证券", "国联证券", "国开证券", "国元证券",
    "国投证券", "国新证券", "广发证券", "光大证券", "高盛高华", "东兴证券",
    "东方财富", "东方证券", "东海证券", "东吴证券", "东北证券", "德邦证券",
    "大同证券", "大和证券", "财信证券", "财达证券", "财通证券", "渤海证券",
    "伯克希尔", "北京高华", "北京证券", "百家湖金", "爱建证券",
]
_BRANCH_WORD = re.compile(r"(总部|分公司|营业部|证券营业部|证券公司)")
_BRANCH_NO = re.compile(r"(?<=第)[0-9一二三四五六七八九十百]+(?=营业部)")


def parse_seat(name: str) -> Dict[str, str]:
    """席位名 → 结构化 {broker, branch, branch_type, note}."""
    base, note = _strip_parens(normalize_name(name))
    broker = ""
    for h in sorted(set(_BROKER_HEADS), key=len, reverse=True):
        if base.startswith(h):
            broker = h
            break
    if not broker:
        # 未命中头词表: 取到第一个 分公司/营业部/总部/公司 为止
        m = re.match(r"^(.*?(?:证券|期货|基金|投资|资管)(?:股份)?有限公司?)", base)
        broker = m.group(1) if m else re.split(r"(?:分公司|营业部|总部)", base)[0]
    branch_type = "other"
    if "总部" in base:
        branch_type = "hq"
    elif "分公司" in base:
        branch_type = "branch"
    elif "营业部" in base:
        branch_type = "branch_office"
    return {"broker": broker, "branch": base[len(broker):], "branch_type": branch_type,
            "note": note}


def seat_edit_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# 公司后缀变体(折叠时视为等价)
_SUFFIX_VARIANTS = [
    ("股份有限公司", ""), ("有限责任公司", ""), ("有限公司", ""),
]
_BRANCH_VARIANTS = [("证券营业部", "营业部"), ("营业部", "营业部")]


def seat_variant_key(name: str) -> str:
    """安全变体键: 去括号备注 + 公司后缀归一 + 分支词归一.
    两名 variant_key 相等 → 结构等价, 可安全折叠."""
    base, _ = _strip_parens(normalize_name(name))
    for suf, rep in _SUFFIX_VARIANTS:
        base = base.replace(suf, rep)
    base = base.replace("证券营业部", "营业部")
    return base


# 短名编辑距离才可信: 长名(>12字)的路名差异会被高 ratio 掩盖(误折叠国泰君安"九江路"/"佳杰路"教训)
_FUZZY_MAX_LEN = 12
_FUZZY_MIN_SIM = 0.85


# ---------------------------------------------------------------- #
# Person / LEGAL
# ---------------------------------------------------------------- #

def is_ascii_name(s: str) -> bool:
    return bool(s) and all(ord(c) < 128 for c in s)


def classify_holder(name: str) -> str:
    """holder_name → legal | person | fund_product | unknown."""
    s = normalize_name(name)
    if not s:
        return "unknown"
    if is_ascii_name(s):
        return "legal"  # QFII / 外资机构
    if _LEGAL_HINTS.search(s):
        # "银行-基金产品" 结构
        if re.search(r"(银行|证券|保险).{0,12}(-|—|–).{0,60}(基金|资管|理财|计划|集合)", s):
            return "fund_product"
        return "legal"
    if re.search(r"(基金|资管|计划|信托|理财|私募)", s):
        return "fund_product"
    # 纯中文 2-5 字且无法人词 → 自然人
    if 1 <= len(s) <= 6 and re.fullmatch(r"[\u4e00-\u9fff·]+", s):
        return "person"
    return "legal"  # 默认按法人处理(保守: 至少给规范化)


# ---------------------------------------------------------------- #
# 归并核心
# ---------------------------------------------------------------- #

def _cid(prefix: str, name: str) -> str:
    h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}.{h}"


def resolve_seats(raw_names: List[str],
                  fuzzy_threshold: float = 0.90,
                  max_edit: int = 3) -> Tuple[Dict[str, Tuple[str, str, float]], List[dict]]:
    """席位归并. 返回 {raw → (canonical_id, canonical_name, confidence)}, 及审计记录.

    纪律:
      - 不同 broker 永不折叠
      - 分支号差异不自动折叠(只记 branch_candidate, 置信度 0.55)
      - 编辑距离折叠阈值: 相似度 >= fuzzy_threshold 且 branch_type 相同
    """
    mapping: Dict[str, Tuple[str, str, float]] = {}
    audit: List[dict] = []
    groups: Dict[str, List[str]] = defaultdict(list)
    for raw in set(raw_names):
        n = normalize_name(raw)
        if not n:
            continue
        if n in _SPECIAL_SEATS:
            mapping[raw] = (_cid("SEAT", n), n, 1.0)
            continue
        groups[parse_seat(n)["broker"]].append(raw)

    merged: Dict[str, str] = {}  # 组内代表: norm_name → canonical_name

    for broker, members in groups.items():
        norms = {m: normalize_name(m) for m in members}
        uniq = sorted(set(norms.values()))
        # union-find on uniq
        parent = {u: u for u in uniq}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        pairs = []
        # 规则1(唯一折叠规则): variant_key 相等 → 结构等价变体折叠.
        # 教训(两轮实测): 编辑距离模糊折叠不可靠 —— 长名路名差异(上海九江路/佳杰路
        # ratio 0.91)与短地区词差异(北京/湖北分公司 ratio 0.91)都会误折叠,
        # 两种口径下 ratio 阈值都失效, 故只保留确定性结构变体.
        var_groups: Dict[str, List[str]] = defaultdict(list)
        for u in uniq:
            var_groups[seat_variant_key(u)].append(u)
        for key, members in var_groups.items():
            if len(members) > 1:
                for other in members[1:]:
                    union(members[0], other)
                    pairs.append((members[0], other, 0.95))
        # 规则2: 分支号差异 → 只记候选, 永不自动折叠(D1 纪律)
        seen_pair = set()
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                a, b = uniq[i], uniq[j]
                if find(a) == find(b):
                    continue
                if _BRANCH_NO.search(a) and _BRANCH_NO.search(b):
                    ka, kb = _BRANCH_NO.sub("第*", a), _BRANCH_NO.sub("第*", b)
                    if ka == kb and (ka, kb) not in seen_pair:
                        seen_pair.add((ka, kb))
                        audit.append({"a": a, "b": b, "sim": None,
                                      "verdict": "branch_candidate"})

        root_rep: Dict[str, str] = {}
        for u in uniq:
            r = find(u)
            if r not in root_rep:
                root_rep[r] = u  # 排序后首个 = 最短/最规范形态
            else:
                # 代表取更短者
                if len(u) < len(root_rep[r]):
                    root_rep[r] = u
        for u in uniq:
            merged[u] = root_rep[find(u)]

        for a, b, sim in pairs:
            audit.append({"a": a, "b": b, "sim": round(sim, 4), "verdict": "merged"})

    for raw in set(raw_names):
        n = normalize_name(raw)
        if not n:
            continue
        if n in _SPECIAL_SEATS:
            continue  # 已处理
        rep = merged.get(n, n)
        mapping[raw] = (_cid("SEAT", rep), rep, 1.0 if rep == n else 0.9)
    return mapping, audit


def resolve_legal(raw_names: List[str]) -> Tuple[Dict[str, Tuple[str, str, float]], List[dict]]:
    """法人股东归并: 规范化 + 去括号 + 无空格比对, 阈值宽松(0.88)."""
    mapping: Dict[str, Tuple[str, str, float]] = {}
    audit: List[dict] = []
    by_key: Dict[str, List[str]] = defaultdict(list)
    for raw in set(raw_names):
        n = normalize_name(raw)
        if not n:
            continue
        key = _strip_parens(n)[0].upper() if is_ascii_name(n) else _strip_parens(n)[0]
        by_key[key].append(raw)

    # 二级: 同 key 内 ASCII 无空格折叠 / 中文编辑距离
    canon: Dict[str, str] = {}
    for key, members in by_key.items():
        uniq = sorted(set(normalize_name(m) for m in members))
        parent = {u: u for u in uniq}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                a, b = uniq[i], uniq[j]
                sa, sb = a.replace(" ", "").upper(), b.replace(" ", "").upper()
                if sa == sb:
                    union(a, b)
                    audit.append({"a": a, "b": b, "sim": 1.0, "verdict": "ascii_space"})
                    continue
                # 长名(>20字, 如基金产品全称)只允许精确/ascii_space 折叠 ——
                # 长字符串编辑距离比率不可靠, "中证500"/"中证1000"差 2 字 ratio 仍 >0.9
                if len(a) > 20 or len(b) > 20:
                    continue
                if abs(len(a) - len(b)) > 2:
                    continue
                sim = seat_edit_sim(a, b)
                if sim >= 0.88:
                    union(a, b)
                    audit.append({"a": a, "b": b, "sim": round(sim, 4), "verdict": "merged"})

        root_rep: Dict[str, str] = {}
        for u in uniq:
            r = find(u)
            if r not in root_rep or len(u) < len(root_rep[r]):
                root_rep[r] = u
        for u in uniq:
            canon[u] = root_rep[find(u)]

    for raw in set(raw_names):
        n = normalize_name(raw)
        if not n:
            continue
        rep = canon.get(n, n)
        mapping[raw] = (_cid("LEGAL", rep), rep, 1.0 if rep == n else 0.85)
    return mapping, audit


def resolve_person(raw_names: List[str]) -> Dict[str, Tuple[str, str, float]]:
    """自然人只规范化, 不跨名归并(重名风险, confidence 0.5)."""
    mapping = {}
    for raw in set(raw_names):
        n = normalize_name(raw)
        if not n:
            continue
        mapping[raw] = (_cid("PERSON", n), n, 0.5)
    return mapping


# ---------------------------------------------------------------- #
# 构建入口
# ---------------------------------------------------------------- #

def build(repo_root: Optional[str] = None) -> "pd.DataFrame":  # noqa: F821
    """全量构建 entity_map.parquet + 返回明细 DF."""
    import os
    import sys
    root = repo_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "quant_backtest"))
    from warehouse_reader import WarehouseReader

    r = WarehouseReader()
    rows: List[dict] = []

    # ---- SEAT ----
    seat_src: Dict[str, set] = {"ts_top_inst": set(), "ts_block_trade": set()}
    df = r.read("ts_top_inst", columns=["exalter"])
    seat_src["ts_top_inst"] |= set(df["exalter"].dropna().astype(str))
    df = r.read("ts_block_trade", columns=["buyer", "seller"])
    seat_src["ts_block_trade"] |= set(df["buyer"].dropna().astype(str))
    seat_src["ts_block_trade"] |= set(df["seller"].dropna().astype(str))
    all_seats: set = set().union(*seat_src.values())
    seat_map, seat_audit = resolve_seats(sorted(all_seats))
    for table, names in seat_src.items():
        for raw in names:
            cid, cname, conf = seat_map.get(raw, (_cid("SEAT", normalize_name(raw)),
                                                  normalize_name(raw), 0.5))
            rows.append(dict(entity_type="SEAT", source_table=table, raw_name=raw,
                             canonical_id=cid, canonical_name=cname,
                             confidence=conf, method="seat_rule"))
    print(f"  SEAT  raw={len(all_seats)} canonical={len(set(v[0] for v in seat_map.values()))}")

    # ---- LEGAL / PERSON ----
    holder_src = ["top10_holders", "top10_floatholders", "ts_stk_holdertrade",
                  "ts_pledge_detail" if "ts_pledge_detail" in r.list_assets()
                  else "pledge_detail"]
    legal_names: set = set()
    person_names: set = set()
    per_table: Dict[str, set] = {}
    for t in holder_src:
        if t not in r.list_assets():
            print(f"  [skip] {t} 不在注册表")
            continue
        df = r.read(t)
        col = "holder_name" if "holder_name" in df.columns else "holder"
        names = set(df[col].dropna().astype(str))
        per_table[t] = names
        for nm in names:
            if classify_holder(nm) == "person":
                person_names.add(nm)
            else:
                legal_names.add(nm)
    legal_map, legal_audit = resolve_legal(sorted(legal_names))
    person_map = resolve_person(sorted(person_names))
    for t, names in per_table.items():
        for raw in names:
            if raw in person_map:
                cid, cname, conf = person_map[raw]
                method = "person_norm"
            else:
                cid, cname, conf = legal_map[raw]
                method = "legal_rule"
            rows.append(dict(entity_type=("PERSON" if raw in person_map else "LEGAL"),
                             source_table=t, raw_name=raw,
                             canonical_id=cid, canonical_name=cname,
                             confidence=conf, method=method))
    print(f"  LEGAL raw={len(legal_names)} "
          f"canonical={len(set(v[0] for v in legal_map.values()))}")
    print(f"  PERSON(仅规范化) raw={len(person_names)}")

    import pandas as pd
    out = pd.DataFrame(rows)
    path = os.path.join(root, "quant_backtest", "data", "entity_map.parquet")
    out.to_parquet(path, index=False)
    print(f"  saved -> {path}")
    # 审计记录也落盘(供人工复核抽样)
    audit_path = os.path.join(root, "quant_backtest", "reports", "p2_merge_audit.json")
    pd.DataFrame(seat_audit + legal_audit).to_json(
        audit_path, orient="records", force_ascii=False, indent=1)
    print(f"  audit -> {audit_path}")
    return out


def build_report(df: "pd.DataFrame") -> dict:  # noqa: F821
    """验收指标: 分两级(营业部级 / 券商主体级)."""
    seat = df[df.entity_type == "SEAT"]
    raw_n = seat.raw_name.nunique()
    can_n = seat.canonical_id.nunique()
    brokers = seat.canonical_name.map(lambda n: parse_seat(n)["broker"])
    ti = seat[seat.source_table == "ts_top_inst"]
    blk = seat[seat.source_table == "ts_block_trade"]
    ti_can = set(ti.canonical_id)
    ti_broker = set(ti.canonical_name.map(lambda n: parse_seat(n)["broker"]))
    blk_cover_branch = blk.canonical_id.isin(ti_can).mean()
    blk_broker = blk.canonical_name.map(lambda n: parse_seat(n)["broker"])
    blk_cover_broker = blk_broker.isin(ti_broker).mean()
    legal = df[df.entity_type == "LEGAL"]
    lraw = legal.raw_name.nunique()
    lcan = legal.canonical_id.nunique()
    return {
        "seat_raw": int(raw_n), "seat_canonical": int(can_n),
        "seat_merge_rate_branch": round(1 - can_n / raw_n, 4),
        "seat_broker_count": int(brokers.nunique()),
        "seat_merge_rate_broker": round(1 - brokers.nunique() / raw_n, 4),
        "block_seat_coverage_branch": round(float(blk_cover_branch), 4),
        "block_seat_coverage_broker": round(float(blk_cover_broker), 4),
        "legal_raw": int(lraw), "legal_canonical": int(lcan),
        "legal_merge_rate": round(1 - lcan / lraw, 4) if lraw else None,
        "person_norm_only": int(df[df.entity_type == "PERSON"].raw_name.nunique()),
    }


if __name__ == "__main__":
    df = build()
    print(build_report(df))
