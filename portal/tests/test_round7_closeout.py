r"""R7 契约（2026-11-06）：L2 收口「已应用」，锁住 3 处改动 + token 路径纠错。

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

应用记录（2026-11-06 05:38，老田批准）：
  · 原件 c67da534 / 58501 B / 1220 CRLF → 7e0ce85b / 58811 B / 1226 CRLF
  · 方式：原地覆盖保持 inode（容器单文件 bind mount，mv 会导致挂载失效）
  · 步骤：覆盖 → 容器内 nginx -t → nginx -s reload（master PID 1 未重启）
  · 回滚：reports/nginx.conf.bak-r7 即原 c67da534 字节快照
  · 附带：C:\Temp\portal_api_token（旧值 c6c1447c）已走回收站删除，
    vault 值 f19d1c90 仍为唯一生效来源，删除后带 token POST 仍 200

本测试锁四件事：
  · 前端提示必须指向真正生效的 token 文件（vault 优先）；
  · portal/nginx.conf 已应用且只含这 3 处改动（以 bak-r7 为未应用基线比对）；
  · 备份必须是「应用前」字节，否则回滚不可信；
  · 副本若存在须与已应用原件字节一致（reports/ 被 gitignore，缺则跳过）。
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
PENDING = os.path.join(ROOT, "reports", "nginx.conf.pending-r7")
BAK = os.path.join(ROOT, "reports", "nginx.conf.bak-r7")

# 应用前基线（2026-11-05 21:55，58501 B / 1220 CRLF）与应用后（2026-11-06 05:38）
BASE_SHA256 = "c67da53437ffa964"
APPLIED_SHA256 = "7e0ce85bf922d052"


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


# ---------------------------------------------------------------- 已应用态
def test_nginx_applied_sha256():
    """原件必须已是应用后版本；若失败说明被改回或又被人动过。"""
    got = hashlib.sha256(NGINX).hexdigest()
    assert got.startswith(APPLIED_SHA256), \
        "portal/nginx.conf 非应用后版本（sha256 %s），先确认是否被回滚或再改" % got[:16]
    assert NGINX.count(b"\r\n") == 1226, "应为 1220 原行 + 6 新行"
    assert NGINX.count(b"\n") == NGINX.count(b"\r\n"), "不得出现裸 LF"


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


def test_no_token_header_injection():
    """token 透传无需新增：nginx `proxy_pass_request_headers` 默认 on 会转发客户端
    header，且存量已有 1 处显式 `proxy_set_header X-Portal-Token`（legion 反代段）。
    不得新增该指令——避免多一个以为必要、实则多余的开关。"""
    assert os.path.isfile(BAK), "缺少基线 reports/nginx.conf.bak-r7，无法比对"
    base = open(BAK, "rb").read().decode("utf-8")
    assert NGINX.decode("utf-8").count("proxy_set_header X-Portal-Token") == \
        base.count("proxy_set_header X-Portal-Token") == 1, \
        "不得新增 X-Portal-Token 透传（存量 1 处保持不变）"


def test_applied_diff_is_only_three_changes():
    """除 3 处改动外，已应用原件与未应用基线逐行等价（防夹带）。"""
    assert os.path.isfile(BAK), "缺少基线 reports/nginx.conf.bak-r7"
    base = open(BAK, "rb").read().decode("utf-8").replace("\r\n", "\n").split("\n")
    now = NGINX.decode("utf-8").replace("\r\n", "\n").split("\n")
    added = [ln for ln in now if ln not in base]
    assert len(added) == 5, "新增实质行应为 5（1 行 off + 2 注释 + 2 location），实得 %d" % len(added)
    assert all(ln.strip().startswith("#")
               or "absolute_redirect off;" in ln
               or ln.strip().startswith("location = /bt") for ln in added), added


# ---------------------------------------------------------------- 回滚与副本
def test_backup_is_pre_apply_baseline():
    """备份必须是「应用前」字节，回滚才可信；它不得等于当前原件。"""
    assert os.path.isfile(BAK), "缺少回滚备份 reports/nginx.conf.bak-r7"
    b = open(BAK, "rb").read()
    assert hashlib.sha256(b).hexdigest().startswith(BASE_SHA256), \
        "备份不是应用前基线（c67da534），回滚会回到错误状态"
    assert b != NGINX, "备份与当前原件相同，说明备份被应用后覆盖，失去回滚价值"
    s = b.decode("utf-8")
    assert "location = /bt_view.html" not in s
    assert not re.search(r"server_name _;\r\n    absolute_redirect off;", s)


@pytest.mark.skipif(not os.path.isfile(PENDING),
                    reason="reports/ 被 .gitignore 忽略，副本可能不在本机")
def test_pending_matches_applied():
    """副本若存在，须与已应用原件字节一致（副本是应用来源，不应漂移）。"""
    assert open(PENDING, "rb").read() == NGINX, "副本与已应用原件不一致"
