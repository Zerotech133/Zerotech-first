r"""nginx 收口累积契约（R7 + R8，2026-11-06）：三处 + 两个 nav 入口。

背景（均以 curl / docker 实证，非推断）：

  R7 ——「重定向丢端口」与「静态页被前缀吞掉」两类系统性故障
    1. `/api/os`、`/api/org`、`/api/monitor`、`/api/jev`、`/api/analysis` 无斜杠请求
       被 nginx 补斜杠 301，且 Location 为绝对 URL、端口被吞成 80（容器内
       server_port=80）→ 浏览器/fetch 一律打不开。server 级一行
       `absolute_redirect off;` 全部转相对，已验证 5/5；顺带修好 `/picks`、
       `/os.html` 等所有 302（不止 api 路径）。
    2. `/bt_view.html`、`/bt2_view.html` 被 `location ^~ /bt`（反代 8091）前缀吞掉
       → 404，文件其实在 webroot 内。精确匹配 `location = /bt*_view.html` 可解。

  R8 ——「nav 有入口、nginx 无 location」的断链
    3. nav 上「🧩 能力看板」`/capability`、「📈 投研绩效」`/perf` 点开 404。
       上游 8091 有动态页（`_html_file` 注入 nav/登录态，capability 17967B
       vs 静态 8743B），但 nginx 无对应 location，请求落到静态 webroot 找不到
       同名文件。按 `/fin`、`/approvals`、`/portal` 同构加精确匹配转发。

  另：`X-Portal-Token` 无需 nginx 显式透传（`proxy_pass_request_headers` 默认 on）。
  此前「经 8734 恒 403」的真因是两个 token 文件内容不一致，与 nginx 无关。

应用记录：
  · R7（05:38）c67da534 / 58501B / 1220 CRLF → 7e0ce85b / 58811B / 1226 CRLF
  · R8（06:07）7e0ce85b → 8ef88f26 / 59484B / 1242 CRLF
  · D1 Q2 签收路由（2026-11-06）8ef88f26 → 07c6d5d7 / 60955B / 1275 CRLF
    （受控静态路由 +33 行，纯加法；本测试基线随之更新，避免旧断言误红）
  · P0-07 `/d1` 总览页路由（2026-11-07）07c6d5d7 → b9aa7c0d / 1295 CRLF
    （+20 行；同时两行旧注释同步改写，无规则/指令删除；本测试基线再次同步）
  · 均为「原地覆盖保 inode」（容器单文件 bind mount，mv 会断挂载）
  · 每轮均 覆盖 → 容器内 nginx -t → nginx -s reload（master PID 1 未重启）
  · 回滚：bak-r7 = R7 前字节；bak-r8 = R8 前字节（即 R7 已应用态）
  · 附带：C:\Temp\portal_api_token（旧值 c6c1447c）已走回收站删除，
    vault f19d1c90 为唯一生效来源，删后带 token POST 仍 200

本测试锁五件事：
  · 前端 token 提示指向真正生效的文件（vault 优先）；
  · 相对 R7 前基线**不删 location、不删指令**（按指令集合判，不按字节/行号，见 §修订）；
  · 三处改动语义正确（server 级 off、bt 精确匹配在前缀前、capability/perf 转发）；
  · 回滚备份各自对应正确的历史状态，回滚链可信；
  · nav 入口数不减少（禁止用「删入口」来消灭 404）。

修订 · 第十一次（2026-11-07 P0-1b，字节锁 → 语义断言）
  · 下线断言：`CUR_SHA256`（当前累积态哈希）、CRLF 精确行数（1313）、累积 diff
    精确「+96/-3」。三者合称「字节锁」。
  · 下线理由（实证，非推测）：本窗口 nginx.conf 按已批 B 方案追加 JEV 本机免登录
    （+27 行）后三锁全红，但配置本身正确。复核 5 条「删除行」的实际语义：
      2 条 = 注释改写（strategies.json 口径作废）；
      2 条 = `auth_request /_auth_guard;` **被 allow/deny 行插入后下移**，
              difflib 记为「删一行 + 加一行」，指令实际仍在（语义零变化）；
      1 条 = F17 受保护页正则**扩面**（追加 feishu_admin），属替换非回退。
    → 字节锁把「指令被移动/正则被扩面」误判为「规则被删除」，连续多个合并窗口
      制造假红；同时它对**真删除**的判别力并不比语义断言强（删 auth_request 只会
      让计数 -1，仍可能落在容差内）。
  · 现行断言（等价强度、零维护）：大括号平衡、location 不消失（扩面算替换）、
    每条 location 的指令集合不缩水、`auth_request` 覆盖不减少、`satisfy any`
    必须 fail-closed（有 deny all 且在 allow 之后）、备份副本不得超前于原件。
  · 防「断言空转」：每类守卫都配一条 **mutation 自测**（喂构造变体，必须报错），
    并有一条「真实文件全过」的正例——沿用 §二 教训「永不执行的断言=没有断言」。
  · sha256 与行数改为 `print` 的 INFO，仅供人眼比对；冻结的历史备份
    （bak-r7 / bak-r8）仍锁 sha，因为它们的字节**本就不该变**。
"""
import hashlib
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PORTAL = os.path.dirname(HERE)
ROOT = os.path.dirname(PORTAL)
BT2 = open(os.path.join(PORTAL, "bt2_view.html"), encoding="utf-8").read()
NGINX_PATH = os.path.join(PORTAL, "nginx.conf")
NGINX = open(NGINX_PATH, "rb").read()
NAV_JS = open(os.path.join(PORTAL, "static", "nav.js"), encoding="utf-8").read()
PENDING_R8 = os.path.join(ROOT, "reports", "nginx.conf.pending-r8")
BAK_R7 = os.path.join(ROOT, "reports", "nginx.conf.bak-r7")   # R7 前 = 初始基线
BAK_R8 = os.path.join(ROOT, "reports", "nginx.conf.bak-r8")   # R8 前 = R7 已应用态

# 冻结备份的基线（sha256 前 16 位）：备份字节本就不该变，锁它们才有意义
BASE_SHA256 = "c67da53437ffa964"     # 初始（58501 B / 1220 CRLF）
R7_SHA256 = "7e0ce85bf922d052"       # R7 后（58811 B / 1226 CRLF）

# 2026-11-07 P0-1b：CUR_SHA256 已下线（原值 cbf213a097ae1aa9 / 1313 CRLF）。
# 当前态不再锁字节：合法追加会被误判为回滚，而真删除它又拦不住。
# 行数改为「下限」：只增不减，少于此即代表有规则被删。
MIN_CRLF = 1295                      # P0-07 签收态（b9aa7c0d / 1295 CRLF）


# ---------------------------------------------------------------- 语义解析
_LOC_START = re.compile(r"^\s*location\s+([^{]+?)\s*\{\s*$")
_INLINE_COMMENT = re.compile(r"\s+#.*$")
_TOKEN = re.compile(r"[a-z0-9_]{3,}")


def _stmt(raw):
    """取一行去掉行内注释后的语句；纯注释行/空行返回 ""。"""
    s = raw.strip()
    if not s or s.startswith("#"):
        return ""
    return _INLINE_COMMENT.sub("", s).strip()


def _parse_locations(text):
    """解析 {location key: frozenset(块内语句)}，同名 key 用 ``#2``/``#3`` 区分。

    按「指令集合」而非字节/行号比对：指令被下移、重排、加行内注释都不算变化，
    只有**语句真的不见了**才算缩水。
    """
    out = {}
    seen = {}
    key = None
    depth = 0
    stmts = set()
    for raw in text.replace("\r\n", "\n").split("\n"):
        if key is None:
            m = _LOC_START.match(raw)
            if m:
                k = m.group(1).strip()
                seen[k] = seen.get(k, 0) + 1
                key = k if seen[k] == 1 else "%s#%d" % (k, seen[k])
                depth = 1
                stmts = set()
            continue
        s = raw.strip()
        opens, closes = s.count("{"), s.count("}")
        if opens:
            # 嵌套块（if/map 等）：整行作为一条语句保留
            code = _stmt(raw)
            if code:
                stmts.add(code)
            depth += opens
        if closes:
            depth -= closes
            if depth <= 0:
                out[key] = frozenset(stmts)
                key = None
                continue
        if not opens:
            code = _stmt(raw)
            if code:
                stmts.add(code)
    return out


def _key_tokens(key):
    """location key 的标识符集合，用于判定「正则扩面」而非「删除」。"""
    return set(_TOKEN.findall(key.lower()))


def _regex_skeleton(key):
    """把正则 location 的括号组内容抹成 `(...)`，得到「骨架」。

    骨架相同才允许按扩面处理：`^/(?:briefing|case)\\.html$` 追加 feishu_admin
    仍同骨架；而 `^/(?:a)\\.html$` 换成 `^/(?:b)\\.html$` 骨架里没有区别，
    因此还需配合词元超集判断（见 `_find_successors`）。

    迭代折叠最内层括号，处理 `(?:...|combo_backtest(?:_jobs)?)` 这类嵌套组；
    只替换一次会把外层组留在骨架里，导致正则扩面被误判为 location 消失。
    """
    s = key.strip()
    out = []
    i = 0
    while i < len(s):
        if s[i] != "(":
            out.append(s[i])
            i += 1
            continue
        depth = 1
        j = i + 1
        while j < len(s) and depth:
            if s[j] == "(":
                depth += 1
            elif s[j] == ")":
                depth -= 1
            j += 1
        if depth == 0:
            out.append("(...)")
            i = j
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _find_successors(key, cur):
    """返回可视为「同一 location 的演进版」的当前 key 列表。

    规则（刻意保守，宁可误红不可放过）：
      · 完全同名 → 直接就是它；
      · 仅 **正则** location 允许「同骨架 + 词元扩面」（如追加保护页 feishu_admin）；
      · 前缀/精确 location（`= `、`^~ `、普通前缀）**不允许**任何替代——
        因为前缀路径改名就是删路由，不能靠共享词元蒙混。
    """
    if key in cur:
        return [key]
    if not key.strip().startswith("~"):
        return []
    skel = _regex_skeleton(key)
    toks = _key_tokens(key)
    return [c for c in cur
            if c.strip().startswith("~") and _regex_skeleton(c) == skel
            and toks <= _key_tokens(c)]


def _assert_braces_balanced(text):
    n_open, n_close = text.count("{"), text.count("}")
    assert n_open == n_close, "大括号不平衡：{ %d 个 / } %d 个" % (n_open, n_close)


def _assert_no_location_shrink(base, cur):
    """基线里每个 location 必须仍存在（或存在扩面后代），且指令集合不缩水。"""
    missing, shrunk = [], []
    for k, dirs in base.items():
        succ = _find_successors(k, cur)
        if not succ:
            missing.append(k)
            continue
        if k in cur:
            lost = dirs - cur[k]
            if lost:
                shrunk.append((k, sorted(lost)))
        elif not any(not (dirs - cur[s]) for s in succ):
            shrunk.append((k, "扩面后代指令不完整：%s" % succ))
    assert not missing, "location 消失且无扩面替代（疑似删路由）：%s" % missing
    assert not shrunk, "指令集合缩水（疑似删规则）：%s" % shrunk


def _assert_auth_guard_preserved(base, cur):
    """基线中带 auth_request 的 location，鉴权不得被静默摘掉。"""
    weakened = []
    for k, dirs in base.items():
        if "auth_request /_auth_guard;" not in dirs:
            continue
        succ = _find_successors(k, cur)
        if not succ:
            weakened.append((k, "整块消失"))
        elif not any("auth_request /_auth_guard;" in cur[s] for s in succ):
            weakened.append((k, "auth_request 被移除"))
    assert not weakened, "受保护 location 鉴权被削弱：%s" % weakened


def _assert_satisfy_any_is_fail_closed(text):
    """`satisfy any` 必须有 `deny all;` 兜底，且 deny 出现在所有 allow 之后。

    否则 allow/deny 语义反转（或漏写 deny）会让「本机免登录」变成「全网免登录」。
    """
    bad = []
    for k, dirs in _parse_locations(text).items():
        if "satisfy any;" not in dirs:
            continue
        ordered = [d for d in _order_of_block(text, k)]
        if "deny all;" not in ordered:
            bad.append((k, "缺 deny all"))
            continue
        allow_idx = [i for i, d in enumerate(ordered) if d.startswith("allow ")]
        deny_idx = ordered.index("deny all;")
        if allow_idx and deny_idx < max(allow_idx):
            bad.append((k, "deny all 早于 allow（语义反转）"))
        if not any(d.startswith("auth_request ") for d in ordered):
            bad.append((k, "satisfy any 但缺 auth_request"))
    assert not bad, "satisfy any 未 fail-closed：%s" % bad


def _order_of_block(text, key):
    """按出现顺序返回某 location 块内的语句（用于顺序敏感断言）。"""
    lines = text.replace("\r\n", "\n").split("\n")
    cur, depth, out = None, 0, []
    seen = {}
    for raw in lines:
        if cur is None:
            m = _LOC_START.match(raw)
            if m:
                k0 = m.group(1).strip()
                seen[k0] = seen.get(k0, 0) + 1
                cur = k0 if seen[k0] == 1 else "%s#%d" % (k0, seen[k0])
                depth, out = 1, []
            continue
        s = raw.strip()
        opens, closes = s.count("{"), s.count("}")
        if opens:
            code = _stmt(raw)
            if code:
                out.append(code)
            depth += opens
        if closes:
            depth -= closes
            if depth <= 0:
                if cur == key:
                    return out
                cur = None
                continue
        if not opens:
            code = _stmt(raw)
            if code:
                out.append(code)
    return out


def _assert_pending_not_ahead(pend_text, cur_text):
    """副本不得含原件没有的 location —— 那意味着原件被回滚了。

    副本可能是旧应用态；若现网对同一 regex location 做了扩面（如加 board），
    旧 key 应视为存在后代，不能误判为“副本超前/原件回滚”。
    """
    p, c = _parse_locations(pend_text), _parse_locations(cur_text)
    ahead = [k for k in p if not _find_successors(k, c)]
    assert not ahead, "应用副本含原件没有的 location（原件疑似被回滚）：%s" % ahead



# ---------------------------------------------------------------- token 路径
def test_bt2_token_hint_points_to_vault():
    """提示必须指向服务端实际优先生效的文件，否则用户照抄必然 403。"""
    body = BT2.split("function promptBt2Token(")[1].split("function bt2Err(")[0]
    assert "_vault/secrets/portal_api_token" in body, "提示未指向 vault 优先路径"
    # 旧路径仍要提及（作为「可能不一致」的警告），但不得再是唯一来源
    assert "C:/Temp/portal_api_token" in body
    assert "不一致" in body, "必须写明旧文件内容可能与生效值不一致"


def test_bt2_token_hint_mentions_precedence():
    """必须把读取优先级写全，避免下次再踩同一个坑。"""
    body = BT2.split("function promptBt2Token(")[1].split("function bt2Err(")[0]
    assert "PORTAL_API_TOKEN" in body, "未提环境变量优先级"
    assert "_vault/secrets" in body, "未提文件优先级"
    assert body.index("_vault/secrets") < body.index("环境变量"), "应先给出该取的文件"


def test_bt2_token_not_in_plain_prompt_string_only():
    """prompt() 文本不得含裸 token 值（只描述路径）。"""
    body = BT2.split("function promptBt2Token(")[1].split("function bt2Err(")[0]
    assert not re.search(r"[0-9a-f]{64}", body), "提示里不得出现 64 位 hex token"


# ---------------------------------------------------------------- 累积态（语义）
def test_nginx_current_state_is_valid():
    """当前累积态的**结构**校验；sha 与行数只打 INFO，不再断言。

    历史断言（sha 前缀 + CRLF 精确 1313 + 累积 diff 精确 +96/-3）已下线，
    理由见文件头「修订 · 第十一次」：它把「指令下移/正则扩面」误判为「删规则」。
    """
    text = NGINX.decode("utf-8")
    crlf = NGINX.count(b"\r\n")
    print("[INFO] nginx.conf sha256=%s bytes=%d crlf=%d locations=%d"
          % (hashlib.sha256(NGINX).hexdigest(), len(NGINX), crlf,
             len(_parse_locations(text))))
    assert NGINX.count(b"\n") == crlf, "不得出现裸 LF（与 CRLF 混行会撕配置）"
    assert crlf >= MIN_CRLF, \
        "行数降到 %d（< P0-07 签收态 %d），疑似删规则" % (crlf, MIN_CRLF)
    _assert_braces_balanced(text)
    assert len(_parse_locations(text)) >= 1, "未解析出任何 location，解析器失效"


def test_no_location_or_directive_is_removed():
    """相对 R7 前基线：不得删 location、不得删 location 内任何指令。

    取代原「累积 diff 必须恰为 +96/-3」。语义等价且不误红：
      · 指令被 allow/deny 行插入后下移 → 仍在，通过（原断言会误判为 -1）；
      · 正则 location 追加域名（扩面）→ 视为替换，通过（原断言需人工改常量）；
      · 真删一条 auth_request / proxy_pass → 指令集合缩水，**必红**。
    """
    assert os.path.isfile(BAK_R7), "缺少初始基线 reports/nginx.conf.bak-r7"
    base = _parse_locations(open(BAK_R7, "rb").read().decode("utf-8"))
    cur = _parse_locations(NGINX.decode("utf-8"))
    assert base, "基线解析为空：解析器或 bak-r7 有问题，断言可能空转"
    print("[INFO] location 数 base=%d cur=%d；指令数 base=%d cur=%d"
          % (len(base), len(cur), sum(len(v) for v in base.values()),
             sum(len(v) for v in cur.values())))
    _assert_no_location_shrink(base, cur)


def test_auth_guard_is_not_weakened():
    """受保护 location 的 `auth_request /_auth_guard;` 不得被静默摘掉。

    这条是 B 方案（本机免登录）唯一的反向守卫：允许加 allow/satisfy，
    但不允许把 auth_request 删掉——那会把「本机免登录」悄悄变成「全网免登录」。
    """
    base = _parse_locations(open(BAK_R7, "rb").read().decode("utf-8"))
    cur = _parse_locations(NGINX.decode("utf-8"))
    _assert_auth_guard_preserved(base, cur)
    n = sum(1 for v in cur.values() if "auth_request /_auth_guard;" in v)
    print("[INFO] 含 auth_request 的 location 数 cur=%d base=%d"
          % (n, sum(1 for v in base.values() if "auth_request /_auth_guard;" in v)))


def test_satisfy_any_blocks_are_fail_closed():
    """`satisfy any` 必须 fail-closed：有 deny all、deny 在 allow 之后、且有 auth_request。"""
    _assert_satisfy_any_is_fail_closed(NGINX.decode("utf-8"))


# ---------------------------------------------------------------- R7 语义
def test_absolute_redirect_is_server_level():
    """必须紧跟 listen/server_name 之后（server 级），不是塞在某个 location 里。"""
    s = NGINX.decode("utf-8")
    assert re.search(r"server_name _;\r\n    absolute_redirect off;\r\n", s), \
        "absolute_redirect off 必须位于 server 级"
    assert s.count("absolute_redirect off;") >= 12, \
        "原有 11 处 location 级 + 1 处 server 级，共 12（只增不减）"


def test_bt_exact_match_before_prefix():
    s = NGINX.decode("utf-8")
    for page in ("/bt_view.html", "/bt2_view.html"):
        assert s.count("location = %s" % page) == 1, page
    # 精确匹配语义上优先于 ^~，但为可读性写在前面
    assert s.index("location = /bt2_view.html") < s.index("location ^~ /bt {")


# ---------------------------------------------------------------- R8 语义
def test_nav_entries_have_location():
    """nav 上能点的 `/capability`、`/perf` 必须有精确匹配 location 转发上游。

    这两个曾是 404：nginx 无 location → 落到静态 webroot → 找不到同名文件。
    注意不能用 `/capability.html` 静态版替代：动态版多出 nav 注入与登录态
    （17967B vs 8743B）。"""
    s = NGINX.decode("utf-8")
    for p in ("/capability", "/perf"):
        assert s.count("location = %s {" % p) == 1, "%s 缺少精确匹配 location" % p
        seg = s.split("location = %s {" % p)[1].split("}")[0]
        assert "proxy_pass http://$h:8091;" in seg, "%s 必须转发上游 8091" % p


def test_nav_entries_are_reachable_in_nav_js():
    """反向守卫：nav 里确实存在这两个入口（不是靠删入口消灭 404）。"""
    assert '"/capability"' in NAV_JS and '"/perf"' in NAV_JS
    # nav 入口总数不得减少（R8 前实测 104 个）
    paths = set(re.findall(r'"/([a-z0-9][a-z0-9/_.?=-]*)"', NAV_JS))
    assert len(paths) >= 104, "nav 入口数从 104 降到 %d，禁止用删入口消灭 404" % len(paths)


def test_no_token_header_injection():
    """相对初始基线不得新增 `proxy_set_header X-Portal-Token`：nginx 默认转发
    客户端 header，存量 1 处（legion 反代段）保持不变即可。"""
    base = open(BAK_R7, "rb").read().decode("utf-8")
    assert NGINX.decode("utf-8").count("proxy_set_header X-Portal-Token") == \
        base.count("proxy_set_header X-Portal-Token") == 1


# ---------------------------------------------------------------- 回滚链
def test_backup_chain_is_consistent():
    """两级备份各自对应正确的历史状态，回滚链才可信。"""
    for path, want, label in ((BAK_R7, BASE_SHA256, "R7 前基线"),
                              (BAK_R8, R7_SHA256, "R8 前（R7 已应用态）")):
        assert os.path.isfile(path), "缺少 %s：%s" % (label, path)
        got = hashlib.sha256(open(path, "rb").read()).hexdigest()
        assert got.startswith(want), "%s sha256 不符（%s）" % (label, got[:16])
    # 备份不得等于当前原件，否则失去回滚价值
    assert open(BAK_R8, "rb").read() != NGINX


@pytest.mark.skipif(not os.path.isfile(PENDING_R8),
                    reason="reports/ 被 .gitignore 忽略，副本可能不在本机")
def test_pending_r8_not_ahead_of_applied():
    """副本可以是「旧的应用态」（原件后来又追加了），但**不得超前于原件**。

    原断言要求字节完全相同 —— 本质是把「副本是历史快照」误当成「副本必须同步」，
    于是每次合法改 nginx 都要重新拷副本。改为单向校验：
      · 副本 ⊆ 原件（location 集合）：通过，差异只打 INFO；
      · 副本 ⊃ 原件（含原件没有的 location）：**红**，说明原件被回滚或应用了别的文件。
    """
    pend = open(PENDING_R8, "rb").read()
    if pend == NGINX:
        return
    _assert_pending_not_ahead(pend.decode("utf-8"), NGINX.decode("utf-8"))
    print("[INFO] 副本与原件不同（副本 crlf=%d / 原件 crlf=%d），副本未超前，视为历史快照"
          % (pend.count(b"\r\n"), NGINX.count(b"\r\n")))


# ---------------------------------------------------------------- 守卫自测（mutation）
# 教训（§二）：只写断言、不验证断言会不会红，等于没写。以下每个用例故意喂
# **坏变体**，要求对应守卫必须抛错；再配一条「真实文件全过」的正例。
def test_selftest_real_file_passes_all_guards():
    """正例：真实基线→当前，三类守卫都不应抛错（防断言空转成永远红）。"""
    base = _parse_locations(open(BAK_R7, "rb").read().decode("utf-8"))
    cur = _parse_locations(NGINX.decode("utf-8"))
    _assert_no_location_shrink(base, cur)
    _assert_auth_guard_preserved(base, cur)
    _assert_satisfy_any_is_fail_closed(NGINX.decode("utf-8"))
    _assert_braces_balanced(NGINX.decode("utf-8"))


def test_selftest_deleted_directive_bites():
    """删一条指令 → 必须红。"""
    base = _parse_locations("location = /a {\n  auth_request /_auth_guard;\n}\n")
    cur = _parse_locations("location = /a {\n}\n")
    with pytest.raises(AssertionError, match="缩水"):
        _assert_no_location_shrink(base, cur)


def test_selftest_moved_directive_does_not_bite():
    """指令被下移/重排（语义未变） → 不得误红。这是原字节锁的病根。"""
    base = _parse_locations("location = /a {\n  auth_request /_auth_guard;\n}\n")
    cur = _parse_locations(
        "location = /a {\n  allow 127.0.0.1;\n  deny all;\n"
        "  auth_request /_auth_guard;\n  satisfy any;\n}\n")
    _assert_no_location_shrink(base, cur)          # 不抛
    _assert_auth_guard_preserved(base, cur)        # 不抛


def test_selftest_removed_location_bites():
    """整个 location 消失且无扩面替代 → 必须红。"""
    base = _parse_locations("location ^~ /api/x/ {\n  proxy_pass http://$h:8091;\n}\n")
    cur = _parse_locations("location ^~ /api/y/ {\n  proxy_pass http://$h:8091;\n}\n")
    with pytest.raises(AssertionError, match="消失"):
        _assert_no_location_shrink(base, cur)


def test_selftest_regex_expansion_does_not_bite():
    """正则 location 追加域名（扩面） → 不得误红（原断言需人工改常量）。"""
    base = _parse_locations(
        'location ~* ^/(?:briefing|case)\\.html$ {\n  auth_request /_auth_guard;\n}\n')
    cur = _parse_locations(
        'location ~* ^/(?:briefing|case|feishu_admin)\\.html$ {\n'
        '  auth_request /_auth_guard;\n}\n')
    _assert_no_location_shrink(base, cur)
    _assert_auth_guard_preserved(base, cur)


def test_selftest_auth_guard_removal_bites():
    """把 auth_request 摘掉（块还在） → 必须红。"""
    base = _parse_locations("location ^~ /api/jev/ {\n  auth_request /_auth_guard;\n}\n")
    cur = _parse_locations(
        "location ^~ /api/jev/ {\n  allow 127.0.0.1;\n  deny all;\n  satisfy any;\n}\n")
    with pytest.raises(AssertionError, match="鉴权被削弱"):
        _assert_auth_guard_preserved(base, cur)


def test_selftest_satisfy_any_without_deny_bites():
    """satisfy any 缺 deny all → 必须红（否则=全网免登录）。"""
    bad = ("location = /a {\n  allow 127.0.0.1;\n"
           "  auth_request /_auth_guard;\n  satisfy any;\n}\n")
    with pytest.raises(AssertionError, match="fail-closed"):
        _assert_satisfy_any_is_fail_closed(bad)


def test_selftest_satisfy_any_deny_before_allow_bites():
    """deny 在 allow 之前（语义反转） → 必须红。"""
    bad = ("location = /a {\n  deny all;\n  allow 127.0.0.1;\n"
           "  auth_request /_auth_guard;\n  satisfy any;\n}\n")
    with pytest.raises(AssertionError, match="fail-closed"):
        _assert_satisfy_any_is_fail_closed(bad)


def test_selftest_pending_ahead_bites():
    """副本含原件没有的 location（=原件被回滚） → 必须红。"""
    pend = "location = /new {\n  proxy_pass http://$h:8091;\n}\n"
    cur = "location = /old {\n  proxy_pass http://$h:8091;\n}\n"
    with pytest.raises(AssertionError, match="超前|回滚"):
        _assert_pending_not_ahead(pend, cur)


def test_selftest_brace_imbalance_bites():
    """少一个 `}` → 必须红（nginx -t 之前先在本机拦住）。"""
    with pytest.raises(AssertionError, match="不平衡"):
        _assert_braces_balanced("http {\n  server {\n    location = /a {\n  }\n")


def test_selftest_real_file_regression_is_caught():
    """**在真实文本上**做变异：摘掉 `^~ /api/jev/` 的 auth_request → 守卫必须红。

    前几条自测用的是构造串，只能证明逻辑对；这条证明断言在真实 nginx.conf
    上有牙齿（B 方案最容易犯的错就是把 auth_request 顺手删掉）。
    """
    text = NGINX.decode("utf-8")
    base = _parse_locations(open(BAK_R7, "rb").read().decode("utf-8"))
    marker = "location ^~ /api/jev/ {"
    i = text.index(marker)
    j = text.index("}", i)
    seg = text[i:j]
    assert "auth_request /_auth_guard;" in seg, "锚点失配：jev 块结构已变，请更新本自测"
    mutated = text[:i] + seg.replace("auth_request /_auth_guard;", "", 1) + text[j:]
    with pytest.raises(AssertionError, match="鉴权被削弱"):
        _assert_auth_guard_preserved(base, _parse_locations(mutated))


