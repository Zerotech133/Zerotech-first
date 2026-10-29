# -*- coding: utf-8 -*-
"""P4 批3：知识图谱域 Mixin（kg 页族 17 方法 + 卡片/标注 JS 与小函数）。
Handler 壳经基座多继承并入（禁 import *）。拍板：D1(v1画布)/D2(net_html) 均留，纯搬运零删减。
"""
# 拆分第一批：顶层 helper 实体在 kg/kg_service.py；此处保留同名薄壳（_mixin_integrity 方法集校验依赖模块级定义）。
def _kg_label_save(*args, **kwargs):
    """label 保存（实体在 kg/kg_service.py）。"""
    from kg.kg_service import _kg_label_save as _impl
    return _impl(*args, **kwargs)


def _kg_label_diff(*args, **kwargs):
    """label diff（实体在 kg/kg_service.py）。"""
    from kg.kg_service import _kg_label_diff as _impl
    return _impl(*args, **kwargs)


def _kg_card_fb_save(*args, **kwargs):
    """card feedback 保存（实体在 kg/kg_service.py）。"""
    from kg.kg_service import _kg_card_fb_save as _impl
    return _impl(*args, **kwargs)



from kg.kg_js import _KG_CARD_JS, _KG_LABEL_JS  # noqa: F401  (2026-10-30 拆分收尾：常量迁 kg_js，此处再导出兼容)



# ══ Handler 方法（经 MRO 并入基座）══

class Handler:
    def _kg_ask_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_ask_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_battle_page(self, *args, **kwargs):
        """委托 kg.kg_pages_events（拆分第三批）。"""
        from kg.kg_pages_events import _kg_battle_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_lexicon_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_lexicon_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_doc_page(self, *args, **kwargs):
        """委托 kg.kg_pages_events（拆分第三批）。"""
        from kg.kg_pages_events import _kg_doc_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_events_page(self, *args, **kwargs):
        """委托 kg.kg_pages_events（拆分第三批）。"""
        from kg.kg_pages_events import _kg_events_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_canvas_v2_page(self, *args, **kwargs):
        """委托 kg.kg_pages_canvas（拆分第四批）。"""
        from kg.kg_pages_canvas import _kg_canvas_v2_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_canvas_page(self, *args, **kwargs):
        """委托 kg.kg_pages_canvas（拆分第四批）。"""
        from kg.kg_pages_canvas import _kg_canvas_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_resonance_block(self):
        from kg.kg_html import _kg_resonance_block as _impl
        return _impl()
    def _kg_shadow_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_shadow_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_label_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_label_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_card_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_card_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_flow_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_flow_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_map_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_map_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_company_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_company_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_net_html(self, mg):
        from kg.kg_html import _kg_net_html as _impl
        return _impl(mg)
    def _kg_overview_html(self, ov):
        from kg.kg_html import _kg_overview_html as _impl
        return _impl(ov)
    def _kg_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_page as _impl
        return _impl(self, *args, **kwargs)
