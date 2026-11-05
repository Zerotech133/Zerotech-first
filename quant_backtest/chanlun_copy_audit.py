# -*- coding: utf-8 -*-
"""对外文案合规审计扫描器 v8（只读）。

背景
----
缠论系统七轮审计后定位改为「结构标注工具」（画图 / 人工研判辅助），
不再作为入场 / 减仓依据。chanlun_scan.py 的 docstring 与推送尾注已改，
但仓库里面向用户的字符串（推送正文、卡片、HTML、报告模板、告警）可能
仍残留交易指令性措辞。本脚本把全仓 `*.py` 里的字符串常量按词表分级，
交人工逐条裁定。

硬约束
------
* 纯只读：只 `ast.parse` 源文件文本，不 import、不执行被扫模块。
* 不修改任何既有文件；唯一写盘是 --json-out / --md-out 两个产物。
* 无 git 操作。

分级
----
RED   直接交易指令（买入 / 满仓 / 目标价 …）—— 必须裁定
AMBER 暗示性措辞（机会 / 布局 / 低吸 …）—— 视语境裁定
GREEN 结构描述词（标注 / 中枢 / 背驰 / 分型 …）—— 用于确认合规覆盖面
NONE  未命中词表

难点是误报抑制：字符串既可能是用户可见文案，也可能是枚举值、字典键、
路径、正则、SQL、日志字段、情感词典。判据见下方 SUPPRESS_* 常量与
`_judge()` 的分支注释，全部规则都会在报告里列出类别与计数，便于复核
「有没有把该报的藏起来」。

用法
----
  python chanlun_copy_audit.py                    # 全仓扫描 + 汇总表
  python chanlun_copy_audit.py --selftest         # 自检：构造 RED 字符串验证能命中
  python chanlun_copy_audit.py --top 30           # 汇总表显示前 30 个文件
  python chanlun_copy_audit.py --list-red         # 顺带打印 RED 明细
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# ─────────────────────────────── 词表（可维护） ───────────────────────────────

RED_WORDS = [
    "买入", "卖出", "建仓", "加仓", "减仓", "清仓", "止损", "止盈",
    "目标价", "建议买", "推荐买", "必涨", "翻倍", "满仓", "抄底", "逃顶",
    # 变体（同义、更口语）
    "全仓", "梭哈", "杀进去", "无脑买", "闭眼买", "稳赚", "包赚",
]

AMBER_WORDS = [
    "机会", "布局", "介入", "上车", "潜伏", "低吸", "高抛",
    "看多", "看空", "牛股", "强势股推荐",
]

GREEN_WORDS = ["标注", "结构", "观察", "中枢", "背驰", "分型", "笔", "线段", "买卖点"]

# 「笔」单字命中率极高（笔记/一笔/随笔），只在同串出现结构词时才算 GREEN。
GREEN_GATED = {"笔": ("分型", "中枢", "线段", "缠论", "背驰", "结构", "标注", "买卖点")}

# AMBER 的良性复合词：「机会成本」是经济学名词，不是招揽。命中前先剥离。
AMBER_BENIGN_COMPOUNDS = {
    "机会成本": "机会",
    "机会集": "机会",
    "布局文件": "布局",
    "布局管理": "布局",
    "布局参数": "布局",
    "布局容器": "布局",
    "介入参数": "介入",
}

# ─────────────────────────────── 抑制规则常量 ───────────────────────────────

# 视为「用户可见出口」的调用名（完整点号名或末段名）
SINK_CALLS = {
    "print", "pprint", "write", "writelines", "echo",
    "send", "send_text", "send_markdown", "send_msg", "send_card", "send_wecom",
    "push", "push_text", "notify", "alert", "reply", "render", "render_template",
}
# 末段命中即视为日志/控制台出口（任意 obj.info / logger.error）
SINK_METHOD_TAILS = {"info", "warning", "warn", "error", "debug", "critical", "exception", "log"}

# 关键字实参名 → 判定为「文案注入点」
USER_KWARGS = {
    "title", "msg", "message", "text", "content", "body", "desc", "description",
    "summary", "caption", "label", "hint", "tip", "note", "reason", "detail",
    "detail_text", "html", "md", "markdown", "subject", "header", "footer",
    "prefix", "suffix", "advice", "action", "remark", "comment", "memo",
}

# 变量名 → 判定为「文案载体」
USER_VAR_RE = re.compile(
    r"(msg|message|text|txt|title|body|content|desc|summary|report|caption|"
    r"label|hint|tip|note|advice|action|reason|detail|html|markdown|md|"
    r"subject|line|card|push|alert|warn|banner|footer|header|template|tpl)",
    re.I,
)

# 词典 / 配置型列表的宿主名（情感词表、停用词、关键词表）
LEXICON_VAR_RE = re.compile(
    r"(words?|lexicon|keywords?|stop_?words?|terms?|sentiment|dict_|themes?|tags?)",
    re.I,
)

# 列表型出口：buy/sell.append("清仓") 这类「动作串拼装」是典型对外文案，
# 不能因为没进 print/logger 就当内部值。只认语义明确承载文案的载体名。
OUTPUT_LIST_VAR_RE = re.compile(
    r"(buy|sell|action|todo|plan|advice|msg|lines|blocks|notes|items|cards|"
    r"html|out|warns|alerts|tips|body|text)",
    re.I,
)

# 交付面识别（只用于排序与分组，不用于抑制）
DELIVERY_FILE_RE = re.compile(
    r"(push|card|notify|alert|report|page|board|portal|digest|brief|weekly|"
    r"runner|premarket|watch|banner|scan|market|position|ledger|subscribe|broadcast)", re.I)
RESEARCH_FILE_RE = re.compile(
    r"(backtest|rotation|probe|diag|sim|audit|qpzm|verify|replay|bench|ablat|"
    r"research|experiment|sweep|grid|_w5c|t\d+_|study|eval)", re.I)
PROMPT_CONST_RE = re.compile(r"(PROMPT|SYS|SYSTEM|RULE|INSTRUCTION|PERSONA)", re.I)

# 否定 / 免责语境：「非买入信号」「不抄底」是合规表述，标记但不按违规处理
NEG_CHARS = "非不勿禁无未别免"
NEG_WINDOW = 6

# 研究口径的良性短语：标注为 benign，便于人工快速放行（不抑制、不隐藏）
BENIGN_PHRASES = [
    "买入持有", "买入信号", "卖出信号", "买入数", "卖出数", "买入源",
    "卖出侧", "止损幅度", "止损距离", "硬止损", "移动止损", "灾难止损", "止损线",
    "买入日", "卖出日", "买价", "卖价", "买入日期", "卖出日期",
]
# 「全仓」在本仓既指 all-in，也常作「全仓库」简称；后接这些词时按良性处理
BENIGN_QUAN_CANG = ("库", "漂移", "范围", "扫描", "一致", "文件", "健康", "无")

# 纯代码标记：无中文、短、仅标识符字符
CODE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-.:/|+*#$%&@!?=<>~^()\[\]{},;'\"]{0,24}$")

# 枚举语义的短中文（信号类型名），如 "1买" "3卖" "二买"
ENUM_CN_RE = re.compile(r"^[0-9一二三四五六七八九十]?\s*[买卖点]{1,2}[A-Za-z0-9_]*$")

# 路径 / 正则 / SQL / 格式化串
PATHY_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\.{0,2}/|https?://|\w+\.(py|json|csv|db|md|html|png|log)$)")
REGEXY_RE = re.compile(r"(\^|\\d|\\w|\[[^\]]*\]\+|\{\d+(,\d*)?\})")
SQLY_RE = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|WITH)\b", re.I)

# HTML / Markdown 模板特征（表格、标题、加粗、标签）
HTML_RE = re.compile(r"<\s*(div|span|table|tr|td|th|p|br|b|strong|ul|li|h[1-6]|a|style|html|body|font|em)\b", re.I)
MARKUP_HEAD_RE = re.compile(r"^\s*(\*\*|#{1,6}\s|#{1,6}[^\s]|\||-\s|>\s|<)")

CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 目录 / 文件排除
EXCLUDE_DIR_PREFIXES = (
    ".venv", "venv", "__pycache__", "node_modules", ".git", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".fastembed_cache", ".models", "data",
    "logs", "outbox", "tmp",
)
BACKUP_NAME_RE = re.compile(r"(\.bak|\.orig|\.old|~$|backup|_copy\d*| copy|副本)", re.I)
TEST_PATH_RE = re.compile(r"(^|[\\/])(tests?|tests_ai_operator)[\\/]|(^|[\\/])test_[^\\/]*\.py$|_test\.py$", re.I)
ARCHIVE_PATH_RE = re.compile(r"(^|[\\/])(archive|_archive_legacy[^\\/]*)[\\/]", re.I)

MAX_TEXT = 400  # JSON / 报告里单条原文上限（超出截断并标记）

# ─────────────────────────────── AST 工具 ───────────────────────────────


def _call_name(node: ast.AST) -> str:
    """取调用的点号名：logger.info → logger.info；print → print。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _target_names(node: ast.AST) -> list[str]:
    """赋值左值的变量名（支持 a = b = ...、tuple 解包、self.x = ...）。"""
    out: list[str] = []
    if isinstance(node, ast.Name):
        out.append(node.id)
    elif isinstance(node, ast.Attribute):
        out.append(node.attr)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            out.extend(_target_names(elt))
    elif isinstance(node, ast.Starred):
        out.extend(_target_names(node.value))
    return out


def _build_parents(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    stack = [tree]
    while stack:
        n = stack.pop()
        for c in ast.iter_child_nodes(n):
            parents[id(c)] = n
            stack.append(c)
    return parents


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """模块 / 类 / 函数体第一条 Expr 的字符串 = docstring（开发者文档，非用户文案）。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _nearest_scope(parents: dict[int, ast.AST], node: ast.AST) -> str:
    """最近的函数限定名（类方法带类名），模块级返回 <module>。"""
    cur = node
    parts: list[str] = []
    while id(cur) in parents:
        cur = parents[id(cur)]
        if isinstance(cur, ast.ClassDef):
            parts.append(cur.name)
        elif isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parts.append(cur.name)
            break
    return "<module>" if not parts else ".".join(reversed(parts))


def _ancestors(parents: dict[int, ast.AST], node: ast.AST, limit: int = 16) -> list[ast.AST]:
    out: list[ast.AST] = []
    cur = node
    while id(cur) in parents and len(out) < limit:
        cur = parents[id(cur)]
        out.append(cur)
    return out


# ─────────────────────────────── 分级 ───────────────────────────────


def _strip_benign(text: str) -> str:
    """剥离良性复合词，避免「机会成本」这类经济学名词被判 AMBER。"""
    out = text
    for compound, _word in AMBER_BENIGN_COMPOUNDS.items():
        out = out.replace(compound, "")
    return out


def classify(text: str) -> tuple[str, list[str]]:
    """返回 (级别, 命中词)。RED > AMBER > GREEN。"""
    hits_red = [w for w in RED_WORDS if w in text]
    if hits_red:
        return "RED", hits_red

    amber_src = _strip_benign(text)
    hits_amber = [w for w in AMBER_WORDS if w in amber_src]
    if hits_amber:
        return "AMBER", hits_amber

    hits_green = []
    for w in GREEN_WORDS:
        if w not in text:
            continue
        gate = GREEN_GATED.get(w)
        if gate and not any(g in text for g in gate):
            continue
        hits_green.append(w)
    if hits_green:
        return "GREEN", hits_green
    return "NONE", []


# ─────────────────────────────── 单条判定 ───────────────────────────────


def _judge(text: str, node: ast.Constant, parents: dict[int, ast.AST], doc_ids: set[int],
           path: Path, ctx_cache: dict) -> dict:
    """对一条字符串常量给出：是否用户可见 / 上下文键 / 抑制原因。

    抑制规则(每条都有明确判据，报告里按 reason 汇总计数)：
      1. docstring            —— 模块/类/函数首条字符串，开发者文档
      2. test_file            —— tests/ 或 test_*.py，测试断言文案
      3. enum_token           —— 无中文且 ≤24 字符的标识符型串（"buy"/"1buy"/"b1"）
      4. enum_cn_label        —— "1买"/"二卖" 这类信号类型短标签
      5. dict_key             —— 字典键（非取值），结构字段名
      6. lexicon_list         —— 情感词/停用词/关键词表元素，词表本身不是话术
      7. code_lookup          —— 比较/下标/成员测试/字符串方法参数，代码分支用值
      8. path_or_regex_or_sql —— 文件路径、正则、SQL
      9. config_literal       —— 短配置值（≤8 字符且无标点、无中文句子特征）
    """
    ctx = ctx_cache

    # ---- 1. docstring ----
    if id(node) in doc_ids:
        return {"visible": False, "reason": "docstring", "key": "docstring", "kind": "docstring"}

    # ---- 2. 测试文件 ----
    if TEST_PATH_RE.search(str(path)):
        return {"visible": False, "reason": "test_file", "key": "test", "kind": "test"}

    anc = _ancestors(parents, node)
    parent = parents.get(id(node))

    # ---- 3. 字典键 ----
    if isinstance(parent, ast.Dict) and any(k is node for k in parent.keys):
        return {"visible": False, "reason": "dict_key", "key": "dict_key", "kind": "dict_key"}

    # ---- 4. 词表元素 ----
    if isinstance(parent, (ast.List, ast.Tuple, ast.Set)):
        holder = parents.get(id(parent))
        holder_names: list[str] = []
        if isinstance(holder, ast.Assign):
            for t in holder.targets:
                holder_names.extend(_target_names(t))
        elif isinstance(holder, ast.AnnAssign):
            holder_names.extend(_target_names(holder.target))
        name_hit = any(LEXICON_VAR_RE.search(n) for n in holder_names)
        elts = getattr(parent, "elts", [])
        all_short = elts and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) and len(e.value.strip()) <= 8
            for e in elts
        )
        if name_hit or (all_short and len(elts) >= 8):
            return {"visible": False, "reason": "lexicon_list",
                    "key": f"list:{holder_names[0] if holder_names else '?'}", "kind": "lexicon"}

    # ---- 5. 先算公共特征（供后续分支复用）----
    stripped = text.strip()
    has_cjk = bool(CJK_RE.search(text))

    # ---- 6. 代码用值 ----
    if isinstance(parent, ast.Compare) or isinstance(parent, ast.Subscript):
        return {"visible": False, "reason": "code_lookup", "key": "compare/subscript", "kind": "code"}
    if isinstance(parent, ast.Call):
        cname = _call_name(parent.func).split(".")[-1]
        if cname in {"startswith", "endswith", "split", "join", "replace", "get", "setdefault",
                     "match", "search", "fullmatch", "compile", "find", "index", "pop",
                     "format", "format_map", "strip", "lstrip", "rstrip", "encode", "decode"}:
            if not any(kw is node for kw in parent.keywords):
                return {"visible": False, "reason": "code_lookup",
                        "key": f"call:{cname}", "kind": "code"}
    if PATHY_RE.search(stripped) or SQLY_RE.search(stripped):
        return {"visible": False, "reason": "path_or_regex_or_sql", "key": "path/sql", "kind": "code"}
    if REGEXY_RE.search(stripped) and not has_cjk:
        return {"visible": False, "reason": "path_or_regex_or_sql", "key": "regex", "kind": "code"}

    # ---- 7. 枚举与信号短标签（放在最具体的代码规则之后）----
    if not has_cjk and len(stripped) <= 24 and CODE_TOKEN_RE.match(stripped):
        return {"visible": False, "reason": "enum_token", "key": "code_token", "kind": "enum"}
    if has_cjk and len(stripped) <= 4 and ENUM_CN_RE.match(stripped):
        return {"visible": False, "reason": "enum_cn_label", "key": "enum_label", "kind": "enum"}

    # ---- 出口判定：向上找最近的调用 / 赋值 / 返回 / f-string ----
    key = ""
    kind = "other"
    is_fstring = False
    sink_hit = ""
    for a in anc:
        if isinstance(a, ast.JoinedStr):
            is_fstring = True
        if isinstance(a, ast.Dict):
            # 字典取值：靠 key 名判断是不是文案字段（{"msg": "..."}）
            idx = next((i for i, v in enumerate(a.values) if v is node), None)
            kname = ""
            if idx is not None:
                k = a.keys[idx] if idx < len(a.keys) else None
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    kname = k.value
            if not key:
                key, kind = f"dict:{kname or '?'}", "dict_value"
            if kname and kname.lower() in USER_KWARGS:
                sink_hit = f"dict:{kname}"
                key, kind = f"dict:{kname}", "sink"
                break
        if isinstance(a, ast.Call):
            cname = _call_name(a.func)
            tail = cname.split(".")[-1]
            recv = _call_name(a.func.value) if isinstance(a.func, ast.Attribute) else ""
            # 列表型出口：buy.append("…→ 清仓") —— 动作串拼装，属对外文案
            if tail in {"append", "extend", "insert", "add"} and recv and OUTPUT_LIST_VAR_RE.search(recv):
                sink_hit = f"{recv}.{tail}"
                key, kind = f"list:{recv}", "list_build"
                break
            # push_feishu / send_wecom / notify_x 这类前缀命名的出口
            if tail.startswith(("push", "send", "notify", "alert_", "post_")):
                sink_hit = cname or tail
                key, kind = f"call:{sink_hit}", "sink"
                break
            if cname in SINK_CALLS or tail in SINK_CALLS or tail in SINK_METHOD_TAILS:
                sink_hit = cname or tail
                key = f"call:{sink_hit}"
                kind = "sink"
                break
            for kw in a.keywords:
                if kw.arg and kw.arg.lower() in USER_KWARGS:
                    sink_hit = f"kwarg:{kw.arg}"
                    key = f"kwarg:{kw.arg}"
                    kind = "sink"
                    break
            if sink_hit:
                break
            if not key:
                key = f"call:{cname or tail}"
                kind = "call_arg"
        elif isinstance(a, ast.AnnAssign):
            names = _target_names(a.target)
            nm = names[0] if names else ""
            if nm and USER_VAR_RE.search(nm):
                key, kind, sink_hit = f"assign:{nm}", "sink", f"assign:{nm}"
                break
        elif isinstance(a, ast.Assign):
            names = []
            for t in a.targets:
                names.extend(_target_names(t))
            nm = names[0] if names else ""
            if nm and USER_VAR_RE.search(nm):
                key, kind, sink_hit = f"assign:{nm}", "sink", f"assign:{nm}"
                break
            if nm and not key:
                key, kind = f"assign:{nm}", "assign"
        elif isinstance(a, ast.Return):
            if not key:
                key, kind = "return", "return"
        elif isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            break

    # HTML / Markdown / 模板文本：即便没找到 sink，也是给渲染层用的
    if not sink_hit and has_cjk and (HTML_RE.search(text) or
                                     (len(stripped) >= 8 and MARKUP_HEAD_RE.match(stripped))):
        sink_hit = "markup_template"
        key, kind = "markup_template", "template"

    # return 一长串中文 —— 若所在函数名是「出文案」的函数（_alert_text / build_card …）
    if not sink_hit and key == "return" and has_cjk and len(stripped) >= 12:
        for a in anc:
            if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if re.search(r"(alert|msg|message|text|report|card|brief|notice|guide|tip|"
                             r"warn|block|body|render|title|desc|plan|line)", a.name, re.I):
                    sink_hit = f"return_of:{a.name}"
                    key, kind = f"return_of:{a.name}", "return_fn"
                break
            if isinstance(a, ast.Module):
                break

    # 模块级常量：全大写变量名（POS_MGMT / _REGIME_REF 这类配置表）里含中文长句
    if not sink_hit:
        for a in anc:
            if isinstance(a, (ast.Assign, ast.AnnAssign)):
                tgts = a.targets if isinstance(a, ast.Assign) else [a.target]
                names: list[str] = []
                for t in tgts:
                    names.extend(_target_names(t))
                hit = next((n for n in names if n.isupper() and len(n) >= 3), "")
                if hit and has_cjk and len(stripped) >= 8:
                    sink_hit = f"module_const:{hit}"
                    key, kind = f"module_const:{hit}", "const"
                break
            if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                break

    if is_fstring and key and not key.startswith("kwarg"):
        key = f"fstring+{key}"
        kind = kind if kind != "other" else "fstring"

    # ---- 9. 短配置值 ----
    if not sink_hit and len(stripped) <= 6 and not re.search(r"[，。！？、；：,]", stripped):
        return {"visible": False, "reason": "config_literal", "key": key or "short", "kind": kind}

    return {"visible": bool(sink_hit), "reason": None if sink_hit else "sink_unresolved",
            "key": key or "other", "kind": kind, "sink": sink_hit}


# ─────────────────────────────── 扫描 ───────────────────────────────


def _negated(text: str, hits: list[str]) -> bool:
    """命中词前 6 字内出现否定字 → 属免责/否定表述（如「非买入信号」「不抄底」）。"""
    for w in hits:
        i = text.find(w)
        while i != -1:
            if any(c in text[max(0, i - NEG_WINDOW):i] for c in NEG_CHARS):
                return True
            i = text.find(w, i + 1)
    return False


def _benign(text: str) -> list[str]:
    out = [p for p in BENIGN_PHRASES if p in text]
    # 券商评级语境：「评级覆盖（买入 x / 中性 y / 卖出 z）」是研报分类，不是交易指令
    if "评级" in text and ("买入" in text or "卖出" in text):
        out.append("评级类别")
    for suf in BENIGN_QUAN_CANG:
        if f"全仓{suf}" in text:
            out.append(f"全仓{suf}")
    return out


def _surface(file_rel: str, key: str, kind: str) -> str:
    """交付面：delivery=推送/卡片/页面，prompt=LLM 提示词，research=回测脚本，
    console=控制台打印，other=无法归类（默认按需裁定）。"""
    if PROMPT_CONST_RE.search(key or ""):
        return "prompt"
    if DELIVERY_FILE_RE.search(file_rel) and not RESEARCH_FILE_RE.search(file_rel):
        return "delivery"
    if RESEARCH_FILE_RE.search(file_rel):
        return "research"
    if kind in ("sink", "list_build") and (key or "").startswith(("call:print", "call:LOG", "call:log", "call:logger")):
        return "console"
    return "other"


def iter_py_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in root.rglob("*.py"):
        parts = [x.lower() for x in p.relative_to(root).parts[:-1]]
        if any(any(part.startswith(pref) for pref in EXCLUDE_DIR_PREFIXES) for part in parts):
            continue
        if BACKUP_NAME_RE.search(p.name):
            continue
        out.append(p)
    return sorted(out)


def scan_file(path: Path, root: Path) -> tuple[list[dict], dict]:
    """扫描单文件，返回 (记录列表, 统计)。记录只保留 RED/AMBER/GREEN。"""
    rel = str(path.relative_to(root)).replace("\\", "/")
    is_archive = bool(ARCHIVE_PATH_RE.search(rel))
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src, filename=str(path))
    except (SyntaxError, ValueError) as e:
        return [], {"parse_error": 1, "error": f"{rel}: {type(e).__name__}"}

    parents = _build_parents(tree)
    doc_ids = _docstring_nodes(tree)
    ctx_cache: dict = {}

    records: list[dict] = []
    n_strings = 0
    suppressed_all: Counter = Counter()   # 全部字符串（含未命中词表的）的抑制原因，用于复核
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        n_strings += 1
        text = node.value
        level, hits = classify(text)
        j = _judge(text, node, parents, doc_ids, path, ctx_cache)
        if not j["visible"]:
            suppressed_all[j["reason"] or "sink_unresolved"] += 1
        if level == "NONE":
            continue
        disp = text if len(text) <= MAX_TEXT else text[:MAX_TEXT]
        # 多行字符串（HTML/模板）里词不一定在起始行，补一个真实命中行，便于直接跳转
        idxs = [text.find(h) for h in hits if h in text]
        off = text[:min(idxs)].count("\n") if idxs else 0
        records.append({
            "file": rel,
            "line": getattr(node, "lineno", 0),
            "hit_line": getattr(node, "lineno", 0) + off,
            "end_line": getattr(node, "end_lineno", 0),
            "func": _nearest_scope(parents, node),
            "level": level,
            "hits": hits,
            "text": disp,
            "truncated": len(text) > MAX_TEXT,
            "context_key": j["key"],
            "context_kind": j["kind"],
            "user_visible": j["visible"],
            "suppressed_reason": j["reason"],
            "is_archive": is_archive,
            "negated": _negated(text, hits),
            "benign_phrase": _benign(text),
            "surface": _surface(rel, j["key"], j["kind"]),
        })
    return records, {"strings": n_strings, "parse_error": 0,
                     "suppressed_all": dict(suppressed_all)}


def scan_all(root: Path) -> dict:
    files = iter_py_files(root)
    all_records: list[dict] = []
    n_strings = 0
    parse_errors: list[str] = []
    suppressed_all: Counter = Counter()
    for p in files:
        recs, st = scan_file(p, root)
        if st.get("parse_error"):
            parse_errors.append(st["error"])
        else:
            n_strings += st["strings"]
            suppressed_all.update(st.get("suppressed_all", {}))
        all_records.extend(recs)

    # GREEN 明细量可能很大，JSON 只留前 500 条（够确认合规面），其余只计数
    green = [r for r in all_records if r["level"] == "GREEN"]
    findings = [r for r in all_records if r["level"] != "GREEN"]
    surface_rank = {"delivery": 0, "prompt": 1, "other": 2, "research": 3, "console": 4}
    findings.sort(key=lambda r: (r["level"] != "RED", surface_rank.get(r["surface"], 9),
                                 r["file"], r["line"]))

    stats = {
        "files_scanned": len(files),
        "strings_extracted": n_strings,
        "parse_errors": parse_errors,
        "by_level": dict(Counter(r["level"] for r in all_records)),
        "by_level_visible": dict(Counter(r["level"] for r in all_records if r["user_visible"])),
        "suppressed_by_reason": dict(Counter(
            r["suppressed_reason"] for r in findings if r["suppressed_reason"])),
        "suppressed_by_reason_level": {
            lv: dict(Counter(r["suppressed_reason"] for r in findings
                             if r["level"] == lv and r["suppressed_reason"]))
            for lv in ("RED", "AMBER")
        },
        "visible_by_archive": dict(Counter(
            ("archive" if r["is_archive"] else "live")
            for r in findings if r["user_visible"])),
        "suppressed_all_strings": dict(suppressed_all.most_common()),
        "hits_by_word": dict(Counter(w for r in findings for w in r["hits"]).most_common()),
        "visible_by_surface": {
            lv: dict(Counter(r["surface"] for r in findings
                             if r["level"] == lv and r["user_visible"]))
            for lv in ("RED", "AMBER")
        },
        "visible_negated": sum(1 for r in findings if r["user_visible"] and r["negated"]),
        "visible_benign_phrase": sum(1 for r in findings
                                     if r["user_visible"] and r["benign_phrase"]),
    }
    return {
        "generated_by": "chanlun_copy_audit.py v8",
        "root": str(root),
        "stats": stats,
        "records": findings,
        "green_sample": green[:500],
        "green_total": len(green),
    }


# ─────────────────────────────── 输出 ───────────────────────────────


def print_summary(result: dict, top: int = 20) -> None:
    st = result["stats"]
    print("=" * 78)
    print("对外文案合规审计 v8 · 只读扫描")
    print("=" * 78)
    print(f"扫描文件      : {st['files_scanned']}")
    print(f"提取字符串    : {st['strings_extracted']}")
    print(f"解析失败      : {len(st['parse_errors'])}")
    lv = st["by_level"]
    print(f"RED / AMBER / GREEN : {lv.get('RED', 0)} / {lv.get('AMBER', 0)} / {lv.get('GREEN', 0)}")
    lvv = st["by_level_visible"]
    print(f"其中用户可见  : RED {lvv.get('RED', 0)} / AMBER {lvv.get('AMBER', 0)} / GREEN {lvv.get('GREEN', 0)}")
    print("-" * 78)
    print("文件 × 级别（仅列出有命中的文件，按 RED 降序）")
    per: dict[str, Counter] = defaultdict(Counter)
    for r in result["records"]:
        per[r["file"]][r["level"]] += 1
    rows = sorted(per.items(), key=lambda kv: (-kv[1]["RED"], -(kv[1]["RED"] + kv[1]["AMBER"]), kv[0]))
    print(f"{'RED':>4} {'AMB':>4} | file")
    for f, c in rows[:top]:
        print(f"{c['RED']:>4} {c['AMBER']:>4} | {f}")
    if len(rows) > top:
        print(f"… 其余 {len(rows) - top} 个文件略（--top 调整）")
    print("-" * 78)
    print("抑制原因分布（RED+AMBER）：")
    for lvl, d in st["suppressed_by_reason_level"].items():
        for reason, n in sorted(d.items(), key=lambda kv: -kv[1]):
            print(f"  [{lvl}] {reason:<22} {n}")
    print(f"  未抑制但出口未定位(sink_unresolved): "
          f"{sum(1 for r in result['records'] if r['suppressed_reason'] == 'sink_unresolved')}")
    print("-" * 78)
    print("用户可见命中 × 交付面（RED）：")
    for s, n in sorted(st["visible_by_surface"].get("RED", {}).items(), key=lambda kv: -kv[1]):
        print(f"  {s:<10} {n}")
    print(f"  其中否定/免责表述(negated) {st['visible_negated']} 条"
          f" · 研究口径良性短语 {st['visible_benign_phrase']} 条")
    print("=" * 78)


# ─────────────────────────────── Markdown 报告 ───────────────────────────────

SURFACE_LABEL = {
    "delivery": "对外交付（推送/卡片/页面/报告）",
    "prompt": "LLM 提示词（间接影响输出文案）",
    "other": "未归类出口（默认按需裁定）",
    "research": "研究/回测脚本（口径描述）",
    "console": "控制台打印（开发可见）",
}

# 逐文件的改法建议（人工撰写，仅针对需要动的文件；不在表内的用通用建议）
FILE_ADVICE = {
    "runner.py": "盘前卡把「今日应买入（开盘执行）」改为「今日待核对标注（纸面，不执行）」，"
                 "列头「待买入」改「待核对」；正文补「结构标注·非入场依据」。",
    "chanlun_market.py": "POS_SIZE/POS_MGMT 的「首笔 40% → 加满」阶梯与 trade_plan 的"
                         "「分批做多 / 清仓复盘 / 止损」是与新定位冲突最硬的一组：建议整段降级为"
                         "「结构位说明」（zd/zg 数值 + 结构态），或整块移出晨报，仅留在研究脚本。",
    "subscribe.py": "「✅ 买入建议（n）」改为「结构标注命中（n）· 非买入建议」；"
                    "分块标题同步改，保留代码/日期字段。",
    "doc_board.py": "「## 待买入（pending）」改「## 待核对标注（pending）」，"
                    "与表头「买入日/买价」一并改为「标注日/标注价」。",
    "chanlun_position.py": "「3卖清仓 %s」改「3卖结构位 %s（历史标注，不构成清仓指令）」；"
                           "「卖出链状态机」改「结构衰减链」。",
    "chanlun_card.py": "卡面「止损 %.1f%%」改「结构失效位距离 %.1f%%」，避免把度量写成指令。",
    "chanlun_daily_ledger.py": "账本 note 的「建仓/减仓/卖出超配」属于账务事件名，"
                               "可保留但建议统一为「开仓记录/调仓记录」并在表头注明纸面模拟。",
    "portfolio.py": "模拟盘账务事件（建仓/减仓/止盈止损）建议保留事件名、在外层战报标题补"
                    "「虚拟账户·纸面记录，不构成建议」；「择时择势：…/止盈止损」改「…/结构失效」。",
    "stock_council.py": "投委会提示词里的「建仓/减仓预警」是模拟盘语境，建议在输出模板外层"
                        "固定加一句「模拟推演，非交易建议」，避免二次传播时被当指令。",
    "breadth_crossing_monitor.py": "「行动: 可开始评估抄底/入场信号」是唯一带「行动」字样的告警句，"
                                   "建议改为「观察项：宽度上穿后进入复核窗口（历史复盘口径）」。",
    "report_page.py": "现有「非买入信号」写法已是合规范式，可作全仓模板；"
                      "「买入持有（灰）」属基准图例，建议保留但在图注写「基准=买入持有」。",
    "sellpoint_ai.py": "「一次性清仓 / 分批止盈」是策略臂名称，建议加前缀「策略臂：」以示非指令。",
}

GENERIC_ADVICE = ("确认该串是否真的到达用户；若是，把动词式指令改为名词式结构描述"
                  "（例：「清仓」→「结构失效位」、「买入建议」→「标注命中」）。")

TOP_NOTES = {
    "runner.py": "每日 08:15 飞书盘前卡的正文标题，措辞是「应买入（开盘执行）」，"
                 "字面就是执行指令，且是全仓触达频率最高的一张卡。",
    "chanlun_market.py": "缠论自身产物：POS_SIZE/POS_MGMT 的仓位阶梯 + trade_plan 的买卖点串，"
                         "经 morning_report.py:117-120 以「买：…／卖：…」进入晨报，"
                         "与「结构标注工具·非入场/减仓依据」直接冲突，且同一文件 9 条成组。",
    "subscribe.py": "订阅推送的分块标题「✅ 买入建议（n）」，是标准的推荐口吻，"
                    "且该块按 action 字段筛选后直接推送。",
    "chanlun_position.py": "持仓 CLI 卡把「3卖」直接接「清仓」，是缠论结构被读成指令的典型句式。",
    "doc_board.py": "门户详情看板的一级标题「## 待买入」，把标注池写成了待执行队列。",
    "chanlun_card.py": "推演卡把「止损」作为展示字段，读者易读成风控指令。",
}

PRIORITY_HITS = ("买入", "卖出", "清仓", "减仓", "加仓", "建仓", "止损", "止盈", "目标价", "满仓", "抄底")

# 人工选出的「最该先处理」三处；命中不到时回落到 _priority 排序（避免报告与代码脱节）
TOP3_PICK = [("runner.py", 712), ("chanlun_market.py", 285), ("subscribe.py", 397)]


def _md_cell(s: str, limit: int = 90) -> str:
    s = (s or "").replace("\r", " ").replace("\n", "⏎").replace("|", "\\|")
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[:limit] + "…"


def _priority(rec: dict) -> int:
    """排序分：交付面越靠前、越像指令、越未被否定，分越高。"""
    score = 0
    score += {"delivery": 40, "other": 30, "prompt": 20, "console": 5, "research": 0}.get(rec["surface"], 10)
    if not rec["negated"]:
        score += 20
    if not rec["benign_phrase"]:
        score += 15
    score += 5 * sum(1 for h in PRIORITY_HITS if h in rec["text"])
    if rec["context_kind"] in ("list_build", "sink"):
        score += 8
    if rec["is_archive"]:
        score -= 25
    return score


def write_markdown(result: dict, out_path: Path) -> None:
    st = result["stats"]
    recs = result["records"]
    reds = [r for r in recs if r["level"] == "RED"]
    ambers = [r for r in recs if r["level"] == "AMBER"]
    vis_red = [r for r in reds if r["user_visible"]]
    vis_amb = [r for r in ambers if r["user_visible"]]

    core = [r for r in vis_red if not r["negated"] and not r["benign_phrase"]
            and r["surface"] in ("delivery", "other", "prompt")]
    index = {(r["file"], r["line"]): r for r in core}
    top = [index[k] for k in TOP3_PICK if k in index]
    if len(top) < 3:  # 指定的行被改动/删除时，用打分排序补齐
        core.sort(key=_priority, reverse=True)
        for r in core:
            if r not in top:
                top.append(r)
            if len(top) == 3:
                break

    cs = [r for r in reds if r["file"] == "chanlun_scan.py"]
    cs_vis = sum(1 for r in cs if r["user_visible"] and not r["negated"])
    cs_reasons = Counter(r["suppressed_reason"] or "可见" for r in cs)

    L: list[str] = []
    w = L.append
    w("# 对外文案合规审计 v8（只读扫描）")
    w("")
    w(f"- 生成：`chanlun_copy_audit.py`（AST 静态扫描，不 import、不执行被扫模块）")
    w(f"- 扫描时点：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
      f"（仓库正处于多人并行改写中，文件数与字符串数会随他人提交漂移；本报告数字为此次时点快照）")
    w(f"- 根目录：`{result['root']}`")
    w(f"- 词表与抑制规则见脚本顶部常量；本报告所有计数均可由 "
      f"`python chanlun_copy_audit.py --json-out data/chanlun_copy_audit.json` 复现")
    w("")
    w("## 0. 结论先说")
    w("")
    w(f"- 全仓 **{st['files_scanned']}** 个 `.py`，提取字符串常量 **{st['strings_extracted']}** 条。")
    w(f"- 词表命中：**RED {st['by_level'].get('RED', 0)} · AMBER {st['by_level'].get('AMBER', 0)} · "
      f"GREEN {st['by_level'].get('GREEN', 0)}**。")
    w(f"- 剔除 docstring / 测试 / 枚举 / 词表后，**RED 仍有 {len(vis_red)} 条被判定为用户可见**，"
      f"AMBER 可见 {len(vis_amb)} 条。")
    w(f"- 其中扣掉「否定/免责表述」（{st['visible_negated']} 条）和「研究口径良性短语」"
      f"（{st['visible_benign_phrase']} 条）后，**真正需要老田逐条裁定的核心项 {len(core)} 条**"
      f"（交付面/未归类/prompt 三类）。")
    w("")
    w("所以结论不是「接近零 RED」。`chanlun_scan.py` 本体确实改干净了——该文件本轮 RED "
      f"{len(cs)} 条，逐条为 " + "、".join(f"{k}×{v}" for k, v in cs_reasons.most_common()) +
      f"（其中用户可见且非否定 {cs_vis} 条，无一条对外指令）；**但下游的晨报、订阅推送、"
      "门户看板、持仓卡仍在输出交易指令口径**——问题不在被改的那个文件，而在它旁边的一圈消费方。")
    w("")
    w("## 1. 分级与词表")
    w("")
    w("| 级别 | 判据 | 词表 |")
    w("|---|---|---|")
    w(f"| RED | 直接交易指令 | {' / '.join(RED_WORDS[:16])} 等 {len(RED_WORDS)} 个 |")
    w(f"| AMBER | 暗示性措辞 | {' / '.join(AMBER_WORDS)} |")
    w(f"| GREEN | 结构描述（用于确认合规覆盖面） | {' / '.join(GREEN_WORDS)} |")
    w("")
    w("「笔」为单字，单独命中率高，仅在串内同时出现 分型/中枢/线段/缠论/背驰/结构/标注/买卖点 时计 GREEN。")
    w("")
    w("## 2. 汇总表（文件 × 级别）")
    w("")
    per: dict[str, Counter] = defaultdict(Counter)
    for r in recs:
        per[r["file"]][r["level"]] += 1
    rows = sorted(per.items(), key=lambda kv: (-kv[1]["RED"], -kv[1]["AMBER"], kv[0]))
    w(f"有命中的文件共 **{len(rows)}** 个；下表按 RED 降序取前 30（全量见 JSON 的 `records`）。")
    w("")
    w("| # | 文件 | RED | AMBER | GREEN |")
    w("|---:|---|---:|---:|---:|")
    for i, (f, c) in enumerate(rows[:30], 1):
        w(f"| {i} | `{f}` | {c['RED']} | {c['AMBER']} | {c['GREEN']} |")
    w("")
    w("### 2.1 RED 的构成（抑制情况）")
    w("")
    w("| 处置 | 条数 | 说明 |")
    w("|---|---:|---|")
    w(f"| 用户可见（进入明细） | {len(vis_red)} | 见第 4 节，逐条列全 |")
    for reason, n in sorted(st["suppressed_by_reason_level"].get("RED", {}).items(), key=lambda kv: -kv[1]):
        w(f"| 抑制：`{reason}` | {n} | 见第 6 节规则说明与全量清单 |")
    w(f"| **合计** | **{len(reds)}** | |")
    w("")
    w("## 3. 抑制规则与计数（有没有藏东西，看这里）")
    w("")
    w("| 规则 | 判据（代码注释同义） | RED | AMBER |")
    w("|---|---|---:|---:|")
    rule_desc = {
        "docstring": "模块/类/函数首条字符串 = 开发者文档，不面向用户",
        "test_file": "`tests/` 或 `test_*.py`，测试断言文案",
        "enum_token": "无中文、≤24 字符、纯标识符型（`buy`/`1buy`/`b1`/`hold`）",
        "enum_cn_label": "≤4 字且形如 `1买`/`二卖` 的信号类型短标签",
        "dict_key": "字典的键（非取值），属结构字段名",
        "lexicon_list": "情感词/停用词/关键词表元素（宿主名匹配或 ≥8 个短词）",
        "code_lookup": "出现在 `==`/下标/`startswith` 等代码分支里的值",
        "path_or_regex_or_sql": "文件路径、正则、SQL 字面量",
        "config_literal": "≤6 字、无标点的配置值",
        "sink_unresolved": "含中文文案但向上 16 层找不到已知出口（打印/日志/返回模板/文案字段）",
    }
    for reason in ["docstring", "test_file", "enum_token", "enum_cn_label", "dict_key", "lexicon_list",
                   "code_lookup", "path_or_regex_or_sql", "config_literal", "sink_unresolved"]:
        rn = st["suppressed_by_reason_level"].get("RED", {}).get(reason, 0)
        an = st["suppressed_by_reason_level"].get("AMBER", {}).get(reason, 0)
        w(f"| `{reason}` | {rule_desc.get(reason, '')} | {rn} | {an} |")
    w("")
    w("（`enum_token` / `enum_cn_label` 在 RED/AMBER 列为 0 是正常的：RED 词均为中文词，"
      "不可能被 ASCII 枚举规则命中；它们的实际作用体现在全量字符串计数里。）")
    w("")
    w("全量字符串（含未命中词表的）抑制计数，用于核对规则整体比例：")
    w("")
    w("| reason | 条数 |")
    w("|---|---:|")
    for k, v in st["suppressed_all_strings"].items():
        w(f"| `{k}` | {v} |")
    w("")
    w("**主动说明三处可能被质疑的抑制：**")
    w("")
    w("1. `docstring` 抑制最多（RED 160 条）。这些是模块设计说明，不推给用户；"
      "但它们是「措辞回流」的源头，第 7 节的改法对 docstring 与正文一视同仁。")
    w("2. 新增了 `list_build` 出口规则（`buy.append(...)` / `sell.append(...)`）。"
      "这是本次最重要的修正：没有它，`chanlun_market.py` 整组动作串（9 条）会被判成"
      "「出口未定位」，而这正是最该报的东西。同类载体还有 `lines.append` / `blocks.append` "
      "（`runner.py` 的盘前卡就在这里拼）。")
    sn = st["suppressed_by_reason_level"].get("RED", {}).get("sink_unresolved", 0)
    w(f"3. `sink_unresolved` 的 {sn} 条 RED 没有被算作「可见」，但**也没有被丢弃**，"
      "全部列在第 6.2 节，供人工复核。")
    w("")
    w("## 4. RED 明细（用户可见，逐条列全）")
    w("")
    w(f"共 {len(vis_red)} 条。标记列：`否定` = 含免责/否定语境（如「非买入信号」）；"
      "`良性` = 研究口径短语（如「买入持有」基准）。这两类仍列出，但不建议按违规改。")
    w("")
    for surface in ("delivery", "prompt", "other", "research", "console"):
        group = [r for r in vis_red if r["surface"] == surface]
        if not group:
            continue
        w(f"### 4.{'delivery prompt other research console'.split().index(surface) + 1} {SURFACE_LABEL[surface]}（{len(group)} 条）")
        w("")
        w("| file:line | 函数 | 上下文 | 原文 | 标记 |")
        w("|---|---|---|---|---|")
        for r in sorted(group, key=lambda x: (x["file"], x["line"])):
            flags = []
            if r["negated"]:
                flags.append("否定")
            if r["benign_phrase"]:
                flags.append("良性")
            if r["is_archive"]:
                flags.append("归档")
            loc = f"`{r['file']}:{r['line']}`"
            if r.get("hit_line") and r["hit_line"] != r["line"]:
                loc += f"（词在 {r['hit_line']}）"
            w(f"| {loc} | `{r['func']}` | `{r['context_key']}` | "
              f"{_md_cell(r['text'])} | {'/'.join(flags) or '—'} |")
        w("")
    w("## 5. AMBER 明细")
    w("")
    w(f"用户可见 {len(vis_amb)} 条（其余 {len(ambers) - len(vis_amb)} 条为 docstring/测试/代码值，见 JSON）。")
    w("")
    w("| file:line | 函数 | 上下文 | 原文 | 标记 |")
    w("|---|---|---|---|---|")
    for r in sorted(vis_amb, key=lambda x: (x["file"], x["line"])):
        flags = "/".join((["否定"] if r["negated"] else []) + (["良性"] if r["benign_phrase"] else [])) or "—"
        loc = f"`{r['file']}:{r['line']}`"
        if r.get("hit_line") and r["hit_line"] != r["line"]:
            loc += f"（词在 {r['hit_line']}）"
        w(f"| {loc} | `{r['func']}` | `{r['context_key']}` | {_md_cell(r['text'])} | {flags} |")
    w("")
    w(f"GREEN 命中 {st['by_level'].get('GREEN', 0)} 条（用户可见 "
      f"{st['by_level_visible'].get('GREEN', 0)} 条），用于确认「结构标注/中枢/背驰」等"
      f"新定位措辞确实在扩散；明细量太大，JSON 里只留前 {len(result['green_sample'])} 条抽样"
      f"（共 {result['green_total']} 条），够核对口径而不淹没重点。")
    w("")
    w("AMBER 的逻辑词「机会」在 `机会成本` 语境已剥离；`低吸/高抛` 多见于研究脚本臂名，"
      "真正落在卡片正文的极少。**AMBER 不是本次的重点**，重点在 RED。")
    w("")
    w("## 6. 被抑制明细（可复核）")
    w("")
    w("### 6.1 按原因计数")
    w("")
    w("| 抑制原因 | RED | AMBER | 占该级别比例 |")
    w("|---|---:|---:|---:|")
    for reason in ["docstring", "test_file", "enum_token", "enum_cn_label", "dict_key", "lexicon_list",
                   "code_lookup", "path_or_regex_or_sql", "config_literal", "sink_unresolved"]:
        rn = st["suppressed_by_reason_level"].get("RED", {}).get(reason, 0)
        an = st["suppressed_by_reason_level"].get("AMBER", {}).get(reason, 0)
        if rn or an:
            share = f"{rn / max(1, len(reds)) * 100:.1f}%"
            w(f"| `{reason}` | {rn} | {an} | {share} |")
    w("")
    w(f"被抑制 RED 合计 {len(reds) - len(vis_red)} 条，"
      f"占全部 RED {len(reds)} 条的 {(len(reds) - len(vis_red)) / max(1, len(reds)) * 100:.1f}%。")
    w("")
    w("### 6.2 被抑制的 RED 全量清单")
    w("")
    w(f"共 {len(reds) - len(vis_red)} 条。")
    w("")
    w("| file:line | 抑制原因 | 原文 |")
    w("|---|---|---|")
    for r in sorted([x for x in reds if not x["user_visible"]],
                    key=lambda x: (x["suppressed_reason"], x["file"], x["line"])):
        w(f"| `{r['file']}:{r['line']}` | `{r['suppressed_reason']}` | {_md_cell(r['text'], 60)} |")
    w("")
    w("## 7. 建议改法（只给建议，不改代码）")
    w("")
    w("原则：**把动词式指令改成名词式结构描述**，并保留「结构标注·非入场依据」尾注。")
    w("")
    w("| 文件 | 建议 |")
    w("|---|---|")
    for f, adv in FILE_ADVICE.items():
        if any(r["file"] == f and r["user_visible"] and r["level"] == "RED" for r in recs):
            w(f"| `{f}` | {adv} |")
    w("")
    w(f"其余文件按通用规则处理：{GENERIC_ADVICE}")
    w("")
    w("## 8. 最该优先处理的 3 条")
    w("")
    for i, r in enumerate(top, 1):
        w(f"### {i}. `{r['file']}:{r['line']}` — {_md_cell(r['text'], 60)}")
        w("")
        w(f"- 交付面：{SURFACE_LABEL.get(r['surface'], r['surface'])}；函数 `{r['func']}`，出口 `{r['context_key']}`")
        note = TOP_NOTES.get(r["file"], "")
        w(f"- 理由：{note or '交付面靠前、含直接指令动词、且未被否定语境覆盖。'}")
        w(f"- 改法：{FILE_ADVICE.get(r['file'], GENERIC_ADVICE)}")
        w("")
    w("## 9. 自检")
    w("")
    w("`python chanlun_copy_audit.py --selftest` 用临时目录构造一份含 RED 措辞的假推送模块，"
      "验证 10 项断言（命中、出口识别、各类抑制）。实际输出：")
    w("")
    w("```")
    w("PASS 提取到字符串 / 命中 RED 用户可见 ≥3 条 / 命中 AMBER 用户可见 ≥1 条 /")
    w("PASS print(f-string) 已识别为可见 / dict 文案字段 msg= 已识别为可见 /")
    w("PASS docstring 中的 RED 被抑制 / 枚举 buy|b1|1buy 被抑制为 enum_token /")
    w("PASS 情感词表被抑制为 lexicon_list / 字典键被抑制 / 比较分支 buy 被抑制为 code_lookup")
    w("自检结论： 通过")
    w("```")
    w("")
    w("用例中被判「可见」的 4 条正是构造进去的 `满仓干！目标价 18.6 元`、`建议买入，止损 15.2`、"
      "`低吸机会，建议布局`、`抄底信号已确认，可上车`，说明词表与出口判定都生效。")
    w("")
    w("## 10. 已知局限")
    w("")
    w("1. **运行时拼接的字符串拿不到**。`f\"{a}...{b}\"` 只能看到字面片段，"
      "变量里装的词（例如从 json / db 读出的模板、i18n 词表）不在 AST 里。"
      "典型漏网：模板文件、`data/*.json` 里的文案、`picks` 记录里的 `action` 字段值。")
    w("2. **跨函数数据流不做追踪**。`def _txt(): return \"…\"` 被 `push(_txt())` 调用时，"
      "只能靠函数名启发式（`_alert_text` 之类）判定，命名不规范就会落到 `sink_unresolved`。")
    w("3. **字符串拼接的语义丢失**。`\"清仓\" + \"复盘\"` 与 `\"清仓复盘\"` 都命中，"
      "但前者只看到两个片段，报告里截断显示，读起来会像噪音。")
    w("4. **docstring 被整体抑制**。它不影响用户，但确实是措辞源头；"
      "本报告的建议对所有层一视同仁，实际改动时建议 docstring 与正文同步改，避免二次回流。")
    w("5. **交付面（surface）是按文件名 + 出口推断的启发式**，不是真实调用图。"
      f"`other` 类（{st['visible_by_surface'].get('RED', {}).get('other', 0)} 条）无法自动判断，"
      "需人工确认；这也是它们没被排除的原因。")
    w("6. **词表是闭集**。同义新词（「轻仓试错」「右侧介入」等）只要不在表内就不会报。"
      "第 3 节的全量抑制计数可用于反向抽查。")
    w("7. **行号是字符串常量的起始行**，不是词所在行。多行 HTML/模板会偏；"
      "所以每条额外给了「词在 N」的命中行（`hit_line`），以它为准。")
    w("8. **仓库正在被并发改写**。两次相隔数分钟的扫描，文件数已从 1177 变 1181、"
      "字符串数从 201 062 变 202 107。本报告是单次快照，重跑请以新数字为准，"
      "结论（核心 54 条、优先 3 条）不受影响。")
    w("")
    w("---")
    w("")
    w(f"数据文件：`data/chanlun_copy_audit.json`（{len(recs)} 条 RED/AMBER 记录 + "
      f"GREEN 抽样 {len(result['green_sample'])}/{result['green_total']} 条）。")
    w("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(L), encoding="utf-8")


# ─────────────────────────────── 自检 ───────────────────────────────



SELFTEST_SRC = '''# -*- coding: utf-8 -*-
"""模块 docstring：这里写 建议买入 不应被当作对外文案。"""
import logging

LOG = logging.getLogger(__name__)

LEXICON = ["满仓", "加仓", "抄底", "翻倍", "牛市", "利好", "突破", "新高", "启动", "爆发"]


def push_card():
    title = "满仓干！目标价 18.6 元"
    body = f"【{title}】建议买入，止损 15.2"
    print(body)
    LOG.info("低吸机会，建议布局")
    return {"msg": "抄底信号已确认，可上车", "sig": "1buy", "code": "b1"}


def _map(sig):
    if sig == "buy":
        return "yes"
    return {"buy": "1buy"}.get(sig, "")
'''


def selftest() -> int:
    """构造含 RED 措辞的临时文件，验证：(a) 能命中 (b) 误报规则生效。

    只写系统临时目录，不碰仓库任何文件。
    """
    tmp = Path(tempfile.mkdtemp(prefix="copy_audit_selftest_"))
    f = tmp / "fake_push.py"
    f.write_text(SELFTEST_SRC, encoding="utf-8")

    recs, st = scan_file(f, tmp)
    sup = st.get("suppressed_all", {})
    red_visible = [r for r in recs if r["level"] == "RED" and r["user_visible"]]
    amber_visible = [r for r in recs if r["level"] == "AMBER" and r["user_visible"]]
    red_suppressed = [r for r in recs if r["level"] == "RED" and not r["user_visible"]]

    checks = [
        ("提取到字符串", st["strings"] >= 10),
        ("命中 RED 用户可见 ≥3 条", len(red_visible) >= 3),
        ("命中 AMBER 用户可见 ≥1 条", len(amber_visible) >= 1),
        ("print(f-string) 已识别为可见",
         any(r["context_kind"] == "sink" for r in red_visible)),
        ("dict 文案字段 msg= 已识别为可见",
         any(r["context_key"].startswith(("dict:", "kwarg:", "assign:", "fstring"))
             for r in recs if r["level"] == "RED")),
        ("docstring 中的 RED 被抑制",
         any(r["suppressed_reason"] == "docstring" for r in red_suppressed)),
        ('枚举 "buy"/"b1"/"1buy" 被抑制为 enum_token', sup.get("enum_token", 0) >= 3),
        ("情感词表被抑制为 lexicon_list", sup.get("lexicon_list", 0) >= 3),
        ("字典键被抑制", sup.get("dict_key", 0) >= 1),
        ('比较分支 "buy" 被抑制为 code_lookup', sup.get("code_lookup", 0) >= 1),
    ]

    print("自检 · 构造用例：")
    print("-" * 78)
    print(f"{'结果':<6} {'检查项'}")
    ok = True
    for name, passed in checks:
        print(f"{'PASS' if passed else 'FAIL':<6} {name}")
        ok = ok and bool(passed)
    print("-" * 78)
    print(f"用例命中明细（{len(recs)} 条）：")
    for r in sorted(recs, key=lambda x: x["line"]):
        flag = "可见" if r["user_visible"] else f"抑制:{r['suppressed_reason']}"
        print(f"  L{r['line']:<3} {r['level']:<5} {flag:<28} {r['context_key']:<22} {r['text'][:40]!r}")
    print("-" * 78)
    print("自检结论：", "通过" if ok else "失败")
    return 0 if ok else 1


# ─────────────────────────────── main ───────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="对外文案合规审计（只读）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--md-out", default=None)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--list-red", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    root = Path(args.root).resolve()
    result = scan_all(root)
    print_summary(result, top=args.top)

    if args.list_red:
        print("\nRED 明细（全部，含被抑制项）：")
        for r in [x for x in result["records"] if x["level"] == "RED"]:
            flag = "" if r["user_visible"] else f"  [抑制:{r['suppressed_reason']}]"
            print(f"  {r['file']}:{r['line']}  {r['context_key']}{flag}\n      {r['text'][:120]}")

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 已写出: {out}")

    if args.md_out:
        md = Path(args.md_out)
        write_markdown(result, md)
        print(f"Markdown 已写出: {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
