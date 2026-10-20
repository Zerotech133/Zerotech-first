# -*- coding: utf-8 -*-
"""FD-B 商务运营面 · 离线契约测试（2026-10-20 飞书中台 P2.5）

全部为读文件断言（不起服务、不发网络请求），覆盖：
1. nginx.conf 有 /sub-admin location（照 notify-command/ops-board 模式）
2. 只读纪律：sub_admin.html 无写操作（无 POST，全部 fetch 显式 GET）
3. 降级诚实：degraded 降级区 + 只读声明 + 页头「本人/商务域专用 · 邓芝统辖」
4. 价格不编造：页面无 ¥ 价格数字硬编码，档位文案含「待定价」
5. observe 卡引用 /api/notify/queue（customer 域留痕数据源）
6. 订阅者总览引用 /api/subscription/status，且诚实标注批量列表挂账
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NGINX = os.path.join(ROOT, "portal", "nginx.conf")
HTML = os.path.join(ROOT, "portal", "static", "sub_admin.html")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_nginx_has_sub_admin_location():
    """nginx.conf 照 notify-command/ops-board 模式有 /sub-admin 静态页 location。"""
    conf = _read(NGINX)
    assert conf.count("location = /sub-admin") == 1
    # 静态页模式判例：try_files + no-cache
    assert re.search(
        r"location = /sub-admin \{ try_files /static/sub_admin\.html =404; "
        r'add_header Cache-Control "no-cache"; \}', conf)
    # 判例锚点仍在（零破坏）
    assert conf.count("location = /ops-board") == 1
    assert conf.count("location = /notify-command") == 1


def test_sub_admin_html_readonly_no_write_ops():
    """只读纪律：页面无任何写操作（无 POST、无 DELETE/PUT、无 form）。"""
    html = _read(HTML)
    for m in re.finditer(r"fetch\([^)]*\)", html):
        seg = m.group(0)
        assert "POST" not in seg.upper(), "页面出现 POST 写操作: %s" % seg
    assert "method:'GET'" in html  # 全部 fetch 显式 GET
    assert "method:'DELETE'" not in html and "method:'PUT'" not in html
    assert "XMLHttpRequest" not in html
    assert "<form" not in html  # open_id 查询用 input+按钮触发 GET，无表单提交


def test_sub_admin_html_degraded_and_readonly_banner():
    """降级诚实：degraded 降级区 + 只读声明 + 页头「本人/商务域专用 · 邓芝统辖」。"""
    html = _read(HTML)
    assert 'id="degraded"' in html
    assert "已降级" in html
    assert "不显示静态兜底数据" in html or "不显示兜底数字" in html
    # 只读声明文案
    assert "只读投影" in html
    assert "本页没有任何写操作" in html
    # 页头统辖标注（派工单分区 4）
    assert "本人/商务域专用" in html
    assert "邓芝统辖" in html
    # 判例一致：60s 自动刷新 + 8s AbortController 超时 + same-origin
    assert "setInterval(load,60000)" in html
    assert "credentials:'same-origin'" in html
    assert "8000" in html


def test_sub_admin_html_no_price_hardcoded():
    """价格不编造：无 ¥ 价格数字硬编码（页面不应出现任何 ¥ 金额）。"""
    html = _read(HTML)
    assert "¥" not in html and "￥" not in html, "页面出现 ¥ 价格，涉嫌编造报价"
    # 也不得出现"元/月"类报价表述
    assert "元/月" not in html and "元/年" not in html


def test_sub_admin_html_plan_pending_pricing():
    """档位卡：SEED_PLANS 两档静态展示，价格原样标注「待定价」。"""
    html = _read(HTML)
    assert "internal" in html
    assert "basic_monthly" in html
    assert "待定价" in html
    # internal 档不限额 / basic_monthly 30 天日 20 条（SEED_PLANS 事实投影）
    assert "不限额" in html
    assert "30 天" in html and "20 条" in html
    # placeholder 诚实声明：不构成真实报价
    assert "不构成真实报价" in html or "不构成报价" in html


def test_sub_admin_html_observe_card_uses_queue_endpoint():
    """customer 域观察卡：引用 /api/notify/queue，过滤 domain-observe / subscription_required。"""
    html = _read(HTML)
    assert "/api/notify/queue" in html
    assert "domain-observe" in html
    assert "subscription_required" in html
    # 空态诚实：无留痕时明示「尚无 customer 域留痕」且空态≠无商机
    assert "尚无 customer 域留痕" in html


def test_sub_admin_html_subscriber_overview():
    """订阅者总览：引用 /api/subscription/status 逐个查询，诚实标注批量列表挂账。"""
    html = _read(HTML)
    assert "/api/subscription/status" in html
    # open_id 逐个查询入口
    assert 'id="open-id-input"' in html
    # 挂账诚实标注（本期无列表端点，不冒充全景）
    assert "挂账" in html
    assert "/api/subscription/list" in html
    assert "批量" in html
