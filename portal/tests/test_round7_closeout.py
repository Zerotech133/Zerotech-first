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
  · 均为「原地覆盖保 inode」（容器单文件 bind mount，mv 会断挂载）
  · 每轮均 覆盖 → 容器内 nginx -t → nginx -s reload（master PID 1 未重启）
  · 回滚：bak-r7 = R7 前字节；bak-r8 = R8 前字节（即 R7 已应用态）
  · 附带：C:\Temp\portal_api_token（旧值 c6c1447c）已走回收站删除，
    vault f19d1c90 为唯一生效来源，删后带 token POST 仍 200

本测试锁五件事：
  · 前端 token 提示指向真正生效的文件（vault 优先）；
  · 原件为当前累积态，且相对 R7 前基线是**纯加法**（+55 / -0，无删除无改写）；
  · 三处改动语义正确（server 级 off、bt 精确匹配在前缀前、capability/perf 转发）；
  · 回滚备份各自对应正确的历史状态，回滚链可信；
  · nav 入口数不减少（禁止用「删入口」来消灭 404）。
"""
import difflib
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

# 三级基线（sha256 前 16 位），若变化说明有人动过，先确认是否经批准
BASE_SHA256 = "c67da53437ffa964"     # 初始（58501 B / 1220 CRLF）
R7_SHA256 = "7e0ce85bf922d052"       # R7 后（58811 B / 1226 CRLF）
CUR_SHA256 = "07c6d5d7a806af7e"      # R8 + D1 Q2 签收路由（60955 B / 1275 CRLF）


def _lines(b):
    return b.decode("utf-8").replace("\r\n", "\n").split("\n")


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


# ---------------------------------------------------------------- 累积态
def test_nginx_is_r8_applied():
    got = hashlib.sha256(NGINX).hexdigest()
    assert got.startswith(CUR_SHA256), \
        "portal/nginx.conf 非当前累积态（sha256 %s），先确认是否被回滚或再改" % got[:16]
    assert NGINX.count(b"\r\n") == 1275, "应为 R8 后 1242 行 + D1 Q2 签收路由 33 行"
    assert NGINX.count(b"\n") == NGINX.count(b"\r\n"), "不得出现裸 LF"


def test_cumulative_diff_is_pure_addition():
    """相对初始基线只增不删：55 行新增（R7 六行 + R8 十六行 + D1 Q2 三十三行），0 行删除。

    纯加法是本轮的核心安全性主张——任何 -行 都意味着改写了既有规则，
    必须重新评估影响面。"""
    assert os.path.isfile(BAK_R7), "缺少初始基线 reports/nginx.conf.bak-r7"
    base, cur = _lines(open(BAK_R7, "rb").read()), _lines(NGINX)
    add = rem = 0
    for ln in difflib.unified_diff(base, cur, n=0, lineterm=""):
        if ln.startswith("+") and not ln.startswith("+++"):
            add += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            rem += 1
    assert (add, rem) == (55, 0), "累积改动应为 +55/-0，实得 +%d/-%d" % (add, rem)


# ---------------------------------------------------------------- R7 语义
def test_absolute_redirect_is_server_level():
    """必须紧跟 listen/server_name 之后（server 级），不是塞在某个 location 里。"""
    s = NGINX.decode("utf-8")
    assert re.search(r"server_name _;\r\n    absolute_redirect off;\r\n", s), \
        "absolute_redirect off 必须位于 server 级"
    assert s.count("absolute_redirect off;") == 12, \
        "原有 11 处 location 级 + 1 处 server 级，共 12"


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
def test_pending_r8_matches_applied():
    """副本若存在，须与已应用原件字节一致（副本是应用来源，不应漂移）。"""
    assert open(PENDING_R8, "rb").read() == NGINX, "副本与已应用原件不一致"
