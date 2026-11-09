"""存量实体别名回填（P1-B）：从 ``osint_knowledge_unit.entities`` 提取
**确定性可归一**的别名，写入 ``osint_entity_alias``。

## 为什么只做确定性归一

别名归一会直接影响事件层的独立来源数（热度分母）：错误合并会把两个实体
算成一个，热度虚高且不可逆。因此本脚本的可写库规则**只有四条**，全部是
字符串层面的等价变换，不涉及任何相似度判断：

1. **全角/半角、大小写、标点差异**：``ＣＡＴＬ`` / ``catl`` / ``C.A.T.L``
   归一后相等（NFKC + 与算子同口径的 ``normalize_text``）。
2. **法定后缀剥离后完全相等**：``有限公司`` / ``股份有限公司`` / ``inc.`` /
   ``ltd`` / ``corp.`` / ``gmbh`` 等后缀去掉后逐字相同，才视为同一实体。
3. **括号内英文缩写与中文全称配对**：``宁德时代新能源科技股份有限公司
   （CATL）`` —— 同一字符串内已经给出了对应关系，属于原文事实而非猜测。
4. **完全相同的别名去重**：同一 alias 只产生一行（``ON CONFLICT DO NOTHING``）。

相似度不够的候选（如 ``宁德时代`` vs ``宁德时代新能源``，前者是后者前缀但
无法由上述规则证明等价）只输出到 ``candidates`` 列表供人工确认，**不写库**。
人工确认后用 source='manual' 单独补录，不要放宽本脚本。

## 幂等

写入走 ``INSERT ... ON CONFLICT (alias) DO NOTHING``，重复执行不产生重复行；
脚本还会先读一遍已存在别名并跳过，避免大小写变体触发
``uq_entity_alias_lower`` 唯一索引报错而中断整批。

## 用法

::

    python scripts/backfill_entity_alias.py                 # dry-run（默认）
    python scripts/backfill_entity_alias.py --write         # 实际写库
    python scripts/backfill_entity_alias.py --limit 500 --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# 让脚本在任意 cwd 下都能以包内绝对导入运行（python scripts/xxx.py）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import db  # noqa: E402
from common.logging_setup import setup_logging  # noqa: E402
from knowledge.entity_alias import (  # noqa: E402
    check_alias_conflicts,
    normalize_alias,
)

#: 机器推导的置信度。低于人工录入（1.0），便于事后按阈值筛出来复核。
BACKFILL_CONFIDENCE: float = 0.95

#: 法定后缀（按长度降序匹配，避免 "有限公司" 先于 "股份有限公司" 命中）。
LEGAL_SUFFIXES: tuple[str, ...] = (
    "股份有限公司",
    "有限责任公司",
    "集团有限公司",
    "控股有限公司",
    "股份公司",
    "有限公司",
    "公司",
    "company limited",
    "co., ltd.",
    "co., ltd",
    "co.,ltd",
    "co ltd",
    "corporation",
    "incorporated",
    "corp.",
    "corp",
    "limited",
    "ltd.",
    "ltd",
    "inc.",
    "inc",
    "gmbh",
    "plc",
)

#: 括号配对：匹配字符串末尾的一组括号及其内容（中英文括号都认）。
_BRACKET_RE = re.compile(r"^(?P<outer>.*?)\s*[（(]\s*(?P<inner>[^（）()]{1,40})\s*[）)]\s*$")
#: 形如英文缩写的括号内容：字母/数字/&/./- 组成，且至少含一个字母。
_ABBR_RE = re.compile(r"^[A-Za-z0-9&.\- ]+$")
_SLUG_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)

#: 候选建议的公共前缀长度门槛（低于 4 个字符的前缀几乎没有区分度）。
CANDIDATE_PREFIX_LEN: int = 4
#: 候选列表上限，避免大库刷屏。
CANDIDATE_LIMIT: int = 100

#: 「分布包含」规则的短名最小长度（按字符计）。
#:
#: 2 个字符的短名几乎全是通用词（``银行`` / ``中国`` / ``协会``），它们虽
#: 满足子串关系却在大量无关文档里出现，靠 :data:`CONTAINMENT_MIN_DOCS`
#: 与分布包含两道门能挡住，但没必要让它们进入 O(n²) 比较。
CONTAINMENT_MIN_LEN: int = 3

#: 「分布包含」规则要求的**最少支持文档数**。
#:
#: 1 条文档的巧合概率过高（正文里凑巧同时提到两个名字），2 条起要求同一
#: 包含关系在不同来源重复成立，才算分布证据。
CONTAINMENT_MIN_DOCS: int = 2

#: 通用词黑名单：即使满足分布包含也**拒绝**配对。
#:
#: 这些词不指代具体主体，一旦被当成别名，会把完全无关的事实拉到同一实体
#: 上——比漏归一危险得多。清单只放"确定会出事"的，不追求完备。
GENERIC_TOKENS: frozenset[str] = frozenset(
    {
        "中国", "国家", "全国", "中华", "公司", "集团", "银行", "协会",
        "委员会", "政府", "部门", "行业", "市场", "企业", "机构", "组织",
        "the", "inc", "ltd", "corp", "company", "group", "china",
    }
)

_INSERT_SQL = """
INSERT INTO osint_entity_alias (alias, canonical_id, alias_type, source, confidence)
VALUES (:alias, :canonical_id, :alias_type, 'backfill', :confidence)
ON CONFLICT (alias) DO NOTHING
"""

#: 取数时一并取回正文：分布包含规则要靠"名字出现在哪些文档里"作证据，
#: 只取 ``entities`` 是拿不到证据的。
_LOAD_UNITS_SQL = (
    "SELECT entities, title, content FROM osint_knowledge_unit WHERE entities IS NOT NULL"
)


# ------------------------------------------------------------ 文本工具 --


def _nfkc(text: str) -> str:
    """全角转半角（``ＣＡＴＬ`` → ``CATL``）并收尾空白。"""
    return unicodedata.normalize("NFKC", text).strip()


def _slug(text: str) -> str:
    """生成 canonical_id：小写、非字母数字（含中文）转 ``-``。

    不用 ``normalize_text`` 是因为它把数字统一成 ``#``——那是召回比较用的
    口径，用作 ID 会把 ``360`` 变成 ``###``，多个数字名公司直接撞车。
    """
    value = _SLUG_RE.sub("-", _nfkc(text).lower()).strip("-")
    return value


def strip_legal_suffix(name: str) -> str:
    """剥离法定后缀（只剥一层：最长匹配后立即停止）。

    只剥一层是刻意的：继续剥会把 ``某某集团`` 之类品牌词吃掉，扩大误合并
    面。剥完仍需逐字相等才算同一实体，见 :func:`derive_aliases`。
    """
    text = _nfkc(name)
    lowered = text.lower()
    for suffix in sorted(LEGAL_SUFFIXES, key=len, reverse=True):
        if lowered.endswith(suffix) and len(text) > len(suffix):
            return text[: len(text) - len(suffix)].strip()
    return text


def split_bracket(name: str) -> tuple[str, str]:
    """拆分 ``中文全称（CATL）`` 形态，返回 ``(主体, 英文缩写)``。

    也支持反向写法 ``CATL（宁德时代）``：只要一侧是 ASCII 缩写、另一侧是
    非空主体，就认这一对。识别不出缩写时返回 ``(原名, "")``。
    """
    match = _BRACKET_RE.match(_nfkc(name))
    if not match:
        return _nfkc(name), ""
    outer = match.group("outer").strip()
    inner = match.group("inner").strip()
    if not outer or not inner:
        return _nfkc(name), ""
    if _ABBR_RE.match(inner) and re.search(r"[A-Za-z]", inner):
        return outer, inner
    if _ABBR_RE.match(outer) and re.search(r"[A-Za-z]", outer):
        return inner, outer
    return _nfkc(name), ""


def infer_alias_type(raw: str, abbr: str) -> str:
    """推断 alias_type（仅用于落库标注，不参与归一判断）。"""
    if abbr:
        return "abbr"
    if re.fullmatch(r"[\x00-\x7f]+", _nfkc(raw)):
        return "en"
    if any(suffix in _nfkc(raw) for suffix in ("公司", "有限", "集团")):
        return "cn_full"
    return "variant"


# ------------------------------------------------------------ 候选生成 --


def _as_items(value: Any) -> list[Any]:
    """把 JSONB 值解析成列表（驱动可能返回 str 或已解析对象）。"""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    if isinstance(value, Mapping):
        return []
    return list(value) if isinstance(value, (list, tuple, set)) else []


def extract_names(units: Iterable[Mapping[str, Any]]) -> list[str]:
    """抽出全部实体原文（保持首次出现顺序、大小写原样）。"""
    seen: dict[str, None] = {}
    for unit in units:
        for item in _as_items(unit.get("entities")):
            if isinstance(item, str):
                name = item.strip()
            elif isinstance(item, Mapping):
                name = str(item.get("name") or item.get("canonical") or item.get("id") or "").strip()
            else:
                name = ""
            if name and name not in seen:
                seen[name] = None
    return list(seen)


def derive_aliases(names: Sequence[str]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """按确定性规则推导 ``alias → canonical_id`` 行。

    返回 ``(aliases, candidates)``：``aliases`` 可写库；``candidates`` 只是
    供人工确认的相似项，**绝不写库**。
    """
    # 第一遍：每个原文算出归一键、括号缩写
    records: list[dict[str, str]] = []
    for name in names:
        outer, abbr = split_bracket(name)
        base = strip_legal_suffix(outer)
        key = normalize_alias(base)
        if not key:
            continue
        records.append({"raw": name, "outer": outer, "abbr": abbr, "key": key, "slug": _slug(base)})

    # 括号配对给出的 canonical：缩写本身即 ID（catl）。
    # 同一缩写若对应多个不同主体（"甲公司（CATL）" 与 "乙公司（CATL）"），
    # 该缩写是二义的，**不能**用来配对——那会把两家公司合并成一家。
    abbr_outer: dict[str, set[str]] = {}
    for record in records:
        if record["abbr"]:
            abbr_outer.setdefault(normalize_alias(record["abbr"]), set()).add(record["key"])
    ambiguous_abbrs = {key for key, outers in abbr_outer.items() if len(outers) > 1}
    abbr_canonical: dict[str, str] = {
        key: _slug(key) for key in abbr_outer if key not in ambiguous_abbrs
    }

    # 第二遍：分组。独立出现的缩写（"CATL"）与括号里的缩写是完全相同的
    # 字符串，按同一 canonical 收敛——这是等价，不是猜测。
    groups: dict[str, dict[str, Any]] = {}
    for record in records:
        abbr_key = normalize_alias(record["abbr"])
        if abbr_key and abbr_key in abbr_canonical:
            group_key, emit_abbr = abbr_canonical[abbr_key], True
        elif not abbr_key and record["key"] in abbr_canonical:
            group_key, emit_abbr = abbr_canonical[record["key"]], True
        else:
            group_key, emit_abbr = record["key"], False
        group = groups.setdefault(
            group_key, {"members": [], "abbr": "", "emit_abbr": False}
        )
        if emit_abbr:
            group["emit_abbr"] = True
            group["abbr"] = group["abbr"] or (record["abbr"] or record["raw"])
        if record["raw"] not in [m["raw"] for m in group["members"]]:
            group["members"].append(record)

    aliases: list[dict[str, str]] = []
    for group in groups.values():
        members: list[dict[str, str]] = group["members"]
        canonical = (
            _slug(group["abbr"])
            if group["emit_abbr"] and group["abbr"]
            else min(member["slug"] for member in members)
        )
        if not canonical:
            continue
        # 单个成员且原文已与 canonical 一致时无需建行（resolve 会原样保留）
        if len(members) < 2 and normalize_alias(members[0]["raw"]) == canonical:
            continue
        for member in members:
            if normalize_alias(member["raw"]) == canonical:
                continue  # canonical 自身不必再映射到自己
            is_abbr = bool(member["abbr"]) and normalize_alias(member["raw"]) == normalize_alias(
                member["abbr"]
            )
            aliases.append(
                {
                    "alias": member["raw"],
                    "canonical_id": canonical,
                    "alias_type": "abbr" if is_abbr else infer_alias_type(member["raw"], ""),
                }
            )
        # 括号里的缩写本身也是一条别名（"CATL" 也是常见写法）
        if group["emit_abbr"] and group["abbr"]:
            aliases.append(
                {
                    "alias": group["abbr"],
                    "canonical_id": canonical,
                    "alias_type": "abbr",
                }
            )

    aliases = _dedupe_aliases(aliases)
    candidates = _suggest_candidates(records, {a["alias"] for a in aliases})
    for abbr in sorted(ambiguous_abbrs):
        candidates.append(
            {
                "left": {"key": abbr, "names": []},
                "right": {"key": "", "names": sorted(abbr_outer[abbr])},
                "reason": "ambiguous_abbr",
                "action": "同一缩写对应多个主体，已拒绝配对；请人工确认后再补录",
            }
        )
    return aliases, candidates


def unit_documents(units: Iterable[Mapping[str, Any]]) -> list[str]:
    """把每条事实的标题与正文拼成一篇"文档"，供分布证据使用。

    拼在一起而不是分开，是因为判断"这个名字出现在哪条事实里"时标题与正文
    是同一来源，分开算会把一条事实算成两条，凭空放大证据量。
    """
    documents: list[str] = []
    for unit in units:
        title = str(unit.get("title") or "").strip()
        content = str(unit.get("content") or "").strip()
        documents.append(f"{title} {content}".strip())
    return documents


def _document_index(
    documents: Sequence[str], names: Sequence[str]
) -> dict[str, frozenset[int]]:
    """``{原文名: 它出现过的文档下标集合}``（大小写不敏感的子串匹配）。

    不能用 ``normalize_text`` 后的键去匹配原文：它把数字统一成 ``#``，
    ``3M`` 会变成 ``#m`` 从而在原文里一个都匹配不到。这里用 ``lower()``
    即可，全角半角差异由上游 ``_nfkc`` 保证。
    """
    lowered = [doc.lower() for doc in documents]
    index: dict[str, frozenset[int]] = {}
    for name in names:
        needle = name.lower()
        if not needle:
            continue
        index[name] = frozenset(
            i for i, doc in enumerate(lowered) if needle in doc
        )
    return index


def derive_containment_aliases(
    documents: Sequence[str],
    names: Sequence[str],
    existing_canonical: Mapping[str, str] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """按「分布包含」推导 ``短名 → 长名`` 别名。

    ## 规则与依据

    若 ``short`` 是 ``long`` 的**严格子串**，且

    1. ``short`` 出现过的文档**全部**也出现 ``long``（``docs(short) ⊆ docs(long)``）；
    2. 支持文档数 ``|docs(short)| >= CONTAINMENT_MIN_DOCS``；
    3. ``short`` 长度 ``>= CONTAINMENT_MIN_LEN``、两侧都不在
       :data:`GENERIC_TOKENS` 里；

    则 ``short`` 视为 ``long`` 的简称，产出 ``short → canonical(long)``。

    ## 为什么这条证据是可靠的

    真机语料（50 条事实）上实测（2026-11-04）::

        中国人民银行 / 人民银行      含短名4 含长名4 同时4   100% 被覆盖 -> 配对
        中国光伏行业协会 / 光伏行业协会  含短名2 含长名2 同时2   100% 被覆盖 -> 配对
        欧盟委员会 / 欧盟            含短名2 含长名1 同时1   s14-02 含"欧盟"不含"委员会" -> 拒绝
        宁德时代新能源科技 / 宁德时代    含长名0               长名从未出现     -> 拒绝

    反向检验这条规则能不能挡住通用词：``银行`` ⊂ ``中国人民银行`` 成立，但含
    ``银行`` 的文档远多于 4 条，``docs(银行) ⊄ docs(中国人民银行)``，自动拒绝。
    ``中国`` / ``协会`` 同理。**这一条是本规则能自动跑而不出错的关键**——它把
    "子串"这种弱证据升级成了"分布等价"这种强证据。

    ## canonical 怎么定

    优先级：既有 canonical（长名）→ 既有 canonical（短名）→ 长名的 slug。

    复用既有 canonical 是第一优先，因为库里人工录的 ``中国人民银行 → pboc``
    与脚本推的 ``人民银行 → 中国人民银行`` 会构成链；链在
    :func:`knowledge.entity_alias._build_alias_map` 里会被压平，但这依赖
    "读表时两条都在"，一旦中间那条被人删掉就断链。此处直接取终值最稳。

    :param existing_canonical: ``{alias_norm: canonical_id}``，来自
        ``osint_entity_alias`` 现状；空表示干净库。
    """
    existing = {
        normalize_alias(key): value
        for key, value in (existing_canonical or {}).items()
        if value
    }
    raws = list(dict.fromkeys(name.strip() for name in names if name and name.strip()))
    index = _document_index(documents, raws)

    aliases: list[dict[str, str]] = []
    candidates: list[dict[str, Any]] = []
    for short in raws:
        lowered = short.lower()
        if len(short) < CONTAINMENT_MIN_LEN or lowered in GENERIC_TOKENS:
            continue
        short_docs = index.get(short, frozenset())
        if len(short_docs) < CONTAINMENT_MIN_DOCS:
            continue

        # 所有把 short 严格包含在内、且自身不是通用词的长名。按长度降序，
        # 命中第一个满足分布包含的就停——最长的那个最具体，歧义最小。
        holders = [
            name
            for name in raws
            if name != short
            and lowered in name.lower()
            and name.lower() not in GENERIC_TOKENS
        ]
        holders.sort(key=len, reverse=True)

        chosen: str | None = None
        rejected: list[tuple[str, str]] = []
        for long_name in holders:
            long_docs = index.get(long_name, frozenset())
            if not long_docs:
                rejected.append((long_name, "长名在任何事实的标题/正文里都未出现"))
                continue
            if short_docs <= long_docs:
                chosen = long_name
                break
            rejected.append(
                (
                    long_name,
                    f"分布包含不成立：含短名的 {len(short_docs)} 条文档中"
                    f"仅 {len(short_docs & long_docs)} 条同时含长名",
                )
            )

        if chosen is None:
            for long_name, reason in rejected[:1]:
                candidates.append(
                    {
                        "left": {"key": normalize_alias(short), "names": [short]},
                        "right": {"key": normalize_alias(long_name), "names": [long_name]},
                        "reason": "containment_unproven",
                        "evidence": reason,
                        "action": "子串关系成立但分布证据不足，需人工确认后按 source='manual' 补录",
                    }
                )
            continue

        canonical = (
            existing.get(normalize_alias(chosen))
            or existing.get(normalize_alias(short))
            or _slug(chosen)
        )
        if not canonical:
            # 纯符号名（slug 全被吃掉）没有可用 ID，宁可漏归一。
            continue
        if normalize_alias(short) == normalize_alias(canonical):
            continue  # 已经是 canonical 自身，映射无意义
        aliases.append(
            {
                "alias": short,
                "canonical_id": canonical,
                "alias_type": infer_alias_type(short, ""),
            }
        )
    return aliases, candidates


def suggest_singleton_entities(
    units: Iterable[Mapping[str, Any]],
    names: Sequence[str],
    known_keys: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """列出**在实体字段里只出现过一次**的名字，提示人工核对是否为已知实体的别名。

    这是对"中英文写法无法自动配对"的兜底。真机语料里 ``CATL`` / ``NVIDIA``
    / ``TSMC`` / ``SMIC`` / ``Tesla`` 各自只出现一次，且都是**纯平行文本**
    （``CATL`` 那条是纯英文标题，``宁德时代`` 那条是纯中文），正文零共现、
    实体零共现——**不存在任何可以自动判定等价的字符串证据**。既然证明不了，
    就不配对；但不配对不等于可以沉默，否则人工永远不知道漏了谁。

    ## 为什么按实体频次而不是文档出现次数

    这两个数**不相等**，且差异恰恰出现在最需要提示的地方：``TSMC`` 那条事实
    的标题是"台积电美国工厂量产4nm"，正文里根本没有 ``TSMC`` 这个串，按文档
    出现次数算是 0、按实体频次算是 1。用文档次数做判据会把 ``TSMC`` /
    ``SMIC`` / ``Tesla`` 全部漏掉——而它们正是"同一主体两种语言写法"的典型，
    是人工最该核对的一批。故此处按 ``entities`` 字段计数。

    ``documents`` 参数已移除：分布证据只在
    :func:`derive_containment_aliases` 里用，本函数不该依赖它。

    ``known_keys`` 是"已被别名表覆盖的键"，一律跳过——已归一的名字再提示人工
    核对是纯噪音（真机第一版 15 条里 9 条属于此类，把真正待确认的 6 个中英
    写法淹没了）。只留"孤立且尚无归属"的。
    """
    covered = {normalize_alias(key) for key in known_keys}
    frequency: dict[str, int] = {}
    for unit in units:
        seen: set[str] = set()
        for item in _as_items(unit.get("entities")):
            if isinstance(item, str):
                name = item.strip()
            elif isinstance(item, Mapping):
                name = str(
                    item.get("name") or item.get("canonical") or item.get("id") or ""
                ).strip()
            else:
                name = ""
            if name:
                seen.add(name)
        for name in seen:
            frequency[name] = frequency.get(name, 0) + 1

    return [
        {
            "name": name,
            "facts": frequency[name],
            "reason": "singleton_entity",
            "action": "实体字段中只出现一次；若为某高频实体的别名（如中英文写法差异），请人工确认后按 source='manual' 补录",
        }
        for name in names
        if frequency.get(name, 0) == 1 and normalize_alias(name) not in covered
    ]


def _dedupe_aliases(aliases: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    """同一别名只留一行；保留最早出现的那条（顺序由输入决定）。"""
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for row in aliases:
        key = normalize_alias(row["alias"])
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _suggest_candidates(
    records: Sequence[dict[str, str]], accepted_aliases: set[str]
) -> list[dict[str, Any]]:
    """给出相似但**无法证明等价**的实体对，供人工确认（不写库）。"""
    key_to_names: dict[str, list[str]] = {}
    for record in records:
        key_to_names.setdefault(record["key"], []).append(record["raw"])

    keys = sorted(key_to_names)
    accepted_keys = {normalize_alias(a) for a in accepted_aliases}
    candidates: list[dict[str, Any]] = []
    for i, left in enumerate(keys):
        for right in keys[i + 1 :]:
            if left == right or left in accepted_keys or right in accepted_keys:
                continue
            reason = ""
            if len(left) >= CANDIDATE_PREFIX_LEN and (
                left in right or right in left
            ):
                reason = "substring"
            elif (
                len(left) >= CANDIDATE_PREFIX_LEN
                and len(right) >= CANDIDATE_PREFIX_LEN
                and left[:CANDIDATE_PREFIX_LEN] == right[:CANDIDATE_PREFIX_LEN]
            ):
                reason = "common_prefix"
            if not reason:
                continue
            candidates.append(
                {
                    "left": {"key": left, "names": key_to_names[left][:3]},
                    "right": {"key": right, "names": key_to_names[right][:3]},
                    "reason": reason,
                    "action": "需人工确认，确认后用 source='manual' 补录，本脚本不写库",
                }
            )
            if len(candidates) >= CANDIDATE_LIMIT:
                return candidates
    return candidates


# ---------------------------------------------------------------- 主流程 --


def _load_units(limit: int | None) -> list[dict[str, Any]]:
    sql = _LOAD_UNITS_SQL
    params: dict[str, Any] = {}
    if limit is not None and limit > 0:
        sql = f"{sql} LIMIT :limit"
        params["limit"] = int(limit)
    return db.fetch_all(sql, params)


def _existing_alias_pairs() -> dict[str, str]:
    """读现有别名表，返回 ``{alias_norm: canonical_id}``。

    一次读回两张信息（键集合 + canonical），既给去重用，也给"分布包含"规则
    复用既有 canonical 用——两件事都必须基于同一个快照，分两次读会在并发
    回填时看到不一致的状态。
    """
    return {
        normalize_alias(str(row.get("alias") or "")): str(row.get("canonical_id") or "")
        for row in db.fetch_all("SELECT alias, canonical_id FROM osint_entity_alias")
    }


def suggest_short_form_candidates(
    names: Sequence[str],
    aliases: Sequence[dict[str, str]],
    existing_canonical: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """找出"某个已归一 canonical 的**前缀短名**"，且自身尚未归一的名字。

    ## 为什么需要这一类

    分布包含规则有一个结构性盲区：它对"长名"的要求是**在正文里出现过**。但
    法定后缀规则产出的 canonical 是**剥后缀的中间形态**——``华为技术有限公司``
    剥出 ``华为技术``。``华为技术`` 这个串在语料里一次都没出现（正文写的是
    "华为技术有限公司"），于是 ``华为`` ⊂ ``华为技术`` 这条子串关系永远拿不到
    分布证据，被 :func:`derive_containment_aliases` 判成 ``containment_unproven``。

    后果不是"少配一条"这么轻：``华为`` 在真机语料出现 3 次、``贵州茅台`` 2 次、
    ``宁德时代`` 3 次，它们恰恰是**事件层最需要归一的实体**。不归一就意味着
    同一事件"华为"与"华为技术有限公司"两种写法实体交集为空，候选召回失败。

    ## 为什么只做候选不做自动配对

    "前缀相同"是弱证据：``中国`` 是 ``中国人民银行`` 的前缀、``苹果`` 是
    ``苹果手机`` 的前缀，但两组都不是同一实体。位置信息（前缀 vs 任意子串）
    只能提高先验概率，够不上"确定性"这条写库门槛。故只出清单，由人工按
    ``source='manual'`` 补录。
    """
    accepted = {normalize_alias(row["alias"]) for row in aliases}
    accepted |= {normalize_alias(key) for key in (existing_canonical or {})}

    # canonical -> 该 canonical 下的全部已知写法（用于在提示里给出对照）
    members: dict[str, list[str]] = {}
    for row in aliases:
        members.setdefault(row["canonical_id"], []).append(row["alias"])
    for alias, canonical in (existing_canonical or {}).items():
        if canonical:
            members.setdefault(canonical, []).append(alias)

    candidates: list[dict[str, Any]] = []
    for name in names:
        key = normalize_alias(name)
        if (
            not key
            or key in accepted
            or len(name) < CONTAINMENT_MIN_LEN - 1
            or key in GENERIC_TOKENS
        ):
            continue
        for canonical, known in members.items():
            canonical_key = normalize_alias(canonical)
            if len(canonical_key) <= len(key) or not canonical_key.startswith(key):
                continue
            candidates.append(
                {
                    "left": {"key": key, "names": [name]},
                    "right": {"key": canonical_key, "names": sorted(set(known))[:3]},
                    "reason": "short_form_suspect",
                    "evidence": (
                        f"「{name}」是 canonical「{canonical}」的前缀短名，"
                        f"但该 canonical 是剥后缀产物，正文中不出现，无法取得分布证据"
                    ),
                    "action": "前缀相同不足以证明等价，需人工确认后按 source='manual' 补录",
                }
            )
            break
    return candidates


def _filter_resolved_candidates(
    candidates: Sequence[dict[str, Any]],
    aliases: Sequence[dict[str, str]],
    existing_keys: Iterable[str],
) -> list[dict[str, Any]]:
    """剔除"两侧都已被归一覆盖"的候选，只留真正待人工确认的。

    候选是给人工看的清单，混进已解决项会让人对整份清单失去信任——真机第一
    版 dry-run 就有 8 条候选里 6 条其实早已被括号/后缀规则或既有别名表解决。
    判据是**两侧同时**命中接受集合：只要还有一侧没被覆盖，这对就仍然值得看。
    """
    accepted = {normalize_alias(row["alias"]) for row in aliases}
    accepted |= {normalize_alias(key) for key in existing_keys}
    remaining: list[dict[str, Any]] = []
    for candidate in candidates:
        left = normalize_alias(str(candidate.get("left", {}).get("key") or ""))
        right = normalize_alias(str(candidate.get("right", {}).get("key") or ""))
        if left and right and left in accepted and right in accepted:
            continue
        remaining.append(candidate)
    return remaining


def run(*, dry_run: bool = True, limit: int | None = None) -> dict[str, Any]:
    """执行一次回填，返回结构化报告（不打印）。异常由 :func:`main` 兜底。"""
    units = _load_units(limit)
    names = extract_names(units)
    documents = unit_documents(units)
    existing_pairs = _existing_alias_pairs()

    # 两条互补的确定性规则：
    #   derive_aliases            —— 括号配对 + 法定后缀剥离（字符串层面的等价）
    #   derive_containment_aliases —— 分布包含（语料层面的证据）
    # 后者的产出可能与前者的 canonical 不同（如脚本推"人民银行→中国人民银行"，
    # 而人工已录"中国人民银行→pboc"），故必须把现状传进去复用 canonical。
    bracket_aliases, candidates = derive_aliases(names)
    containment, containment_candidates = derive_containment_aliases(
        documents, names, existing_pairs
    )
    aliases = _dedupe_aliases([*bracket_aliases, *containment])
    candidates = [
        *candidates,
        *containment_candidates,
        *suggest_short_form_candidates(names, aliases, existing_pairs),
    ]

    conflicts = check_alias_conflicts(
        [(row["alias"], row["canonical_id"]) for row in aliases]
    )
    if conflicts:
        # 二义别名整条丢弃：宁可漏归一，不可错归一
        conflicted = set(conflicts)
        aliases = [row for row in aliases if normalize_alias(row["alias"]) not in conflicted]
        candidates.extend(
            {
                "left": {"key": key, "names": []},
                "right": {"key": "", "names": canonicals},
                "reason": "ambiguous_alias",
                "action": "别名指向多个 canonical，已拒绝入表；请人工确认唯一归属",
            }
            for key, canonicals in conflicts.items()
        )

    existing = set(existing_pairs)
    planned = [row for row in aliases if normalize_alias(row["alias"]) not in existing]
    candidates = _filter_resolved_candidates(candidates, aliases, existing_pairs)

    inserted = 0
    if not dry_run and planned:
        inserted = db.execute_many(
            _INSERT_SQL,
            [
                {
                    "alias": row["alias"],
                    "canonical_id": row["canonical_id"],
                    "alias_type": row["alias_type"],
                    "confidence": BACKFILL_CONFIDENCE,
                }
                for row in planned
            ],
        )

    return {
        "ok": True,
        "dry_run": dry_run,
        "units_scanned": len(units),
        "names_scanned": len(names),
        "aliases_detected": len(aliases),
        "aliases_from_bracket_or_suffix": len(bracket_aliases),
        "aliases_from_containment": len(containment),
        "aliases_skipped_existing": len(aliases) - len(planned),
        "aliases_planned": len(planned),
        "aliases_inserted": inserted,
        "aliases": planned if dry_run else [],
        "candidates": candidates,
        "singletons": suggest_singleton_entities(
            units,
            names,
            known_keys=[*existing_pairs, *(row["alias"] for row in aliases)],
        ),
        "conflicts": conflicts,
    }


def _print_report(report: Mapping[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(
        f"[{'dry-run' if report['dry_run'] else 'write'}] "
        f"units={report['units_scanned']} names={report['names_scanned']} "
        f"detected={report['aliases_detected']} "
        f"(bracket/suffix={report['aliases_from_bracket_or_suffix']} "
        f"containment={report['aliases_from_containment']}) "
        f"planned={report['aliases_planned']} "
        f"inserted={report['aliases_inserted']} "
        f"candidates={len(report['candidates'])} "
        f"singletons={len(report['singletons'])} "
        f"conflicts={len(report['conflicts'])}"
    )
    for row in report["aliases"]:
        print(f"  [plan] {row['alias']} -> {row['canonical_id']} ({row['alias_type']})")
    for item in report["candidates"]:
        print(f"  [candidate] {item['left']} vs {item['right']} ({item['reason']})")
        evidence = item.get("evidence")
        if evidence:
            print(f"            └ {evidence}")
    if report["singletons"]:
        names = [item["name"] for item in report["singletons"]]
        print(f"  [singleton] 仅出现一次的实体（需人工核对是否为别名）：{', '.join(names)}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="实体别名回填（确定性规则，默认 dry-run）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="只打印计划，不写库（默认）",
    )
    parser.add_argument(
        "--write",
        dest="dry_run",
        action="store_false",
        help="实际写入 osint_entity_alias",
    )
    parser.add_argument("--limit", type=int, default=None, help="最多扫描的知识单元数")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出到 stdout")
    args = parser.parse_args(argv)

    setup_logging()
    try:
        report = run(dry_run=args.dry_run, limit=args.limit)
    except Exception as exc:  # noqa: BLE001 - 脚本入口统一转退出码
        error = {"ok": False, "dry_run": args.dry_run, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(error, ensure_ascii=False, indent=2))
        return 1
    _print_report(report, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
