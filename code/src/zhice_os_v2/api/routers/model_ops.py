"""模型运维 API（V1 model-ops 对齐）。

V1 参照 zhice/backend apps/workbench_api model-ops 路由 + ModelOpsPage.tsx /
LearningPage.tsx（实验运行）：
  - 模型注册表：V2 无 model_registry 表，用内存态实现（重启复位可接受）
  - 版本晋升：DRAFT → ACTIVE（旧 ACTIVE → ARCHIVED）
  - 实验运行：读 v2_experiment_run 库表（join 策略名），结题写 CONCLUDED
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session

router = APIRouter(prefix="/model-ops", tags=["model-ops"], dependencies=[Depends(get_current_user)])

# 内存模型注册表：model_id -> {name, task_type, versions: [...], active_version}
_models: dict[str, dict] = {}

TASK_TYPES = ("decay_detect", "regime_classify", "trade_quality")

TASK_TYPE_LABELS = {
    "decay_detect": "策略衰减检测",
    "regime_classify": "市场状态分类",
    "trade_quality": "交易质量评估",
}


class ModelBody(BaseModel):
    model_id: str
    name: str
    task_type: str  # decay_detect | regime_classify | trade_quality


class VersionBody(BaseModel):
    version: str
    metrics: dict = {}


class ConcludeBody(BaseModel):
    conclusion: str
    adopt: bool = False


def _now() -> str:
    return datetime.now(UTC).isoformat()


@router.get("/models")
def list_models(task_type: str | None = None):
    """模型列表（可按任务类型过滤）。"""
    items = [m for m in _models.values() if task_type is None or m["task_type"] == task_type]
    return sorted(items, key=lambda m: m["model_id"])


@router.post("/models")
def register_model(body: ModelBody):
    """注册模型（重复 model_id 返回 409）。"""
    if body.task_type not in TASK_TYPES:
        raise HTTPException(status_code=422, detail={"code": "model.invalid_task_type"})
    if body.model_id in _models:
        raise HTTPException(status_code=409, detail={"code": "model.already_registered"})
    _models[body.model_id] = {
        "model_id": body.model_id,
        "name": body.name,
        "task_type": body.task_type,
        "task_type_label": TASK_TYPE_LABELS[body.task_type],
        "versions": [],
        "active_version": None,
        "created_at": _now(),
    }
    return _models[body.model_id]


def _get_model(model_id: str) -> dict:
    model = _models.get(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail={"code": "model.not_found"})
    return model


@router.post("/models/{model_id}/versions")
def add_version(model_id: str, body: VersionBody):
    """加版本（status=DRAFT）。"""
    model = _get_model(model_id)
    if any(v["version"] == body.version for v in model["versions"]):
        raise HTTPException(status_code=409, detail={"code": "model.version_exists"})
    entry = {
        "version": body.version,
        "metrics": body.metrics,
        "status": "DRAFT",
        "created_at": _now(),
    }
    model["versions"].append(entry)
    return {"model_id": model_id, "added": entry, "total_versions": len(model["versions"])}


@router.post("/models/{model_id}/versions/{version}/promote")
def promote_version(model_id: str, version: str):
    """晋升为 active（旧 active → ARCHIVED）。"""
    model = _get_model(model_id)
    target = next((v for v in model["versions"] if v["version"] == version), None)
    if target is None:
        raise HTTPException(status_code=404, detail={"code": "model.version_not_found"})
    old_active = model["active_version"]
    if old_active == version:
        raise HTTPException(status_code=409, detail={"code": "model.version_already_active"})
    for v in model["versions"]:
        if v["status"] == "ACTIVE":
            v["status"] = "ARCHIVED"
        if v["version"] == version:
            v["status"] = "ACTIVE"
    model["active_version"] = version
    return {
        "model_id": model_id,
        "promoted": version,
        "archived": old_active,
        "active_version": version,
    }


@router.get("/active/{task_type}")
def active_model(task_type: str):
    """按任务类型取活跃版本（无则 404）。"""
    for model in _models.values():
        if model["task_type"] == task_type and model["active_version"]:
            return model
    raise HTTPException(status_code=404, detail={"code": "model.no_active_version"})


@router.get("/experiments")
def experiments(market: str = "CN", status: str | None = None):
    """实验列表（读 v2_experiment_run 库表 + join 策略名）。"""
    session = get_session()
    try:
        q = (
            session.query(
                orm.ExperimentRunModel.experiment_id,
                orm.ExperimentRunModel.strategy_version_id,
                orm.ExperimentRunModel.status,
                orm.ExperimentRunModel.config,
                orm.ExperimentRunModel.result,
                orm.ExperimentRunModel.created_at,
                orm.StrategyModel.strategy_name,
            )
            .outerjoin(
                orm.StrategyVersionModel,
                orm.StrategyVersionModel.strategy_version_id == orm.ExperimentRunModel.strategy_version_id,
            )
            .outerjoin(
                orm.StrategyModel,
                orm.StrategyModel.strategy_id == orm.StrategyVersionModel.strategy_id,
            )
            .filter(orm.ExperimentRunModel.market == market)
        )
        if status:
            q = q.filter(orm.ExperimentRunModel.status == status)
        rows = q.order_by(orm.ExperimentRunModel.created_at.desc()).all()
    finally:
        session.close()

    return [
        {
            "experiment_id": experiment_id,
            "strategy_version_id": strategy_version_id,
            "strategy_name": strategy_name or strategy_version_id,
            "status": run_status,
            "config": config or {},
            "result": result or {},
            "created_at": created_at.isoformat() if created_at else None,
        }
        for experiment_id, strategy_version_id, run_status, config, result, created_at, strategy_name in rows
    ]


@router.post("/experiments/{experiment_id}/conclude")
def conclude_experiment(experiment_id: str, body: ConcludeBody):
    """实验结题：UPDATE v2_experiment_run SET status='CONCLUDED', result=result||{conclusion, adopt}。"""
    session = get_session()
    try:
        run = (
            session.query(orm.ExperimentRunModel)
            .filter(orm.ExperimentRunModel.experiment_id == experiment_id)
            .one_or_none()
        )
        if run is None:
            raise HTTPException(status_code=404, detail={"code": "model.experiment_not_found"})
        if run.status == "CONCLUDED":
            raise HTTPException(status_code=409, detail={"code": "model.experiment_already_closed"})
        run.status = "CONCLUDED"
        merged = dict(run.result or {})
        merged.update({"conclusion": body.conclusion, "adopt": body.adopt})
        run.result = merged
        session.commit()
    finally:
        session.close()

    suggestion = None
    if body.adopt:
        suggestion = f"实验 {experiment_id} 已采纳：建议将对应策略版本晋升为 ACTIVE（模型与实验页 → 晋升）"
    return {
        "experiment_id": experiment_id,
        "status": "CONCLUDED",
        "result": merged,
        "suggestion": suggestion,
    }
