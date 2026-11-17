"""采集存储分层接口：维护存储策略，覆盖启用策略、暂停策略、关闭策略等动作。

同时承载全局采集指标的存储侧能力：
- 可解释采集核对清单（指标来源、统计时间、口径版本）；
- 看板指标快照分层登记（热存当前口径、温存旧口径待复核）；
- 存储模块同步刷新入口，与看板刷新复用同一条指标管线。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.metrics import pipeline
from app.services.storage import StorageService

router = APIRouter(prefix="/api/storage", tags=["采集存储分层"])

service = StorageService()

LIST_FIELDS = ["策略编号", "存储层级", "文件格式", "压缩算法", "保留天数", "迁移规则", "清理方式", "策略状态"]
STATUSES = ["草稿", "已生效", "已暂停", "已关闭"]


class SyncPayload(BaseModel):
    """存储模块同步刷新请求。"""

    conflict_policy: str = "current"
    simulate_failure: bool = False


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按策略编号检索"),
    status: str | None = Query(default=None, description="草稿、已生效、已暂停、已关闭"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按策略编号与状态过滤采集存储分层列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/checklist")
def list_checklist(
    module: str | None = Query(default=None, description="按业务模块名过滤"),
    caliber_version: str | None = Query(default=None, description="按指标口径版本过滤"),
) -> dict[str, Any]:
    """读取可解释采集核对清单：逐项列出指标来源、口径版本与统计时间。"""
    items = pipeline.list_checklist(module=module, caliber_version=caliber_version)
    return {"total": len(items), "items": items}


@router.get("/metric-snapshots")
def list_metric_snapshots(
    panel_id: int | None = Query(default=None, description="按面板 id 过滤"),
    review_status: str | None = Query(default=None, description="正常、待复核"),
) -> dict[str, Any]:
    """读取分层登记的看板指标快照，快照内保存指标口径版本。"""
    items = pipeline.list_snapshots(panel_id=panel_id, review_status=review_status)
    return {"total": len(items), "items": items}


@router.get("/refresh-logs")
def list_refresh_logs() -> dict[str, Any]:
    """读取全量看板刷新流水（含存储同步入口触发的刷新）。"""
    items = pipeline.list_refresh_logs()
    return {"total": len(items), "items": items}


@router.post("/sync-metrics")
def sync_metrics(payload: SyncPayload | None = None) -> dict[str, Any]:
    """另一个入口：同步刷新可解释采集清单与全局采集指标。

    复用看板刷新管线：按当前发布口径重算全局指标、幂等更新核对清单，
    并刷新全部已启用面板；重复执行不会生成第二份采集趋势。
    """
    if payload is None:
        payload = SyncPayload()
    if payload.conflict_policy not in ("current", "requested"):
        raise HTTPException(status_code=400, detail="conflict_policy 仅支持 current 或 requested")
    return pipeline.sync_from_storage(
        conflict_policy=payload.conflict_policy,
        simulate_failure=payload.simulate_failure,
    )


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条存储策略明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"存储策略 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条存储策略，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="存储策略已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条存储策略执行启用策略、暂停策略、关闭策略；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出采集存储分层清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "storage", "total": total, "items": items}
