"""平台席模板骨架（选股/量化/研发/PPT/商务/审查 六平台共用）。

定位（老田 2026-10-20 校准）：业务板块是自运行的模拟经营体（自营盘），
像一盘持续进行的游戏——自主完成模拟交易、模拟推演，不依赖任务单逐单
驱动；任务单只承载调度中心的专项指令。自运行产出（战绩、推演结论）
按「基层→平台→中台→本人」层级汇报。

边界：模拟盘交易不触真实资金；真实交易仍须独立风控 PASS + 本人签准
（approval_gate）。平台不得绕过中台直接向本人发零散消息，也不得直连
外部 IM 通道——上行一律经 message_bus。
"""
from __future__ import annotations

import random
from typing import Any, Dict, Iterator, List

from roles.base_agent import BaseAgent, Report, StatusPoint, TaskState

PLATFORMS = ("stock_picking", "quant_trading", "r_and_d",
             "ppt_studio", "biz_negotiation", "review_audit")


class PlatformAgent(BaseAgent):
    def __init__(self, platform: str, daily_brief_to: str = "tian_feng",
                 **kw: Any) -> None:
        if platform not in PLATFORMS:
            raise ValueError(f"未知平台: {platform}")
        super().__init__(name=f"platform:{platform}", role="platform:head",
                         level="platform", **kw)
        self.platform = platform
        self.daily_brief_to = daily_brief_to

    # ---- 自运行主循环：模拟经营体常态 ----

    def self_run_loop(self, steps: int = 1) -> Iterator[StatusPoint]:
        """持续自运行（当前为可迭代占位）：每步 yield 一个 StatusPoint。

        占位语义：每个步进自述「模拟推演循环步进（骨架占位）」，
        progress/artifact 如实留空，不造数。
        TODO(领域实现): 各平台各自的推演引擎（如量化的回测/沙盘流水线、
        选股的信号演练）替换本占位循环；循环产出记入本平台专业账本，
        异常走 risk_guard。
        """
        for _ in range(max(0, steps)):
            yield StatusPoint(
                agent=self.name,
                platform=self.platform,
                doing="模拟推演循环步进（骨架占位）",
                progress="",
                artifact="",
            )

    # ---- 调度中心专项指令（区别于自运行） ----

    def receive_task(self, task: Dict[str, Any]) -> TaskState:
        """承接调度中心专项指令（非自运行常态）。

        专项指令语义：任务单只承载调度中心的专项指令，与自运行常态
        严格区分；本席确认承接（state=assigned），完成后经 zhao_yun
        核验，不自行宣布 DONE。
        TODO(领域实现): 按 platform 分派到本平台专业执行器；一单一事、
        窄职责。
        """
        return TaskState.ASSIGNED

    # ---- 平台日报 ----

    def daily_report(self) -> Report:
        """平台日报：结果、依据、异常、需裁决事项。

        TODO(领域实现): 日报字段契约（与文枢席对齐）；缺失数据须显式
        标注「缺失」，不得以退出码或健康报告冒充业务结果。
        """
        raise NotImplementedError

    # ---- 专业账本 ----

    def ledger_snapshot(self) -> Dict[str, Any]:
        """平台专业账本快照（只读投影给 OS，不造第二账）。

        TODO(领域实现): 各平台账本 schema 与导出。
        """
        raise NotImplementedError


class QuantPlatformAgent(PlatformAgent):
    """量化平台接入试点：最小模拟推演引擎（工位L，S3 正式波次）。

    【模拟声明 · 红线重申】
    - 本类全部产出（行情、持仓、盈亏、账本、日报）均为**模拟**数据：
      行情由 random.Random(seed) 随机步进生成，不含任何真实行情数据；
      盈亏为模拟持仓的模拟盯市结果，不构成真实战绩。
    - 模拟盘不触真实资金，不下任何真实委托；真实交易必须走独立风控
      PASS + 本人签准（approval_gate），任何代码路径不得绕行。
    - 模拟账本为纯内存对象：不落盘、不造第二账；ledger_snapshot 只做
      只读投影。
    """

    # 模拟标的（虚构代码，明确标注 SIM 前缀，禁止映射到真实证券）
    SIM_SYMBOL = "SIM:DEMO"

    def __init__(self, seed: int = 20261020, **kw: Any) -> None:
        """构造模拟推演席。

        seed：随机种子，同种子实例的推演序列完全可复现。
        其余参数（report_to/bus_url/metrics_port 等）透传 PlatformAgent。
        """
        super().__init__(platform="quant_trading", **kw)
        # 席位名按派工单固定为 quant_platform（覆盖父类 platform:* 命名）
        self.name = "quant_platform"
        self.seed = seed
        self._rng = random.Random(seed)      # 模拟行情随机源（种子可控）
        self._price = 100.0                  # 模拟行情价格（非真实行情）
        self._positions: Dict[str, Dict[str, float]] = {}  # 模拟持仓（内存）
        self._cum_pnl = 0.0                  # 累计模拟盈亏（模拟盯市）
        self._steps = 0                      # 已执行模拟步数
        self._day_points: List[StatusPoint] = []  # 当日状态点（内存）

    # ---- 模拟推演引擎（覆写骨架占位循环） ----

    def self_run_loop(self, steps: int = 1) -> Iterator[StatusPoint]:
        """最小模拟推演引擎：模拟行情随机步进 + 模拟持仓盈亏累计。

        每步：模拟行情按高斯随机收益率步进（seed 驱动，可复现）；
        首步开立一笔模拟持仓（SIM:DEMO，模拟盘，不触真实资金），其后
        逐步盯市累计模拟盈亏。每步产出并留存一个 StatusPoint：
        doing="模拟推演"，progress=步进摘要，artifact=模拟账本摘要。
        全部为模拟数据，不含真实行情/资金。
        """
        total = max(0, steps)
        for i in range(1, total + 1):
            self._step_sim()
            point = StatusPoint(
                agent=self.name,
                platform=self.platform,
                doing="模拟推演",
                progress=self._progress_summary(i, total),
                artifact=self._artifact_summary(),
            )
            self._day_points.append(point)
            yield point

    def _step_sim(self) -> None:
        """单步模拟：行情随机步进（模拟）+ 模拟持仓盯市（模拟）。"""
        ret = self._rng.gauss(0.0, 0.01)  # 模拟步收益率，非真实行情
        self._price = round(self._price * (1.0 + ret), 4)
        if self.SIM_SYMBOL not in self._positions:
            # 首步开立模拟持仓（模拟盘：不触真实资金、不下真实委托）
            self._positions[self.SIM_SYMBOL] = {
                "qty": 100.0, "avg_cost": self._price}
        pos = self._positions[self.SIM_SYMBOL]
        self._cum_pnl = (self._price - pos["avg_cost"]) * pos["qty"]
        self._steps += 1

    def _progress_summary(self, i: int, total: int) -> str:
        """步进摘要（progress 字段，显式标注模拟）。"""
        return (f"模拟步进{i}/{total}: 模拟行情={self._price:.2f}, "
                f"模拟持仓={len(self._positions)}笔, "
                f"累计模拟盈亏={self._cum_pnl:+.2f}(模拟)")

    def _artifact_summary(self) -> str:
        """模拟账本摘要（artifact 字段，显式标注模拟/内存不落盘）。"""
        return (f"模拟账本(内存不落盘): 持仓{len(self._positions)}笔/"
                f"累计模拟盈亏{self._cum_pnl:+.2f}/步数{self._steps}(模拟)")

    # ---- 平台日报（覆写骨架占位） ----

    def daily_report(self) -> Report:
        """平台日报：汇总当日状态点与模拟账本投影（纯模拟）。

        字段缺失如实标注（如当日未步进则写明「无」），不造数；
        level="platform"，经 report_up 走 message_bus 上行至中台
        （田丰席），不绕过中台直发本人。
        """
        snap = self.ledger_snapshot()
        lines = [
            "[模拟] quant_platform 平台日报（模拟推演试点，非真实战绩）",
            f"模拟步数：{snap['步数']}",
            f"模拟持仓数：{snap['持仓数']}",
            f"累计模拟盈亏：{snap['累计模拟盈亏']:+.2f}（模拟）",
        ]
        if self._day_points:
            lines.append(f"当日状态点：{len(self._day_points)} 条，"
                         f"最新：{self._day_points[-1].progress}")
        else:
            lines.append("当日状态点：无（今日尚未模拟步进，如实标注）")
        lines.append("红线：模拟盘不触真实资金；真实交易须独立风控 "
                     "PASS+本人签准（approval_gate）。")
        return Report(level="platform", source=self.name,
                      subject="[模拟][平台日报] quant_platform",
                      body="\n".join(lines))

    # ---- 模拟账本只读投影（覆写骨架占位） ----

    def ledger_snapshot(self) -> Dict[str, Any]:
        """模拟账本只读摘要：持仓数、累计模拟盈亏、步数。

        内存对象投影：每次调用新建 dict 返回，调用方篡改返回值不穿透
        内部账本；不落盘、不造第二账。
        """
        return {
            "simulated": True,  # 模拟标注：本账本全部为模拟数据
            "持仓数": len(self._positions),
            "累计模拟盈亏": round(self._cum_pnl, 4),
            "步数": self._steps,
        }

    # ---- 状态点自述（工位T，S6 清障波次；fail-visible 语义不动） ----

    def _platform_label(self) -> str:
        """状态点 platform 字段取值：注册表同源中文名。

        从 PLATFORM_REGISTRY 按 platform 标识反查 label（quant_trading →
        量化交易），与注册表同源；查不到时如实回退平台标识，不造数。
        """
        for entry in PLATFORM_REGISTRY.values():
            if entry["name"] == self.platform:
                return str(entry["label"])
        return self.platform

    def status_point(self) -> StatusPoint:
        """平台状态点自述（覆写 BaseAgent 骨架占位，OS 归集最小接口）。

        全部取自实例内存态，不造数：
        - agent=席位名；platform=注册表同源中文名（量化交易）；
        - doing="模拟推演"（显式含「模拟」标注，模拟红线）；
        - progress=最近一次步进摘要；尚未步进时如实报「尚未开始推演」；
        - artifact=模拟账本只读摘要（内存不落盘）。
        """
        progress = (self._day_points[-1].progress
                    if self._day_points else "尚未开始推演")
        return StatusPoint(
            agent=self.name,
            platform=self._platform_label(),
            doing="模拟推演",
            progress=progress,
            artifact=self._artifact_summary(),
        )


# ======================================================================
# S5 扩编波次（工位P）：平台注册表与五平台实例化
# 仅追加：不改动既有 PlatformAgent / QuantPlatformAgent 的签名与行为。
# ======================================================================

class SimulatedPlatformAgent(QuantPlatformAgent):
    """配置驱动的通用模拟平台席（stock/rd/ppt/biz/review 五平台共用）。

    通过**继承** QuantPlatformAgent 复用同一套模拟推演引擎（random.Random
    种子驱动的模拟行情步进、模拟持仓盯市、模拟账本投影），不复制引擎
    逻辑；仅按注册表配置替换 platform/name，并让状态点与日报携带平台
    中文名与职责描述。

    【模拟声明 · 红线与量化试点同构】全部产出（推演、账本、日报）均为
    **模拟**数据；模拟盘不触真实资金、不下真实委托；真实交易必须走
    独立风控 PASS + 本人签准（approval_gate），任何代码路径不得绕行。
    模拟账本为纯内存对象：不落盘、不造第二账；ledger_snapshot 只做
    只读投影。
    """

    def __init__(self, platform: str, label: str = "", doing: str = "",
                 **kw: Any) -> None:
        """按注册表配置实例化一个非量化模拟平台席。

        platform：平台标识（须在 PLATFORMS 内且非 quant_trading）；
        label：平台中文名（进状态点 doing 与日报）；
        doing：平台职责描述（进日报正文）；
        其余参数（seed/report_to/bus_url/metrics_port 等）透传引擎。
        """
        if platform not in PLATFORMS:
            raise ValueError(f"未知平台: {platform}")
        if platform == "quant_trading":
            raise ValueError("quant_trading 请直接使用 QuantPlatformAgent")
        self.label = label
        self.doing_desc = doing
        super().__init__(**kw)  # 引擎初始化（quant_trading 占位名随后覆盖）
        self.platform = platform
        self.name = f"{platform}_platform"

    # ---- 自运行循环：复用引擎步进，状态点附加平台中文名 ----

    def self_run_loop(self, steps: int = 1) -> Iterator[StatusPoint]:
        """复用父类（量化试点）模拟推演引擎，doing 标注平台中文名。

        引擎步进（模拟行情/持仓/盈亏）全部继承自 QuantPlatformAgent，
        此处仅重写状态点的 doing 字段为「模拟推演（平台中文名）」，
        保证非量化平台产出同样显式标注「模拟」。
        """
        for point in super().self_run_loop(steps):
            yield StatusPoint(
                agent=point.agent,
                platform=point.platform,
                doing=f"模拟推演（{self.label}）",
                progress=point.progress,
                artifact=point.artifact,
                ts=point.ts,
            )

    # ---- 平台日报：账本投影字段同构，标注平台中文名与职责 ----

    def daily_report(self) -> Report:
        """平台日报：汇总当日状态点与模拟账本投影（纯模拟）。

        字段缺失如实标注（如当日未步进则写明「无」），不造数；
        level="platform"，经 report_up 走 message_bus 上行至中台，
        不绕过中台直发本人。
        """
        snap = self.ledger_snapshot()
        lines = [
            f"[模拟] {self.name}（{self.label}）平台日报"
            f"（模拟推演，非真实战绩）",
            f"平台职责：{self.doing_desc}",
            f"模拟步数：{snap['步数']}",
            f"模拟持仓数：{snap['持仓数']}",
            f"累计模拟盈亏：{snap['累计模拟盈亏']:+.2f}（模拟）",
        ]
        if self._day_points:
            lines.append(f"当日状态点：{len(self._day_points)} 条，"
                         f"最新：{self._day_points[-1].progress}")
        else:
            lines.append("当日状态点：无（今日尚未模拟步进，如实标注）")
        lines.append("红线：模拟盘不触真实资金；真实交易须独立风控 "
                     "PASS+本人签准（approval_gate）。")
        return Report(level="platform", source=self.name,
                      subject=f"[模拟][平台日报] {self.name}（{self.label}）",
                      body="\n".join(lines))

    # ---- 状态点自述：platform 字段用自身配置 label（工位T） ----

    def status_point(self) -> StatusPoint:
        """平台状态点自述：复用量化试点实现，platform 用自身配置 label。

        doing/progress/artifact 语义与量化试点同构，全部取自实例内存态，
        不造数；label 缺省为空时如实回退平台标识。
        """
        point = super().status_point()
        return StatusPoint(
            agent=point.agent,
            platform=self.label or self.platform,
            doing=point.doing,
            progress=point.progress,
            artifact=point.artifact,
            ts=point.ts,
        )


# 六平台注册表：注册名（工厂入口）→ 配置（平台标识/中文名/职责/实现类）。
# 量化条目指向 QuantPlatformAgent（工位L 试点），其余五平台由
# SimulatedPlatformAgent 配置驱动实例化（同构模拟推演，继承共享引擎）。
PLATFORM_REGISTRY: Dict[str, Dict[str, Any]] = {
    "quant": {
        "name": "quant_trading", "label": "量化交易",
        "doing": "模拟行情步进与模拟持仓盯市的量化推演试点",
        "cls": QuantPlatformAgent,
    },
    "stock": {
        "name": "stock_picking", "label": "选股",
        "doing": "选股信号模拟演练与模拟组合推演（模拟数据）",
    },
    "rd": {
        "name": "r_and_d", "label": "研发",
        "doing": "研发课题模拟推演与技术方案演练（模拟数据）",
    },
    "ppt": {
        "name": "ppt_studio", "label": "PPT工坊",
        "doing": "演示文稿制作流程模拟推演（模拟数据）",
    },
    "biz": {
        "name": "biz_negotiation", "label": "商务谈判",
        "doing": "商务谈判策略模拟推演与话术演练（模拟数据）",
    },
    "review": {
        "name": "review_audit", "label": "审查稽核",
        "doing": "审查稽核要点模拟推演与合规演练（模拟数据）",
    },
}


def make_platform(name: str, **kw: Any) -> PlatformAgent:
    """平台工厂：按注册名返回对应平台席实例。

    注册表内名字 → 对应实例（quant → QuantPlatformAgent，其余五平台 →
    SimulatedPlatformAgent 配置驱动实例化）；未知名字抛 ValueError。
    其余参数（seed/report_to/bus_url/metrics_port 等）透传平台席构造。
    """
    entry = PLATFORM_REGISTRY.get(name)
    if entry is None:
        raise ValueError(
            f"未知平台注册名: {name}"
            f"（可用: {', '.join(PLATFORM_REGISTRY)}）")
    cls = entry.get("cls", SimulatedPlatformAgent)
    if cls is QuantPlatformAgent:
        return QuantPlatformAgent(**kw)
    return cls(platform=entry["name"], label=entry["label"],
               doing=entry["doing"], **kw)
