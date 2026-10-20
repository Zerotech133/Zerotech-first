"""OS 归集层（os_commander）驾驶舱最小实现。

职责：从总线 board() 只读投影各板块状态点，归集为驾驶舱视图
（谁·在干什么·干到哪·产出在哪），缺失字段如实以 None 占位并记入
data_gaps，不造数、不补全。

OS 归集层铁律：只投影不造第二账——本层不维护任何独立台账，
一切数据以 bus.board() 为唯一事实来源；本层产出仅为内存中的
归集视图 dict，不落盘、不回写总线。

OS 归集层界面另立：本模块不含任何界面/前端逻辑，仅提供归集
数据结构（collect）与自身状态点自述（status_point）。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from roles.base_agent import BaseAgent, StatusPoint
from roles.message_bus import STATUS_POINT_FIELDS


class OsCommander(BaseAgent):
    """归集驾驶舱：bus.board() 的只读投影器（界面另立，不在此实现）。"""

    def __init__(self, **kw: Any) -> None:
        super().__init__(name="os_commander", role="command:collector",
                         level="command", **kw)
        # 最近一次归集的自述摘要（仅用于 status_point 自述，不构成第二账）。
        self._last_collect_summary: str = "尚未执行归集"

    def collect(self, bus: Any) -> Dict[str, Any]:
        """归集视图：bus.board() 只读投影，缺失字段如实标注。

        返回 {"generated_at", "points": [{agent, platform, doing,
        progress, artifact, ts}], "agents_online", "data_gaps"}。

        铁律：只投影不造第二账——不补全、不推断、不落盘；
        六字段缺失者如实以 None 占位并记入 data_gaps。
        """
        raw_points: List[Dict[str, Any]] = bus.board()  # 只读唯一事实来源
        points: List[Dict[str, Any]] = []
        data_gaps: List[Dict[str, Any]] = []
        for raw in raw_points:
            row = {field: raw.get(field) for field in STATUS_POINT_FIELDS}
            missing = [f for f in STATUS_POINT_FIELDS if row[f] is None]
            if missing:
                data_gaps.append({
                    "agent": row.get("agent"),
                    "missing_fields": missing,
                })
            points.append(row)
        if not raw_points:
            # 空总线如实标注：无可归集数据，不静默、不造数。
            data_gaps.append({"reason": "总线无状态点：无可归集数据"})
        self._last_collect_summary = f"最近归集 {len(points)} 条状态点"
        return {
            "generated_at": time.time(),
            "points": points,
            "agents_online": len(points),
            "data_gaps": data_gaps,
        }

    def status_point(self) -> StatusPoint:
        """自身归集状态自述：OS 归集层也是总线上的一个点。"""
        return StatusPoint(
            agent=self.name,
            platform="os",
            doing="归集各板块状态点",
            progress=self._last_collect_summary,
            artifact="collect(bus) 归集视图（只投影，不落盘）",
        )

    # ---------------- OS 单屏驾驶舱六平台汇聚视图（digest） ----------------
    #
    # UI 另立：digest 只输出单屏快照数据结构（v0 契约），不含任何
    # 渲染/前端逻辑。
    # 挂账：归集快照落盘策略待老田裁定——digest 只读不落盘、不回写
    # 总线、不造第二账；是否落盘/落盘位置/保留策略另行裁定后补充。

    def digest(self, bus: Any, registry: Any = None) -> Dict[str, Any]:
        """六平台单屏汇聚快照：bus.board() 只读投影 + 可选平台注册表。

        v0 契约：{"generated_at", "platforms": [{platform, label, doing,
        progress, artifact, ts, reported}], "agents_online", "data_gaps"}。

        - registry 缺省 None：不依赖注册表，按 board 顺序降级逐点展示
          （label 无配置来源，如实 None，不造数）；
        - registry 传入：按注册表顺序展示六平台；未上报平台如实
          reported=False（progress/artifact/ts 为 None，不造数）；
          上报平台按状态点投影，字段缺失如实 None 并记入 data_gaps；
        - registry 为 duck typing 协议：序列化即可迭代项，每项含
          name/label/doing 即可；不 import platform_agent 模块
          （避免跨工位耦合）；
        - agents_online 与 collect 同源同口径（board 点数）；
        - 铁律：只投影不造第二账——只读、不落盘、不回写总线。
        """
        raw_points: List[Dict[str, Any]] = bus.board()  # 只读唯一事实来源
        data_gaps: List[Dict[str, Any]] = []
        if not raw_points:
            # 空总线如实标注：无可归集数据，不静默、不造数。
            data_gaps.append({"reason": "总线无状态点：无可归集数据"})

        # board 按 agent 保留最新一条；同一平台多点时取 board 顺序首个。
        first_by_platform: Dict[Any, Dict[str, Any]] = {}
        for raw in raw_points:
            plat = raw.get("platform")
            if plat is not None and plat not in first_by_platform:
                first_by_platform[plat] = raw

        platforms: List[Dict[str, Any]] = []
        if registry is None:
            # 降级模式：按 board 顺序逐点展示，label 无配置来源如实 None。
            for raw in raw_points:
                platforms.append({
                    "platform": raw.get("platform"),
                    "label": None,
                    "doing": raw.get("doing"),
                    "progress": raw.get("progress"),
                    "artifact": raw.get("artifact"),
                    "ts": raw.get("ts"),
                    "reported": True,
                })
        else:
            reported_names: set = set()
            for entry in registry:
                name = _registry_field(entry, "name")
                label = _registry_field(entry, "label")
                reg_doing = _registry_field(entry, "doing")
                raw = first_by_platform.get(name)
                if raw is None:
                    # 未上报平台如实标注：reported=False，运行时字段不造数。
                    data_gaps.append({"platform": name, "reason": "未上报"})
                    platforms.append({
                        "platform": name,
                        "label": label,
                        "doing": reg_doing,
                        "progress": None,
                        "artifact": None,
                        "ts": None,
                        "reported": False,
                    })
                    continue
                reported_names.add(name)
                row = {
                    "platform": name,
                    "label": label,
                    "doing": raw.get("doing"),
                    "progress": raw.get("progress"),
                    "artifact": raw.get("artifact"),
                    "ts": raw.get("ts"),
                    "reported": True,
                }
                missing = [f for f in ("doing", "progress", "artifact", "ts")
                           if row[f] is None]
                if missing:
                    data_gaps.append({"platform": name,
                                      "missing_fields": missing})
                platforms.append(row)
            # 注册表之外的已上报状态点如实挂账，不静默丢弃。
            for plat, raw in first_by_platform.items():
                if plat not in reported_names:
                    data_gaps.append({
                        "agent": raw.get("agent"),
                        "platform": plat,
                        "reason": "平台不在注册表内",
                    })
        return {
            "generated_at": time.time(),
            "platforms": platforms,
            "agents_online": len(raw_points),
            "data_gaps": data_gaps,
        }


def _registry_field(entry: Any, field: str) -> Any:
    """duck typing 取注册表条目字段：dict 用 get，对象用 getattr。

    协议：注册表为可迭代项序列，每项含 name/label/doing 即可；
    不 import platform_agent 模块，避免跨工位耦合。
    """
    if isinstance(entry, dict):
        return entry.get(field)
    return getattr(entry, field, None)
