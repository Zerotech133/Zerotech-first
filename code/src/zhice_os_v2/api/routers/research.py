"""研究 API：对外统一为智策研究引擎。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from zhice_os_v2.api.dependencies import (
    get_current_user,
    get_db,
    get_research_gateway,
    get_strategy_service,
)
from zhice_os_v2.application.services.report_service import ReportService
from zhice_os_v2.application.services.research_gateway import ResearchGateway
from zhice_os_v2.application.services.strategy_service import StrategyService
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/research", tags=["research"], dependencies=[Depends(get_current_user)])


class StartResearchBody(BaseModel):
    market: str
    topic: str


class AssistantBody(BaseModel):
    market: str
    question: str


@router.get("/health")
def research_health():
    return {"status": "ok", "engine": "zhice-research-engine"}


@router.post("/start")
def start_research(
    body: StartResearchBody,
    gateway: ResearchGateway = Depends(get_research_gateway),
):
    report = gateway.start_research(body.market, body.topic)
    return {
        "report_id": report.id,
        "title": report.title,
        "market": report.market,
        "status": report.status,
        "summary": report.summary,
        "strategy_spec": report.strategy_spec.model_dump() if report.strategy_spec else None,
    }


@router.post("/assistant")
def research_assistant(
    body: AssistantBody,
    gateway: ResearchGateway = Depends(get_research_gateway),
    db: PersistencePort = Depends(get_db),
):
    from zhice_os_v2.application.services.regime_service import RegimeService

    report = gateway.start_research(body.market, body.question)
    replay_report = ReportService(db).replay_report(market=body.market)

    # 基于真实市场状态与复盘数据生成结构化分析
    benchmark = {"CN": "000001.SH", "US": "SPY", "HK": "HSI", "CRYPTO": "BTC/USDT"}.get(body.market, "000001.SH")
    regime = RegimeService().get_regime(body.market, benchmark=benchmark)

    regime_label = {"BULL": "多头趋势", "BEAR": "空头趋势", "SIDEWAYS": "震荡整理", "UNKNOWN": "数据不足"}.get(
        regime.get("regime", "UNKNOWN"), "未知"
    )
    win_rate = replay_report.get("win_rate", 0.0)
    total = replay_report.get("total", 0)

    # 策略建议：市场状态 → 策略类型
    regime_state = regime.get("regime", "UNKNOWN")
    if regime_state == "BULL":
        suggestion = "当前处于多头趋势，趋势跟随/动量策略更占优；可适度提高仓位，注意回撤保护。"
    elif regime_state == "BEAR":
        suggestion = "当前处于空头趋势，建议降低仓位或空仓观望；均值回归策略需谨慎，避免逆势抄底。"
    elif regime_state == "SIDEWAYS":
        suggestion = "当前市场震荡整理，均值回归/网格策略更适合；控制单笔仓位，快进快出。"
    else:
        suggestion = "当前市场数据不足，建议先补充行情数据再评估。"

    perf_note = (
        f"历史复盘共 {total} 笔，胜率 {win_rate * 100:.1f}%。"
        if total > 0
        else "暂无历史复盘数据，建议先运行盘前/盘后工作流积累样本。"
    )

    answer = (
        f"【{body.market} 市场分析】\n"
        f"• 市场状态：{regime_label}"
        f"（近5日 {regime.get('ret5', 0) * 100:+.2f}%，近20日 {regime.get('ret20', 0) * 100:+.2f}%）\n"
        f"• 策略建议：{suggestion}\n"
        f"• 历史绩效：{perf_note}\n"
        f"• 关于「{body.question}」：结合上述市场状态，"
        f"{'建议采用趋势类策略' if regime_state == 'BULL' else '建议采用均值回归类策略' if regime_state == 'SIDEWAYS' else '建议保守观望'}。"
    )

    return {
        "answer": answer,
        "regime": regime,
        "report": {
            "report_id": report.id,
            "title": report.title,
            "market": report.market,
            "strategy_spec": report.strategy_spec.model_dump() if report.strategy_spec else None,
        },
        "replay_summary": {
            "total": total,
            "win_rate": win_rate,
            "wins": replay_report.get("wins", 0),
            "losses": replay_report.get("losses", 0),
            "total_pnl": replay_report.get("total_pnl", "0"),
        },
    }


@router.get("/strategies")
def list_research_strategies(service: StrategyService = Depends(get_strategy_service)):
    return [
        {
            "strategy_version_id": v.strategy_version_id,
            "strategy_id": v.strategy_id,
            "market": v.market,
            "status": v.status,
            "research_ref": v.research_ref,
        }
        for v in service.list_versions()
    ]
