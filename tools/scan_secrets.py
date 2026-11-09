#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan_secrets — 防泄漏只读扫描器（pre-commit 闸门）

用途：
  在提交/共享前机械检查工作区文件中的疑似真实凭据。只读，绝不修改被扫文件。

用法：
  python tools/scan_secrets.py                # 扫 git diff --cached 的暂存文件（pre-commit 默认）
  python tools/scan_secrets.py --all          # 扫全仓 tracked 文件（基线盘点）
  python tools/scan_secrets.py path1 path2    # 扫指定文件/目录（自验证用，可脱离 git）

退出码：0 = 干净（或仅有豁免项）；1 = 存在未豁免命中。

红线：本脚本任何输出（含报告预览）只显示脱敏片段（保留前4后2字符，短串仅前2），
      绝不回显完整凭据。
"""
import argparse
import math
import os
import re
import subprocess
import sys

# 让 Windows 控制台在 UTF-8 环境下稳定输出中文
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---------------------------------------------------------------------------
# 配置区：本仓已知敏感字面量（发现新的请追加；注意：命中输出会自动脱敏）。
# 说明：为避免脚本源码本身成为敏感字面量的传播源，此处仅存片段、运行时拼装。
_KNOWN_LITERAL_PARTS = [("TianF" + "eng", "20" + "26")]
KNOWN_LITERALS = [a + b for a, b in _KNOWN_LITERAL_PARTS]

# 本就应含密、按路径豁免的文件模式（仍会列入"豁免清单"供人工复核）
EXEMPT_PATH_PATTERNS = [
    r"(^|/)\.env(\..*)?$",          # .env / .env.local / .env.production
    r"(^|/)\.envrc$",
    r"(^|/)secrets?(/|$)",          # secrets/ 目录
    r"\.env\.example$",             # 示例 env（配合占位符过滤）
    # 2026-10-26 EXEC-R39：基线豁免清单本身含大量 path:rule 形态的 key 文本，
    # 扫描它会自指命中（如长文档名触发 HIGH_ENTROPY）；它不是凭据载体，按路径豁免。
    r"(^|/)scan_secrets_allowlist\.tsv$",
    # 自动生成的仓库 Wiki 会列模块文件名；非凭据载体，按生成物路径豁免。
    r"(^|/)project_wiki\.md$",
    # 2026-10-31 K-ORG-20261031-PLANREG：计划台账生成物列出计划路径/标题，
    # 文件名可能触发 HIGH_ENTROPY 误报；它不是凭据载体，按生成物路径豁免。
    r"(^|/)docs/plans/PLAN_REGISTER(_ALL)?_[0-9]+\.(json|md)$",
    # 2026-11-02 错误修正：dw_snapshot 生成物内嵌 hypotheses 的 evidence 文件名
    # （如 research/reports/H13_xxx_20261029.md），每日重写且文件名含哈希段会触发
    # HIGH_ENTROPY 误报；它是只读展示数据生成物，非凭据载体，按生成物路径豁免。
    r"(^|/)portal/static/dw_snapshot\.json$",
]

# 测试桩目录/文件标记（按路径豁免）
TEST_PATH_MARKERS = re.compile(
    r"(^|/)(tests?|fixtures?|mocks?|samples?|examples?|stubs?|spec)(/|$)"
    r"|(^|/)test_[^/]*\.py$|(^|/)[^/]*_test\.py$|(^|/)conftest\.py$",
    re.IGNORECASE,
)
# 命中行内自带的"假密钥"标记（按行豁免）
TEST_LINE_MARKERS = re.compile(
    r"(?i)(fake|dummy|placeholder|not[-_ ]?a[-_ ]?real|sample[_-]?key|fixture|示例|假密钥|测试用)",
)

# 占位符/引用形态（非真实凭据，跳过）
PLACEHOLDER_RE = re.compile(
    r"(?i)(\$\{|\{\{|<[^>]*>|os\.environ|getenv|process\.env|your[_-]|changeme|"
    r"xxx+|\*\*\*|＜[^＞]*＞|示例|请输入|此处填|待填|redacted|<masked>)"
)

# 第三方/依赖文件：默认跳过（其中哈希、证书、压缩 JS 是高熵规则的最大噪声源），
# 统计入"第三方跳过"清单，可用 --include-vendor 纳入扫描
VENDOR_PATH_PATTERNS = [
    r"(^|/)pylibs/",
    r"(^|/)vendor/",
    r"(^|/)node_modules/",
    r"(^|/)_study/",                # 第三方学习仓整体
    r"(^|/)bower_components/",
    r"\.dist-info/",
    r"(^|/)package-lock\.json$",
    r"(^|/)yarn\.lock$",
    r"(^|/)poetry\.lock$",
    r"\.min\.js$",
    r"cacert\.pem$",
]

MAX_LINE_PREVIEW = 120
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB 以上跳过（记录 skipped）

# ---------------------------------------------------------------------------
# 规则定义
# 规则元组：(规则名, 编译后正则, 捕获组序号或 None(整段匹配即密))
RULES = [
    # 私钥头
    ("PRIVATE_KEY",
     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
     None),
    # 已知字面量（下方单独处理，见 KNOWN_LITERALS）
    # 显式赋值型 token / api_key / secret
    ("TOKEN_ASSIGN",
     re.compile(
         r"(?i)(?:api[_-]?key|apikey|secret(?:[_-]?key)?|access[_-]?key|"
         r"client[_-]?secret|auth[_-]?token|token|tushare)\s*[:=：>]{1,2}\s*"
         r"[\"']?([A-Za-z0-9_\-\.+/]{8,})[\"']?"),
     1),
    # 密码字面（含中文文档写法：密码：xxx / 密码为xxx / password = xxx）
    ("PASSWORD_LITERAL",
     re.compile(
         r"(?i)(?:密码|口令|password|passwd|pwd)\s*(?:[:=：]|为|是)\s*"
         r"[\"']?([A-Za-z0-9_\-@#$%^&*./+!~?=]{4,64})[\"']?"),
     1),
    # 手机号（中国大陆）
    ("PHONE_CN",
     re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
     None),
    # 内网 IP（配合"同文件含凭据"触发 INTRANET_IP_WITH_CREDS）
    ("_INTRANET_IP",
     re.compile(
         r"(?<![\d.])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
         r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|"
         r"192\.168\.\d{1,3}\.\d{1,3})(?![\d.])"),
     None),
    # 高熵候选串（base64/hex 形态，长度>=20；不含点号/斜杠）
    ("HIGH_ENTROPY",
     re.compile(r"[A-Za-z0-9_=]{20,}"),
     None),
]

HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
COMMIT_CTX_RE = re.compile(r"(?i)(commit|sha|hash|版本号|revision|git\s+[0-9a-f]{7,40})")
# 凭据语境词：纯 hex 长串（md5/sha/token 难区分）仅在此语境下才视为敏感
CRED_CONTEXT_RE = re.compile(
    r"(?i)(token|api[_-]?key|secret|password|passwd|pwd|密码|口令|authorization|bearer|credential|密钥)")

# 2026-11-07 K-ORG-20261107-ENTROPY：文件名主体被当成高熵候选时的右侧闭合判据。
# 只认常见文本/代码扩展名，避免把 `abc/def123456789ghi`（无扩展名）也算路径成分。
FILE_EXT_RE = re.compile(
    r"\.(?:py|md|json|tsv|csv|txt|log|ya?ml|js|ts|ps1|html?|sh|sql|cfg|ini|toml|xml|java|go)\b")


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


BASE64_DATA_URI_RE = re.compile(r"(?i)(data:image|base64,)")
# 手机号误报语境：文件大小/计数类数字
PHONE_NOISE_CTX_RE = re.compile(
    r"(?i)(size|bytes|字节|容量|timestamp|count|total|limit|created_at|mtime|unix|epoch|时间|毫秒|微秒|秒级)")


def looks_like_identifier(cand: str) -> bool:
    """形似代码标识符而非密钥：短段点分名（uuid.uuid4）、SCREAMING_CASE 环境变量名。"""
    if "." in cand and all(len(s) <= 12 for s in cand.split(".")):
        return True
    if re.fullmatch(r"[A-Z0-9_]+", cand):
        return True
    return False


# 2026-10-31 WO-SCANFP-FLAG-20261031：布尔开关赋值（标识符=True/False）窄豁免。
# HIGH_ENTROPY 字符集 [A-Za-z0-9_=] 会把 docstring/注释里的
# `warn_unmatched_over_20pct=True` 整段拼成候选（含 `=`、熵约 4.1）→ 误判密钥
# 并阻塞合并。真实高熵 token 不含 `=True|False` 尾巴，故只放行「整串恰为
# name=True/False 且 name/整行无凭据语境」的形态；其余规则一律不动。
_FLAG_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(True|False)$")
_CRED_NAME_RE = re.compile(
    r"(?i)(token|api[_-]?key|apikey|secret|password|passwd|pwd|credential|"
    r"authorization|bearer|密钥|口令|密码)")


def is_boolean_flag_assign(cand: str, line: str = "") -> bool:
    """「标识符=True|False」布尔开关赋值 → 非密钥（窄豁免，2026-10-31 SCANFIX）。

    只放行候选**整串**恰为 ``name=True`` / ``name=False`` 的形态（高熵规则把
    ``=`` 纳入字符集才会误拼出这种尾巴）；``name`` 或整行命中凭据语境词
    （token/key/secret/password/密码…）时不放行——如 ``api_key_*_enabled=True``
    这类带凭据语义的参数名照旧走原规则，避免一刀切免扫。
    """
    m = _FLAG_ASSIGN_RE.match(str(cand or ""))
    if not m:
        return False
    name = m.group(1)
    if _CRED_NAME_RE.search(name) or _CRED_NAME_RE.search(str(line or "")):
        return False
    return True


def is_high_entropy_candidate(cand: str, line: str) -> bool:
    """高熵规则判定：len>=20、base64/hex 字符集；剔除 UUID、纯数字、
    含点号的标识符/路径形态、commit 上下文 40 位 hex（git sha）、
    base64 图片 data URI（SVG/PNG 内嵌资源）、KEY=value 连写赋值片段（第6轮）、
    PascalCase 长标识符类名（第7轮）、标识符=True|False 布尔开关（第8轮）、
    文档里引用的带扩展名路径成分（第9轮）。"""
    if len(cand) < 20:
        return False
    # 2026-11-01 SCANFIX：JSON 字符串字段 `"id": "..."` 是标识符/策略 id，不是凭据。
    # 典型误报：strategy_registry / selection_daily 里的 S03_H18_forecast_revision_watch。
    # 只放行值为 id 的整串，不扩大到任意 JSON 字符串。
    if re.search(r'"id"\s*:\s*"' + re.escape(cand) + r'"', line):
        return False
    # 第8轮（2026-10-31）：docstring/注释里的布尔开关参数 `name=True|False`
    # 被高熵字符集误拼成候选 → 窄豁免（真实 token 不会以 =True/False 结尾）。
    if is_boolean_flag_assign(cand, line):
        return False
    if UUID_RE.match(cand) or cand.isdigit():
        return False
    if re.match(r"^[A-Z][A-Z0-9_]*=", cand):
        return False  # KEY=value 连写（如 DATABASE_URL=postgresql）是赋值片段，非密钥
    if BASE64_DATA_URI_RE.search(line):
        return False  # 内嵌图片资源，非凭据
    if looks_like_identifier(cand):
        return False
    # 2026-10-27：纯小写字母+下划线的长标识符（如 _load_neo4j_password）是代码名，
    # 真实 token 几乎不可能 20+ 位全小写无数字 → 判为非密钥（HIGH_ENTROPY 误报治理）
    if re.fullmatch(r"[a-z_]{12,}", cand):
        return False
    # 2026-10-27 补：候选在行内被当作函数调用（如 _load_neo4j_password()）→ 代码名
    if re.search(re.escape(cand) + r"\s*\(", line):
        return False
    # 2026-10-27 SCANFIX：multipart boundary / 字典键名 误报
    if cand.startswith("----") or "boundary" in line.lower():
        return False
    if re.search(r"[\"\']" + re.escape(cand) + r"[\"\']\s*:", line):
        return False  # 被引号包裹且后接冒号 → JSON/字典键名，非密钥
    if "." in cand:
        return False  # 含点号 → 多为标识符/路径/域名，非密钥
    # 第9轮（2026-11-07 K-ORG-20261107-ENTROPY）：文档里引用「带扩展名的路径成分」必然命中。
    # 例：portal/test_legion_live_boot_p0.py —— 文件名主体 test_legion_live_boot_p0 共 24 字符、
    # 全小写字母+数字+下划线，正好落在高熵字符集内；改措辞消不掉（命中的是文件名本身）。
    # 三条**同时**满足才豁免：全小写候选 + 左侧紧邻 '/'（或 '\'）+ 右侧紧跟已知扩展名。
    # 因此混合大小写 token 不在范围内（真实随机 token 必含大写或符号）；URL 末端裸 token
    # 也不在范围内（无左侧 '/' 或右侧无扩展名）。不放 `"://" not in line`：本判据看的是候选
    # 两侧形态而非整行内容，同一行既含 URL 又含文件路径时仍应豁免，否则该类误报会复发。
    if re.fullmatch(r"[a-z0-9_]{12,}", cand) \
            and re.search(r"[/\\]" + re.escape(cand), line) \
            and re.search(re.escape(cand) + FILE_EXT_RE.pattern, line):
        return False
    if len(cand) == 40 and HEX_RE.match(cand) and COMMIT_CTX_RE.search(line):
        return False  # 疑似 git sha
    if HEX_RE.match(cand):
        # 纯 hex 长串（md5/sha256 与 tushare token 同形态）：仅在凭据语境词同行时命中
        return bool(CRED_CONTEXT_RE.search(line)) and shannon_entropy(cand) >= 3.0
    # 第7轮（2026-10-19）：PascalCase 长标识符（如 PortfolioAuthorizationRequest 类名）
    # 判为代码标识符不命中。放在纯 hex 分支之后：hex 串仍走上面的严格凭据语境闸门，
    # 不受本判据豁免。漏报风险评估：真实随机 token 含数字且大小写随机分布，要匹配
    # 「大写开头+小写段相连≥2次」的严格交替形态，其概率对 >=20 字符随机串可忽略
    # （每个段边界都必须恰好大写转小写连续段）；且本仓已见真实凭据均为 hex 或含
    # 符号/数字形态，几乎不可能落入该形态。注意本判据同时化解了类名内含
    # "Authorization" 等子串误触 CRED_CONTEXT 放宽分支的放大效应。
    if re.match(r"^(?:[A-Z][a-z]+){2,}[A-Za-z0-9]*$", cand):
        return False
    has_lower = bool(re.search(r"[a-z]", cand))
    has_upper = bool(re.search(r"[A-Z]", cand))
    has_digit = bool(re.search(r"[0-9]", cand))
    classes = sum([has_lower, has_upper, has_digit])
    ent = shannon_entropy(cand)
    if classes >= 3 and ent >= 4.0:
        return True
    # 凭据语境词同行时放宽阈值（漏报代价高于误报）
    if CRED_CONTEXT_RE.search(line) and classes >= 2 and ent >= 3.2:
        return True
    return False


PY_LITERAL_VALUES = {"none", "true", "false", "null", "nil"}
_ENV_REF_RE = re.compile(r"(?i)(os\.environ|os\.getenv|getenv\s*\(|environ\.get\s*\()")


def is_assign_false_positive(cand: str, line: str, m, path_norm: str) -> bool:
    """TOKEN_ASSIGN/PASSWORD_LITERAL 误报豁免（2026-10-27 关羽 SCANFIX 单）：
    ① RHS 是 Python 字面量 None/True/False/null/nil；
    ② kwarg/属性自引用同名（password=同名变量、app_secret=同名变量、
       self.sign_secret=同名变量）——代码引用而非字面密钥；
    ③ 同行含 os.environ/getenv 等环境读取（值在运行时注入）；
    ④ .py 文件里未加引号的标识符（含下划线/纯小写驼峰）→ 变量/函数引用。
    真实密钥特征保留：加引号字符串、.env/.ps1/.json 等非 .py 文件、含随机字符的 token。
    """
    low = cand.lower()
    if low in PY_LITERAL_VALUES:
        return True
    if _ENV_REF_RE.search(line):
        return True
    # ② 提取 LHS 名（匹配段内第一个标识符链），与 RHS 最后一段比较
    lhs = re.match(r"(?i)([A-Za-z_][\w\.]*)\s*[:=：>]{1,2}\s*", m.group(0))
    if lhs:
        lhs_name = lhs.group(1).split(".")[-1]
        if lhs_name and lhs_name.lower() == low and low not in ("token", "secret", "password", "pwd", "key"):
            return True
    # ④ .py 里未加引号且形似标识符 → 引用而非字面量
    start = m.start(1) if m.lastindex else m.start()
    quoted = start > 0 and line[start - 1] in "\"'`"
    if path_norm.endswith(".py") and not quoted and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", cand):
        return True
    return False


def mask(secret: str) -> str:
    """脱敏：保留前4后2；长度<=8 的短串只保留前2，避免信息过多。"""
    s = secret.strip()
    if len(s) <= 8:
        return s[:2] + "****"
    return s[:4] + "****" + s[-2:]


def masked_line_all(line: str, secrets) -> str:
    """把行内出现的所有密钥材料统一打码后返回预览（单行截断）。
    红线：同一行哪怕只有一个候选命中，其余真实凭据也必须一并打码。"""
    out = line.rstrip("\r\n")
    for s in sorted(set(secrets), key=len, reverse=True):
        if s and s in out:
            out = out.replace(s, mask(s))
    if len(out) > MAX_LINE_PREVIEW:
        out = out[:MAX_LINE_PREVIEW] + "…"
    return out


class FileHits:
    __slots__ = ("ip_lines", "cred_lines", "hits")

    def __init__(self):
        self.ip_lines = []      # [(lineno, line)]
        self.cred_lines = []    # lineno
        self.hits = []          # (lineno, rule, preview, exempt:bool)


def is_vendor(path_norm: str) -> bool:
    return any(re.search(p, path_norm) for p in VENDOR_PATH_PATTERNS)


def scan_file(path: str, include_vendor: bool = False):
    """扫描单个文件。返回 (hits, meta)；hits = [(lineno, rule, preview, exempt)]"""
    hits = []
    try:
        if os.path.getsize(path) > MAX_FILE_SIZE:
            return hits, {"skipped": "too-large"}
        with open(path, "rb") as f:
            raw = f.read()
        if b"\x00" in raw[:8192]:
            return hits, {"skipped": "binary"}
        text = raw.decode("utf-8", errors="replace")
    except (OSError, PermissionError):
        return hits, {"skipped": "unreadable"}

    path_norm = path.replace("\\", "/")
    if not include_vendor and is_vendor(path_norm):
        return hits, {"skipped": "vendor"}
    path_exempt = any(re.search(p, path_norm) for p in EXEMPT_PATH_PATTERNS)
    path_test = bool(TEST_PATH_MARKERS.search(path_norm))

    state = FileHits()
    seen = set()
    lines = text.splitlines()
    secrets_at = {}   # lineno -> 该行出现的全部密钥材料（供任意预览统一打码）
    for lineno, line in enumerate(lines, start=1):
        pending = []   # (rule, secret, base_exempt)

        # 1) 已知字面量
        for lit in KNOWN_LITERALS:
            if lit in line:
                pending.append(("KNOWN_LITERAL", lit, path_exempt))

        # 2) 各正则规则
        for rule_name, regex, group in RULES:
            for m in regex.finditer(line):
                cand = m.group(group) if group else m.group(0)
                cand = cand.strip().strip("\"'`=:,;")
                if not cand:
                    continue
                if rule_name == "_INTRANET_IP":
                    state.ip_lines.append((lineno, cand))
                    continue
                if rule_name == "HIGH_ENTROPY":
                    if line.lstrip().startswith(("def ", "async def ", "class ", "#", "//")):
                        continue  # 整行注释/定义行，不按高熵密钥判定
                    hash_pos = line.find("#")
                    if hash_pos >= 0 and m.start() > hash_pos:
                        continue  # 行尾注释里的 TASK_LOGON 常量类内容非密钥
                        # 2026-10-27：函数/类名、纯注释行常含 token/password 等词，非密钥
                        continue
                        continue  # 2026-10-27：函数/类名常含 token/password 等词，非密钥
                    if not is_high_entropy_candidate(cand, line):
                        continue
                if rule_name in ("TOKEN_ASSIGN", "PASSWORD_LITERAL"):
                    if cand.startswith("$") or cand.startswith("<"):
                        continue  # 环境变量引用 / XML 占位
                    if cand.startswith("?"):
                        continue  # compose ${VAR:?报错串} 插值残段（2026-10-19 第6轮）
                    tail = line[m.end():].lstrip()[:1]
                    if tail == "(":
                        continue  # 候选后紧跟 "(" → 函数调用（如 password=_load_pwd()），非字面密钥（第6轮）
                    cand = cand.lstrip("-=:")  # 剥离 ':-' 等赋值连接符残留
                    if "." in cand and all(len(s) <= 12 for s in cand.split(".")):
                        continue  # 短段点分标识符（如 uuid.uuid4）
                    if PLACEHOLDER_RE.search(cand):
                        continue  # 候选本身是占位符/变量引用
                    if re.fullmatch(r"[A-Z0-9_]+", cand):
                        continue  # SCREAMING_CASE：是变量名不是密钥
                    if is_assign_false_positive(cand, line, m, path_norm):
                        continue  # 2026-10-27 SCANFIX：kwarg 自引用/None/环境变量/未加引号标识符
                if rule_name == "PHONE_CN" and PHONE_NOISE_CTX_RE.search(line):
                    continue  # 文件大小/计数语境下的 11 位数字
                pending.append((rule_name, cand, path_exempt))

        if not pending:
            continue
        state.cred_lines.append(lineno)
        line_test = bool(TEST_LINE_MARKERS.search(line))
        line_secrets = [s for _, s, _ in pending]
        secrets_at[lineno] = line_secrets
        for rule, secret, base_exempt in pending:
            key = (lineno, rule, secret)
            if key in seen:
                continue
            seen.add(key)
            exempt = base_exempt or path_test or line_test
            hits.append((lineno, rule, masked_line_all(line, line_secrets), exempt))

    # 3) 内网 IP + 凭据同文件模式（预览同样打码该行全部密钥材料与 IP 本身）
    if state.ip_lines and state.cred_lines:
        ip_lineno, ip_str = state.ip_lines[0]
        if not path_test and not path_exempt:
            preview = masked_line_all(
                lines[ip_lineno - 1], [ip_str] + secrets_at.get(ip_lineno, []))
            hits.append((ip_lineno, "INTRANET_IP_WITH_CREDS",
                         preview + f"  [本文件另有凭据命中 {len(state.cred_lines)} 行]", False))

    meta = {}
    if path_exempt:
        meta["path_exempt"] = True
    return hits, meta


def collect_targets(repo: str, mode_all: bool, extra_paths):
    """收集待扫文件列表。"""
    if extra_paths:
        out = []
        for p in extra_paths:
            if os.path.isfile(p):
                out.append(p)
            elif os.path.isdir(p):
                for root, dirs, files in os.walk(p):
                    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")]
                    out.extend(os.path.join(root, f) for f in files)
        return sorted(set(out))
    if repo and os.path.isdir(os.path.join(repo, ".git")):
        if mode_all:
            cmd = ["git", "-C", repo, "ls-files", "-z"]
        else:
            cmd = ["git", "-C", repo, "diff", "--cached", "--name-only", "-z"]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=60)
            names = res.stdout.decode("utf-8", errors="replace").split("\0")
            return [os.path.join(repo, n) for n in names if n.strip()]
        except (subprocess.SubprocessError, OSError) as e:
            print(f"[scan_secrets] git 调用失败：{e}", file=sys.stderr)
            return []
    print("[scan_secrets] 未找到 git 仓库且未指定路径，无文件可扫", file=sys.stderr)
    return []


def load_baseline(path):
    """读取基线豁免 TSV（EXEC-R22）。

    格式：每行 `path:line:rule<TAB>category<TAB>note`；# 开头与空行跳过。
    只取第一列精确 key；`./` 前缀归一化，便于 `--all`（./x）与显式路径（x）两种模式共用。
    文件不可读时打印 stderr 告警并按空基线处理（fail-open，不隐藏真实命中）。
    """
    keys = set()
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                k = ln.split("\t")[0].strip()
                if k and not k.startswith("#"):
                    keys.add(k[2:] if k.startswith("./") else k)
    except OSError as e:
        print(f"[scan_secrets] baseline 读取失败（按空基线继续）：{e}", file=sys.stderr)
    return keys


def _baseline_key(path, lineno, rule):
    """命中 → 与 allowlist 对齐的 key（去掉 ./ 前缀）。"""
    p = path[2:] if path.startswith("./") else path
    return "%s:%s:%s" % (p, lineno, rule)


def main():
    ap = argparse.ArgumentParser(description="防泄漏只读扫描器（输出全部脱敏）")
    ap.add_argument("paths", nargs="*", help="显式指定的文件/目录（优先于 git 模式）")
    ap.add_argument("--all", action="store_true", help="扫全仓 tracked 文件")
    ap.add_argument("--repo", default=".", help="git 仓库根目录")
    ap.add_argument("--include-vendor", action="store_true",
                    help="包含第三方/依赖文件（默认跳过：pylibs、vendor、dist-info、min.js 等）")
    ap.add_argument("--baseline", default=None,
                    help="基线豁免清单 TSV（path:line:rule）：表内命中只备案、不计入未豁免，"
                         "退出码仅看新增命中（EXEC-R22）")
    args = ap.parse_args()

    targets = collect_targets(args.repo, args.all, args.paths)
    exempt_files = []       # 路径豁免（应含密文件）清单
    skipped = []
    all_hits = []           # (path, lineno, rule, preview, exempt)
    files_scanned = 0

    for path in targets:
        if not os.path.isfile(path):
            continue
        files_scanned += 1
        hits, meta = scan_file(path, include_vendor=args.include_vendor)
        # 统一以正斜杠展示路径
        path = path.replace("\\", "/")
        if meta.get("skipped"):
            skipped.append((path, meta["skipped"]))
            continue
        if meta.get("path_exempt"):
            exempt_files.append(path)
        for lineno, rule, preview, exempt in hits:
            all_hits.append((path, lineno, rule, preview, exempt))

    # 输出
    real_hits = [h for h in all_hits if not h[4]]
    exempt_hits = [h for h in all_hits if h[4]]
    # 基线豁免（EXEC-R22）：allowlist 内的 path:line:rule 只备案，不算未豁免
    baseline_hits = []
    if args.baseline:
        _base_keys = load_baseline(args.baseline)
        kept = []
        for h in real_hits:
            if _baseline_key(h[0], h[1], h[2]) in _base_keys:
                baseline_hits.append(h)
            else:
                kept.append(h)
        real_hits = kept
    baseline_keys = {_baseline_key(h[0], h[1], h[2]) for h in baseline_hits}

    print("=" * 72)
    vendor_n = sum(1 for _, why in skipped if why == "vendor")
    print(f"[scan_secrets] 扫描文件数={files_scanned}  跳过={len(skipped)}"
          f"(其中第三方/依赖={vendor_n})  路径豁免文件={len(exempt_files)}  "
          f"命中行: 未豁免={len(real_hits)}  基线豁免={len(baseline_hits)}"
          f"  豁免(假密钥/测试桩)={len(exempt_hits)}")
    print("=" * 72)

    if exempt_files:
        print("\n-- 豁免文件清单（本应含密，请人工确认它们未被误放他处）--")
        for p in exempt_files:
            print(f"  EXEMPT-PATH  {p}")

    if exempt_hits:
        print("\n-- 按标记豁免的命中（测试桩/假密钥标记，仅备案）--")
        for path, lineno, rule, preview, _ in exempt_hits:
            print(f"  EXEMPT-TEST {path}:{lineno}:{rule}: {preview}")

    if baseline_hits:
        print(f"\n-- 基线豁免命中（allowlist 备案 {len(baseline_hits)} 条，不计入未豁免）--")
        print("  清单：tools/scan_secrets_allowlist.tsv"
              "（或用 --baseline <path> 指定）；复核方法见 _audit_20261025/exec/E3b 报告")

    if real_hits:
        print("\n-- 未豁免命中（预览已脱敏）--")
        for path, lineno, rule, preview, _ in real_hits:
            print(f"  HIT {path}:{lineno}:{rule}: {preview}")

    # 统计
    stats = {}
    for path, lineno, rule, preview, exempt in all_hits:
        if exempt:
            key = rule + "(豁免)"
        elif _baseline_key(path, lineno, rule) in baseline_keys:
            key = rule + "(基线)"
        else:
            key = rule
        stats[key] = stats.get(key, 0) + 1
    if stats:
        print("\n-- 按规则统计 --")
        for rule, n in sorted(stats.items(), key=lambda x: -x[1]):
            print(f"  {rule}: {n}")

    print(f"\n退出码: {1 if real_hits else 0}  "
          f"({len(real_hits)} 个未豁免命中 → {'建议修复后再提交' if real_hits else '可通过'})")
    sys.exit(1 if real_hits else 0)


if __name__ == "__main__":
    main()
