"""全局采集看板接口：维护看板面板，覆盖启用面板、暂停面板、归档面板等动作。

看板刷新相关接口（指标计算、口径冲突、采集趋势）统一走 services.metrics 管线，
路由层不做业务判断，只负责参数校验与结果转译。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.dashboard import DashboardService
from app.services.metrics import CURRENT_CALIBER, pipeline
from app.store import store

router = APIRouter(prefix="/api/dashboard", tags=["全局采集看板"])

service = DashboardService()

LIST_FIELDS = ["面板编号", "面板名称", "显示指标", "刷新间隔", "告警配置", "数据范围", "创建人", "面板状态"]
STATUSES = ["草稿", "已启用", "已暂停", "已归档"]


class RefreshPayload(BaseModel):
    """看板刷新请求：可携带旧指标口径版本与冲突处理策略。"""

    caliber_version: str | None = None
    conflict_policy: str = "current"  # current=采用当前已发布口径；requested=沿用请求携带的旧口径
    simulate_failure: bool = False


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按面板编号检索"),
    status: str | None = Query(default=None, description="草稿、已启用、已暂停、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按面板编号与状态过滤全局采集看板列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/calibers")
def list_calibers() -> dict[str, Any]:
    """列出全部指标口径版本，并标明当前发布版本。"""
    return {"current": CURRENT_CALIBER, "items": pipeline.list_calibers()}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条看板面板明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"看板面板 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条看板面板，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="看板面板已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条看板面板执行启用面板、暂停面板、归档面板；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/refresh")
def refresh_entry(entry_id: int, payload: RefreshPayload | None = None) -> dict[str, Any]:
    """刷新看板面板：计算全局采集指标、生成按面板范围聚合的采集趋势并登记快照。

    - 一次刷新只引用一个指标口径版本，业务模块数据同口径取数；
    - 请求携带旧口径且当前已有新发布口径时按 conflict_policy 处理冲突；
    - 重复刷新幂等返回既有趋势，刷新失败保留上一份成功结果；
    - 仅已启用面板允许刷新。
    """
    panel = store.find("dashboard", entry_id)
    if panel is None:
        raise HTTPException(status_code=404, detail=f"看板面板 {entry_id} 不存在或已归档")
    if payload is None:
        payload = RefreshPayload()
    if payload.conflict_policy not in ("current", "requested"):
        raise HTTPException(status_code=400, detail="conflict_policy 仅支持 current 或 requested")
    outcome = pipeline.refresh_panel(
        panel,
        requested_caliber=payload.caliber_version,
        conflict_policy=payload.conflict_policy,
        simulate_failure=payload.simulate_failure,
        source="dashboard",
    )
    return outcome


@router.get("/{entry_id}/trend")
def get_trend(entry_id: int) -> dict[str, Any]:
    """读取面板最近一份成功刷新的采集趋势（含概览、分组与逐项指标来源）。"""
    panel = store.find("dashboard", entry_id)
    if panel is None:
        raise HTTPException(status_code=404, detail=f"看板面板 {entry_id} 不存在或已归档")
    trend = pipeline.get_panel_trend(entry_id)
    runtime = pipeline.get_panel_runtime(entry_id)
    if trend is None:
        return {
            "ok": False,
            "message": "该面板尚未成功刷新过，暂无采集趋势",
            "runtime": runtime,
            "trend": None,
        }
    return {"ok": True, "runtime": runtime, "trend": trend}


@router.get("/{entry_id}/snapshots")
def list_snapshots(entry_id: int) -> dict[str, Any]:
    """读取面板在存储分层登记的指标快照（含口径版本与待复核标记）。"""
    panel = store.find("dashboard", entry_id)
    if panel is None:
        raise HTTPException(status_code=404, detail=f"看板面板 {entry_id} 不存在或已归档")
    items = pipeline.list_snapshots(panel_id=entry_id)
    return {"panel_id": entry_id, "total": len(items), "items": items}


@router.get("/{entry_id}/refresh-logs")
def list_refresh_logs(entry_id: int) -> dict[str, Any]:
    """读取面板刷新流水：成功、重复、失败与口径冲突处理都可追溯。"""
    panel = store.find("dashboard", entry_id)
    if panel is None:
        raise HTTPException(status_code=404, detail=f"看板面板 {entry_id} 不存在或已归档")
    items = pipeline.list_refresh_logs(panel_id=entry_id)
    return {"panel_id": entry_id, "total": len(items), "items": items}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出全局采集看板清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "dashboard", "total": total, "items": items}
