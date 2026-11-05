r"""R7 契约（2026-11-06）：L2 收口副本与 token 路径纠错。

背景（本轮实测，均以 curl/docker 实证，非推断）：
  1. `/api/os`、`/api/org`、`/api/monitor`、`/api/jev`、`/api/analysis` 等
     无斜杠请求被 nginx 补斜杠 301，且 Location 为绝对 URL、端口被吞成 80
     （容器内 server_port=80）→ 浏览器/前端 fetch 一律打不开。
     server 级一行 `absolute_redirect off;` 即可全部转相对路径，已验证 5/5。
  2. `/bt_view.html`、`/bt2_view.html` 被 `location ^~ /bt`（反代 8091）前缀吞掉
     → 404。文件其实在 webroot 内。精确匹配 `location = /bt*_view.html` 可解。
  3. `X-Portal-Token` 无需 nginx 显式透传：`proxy_pass_request_headers` 默认 on，
     客户端 header 本就转发。此前「经 8734 恒 403」的真因是**两个 token 文件内容
     不一致**：服务端按 PORTAL_API_TOKEN → _vault\secrets\portal_api_token →
     C:\Temp\portal_api_token 的优先级读取，而页面提示只指了最后那个旧文件。

本测试锁三件事：
  · 前端提示必须指向真正生效的 token 文件（vault 优先）；
  · 收口副本存在且只含这 3 处改动，未夹带其它变更；
  · 原件 portal/nginx.conf 保持未应用状态（应用需老田单独批准）。
"""
import hashlib
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PORTAL = os.path.dirname(HERE)
ROOT = os.path.dirname(PORTAL)
BT2 = open(os.path.join(PORTAL, "bt2_view.html"), encoding="utf-8").read()
NGINX = open(os.path.join(PORTAL, "nginx.conf"), "rb").read()
PENDING = os.path.join(ROOT, "reports", "nginx.conf.pending-r7")

# 原件基线 sha256（2026-11-05 21:55，58501 B / 1220 CRLF）。若此断言失败，
# 说明有人改了原件却没同步本契约——先确认改动是否经批准，再更新此值。
NGINX_SHA256 = "c67da53437ffa964"


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
    # 告诉用户「该取哪个」的路径必须出现在解读优先级之前
    assert body.index("_vault/secrets") < body.index("环境变量"), "应先给出该取的文件"


def test_bt2_token_not_in_plain_prompt_string_only():
    """prompt() 文本不得含裸 token 值（只描述路径）。"""
    body = BT2.split("function promptBt2Token(")[1].split("function bt2Err(")[0]
    assert not re.search(r"[0-9a-f]{64}", body), "提示里不得出现 64 位 hex token"


# ---------------------------------------------------------------- 收口副本
def _pending_bytes():
    assert os.path.isfile(PENDING), "缺少收口副本 reports/nginx.conf.pending-r7"
    return open(PENDING, "rb").read()


def test_pending_exists_and_keeps_crlf():
    b = _pending_bytes()
    assert b.count(b"\r\n") == 1226, "应为 1220 原行 + 6 新行，且全 CRLF"
    assert b.count(b"\n") == b.count(b"\r\n"), "不得出现裸 LF"


def test_pending_absolute_redirect_is_server_level():
    s = _pending_bytes().decode("utf-8")
    # 必须紧跟 listen/server_name 之后（server 级），不是塞在某个 location 里
    assert re.search(r"server_name _;\r\n    absolute_redirect off;\r\n", s), \
        "absolute_redirect off 必须位于 server 级"
    assert s.count("absolute_redirect off;") == 12, "原有 11 处 location 级 + 新增 1 处 server 级"


def test_pending_bt_exact_match_before_prefix():
    s = _pending_bytes().decode("utf-8")
    for page in ("/bt_view.html", "/bt2_view.html"):
        assert s.count("location = %s" % page) == 1, page
    # 精确匹配可以写在 ^~ 之后（nginx 语义上仍然优先），但为可读性放在前面
    i_exact = s.index("location = /bt2_view.html")
    i_prefix = s.index("location ^~ /bt {")
    assert i_exact < i_prefix


def test_pending_no_token_header_injection():
    """token 透传无需新增：nginx `proxy_pass_request_headers` 默认 on 会转发客户端
    header，且原件已有 1 处显式 `proxy_set_header X-Portal-Token`（见 nginx.conf
    的 legion 反代段）。副本不得新增该指令——避免多一个以为必要、实则多余的开关。"""
    s = _pending_bytes().decode("utf-8")
    orig = NGINX.decode("utf-8")
    assert s.count("proxy_set_header X-Portal-Token") == \
        orig.count("proxy_set_header X-Portal-Token") == 1, \
        "副本不得新增 X-Portal-Token 透传（存量 1 处保持不变）"


def test_pending_diff_is_only_three_changes():
    """除 3 处改动外，副本与原件逐行等价（防夹带）。"""
    orig = NGINX.decode("utf-8").replace("\r\n", "\n").split("\n")
    pend = _pending_bytes().decode("utf-8").replace("\r\n", "\n").split("\n")
    added = [ln for ln in pend if ln not in orig]
    assert len(added) == 5, "新增实质行应为 5（1 行 off + 2 注释 + 2 location）"
    assert all(ln.strip().startswith("#")
               or "absolute_redirect off;" in ln
               or ln.strip().startswith("location = /bt") for ln in added), added


# ---------------------------------------------------------------- 原件保护
def test_original_still_unapplied():
    """原件必须保持未应用状态：应用需老田单独批准。"""
    assert hashlib.sha256(NGINX).hexdigest().startswith(NGINX_SHA256), \
        "portal/nginx.conf 已被改动——本契约假定它仍是待批准状态"
    s = NGINX.decode("utf-8")
    assert "location = /bt_view.html" not in s
    assert "location = /bt2_view.html" not in s
    # server 级不得有 absolute_redirect off（只有 location 级那 10 处）
    assert not re.search(r"server_name _;\r\n    absolute_redirect off;", s)


def test_backup_exists_for_rollback():
    """回滚备份必须存在。"""
    bak = os.path.join(ROOT, "reports", "nginx.conf.bak-r7")
    assert os.path.isfile(bak), "缺少回滚备份 reports/nginx.conf.bak-r7"
    # 备份必须与当前原件字节一致（应用前拍的原件快照）
    assert open(bak, "rb").read() == NGINX, "备份与原件不一致，回滚将不可信"
