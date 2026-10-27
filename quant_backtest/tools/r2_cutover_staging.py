"""R2 灰度切流护栏（工位 #373，门 N12 R2 发布审批包）。

休眠默认策略：本护栏默认「不武装」(armed=False)。即便被武装，只要 R2 前置
验收检查项有任何一项未达成，trigger() 也强制停止，绝不实际触发 R2 切流。

背景（来自 N12 清单）：R2 = 选股生产切流，前置验收含 R0 已闭环、R1 恒
BLOCK_CUTOVER、N3/N5–N8/N10/N11 全部满足。当前这些门**均未满足**：N12 草案
明列 R2 前置验收表全未达成。因此本护栏当前在任何路径下都不可达「实际切流」。

真实 R2 切流触发须经老田五要素批准（命令全文 / 影响面 / 不可逆点 /
替代方案 / 回滚预案）且 N12 审批包到齐；本护栏在任何前置缺失时强制停止，
激活与填要素留待老田拍板。

依赖：仅 Python 标准库。
"""

from __future__ import annotations

# R2 前置验收检查项（每一项当前默认未满足）。字典便于扩展：键为检查项名，
# 值为当前是否满足；True 表示已满足，False 表示未满足。
DEFAULT_PRECONDITIONS = {
    "R0_closed": False,  # R0 已闭环
    "R1_BLOCK_enforced": False,  # R1 恒 BLOCK_CUTOVER
    "N3_adopted": False,  # N3 已采用
    "N5_signed": False,  # N5 真实通道回签
    "N6_ceremony_done": False,  # N6 密钥 ceremony 完成
    "N7_done": False,  # N7 完成
    "N8_semantics_decided": False,  # N8 语义已定
    "N10_nav_mounted": False,  # N10 导航已挂载
    "N11_pool_decided": False,  # N11 标的池已定
    "N12_five_elements_approved": False,  # N12 五要素已批准
}


class R2CutoverStaging:
    """R2 灰度切流护栏。

    默认未武装。arm() 仅模拟「武装」，绝不切流；trigger() 在任何前置缺失时
    强制停止，绝不实际触发 R2 切流。
    """

    def __init__(self, armed: bool = False) -> None:
        # 休眠默认：armed 默认 False（不武装）。
        self._armed = bool(armed)
        # 复制一份，避免外部改动 DEFAULT_PRECONDITIONS 影响实例。
        self.preconditions = dict(DEFAULT_PRECONDITIONS)

    def is_armed(self) -> bool:
        """护栏是否已武装。"""
        return self._armed

    def validate_checklist(self) -> tuple[bool, list[str]]:
        """校验 R2 前置检查项。

        返回 (ok, missing)：
        - ok     : 全部满足为 True，否则 False。
        - missing: 未满足的检查项名称列表。

        当前所有前置默认未满足 → ok=False，missing=全部检查项。
        """
        missing = [k for k, v in self.preconditions.items() if not v]
        ok = len(missing) == 0
        return ok, missing

    def arm(self) -> None:
        """模拟「武装」护栏：仅置 armed=True，绝不触发任何切流。

        真实 R2 切流触发须经老田五要素批准且 N12 审批包到齐；武装本身不构成
        任何实际切流动作。
        """
        self._armed = True

    def trigger(self) -> dict:
        """核心护栏：尝试触发 R2 切流。

        返回 dict：
        - 未武装           : {"triggered": False, "reason": "not_armed"}
        - 已武装但前置缺失 : {"triggered": False, "reason": "preconditions_unmet",
                              "missing": [...]}
        - 已武装且全部满足 : {"triggered": True, ...}（当前不可达，留作未来）

        注意：本方法在任何情况下都不执行真实切流；当前所有前置未满足，
        因此永远返回 triggered=False。
        """
        if not self.is_armed():
            return {"triggered": False, "reason": "not_armed"}
        ok, missing = self.validate_checklist()
        if not ok:
            return {
                "triggered": False,
                "reason": "preconditions_unmet",
                "missing": missing,
            }
        # 以下分支当前不可达：需 armed 且全部前置满足（须经 N12 五要素批准）。
        # 即便命中，也只在此返回信号，绝不在本工位执行真实切流。
        return {
            "triggered": True,
            "reason": "approved",
            "missing": [],
        }
