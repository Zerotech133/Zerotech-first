"""账户 API。"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from zhice_os_v2.api.dependencies import deny_viewer, get_current_user, get_db
from zhice_os_v2.application.services.account_service import AccountService
from zhice_os_v2.application.services.account_stats_service import AccountStatsService
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/accounts", tags=["accounts"], dependencies=[Depends(get_current_user)])


_service = AccountService()


def get_account_service() -> AccountService:
    return _service


class CreateAccountBody(BaseModel):
    account_id: str
    market: str
    initial_capital: float


@router.post("")
def create_account(body: CreateAccountBody, service: AccountService = Depends(get_account_service)):
    account = service.create_account(body.account_id, body.market, Decimal(str(body.initial_capital)))
    return {
        "account_id": account.account_id,
        "market": account.market,
        "initial_capital": str(account.initial_capital),
        "cash": str(account.cash),
    }


@router.get("", deprecated=True, description="已弃用：进程内内存账户，不读取 v2_user_account；请用 /my/accounts 或 /accounts/stats。")
def list_accounts(service: AccountService = Depends(get_account_service)):
    return [
        {
            "account_id": a.account_id,
            "market": a.market,
            "initial_capital": str(a.initial_capital),
            "cash": str(a.cash),
            "market_value": str(a.market_value),
            "total_equity": str(a.total_equity),
        }
        for a in service.list_accounts()
    ]


@router.get("/stats")
def account_stats(market: str = "CN", db: PersistencePort = Depends(get_db),
                  _guard=Depends(deny_viewer)):
    """全市场账户汇总（总权益/现金/市值）。VIEWER 403（泄露总权益与账户余额）。"""
    return AccountStatsService(db).market_summary(market)


@router.get("/stats/by-strategy")
def account_stats_by_strategy(market: str = "CN", db: PersistencePort = Depends(get_db),
                              _guard=Depends(deny_viewer)):
    """分策略账户统计。VIEWER 403（含他人私有策略账户余额）。"""
    return AccountStatsService(db).by_strategy(market)
