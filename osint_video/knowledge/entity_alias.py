"""实体别名归一（P1-B）：把同一实体的多种写法收敛到一个 ``canonical_id``。

## 职责边界

- **只做别名 → canonical 的单向查表**，不改写任何事实数据：
  ``osint_knowledge_unit.entities`` / ``osint_story.entities`` 保留原文，
  归一只在内存里发生（算子读表后自行映射），原文是审计依据。
- **不写库、不建表**：本模块只有 :func:`load_alias_map` 一个 IO 函数，
  建表归 ``store/schema.sql`` / ``scripts/init_db.py``，灌数归
  ``scripts/backfill_entity_alias.py``。
- **不定义归一算法**：比较口径必须与事件层算子完全一致，因此直接复用
  :func:`knowledge.story_group.normalize_text`（标点剥离 + 全角空白折叠 +
  小写化 + 数字归一），不另起一套规则，避免"查表用一套、召回用另一套"。
- 不硬依赖 psycopg / SQLAlchemy：``conn`` 按 duck typing 处理
  （任意提供 ``execute(sql)`` 的对象均可，测试可注入 fake）。

## 为什么必须这么保守

``entities`` 是候选召回的键（``pivot.entities & other.entities``）。归一错误
会**直接抬高独立来源数**，而独立来源数是热度的分母，错误不可逆且表现为
"数字偶尔偏大"，很难定位。所以这里的取向是：**宁可漏归一，不可错归一**。
具体到实现：

- 一个别名映射到两个 canonical 时，该别名**整条丢弃**（不是任选其一），
  见 :func:`load_alias_map` 的冲突处理；
- :func:`resolve` 对未命中的键原样保留——没有别名不等于实体不存在，
  丢掉它会让召回率下降，那是另一种失真。
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

from knowledge.story_group import entity_keys, normalize_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 常量 --

#: 别名类型取值（与 store/schema.sql 的 CHECK 约束一一对应，改此处必须同步 DDL）。
ALIAS_TYPES: tuple[str, ...] = (
    "abbr",  # 英文缩写：CATL
    "en",  # 英文全称：Contemporary Amperex Technology
    "cn_full",  # 中文全称：宁德时代新能源科技股份有限公司
    "former_name",  # 曾用名
    "product",  # 产品/品牌名：麒麟电池
    "variant",  # 其他书写变体（空格、全半角、大小写等）
)

#: 别名来源取值（与 DDL 的 CHECK 约束对应）。
ALIAS_SOURCES: tuple[str, ...] = ("manual", "backfill", "llm")

#: 供测试与运维判断的规范值集合。
_VALID_ALIAS_TYPES = frozenset(ALIAS_TYPES)

#: 读表语句。别名原文与 canonical_id 都取原样，归一在 Python 侧做，
#: 这样数据库里能直接看到原始写法，排障不必反推 SQL 函数。
_LOAD_SQL = "SELECT alias, canonical_id FROM osint_entity_alias"


# ------------------------------------------------------------ 纯函数 --


def is_valid_alias_type(value: str | None) -> bool:
    """``alias_type`` 是否在 :data:`ALIAS_TYPES` 内（落库前的最后一道校验）。"""
    return value in _VALID_ALIAS_TYPES


def normalize_alias(text: str | None) -> str:
    """别名归一：NFKC 折叠全角后，复用事件层算子的 ``normalize_text``。

    NFKC 这一步是必要的补充：``normalize_text`` 只处理空白/标点/大小写/数字，
    不折叠全角字母，``ＣＡＴＬ`` 会归一成 ``ｃａｔｌ`` 而非 ``catl``，与
    ``CATL`` 的查询侧对不上。除此之外口径与算子完全一致（同一函数），
    避免"查表一套、召回另一套"。
    """
    if not text:
        return ""
    return normalize_text(unicodedata.normalize("NFKC", text))


def check_alias_conflicts(
    rows: Iterable[tuple[str, str]],
) -> dict[str, list[str]]:
    """检查候选行里的二义别名：同一 alias 指向多个 canonical。

    返回 ``{alias_norm: [canonical_id, ...]}``，只含冲突项（空 dict = 无冲突）。
    回填脚本在写库前必须调用它：二义别名**一律不入表**，哪怕它们看起来
    很像同一实体——"像"不是证据，错归一会污染独立来源数。
    """
    grouped: dict[str, list[str]] = {}
    for alias, canonical_id in rows:
        key = normalize_alias(alias)
        canonical = str(canonical_id or "").strip()
        if not key or not canonical:
            continue
        bucket = grouped.setdefault(key, [])
        if canonical not in bucket:
            bucket.append(canonical)
    return {key: canonicals for key, canonicals in grouped.items() if len(canonicals) > 1}


def resolve(keys: set[str], alias_map: Mapping[str, str]) -> set[str]:
    """把实体键集合映射为 canonical 集合；未命中的键**原样保留**。

    ``keys`` 可以已归一（``entity_keys`` 的产物），也可以是原文——对已归一
    的字符串再次归一结果不变（``normalize_text`` 幂等），因此两种入参都安全。
    幂等：``resolve(resolve(keys))`` 与 ``resolve(keys)`` 相等。
    """
    resolved: set[str] = set()
    for key in keys:
        norm = normalize_alias(key)
        if not norm:
            continue  # 空键没有信息量，保留它只会制造无意义的"共同实体"
        resolved.add(alias_map.get(norm, norm))
    return resolved


def canonicalize(entities: Sequence[Any] | str | None, alias_map: Mapping[str, str]) -> list[str]:
    """把 JSONB 原始 ``entities`` 归一为去重后的 canonical 列表（按字典序）。

    内部复用 :func:`knowledge.story_group.entity_keys`，因而兼容
    ``["宁德时代", ...]`` 与 ``[{"name": "宁德时代"}, ...]`` 两种形态。
    排序是为了让输出可比较（单测与 diff 友好），语义上仍是无序集合。
    """
    keys = entity_keys({"entities": entities})
    return sorted(resolve(keys, alias_map))


# ---------------------------------------------------------------- IO --


def _as_statement(conn: Any, sql: str) -> Any:
    """把 SQL 字符串适配成 conn 能接受的对象。

    SQLAlchemy 2.x 拒绝裸字符串（要求 ``text()``），而 psycopg / fake conn
    原生接受 ``str``。这里按连接类型判断，SQLAlchemy 只在真正需要时才导入，
    模块本身不硬依赖它。
    """
    module = type(conn).__module__ or ""
    if module.startswith("sqlalchemy"):
        from sqlalchemy import text  # 局部导入：仅 SQLAlchemy 连接才需要

        return text(sql)
    return sql


def _fetch_rows(conn: Any, sql: str) -> list[Any]:
    """执行查询并尽量归一为「行序列」，兼容三种常见返回形态。

    - SQLAlchemy ``CursorResult`` → ``.mappings().all()``（dict 行）
    - psycopg ``Cursor`` → ``.fetchall()``（tuple 行）
    - 已物化的 list → 原样返回（fake conn 常用）
    """
    result = conn.execute(_as_statement(conn, sql))
    mappings = getattr(result, "mappings", None)
    if callable(mappings):
        return list(mappings().all())
    fetchall = getattr(result, "fetchall", None)
    if callable(fetchall):
        return list(fetchall())
    return list(result or [])


def _pick(row: Any, index: int, name: str) -> str:
    """从 dict 行或 tuple 行里取字段（不假设驱动返回类型）。"""
    if isinstance(row, Mapping):
        return str(row.get(name) or "")
    try:
        return str(row[index] or "")
    except (IndexError, TypeError):  # pragma: no cover - 驱动返回异常形态
        return ""


def _flatten_alias_chains(alias_map: dict[str, str]) -> tuple[dict[str, str], set[str]]:
    """把 ``A -> B -> C`` 压平成 ``A -> C``，环形链整组剔除。

    ## 为什么必须压平

    :func:`resolve` 只跳**一层**。若库里同时存在两条人工/回填各写一半的行::

        中国人民银行 -> pboc          # 人工录的正式 slug
        人民银行     -> 中国人民银行   # 回填脚本按"分布包含"推导的长名

    那么 ``resolve({'人民银行'})`` 得 ``{'中国人民银行'}``，而
    ``resolve({'中国人民银行'})`` 得 ``{'pboc'}``——**同一主体的两种写法落到
    两个不同 canonical**，实体交集判空，独立来源数被系统性抬高。这正是本模
    块开宗明义要防的错误，且它不报错、只表现为"来源数偶尔偏大"。

    压平放在**建表期**而不是 :func:`resolve` 里，是为了让调用方（事件层算子）
    保持"查一次表就拿到终值"的简单约定，不必关心链有多长。

    ## 环怎么处理

    ``a -> b -> a`` 无法判定谁是 canonical，整条链上的键**全部剔除**并记
    ERROR——与二义键同一取向：宁可漏归一，不可错归一。剔除后两侧各自原样保
    留，最坏结果是漏合并，而不是错合并。

    返回 ``(压平后的映射, 因成环而剔除的键集合)``。

    ## 自指不是环

    ``catl -> catl`` 是**恒等映射**，必须视为终止点而不是环。库里这类行是
    正常的（canonical 本身恰好也是常见写法），把它当环剔除会让
    ``alias_map["catl"]`` 直接消失。:func:`resolve` 对未命中键原样保留，
    所以行为等价，但这属于"靠下游兜底掩盖建表错误"，不能留。

    连带的是 ``a -> b`` 加 ``b -> b`` 必须压平成 ``a -> b``：若只看"回到链上
    更早节点"就判环，``b`` 会被误判（它回到的是自己，不是 ``a``）。
    """
    identity = {
        key for key, value in alias_map.items()
        if normalize_alias(key) == normalize_alias(value)
    }
    # 自指项不参与遍历（否则 a->b, b->b 里 b 会被误判成环），但结果要保留。
    graph = {key: value for key, value in alias_map.items() if key not in identity}

    flattened: dict[str, str] = {}
    cyclic: set[str] = set()
    for start in graph:
        seen = {start}
        chain = [start]
        current = graph[start]
        while current in graph:
            if current in seen:
                cyclic.update(chain)
                break
            seen.add(current)
            chain.append(current)
            current = graph[current]
        else:
            flattened[start] = current
    for key in identity:
        flattened.setdefault(key, alias_map[key])
    return flattened, cyclic


def _build_alias_map(pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """把 ``(alias, canonical_id)`` 序列收成映射，二义键与环形链整条剔除。

    读表与读 ``common.db`` 两条路径共用本函数，避免《冲突处理》在多处
    各写一遍而漂移——这类"宁可漏归一"的判定一旦两处不一致，排查成本极高。

    处理顺序有依赖：**先剔除二义键，再压平链**。反过来的话，一条二义键会让
    经过它的链断在半路，压平结果会依赖于读表顺序。
    """
    alias_map: dict[str, str] = {}
    conflicts: set[str] = set()
    for alias, canonical_raw in pairs:
        key = normalize_alias(alias)
        canonical = str(canonical_raw or "").strip()
        if not key or not canonical:
            continue
        existing = alias_map.get(key)
        if existing is not None and existing != canonical:
            conflicts.add(key)
            continue
        alias_map[key] = canonical
    if conflicts:
        logger.error(
            "实体别名存在二义映射，已整条剔除（宁可漏归一不可错归一）：%s",
            sorted(conflicts),
        )
        for key in conflicts:
            alias_map.pop(key, None)

    alias_map, cyclic = _flatten_alias_chains(alias_map)
    if cyclic:
        logger.error(
            "实体别名存在环形映射，已整条剔除（无法判定 canonical，宁漏不错）：%s",
            sorted(cyclic),
        )
        for key in cyclic:
            alias_map.pop(key, None)
    return alias_map


def load_alias_map(conn: Any) -> dict[str, str]:
    """读 ``osint_entity_alias``，返回 ``{alias_norm: canonical_id}``。

    冲突处理：同一 ``alias_norm`` 出现两个不同 ``canonical_id`` 时，该键
    **整条剔除**并记 ERROR——任选其一会让归一结果依赖读表顺序，是不可接受
    的随机性。DB 侧 ``UNIQUE (alias)`` + ``uq_entity_alias_lower`` 已把这种
    情况挡在写入阶段，这里只是防御性兜底（如手工改库、迁移残留）。

    canonical_id 自身不参与归一：它是 ID（slug），不是可再归一的写法；
    但它若恰好也出现在别名里（``catl → catl``），映射结果不变，天然幂等。
    """
    return _build_alias_map(
        (_pick(row, 0, "alias"), _pick(row, 1, "canonical_id"))
        for row in _fetch_rows(conn, _LOAD_SQL)
    )


def load_alias_map_from_db(fetch_all: Any = None) -> dict[str, str]:
    """用项目统一入口 ``common.db`` 读别名表。

    算子（``story_group``）只用 ``common.db`` 的模块级函数，拿不到 DB-API
    连接，故单开这一条路径。``common.db`` 延迟导入：knowledge 层不应因为
    "想查个别名"就在 import 期硬依赖数据库驱动。

    :param fetch_all: 可注入 ``(sql, params=None) -> list[dict]``，便于单测离线。
    """
    if fetch_all is None:
        from common import db  # 延迟导入，避免 knowledge 层硬绑驱动

        fetch_all = db.fetch_all
    rows = fetch_all(_LOAD_SQL) or []
    return _build_alias_map(
        (row.get("alias"), row.get("canonical_id")) for row in rows
    )
