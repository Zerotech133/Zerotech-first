# -*- coding: utf-8 -*-
"""trace_drilldown 看板的守卫测试（R12 批次 6）。

这个看板是**只读运维工具**，无鉴权，安全性完全依赖三条约束：

1. 只绑 127.0.0.1，绝不 0.0.0.0；
2. 没有任何 nginx 反代把它暴露到公网（一旦反代 = 无鉴权公开 trace 数据）；
3. 对 traces.sqlite 强制 query_only，任何写语句直接报错。

任何一个被改掉都是安全事故，且不会有报错提示，所以用测试钉死。
"""
import re
import sys
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parents[1]
API = PORTAL / "api_trace_drilldown.py"
HTML = PORTAL / "trace_drilldown.html"
BAT = PORTAL / "start_trace_drilldown.bat"

sys.path.insert(0, str(PORTAL))


def _api_src():
    return API.read_text(encoding="utf-8")


# ---------- 1. 只读约束 ----------

def test_sqlite_connection_is_hard_readonly():
    """PRAGMA query_only 必须存在——去掉它，看板就能写 traces.sqlite。"""
    assert "PRAGMA query_only=1" in _api_src()


def test_no_write_statements_in_source():
    """源码不得出现任何写 SQL 或文件写操作。

    注意：不能用裸 \\bINSERT\\b —— Python 的 list.insert() 会误命中。
    必须限定为 SQL 形态（INSERT INTO / UPDATE..SET / DELETE FROM）。
    """
    src = _api_src()
    for pat in (r"\bINSERT\s+INTO\b", r"\bUPDATE\s+\w+\s+SET\b", r"\bDELETE\s+FROM\b",
                r"\bDROP\s+TABLE\b", r"\bCREATE\s+TABLE\b", r"\bALTER\s+TABLE\b"):
        assert not re.search(pat, src, re.I), "出现写语句：%s" % pat
    for pat in (r"\.write_text\(", r"shutil\.rmtree", r"os\.remove\(", r"\.unlink\("):
        assert not re.search(pat, src), "出现写/删文件操作：%s" % pat


def test_sql_is_parameterized_not_concatenated():
    """trace 查询必须用占位符，不能把入参拼进 SQL 字符串。"""
    src = _api_src()
    # 入参 name_filter 必须走参数绑定
    assert '"%" + name_filter + "%"' in src
    assert "WHERE name LIKE ?" in src or "name LIKE ?" in src
    # 出现 f-string 拼 SELECT 即视为注入风险
    assert not re.search(r'f"""\s*SELECT|f"\s*SELECT', src, re.I)


# ---------- 2. 暴露面约束 ----------

def test_default_host_is_loopback():
    """默认绑定必须是 127.0.0.1；出现 0.0.0.0 即视为缺陷。"""
    src = _api_src()
    m = re.search(r'"--host",\s*default="([^"]+)"', src)
    assert m, "未找到 --host 默认值"
    assert m.group(1) == "127.0.0.1"
    assert "0.0.0.0" not in src, "源码出现 0.0.0.0，会把只读看板暴露到全网"


def test_bat_also_binds_loopback_and_marks_scope():
    assert BAT.is_file(), "缺少启动脚本"
    txt = BAT.read_text(encoding="utf-8", errors="replace")
    assert "127.0.0.1:8106" in txt
    assert "--port 8106" in txt


def test_no_nginx_reverse_proxy_for_port():
    """8106 一旦被 nginx 反代出去，就是无鉴权公开 trace 数据。

    这条检查覆盖仓库内所有 nginx 配置（含 pending/bak/SUPERSEDED 副本），
    任一版本出现 upstream 8106 都要人工复核。
    """
    hits = []
    for p in PORTAL.parent.rglob("nginx*.conf*"):
        if ".git" in p.parts:
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "8106" in txt:
            hits.append(str(p.relative_to(PORTAL.parent)))
    assert not hits, "nginx 配置中出现 8106，需人工确认是否有鉴权：%s" % hits


# ---------- 3. 前端 XSS 防线 ----------

def test_esc_escapes_quotes_for_attribute_context():
    """esc() 用在 data-id="${esc(...)}" 属性位置，只转 &<> 会被引号逃逸。"""
    html = HTML.read_text(encoding="utf-8")
    m = re.search(r"^function esc\(s\)\{.*$", html, re.M)
    assert m, "未找到 esc 定义"
    body = m.group(0)
    assert "&quot;" in body, "esc 未转义双引号（属性位置可被逃逸）"
    assert "&#39;" in body, "esc 未转义单引号"
    # 属性位置确实用到了 esc
    assert 'data-id="${esc(' in html


def test_html_has_no_external_resources():
    """看板是离线只读工具，不得引用外部 CDN/脚本（防外链泄露与供应链风险）。"""
    html = HTML.read_text(encoding="utf-8")
    assert "http://" not in html and "https://" not in html


# ---------- 4. 路由与降级语义 ----------

@pytest.mark.parametrize("path", ["/api/trace/", "/api/trace/data",
                                  "/api/trace/lineage", "/api/trace/kg"])
def test_routes_declared(path):
    assert path in _api_src(), "路由未实现：%s" % path


def test_kg_endpoint_degrades_gracefully():
    """Neo4j 挂了要返回 ok:false 的 JSON，不能抛未捕获异常。"""
    src = _api_src()
    assert '"Neo4j 口令为空' in src or "凭据未配置" in src
    # kg 分支必须有 try/except 包裹
    assert "except Exception" in src


def test_lineage_missing_password_is_actionable():
    """缺 LINEAGE_PASSWORD 时错误信息要指路，而不是静默返回空。"""
    assert "LINEAGE_PASSWORD 未配置（env 或 quant_backtest/.env.local）" in _api_src()
