"""门户模块拆分后的兼容回归测试。"""

import importlib
import sys
from pathlib import Path


PORTAL_DIR = Path(__file__).resolve().parent
if str(PORTAL_DIR) not in sys.path:
    sys.path.insert(0, str(PORTAL_DIR))


def test_handler_back_bar_uses_bound_instance_method():
    portal_api = importlib.import_module("portal_api")
    handler = object.__new__(portal_api.Handler)

    html = handler._back_bar_html()

    assert "href='/portal'" in html
    assert "← 返回" in html


def test_portal_api_exports_sa_history_reader_for_fin_page_injection():
    portal_api = importlib.import_module("portal_api")

    assert callable(portal_api._sa_hist_read)


def test_lineage_page_loads_deferred_unified_navigation():
    html = (PORTAL_DIR / "lineage.html").read_text(encoding="utf-8")

    assert '<script src="/static/nav.js" defer></script>' in html
