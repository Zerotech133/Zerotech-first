"""导出 API。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from zhice_os_v2.api.dependencies import get_current_user, get_db
from zhice_os_v2.application.services.export_service import ExportService
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/exports", tags=["exports"], dependencies=[Depends(get_current_user)])


@router.get("/replays")
def export_replays(db: PersistencePort = Depends(get_db)):
    """复盘 CSV 导出（C4：attachment 下载形态，文件名带当日日期）。"""
    filename = f"replays_{date.today():%Y%m%d}.csv"
    return PlainTextResponse(
        ExportService(db).replays_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

